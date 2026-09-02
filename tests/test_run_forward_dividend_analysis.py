import csv
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load_module():
    path = SCRIPTS / "run_forward_dividend_analysis.py"
    spec = importlib.util.spec_from_file_location("run_forward_dividend_analysis", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RunForwardDividendAnalysisTest(unittest.TestCase):
    def test_console_summary_prints_readable_top10_fields_and_data_gap(self):
        module = load_module()
        rows = [
            {
                "rank": 1, "name": "示例公司", "ts_code": "600001.SH",
                "dividend_score_total": 80.0, "quote_price": 25.0,
                "forecast_fiscal_year": 2026,
                "forecast_fy_regular_dps_low": 0.85,
                "forecast_fy_regular_dps_base": 0.9,
                "forecast_fy_regular_dps_high": 0.95,
                "expected_dividend_yield": 0.036,
                "target_yield_low": 0.04,
                "target_yield_high": 0.05,
                "target_display_label": "进入目标区间",
                "forecast_method": "policy_derived",
                "evidence_completeness": "complete",
                "forecast_uncertainty": "medium",
                "forecast_status": "modelled",
            },
            {
                "rank": 2, "name": "缺口公司", "ts_code": "600002.SH",
                "dividend_score_total": 75.0, "quote_price": 18.0,
                "forecast_fy_regular_dps_base": None,
                "expected_dividend_yield": None,
                "target_yield_low": 0.06,
                "target_yield_high": 0.08,
                "target_display_label": "暂不判断",
                "forecast_status": "data_gap",
            },
        ]

        result = module.format_console_top10(rows, run_date="20260902")

        self.assertIn("AHongli A股前瞻Top10 20260902", result)
        self.assertIn("排名 | 公司 | 得分 | 股价 | 预期分红 | 预期股息率 | 当前位置 | 目标股息率区间", result)
        self.assertIn("1 | 示例公司 | 80.00 | 25.00 | 0.9000 | 3.60% | 进入目标区间 | 4.00%–5.00%", result)
        self.assertIn("2 | 缺口公司 | 75.00 | 18.00 | — | — | 暂不判断 | 6.00%–8.00%", result)
        self.assertNotIn("DPS低/基准/高", result)
        self.assertNotIn("P25", result)

    def test_offline_run_preserves_formal_top10_and_writes_independent_outputs(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            market = out / "market_data"
            evidence_dir = market / "forward_dividend_evidence" / "600900.SH"
            evidence_dir.mkdir(parents=True)
            top10 = out / "hs300-dividend-top10-20260902.csv"
            with top10.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=[
                    "rank", "ts_code", "name", "industry", "selected",
                    "dividend_score_total", "current_price", "price_date", "current_dividend_yield",
                ])
                writer.writeheader()
                writer.writerow({
                    "rank": 1,
                    "ts_code": "600900.SH",
                    "name": "长江电力",
                    "industry": "水力发电",
                    "selected": "是",
                    "dividend_score_total": 74.58,
                    "current_price": 28.4,
                    "price_date": "20260901",
                    "current_dividend_yield": 3.52,
                })
            evidence = {
                "ts_code": "600900.SH",
                "normalized_facts": [
                    {"fact_type": "net_profit_parent", "period": "20251231", "value": 33_000_000_000.0},
                    {"fact_type": "net_profit_parent", "period": "20260630", "value": 17_000_000_000.0},
                    {"fact_type": "net_profit_parent_prior_comparable", "period": "20250630", "value": 15_000_000_000.0},
                    {"fact_type": "total_shares", "period": "20251231", "value": 24_470_000_000},
                    {"fact_type": "official_payout_floor", "value": 0.70, "valid_from": 2026, "valid_to": 2030},
                ],
                "dividend_events": [],
            }
            (evidence_dir / "forecast-evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
            before = hashlib.sha256(top10.read_bytes()).hexdigest()
            before_mtime = top10.stat().st_mtime_ns

            result = module.run_forward_analysis(
                run_date="20260902",
                top10_path=top10,
                output_dir=out,
                skip_prepare=True,
            )

            self.assertEqual(hashlib.sha256(top10.read_bytes()).hexdigest(), before)
            self.assertEqual(top10.stat().st_mtime_ns, before_mtime)
            self.assertTrue(result["outputs"]["csv"].exists())
            status = json.loads((market / "forward-dividend-run-status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["input_top10_sha256"], before)
            self.assertEqual(status["counts"]["modelled"], 1)

    def test_online_run_prepares_parses_and_forecasts_original_evidence(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            top10 = out / "hs300-dividend-top10-20260902.csv"
            with top10.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=[
                    "rank", "ts_code", "name", "industry", "selected",
                    "dividend_score_total", "current_price", "price_date", "current_dividend_yield",
                ])
                writer.writeheader()
                writer.writerow({
                    "rank": 1, "ts_code": "600900.SH", "name": "长江电力",
                    "industry": "水力发电", "selected": "是",
                    "dividend_score_total": 74.58, "current_price": 28.4,
                    "price_date": "20260901", "current_dividend_yield": 3.52,
                })

            class Client:
                def resolve_stock(self, code):
                    return {"stock_code": "600900", "market": "szse", "zwjc": "长江电力"}

                def query_announcements(self, code, **kwargs):
                    return [
                        {"announcementId": "R1", "announcementTitle": "长江电力2025年年度报告", "announcementTime": "2026-03-20 10:00:00", "adjunctUrl": "R1.PDF", "adjunctType": "PDF"},
                        {"announcementId": "H1", "announcementTitle": "长江电力2026年半年度报告", "announcementTime": "2026-08-20 10:00:00", "adjunctUrl": "H1.PDF", "adjunctType": "PDF"},
                        {"announcementId": "P1", "announcementTitle": "2026年至2030年股东分红回报规划", "announcementTime": "2026-04-01 10:00:00", "adjunctUrl": "P1.PDF", "adjunctType": "PDF"},
                    ]

            texts = {
                "R1": "归属于上市公司股东的净利润（元） 33,000,000,000.00\n期末总股本（股） 24,470,000,000",
                "H1": "归属于上市公司股东的净利润（元） 17,000,000,000.00 15,000,000,000.00",
                "P1": "2026年至2030年，公司每年现金分红比例不低于当年归母净利润的70%。",
            }

            result = module.run_forward_analysis(
                run_date="20260902",
                top10_path=top10,
                output_dir=out,
                client=Client(),
                download_bytes=lambda url: b"pdf bytes",
                page_provider=lambda document, _: [{"page": 1, "text": texts[document["announcement_id"]]}],
            )

            self.assertEqual(result["status"]["counts"]["modelled"], 1)
            evidence_root = out / "market_data" / "forward_dividend_evidence" / "600900.SH"
            self.assertTrue((evidence_root / "manifest.json").exists())
            self.assertTrue((evidence_root / "forecast-evidence.json").exists())

    def test_one_company_parse_failure_becomes_row_failure_and_other_rows_continue(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            top10 = out / "hs300-dividend-top10-20260902.csv"
            with top10.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=[
                    "rank", "ts_code", "name", "industry", "selected", "dividend_score_total",
                ])
                writer.writeheader()
                writer.writerows([
                    {"rank": 1, "ts_code": "600001.SH", "name": "正常公司", "industry": "水力发电", "selected": "是", "dividend_score_total": 80},
                    {"rank": 2, "ts_code": "600002.SH", "name": "解析失败公司", "industry": "水力发电", "selected": "是", "dividend_score_total": 70},
                ])

            class Client:
                def resolve_stock(self, code):
                    return {"stock_code": code.split(".")[0], "market": "szse", "zwjc": code}

                def query_announcements(self, code, **kwargs):
                    return [
                        {"announcementId": "R1", "announcementTitle": "公司2025年年度报告", "announcementTime": "2026-03-20 10:00:00", "adjunctUrl": "R1.PDF", "adjunctType": "PDF"},
                        {"announcementId": "H1", "announcementTitle": "公司2026年半年度报告", "announcementTime": "2026-08-20 10:00:00", "adjunctUrl": "H1.PDF", "adjunctType": "PDF"},
                        {"announcementId": "P1", "announcementTitle": "2026年至2030年股东分红回报规划", "announcementTime": "2026-04-01 10:00:00", "adjunctUrl": "P1.PDF", "adjunctType": "PDF"},
                    ]

            texts = {
                "R1": "归属于上市公司股东的净利润（元） 100.00\n期末总股本（股） 100",
                "H1": "归属于上市公司股东的净利润（元） 60.00 50.00",
                "P1": "2026年至2030年，公司每年现金分红比例不低于当年归母净利润的30%。",
            }

            def pages(document, company_root):
                if company_root.name == "600002.SH":
                    raise RuntimeError("one PDF cannot be parsed")
                return [{"page": 1, "text": texts[document["announcement_id"]]}]

            result = module.run_forward_analysis(
                run_date="20260902", top10_path=top10, output_dir=out,
                client=Client(), download_bytes=lambda url: b"pdf", page_provider=pages,
            )

            self.assertEqual(result["status"]["status"], "partial")
            self.assertEqual(result["status"]["counts"]["modelled"], 1)
            self.assertEqual(result["status"]["counts"]["failed"], 1)

    def test_all_company_download_failures_write_failed_run_status_without_touching_formal_top10(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            top10 = out / "hs300-dividend-top10-20260902.csv"
            top10.write_text(
                "rank,ts_code,name,industry,selected,dividend_score_total\n"
                "1,600900.SH,长江电力,水力发电,是,74.58\n",
                encoding="utf-8",
            )
            before = top10.read_bytes()

            class FailingClient:
                def resolve_stock(self, code):
                    return {"stock_code": "600900", "market": "szse", "zwjc": "长江电力"}

                def query_announcements(self, code, **kwargs):
                    raise RuntimeError("CNinfo unavailable")

            result = module.run_forward_analysis(
                run_date="20260902",
                top10_path=top10,
                output_dir=out,
                client=FailingClient(),
                download_bytes=lambda url: b"",
            )

            self.assertEqual(top10.read_bytes(), before)
            status_path = out / "market_data" / "forward-dividend-run-status.json"
            status = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(status["status"], "failed")
            self.assertEqual(status["counts"]["failed"], 1)
            self.assertEqual(result["rows"][0]["forecast_reason"], "CNinfo unavailable")

    def test_forecast_failure_also_writes_failed_run_status(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            market = out / "market_data"
            evidence_dir = market / "forward_dividend_evidence" / "600001.SH"
            evidence_dir.mkdir(parents=True)
            top10 = out / "hs300-dividend-top10-20260902.csv"
            top10.write_text(
                "rank,ts_code,name,industry,selected,dividend_score_total,current_price\n"
                "1,600001.SH,测试公司,公用事业,是,80,25\n",
                encoding="utf-8",
            )
            evidence = {
                "normalized_facts": [],
                "dividend_events": [{
                    "fiscal_period": "20261231", "regular_or_special": "regular",
                    "status": "dividend_plan", "distribution_phase": "full_year",
                    "cash_dividend_per_share_pre_tax": 1.0,
                }],
            }
            (evidence_dir / "forecast-evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
            with self.assertRaises(ValueError):
                module.run_forward_analysis(
                    run_date="20260902", top10_path=top10, output_dir=out,
                    skip_prepare=True, risk_free_rate=-0.01,
                )

            status = json.loads((market / "forward-dividend-run-status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["status"], "failed")
            self.assertIn("risk_free_rate", status["error"])


if __name__ == "__main__":
    unittest.main()
