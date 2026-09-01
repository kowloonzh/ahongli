import importlib.util
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "SKILL.md"
SCRIPT = ROOT / "scripts" / "run_a_dividend_strategy.py"


def load_module():
    spec = importlib.util.spec_from_file_location("run_a_dividend_strategy", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ADividendStrategyTest(unittest.TestCase):
    def test_default_universe_is_hs300(self):
        module = load_module()

        self.assertEqual(getattr(module, "INDEX_CODE", None), "000300.SH")
        self.assertEqual(getattr(module, "INDEX_NAME", None), "沪深300")
        self.assertEqual(module.output_dir("20260806").parent.name, "a_dividend_outputs")

    def test_build_latest_constituents_requires_complete_hs300_snapshot(self):
        module = load_module()
        weights = pd.DataFrame(
            [
                {
                    "index_code": "000300.SH",
                    "con_code": f"{index:06d}.SZ",
                    "trade_date": "20260731",
                    "weight": str(300 - index),
                }
                for index in range(300)
            ]
        )
        basic = pd.DataFrame(
            [
                {
                    "ts_code": f"{index:06d}.SZ",
                    "symbol": f"{index:06d}",
                    "name": f"公司{index}",
                    "area": "测试",
                    "industry": "测试行业",
                    "market": "主板",
                    "list_date": "20000101",
                }
                for index in range(300)
            ]
        )

        builder = getattr(module, "build_latest_constituents", None)
        self.assertTrue(callable(builder), "缺少沪深300成分股标准化函数")
        result = builder(weights, basic, "20260806")

        self.assertEqual(len(result), 300)
        self.assertEqual(result.iloc[0]["index_code"], "000300.SH")
        self.assertEqual(result.iloc[0]["name"], "公司0")

    def test_fetch_market_data_uses_hs300_index_and_writes_required_files(self):
        module = load_module()

        class FakePro:
            requested_index_code = ""

            def index_weight(self, **kwargs):
                self.requested_index_code = kwargs["index_code"]
                return pd.DataFrame(
                    [
                        {
                            "index_code": "000300.SH",
                            "con_code": "000001.SZ",
                            "trade_date": "20260731",
                            "weight": "60",
                        },
                        {
                            "index_code": "000300.SH",
                            "con_code": "600000.SH",
                            "trade_date": "20260731",
                            "weight": "40",
                        },
                    ]
                )

            def stock_basic(self, **kwargs):
                return pd.DataFrame(
                    [
                        {
                            "ts_code": "000001.SZ",
                            "symbol": "000001",
                            "name": "平安银行",
                            "area": "深圳",
                            "industry": "银行",
                            "market": "主板",
                            "list_date": "19910403",
                        },
                        {
                            "ts_code": "600000.SH",
                            "symbol": "600000",
                            "name": "浦发银行",
                            "area": "上海",
                            "industry": "银行",
                            "market": "主板",
                            "list_date": "19991110",
                        },
                    ]
                )

            def stock_company(self, **kwargs):
                exchange = kwargs["exchange"]
                rows = {
                    "SZSE": [
                        {
                            "ts_code": "000001.SZ",
                            "exchange": "SZSE",
                            "introduction": "全国性股份制银行",
                            "main_business": "商业银行业务",
                            "business_scope": "吸收公众存款、发放贷款",
                        }
                    ],
                    "SSE": [
                        {
                            "ts_code": "600000.SH",
                            "exchange": "SSE",
                            "introduction": "全国性股份制银行",
                            "main_business": "商业银行业务",
                            "business_scope": "吸收公众存款、发放贷款",
                        }
                    ],
                }
                return pd.DataFrame(rows[exchange])

            def daily(self, **kwargs):
                return pd.DataFrame(
                    [
                        {
                            "ts_code": kwargs["ts_code"],
                            "trade_date": "20260731",
                            "close": "10",
                            "low": "9",
                            "high": "11",
                        }
                    ]
                )

            def daily_basic(self, **kwargs):
                return pd.DataFrame(
                    [
                        {
                            "ts_code": kwargs["ts_code"],
                            "trade_date": "20260731",
                            "dv_ttm": "4",
                        }
                    ]
                )

        fetcher = getattr(module, "fetch_hs300_market_data", None)
        self.assertTrue(callable(fetcher), "缺少沪深300行情抓取函数")
        fake = FakePro()
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            module, "EXPECTED_CONSTITUENT_COUNT", 2
        ):
            market = Path(tmp) / "market_data"
            fetcher(
                "20260806",
                start_market="20210806",
                sleep_seconds=0,
                pro=fake,
                market_dir=market,
            )

            self.assertEqual(fake.requested_index_code, "000300.SH")
            constituents = pd.read_csv(market / "constituents.csv")
            self.assertEqual(len(constituents), 2)
            self.assertEqual(set(constituents["main_business"]), {"商业银行业务"})
            self.assertTrue(constituents["business_scope"].str.contains("发放贷款").all())
            self.assertTrue(constituents["introduction"].str.contains("股份制银行").all())
            daily_files = [
                path
                for path in market.glob("daily_*.csv")
                if not path.name.startswith("daily_basic_")
            ]
            self.assertEqual(len(daily_files), 2)
            self.assertEqual(len(list(market.glob("daily_basic_*.csv"))), 2)

    def test_fetch_market_data_uses_bounded_parallel_requests_when_requested(self):
        module = load_module()

        class FakePro:
            active = 0
            max_active = 0
            lock = threading.Lock()

            def index_weight(self, **kwargs):
                return pd.DataFrame(
                    [
                        {
                            "index_code": "000300.SH",
                            "con_code": f"60000{index}.SH",
                            "trade_date": "20260807",
                            "weight": str(3 - index),
                        }
                        for index in range(3)
                    ]
                )

            def stock_basic(self, **kwargs):
                return pd.DataFrame(
                    [
                        {
                            "ts_code": f"60000{index}.SH",
                            "symbol": f"60000{index}",
                            "name": f"公司{index}",
                            "area": "测试",
                            "industry": "测试",
                            "market": "主板",
                            "list_date": "20000101",
                        }
                        for index in range(3)
                    ]
                )

            def stock_company(self, **kwargs):
                suffix = ".SH" if kwargs["exchange"] == "SSE" else ".SZ"
                rows = []
                for index in range(3):
                    ts_code = f"60000{index}.SH"
                    if ts_code.endswith(suffix):
                        rows.append(
                            {
                                "ts_code": ts_code,
                                "exchange": kwargs["exchange"],
                                "introduction": f"公司{index}介绍",
                                "main_business": f"公司{index}主营业务",
                                "business_scope": f"公司{index}经营范围",
                            }
                        )
                return pd.DataFrame(rows)

            def _response(self, interface, **kwargs):
                with self.lock:
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                time.sleep(0.03)
                with self.lock:
                    self.active -= 1
                if interface == "daily":
                    return pd.DataFrame([
                        {"ts_code": kwargs["ts_code"], "trade_date": "20260807", "close": 10, "low": 9, "high": 11}
                    ])
                return pd.DataFrame([
                    {"ts_code": kwargs["ts_code"], "trade_date": "20260807", "dv_ttm": 4}
                ])

            def daily(self, **kwargs):
                return self._response("daily", **kwargs)

            def daily_basic(self, **kwargs):
                return self._response("daily_basic", **kwargs)

        fake = FakePro()
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            module, "EXPECTED_CONSTITUENT_COUNT", 3
        ):
            market = Path(tmp) / "market_data"
            module.fetch_hs300_market_data(
                "20260810",
                start_market="20210804",
                sleep_seconds=0,
                pro=fake,
                market_dir=market,
                max_workers=3,
            )

            self.assertGreaterEqual(fake.max_active, 2)
            self.assertEqual(len(list(market.glob("daily_*.csv"))), 6)

    def test_ensure_market_data_uses_local_hs300_fetcher(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            module, "REPO_ROOT", Path(tmp)
        ), patch.object(module, "fetch_hs300_market_data") as fetcher:
            expected = Path(tmp) / "a_dividend_outputs" / "20260806" / "market_data"
            fetcher.return_value = expected

            try:
                result = module.ensure_market_data(
                    "20260806",
                    skip_fetch=False,
                    refresh_constituents=True,
                    sleep_seconds=0,
                )
            except NameError as exc:
                self.fail(f"行情入口仍依赖旧A50模块：{exc}")

            self.assertEqual(result, expected)
            fetcher.assert_called_once()
            self.assertTrue(fetcher.call_args.kwargs["refresh_constituents"])

    def test_dividend_preflight_requires_enough_valid_days_and_coverage(self):
        module = load_module()
        too_few = pd.DataFrame(
            {
                "trade_date": [f"day-{index}" for index in range(999)],
                "dv_ttm": [4.0] * 999,
            }
        )
        low_coverage = pd.DataFrame(
            {
                "trade_date": [f"day-{index}" for index in range(1300)],
                "dv_ttm": [4.0] * 1000 + [None] * 300,
            }
        )
        sufficient = pd.DataFrame(
            {
                "trade_date": [f"day-{index}" for index in range(1200)],
                "dv_ttm": [4.0] * 1000 + [None] * 200,
            }
        )

        too_few_profile = module.dividend_yield_profile(too_few)
        low_coverage_profile = module.dividend_yield_profile(low_coverage)
        sufficient_profile = module.dividend_yield_profile(sufficient)

        self.assertEqual(too_few_profile.get("dividend_yield_valid_days_5y"), 999)
        self.assertFalse(too_few_profile.get("dividend_yield_data_sufficient"))
        self.assertFalse(too_few_profile["long_term_dividend_above_3"])
        self.assertAlmostEqual(
            low_coverage_profile.get("dividend_yield_data_coverage_5y"), 1000 / 1300, places=4
        )
        self.assertFalse(low_coverage_profile.get("dividend_yield_data_sufficient"))
        self.assertFalse(low_coverage_profile["long_term_dividend_above_3"])
        self.assertTrue(sufficient_profile.get("dividend_yield_data_sufficient"))
        self.assertTrue(sufficient_profile["long_term_dividend_above_3"])

    def test_insufficient_dividend_data_fails_before_financial_report(self):
        module = load_module()
        row = {
            "current_price": 10,
            "current_dividend_yield": 4,
            "dividend_yield_valid_days_5y": 999,
            "dividend_yield_observation_days_5y": 999,
            "dividend_yield_data_coverage_5y": 1.0,
            "dividend_yield_data_sufficient": False,
            "dividend_yield_ge3_ratio": 1.0,
            "long_term_dividend_above_3": False,
        }

        reason = module.evaluate_market_dividend_preconditions(row)

        self.assertIn("有效数据不足", reason)
        self.assertIn("1000", reason)

    def test_direct_real_estate_policy_excludes_only_confirmed_main_business(self):
        module = load_module()

        direct = module.real_estate_relevance(
            {"industry": "全国地产", "main_business": "房地产开发与商品房销售"}
        )
        construction = module.real_estate_relevance(
            {"industry": "建筑工程", "main_business": "建筑施工、基建投资及房地产开发"}
        )

        self.assertEqual(direct["real_estate_relevance_score"], 100)
        self.assertTrue(module.is_real_estate_related({**direct}))
        self.assertEqual(construction["real_estate_relevance_score"], 0)
        self.assertFalse(module.is_real_estate_related({**construction}))

    def test_portfolio_scope_gate_uses_structured_business_without_company_quality(self):
        module = load_module()

        passed = module.portfolio_scope_gate_profile(
            {
                "industry": "建筑工程",
                "main_business": "建筑施工、基础设施建设",
                "business_scope": "建筑施工；房地产开发经营",
            }
        )
        failed = module.portfolio_scope_gate_profile(
            {
                "industry": "全国地产",
                "main_business": "房地产开发、销售、租赁及物业管理",
                "business_scope": "房地产开发经营",
            }
        )

        self.assertTrue(passed["portfolio_scope_gate_passed"])
        self.assertNotIn("company_quality", passed)
        self.assertFalse(failed["portfolio_scope_gate_passed"])

    def test_empty_tushare_csv_is_treated_as_missing_data(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            market = Path(tmp)
            empty_csv = market / "daily_basic_600027.SH_20210728_20260806.csv"
            empty_csv.write_text("\ufeff\n", encoding="utf-8")

            try:
                result = module.read_market_csv(market, "daily_basic", "600027.SH")
            except pd.errors.EmptyDataError:
                self.fail("空的 Tushare CSV 应按缺失数据处理，而不是中断全量筛选")

            self.assertTrue(result.empty)

    def test_skill_contract_describes_hs300_without_a50_scope(self):
        text = SKILL.read_text(encoding="utf-8")

        self.assertIn("name: ahongli", text)
        self.assertIn("沪深300", text)
        self.assertIn("stock_company", text)
        self.assertIn("does not consume subjective `financial-report-reader`", text)
        self.assertNotIn("公司质量", text)
        self.assertNotIn("latest_period", text)
        self.assertNotIn("中证A50", text)
        self.assertNotIn("a50-dividend", text)

    def test_implemented_cash_dividends_use_tax_inclusive_field_and_ignore_stock_only_events(self):
        module = load_module()
        dividends = pd.DataFrame(
            [
                {
                    "end_date": "20241231",
                    "div_proc": "实施",
                    "imp_ann_date": "20250601",
                    "cash_div": 0.08,
                    "cash_div_tax": 0.10,
                    "base_share": 10000,
                },
                {
                    "end_date": "20241231",
                    "div_proc": "实施",
                    "imp_ann_date": "20250602",
                    "cash_div": None,
                    "cash_div_tax": None,
                    "stk_div": 0.5,
                    "base_share": 10000,
                },
            ]
        )

        events = module._implemented_cash_dividend_events(dividends, "20260820")

        self.assertEqual(len(events), 1)
        self.assertEqual(events.iloc[0]["_cash_div_tax"], 0.10)
        self.assertEqual(events.iloc[0]["_cash_dividend_total_yuan"], 10_000_000)

    def test_dividend_history_normalizes_dps_for_stock_dividends_and_does_not_cap_years(self):
        module = load_module()
        rows = []
        for year in range(2006, 2026):
            rows.append(
                {
                    "end_date": f"{year}1231",
                    "div_proc": "实施",
                    "imp_ann_date": f"{year + 1}0601",
                    "cash_div_tax": 1.0 if year < 2025 else 0.5,
                    "base_share": 10000,
                    "stk_div": 1.0 if year == 2024 else 0.0,
                }
            )

        profile = module.dividend_history_profile(pd.DataFrame(rows), "20260820")

        self.assertEqual(profile["dividend_anchor_year"], 2025)
        self.assertEqual(profile["consecutive_dividend_years"], 20)
        self.assertAlmostEqual(profile["annual_comparable_dps"][2024], 0.5)
        self.assertAlmostEqual(profile["annual_comparable_dps"][2025], 0.5)
        self.assertAlmostEqual(profile["latest_dps_to_prior3_median"], 1.0)

    def test_stock_only_implemented_event_adjusts_comparable_dps_without_counting_as_cash(self):
        module = load_module()
        rows = [
            {
                "end_date": "20241231",
                "div_proc": "实施",
                "imp_ann_date": "20250601",
                "cash_div_tax": 1.0,
                "base_share": 10000,
            },
            {
                "end_date": "20241231",
                "div_proc": "实施",
                "imp_ann_date": "20250602",
                "cash_div_tax": None,
                "base_share": 10000,
                "stk_div": 1.0,
            },
            {
                "end_date": "20251231",
                "div_proc": "实施",
                "imp_ann_date": "20260601",
                "cash_div_tax": 0.5,
                "base_share": 20000,
            },
        ]

        profile = module.dividend_history_profile(pd.DataFrame(rows), "20260820")

        self.assertAlmostEqual(profile["annual_comparable_dps"][2024], 0.5)
        self.assertAlmostEqual(profile["annual_comparable_dps"][2025], 0.5)
        self.assertEqual(profile["annual_cash_dividend_total_yuan"][2024], 100_000_000)

    def test_explicit_no_dividend_does_not_allow_anchor_year_fallback(self):
        module = load_module()
        dividends = pd.DataFrame(
            [
                {
                    "end_date": "20241231",
                    "div_proc": "实施",
                    "imp_ann_date": "20250601",
                    "cash_div_tax": 0.5,
                    "base_share": 10000,
                }
            ]
        )

        profile = module.dividend_history_profile(
            dividends,
            "20260820",
            explicit_no_dividend_years={2025},
        )

        self.assertEqual(profile["dividend_anchor_year"], 2025)
        self.assertEqual(profile["consecutive_dividend_years"], 0)
        self.assertEqual(profile["data_quality"], "normal")

    def test_ttm_roe_uses_annual_plus_current_ytd_minus_prior_ytd(self):
        module = load_module()
        income = pd.DataFrame(
            [
                {"end_date": "20240331", "n_income_attr_p": 20},
                {"end_date": "20241231", "n_income_attr_p": 100},
                {"end_date": "20250331", "n_income_attr_p": 30},
            ]
        )
        balance = pd.DataFrame(
            [
                {"end_date": "20240331", "total_hldr_eqy_exc_min_int": 1000},
                {"end_date": "20250331", "total_hldr_eqy_exc_min_int": 1100},
            ]
        )

        result = module.build_ttm_roe_series(income, balance)
        row = result.loc[result["end_date"].eq("20250331")].iloc[0]

        self.assertEqual(row["ttm_parent_net_profit"], 110)
        self.assertAlmostEqual(row["ttm_roe"], 110 / 1050 * 100)

    def test_droe_compares_same_quarter_ttm_roe(self):
        module = load_module()
        series = pd.DataFrame(
            [
                {"end_date": "20250331", "ttm_roe": 10.0},
                {"end_date": "20260331", "ttm_roe": 12.5},
            ]
        )

        self.assertEqual(module.latest_roe_metrics(series)["droe"], 2.5)

    def test_payout_ratio_gate_uses_three_year_average_and_latest_bounds(self):
        module = load_module()

        passed = module.payout_ratio_gate({2023: 0.20, 2024: 0.30, 2025: 0.40})
        failed = module.payout_ratio_gate({2023: 0.05, 2024: 0.10, 2025: 0.15})

        self.assertTrue(passed["passed"])
        self.assertFalse(failed["passed"])
        self.assertEqual(failed["three_year_average_payout_ratio"], 0.10)

    def test_profit_trend_gate_requires_four_positive_years_and_nonnegative_endpoint_change(self):
        module = load_module()

        self.assertTrue(module.profit_trend_gate({2022: 100, 2023: 85, 2024: 92, 2025: 105})["passed"])
        self.assertFalse(module.profit_trend_gate({2022: 100, 2023: 120, 2024: 110, 2025: 90})["passed"])
        self.assertFalse(module.profit_trend_gate({2022: 100, 2023: -1, 2024: 110, 2025: 120})["passed"])

    def test_cashflow_dividend_gate_uses_latest_and_three_year_median(self):
        module = load_module()

        passed = module.cashflow_dividend_gate({2023: 1.1, 2024: 0.9, 2025: 1.2})
        failed = module.cashflow_dividend_gate({2023: 1.2, 2024: 1.1, 2025: 0.8})

        self.assertTrue(passed["passed"])
        self.assertFalse(failed["passed"])
        self.assertEqual(failed["latest_cashflow_dividend_coverage"], 0.8)

    def test_audit_opinion_is_a_structured_hard_gate(self):
        module = load_module()

        self.assertTrue(module.audit_opinion_gate("标准无保留意见")["passed"])
        self.assertFalse(module.audit_opinion_gate("保留意见")["passed"])

    def test_weighted_scores_use_separate_financial_and_nonfinancial_weights(self):
        module = load_module()
        perfect = {
            "roe_percentile": 100,
            "droe_percentile": 100,
            "opcfd_percentile": 100,
            "fcf_dividend_coverage_percentile": 100,
            "dividend_yield_cv_percentile": 100,
            "consecutive_dividend_years_percentile": 100,
            "dps_cagr_5y_percentile": 100,
            "price_score": 0,
            "current_yield_score": 0,
            "dividend_financing_score": 0,
        }

        nonfinancial = module.weighted_quality_score({**perfect, "is_financial": False})
        financial = module.weighted_quality_score({**perfect, "is_financial": True})

        self.assertEqual(sum(module.NONFINANCIAL_FACTOR_WEIGHTS.values()), 1.0)
        self.assertEqual(sum(module.FINANCIAL_FACTOR_WEIGHTS.values()), 1.0)
        self.assertEqual(nonfinancial["dividend_score_total"], 100.0)
        self.assertEqual(financial["dividend_score_total"], 100.0)
        self.assertNotIn("price_score", nonfinancial["score_contributions"])
        self.assertNotIn("dividend_financing_score", financial["score_contributions"])

    def test_bank_quality_profile_requires_three_years_and_applies_safety_gates(self):
        module = load_module()
        records = [
            {
                "period": period,
                "npl_ratio": npl,
                "provision_coverage_ratio": provision,
                "loan_provision_ratio": loan_provision,
                "net_interest_margin": nim,
                "cost_income_ratio": cost,
                "core_tier1_capital_ratio": core,
                "tier1_capital_ratio": tier1,
                "capital_adequacy_ratio": capital,
                "data_quality": "normal",
            }
            for period, npl, provision, loan_provision, nim, cost, core, tier1, capital in [
                ("20231231", 1.30, 220, 2.80, 1.40, 30, 10.0, 11.0, 14.0),
                ("20241231", 1.20, 230, 2.85, 1.30, 29, 10.3, 11.2, 14.2),
                ("20251231", 1.10, 240, 2.90, 1.20, 28, 10.6, 11.4, 14.5),
            ]
        ]

        passed = module.bank_quality_profile(records)
        failed = module.bank_quality_profile(
            [*records[:-1], {**records[-1], "npl_ratio": 2.10}]
        )
        failed_loan_provision = module.bank_quality_profile(
            [*records[:-1], {**records[-1], "loan_provision_ratio": 2.49}]
        )

        self.assertTrue(passed["bank_quality_gate_passed"])
        self.assertEqual(passed["bank_npl_ratio"], 1.10)
        self.assertAlmostEqual(passed["bank_npl_change_3y"], -0.20)
        self.assertAlmostEqual(passed["bank_nim_change_3y"], -0.20)
        self.assertEqual(passed.get("bank_required_loan_provision_ratio"), 2.50)
        self.assertAlmostEqual(passed.get("bank_excess_loan_provision_ratio"), 0.40)
        self.assertFalse(failed["bank_quality_gate_passed"])
        self.assertIn("不良贷款率", failed["bank_quality_gate_reason"])
        self.assertFalse(failed_loan_provision["bank_quality_gate_passed"])
        self.assertIn("贷款拨备率", failed_loan_provision["bank_quality_gate_reason"])

    def test_bank_weight_model_is_independent_and_totals_one_hundred(self):
        module = load_module()
        perfect = {
            "is_bank": True,
            "roe_percentile": 100,
            "droe_percentile": 100,
            "bank_npl_quality_percentile": 100,
            "bank_provision_quality_percentile": 100,
            "bank_capital_resilience_percentile": 100,
            "bank_nim_quality_percentile": 100,
            "bank_cost_income_ratio_percentile": 100,
            "dividend_yield_cv_percentile": 100,
            "consecutive_dividend_years_percentile": 100,
            "dps_cagr_5y_percentile": 100,
        }

        score = module.weighted_quality_score(perfect)

        self.assertEqual(sum(module.BANK_FACTOR_WEIGHTS.values()), 1.0)
        self.assertEqual(score["dividend_score_total"], 100.0)

    def test_bank_minimal_rebalance_prioritizes_levels_over_yield_cv_and_trends(self):
        module = load_module()

        self.assertEqual(module.BANK_FACTOR_WEIGHTS["roe"], 0.15)
        self.assertEqual(module.BANK_FACTOR_WEIGHTS["dividend_yield_cv_5y"], 0.10)
        self.assertEqual(sum(module.BANK_FACTOR_WEIGHTS.values()), 1.0)

        frame = pd.DataFrame(
            [
                {
                    "bank_npl_ratio": 0.9,
                    "bank_npl_change_3y": -0.01,
                    "bank_provision_coverage_ratio": 390,
                    "bank_loan_provision_ratio": 3.7,
                    "bank_core_tier1_capital_ratio": 14,
                    "bank_capital_adequacy_ratio": 18,
                    "bank_net_interest_margin": 1.9,
                    "bank_nim_change_3y": -0.30,
                },
                {
                    "bank_npl_ratio": 1.1,
                    "bank_npl_change_3y": -0.10,
                    "bank_provision_coverage_ratio": 360,
                    "bank_loan_provision_ratio": 4.0,
                    "bank_core_tier1_capital_ratio": 12,
                    "bank_capital_adequacy_ratio": 14,
                    "bank_net_interest_margin": 1.6,
                    "bank_nim_change_3y": -0.10,
                },
            ]
        )

        module._build_bank_composite_percentiles(frame)

        self.assertEqual(frame.loc[0, "bank_npl_quality_percentile"], 90.0)
        self.assertEqual(frame.loc[0, "bank_nim_quality_percentile"], 85.0)
        self.assertEqual(frame.loc[0, "bank_provision_quality_percentile"], 100.0)

    def test_bank_cross_section_rewards_asset_quality_capital_and_efficiency(self):
        module = load_module()
        shared = {
            "is_financial": True,
            "is_bank": True,
            "roe": 10,
            "droe": 0,
            "dividend_yield_cv_5y": 0.2,
            "consecutive_dividend_years": 10,
            "dps_cagr_5y": 0.05,
        }
        rows = [
            {
                **shared,
                "ts_code": "GOOD",
                "bank_npl_ratio": 0.8,
                "bank_npl_change_3y": -0.2,
                "bank_provision_coverage_ratio": 350,
                "bank_loan_provision_ratio": 3.5,
                "bank_core_tier1_capital_ratio": 13,
                "bank_capital_adequacy_ratio": 17,
                "bank_net_interest_margin": 1.8,
                "bank_nim_change_3y": 0.0,
                "bank_cost_income_ratio": 25,
            },
            {
                **shared,
                "ts_code": "WEAK",
                "bank_npl_ratio": 1.8,
                "bank_npl_change_3y": 0.3,
                "bank_provision_coverage_ratio": 160,
                "bank_loan_provision_ratio": 2.0,
                "bank_core_tier1_capital_ratio": 8,
                "bank_capital_adequacy_ratio": 11,
                "bank_net_interest_margin": 1.0,
                "bank_nim_change_3y": -0.3,
                "bank_cost_income_ratio": 45,
            },
        ]

        scored = {row["ts_code"]: row for row in module.score_cross_section(rows)}

        self.assertGreater(
            scored["GOOD"]["dividend_score_total"],
            scored["WEAK"]["dividend_score_total"],
        )
        self.assertGreater(
            scored["GOOD"]["bank_npl_quality_percentile"],
            scored["WEAK"]["bank_npl_quality_percentile"],
        )

    def test_single_insurer_uses_all_financials_as_percentile_reference(self):
        module = load_module()
        bank = {
            "ts_code": "BANK",
            "is_financial": True,
            "is_bank": True,
            "roe": 20,
            "droe": 2,
            "dividend_yield_cv_5y": 0.10,
            "consecutive_dividend_years": 20,
            "dps_cagr_5y": 0.10,
            "bank_npl_ratio": 0.8,
            "bank_npl_change_3y": -0.2,
            "bank_provision_coverage_ratio": 350,
            "bank_loan_provision_ratio": 3.5,
            "bank_core_tier1_capital_ratio": 13,
            "bank_capital_adequacy_ratio": 17,
            "bank_net_interest_margin": 1.8,
            "bank_nim_change_3y": 0.0,
            "bank_cost_income_ratio": 25,
        }
        insurer = {
            "ts_code": "INSURER",
            "is_financial": True,
            "is_bank": False,
            "roe": 10,
            "droe": 1,
            "dividend_yield_cv_5y": 0.20,
            "consecutive_dividend_years": 10,
            "dps_cagr_5y": 0.05,
        }

        scored = {row["ts_code"]: row for row in module.score_cross_section([bank, insurer])}

        self.assertLess(scored["INSURER"]["roe_percentile"], 100)
        self.assertLess(scored["INSURER"]["dividend_score_total"], 100)

    def test_cross_section_scoring_reverses_cv_and_keeps_actual_dividend_years(self):
        module = load_module()
        rows = [
            {
                "ts_code": "A",
                "is_financial": False,
                "roe": 10,
                "droe": 1,
                "opcfd": 0.2,
                "fcf_dividend_coverage_3y": 1.2,
                "dividend_yield_cv_5y": 0.10,
                "consecutive_dividend_years": 20,
                "dps_cagr_5y": 0.05,
            },
            {
                "ts_code": "B",
                "is_financial": False,
                "roe": 10,
                "droe": 1,
                "opcfd": 0.2,
                "fcf_dividend_coverage_3y": 1.2,
                "dividend_yield_cv_5y": 0.50,
                "consecutive_dividend_years": 10,
                "dps_cagr_5y": 0.05,
            },
        ]

        scored = module.score_cross_section(rows)
        by_code = {row["ts_code"]: row for row in scored}

        self.assertGreater(by_code["A"]["dividend_yield_cv_percentile"], by_code["B"]["dividend_yield_cv_percentile"])
        self.assertGreater(by_code["A"]["consecutive_dividend_years_percentile"], by_code["B"]["consecutive_dividend_years_percentile"])

    def test_new_contract_uses_top10_and_excludes_legacy_score_fields(self):
        module = load_module()
        text = SKILL.read_text(encoding="utf-8")

        self.assertEqual(module.TOP_CANDIDATE_COUNT, 10)
        for field in [
            "price_score",
            "current_yield_score",
            "dividend_safety_score",
            "dividend_financing_score",
            "cash_dividend_execution_score",
            "company_quality_score",
            "company_quality",
            "company_quality_gate_passed",
            "latest_period",
            "analysis_latest_period",
            "financial_report_period_match",
        ]:
            self.assertNotIn(field, module.CSV_FIELDS)
        self.assertIn("Top10", text)
        self.assertNotIn("分红融资比：7 分", text)

    def test_statement_deduplication_uses_consolidated_latest_revision_available_by_run_date(self):
        module = load_module()
        frame = pd.DataFrame(
            [
                {"end_date": "20251231", "report_type": "1", "f_ann_date": "20260301", "update_flag": "0", "value": 10},
                {"end_date": "20251231", "report_type": "1", "f_ann_date": "20260320", "update_flag": "1", "value": 12},
                {"end_date": "20251231", "report_type": "2", "f_ann_date": "20260325", "update_flag": "1", "value": 99},
                {"end_date": "20251231", "report_type": "1", "f_ann_date": "20260821", "update_flag": "1", "value": 15},
            ]
        )

        result = module.dedupe_statement_rows(frame, "20260820")

        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["value"], 12)

    def test_structured_gate_profile_is_complete_before_report_quality(self):
        module = load_module()
        passed = module.structured_gate_profile(
            {
                "market_preflight_passed": True,
                "is_financial": False,
                "three_year_continuous_dividend_passed": True,
                "payout_ratio_gate_passed": True,
                "roe_stability_gate_passed": True,
                "profit_trend_gate_passed": True,
                "latest_dps_to_prior3_median": 0.8,
                "cashflow_dividend_gate_passed": True,
                "audit_gate_passed": True,
                "structured_data_quality": "normal",
                "industry": "电力",
            }
        )
        failed = module.structured_gate_profile(
            {
                "market_preflight_passed": True,
                "is_financial": False,
                "three_year_continuous_dividend_passed": True,
                "payout_ratio_gate_passed": True,
                "roe_stability_gate_passed": True,
                "profit_trend_gate_passed": True,
                "latest_dps_to_prior3_median": 0.6,
                "cashflow_dividend_gate_passed": True,
                "audit_gate_passed": True,
                "structured_data_quality": "normal",
                "industry": "电力",
            }
        )

        self.assertTrue(passed["structured_gate_passed"])
        self.assertNotIn("company_quality", passed)
        self.assertFalse(failed["structured_gate_passed"])
        self.assertIn("削减", failed["structured_gate_reason"])

    def test_top10_contains_only_selected_rows(self):
        module = load_module()
        rows = [
            {"ts_code": "A", "selected": "是", "dividend_score_total": 80},
            {"ts_code": "B", "selected": "否", "dividend_score_total": 99},
            {"ts_code": "C", "selected": "是", "dividend_score_total": 90},
        ]

        top = module.formal_top_candidates(rows)

        self.assertEqual([row["ts_code"] for row in top], ["C", "A"])

    def test_structured_factor_record_calculates_all_nonfinancial_gates_and_scores(self):
        module = load_module()
        periods = []
        for year in range(2022, 2026):
            periods.extend(
                [
                    (f"{year}0331", 25_000_000),
                    (f"{year}0630", 50_000_000),
                    (f"{year}0930", 75_000_000),
                    (f"{year}1231", 100_000_000),
                ]
            )
        periods.append(("20260331", 25_000_000))
        income = pd.DataFrame(
            [{"end_date": period, "n_income_attr_p": value} for period, value in periods]
        )
        balance = pd.DataFrame(
            [
                {
                    "end_date": period,
                    "total_hldr_eqy_exc_min_int": 1_000_000_000,
                    "total_liab": 500_000_000,
                }
                for period, _ in periods
            ]
        )
        cashflow = pd.DataFrame(
            [
                {
                    "end_date": period,
                    "n_cashflow_act": value * 1.5,
                    "c_pay_acq_const_fiolta": 20_000_000 if period.endswith("1231") else 5_000_000,
                }
                for period, value in periods
            ]
        )
        fina = pd.DataFrame(
            [
                {"end_date": f"{year}1231", "profit_dedt": 100_000_000}
                for year in range(2022, 2026)
            ]
        )
        dividends = pd.DataFrame(
            [
                {
                    "end_date": f"{year}1231",
                    "div_proc": "实施",
                    "imp_ann_date": f"{year + 1}0601",
                    "cash_div_tax": 0.30,
                    "base_share": 10000,
                }
                for year in range(2021, 2026)
            ]
        )
        audit = pd.DataFrame(
            [{"end_date": "20251231", "ann_date": "20260320", "audit_result": "标准无保留意见"}]
        )

        record = module.structured_factor_record_from_frames(
            ts_code="600001.SH",
            industry="电力",
            run_date="20260820",
            dividends=dividends,
            income=income,
            fina_indicator=fina,
            cashflow=cashflow,
            balance=balance,
            audit=audit,
            market_preflight_passed=True,
            roe_stability_cutoff=1.0,
        )

        self.assertFalse(record["is_financial"])
        self.assertEqual(record["dividend_anchor_year"], 2025)
        self.assertAlmostEqual(record["three_year_average_payout_ratio"], 0.30)
        self.assertTrue(record["payout_ratio_gate_passed"])
        self.assertEqual(record["roe_stability_observations"], 12)
        self.assertTrue(record["roe_stability_gate_passed"])
        self.assertTrue(record["profit_trend_gate_passed"])
        self.assertTrue(record["cashflow_dividend_gate_passed"])
        self.assertTrue(record["audit_gate_passed"])
        self.assertGreater(record["opcfd"], 0)
        self.assertGreater(record["fcf_dividend_coverage_3y"], 1)
        self.assertTrue(record["structured_gate_passed"])
        self.assertAlmostEqual(json.loads(record["annual_payout_ratios_json"])["2025"], 0.30)
        self.assertEqual(len(json.loads(record["ttm_roe_12q_json"])), 12)
        self.assertEqual(record["structured_as_of_date"], "20260820")

    def test_structured_source_fetch_is_light_for_market_failures(self):
        module = load_module()

        class FakePro:
            def __init__(self):
                self.calls = []

            def _record(self, name):
                self.calls.append(name)
                return pd.DataFrame()

            def income(self, **kwargs):
                return self._record("income")

            def balancesheet(self, **kwargs):
                return self._record("balancesheet")

            def dividend(self, **kwargs):
                return self._record("dividend")

            def fina_indicator(self, **kwargs):
                return self._record("fina_indicator")

            def cashflow(self, **kwargs):
                return self._record("cashflow")

            def fina_audit(self, **kwargs):
                return self._record("fina_audit")

        light = FakePro()
        module.fetch_structured_source_frames(
            light,
            "600001.SH",
            "20260820",
            include_candidate_details=False,
        )
        full = FakePro()
        module.fetch_structured_source_frames(
            full,
            "600001.SH",
            "20260820",
            include_candidate_details=True,
        )

        self.assertEqual(light.calls, ["income", "balancesheet"])
        self.assertEqual(
            full.calls,
            ["income", "balancesheet", "dividend", "fina_indicator", "cashflow", "fina_audit"],
        )

    def test_structured_factor_cache_must_cover_the_full_constituent_snapshot(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            market = Path(tmp)
            pd.DataFrame(
                [
                    {"ts_code": "600001.SH", "name": "甲", "industry": "电力"},
                    {"ts_code": "600002.SH", "name": "乙", "industry": "银行"},
                ]
            ).to_csv(market / "constituents.csv", index=False)
            pd.DataFrame(
                [
                    {"ts_code": "600001.SH", "structured_gate_passed": True},
                    {"ts_code": "600002.SH", "structured_gate_passed": False},
                ]
            ).to_csv(market / module.STRUCTURED_FACTORS_FILENAME, index=False)

            cached = module.ensure_structured_factor_data(
                "20260820",
                market,
                skip_fetch=True,
            )
            self.assertEqual(set(cached), {"600001.SH", "600002.SH"})

            pd.DataFrame(
                [{"ts_code": "600001.SH", "structured_gate_passed": True}]
            ).to_csv(market / module.STRUCTURED_FACTORS_FILENAME, index=False)
            with self.assertRaises(RuntimeError):
                module.ensure_structured_factor_data("20260820", market, skip_fetch=True)

    def test_skip_fetch_requires_complete_company_profile_cache(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp, patch.object(module, "output_dir", return_value=Path(tmp)):
            market = Path(tmp) / "market_data"
            market.mkdir()
            pd.DataFrame(
                [{"ts_code": "600001.SH", "name": "甲", "main_business": ""}]
            ).to_csv(market / "constituents.csv", index=False)

            with self.assertRaises(RuntimeError):
                module.ensure_market_data("20260820", skip_fetch=True)

    def test_run_strategy_uses_structured_profiles_without_financial_reports(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            market = root / "market_data"
            market.mkdir()
            pd.DataFrame(
                [
                    {"ts_code": "600001.SH", "name": "甲", "industry": "电力"},
                    {"ts_code": "600002.SH", "name": "乙", "industry": "银行"},
                ]
            ).to_csv(market / "constituents.csv", index=False)
            structured = {
                "600001.SH": {"ts_code": "600001.SH", "industry": "电力", "is_bank": False, "market_preflight_passed": True, "structured_gate_passed": True},
                "600002.SH": {"ts_code": "600002.SH", "industry": "银行", "is_bank": True, "market_preflight_passed": True, "structured_gate_passed": False},
            }
            rows = [
                {"ts_code": "600001.SH", "selected": "是", "dividend_score_total": 90},
                {"ts_code": "600002.SH", "selected": "否", "dividend_score_total": 99},
            ]
            observed = {"csv": [], "stage_counts": None}
            bank_profiles = {
                "600002.SH": {
                    "ts_code": "600002.SH",
                    "is_bank": True,
                    "bank_quality_data_quality": "normal",
                    "bank_quality_gate_passed": True,
                }
            }

            def fake_write_csv(content, path):
                observed["csv"].append((path.name, [row["ts_code"] for row in content]))

            def fake_write_markdown(*args, **kwargs):
                observed["stage_counts"] = kwargs["stage_counts"]

            with (
                patch.object(module, "output_dir", return_value=root),
                patch.object(module, "ensure_market_data", return_value=market),
                patch.object(module, "ensure_structured_factor_data", return_value=structured),
                patch.object(module, "load_bank_quality_profiles", return_value=bank_profiles) as load_banks,
                patch.object(module, "build_candidate_rows", return_value=rows),
                patch.object(module, "write_csv", side_effect=fake_write_csv),
                patch.object(module, "write_markdown", side_effect=fake_write_markdown),
                patch.object(module, "write_html"),
            ):
                module.run_strategy("20260820", skip_fetch=True)

        self.assertIn(("hs300-dividend-top10-20260820.csv", ["600001.SH"]), observed["csv"])
        self.assertFalse(any("top5" in name for name, _ in observed["csv"]))
        self.assertEqual(observed["stage_counts"]["structured_passers"], 1)
        self.assertNotIn("reused_reports", observed["stage_counts"])
        self.assertNotIn("generated_reports", observed["stage_counts"])
        load_banks.assert_called_once_with(
            market / module.BANK_METRICS_FILENAME,
            {"600002.SH"},
        )

    def test_bank_quality_gate_is_merged_before_cross_section_scoring(self):
        module = load_module()
        structured = {
            "ts_code": "600002.SH",
            "industry": "银行",
            "is_financial": True,
            "is_bank": True,
            "structured_gate_passed": True,
            "structured_gate_status": "passed",
            "structured_gate_reason": "通过通用门槛",
        }
        bank = {
            "ts_code": "600002.SH",
            "is_bank": True,
            "bank_quality_data_quality": "normal",
            "bank_quality_gate_passed": False,
            "bank_quality_gate_reason": "拨备覆盖率低于150%",
        }

        merged = module.merge_bank_quality_gate(structured, bank)

        self.assertFalse(merged["structured_gate_passed"])
        self.assertEqual(merged["structured_gate_status"], "hard_gate_failed")
        self.assertIn("拨备覆盖率", merged["structured_gate_reason"])

    def test_missing_bank_cache_is_prepared_automatically(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / module.BANK_METRICS_FILENAME
            observed = {}

            def prepare_cache(*, output_path, bank_codes, as_of_date):
                observed.update(
                    output_path=output_path,
                    bank_codes=bank_codes,
                    as_of_date=as_of_date,
                )
                rows = []
                for period in ["20231231", "20241231", "20251231"]:
                    rows.append(
                        {
                            "ts_code": "600001.SH",
                            "period": period,
                            "npl_ratio": 1.0,
                            "provision_coverage_ratio": 250,
                            "loan_provision_ratio": 2.6,
                            "net_interest_margin": 1.5,
                            "cost_income_ratio": 30,
                            "core_tier1_capital_ratio": 10,
                            "tier1_capital_ratio": 11,
                            "capital_adequacy_ratio": 14,
                            "data_quality": "normal",
                        }
                    )
                pd.DataFrame(rows).to_csv(output_path, index=False)

            ensure = getattr(module, "ensure_bank_quality_profiles", None)
            self.assertTrue(callable(ensure))
            profiles = ensure(
                path,
                {"600001.SH"},
                "20260831",
                preparer=prepare_cache,
            )

        self.assertEqual(set(profiles), {"600001.SH"})
        self.assertEqual(observed["bank_codes"], {"600001.SH"})
        self.assertEqual(observed["as_of_date"], "20260831")

    def test_markdown_and_html_describe_only_the_new_quality_dividend_model(self):
        module = load_module()
        rows = [
            {
                "rank": 1,
                "ts_code": "600001.SH",
                "name": "甲公司",
                "industry": "电力",
                "selected": "是",
                "selected_reason": "通过全部硬门槛",
                "dividend_score_total": 88,
                "main_business": "发电业务",
                "roe": 12,
                "droe": 1,
                "opcfd": 0.2,
                "fcf_dividend_coverage_3y": 2,
                "dividend_yield_cv_5y": 0.2,
                "consecutive_dividend_years": 15,
                "dps_cagr_5y": 0.05,
            }
        ]
        counts = {
            "universe": 300,
            "market_passers": 44,
            "structured_passers": 20,
            "selected": 10,
        }
        with tempfile.TemporaryDirectory() as tmp:
            markdown = Path(tmp) / "report.md"
            html = Path(tmp) / "report.html"
            module.write_markdown(rows, "20260820", markdown, stage_counts=counts)
            module.write_html(rows, "20260820", html, stage_counts=counts)
            combined = markdown.read_text(encoding="utf-8") + html.read_text(encoding="utf-8")

        self.assertIn("Top10", combined)
        self.assertIn("结构化门槛通过", combined)
        self.assertIn("连续分红年数", combined)
        for obsolete in ["分红融资比", "兑现度", "当前价5", "Top5", "公司质量", "财报"]:
            self.assertNotIn(obsolete, combined)


if __name__ == "__main__":
    unittest.main()
