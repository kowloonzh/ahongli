#!/usr/bin/env python3
"""Build and verify portable forward-dividend replay manifests."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from forecast_selected_dividends import forecast_selected_dividends
from forward_dividend_models import load_target_yield_policy
from render_forward_dividend_outputs import FORWARD_FIELDS, _value


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _git_state(repo: Path) -> tuple[str, bool]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, capture_output=True
    ).stdout.strip()
    dirty = bool(subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, check=True, text=True, capture_output=True
    ).stdout.strip())
    return commit, dirty


def build_replay_manifest(
    *,
    output_dir: Path,
    top10_path: Path,
    evidence_by_code: dict[str, dict[str, Any]],
    policy_path: Path,
    forward_csv: Path,
    run_date: str,
    risk_free_rate: float,
    risk_free_rate_date: str,
    risk_free_rate_source: str,
    skill_root: Path,
) -> dict[str, Any]:
    output_dir = Path(output_dir).resolve()
    commit, dirty = _git_state(skill_root)
    evidence_files = []
    for code in sorted(evidence_by_code):
        path = output_dir / "market_data" / "forward_dividend_evidence" / code / "forecast-evidence.json"
        source_documents = []
        for document in evidence_by_code[code].get("source_documents", []):
            source_file = document.get("source_file")
            if not source_file:
                continue
            source_path = path.parent / str(source_file)
            if not source_path.exists():
                raise FileNotFoundError(source_path)
            actual_hash = _sha256(source_path)
            declared_hash = str(document.get("sha256") or "")
            if declared_hash and declared_hash != actual_hash:
                raise ValueError(f"source document hash mismatch: {source_path}")
            source_documents.append({
                "announcement_id": str(document.get("announcement_id") or ""),
                "path": str(source_path.relative_to(output_dir)),
                "sha256": actual_hash,
            })
        evidence_files.append({
            "ts_code": code,
            "path": str(path.relative_to(output_dir)),
            "sha256": _sha256(path),
            "source_documents": source_documents,
        })
    return {
        "schema_version": "1",
        "run_date": run_date,
        "code_commit": commit,
        "code_dirty": dirty,
        "parameters": {
            "risk_free_rate": risk_free_rate,
            "risk_free_rate_date": risk_free_rate_date,
            "risk_free_rate_source": risk_free_rate_source,
        },
        "policy": {
            "path": str(Path(policy_path).resolve().relative_to(output_dir)),
            "sha256": _sha256(policy_path),
        },
        "inputs": {
            "top10_path": str(Path(top10_path).resolve().relative_to(output_dir)),
            "top10_sha256": _sha256(top10_path),
            "evidence": evidence_files,
        },
        "outputs": {
            "forward_csv_path": str(Path(forward_csv).resolve().relative_to(output_dir)),
            "forward_csv_sha256": _sha256(forward_csv),
        },
    }


def replay_forward_analysis(manifest_path: Path, *, verify_code: bool = True) -> dict[str, Any]:
    manifest_path = Path(manifest_path).resolve()
    output_dir = manifest_path.parent.parent
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if verify_code:
        commit, dirty = _git_state(Path(__file__).resolve().parents[1])
        if commit != manifest["code_commit"] or dirty:
            raise RuntimeError("current code does not match clean manifest commit")
    top10 = output_dir / manifest["inputs"]["top10_path"]
    policy_path = output_dir / manifest["policy"]["path"]
    forward_csv = output_dir / manifest["outputs"]["forward_csv_path"]
    for path, expected in [
        (top10, manifest["inputs"]["top10_sha256"]),
        (policy_path, manifest["policy"]["sha256"]),
        (forward_csv, manifest["outputs"]["forward_csv_sha256"]),
    ]:
        if _sha256(path) != expected:
            raise ValueError(f"hash mismatch: {path}")
    with top10.open(encoding="utf-8-sig", newline="") as handle:
        top_rows = list(csv.DictReader(handle))
    evidence = {}
    for item in manifest["inputs"]["evidence"]:
        path = output_dir / item["path"]
        if _sha256(path) != item["sha256"]:
            raise ValueError(f"hash mismatch: {path}")
        evidence[item["ts_code"]] = json.loads(path.read_text(encoding="utf-8"))
        for document in item.get("source_documents", []):
            source_path = output_dir / document["path"]
            if _sha256(source_path) != document["sha256"]:
                raise ValueError(f"hash mismatch: {source_path}")
    params = manifest["parameters"]
    replayed = forecast_selected_dividends(
        top_rows=top_rows,
        evidence_by_code=evidence,
        run_date=manifest["run_date"],
        risk_free_rate=float(params["risk_free_rate"]),
        risk_free_rate_date=params["risk_free_rate_date"],
        risk_free_rate_source=params["risk_free_rate_source"],
        target_yield_policy=load_target_yield_policy(policy_path),
    )
    with forward_csv.open(encoding="utf-8-sig", newline="") as handle:
        persisted = list(csv.DictReader(handle))
    for row in replayed:
        row["evidence_detail_path"] = (
            f"market_data/forward_dividend_evidence/{row['ts_code']}/forecast-detail.md"
        )
    expected = [
        {field: str(_value(row, field)) for field in FORWARD_FIELDS}
        for row in sorted(replayed, key=lambda item: int(item.get("rank") or 10_000))
    ]
    if expected != persisted:
        differences = []
        for index, (left, right) in enumerate(zip(expected, persisted)):
            fields = {key: [left.get(key), right.get(key)] for key in FORWARD_FIELDS if left.get(key) != right.get(key)}
            if fields:
                differences.append({"row": index, "fields": fields})
        raise AssertionError("replayed forward rows differ from persisted CSV: " + json.dumps(differences[:3], ensure_ascii=False))
    return {"replay_verified": True, "row_count": len(expected), "code_commit": manifest["code_commit"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay and verify an AHongli forward-dividend run.")
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(replay_forward_analysis(args.manifest), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
