import copy
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from parse_forward_dividend_evidence import parse_company_evidence
from forecast_selected_dividends import forecast_selected_dividends
from render_forward_dividend_outputs import render_forward_dividend_outputs


class NonfinancialHistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.docs = []
        self.pages = {}
        self.company = {"ts_code": "000333.SZ", "name": "美的集团", "rank": 7,
                        "dividend_score_total": 60, "selected": "是", "selection_status": "selected",
                        "industry": "家用电器", "is_financial": "否", "audit_result": "标准无保留意见",
                        "current_price": 86.02, "price_date": "20260909"}
        for year, profit, cfo, dps in [(2023, 33719935, 57902611, 3), (2024, 38537237, 60511572, 3.5), (2025, 43945411, 53345930, 4.3)]:
            self.add(str(year), f"{year}年年度报告", "annual", f"{year+1}0331", [
                (4, f"公司{year}年度利润分配方案为：每10股派发现金{dps*10:g}元（含税）。"),
                (9, f"归属于上市公司股东的净利润（千元） {profit}\n经营活动产生的现金流量净额（千元） {cfo}"),
            ])
        # Source text patterns reduced from the implementation announcements.
        self.add("D23", "2023年度利润分配实施公告", "implementation", "20240509", [(1,
            "特别提示：公司实施的2023年度利润分配方案为：以可参与分配的总股数6,926,759,241股为基数，"
            "向全体股东每10股派发现金30元，分红总金额为20,780,277,723元。")])
        self.add("D24", "2024年度A股利润分配实施公告", "implementation", "20250606", [(1,
            "公司实施的2024年度A股利润分配方案为：向全体股东每10股派发现金35元，分红总金额为24,443,728,680元。")])
        self.add("I25", "2025年中期A股利润分配实施公告", "implementation", "20251112", [(1,
            "公司实施的2025年中期A股利润分配方案为：向全体股东每10股派发现金5元，分红总金额为3,448,324,163.50元。")])
        self.add("D25", "2025年度A股利润分配实施公告", "implementation", "20260623", [(1,
            "特别提示：公司实施的2025年度A股利润分配方案为：向全体股东每10股派发现金38元，分红总金额为25,916,074,233元。")])
        self.pages["2025"].extend([
            {"page": 137, "text": "2025年度合并及公司现金流量表\n单位：千元\n合并 合并 公司 公司\n购建固定资产、无形资产和其他长期资产支付的\n现金 (11,141,889) (7,839,636)"},
            {"page": 223, "text":
             "根据2025年5月30日股东大会决议，本公司向全体股东派发现金股利，每股人民币3.5元，"
             "应计A股股利约24,443,729,000元，应计H股股利约2,277,969,000元。"
             "根据2025年9月24日临时股东会决议，本公司向全体股东派发现金股利，每股人民币0.5元，"
             "应计A股股利约3,448,324,000元，应计H股股利约325,424,000元。"},
        ])
        self.add("H26", "2026年半年度报告", "interim", "20260829", [
            (2, "公司总股本7,628,798,092股扣除回购专户上已回购股份后（回购专户股份余额为180,200,108股）的股本总额7,448,597,984股为基数，向全体股东每10股派发现金5元。"),
            (7, "归属于上市公司股东的净利润（千元）26,446,037 26,013,690\n经营活动产生的现金流量净额（千元）37,552,090 37,281,015"),
            (99, "2026年半年度合并及公司现金流量表\n单位：千元\n合并 合并 公司 公司\n购建固定资产、无形资产和其他长期资产支付\n的现金 (2,634,210) (3,145,319)"),
            (171, "根据2026年6月5日股东大会决议，本公司向全体股东派发现金股利，每股人民币3.8元，应计A股股利约25,916,074,000元，应计H股股利约2,473,225,000元。"),
        ])

    def add(self, identity, title, role, date, pages):
        self.docs.append({"announcement_id": identity, "source_title": title, "logical_role": role,
                          "source_file": f"{identity}.pdf", "available_at": f"{date[:4]}-{date[4:6]}-{date[6:]}T00:00:00+08:00"})
        self.pages[identity] = [{"page": n, "text": t} for n, t in pages]

    def evidence(self):
        path = self.root / "manifest.json"
        path.write_text(json.dumps({"ts_code": "000333.SZ", "company_name": "美的集团", "run_date": "20260909", "documents": self.docs}), encoding="utf-8")
        return parse_company_evidence(path, page_provider=lambda d, _: self.pages[d["announcement_id"]])

    def forecast(self, evidence=None):
        return forecast_selected_dividends(top_rows=[self.company], evidence_by_code={"000333.SZ": evidence or self.evidence()}, run_date="20260909")[0]

    def test_end_to_end_traceable_median_forecast(self):
        evidence = self.evidence()
        row = self.forecast(evidence)
        self.assertEqual(row["forecast_status"], "modelled")
        self.assertEqual(row["model_id"], "nonfinancial_historical_payout_v1")
        self.assertAlmostEqual(row["forecast_fy_regular_dps_base"], 4.1311817301, places=7)
        self.assertEqual(row["forecast_profit"], 44_377_758_000)
        self.assertEqual(row["forecast_total_shares"], 7_448_597_984)
        self.assertEqual(row["forecast_ttm_cfo"], 53_617_005_000)
        self.assertEqual(row["forecast_ttm_capex"], 10_630_780_000)
        self.assertAlmostEqual(row["forecast_fcf_dividend_coverage"], 1.39694874696)
        self.assertEqual(row["forecast_uncertainty"], "high")
        self.assertEqual((row["rank"], row["dividend_score_total"]), (7, 60))
        facts = {f["fact_id"]: f for f in evidence["normalized_facts"]}
        spans = {s["evidence_span_id"] for s in evidence["evidence_spans"]}
        self.assertGreater(len(row["forecast_input_fact_ids"]), 10)
        for identity in row["forecast_input_fact_ids"]:
            self.assertIn(facts[identity]["evidence_span_id"], spans)
        self.assertEqual(len(row["forecast_history"]), 3)

    def test_duplicate_source_pages_do_not_double_dividends(self):
        self.pages["2025"].append(copy.deepcopy(self.pages["2025"][-1]))
        self.assertAlmostEqual(self.forecast()["forecast_fy_regular_dps_base"], 4.1311817301, places=7)

    def test_missing_h_share_component_is_not_replaced_with_a_cash(self):
        self.pages["2025"][-1]["text"] = ""
        row = self.forecast()
        self.assertEqual(row["forecast_status"], "data_gap")
        self.assertIsNone(row["forecast_fy_regular_dps_base"])

    def test_missing_interim_does_not_pass_as_complete_annual(self):
        self.docs = [d for d in self.docs if d["announcement_id"] != "I25"]
        self.assertEqual(self.forecast()["forecast_status"], "data_gap")

    def test_future_source_is_rejected_even_in_cached_evidence(self):
        evidence = self.evidence()
        for d in evidence["source_documents"]:
            if d["announcement_id"] == "H26":
                d["available_at"] = "2026-09-10T00:00:00+08:00"
        self.assertEqual(self.forecast(evidence)["forecast_status"], "data_gap")

    def test_conflicting_profit_is_not_resolved_by_picking_larger(self):
        self.pages["2025"].append({"page": 10, "text": "归属于上市公司股东的净利润（千元）20,000,000"})
        row = self.forecast()
        self.assertEqual(row["evidence_completeness"], "conflict")
        self.assertIsNone(row["forecast_fy_regular_dps_base"])

    def test_negative_cashflow_stops_forecast(self):
        self.pages["H26"][1]["text"] = self.pages["H26"][1]["text"].replace("37,552,090", "-37,552,090")
        row = self.forecast()
        self.assertEqual(row["forecast_status"], "data_gap")
        self.assertIn("现金流", row["forecast_reason"])

    def test_special_dividend_is_not_regular_income(self):
        self.docs[-2]["source_title"] = "2025年度特别分红实施公告"
        self.assertEqual(self.forecast()["forecast_status"], "data_gap")

    def test_unimplemented_plan_cannot_supply_cash_history(self):
        for d in self.docs:
            if d["announcement_id"] == "D23":
                d["logical_role"] = "dividend_plan"
        self.assertEqual(self.forecast()["forecast_status"], "data_gap")

    def test_renderer_keeps_audit_in_csv_and_detail(self):
        evidence = self.evidence()
        row = self.forecast(evidence)
        out = render_forward_dividend_outputs(rows=[row], run_date="20260909", output_dir=self.root / "out", evidence_by_code={"000333.SZ": evidence})
        self.assertIn("forecast_history", out["csv"].read_text())
        detail = (self.root / "out" / row["evidence_detail_path"]).read_text()
        self.assertIn("三年常规现金派息率", detail)
        self.assertIn("TTM", detail)
        self.assertIn("仅为", detail)

    def test_unknown_industry_does_not_gain_universal_fallback(self):
        self.company["industry"] = "煤炭开采"
        self.assertNotEqual(self.forecast().get("model_id"), "nonfinancial_historical_payout_v1")

    def test_quarterly_cashflow_is_not_full_year(self):
        self.pages["2025"].append({"page": 11, "text": "分季度主要财务指标 单位：千元\n第一季度 第二季度 第三季度 第四季度\n经营活动产生的现金流量净额 14320968 22960047 113 114"})
        self.assertAlmostEqual(self.forecast()["forecast_fy_regular_dps_base"], 4.1311817301, places=7)

    def test_parent_only_capex_is_not_consolidated_capex(self):
        self.pages["H26"][2]["text"] = self.pages["H26"][2]["text"].replace("合并及公司", "母公司").replace("合并 合并 公司 公司", "公司 公司")
        self.assertEqual(self.forecast()["forecast_status"], "data_gap")

    def test_reversed_cashflow_columns_fail_closed(self):
        self.pages["H26"][2]["text"] = self.pages["H26"][2]["text"].replace("合并 合并 公司 公司", "公司 公司 合并 合并")
        self.assertEqual(self.forecast()["forecast_status"], "data_gap")

    def test_positive_cashflow_with_insufficient_fcf_fails(self):
        self.pages["H26"][2]["text"] = self.pages["H26"][2]["text"].replace("2,634,210", "30,000,000")
        row = self.forecast()
        self.assertEqual(row["forecast_status"], "data_gap")
        self.assertLess(row["forecast_fcf_dividend_coverage"], 1)

    def test_unknown_currency_is_not_silently_cny(self):
        self.pages["H26"][2]["text"] = self.pages["H26"][2]["text"].replace("单位：千元", "单位：美元")
        self.assertEqual(self.forecast()["forecast_status"], "data_gap")

    def test_future_implementation_corroboration_cannot_complete_history(self):
        evidence = self.evidence()
        for d in evidence["source_documents"]:
            if d["announcement_id"] == "I25":
                d["available_at"] = "2026-09-10T00:00:00+08:00"
        self.assertEqual(self.forecast(evidence)["forecast_status"], "data_gap")

    def test_cutoff_uses_shanghai_day_not_utc_date(self):
        evidence = self.evidence()
        for d in evidence["source_documents"]:
            if d["announcement_id"] == "H26":
                d["available_at"] = "2026-09-09T20:00:00+00:00"
        self.assertEqual(self.forecast(evidence)["forecast_status"], "data_gap")

    def test_nonnumeric_required_fact_becomes_gap(self):
        evidence = self.evidence()
        for f in evidence["normalized_facts"]:
            if f["fact_type"] == "regular_cash_distribution":
                f["value"] = "unknown"
        self.assertEqual(self.forecast(evidence)["forecast_status"], "data_gap")

    def test_missing_source_or_share_fact_fails_closed(self):
        for kind in ["dividend_eligible_shares", "net_profit_parent_prior_comparable"]:
            with self.subTest(kind=kind):
                evidence = self.evidence()
                evidence["normalized_facts"] = [f for f in evidence["normalized_facts"] if f["fact_type"] != kind]
                self.assertIsNone(self.forecast(evidence)["forecast_fy_regular_dps_base"])

    def test_excessive_payout_cannot_be_projected(self):
        self.pages["2023"][1]["text"] = self.pages["2023"][1]["text"].replace("33719935", "10000000")
        self.assertEqual(self.forecast()["forecast_status"], "data_gap")

    def test_announced_forecast_takes_precedence(self):
        # Other replay tests intentionally reload modules; patch this imported
        # callable's namespace, not a potentially newer sys.modules instance.
        with patch.dict(forecast_selected_dividends.__globals__, {"forecast_company": lambda **kwargs: {
            "forecast_status": "announced", "forecast_fy_regular_dps_base": 3.0, "model_id": "announced"}}):
            self.assertEqual(self.forecast()["model_id"], "announced")

    def test_rejected_formal_company_never_gets_fallback(self):
        self.company["selection_status"] = "hard_gate_failed"
        self.assertNotEqual(self.forecast().get("model_id"), "nonfinancial_historical_payout_v1")

    def test_scenarios_do_not_change_target_yield_policy(self):
        self.company.update(roe=20, latest_payout_ratio=70, dps_cagr_5y=10)
        row = self.forecast()
        self.assertEqual((row["target_yield_low"], row["target_yield_high"]), (0.06, 0.08))
        self.assertAlmostEqual(row["forecast_fy_regular_dps_low"], 3.6716006756)
        self.assertAlmostEqual(row["forecast_fy_regular_dps_high"], 4.3604817904)


if __name__ == "__main__":
    unittest.main()
