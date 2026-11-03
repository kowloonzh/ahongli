"""Reduced original-report cases; no network or workspace-specific fixtures."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from cninfo_client import CnInfoClient, load_stock_maps
from prepare_forward_dividend_evidence import collect_candidate_announcements
from parse_forward_dividend_evidence import (
    parse_company_evidence, parse_financial_facts, parse_historical_payout_fact,
    parse_policy_facts,
)
from forecast_selected_dividends import _model_facts, forecast_selected_dividends


class MideaForwardRegressionTest(unittest.TestCase):
    def test_midea_can_be_resolved_from_packaged_stock_maps(self):
        client = CnInfoClient(stocks=load_stock_maps(ROOT / "assets/forward_stocks.json"))
        self.assertEqual(client.resolve_stock("000333.SZ")["orgId"], "9900005965")

    def test_policy_title_variant_is_fetched_and_deduplicated(self):
        class Client:
            def query_announcements(self, *args, searchkey, **kwargs):
                if searchkey in {"股东回报规划", "分红政策"}:
                    return [{"announcementId": "1222951882"}]
                return []
        rows = collect_candidate_announcements(Client(), "000333.SZ", start_date="20250101", end_date="20260909")
        self.assertEqual(rows, [{"announcementId": "1222951882"}])

    def parse_profit(self, text):
        return parse_financial_facts(text, source={"announcement_id": "1225065145", "page": 9}, ts_code="000333.SZ", period="20251231")

    def test_year_column_headers_are_not_profit(self):
        text = (
            "单位：千元\n"
            "归属于母公司股东的净利润     归属于母公司股东权益\n"
            "     2025 年度   2024 年度   2025 年  2024 年\n"
            "按中国企业会计准则 43,945,411 38,537,237 223,221,305 216,750,057"
        )
        self.assertNotIn("net_profit_parent", self.parse_profit(text))

    def test_one_profit_value_does_not_consume_next_metric(self):
        facts = self.parse_profit("归属于上市公司股东的净利润（千元）43,945,411\n期末总股本（股）7,628,798,092")
        self.assertEqual(facts["net_profit_parent"]["value"], 43_945_411_000)
        self.assertNotIn("net_profit_parent_prior_comparable", facts)

    def test_report_page_number_is_not_profit(self):
        self.assertNotIn("net_profit_parent", self.parse_profit("现金分红占归属于上市公司股东的净利润的比例。\n\n3\n单位：元"))

    def test_quarterly_row_is_not_full_year_profit(self):
        text = "分季度主要财务指标\n单位：千元\n第一季度 第二季度 第三季度 第四季度\n归属于上市公司股东的净利润 12,422,233 13,591,457 10,000,000 7,931,721"
        self.assertNotIn("net_profit_parent", self.parse_profit(text))

    def test_annual_profit_keeps_current_and_prior_values(self):
        facts = self.parse_profit("2025 年 2024 年 本年比上年增减\n归属于上市公司股东的净利润（千元）43,945,411 38,537,237 14.03%\n其他指标 1 2")
        self.assertEqual(facts["net_profit_parent"]["value"], 43_945_411_000)
        self.assertEqual(facts["net_profit_parent_prior_comparable"]["value"], 38_537_237_000)

    def test_table_payout_binds_to_row_year_not_report_year(self):
        text = "现金分红总额占归属于上市公司股\n年份 每10股分红金额（元） 现金分红总金额（亿元）\n东的净利润比例\n2024 35 267 69.34%"
        fact = parse_historical_payout_fact(text, source={"announcement_id": "1225065145", "page": 78}, ts_code="000333.SZ", period="20251231")
        self.assertEqual(fact["period"], "20241231")
        self.assertEqual(fact["value"], .6934)

    def test_cash_share_of_distribution_is_not_historical_payout(self):
        text = "现金分红总额（含其他方式）占利润分配总额的比例 100%"
        self.assertIsNone(parse_historical_payout_fact(text, source={}, ts_code="000333.SZ", period="20251231"))

    def test_policy_date_range_accepts_dashes_and_title_context(self):
        for separator in ["-", "—", "至", "–"]:
            with self.subTest(separator=separator):
                fact = parse_policy_facts("每年现金分红不少于当年归母净利润的70%。", source={"source_title": f"股东回报规划（2025年{separator}2027年）", "page": 3}, ts_code="600001.SH")
                self.assertEqual((fact["official_payout_floor"]["valid_from"], fact["official_payout_floor"]["valid_to"]), (2025, 2027))

    def test_midea_policy_basis_is_retained_not_relabelled_as_profit_payout(self):
        text = ("公司以现金方式分配的利润不少\n于最近三年实现的年均可分配利润的30%。"
                "现金分红在该次利润分配中所占比例最低应达到60%。"
                "公司以现金为对价回购股份的，视同公司现金分红，纳入现金分红的相关比例计算。")
        facts = parse_policy_facts(text, source={"source_title": "未来三年股东回报规划（2025-2027年）", "page": 3}, ts_code="000333.SZ")
        self.assertNotIn("official_payout_floor", facts)
        self.assertEqual(facts["payout_policy_constraint"]["denominator_basis"], "three_year_average_distributable_profit")
        self.assertEqual(facts["payout_policy_constraint"]["value"], .3)
        self.assertTrue(facts["payout_policy_constraint"]["includes_buybacks"])

    def test_same_year_payout_including_buybacks_is_not_cash_dps_policy(self):
        facts = parse_policy_facts("每年现金分红不低于当年归母净利润的70%。回购股份视同现金分红。", source={}, ts_code="000333.SZ")
        self.assertNotIn("official_payout_floor", facts)

    def test_distribution_share_retains_sixty_percent_not_last_digit(self):
        facts = parse_policy_facts("现金分红在该次利润分配中所占比例最低应达到60%。", source={}, ts_code="000333.SZ")
        self.assertEqual(facts["payout_policy_constraint"]["value"], .6)
        self.assertNotIn("official_payout_floor", facts)

    def test_nonfinancial_history_alone_is_not_implicitly_enabled(self):
        evidence = {"normalized_facts": [
            {"fact_type": "historical_payout_ratio", "period": f"{year}1231", "value": .7}
            for year in [2023, 2024, 2025]
        ]}
        self.assertNotIn("official_payout_ratio", _model_facts(evidence, "20260909", {"industry": "家用电器"}))

    def test_valid_cash_profit_policy_remains_supported_and_expires(self):
        policy = parse_policy_facts("每年现金分红不少于当年归母净利润的70%。", source={"source_title": "2025-2027年股东回报规划"}, ts_code="000333.SZ")["official_payout_floor"]
        evidence = {"normalized_facts": [{**policy, "fact_type": "official_payout_floor", "period": "20251231"}]}
        self.assertEqual(_model_facts(evidence, "20260909")["official_payout_ratio"], .7)
        self.assertNotIn("official_payout_ratio", _model_facts(evidence, "20280909"))

    def test_company_evidence_ttm_and_explicit_policy_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docs = [
                {"announcement_id": "A", "source_title": "2025年年度报告", "logical_role": "annual", "source_file": "A.pdf"},
                {"announcement_id": "I", "source_title": "2026年半年度报告", "logical_role": "interim", "source_file": "I.pdf"},
                {"announcement_id": "P", "source_title": "未来三年股东回报规划（2025-2027年）", "logical_role": "dividend_policy", "source_file": "P.pdf"},
            ]
            pages = {
                "A": [{"page": 9, "text": "归属于上市公司股东的净利润（千元）43,945,411 38,537,237"},
                      {"page": 10, "text": "单位：千元\n归属于母公司股东的净利润\n2025 年度 2024 年度"}],
                "I": [{"page": 7, "text": "归属于上市公司股东的净利润（千元）26,446,037 26,013,690\n期末总股本（股）7,628,798,092"}],
                "P": [{"page": 3, "text": "以现金方式分配的利润不少于最近三年实现的年均可分配利润的30%。回购股份视同现金分红。"}],
            }
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"ts_code": "000333.SZ", "documents": docs}), encoding="utf-8")
            evidence = parse_company_evidence(manifest, page_provider=lambda doc, _: pages[doc["announcement_id"]])
            facts = _model_facts(evidence, "20260909", {"industry": "家用电器"})
            self.assertEqual(facts["forecast_profit_scenarios"]["base"], 44_377_758_000)
            result = forecast_selected_dividends(top_rows=[{"ts_code": "000333.SZ", "industry": "家用电器"}], evidence_by_code={"000333.SZ": evidence}, run_date="20260909")[0]
            self.assertEqual(result["forecast_status"], "data_gap")
            self.assertIn("政策口径", result["forecast_reason"])
            self.assertIsNone(result["forecast_fy_regular_dps_base"])
            self.assertTrue(result["forecast_input_fact_ids"])


if __name__ == "__main__":
    unittest.main()
