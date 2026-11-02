import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load_module():
    path = SCRIPTS / "backtest_dividend_strategy.py"
    spec = importlib.util.spec_from_file_location("backtest_dividend_strategy", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def dividend_rows(
    per_year: dict[int, float],
    *,
    paid_month: str = "0715",
    shares: dict[int, float] | None = None,
) -> pd.DataFrame:
    rows = []
    for year, dps in per_year.items():
        date = f"{year + 1}{paid_month}"
        rows.append(
            {
                "ts_code": "600000.SH",
                "end_date": f"{year}1231",
                "ann_date": date,
                "div_proc": "实施",
                "stk_div": 0.0,
                "cash_div_tax": dps,
                "record_date": date,
                "ex_date": date,
                "pay_date": date,
                "imp_ann_date": date,
                "base_share": (shares or {}).get(year, 1000.0),
            }
        )
    return pd.DataFrame(rows)


class FakeClient:
    def __init__(self):
        self.calls = []

    def daily(self, **kwargs):
        self.calls.append(("daily", kwargs))
        return pd.DataFrame({"ts_code": [kwargs["ts_code"]], "trade_date": ["20200102"], "close": [1.0]})

    def fina_indicator(self, **kwargs):
        self.calls.append(("fina_indicator", kwargs))
        year = kwargs["start_date"][:4]
        return pd.DataFrame({"ts_code": [kwargs["ts_code"]], "ann_date": [f"{year}0430"], "end_date": [f"{year}0331"]})


class BacktestDividendStrategyTest(unittest.TestCase):
    def test_statement_windows_cover_history_without_gaps(self):
        module = load_module()

        windows = module.statement_windows("20261102")

        self.assertEqual(windows[0][0], f"{module.STATEMENT_HISTORY_START_YEAR}0101")
        self.assertEqual(windows[-1][1], "20261102")
        for (start, stop), (next_start, _) in zip(windows, windows[1:]):
            self.assertEqual(int(next_start[:4]), int(stop[:4]) + 1)
        for start, stop in windows:
            self.assertLessEqual(int(stop[:4]) - int(start[:4]) + 1, module.STATEMENT_WINDOW_YEARS)

    def test_store_fetches_once_pages_statements_and_refuses_offline_gaps(self):
        module = load_module()
        client = FakeClient()
        with tempfile.TemporaryDirectory() as tmp:
            store = module.RawStore(Path(tmp), "20261102", lambda: client, sleep_seconds=0)
            first = store.frame("daily", "600000.SH")
            reread = module.RawStore(Path(tmp), "20261102", lambda: client, sleep_seconds=0, offline=True)
            second = reread.frame("daily", "600000.SH")
            fina = store.frame("fina_indicator", "600000.SH")
            with self.assertRaises(RuntimeError):
                reread.frame("daily", "600036.SH")

        self.assertEqual(sum(name == "daily" for name, _ in client.calls), 1)
        self.assertEqual(first["trade_date"].tolist(), second["trade_date"].tolist())
        self.assertIsInstance(second["trade_date"].iloc[0], str)
        self.assertEqual(
            sum(name == "fina_indicator" for name, _ in client.calls),
            len(module.statement_windows("20261102")),
        )
        self.assertEqual(len(fina), len(module.statement_windows("20261102")))

    def test_pit_statement_drops_unannounced_and_unended_periods(self):
        module = load_module()
        frame = pd.DataFrame(
            {
                "ts_code": ["A"] * 4,
                "ann_date": ["20160428", "20160829", "20161028", "20170420"],
                "f_ann_date": ["20160428", "20160829", "20161102", "20170420"],
                "end_date": ["20160331", "20160630", "20160930", "20161231"],
                "report_type": ["1"] * 4,
                "n_income_attr_p": [1.0, 2.0, 3.0, 4.0],
            }
        )

        result = module.pit_statement(frame, "20161101")

        self.assertEqual(result["end_date"].tolist(), ["20160331", "20160630"])

    def test_slice_market_keeps_five_years_ending_on_period(self):
        module = load_module()
        frame = pd.DataFrame({"trade_date": ["20101231", "20111025", "20161101", "20161102"], "close": [1, 2, 3, 4]})

        result = module.slice_market(frame, "20161101")

        self.assertEqual(result["trade_date"].tolist(), ["20111025", "20161101"])

    def test_constituents_use_latest_snapshot_and_include_delisted(self):
        module = load_module()

        class Store:
            def frame(self, interface, key="all"):
                if interface == "index_weight":
                    codes = [f"{index:06d}.SH" for index in range(300)]
                    return pd.DataFrame(
                        {
                            "index_code": "000300.SH",
                            "con_code": codes * 2,
                            "trade_date": ["20160930"] * 300 + ["20161031"] * 300,
                            "weight": [1.0] * 600,
                        }
                    )
                if interface == "stock_basic":
                    codes = [f"{index:06d}.SH" for index in range(300)]
                    return pd.DataFrame(
                        {
                            "ts_code": codes,
                            "name": codes,
                            "industry": ["银行"] * 300,
                            "list_status": ["D"] + ["L"] * 299,
                            "delist_date": ["20190101"] + [""] * 299,
                        }
                    )
                return pd.DataFrame()

        result = module.period_constituents(Store(), "20161101")

        self.assertEqual(len(result), 300)
        self.assertEqual(set(result["trade_date"]), {"20161031"})
        self.assertIn("000000.SH", set(result["ts_code"]))

    def test_last_complete_fiscal_year_waits_for_final_dividends(self):
        module = load_module()

        self.assertEqual(module.last_complete_fiscal_year("20261102"), 2025)
        self.assertEqual(module.last_complete_fiscal_year("20260601"), 2024)

    def test_dividend_outcome_flags_cuts_and_zero_years(self):
        module = load_module()

        steady = module.dividend_outcome(dividend_rows({2014: 1.0, 2015: 1.0, 2016: 1.0, 2017: 1.02, 2018: 1.1}), 2015, "20261102")
        cut = module.dividend_outcome(dividend_rows({2014: 1.0, 2015: 1.0, 2016: 0.9, 2017: 1.0}), 2015, "20261102")
        stopped = module.dividend_outcome(dividend_rows({2014: 1.0, 2015: 1.0}), 2015, "20261102")
        boundary = module.dividend_outcome(dividend_rows({2015: 1.0, 2016: 0.95, 2017: 0.95}), 2015, "20261102")

        self.assertFalse(steady["cut"])
        self.assertEqual(steady["cut_years_evaluated"], 2)
        self.assertAlmostEqual(steady["dps_growth_3y"], 1.1 ** (1 / 3) - 1)
        self.assertTrue(cut["cut"])
        self.assertEqual(cut["cut_years"], "2016")
        self.assertTrue(stopped["cut"])
        self.assertEqual(stopped["cut_years"], "2016,2017")
        self.assertEqual(stopped["dps_growth_3y"], -1.0)
        self.assertFalse(boundary["cut"])

    def test_dilution_with_maintained_total_is_not_a_cut(self):
        module = load_module()
        diluted = dividend_rows({2023: 0.38, 2024: 0.38, 2025: 0.325}, shares={2025: 1170.0})
        shrunk = dividend_rows({2023: 0.38, 2024: 0.38, 2025: 0.325}, shares={2025: 1100.0})

        maintained = module.dividend_outcome(diluted, 2024, "20261102")
        reduced = module.dividend_outcome(shrunk, 2024, "20261102")

        self.assertFalse(maintained["cut"])
        self.assertTrue(reduced["cut"])

    def test_dividend_outcome_skips_incomplete_windows_and_delisted(self):
        module = load_module()
        rows = dividend_rows({2023: 1.0, 2024: 1.0, 2025: 1.0})

        partial = module.dividend_outcome(rows, 2024, "20261102")
        unseen = module.dividend_outcome(rows, 2025, "20261102")
        delisted = module.dividend_outcome(rows, 2023, "20261102", delist_date="20250301")

        self.assertEqual(partial["cut_years_evaluated"], 1)
        self.assertFalse(partial["cut"])
        self.assertIsNone(partial["dps_growth_3y"])
        self.assertIsNone(unseen["cut"])
        self.assertIsNone(delisted["cut"])
        self.assertTrue(delisted["delisted"])

    def test_holding_return_holds_suspended_security_at_last_close(self):
        module = load_module()
        series = pd.Series([10.0, 11.0, 12.0], index=["20161031", "20170301", "20170601"])

        self.assertAlmostEqual(module.holding_return(series, "20161101", "20171101"), 0.2)
        self.assertIsNone(module.holding_return(series, "20160101", "20171101"))

    def test_structured_only_bank_weights_drop_report_factors(self):
        module = load_module()

        weights = module.factor_weights_for("v1", "structured_only")
        full = module.factor_weights_for("v1", "metrics_file")

        self.assertFalse(any(factor.startswith("bank_") for factor in weights["bank"]))
        self.assertAlmostEqual(sum(weights["bank"].values()), 1.0)
        self.assertAlmostEqual(weights["bank"]["roe"], 0.30)
        self.assertEqual(full["bank"], module.strategy.BANK_FACTOR_WEIGHTS)
        self.assertEqual(weights["nonfinancial"], module.strategy.NONFINANCIAL_FACTOR_WEIGHTS)

    def perturbation_rows(self, count=12, gap=10.0):
        rows = []
        for index in range(count):
            quality = 100.0 - index * gap
            rows.append(
                {
                    "ts_code": f"{index:06d}.SH",
                    "industry": f"行业{index}",
                    "is_financial": False,
                    "is_bank": False,
                    "market_preflight_passed": True,
                    "structured_gate_passed": True,
                    "roe": quality,
                    "droe": quality,
                    "roe_stability_12q": 1.0,
                    "fcf_dividend_coverage_3y": quality,
                    "consecutive_dividend_years_capped": quality,
                    "dps_cut_count_10y": index,
                    "dps_cagr_5y": quality / 1000,
                }
            )
        return rows

    def test_perturbation_keeps_a_well_separated_top10(self):
        module = load_module()
        weights = module.factor_weights_for("v2", "structured_only")

        result = module.perturbation_overlaps(
            self.perturbation_rows(), rule_version="v2", bank_mode="structured_only",
            group_weights=weights, rule_params=None, draws=20, seed=1,
        )

        self.assertEqual(len(result["base_top"]), 10)
        self.assertEqual(result["base_top"][0], "000000.SH")
        self.assertEqual(result["perturbation_mean_overlap"], 10)
        self.assertEqual(result["perturbation_mean_overlap_ratio"], 1.0)

    def test_perturbation_is_seeded_and_detects_fragile_rankings(self):
        module = load_module()
        weights = module.factor_weights_for("v2", "structured_only")
        rows = self.perturbation_rows(count=14)
        # Make the record and quality blocks disagree so weights decide the order.
        for index, row in enumerate(rows):
            row["consecutive_dividend_years_capped"] = float(index)
            row["dps_cut_count_10y"] = 14 - index
            row["dps_cagr_5y"] = index / 1000

        first = module.perturbation_overlaps(rows, rule_version="v2", bank_mode="structured_only", group_weights=weights, rule_params=None, draws=30, seed=7)
        second = module.perturbation_overlaps(rows, rule_version="v2", bank_mode="structured_only", group_weights=weights, rule_params=None, draws=30, seed=7)

        self.assertEqual(first, second)
        self.assertLess(first["perturbation_min_overlap"], 10)

    def test_gate_failures_never_enter_the_rescored_top(self):
        module = load_module()
        rows = self.perturbation_rows()
        rows[0]["structured_gate_passed"] = False

        top = module.rescored_top_codes(rows, rule_version="v2", bank_mode="structured_only", group_weights=module.factor_weights_for("v2", "structured_only"))

        self.assertNotIn("000000.SH", top)


if __name__ == "__main__":
    unittest.main()
