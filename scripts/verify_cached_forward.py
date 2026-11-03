#!/usr/bin/env python3
"""Re-extract archived PDFs through prepare in an isolated diagnostic run.

No network, no formal reranking, no skip-prepare, no archive overwrite. This
validates a recorded snapshot, not the completeness of today's disclosures.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from forward_replay import replay_forward_analysis
from document_cache import atomic_write_text
from parse_forward_dividend_evidence import extract_document_pages
from run_forward_dividend_analysis import run_forward_analysis


class RecordedClient:
    def __init__(self, archive):
        self.documents = {}
        self.files = {}
        for manifest in (archive / "market_data" / "forward_dividend_evidence").glob("*/manifest.json"):
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            self.documents[payload["ts_code"]] = payload["documents"]
            for d in payload["documents"]:
                self.files[d["source_url"]] = (manifest.parent / d["source_file"], d.get("sha256"))

    def resolve_stock(self, code):
        return {"code": code} if code in self.documents else None

    def query_announcements(self, code, **kwargs):
        return [{"announcementId": d["announcement_id"], "announcementTitle": d["source_title"],
                 "announcementTime": int(dt.datetime.fromisoformat(d["available_at"]).timestamp()*1000),
                 "adjunctUrl": d["source_url"].removeprefix("https://static.cninfo.com.cn/"),
                 "adjunctType": "PDF"} for d in self.documents.get(code, [])]

    def download_bytes(self, url):
        path, expected_hash = self.files[url]
        content = path.read_bytes()
        if not expected_hash or hashlib.sha256(content).hexdigest() != expected_hash:
            raise ValueError(f"archived source hash mismatch: {path}")
        return content


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--run-date", required=True)
    parser.add_argument("--diagnostics-root", type=Path, required=True)
    args = parser.parse_args()
    archive = args.archive.resolve()
    args.diagnostics_root.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix=f"history-model-{args.run_date}-", dir=args.diagnostics_root)).resolve()
    client = RecordedClient(archive)
    original_top10 = archive / f"hs300-dividend-top10-{args.run_date}.csv"
    original_state = (hashlib.sha256(original_top10.read_bytes()).hexdigest(), original_top10.stat().st_mtime_ns)
    snapshot = output / original_top10.name
    shutil.copy2(original_top10, snapshot)

    def extract(document, root):
        def run_command(command, **kwargs):
            result = subprocess.run(command, **kwargs, capture_output=True, text=True)
            if result.stderr:
                atomic_write_text(root / "extracted" / f"{document['announcement_id']}.stderr.log", result.stderr)
            return result
        return extract_document_pages(document, root, command_runner=run_command)

    try:
        status = run_forward_analysis(run_date=args.run_date, top10_path=snapshot,
                                      output_dir=output, client=client, download_bytes=client.download_bytes,
                                      page_provider=extract)
    finally:
        final_state = (hashlib.sha256(original_top10.read_bytes()).hexdigest(), original_top10.stat().st_mtime_ns)
        if final_state != original_state:
            raise RuntimeError("archived formal Top10 changed during diagnostic")
    print(json.dumps({"diagnostic_only": True, "output_dir": str(output), "status": status["status"],
                      "archived_top10_unchanged": True, "archived_top10_sha256": original_state[0],
                      "replay": replay_forward_analysis(output / "market_data" / "forward-dividend-replay-manifest.json")},
                     ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
