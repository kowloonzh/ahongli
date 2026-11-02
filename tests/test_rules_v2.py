import json
import math
import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import rules_v2  # noqa: E402


def annual_frame(year: int, **values) -> pd.DataFrame:
    return pd.DataFrame([{"ts_code": "A", "end_date": f"{year}1231", **values}])


class DpsCutProfileTest(unittest.TestCase):
    def test_counts_cuts_and_stops_but_not_first_payment(self):
        dps = {2008: 1.0, 2009: 1.0, 2010: 0.9, 2011: 0.9, 2012: 0.0, 2013: 1.0, 2014: 0.95}

        profile = rules_v2.dps_cut_profile(dps, 2014, years=10)

        self.assertEqual(profile["dps_cut_count_10y"], 2)
        self.assertEqual(profile["dps_cut_years_10y"], "2010,2012")

    def test_window_excludes_older_cuts(self):
        dps = {2000: 1.0, 2001: 0.5, **{year: 1.0 for year in range(2002, 2016)}}

        self.assertEqual(rules_v2.dps_cut_profile(dps, 2015)["dps_cut_count_10y"], 0)


class ProfitCycleProfileTest(unittest.TestCase):
    def test_ratio_and_drawdown(self):
        profit = {2006 + index: value for index, value in enumerate([10, 12, 6, 8, 10, 10, 11, 12, 20, 30])}

        profile = rules_v2.profit_cycle_profile(profit, 2015)

        self.assertEqual(profile["profit_dedt_10y_observations"], 10)
        self.assertAlmostEqual(profile["profit_dedt_10y_median"], 10.5)
        self.assertAlmostEqual(profile["profit_dedt_to_10y_median"], 30 / 10.5)
        self.assertAlmostEqual(profile["profit_dedt_10y_max_drawdown"], -0.5)

    def test_loss_years_give_drawdown_beyond_minus_one(self):
        profit = {2006 + index: value for index, value in enumerate([10, 10, -7, 1, 2, 3, 4, 5, 6, 8])}

        profile = rules_v2.profit_cycle_profile(profit, 2015)

        self.assertAlmostEqual(profile["profit_dedt_10y_max_drawdown"], -1.7)

    def test_short_history_is_unjudged(self):
        profit = {year: 1.0 for year in range(2009, 2016)}

        profile = rules_v2.profit_cycle_profile(profit, 2015)

        self.assertIsNone(profile["profit_dedt_to_10y_median"])
        self.assertIsNone(profile["profit_dedt_10y_max_drawdown"])

    def test_nonpositive_median_with_positive_profit_is_infinite(self):
        profit = {2006 + index: value for index, value in enumerate([-1, -1, -1, -1, -1, -1, 1, 1, 1, 2])}

        self.assertEqual(rules_v2.profit_cycle_profile(profit, 2015)["profit_dedt_to_10y_median"], math.inf)


class InterestCoverageTest(unittest.TestCase):
    def test_prefers_tushare_ratio(self):
        fina = annual_frame(2022, ebit_to_interest=7.4, netdebt=1e11)
        income = annual_frame(2022, ebit=27.0, fin_exp_int_exp=4.0, total_profit=26.0)

        profile = rules_v2.interest_coverage_profile(fina, 2022, income)

        self.assertEqual(profile["ebit_to_interest_latest"], 7.4)
        self.assertEqual(profile["ebit_to_interest_source"], "fina_indicator")

    def test_blank_ratio_falls_back_to_income_statement(self):
        fina = annual_frame(2024, ebit_to_interest=None, netdebt=2e11)
        with_ebit = annual_frame(2024, ebit=26.6, fin_exp_int_exp=7.8, total_profit=18.1)
        without_ebit = annual_frame(2024, ebit=None, fin_exp_int_exp=8.0, total_profit=16.0)

        first = rules_v2.interest_coverage_profile(fina, 2024, with_ebit)
        second = rules_v2.interest_coverage_profile(fina, 2024, without_ebit)

        self.assertAlmostEqual(first["ebit_to_interest_latest"], 26.6 / 7.8)
        self.assertEqual(first["ebit_to_interest_source"], "income")
        self.assertAlmostEqual(second["ebit_to_interest_latest"], 3.0)

    def test_gate_rules(self):
        gate = rules_v2.interest_coverage_gate
        self.assertEqual(gate({"is_financial": True}, 3)[0], "not_applicable")
        self.assertEqual(gate({"netdebt_latest": -1.0, "ebit_to_interest_latest": 1.0}, 3)[0], "passed")
        self.assertEqual(gate({"netdebt_latest": 1.0, "ebit_to_interest_latest": 3.0}, 3)[0], "passed")
        self.assertEqual(gate({"netdebt_latest": 1.0, "ebit_to_interest_latest": 2.9}, 3)[0], "hard_gate_failed")
        self.assertEqual(gate({"netdebt_latest": 1.0, "ebit_to_interest_source": "income:no_interest_expense"}, 3)[0], "passed")
        self.assertEqual(gate({"netdebt_latest": 1.0}, 3)[0], "data_gap")


