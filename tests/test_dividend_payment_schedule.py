import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from forecast_selected_dividends import next_dividend_schedule
from forward_dividend_models import forward_twelve_month_eligible_dps
from render_forward_dividend_outputs import format_next_dividend_payment, render_forward_dividend_outputs
from run_forward_dividend_analysis import format_console_top10


class DividendPaymentScheduleTest(unittest.TestCase):
    def event(self, **changes):
        # Reduced from Ping An's 2026 interim A-share implementation notice.
        return {"event_id": "PINGAN-2026-H1", "fiscal_period": "20261231",
                "distribution_phase": "interim", "regular_or_special": "regular",
                "share_class": "A", "currency": "CNY", "status": "implementation",
                "cash_dividend_per_share_pre_tax": 0.98, "available_date": "20260903",
                "record_date": "20260909", "ex_dividend_date": "20260910",
                "payment_date": "20260910", "evidence_source_ids": ["1225543317"], **changes}

    def test_ping_an_day_before_day_of_and_day_after_payment(self):
        events = [self.event()]
        before = next_dividend_schedule(events, as_of_date="20260909")
        today = next_dividend_schedule(events, as_of_date="20260910")
        after = next_dividend_schedule(events, as_of_date="20260911")
        self.assertEqual(before["next_dividend_date_status"], "scheduled")
        self.assertEqual(today["next_dividend_date_status"], "payment_today")
        self.assertEqual(today["next_dividend_payment_date"], "20260910")
        self.assertEqual(today["next_dividend_evidence_source_ids"], ["1225543317"])
        self.assertEqual(after["next_dividend_date_status"], "no_upcoming_payment")
        self.assertIsNone(after["next_dividend_payment_date"])

    def test_already_ex_dividend_but_not_yet_paid_stays_on_calendar(self):
        event = self.event(ex_dividend_date="20260908", record_date="20260907", payment_date="20260915")
        result = next_dividend_schedule([event], as_of_date="20260910")
        self.assertEqual(result["next_dividend_payment_date"], "20260915")
        self.assertEqual(result["next_dividend_date_status"], "scheduled")
        self.assertIsNone(forward_twelve_month_eligible_dps(events=[event], quote_date="20260910"))

    def test_payment_order_is_not_ex_dividend_order(self):
        earlier_ex = self.event(event_id="LATER-PAYMENT", ex_dividend_date="20260911", payment_date="20260920")
        earlier_payment = self.event(event_id="EARLIER-PAYMENT", ex_dividend_date="20260912", payment_date="20260913")
        result = next_dividend_schedule([earlier_ex, earlier_payment], as_of_date="20260910")
        self.assertEqual(result["next_dividend_event_id"], "EARLIER-PAYMENT")

    def test_today_calendar_does_not_change_eligible_dps(self):
        events = [self.event()]
        self.assertEqual(next_dividend_schedule(events, as_of_date="20260910")["next_dividend_payment_date"], "20260910")
        self.assertEqual(forward_twelve_month_eligible_dps(events=events, quote_date="20260909"), 0.98)
        self.assertIsNone(forward_twelve_month_eligible_dps(events=events, quote_date="20260910"))

    def test_foreign_special_and_future_announcements_are_excluded(self):
        for changes in [{"share_class": "H"}, {"currency": "HKD"}, {"regular_or_special": "special"},
                        {"available_date": "20260911"}, {"payment_date": "20261301"}]:
            with self.subTest(changes=changes):
                result = next_dividend_schedule([self.event(**changes)], as_of_date="20260910")
                self.assertIsNone(result["next_dividend_payment_date"])

    def test_plan_does_not_claim_a_confirmed_payment_date(self):
        result = next_dividend_schedule([self.event(status="dividend_plan")], as_of_date="20260910")
        self.assertEqual(result["next_dividend_date_status"], "pending_implementation")
        self.assertIsNone(result["next_dividend_payment_date"])

    def test_today_label_is_consistent_in_console_html_markdown_and_detail(self):
        row = {"rank": 1, "ts_code": "601318.SH", "name": "中国平安", "quote_price": 56.26,
               **next_dividend_schedule([self.event()], as_of_date="20260910")}
        label = "2026-09-10"
        self.assertEqual(format_next_dividend_payment(row), label)
        self.assertEqual(format_next_dividend_payment({**row, "next_dividend_date_status": "scheduled"}), label)
        self.assertIn(label, format_console_top10([row], run_date="20260910"))
        self.assertNotIn("当日派息", format_console_top10([row], run_date="20260910"))
        with tempfile.TemporaryDirectory() as tmp:
            output = render_forward_dividend_outputs(rows=[row], run_date="20260910", output_dir=Path(tmp))
            for key in ["markdown", "html"]:
                self.assertIn(label, output[key].read_text())
                self.assertNotIn("当日派息", output[key].read_text())
            detail = (Path(tmp) / row["evidence_detail_path"]).read_text()
            self.assertIn(label, detail)
            self.assertNotIn("当日派息", detail)
            self.assertIn("不代表", detail)
            # CSV retains the normalized date, not the rendered label.
            self.assertIn("20260910", output["csv"].read_text())
            self.assertNotIn(label, output["csv"].read_text())


if __name__ == "__main__":
    unittest.main()
