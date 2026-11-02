import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import prepare_backtest_bank_metrics as preparer  # noqa: E402

METRICS = list(preparer.parser_module.METRIC_ALIASES)


def parsed(title, date, **values):
    row = {metric: values.get(metric) for metric in METRICS}
    row.update(
        ts_code="600000.SH",
        period="20221231",
        title=title,
        announcement_date=date,
        source_file=f"{title}.md",
        missing_metrics=[metric for metric in METRICS if values.get(metric) is None],
        metric_evidence_json=json.dumps({metric: f"{title}:{metric}" for metric in values}, ensure_ascii=False),
    )
    row["data_quality"] = "normal" if not row["missing_metrics"] else "data_gap"
    return row


class DeriveLoanProvisionRatioTest(unittest.TestCase):
    def test_derives_from_npl_and_coverage_only_when_missing(self):
        complete = {metric: 1.0 for metric in METRICS}
        row = parsed("年报", "20230330", **{**complete, "loan_provision_ratio": None, "npl_ratio": 1.2, "provision_coverage_ratio": 250.0})

        derived = preparer.derive_loan_provision_ratio(row)

        self.assertAlmostEqual(derived["loan_provision_ratio"], 3.0)
        self.assertEqual(derived["data_quality"], "normal")
        self.assertIn("derived", json.loads(derived["metric_evidence_json"])["loan_provision_ratio"])
        self.assertIsNone(row["loan_provision_ratio"])

    def test_keeps_reported_value(self):
        row = parsed("年报", "20230330", loan_provision_ratio=2.5, npl_ratio=1.2, provision_coverage_ratio=250.0)

        self.assertEqual(preparer.derive_loan_provision_ratio(row)["loan_provision_ratio"], 2.5)


class MergeMetricRowsTest(unittest.TestCase):
    def test_fills_gaps_in_priority_order_and_records_source(self):
        complete = {metric: 1.0 for metric in METRICS}
        a_share = parsed("A股年报", "20230330", **{**complete, "net_interest_margin": None, "npl_ratio": 1.1})
        h_share = parsed("H股年报", "20230331", npl_ratio=9.9)
        summary = parsed("年报摘要", "20230330", net_interest_margin=1.8)

        merged = preparer.merge_metric_rows([a_share, h_share, summary])

        self.assertEqual(merged["npl_ratio"], 1.1)
        self.assertEqual(merged["net_interest_margin"], 1.8)
        self.assertEqual(merged["data_quality"], "normal")
        self.assertEqual(json.loads(merged["metric_evidence_json"])["net_interest_margin"]["source_title"], "年报摘要")
        # The H-share copy filled nothing, so its later date does not delay the row.
        self.assertEqual(merged["announcement_date"], "20230330")
        self.assertEqual(merged["title"], "A股年报 + 年报摘要")
        self.assertIn("net_interest_margin", a_share["missing_metrics"])

    def test_row_dates_to_latest_document_used(self):
        a_share = parsed("A股年报", "20230330", npl_ratio=1.1)
        h_share = parsed("H股年报", "20230415", net_interest_margin=1.8)

        self.assertEqual(preparer.merge_metric_rows([a_share, h_share])["announcement_date"], "20230415")


class AnnualSummaryTitleTest(unittest.TestCase):
    def test_matches_chinese_summary_only(self):
        self.assertTrue(preparer.is_annual_summary("江苏银行2023年年度报告摘要", 2023))
        self.assertFalse(preparer.is_annual_summary("江苏银行2023年年度报告摘要（英文版）", 2023))
        self.assertFalse(preparer.is_annual_summary("江苏银行2022年年度报告摘要", 2023))


if __name__ == "__main__":
    unittest.main()