class ApplyV2Test(unittest.TestCase):
    def record(self, **overrides):
        base = {
            "ts_code": "A",
            "is_financial": False,
            "dividend_anchor_year": 2015,
            "consecutive_dividend_years": 20,
            "annual_comparable_dps_json": json.dumps({str(year): 1.0 for year in range(2000, 2016)}),
            "annual_profit_dedt_json": json.dumps({str(2006 + i): v for i, v in enumerate([10] * 9 + [25])}),
            "structured_gate_passed": True,
            "structured_gate_status": "passed",
            "structured_gate_reason": "ok",
        }
        return {**base, **overrides}

    def test_gates_only_flip_passers_and_keep_earlier_reasons(self):
        params = rules_v2.resolve_params({"cycle_gate_max_ratio": 2})
        fina = annual_frame(2015, ebit_to_interest=10.0, netdebt=1.0)

        cyclical = rules_v2.apply_v2(self.record(), fina, params)
        failed = rules_v2.apply_v2(self.record(structured_gate_passed=False, structured_gate_reason="支付率"), fina, params)

        self.assertEqual(cyclical["consecutive_dividend_years_capped"], 15)
        self.assertEqual(cyclical["dps_cut_count_10y"], 0)
        self.assertFalse(cyclical["structured_gate_passed"])
        self.assertIn("2.5", cyclical["structured_gate_reason"])
        self.assertEqual(failed["structured_gate_reason"], "支付率")

    def test_cycle_gate_can_skip_financials(self):
        params = rules_v2.resolve_params({"cycle_gate_max_ratio": 2, "cycle_gate_nonfinancial_only": 1})
        fina = annual_frame(2015, ebit_to_interest=10.0, netdebt=1.0)

        bank = rules_v2.apply_v2(self.record(is_financial=True), fina, params)
        industrial = rules_v2.apply_v2(self.record(), fina, params)

        self.assertTrue(bank["structured_gate_passed"])
        self.assertEqual(bank["cycle_gate_status"], "not_applicable")
        self.assertFalse(industrial["structured_gate_passed"])

    def test_default_params_do_not_apply_cycle_gate(self):
        fina = annual_frame(2015, ebit_to_interest=10.0, netdebt=1.0)

        result = rules_v2.apply_v2(self.record(), fina, rules_v2.resolve_params())

        self.assertTrue(result["structured_gate_passed"])
        self.assertEqual(result["cycle_gate_status"], "not_applied")

    def test_unknown_parameter_is_rejected(self):
        with self.assertRaises(ValueError):
            rules_v2.resolve_params({"interest_cover": 2})


class ScoreV2Test(unittest.TestCase):
    def rows(self):
        def row(code, bank, roe, years, cuts, cagr):
            return {
                "ts_code": code,
                "is_financial": bank,
                "is_bank": bank,
                "structured_gate_passed": True,
                "roe": roe,
                "droe": 0.0,
                "roe_stability_12q": 1.0,
                "fcf_dividend_coverage_3y": roe / 10,
                "consecutive_dividend_years_capped": years,
                "dps_cut_count_10y": cuts,
                "dps_cagr_5y": cagr,
                "profit_dedt_to_10y_median": 1.0,
            }

        return [
            row("B1", True, 12.0, 15, 0, 0.05),
            row("B2", True, 10.0, 10, 1, 0.02),
            row("N1", False, 20.0, 15, 2, 0.08),
            row("N2", False, 15.0, 8, 0, 0.01),
        ]

    def test_total_is_half_cross_group_record_half_within_group_quality(self):
        scored = {row["ts_code"]: row for row in rules_v2.score_v2(self.rows(), rules_v2.resolve_params(), "structured_only")}

        for row in scored.values():
            self.assertAlmostEqual(row["dividend_score_total"], 0.5 * row["record_score"] + 0.5 * row["quality_score"], places=1)
        # Quality is within group: the better bank and the better nonfinancial both top their group.
        self.assertGreater(scored["B1"]["quality_score"], scored["B2"]["quality_score"])
        self.assertGreater(scored["N1"]["quality_score"], scored["N2"]["quality_score"])
        self.assertGreater(scored["B2"]["quality_score"], 0)
        # Record block ranks across groups: B1 has the most years, no cuts, mid growth.
        self.assertGreater(scored["B1"]["record_score"], scored["B2"]["record_score"])
        self.assertGreater(scored["B1"]["record_score"], scored["N2"]["record_score"])

    def test_gate_failures_get_no_total(self):
        rows = self.rows()
        rows[0]["structured_gate_passed"] = False

        scored = {row["ts_code"]: row for row in rules_v2.score_v2(rows, rules_v2.resolve_params(), "structured_only")}

        self.assertIsNone(scored["B1"]["dividend_score_total"])
        self.assertIsNotNone(scored["B2"]["dividend_score_total"])

    def test_cycle_factor_weight_rewards_normal_profit_level(self):
        rows = self.rows()
        rows[2]["profit_dedt_to_10y_median"] = 2.5
        params = rules_v2.resolve_params({"cycle_factor_weight": 0.2})

        plain = {row["ts_code"]: row for row in rules_v2.score_v2(rows, rules_v2.resolve_params(), "structured_only")}
        penalized = {row["ts_code"]: row for row in rules_v2.score_v2(rows, params, "structured_only")}

        self.assertLess(penalized["N1"]["dividend_score_total"], plain["N1"]["dividend_score_total"])


if __name__ == "__main__":
    unittest.main()
