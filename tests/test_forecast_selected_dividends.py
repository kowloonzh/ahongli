import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load_module():
    path = SCRIPTS / "forecast_selected_dividends.py"
    spec = importlib.util.spec_from_file_location("forecast_selected_dividends", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ForecastSelectedDividendsTest(unittest.TestCase):
    def test_policy_forecast_builds_ttm_scenarios_without_changing_formal_rank(self):
        module = load_module()
        top = [{
            "rank": 2,
            "ts_code": "600900.SH",
            "name": "长江电力",
            "industry": "水力发电",
            "selected": "是",
            "dividend_score_total": 74.58,
            "current_price": 28.40,
            "price_date": "20260901",
            "current_dividend_yield": 3.52,
        }]
        evidence = {
            "600900.SH": {
                "normalized_facts": [
                    {"fact_id": "FY", "fact_type": "net_profit_parent", "period": "20251231", "value": 33_000_000_000.0},
                    {"fact_id": "CUR", "fact_type": "net_profit_parent", "period": "20260630", "value": 17_000_000_000.0},
                    {"fact_id": "PRIOR", "fact_type": "net_profit_parent_prior_comparable", "period": "20250630", "value": 15_000_000_000.0},
                    {"fact_id": "SHARES", "fact_type": "total_shares", "period": "20251231", "value": 24_470_000_000},
                    {"fact_id": "POLICY", "fact_type": "official_payout_floor", "value": 0.70, "valid_from": 2026, "valid_to": 2030},
                ],
                "dividend_events": [],
            }
        }

        result = module.forecast_selected_dividends(
            top_rows=top,
            evidence_by_code=evidence,
            run_date="20260902",
        )

        self.assertEqual(result[0]["rank"], 2)
        self.assertEqual(result[0]["dividend_score_total"], 74.58)
        self.assertEqual(result[0]["forecast_profit"], 35_000_000_000.0)
        self.assertAlmostEqual(result[0]["forecast_fy_regular_dps_base"], 1.0012, places=4)
        self.assertEqual(result[0]["forecast_status"], "modelled")
        self.assertEqual(result[0]["instrument_ts_code"], "600900.SH")
        self.assertEqual(result[0]["share_class"], "A")
        self.assertEqual(result[0]["quote_currency"], "CNY")
        self.assertEqual(result[0]["dividend_currency"], "CNY")
        self.assertEqual(result[0]["forecast_share_denominator_scope"], "all_ordinary_shares")
        self.assertEqual(set(result[0]["forecast_input_fact_ids"]), {"FY", "CUR", "PRIOR", "SHARES", "POLICY"})

    def test_announced_events_for_forecast_year_override_profit_model(self):
        module = load_module()
        top = [{
            "rank": 1,
            "ts_code": "600001.SH",
            "name": "测试公司",
            "industry": "公用事业",
            "selected": "是",
            "dividend_score_total": 80.0,
        }]
        evidence = {
            "600001.SH": {
                "normalized_facts": [],
                "dividend_events": [
                    {
                        "fiscal_period": "20261231",
                        "regular_or_special": "regular",
                        "status": "dividend_plan",
                        "distribution_phase": "full_year",
                        "cash_dividend_per_share_pre_tax": 0.40,
                    },
                    {
                        "fiscal_period": "20261231",
                        "regular_or_special": "special",
                        "status": "dividend_plan",
                        "distribution_phase": "full_year",
                        "cash_dividend_per_share_pre_tax": 0.10,
                    },
                ],
            }
        }

        result = module.forecast_selected_dividends(
            top_rows=top,
            evidence_by_code=evidence,
            run_date="20260902",
        )

        self.assertEqual(result[0]["forecast_status"], "announced")
        self.assertEqual(result[0]["forecast_fy_regular_dps_base"], 0.40)

    def test_forecast_adds_analysed_target_yield_and_target_prices(self):
        module = load_module()
        top = [{
            "rank": 1,
            "ts_code": "600001.SH",
            "name": "测试公司",
            "industry": "公用事业",
            "selected": "是",
            "dividend_score_total": 80.0,
            "current_price": 25.0,
            "price_date": "20260901",
            "roe": 12.0,
            "latest_payout_ratio": 0.50,
            "dps_cagr_5y": 0.03,
        }]
        evidence = {
            "600001.SH": {
                "normalized_facts": [],
                "dividend_events": [
                    {
                        "fiscal_period": "20261231",
                        "regular_or_special": "regular",
                        "status": "dividend_plan",
                        "distribution_phase": "full_year",
                        "cash_dividend_per_share_pre_tax": 1.0,
                    },
                    {
                        "fiscal_period": "20251231",
                        "regular_or_special": "regular",
                        "status": "implementation",
                        "ex_dividend_date": "20260601",
                        "available_date": "20260501",
                        "cash_dividend_per_share_pre_tax": 0.8,
                    },
                    {
                        "fiscal_period": "20261231",
                        "regular_or_special": "regular",
                        "status": "implementation",
                        "distribution_phase": "interim",
                        "ex_dividend_date": "20261201",
                        "available_date": "20260820",
                        "cash_dividend_per_share_pre_tax": 0.2,
                    },
                ],
            }
        }
        result = module.forecast_selected_dividends(
            top_rows=top,
            evidence_by_code=evidence,
            run_date="20260902",
            risk_free_rate=0.017,
        )

        self.assertAlmostEqual(result[0]["expected_dividend_yield"], 0.04, places=6)
        self.assertEqual(result[0]["forward_12m_eligible_dps"], 0.2)
        self.assertAlmostEqual(result[0]["target_yield_low"], 0.06)
        self.assertAlmostEqual(result[0]["target_yield_high"], 0.08)
        self.assertAlmostEqual(result[0]["target_price_low"], 12.5)
        self.assertAlmostEqual(result[0]["target_price_high"], 16.6667, places=4)
        self.assertEqual(result[0]["target_display_label"], "未达目标区间")
        self.assertNotIn("historical_regular_yield_p25", result[0])

    def test_supported_company_with_missing_evidence_is_data_gap_not_unsupported(self):
        module = load_module()
        top = [{
            "rank": 1,
            "ts_code": "600900.SH",
            "name": "长江电力",
            "industry": "水力发电",
            "selected": "是",
            "dividend_score_total": 74.58,
        }]

        result = module.forecast_selected_dividends(
            top_rows=top,
            evidence_by_code={},
            run_date="20260902",
        )

        self.assertEqual(result[0]["forecast_status"], "data_gap")
        self.assertIsNone(result[0]["forecast_fy_regular_dps_base"])
        self.assertIn("证据", result[0]["forecast_reason"])

    def test_company_evidence_failure_is_reported_as_failed_not_data_gap(self):
        module = load_module()
        top = [{
            "rank": 1, "ts_code": "600900.SH", "name": "长江电力",
            "industry": "水力发电", "selected": "是", "dividend_score_total": 74.58,
        }]

        result = module.forecast_selected_dividends(
            top_rows=top,
            evidence_by_code={"600900.SH": {"stage_status": "failed", "stage_reason": "PDF parse failed"}},
            run_date="20260902",
        )

        self.assertEqual(result[0]["forecast_status"], "failed")
        self.assertEqual(result[0]["forecast_reason"], "PDF parse failed")
        self.assertIsNone(result[0]["forecast_fy_regular_dps_base"])

    def test_insurance_evidence_builds_operating_profit_model(self):
        module = load_module()
        top = [{
            "rank": 1, "ts_code": "601318.SH", "name": "中国平安",
            "industry": "保险", "selected": "是", "dividend_score_total": 82.62,
        }]
        evidence = {
            "601318.SH": {
                "normalized_facts": [
                    {"fact_type": "operating_profit_parent", "period": "20251231", "value": 110.0},
                    {"fact_type": "operating_profit_parent", "period": "20260630", "value": 60.0},
                    {"fact_type": "operating_profit_parent_prior_comparable", "period": "20250630", "value": 50.0},
                    {"fact_type": "total_shares", "period": "20251231", "value": 100.0},
                    {"fact_type": "official_payout_floor", "value": 0.30, "valid_from": 2026, "valid_to": 2028},
                ],
                "dividend_events": [],
            }
        }

        result = module.forecast_selected_dividends(
            top_rows=top,
            evidence_by_code=evidence,
            run_date="20260902",
        )

        self.assertEqual(result[0]["model_id"], "insurance_operating_profit_policy_v1")
        self.assertEqual(result[0]["forecast_profit"], 120.0)
        self.assertAlmostEqual(result[0]["forecast_fy_regular_dps_base"], 0.36)

    def test_repeated_same_period_facts_use_consensus_not_last_outlier(self):
        module = load_module()
        top = [{
            "rank": 1, "ts_code": "600036.SH", "name": "招商银行",
            "industry": "银行", "selected": "是", "dividend_score_total": 72.83,
            "is_bank": "是", "bank_quality_gate_passed": "是",
            "bank_core_tier1_capital_ratio": 10.0, "bank_tier1_capital_ratio": 11.0,
            "bank_capital_adequacy_ratio": 14.0,
        }]
        evidence = {
            "600036.SH": {
                "normalized_facts": [
                    {"fact_type": "net_profit_parent", "period": "20251231", "value": 37.0},
                    {"fact_type": "net_profit_parent", "period": "20251231", "value": 150.0},
                    {"fact_type": "net_profit_parent", "period": "20251231", "value": 150.0},
                    {"fact_type": "net_profit_parent", "period": "20260630", "value": 76.0},
                    {"fact_type": "net_profit_parent_prior_comparable", "period": "20250630", "value": 75.0},
                    {"fact_type": "total_shares", "period": "20251231", "value": 100.0},
                    {"fact_type": "official_payout_floor", "value": 0.30, "valid_from": 2026, "valid_to": 2028},
                ],
                "dividend_events": [],
            }
        }

        result = module.forecast_selected_dividends(
            top_rows=top, evidence_by_code=evidence, run_date="20260902"
        )

        self.assertEqual(result[0]["forecast_profit"], 151.0)
        self.assertAlmostEqual(result[0]["forecast_fy_regular_dps_base"], 0.453)

    def test_bank_model_can_use_three_year_original_evidence_payout_median(self):
        module = load_module()
        top = [{
            "rank": 1, "ts_code": "600036.SH", "name": "招商银行",
            "industry": "银行", "selected": "是", "dividend_score_total": 72.83,
            "is_bank": "是", "bank_quality_gate_passed": "是",
            "bank_core_tier1_capital_ratio": 10.0, "bank_tier1_capital_ratio": 11.0,
            "bank_capital_adequacy_ratio": 14.0,
        }]
        evidence = {
            "600036.SH": {
                "normalized_facts": [
                    {"fact_type": "net_profit_parent", "period": "20251231", "value": 150.0},
                    {"fact_type": "net_profit_parent", "period": "20260630", "value": 76.0},
                    {"fact_type": "net_profit_parent_prior_comparable", "period": "20250630", "value": 75.0},
                    {"fact_type": "total_shares", "period": "20251231", "value": 100.0},
                    {"fact_id": "P23", "fact_type": "historical_payout_ratio", "period": "20231231", "value": 0.30},
                    {"fact_id": "P24", "fact_type": "historical_payout_ratio", "period": "20241231", "value": 0.35},
                    {"fact_id": "P25", "fact_type": "historical_payout_ratio", "period": "20251231", "value": 0.34},
                ],
                "dividend_events": [],
            }
        }

        result = module.forecast_selected_dividends(
            top_rows=top, evidence_by_code=evidence, run_date="20260902"
        )

        self.assertEqual(result[0]["forecast_status"], "modelled")
        self.assertEqual(result[0]["forecast_method"], "historical_payout")
        self.assertEqual(result[0]["model_id"], "bank_historical_payout_capital_v1")
        self.assertAlmostEqual(result[0]["forecast_payout_ratio"], 0.34)
        self.assertIn("三年原始证据", result[0]["forecast_reason"])
        self.assertTrue({"P23", "P24", "P25"}.issubset(set(result[0]["forecast_input_fact_ids"])))

    def test_insurance_model_can_use_original_operating_payout_history(self):
        module = load_module()
        top = [{
            "rank": 1, "ts_code": "601318.SH", "name": "中国平安",
            "industry": "保险", "selected": "是", "dividend_score_total": 82.62,
        }]
        evidence = {
            "601318.SH": {
                "normalized_facts": [
                    {"fact_type": "operating_profit_parent", "period": "20251231", "value": 110.0},
                    {"fact_type": "operating_profit_parent", "period": "20260630", "value": 60.0},
                    {"fact_type": "operating_profit_parent_prior_comparable", "period": "20250630", "value": 50.0},
                    {"fact_type": "total_shares", "period": "20251231", "value": 100.0},
                    {"fact_type": "historical_payout_ratio", "period": "20231231", "value": 0.30},
                    {"fact_type": "historical_payout_ratio", "period": "20241231", "value": 0.32},
                    {"fact_type": "historical_payout_ratio", "period": "20251231", "value": 0.36},
                ],
                "dividend_events": [],
            }
        }

        result = module.forecast_selected_dividends(
            top_rows=top, evidence_by_code=evidence, run_date="20260902"
        )

        self.assertEqual(result[0]["forecast_status"], "modelled")
        self.assertEqual(result[0]["forecast_method"], "historical_payout")
        self.assertEqual(result[0]["model_id"], "insurance_operating_profit_historical_payout_v1")
        self.assertEqual(result[0]["forecast_uncertainty"], "high")

    def test_historical_payout_can_be_recomputed_from_events_shares_and_profit(self):
        module = load_module()
        top = [{
            "rank": 1, "ts_code": "601318.SH", "name": "中国平安",
            "industry": "保险", "selected": "是", "dividend_score_total": 82.62,
        }]
        facts = []
        for year, profit in [(2023, 100.0), (2024, 110.0), (2025, 120.0)]:
            facts.extend([
                {"fact_type": "operating_profit_parent", "period": f"{year}1231", "value": profit},
                {"fact_type": "total_shares", "period": f"{year}1231", "value": 100.0},
            ])
        facts.extend([
            {"fact_type": "operating_profit_parent", "period": "20260630", "value": 60.0},
            {"fact_type": "operating_profit_parent_prior_comparable", "period": "20250630", "value": 50.0},
        ])
        evidence = {
            "601318.SH": {
                "normalized_facts": facts,
                "dividend_events": [
                    {
                        "fiscal_period": f"{year}1231", "regular_or_special": "regular",
                        "cash_dividend_per_share_pre_tax": dps,
                    }
                    for year, dps in [(2023, 0.30), (2024, 0.33), (2025, 0.36)]
                ],
            }
        }

        result = module.forecast_selected_dividends(
            top_rows=top, evidence_by_code=evidence, run_date="20260902"
        )

        self.assertEqual(result[0]["forecast_status"], "modelled")
        self.assertAlmostEqual(result[0]["forecast_payout_ratio"], 0.30)
        self.assertEqual(result[0]["forecast_method"], "historical_payout")


if __name__ == "__main__":
    unittest.main()
