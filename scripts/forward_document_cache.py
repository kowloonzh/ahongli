"""Verified PDF reuse across forward runs, without changing archived evidence."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path

from document_cache import atomic_write_text, store_document, validate_document


def _read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _identity(code, source):
    return (code, str(source.get("announcement_id", "")), str(source.get("source_url", "")))


def _contained_file(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        return None
    path = (root / relative).resolve()
    return path if path.is_relative_to(root.resolve()) else None


class ForwardDocumentCache:
    def __init__(self, evidence_root):
        self.evidence_root = Path(evidence_root).resolve()
        output = self.evidence_root.parent.parent
        self.archive_root = output.parent if output.parent.name == "a_dividend_outputs" else None
        self.root = (self.archive_root.parent / "_forward_report_cache" if self.archive_root
                     else self.evidence_root / "_forward_report_cache")
        self.legacy = {}

    def _legacy_records(self, code):
        if code in self.legacy:
            return self.legacy[code]
        manifests = [self.evidence_root / code / "manifest.json"]
        if self.archive_root:
            manifests += sorted(self.archive_root.glob(f"*/market_data/forward_dividend_evidence/{code}/manifest.json"), reverse=True)
        records = []
        for manifest in dict.fromkeys(manifests):
            if manifest != manifests[0] and not re.fullmatch(r"\d{8}", manifest.parents[3].name):
                continue
            payload = _read_json(manifest)
            if payload.get("ts_code") != code:
                continue
            documents = payload.get("documents", [])
            for document in documents if isinstance(documents, list) else []:
                if isinstance(document, dict):
                    records.append((manifest, document))
        self.legacy[code] = records
        return records

    def get(self, *, code, source, directory, download_bytes, refresh=False):
        identity = _identity(code, source)
        key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        record_path = self.root / "records" / f"{key}.json"
        notes = []
        content, original, origin = None, {}, "network"

        def validated(path, record):
            digest = str(record.get("sha256", ""))
            if path and re.fullmatch(r"[a-f0-9]{64}", digest):
                try:
                    if validate_document(path, digest) and path.stat().st_size:
                        data = path.read_bytes()
                        if b"%PDF-" in data[:1024]:
                            return data
                except OSError:
                    pass
            notes.append({"path": str(path or ""), "reason": "missing, unsafe, non-PDF or SHA-256 mismatch"})
            return None

        if not refresh:
            shared = _read_json(record_path)
            if shared.get("identity") == list(identity):
                path = _contained_file(self.root, shared.get("blob_file"))
                content = validated(path, shared)
                if content is not None:
                    original, origin = shared, "shared"
            if content is None:
                candidates = []
                for manifest, document in self._legacy_records(code):
                    if _identity(code, document) != identity:
                        continue
                    path = _contained_file(manifest.parent, document.get("source_file"))
                    data = validated(path, document)
                    if data is not None:
                        candidates.append((manifest, document, data))
                if len({d[1]["sha256"] for d in candidates}) > 1:
                    notes.append({"reason": "conflicting archived hashes for same identity; network refresh required"})
                elif candidates:
                    manifest, original, content = candidates[0]
                    origin = "current_run" if manifest.parent.parent == self.evidence_root else "historical_run"
        now = dt.datetime.now(dt.UTC).isoformat()
        if content is None:
            content = download_bytes(source["source_url"])
            if not isinstance(content, bytes) or b"%PDF-" not in content[:1024]:
                raise ValueError("empty or invalid PDF download")
            original, origin = {"retrieved_at": now}, "network"
        digest = hashlib.sha256(content).hexdigest()
        # Content-addressed storage preserves old versions; no shared hardlinks
        # that would let corruption of one run mutate another archived run.
        blob = store_document(directory=self.root / "objects", announcement_id=digest,
                              logical_role="source", suffix=".pdf", content=content)
        record = {"identity": list(identity), "sha256": digest,
                  "blob_file": "objects/" + blob["source_file"],
                  "retrieved_at": original.get("retrieved_at", "")}
        atomic_write_text(record_path, json.dumps(record, ensure_ascii=False, indent=2))
        stored = store_document(directory=directory, announcement_id=source["announcement_id"],
                                logical_role=source["logical_role"], suffix=".pdf", content=content)
        return {**stored, "storage_status": stored["cache_status"],
                "cache_status": "downloaded" if origin == "network" else "reused",
                "cache_origin": origin, "cache_path": str(self.root / record["blob_file"]),
                "cache_checked_at": now, "retrieved_at": record["retrieved_at"],
                "cache_validation_notes": notes}
