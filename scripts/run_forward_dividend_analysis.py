#!/usr/bin/env python3
"""Run the independent forward-dividend transaction for a formal Top10."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from cninfo_client import CnInfoClient, load_stock_maps
from forecast_selected_dividends import forecast_selected_dividends
from parse_forward_dividend_evidence import parse_company_evidence
from prepare_forward_dividend_evidence import prepare_forward_evidence
from render_forward_dividend_outputs import render_forward_dividend_outputs


SKILL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RISK_FREE_RATE = 0.016883
DEFAULT_RISK_FREE_RATE_DATE = "20260831"
DEFAULT_RISK_FREE_RATE_SOURCE = "https://yield.chinabond.com.cn/cbweb-pbc-web/pbc/more?locale=cn_ZH"


def _console_number(value: Any, digits: int = 2) -> str:
    if value in {None, ""}:
        return "—"
    return f"{float(value):.{digits}f}"


def _console_percent(value: Any, *, stored_as_ratio: bool = True) -> str:
    if value in {None, ""}:
        return "—"
    multiplier = 100 if stored_as_ratio else 1
    return f"{float(value) * multiplier:.2f}%"


def format_console_top10(rows: list[dict[str, Any]], *, run_date: str) -> str:
    lines = [
        f"AHongli A股前瞻Top10 {run_date}",
        "排名 | 公司 | 得分 | 股价 | 预期分红 | 预期股息率 | 当前位置 | 目标股息率区间",
        "--- | --- | ---: | ---: | ---: | ---: | --- | ---:",
    ]
    for row in sorted(rows, key=lambda item: int(item.get("rank") or 10_000)):
        target_yield = (
            f"{_console_percent(row.get('target_yield_low'))}–"
            f"{_console_percent(row.get('target_yield_high'))}"
        )
        lines.append(
            f"{row.get('rank','')} | {row.get('name','')} | {_console_number(row.get('dividend_score_total'))} | "
            f"{_console_number(row.get('quote_price'))} | {_console_number(row.get('forecast_fy_regular_dps_base'), 4)} | "
            f"{_console_percent(row.get('expected_dividend_yield'))} | "
            f"{row.get('target_display_label') or '暂不判断'} | {target_yield}"
        )
    return "\n".join(lines)


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _write_failure_status(
    *,
    path: Path,
    run_date: str,
    started_at: str,
    top10_path: Path,
    top10_sha: str,
    top10_mtime: int,
    error: Exception,
) -> None:
    _atomic_json(
        path,
        {
            "run_date": run_date,
            "started_at": started_at,
            "finished_at": dt.datetime.now(dt.UTC).isoformat(),
            "status": "failed",
            "input_top10": str(top10_path),
            "input_top10_sha256": top10_sha,
            "input_top10_mtime_ns": top10_mtime,
            "counts": {"announced": 0, "modelled": 0, "data_gap": 0, "unsupported": 0, "failed": 1},
            "error": str(error),
            "outputs": {},
        },
    )


def _read_top10(path: Path) -> list[dict[str, Any]]:
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    selected = [row for row in rows if str(row.get("selected", "是")) == "是"]
    if len(rows) > 10 or len(selected) != len(rows):
        raise ValueError("forward input must contain at most ten formal selected rows")
    return rows


def _load_evidence(evidence_root: Path, rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result = {}
    for row in rows:
        code = str(row["ts_code"])
        path = evidence_root / code / "forecast-evidence.json"
        if path.exists():
            result[code] = json.loads(path.read_text(encoding="utf-8"))
    return result


def run_forward_analysis(
    *,
    run_date: str,
    top10_path: Path,
    output_dir: Path,
    skip_prepare: bool = False,
    client=None,
    download_bytes=None,
    page_provider=None,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    risk_free_rate_date: str = DEFAULT_RISK_FREE_RATE_DATE,
    risk_free_rate_source: str = DEFAULT_RISK_FREE_RATE_SOURCE,
) -> dict[str, Any]:
    top10_path = Path(top10_path).resolve()
    output_dir = Path(output_dir).resolve()
    market_dir = output_dir / "market_data"
    evidence_root = market_dir / "forward_dividend_evidence"
    before_sha = _sha256(top10_path)
    before_mtime = top10_path.stat().st_mtime_ns
    started_at = dt.datetime.now(dt.UTC).isoformat()
    rows = _read_top10(top10_path)
    if not skip_prepare:
        try:
            if client is None:
                client = CnInfoClient(
                    stocks=load_stock_maps(
                        SKILL_ROOT / "assets" / "stocks.json",
                        SKILL_ROOT / "assets" / "forward_stocks.json",
                    )
                )
            if download_bytes is None:
                download_bytes = client.download_bytes
            prepare_forward_evidence(
                top_rows=rows,
                run_date=run_date,
                evidence_root=evidence_root,
                client=client,
                download_bytes=download_bytes,
            )
            for row in rows:
                manifest = evidence_root / str(row["ts_code"]) / "manifest.json"
                if not manifest.exists():
                    continue
                try:
                    if page_provider is None:
                        parse_company_evidence(manifest)
                    else:
                        parse_company_evidence(manifest, page_provider=page_provider)
                except Exception as exc:
                    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
                    _atomic_json(
                        manifest.parent / "forecast-evidence.json",
                        {
                            "ts_code": row["ts_code"],
                            "company_name": row.get("name", ""),
                            "run_date": run_date,
                            "stage_status": "failed",
                            "stage_reason": str(exc),
                            "source_documents": manifest_payload.get("documents", []),
                            "normalized_facts": [],
                            "dividend_events": [],
                        },
                    )
        except Exception as exc:
            _write_failure_status(
                path=market_dir / "forward-dividend-run-status.json",
                run_date=run_date,
                started_at=started_at,
                top10_path=top10_path,
                top10_sha=before_sha,
                top10_mtime=before_mtime,
                error=exc,
            )
            raise
    try:
        evidence = _load_evidence(evidence_root, rows)
        forecast_rows = forecast_selected_dividends(
            top_rows=rows,
            evidence_by_code=evidence,
            run_date=run_date,
            risk_free_rate=risk_free_rate,
            risk_free_rate_date=risk_free_rate_date,
            risk_free_rate_source=risk_free_rate_source,
        )
        outputs = render_forward_dividend_outputs(
            rows=forecast_rows,
            run_date=run_date,
            output_dir=output_dir,
            evidence_by_code=evidence,
        )
        after_sha = _sha256(top10_path)
        after_mtime = top10_path.stat().st_mtime_ns
        if (after_sha, after_mtime) != (before_sha, before_mtime):
            raise RuntimeError("formal Top10 changed during forward-dividend transaction")
        statuses = [str(row.get("forecast_status") or "failed") for row in forecast_rows]
        counts = {
            status: statuses.count(status)
            for status in ["announced", "modelled", "data_gap", "unsupported", "failed"]
        }
        if forecast_rows and counts["failed"] == len(forecast_rows):
            transaction_status = "failed"
        elif counts["data_gap"] + counts["unsupported"] + counts["failed"]:
            transaction_status = "partial"
        else:
            transaction_status = "success"
        run_status = {
            "run_date": run_date,
            "started_at": started_at,
            "finished_at": dt.datetime.now(dt.UTC).isoformat(),
            "status": transaction_status,
            "input_top10": str(top10_path),
            "input_top10_sha256": before_sha,
            "input_top10_mtime_ns": before_mtime,
            "counts": counts,
            "outputs": {name: str(path) for name, path in outputs.items()},
        }
        _atomic_json(market_dir / "forward-dividend-run-status.json", run_status)
        return {"rows": forecast_rows, "outputs": outputs, "status": run_status}
    except Exception as exc:
        _write_failure_status(
            path=market_dir / "forward-dividend-run-status.json",
            run_date=run_date,
            started_at=started_at,
            top10_path=top10_path,
            top10_sha=before_sha,
            top10_mtime=before_mtime,
            error=exc,
        )
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Run AHongli forward-dividend analysis for a formal Top10.")
    parser.add_argument("--run-date", required=True)
    parser.add_argument("--top10", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--skip-prepare", action="store_true")
    parser.add_argument("--risk-free-rate", type=float, default=DEFAULT_RISK_FREE_RATE)
    parser.add_argument("--risk-free-rate-date", default=DEFAULT_RISK_FREE_RATE_DATE)
    parser.add_argument("--risk-free-rate-source", default=DEFAULT_RISK_FREE_RATE_SOURCE)
    args = parser.parse_args()
    output_dir = args.output_dir or args.top10.resolve().parent
    result = run_forward_analysis(
        run_date=args.run_date,
        top10_path=args.top10,
        output_dir=output_dir,
        skip_prepare=args.skip_prepare,
        risk_free_rate=args.risk_free_rate,
        risk_free_rate_date=args.risk_free_rate_date,
        risk_free_rate_source=args.risk_free_rate_source,
    )
    print(json.dumps(result["status"], ensure_ascii=False, indent=2))
    print()
    print(format_console_top10(result["rows"], run_date=args.run_date))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
