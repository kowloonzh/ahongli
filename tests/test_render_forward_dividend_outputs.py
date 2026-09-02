import csv
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load_module():
    path = SCRIPTS / "render_forward_dividend_outputs.py"
    spec = importlib.util.spec_from_file_location("render_forward_dividend_outputs", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RenderForwardDividendOutputsTest(unittest.TestCase):
    def test_renderer_writes_numeric_blanks_and_neutral_user_outputs(self):
        module = load_module()
        rows = [
            {
                "rank": 1,
                "ts_code": "600001.SH",
                "name": "示例公司",
                "dividend_score_total": 80.0,
                "quote_price": 25.0,
                "source_dividend_yield": 3.2,
                "forecast_fiscal_year": 2026,
                "forecast_fy_regular_dps_low": 0.85,
                "forecast_fy_regular_dps_base": 0.90,
                "forecast_fy_regular_dps_high": 0.95,
                "expected_dividend_yield": 0.036,
                "target_yield_low": 0.04,
                "target_yield_high": 0.05,
                "target_price_low": 18.0,
                "target_price_high": 22.5,
                "target_status": "below_target_yield",
                "target_display_label": "未达目标区间",
                "target_yield_model_id": "required_return_minus_growth_v1",
                "required_return_low": 0.08,
                "required_return_high": 0.09,
                "sustainable_dividend_growth": 0.04,
                "forecast_method": "policy_derived",
                "evidence_completeness": "complete",
                "forecast_uncertainty": "medium",
                "forecast_status": "modelled",
                "forecast_reason": "正式政策与TTM利润",
                "forecast_profit": 100.0,
                "forecast_payout_ratio": 0.30,
                "forecast_total_shares": 100.0,
                "forecast_input_fact_ids": ["F1"],
                "forecast_input_event_ids": ["E1"],
            },
            {
                "rank": 2,
                "ts_code": "600002.SH",
                "name": "缺口公司",
                "dividend_score_total": 75.0,
                "forecast_fy_regular_dps_base": None,
                "forecast_status": "data_gap",
                "forecast_reason": "缺少正式派息政策",
                "evidence_completeness": "missing",
                "forecast_uncertainty": "not_estimable",
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            paths = module.render_forward_dividend_outputs(
                rows=rows,
                run_date="20260902",
                output_dir=Path(tmp),
                evidence_by_code={
                    "600001.SH": {
                        "source_documents": [{"announcement_id": "A1", "source_title": "年度报告", "source_url": "https://example.test/A1.pdf"}],
                        "normalized_facts": [{"fact_id": "F1", "fact_type": "net_profit_parent", "value": 100}],
                        "dividend_events": [{"event_id": "E1", "cash_dividend_per_share_pre_tax": 0.9}],
                    }
                },
            )

            with paths["csv"].open(encoding="utf-8-sig", newline="") as handle:
                csv_rows = list(csv.DictReader(handle))
            markdown = paths["markdown"].read_text(encoding="utf-8")
            html = paths["html"].read_text(encoding="utf-8")

            self.assertEqual([row["rank"] for row in csv_rows], ["1", "2"])
            self.assertEqual(csv_rows[1]["forecast_fy_regular_dps_base"], "")
            self.assertEqual(csv_rows[0]["forecast_input_fact_ids"], '["F1"]')
            self.assertIn("| 排名 | 公司 | 得分 | 股价 | 预期分红 | 预期股息率 | 当前位置 | 目标股息率区间 |", markdown)
            self.assertIn("未达目标区间", markdown)
            self.assertIn("目标股息率区间", html)
            self.assertNotIn("P25", markdown + html)
            self.assertNotIn("历史常规股息率带", markdown + html)
            self.assertNotIn("historical_regular_yield_p25", csv_rows[0])
            self.assertEqual(csv_rows[0]["target_yield_low"], "0.04")
            self.assertNotIn("持有收息", markdown + html)
            self.assertTrue(paths["sources"].exists())
            self.assertTrue(paths["facts"].exists())
            self.assertTrue(paths["events"].exists())
            self.assertTrue(paths["results"].exists())
            detail = Path(tmp) / csv_rows[0]["evidence_detail_path"]
            detail_text = detail.read_text(encoding="utf-8")
            self.assertIn("100.00 × 30.00% ÷ 100.00", detail_text)
            self.assertIn("要求总回报率", detail_text)
            self.assertIn("目标价格区间", detail_text)
            self.assertIn("年度报告", detail_text)
            self.assertIn("net_profit_parent", detail_text)
            self.assertIn("F1", detail_text)
            gap_detail = Path(tmp) / csv_rows[1]["evidence_detail_path"]
            self.assertIn("缺少正式派息政策", gap_detail.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
