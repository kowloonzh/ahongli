import importlib.util
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    path = ROOT / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class InsuranceMetricsParserTest(unittest.TestCase):
    def setUp(self):
        self.parser = load("insurance_parser_test", "parse_insurance_metrics.py")
        self.preparer = load("insurance_preparer_test", "prepare_insurance_metrics.py")
        self.strategy = load("insurance_strategy_test", "run_a_dividend_strategy.py")

    def fixture(self):
        return """
归属于母公司股东的营运利润 1,344.15 亿元，同比增长 10.3%；
现金分红总额488.91亿元，基于归母营运利润计算的现金分红比例为36.4%。
\f集团母公司可动用资金
（人民币百万元） 2025年
期初可动用资金余额 74,565
子公司分红 53,022
集团对外分红 (46,537)
对子公司的投资 (19,983)
发行可转换债券所得款净额 10,686
其他 (3,594)
期末可动用资金余额 68,159
\f集团偿付能力
（人民币百万元） 2025年 2024年
核心资本 1,704,959 1,457,074
实际资本 2,050,444 1,799,586
最低资本 1,060,731 881,890
核心偿付能力充足率(%) 160.7 165.2
综合偿付能力充足率(%) 193.3 204.1
本公司已测算利率下行和权益资产下跌对平安集团、平安寿险和平安产险于2025年12月31日偿付能力充足率的影响，结果如下：
核心偿付能力充足率 综合偿付能力充足率
2025年12月31日 平安集团 平安寿险 平安产险 平安集团 平安寿险 平安产险
当期利率下降50个基点 下降1.2个百分点 下降8.1个百分点 上升3.3个百分点 下降2.5个百分点 下降10.9个百分点 上升3.2个百分点
权益资产公允价值下跌10% 下降4.4个百分点 下降10.3个百分点 下降5.4个百分点 下降3.7个百分点 下降7.4个百分点 下降4.7个百分点
\f下表列示了本集团及其主要保险业子公司的实际资本及最低资本。
2025年12月31日 2024年12月31日
本集团 平安寿险 平安产险 本集团 平安寿险 平安产险
核心资本 1,704,959 716,540 126,310 1,457,074 490,983 115,692
实际资本 2,050,444 1,021,252 158,098 1,799,586 797,818 138,649
最低资本 1,060,731 581,306 72,810 881,890 421,693 67,536
核心偿付能力充足率 160.7% 123.3% 173.5% 165.2% 116.4% 171.3%
综合偿付能力充足率 193.3% 175.7% 217.1% 204.1% 189.2% 205.3%
"""

    def test_parser_keeps_entities_periods_cash_bridge_and_standalone_stress(self):
        result = self.parser.parse_insurance_metrics(
            self.fixture(), ts_code="601318.SH", period="20251231",
            report_type="annual", source_file="annual.txt",
            source_document_id="A2025", available_date="20260327",
        )
        self.assertEqual(result["data_quality"], "normal")
        self.assertEqual(result["group_core_capital"], 1_704_959_000_000)
        self.assertTrue(result["parent_funds_bridge_reconciled"])
        self.assertAlmostEqual(result["subsidiary_dividend_coverage"], 53_022 / 46_537)
        self.assertAlmostEqual(result["operating_profit_payout_ratio"], 0.364)
        subsidiaries = json.loads(result["material_subsidiaries_json"])
        self.assertEqual({item["reporting_entity"] for item in subsidiaries}, {"平安寿险", "平安产险"})
        stresses = json.loads(result["stress_scenarios_json"])
        self.assertEqual(len(stresses), 6)
        self.assertTrue(all(item["combination_rule"] == "standalone_only" for item in stresses))
        evidence = json.loads(result["metric_evidence_json"])
        self.assertEqual(evidence["group_core_capital"]["source_document_id"], "A2025")
        self.assertEqual(evidence["group_core_capital"]["extractor_version"], self.parser.EXTRACTOR_VERSION)

    def _annual_record(self, year, **changes):
        result = self.parser.parse_insurance_metrics(
            self.fixture().replace("2025", str(year)), ts_code="601318.SH", period=f"{year}1231",
            report_type="annual", source_document_id=str(year),
            available_date=f"{year + 1}0327",
        )
        result.update(changes)
        return result

    def test_formal_gate_fails_when_material_subsidiary_is_below_regulatory_floor(self):
        records = [self._annual_record(year) for year in [2023, 2024, 2025]]
        latest_subsidiaries = json.loads(records[-1]["material_subsidiaries_json"])
        latest_subsidiaries[0]["core_solvency_ratio"] = 49.9
        records[-1]["material_subsidiaries_json"] = json.dumps(latest_subsidiaries)
        profile = self.strategy.insurance_quality_profile(records)
        self.assertEqual(profile["insurance_quality_gate_status"], "hard_gate_failed")
        self.assertIn("主要保险子公司", profile["insurance_quality_gate_reason"])

    def test_missing_latest_stress_is_data_gap(self):
        records = [self._annual_record(year) for year in [2023, 2024, 2025]]
        records[-1]["stress_scenarios_json"] = "[]"
        profile = self.strategy.insurance_quality_profile(records)
        self.assertEqual(profile["insurance_quality_gate_status"], "data_gap")
        self.assertFalse(profile["insurance_quality_gate_passed"])

    def test_quarterly_report_without_stress_uses_latest_annual_stress(self):
        records = [self._annual_record(year) for year in [2023, 2024, 2025]]
        quarter = {**records[-1], "period": "20260930", "report_type": "q3",
                   "available_date": "20261029", "stress_scenarios_json": "[]"}
        profile = self.strategy.insurance_quality_profile([*records, quarter])
        self.assertTrue(profile["insurance_quality_gate_passed"])
        self.assertEqual(profile["insurance_metrics_latest_period"], "20260930")
        self.assertAlmostEqual(profile["insurance_stressed_core_solvency_ratio"], 156.3)

    def test_stale_annual_stress_is_not_reused(self):
        records = [self._annual_record(year) for year in [2023, 2024, 2025]]
        records[-1]["stress_scenarios_json"] = "[]"
        quarter = {**records[-1], "period": "20260930", "report_type": "q3", "available_date": "20261029"}
        profile = self.strategy.insurance_quality_profile([*records, quarter])
        self.assertEqual(profile["insurance_quality_gate_status"], "data_gap")

    def test_financing_is_reported_but_not_counted_in_subsidiary_coverage(self):
        records = [self._annual_record(year) for year in [2023, 2024, 2025]]
        profile = self.strategy.insurance_quality_profile(records)
        self.assertTrue(profile["insurance_quality_gate_passed"])
        self.assertAlmostEqual(profile["insurance_subsidiary_dividend_coverage"], 53_022 / 46_537)
        self.assertAlmostEqual(profile["insurance_financing_share_of_external_dividends"], 10_686 / 46_537)
        self.assertAlmostEqual(profile["insurance_stressed_core_solvency_ratio"], 156.3)

    def test_document_published_after_run_date_is_excluded(self):
        records = [self._annual_record(year) for year in [2022, 2023, 2024]]
        records.append(self._annual_record(2025, available_date="20260911"))
        selected = self.preparer.select_required_rows(records, {"601318.SH"}, "20260910")
        self.assertEqual([row["period"] for row in selected], ["20221231", "20231231", "20241231"])

    def test_fiscal_dividend_and_cash_payment_are_kept_as_distinct_facts(self):
        record = self._annual_record(2025)
        self.assertEqual(record["period"], "20251231")
        self.assertEqual(record["parent_external_dividends_paid"], 46_537_000_000)
        self.assertNotIn("fiscal_dividend_dps", record)


if __name__ == "__main__":
    unittest.main()
