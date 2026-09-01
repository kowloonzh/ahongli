import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "parse_bank_metrics.py"
DOWNLOAD_SCRIPT = SCRIPT.with_name("download_bank_reports.py")
EXTRACT_SCRIPT = SCRIPT.with_name("extract_bank_reports.py")
PREPARE_SCRIPT = SCRIPT.with_name("prepare_bank_metrics.py")
BANK_STOCKS = SCRIPT.parents[1] / "assets" / "stocks.json"


def load_module():
    spec = importlib.util.spec_from_file_location("parse_bank_metrics", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_prepare_module():
    spec = importlib.util.spec_from_file_location("prepare_bank_metrics", PREPARE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class BankMetricsParserTest(unittest.TestCase):
    def test_downloader_contains_no_hardcoded_session_cookie(self):
        source = DOWNLOAD_SCRIPT.read_text(encoding="utf-8")

        self.assertIsNone(re.search(r'"JSESSIONID"\s*:\s*"[^"$]+"', source))
        self.assertIsNone(re.search(r'"insert_cookie"\s*:\s*"[^"$]+"', source))

    def test_vendored_report_tools_and_bank_stock_map_are_self_contained(self):
        self.assertTrue(DOWNLOAD_SCRIPT.exists())
        self.assertTrue(EXTRACT_SCRIPT.exists())
        stocks = json.loads(BANK_STOCKS.read_text(encoding="utf-8"))
        expected = {
            "000001", "002142", "600926", "601916",
            "601229", "601398", "601077", "600036", "600919",
            "601998", "601288", "600015", "601939", "601988",
            "601328", "601825", "601658", "601009", "601838",
            "601166", "600000", "600016", "601169", "601818",
        }
        self.assertTrue(expected.issubset(set(stocks["szse"])))
        self.assertEqual(len(stocks["szse"]), 24)

    def test_bank_preparer_keeps_exactly_latest_three_complete_annual_rows(self):
        self.assertTrue(PREPARE_SCRIPT.exists())
        module = load_prepare_module()
        records = [
            {
                "ts_code": "600001.SH",
                "period": f"{year}1231",
                "data_quality": "normal",
                **{field: 1 for field in module.REQUIRED_METRICS},
            }
            for year in [2021, 2022, 2023, 2024, 2025]
        ]

        selected = module.select_latest_three_complete_annual_rows(
            records,
            {"600001.SH"},
        )

        self.assertEqual([row["period"] for row in selected], ["20231231", "20241231", "20251231"])

    def test_parses_simplified_chinese_bank_metrics_and_current_period_value(self):
        module = load_module()
        text = """
2025年度报告
净息差 1.16% 1.17% 1.34%
成本收入比 23.31% 23.82% 24.61%
核心一级资本充足率 10.65% 10.35% 9.53%
一级资本充足率 11.09% 11.24% 10.42%
资本充足率 14.00% 14.21% 13.38%
不良贷款率 1.18% 1.18% 1.21%
拨备覆盖率 244.94% 269.81% 272.66%
贷款拨备率 2.89% 3.18% 3.29%
关注类贷款占比 2.11% 2.06% 2.07%
逾期贷款率 1.65% 1.73%
"""

        result = module.parse_bank_metrics(
            text,
            ts_code="601229.SH",
            period="20251231",
            source_file="上海银行2025年度报告.pdf",
        )

        self.assertEqual(result["npl_ratio"], 1.18)
        self.assertEqual(result["provision_coverage_ratio"], 244.94)
        self.assertEqual(result["loan_provision_ratio"], 2.89)
        self.assertEqual(result["net_interest_margin"], 1.16)
        self.assertEqual(result["cost_income_ratio"], 23.31)
        self.assertEqual(result["core_tier1_capital_ratio"], 10.65)
        self.assertEqual(result["tier1_capital_ratio"], 11.09)
        self.assertEqual(result["capital_adequacy_ratio"], 14.00)
        self.assertEqual(result["attention_loan_ratio"], 2.11)
        self.assertEqual(result["overdue_loan_ratio"], 1.65)
        self.assertEqual(result["data_quality"], "normal")
        evidence = json.loads(result["metric_evidence_json"])
        self.assertIn("上海银行2025年度报告.pdf", evidence["npl_ratio"]["source_file"])

    def test_normalizes_traditional_chinese_metric_names(self):
        module = load_module()
        text = """
不良貸款率 1.22% 1.25%
撥備覆蓋率 238.00% 240.00%
貸款撥備率 2.90% 3.00%
淨利息收益率 1.42% 1.51%
成本收入比 25.00% 26.00%
核心一級資本充足率 12.10% 11.90%
一級資本充足率 13.20% 13.00%
資本充足率 16.30% 16.10%
"""

        result = module.parse_bank_metrics(text)

        self.assertEqual(result["npl_ratio"], 1.22)
        self.assertEqual(result["provision_coverage_ratio"], 238.00)
        self.assertEqual(result["net_interest_margin"], 1.42)
        self.assertEqual(result["core_tier1_capital_ratio"], 12.10)
        self.assertEqual(result["capital_adequacy_ratio"], 16.30)
        self.assertEqual(result["data_quality"], "normal")

    def test_total_capital_ratio_does_not_reuse_tier1_values(self):
        module = load_module()
        text = """
核心一级资本充足率 9.10%
一级资本充足率 10.20%
资本充足率 13.30%
不良贷款率 1.00%
拨备覆盖率 200.00%
贷款拨备率 2.00%
净息差 1.50%
成本收入比 30.00%
"""

        result = module.parse_bank_metrics(text)

        self.assertEqual(result["core_tier1_capital_ratio"], 9.10)
        self.assertEqual(result["tier1_capital_ratio"], 10.20)
        self.assertEqual(result["capital_adequacy_ratio"], 13.30)

    def test_missing_required_metrics_is_a_data_gap_not_zero(self):
        module = load_module()

        result = module.parse_bank_metrics("不良贷款率 1.18%")

        self.assertEqual(result["npl_ratio"], 1.18)
        self.assertIsNone(result["net_interest_margin"])
        self.assertEqual(result["data_quality"], "data_gap")
        self.assertIn("net_interest_margin", result["missing_metrics"])

    def test_accepts_short_loan_provision_label_used_by_banks(self):
        module = load_module()
        text = """
不良贷款率 1.28 1.31
拨备覆盖率 208.38 201.94
拨备率 2.67 2.64
净息差 1.20 1.22
成本收入比 29.30 30.00
核心一级资本充足率 11.43 11.20
一级资本充足率 12.70 12.50
资本充足率 15.96 15.60
"""

        result = module.parse_bank_metrics(text)

        self.assertEqual(result["loan_provision_ratio"], 2.67)
        self.assertEqual(result["data_quality"], "normal")

    def test_parses_metric_when_parenthetical_label_wraps_before_values(self):
        module = load_module()
        text = """
成本收入比（境內
  監管口徑，%）       13        28.77         28.50
"""

        result = module.parse_bank_metrics(text)

        self.assertEqual(result["cost_income_ratio"], 28.77)

    def test_metric_table_row_beats_narrative_with_many_unrelated_percentages(self):
        module = load_module()
        text = """
不良贷款率 1.21% 1.25% -0.04 1.25%
制造业等资产质量改善，不良贷款率较上年末分别下降 0.45%、0.07%、2.58%、0.25%、0.97%。
"""

        result = module.parse_bank_metrics(text)

        self.assertEqual(result["npl_ratio"], 1.21)

    def test_same_line_table_values_beat_earlier_chart_continuation(self):
        module = load_module()
        text = """
不良贷款率（%）
1.60 302.60 303.87 1.37 1.33
不良贷款率 8 1.30 1.33 1.37
拨备覆盖率 9 299.61 303.87 302.60
"""

        result = module.parse_bank_metrics(text)

        self.assertEqual(result["npl_ratio"], 1.30)

    def test_skips_regulatory_minimum_before_actual_bank_metric(self):
        module = load_module()
        text = """
贷款拨备率 ≥1.8 2.44 2.45 2.49
核心一级资本充足率（≥7.5） 14.44 14.73 13.32
一级资本充足率 ≥9.00% 10.90% 11.26% 10.75%
资本充足率 ≥11.00% 12.80% 13.36% 12.93%
"""

        result = module.parse_bank_metrics(text)

        self.assertEqual(result["loan_provision_ratio"], 2.44)
        self.assertEqual(result["core_tier1_capital_ratio"], 14.44)
        self.assertEqual(result["tier1_capital_ratio"], 10.90)
        self.assertEqual(result["capital_adequacy_ratio"], 12.80)


if __name__ == "__main__":
    unittest.main()
