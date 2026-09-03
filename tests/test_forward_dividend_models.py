import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "forward_dividend_models.py"


def load_module():
    spec = importlib.util.spec_from_file_location("forward_dividend_models", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ForwardDividendModelsTest(unittest.TestCase):
    def test_ttm_profit_uses_prior_fy_minus_prior_same_period_plus_current(self):
        module = load_module()

        result = module.compute_ttm_profit(
            prior_fy_profit=330.0,
            prior_same_period_profit=150.0,
            current_same_period_profit=170.0,
        )

        self.assertEqual(result, 350.0)

    def test_dps_scenarios_derive_each_value_from_profit_payout_and_shares(self):
        module = load_module()

        result = module.compute_dps_scenarios(
            profit_scenarios={"low": 315.0, "base": 350.0, "high": 385.0},
            payout_ratio=0.70,
            forecast_total_shares=244.7,
        )

        self.assertAlmostEqual(result["low"], 0.9011, places=4)
        self.assertAlmostEqual(result["base"], 1.0012, places=4)
        self.assertAlmostEqual(result["high"], 1.1013, places=4)

    def test_dps_scenarios_reject_payout_above_one(self):
        module = load_module()

        with self.assertRaisesRegex(ValueError, "payout_ratio"):
            module.compute_dps_scenarios(
                profit_scenarios={"low": 100.0, "base": 100.0, "high": 100.0},
                payout_ratio=1.01,
                forecast_total_shares=100.0,
            )

    def test_target_yield_uses_required_return_less_sustainable_growth(self):
        module = load_module()

        result = module.target_yield_from_analysis(
            company={
                "ts_code": "600900.SH",
                "industry": "水力发电",
                "roe": 17.13,
                "latest_payout_ratio": 0.7092,
                "dps_cagr_5y": 0.05,
            },
            risk_free_rate=0.017,
        )

        self.assertEqual(result["target_yield_model_id"], "required_return_minus_growth_v1")
        self.assertAlmostEqual(result["required_return_low"], 0.08)
        self.assertAlmostEqual(result["required_return_high"], 0.09)
        self.assertAlmostEqual(result["sustainable_dividend_growth"], 0.04)
        self.assertAlmostEqual(result["target_yield_low"], 0.04)
        self.assertAlmostEqual(result["target_yield_high"], 0.05)

    def test_bank_target_yield_adds_half_point_to_lower_bound_only(self):
        module = load_module()

        result = module.target_yield_from_analysis(
            company={
                "ts_code": "600036.SH",
                "industry": "银行",
                "is_bank": "是",
                "roe": 11.52,
                "latest_payout_ratio": 0.3385,
                "dps_cagr_5y": 0.07,
            },
            risk_free_rate=0.017,
        )

        self.assertAlmostEqual(result["sustainable_dividend_growth"], 0.03)
        self.assertAlmostEqual(result["required_return_low"], 0.085)
        self.assertAlmostEqual(result["required_return_high"], 0.10)
        self.assertAlmostEqual(result["target_yield_low"], 0.055)
        self.assertAlmostEqual(result["target_yield_high"], 0.07)
        self.assertEqual(result["target_yield_model_version"], "2")

    def test_valuation_uses_target_yield_and_reports_decision_position(self):
        module = load_module()

        result = module.valuation_from_target_yield(
            base_dps=1.0,
            quote_price=28.4,
            target_yield_low=0.04,
            target_yield_high=0.05,
        )

        self.assertAlmostEqual(result["expected_dividend_yield"], 0.0352113, places=7)
        self.assertAlmostEqual(result["target_price_low"], 20.0, places=4)
        self.assertAlmostEqual(result["target_price_high"], 25.0, places=4)
        self.assertEqual(result["target_status"], "below_target_yield")
        self.assertEqual(result["target_display_label"], "未达目标区间")

    def test_announced_regular_dps_is_not_presented_as_a_model_prediction(self):
        module = load_module()

        result = module.forecast_company(
            company={"ts_code": "600001.SH", "industry": "公用事业"},
            facts={"announced_regular_dps": 1.20, "forecast_fiscal_year": 2026},
        )

        self.assertEqual(result["forecast_status"], "announced")
        self.assertEqual(result["forecast_method"], "announced")
        self.assertEqual(result["model_id"], "announced_regular_dps_v1")
        self.assertEqual(result["forecast_fy_regular_dps_low"], 1.20)
        self.assertEqual(result["forecast_fy_regular_dps_base"], 1.20)
        self.assertEqual(result["forecast_fy_regular_dps_high"], 1.20)
        self.assertEqual(result["forecast_uncertainty"], "low")

    def test_policy_model_requires_explainable_profit_scenarios(self):
        module = load_module()

        result = module.forecast_company(
            company={"ts_code": "600900.SH", "industry": "水力发电"},
            facts={
                "forecast_fiscal_year": 2026,
                "forecast_profit_scenarios": {"low": 315.0, "base": 350.0, "high": 385.0},
                "official_payout_ratio": 0.70,
                "forecast_total_shares": 244.7,
                "evidence_completeness": "complete",
            },
        )

        self.assertEqual(result["forecast_status"], "modelled")
        self.assertEqual(result["forecast_method"], "policy_derived")
        self.assertEqual(result["model_id"], "regular_profit_policy_v1")
        self.assertAlmostEqual(result["forecast_fy_regular_dps_base"], 1.0012, places=4)
        self.assertEqual(result["forecast_uncertainty"], "medium")

    def test_supported_policy_model_with_missing_profit_is_a_data_gap(self):
        module = load_module()

        result = module.forecast_company(
            company={"ts_code": "600900.SH", "industry": "水力发电"},
            facts={
                "forecast_fiscal_year": 2026,
                "official_payout_ratio": 0.70,
                "forecast_total_shares": 244.7,
            },
        )

        self.assertEqual(result["forecast_status"], "data_gap")
        self.assertIsNone(result["forecast_fy_regular_dps_base"])
        self.assertIn("forecast_profit_scenarios", result["forecast_reason"])

    def test_company_without_an_applicable_model_is_unsupported(self):
        module = load_module()

        result = module.forecast_company(
            company={"ts_code": "600188.SH", "industry": "煤炭开采"},
            facts={"forecast_fiscal_year": 2026},
        )

        self.assertEqual(result["forecast_status"], "unsupported")
        self.assertIsNone(result["forecast_fy_regular_dps_base"])
        self.assertEqual(result["forecast_uncertainty"], "not_estimable")

    def test_bank_policy_model_keeps_capital_constraint_in_model_identity(self):
        module = load_module()

        result = module.forecast_company(
            company={
                "ts_code": "600036.SH",
                "industry": "银行",
                "is_bank": "是",
                "bank_quality_gate_passed": "是",
                "bank_core_tier1_capital_ratio": 10.0,
                "bank_tier1_capital_ratio": 11.0,
                "bank_capital_adequacy_ratio": 14.0,
            },
            facts={
                "forecast_fiscal_year": 2026,
                "forecast_profit_scenarios": {"low": 140.0, "base": 150.0, "high": 160.0},
                "official_payout_ratio": 0.30,
                "forecast_total_shares": 100.0,
                "evidence_completeness": "complete",
            },
        )

        self.assertEqual(result["forecast_status"], "modelled")
        self.assertEqual(result["model_id"], "bank_profit_policy_capital_v1")
        self.assertEqual(result["forecast_method"], "policy_derived")
        self.assertAlmostEqual(result["forecast_fy_regular_dps_base"], 0.45)

    def test_bank_model_with_missing_tier1_capital_is_a_data_gap(self):
        module = load_module()

        result = module.forecast_company(
            company={
                "ts_code": "600036.SH",
                "industry": "银行",
                "is_bank": "是",
                "bank_quality_gate_passed": "是",
                "bank_core_tier1_capital_ratio": 10.0,
                "bank_capital_adequacy_ratio": 14.0,
            },
            facts={
                "forecast_fiscal_year": 2026,
                "forecast_profit_scenarios": {"low": 140.0, "base": 150.0, "high": 160.0},
                "official_payout_ratio": 0.30,
                "forecast_total_shares": 100.0,
            },
        )

        self.assertEqual(result["forecast_status"], "data_gap")
        self.assertIsNone(result["forecast_fy_regular_dps_base"])

    def test_insurance_model_uses_operating_profit_not_gaap_profit(self):
        module = load_module()

        result = module.forecast_company(
            company={"ts_code": "601318.SH", "industry": "保险"},
            facts={
                "forecast_fiscal_year": 2026,
                "operating_profit_scenarios": {"low": 110.0, "base": 120.0, "high": 130.0},
                "official_payout_ratio": 0.30,
                "forecast_total_shares": 100.0,
                "evidence_completeness": "complete",
            },
        )

        self.assertEqual(result["forecast_status"], "modelled")
        self.assertEqual(result["model_id"], "insurance_operating_profit_policy_v1")
        self.assertEqual(result["forecast_profit"], 120.0)
        self.assertAlmostEqual(result["forecast_fy_regular_dps_base"], 0.36)

    def test_forward_twelve_month_dps_excludes_already_ex_dividend_events(self):
        module = load_module()
        events = [
            {"regular_or_special": "regular", "ex_dividend_date": "20260801", "cash_dividend_per_share_pre_tax": 0.20},
            {"regular_or_special": "special", "ex_dividend_date": "20261201", "cash_dividend_per_share_pre_tax": 0.10},
            {"regular_or_special": "regular", "ex_dividend_date": "20261201", "cash_dividend_per_share_pre_tax": 0.30},
            {"regular_or_special": "regular", "ex_dividend_date": "20271001", "cash_dividend_per_share_pre_tax": 0.40},
        ]

        result = module.forward_twelve_month_eligible_dps(
            events=events,
            quote_date="20260901",
        )

        self.assertEqual(result, 0.30)


if __name__ == "__main__":
    unittest.main()
