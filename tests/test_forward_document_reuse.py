import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from prepare_forward_dividend_evidence import prepare_forward_evidence
from parse_forward_dividend_evidence import extract_document_pages
from forward_document_cache import ForwardDocumentCache


class ForwardDocumentReuseTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.row = {"ts_code": "000333.SZ", "name": "测试公司", "selected": "是"}
        self.announcement = {"announcementId": "A1", "announcementTitle": "测试公司2025年年度报告",
                             "announcementTime": "2026-03-20 10:00:00", "adjunctUrl": "finalpage/A1.PDF", "adjunctType": "PDF"}
        self.client = Mock()
        self.client.resolve_stock.return_value = {"stock_code": "000333"}
        self.client.query_announcements.return_value = [self.announcement]
        self.content = b"%PDF-1.4\noriginal document\n%%EOF"
        self.download = Mock(return_value=self.content)

    def evidence_root(self, date):
        return self.root / "a_dividend_outputs" / date / "market_data" / "forward_dividend_evidence"

    def run_prepare(self, date="20260910", **kwargs):
        return prepare_forward_evidence(top_rows=[self.row], run_date=date,
                                        evidence_root=self.evidence_root(date), client=self.client,
                                        download_bytes=self.download, **kwargs)[0]

    def seed_legacy(self):
        company = self.evidence_root("20260909") / "000333.SZ"
        path = company / "reports/A1-annual.pdf"
        path.parent.mkdir(parents=True)
        path.write_bytes(self.content)
        doc = {"announcement_id": "A1", "logical_role": "annual",
               "source_url": "https://static.cninfo.com.cn/finalpage/A1.PDF",
               "source_file": "reports/A1-annual.pdf", "sha256": hashlib.sha256(self.content).hexdigest(),
               "retrieved_at": "2026-09-09T00:00:00+00:00"}
        (company / "manifest.json").write_text(json.dumps({"ts_code": "000333.SZ", "documents": [doc]}))
        return path

    def test_same_day_repeat_does_not_download(self):
        first = self.run_prepare()
        second = self.run_prepare()
        self.assertEqual(second["status"], "normal")
        self.assertEqual(self.download.call_count, 1)
        self.assertEqual(second["documents"][0]["cache_status"], "reused")
        self.assertEqual(second["documents"][0]["retrieved_at"], first["documents"][0]["retrieved_at"])
        self.assertEqual(self.client.query_announcements.call_count, 14)

    def test_legacy_date_reused_and_original_untouched(self):
        old = self.seed_legacy()
        before = (old.read_bytes(), old.stat().st_mtime_ns)
        result = self.run_prepare()
        self.assertEqual(result["status"], "normal")
        self.download.assert_not_called()
        self.assertEqual((old.read_bytes(), old.stat().st_mtime_ns), before)
        self.assertEqual(result["documents"][0]["cache_origin"], "historical_run")
        self.assertEqual(result["documents"][0]["retrieved_at"], "2026-09-09T00:00:00+00:00")
        self.assertTrue((self.root / "_forward_report_cache").is_dir())

    def test_corrupt_legacy_is_downloaded_not_trusted(self):
        old = self.seed_legacy()
        old.write_bytes(b"truncated")
        result = self.run_prepare()
        self.download.assert_called_once()
        self.assertEqual(result["status"], "normal")
        self.assertEqual(result["documents"][0]["cache_status"], "downloaded")
        self.assertTrue(result["documents"][0]["cache_validation_notes"])
        self.assertEqual(old.read_bytes(), b"truncated")

    def test_new_announcement_is_downloaded_but_old_one_reused(self):
        self.seed_legacy()
        newer = {**self.announcement, "announcementId": "A2", "announcementTitle": "2026年半年度报告",
                 "adjunctUrl": "finalpage/A2.PDF", "announcementTime": "2026-08-30 10:00:00"}
        self.client.query_announcements.return_value = [self.announcement, newer]
        result = self.run_prepare()
        self.assertEqual(len(result["documents"]), 2)
        self.download.assert_called_once_with("https://static.cninfo.com.cn/finalpage/A2.PDF")

    def test_changed_url_does_not_reuse_by_id_alone(self):
        self.seed_legacy()
        self.client.query_announcements.return_value = [{**self.announcement, "adjunctUrl": "revised/A1.PDF"}]
        self.assertEqual(self.run_prepare()["status"], "normal")
        self.download.assert_called_once_with("https://static.cninfo.com.cn/revised/A1.PDF")

    def test_future_announcement_is_not_admitted_from_cache(self):
        self.seed_legacy()
        self.client.query_announcements.return_value = [{**self.announcement, "announcementTime": "2026-09-11 00:00:00"}]
        self.assertEqual(self.run_prepare()["documents"], [])
        self.download.assert_not_called()

    def test_failed_download_retains_earlier_success_for_retry(self):
        newer = {**self.announcement, "announcementId": "A2", "announcementTitle": "2026年半年度报告",
                 "adjunctUrl": "finalpage/A2.PDF", "announcementTime": "2026-08-30 10:00:00"}
        self.client.query_announcements.return_value = [self.announcement, newer]
        self.download.side_effect = [self.content, OSError("temporary failure")]
        self.assertEqual(self.run_prepare()["status"], "failed")
        self.download.reset_mock(side_effect=True)
        self.download.return_value = self.content
        self.assertEqual(self.run_prepare()["status"], "normal")
        self.download.assert_called_once_with("https://static.cninfo.com.cn/finalpage/A2.PDF")

    def test_shared_cache_recovers_corrupt_run_copy(self):
        result = self.run_prepare()
        current = self.evidence_root("20260910") / "000333.SZ" / result["documents"][0]["source_file"]
        current.write_bytes(b"damaged run copy")
        self.download.reset_mock()
        self.assertEqual(self.run_prepare()["status"], "normal")
        self.download.assert_not_called()
        self.assertEqual(current.read_bytes(), self.content)

    def test_explicit_refresh_bypasses_cache(self):
        self.seed_legacy()
        self.assertEqual(self.run_prepare(refresh_documents=True)["status"], "normal")
        self.download.assert_called_once()

    def test_corrupt_shared_and_local_copies_require_download(self):
        result = self.run_prepare()
        doc = result["documents"][0]
        (self.evidence_root("20260910") / "000333.SZ" / doc["source_file"]).write_bytes(b"bad")
        Path(doc["cache_path"]).write_bytes(b"bad")
        self.download.reset_mock()
        self.assertEqual(self.run_prepare()["status"], "normal")
        self.download.assert_called_once()

    def test_html_error_response_is_not_saved_as_pdf(self):
        self.download.return_value = b"<html>gateway failure</html>"
        self.assertEqual(self.run_prepare()["status"], "failed")
        self.assertEqual(list(self.root.rglob('*.pdf')), [])

    def test_bad_shared_metadata_falls_back_to_valid_run(self):
        self.run_prepare()
        for record in (self.root / "_forward_report_cache/records").glob('*.json'):
            record.write_text('{"identity":null}')
        self.download.reset_mock()
        self.assertEqual(self.run_prepare()["status"], "normal")
        self.download.assert_not_called()

    def test_custom_evidence_roots_do_not_share_a_parent_cache(self):
        for name in ["first", "second"]:
            root = self.root / name
            cache = ForwardDocumentCache(root)
            self.assertTrue(cache.root.is_relative_to(root.resolve()))

    def test_extraction_cache_tracks_pdf_and_pages_hashes(self):
        company = self.root / "extract-test"
        company.mkdir()
        pdf = company / "report.pdf"
        pdf.write_bytes(self.content)
        doc = {"announcement_id": "A1", "logical_role": "annual", "source_file": "report.pdf"}
        def extract(args, **kwargs):
            Path(args[-1]).write_text(pdf.read_bytes().decode())
        runner = Mock(side_effect=extract)
        first = extract_document_pages(doc, company, command_runner=runner)
        self.assertEqual(extract_document_pages(doc, company, command_runner=runner), first)
        self.assertEqual(runner.call_count, 1)
        pdf.write_bytes(b"%PDF-1.4 updated")
        updated = extract_document_pages(doc, company, command_runner=runner)
        self.assertNotEqual(updated, first)
        self.assertEqual(runner.call_count, 2)
        (company / "extracted/A1-annual.pages.json").write_text('[{"page":1,"text":"corrupt"}]')
        self.assertEqual(extract_document_pages(doc, company, command_runner=runner), updated)
        self.assertEqual(runner.call_count, 3)


if __name__ == "__main__":
    unittest.main()
