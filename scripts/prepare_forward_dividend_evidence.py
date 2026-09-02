#!/usr/bin/env python3
"""Prepare original-document evidence packages for a formal Top10."""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any, Callable

from announcement_resolver import resolve_announcements
from document_cache import atomic_write_text, store_document


def collect_candidate_announcements(
    client,
    code: str,
    *,
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    queries = [
        {"category": "category_ndbg_szsh", "searchkey": ""},
        {"category": "category_bndbg_szsh", "searchkey": ""},
        {"category": "", "searchkey": "分红回报规划"},
        {"category": "", "searchkey": "利润分配"},
        {"category": "", "searchkey": "权益分派"},
    ]
    collected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for query in queries:
        for announcement in client.query_announcements(
            code,
            start_date=start_date,
            end_date=end_date,
            category=query["category"],
            searchkey=query["searchkey"],
        ):
            identity = str(
                announcement.get("announcementId")
                or announcement.get("adjunctUrl")
                or announcement.get("announcementTitle")
                or ""
            )
            if identity in seen:
                continue
            seen.add(identity)
            collected.append(announcement)
    return collected


def _select_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    current = [
        source
        for source in sources
        if not source.get("superseded_by_source_id")
        and source.get("adjunct_type", "PDF").upper() == "PDF"
        and not any(token in source.get("source_title", "") for token in ["摘要", "英文版"])
    ]
    selected: list[dict[str, Any]] = []
    annual_by_year: dict[int, dict[str, Any]] = {}
    for source in current:
        if source["logical_role"] != "annual":
            continue
        year_match = re.search(r"(20\d{2})", source.get("source_title", ""))
        if not year_match:
            continue
        year = int(year_match.group(1))
        title = source.get("source_title", "")
        score = (10 if "H股公告" not in title else 0) + (2 if "A股" in title else 0)
        previous = annual_by_year.get(year)
        previous_title = previous.get("source_title", "") if previous else ""
        previous_score = (10 if previous and "H股公告" not in previous_title else 0) + (2 if "A股" in previous_title else 0)
        if previous is None or (score, source["available_at"]) > (previous_score, previous["available_at"]):
            annual_by_year[year] = source
    selected.extend(annual_by_year[year] for year in sorted(annual_by_year)[-3:])
    interim = [source for source in current if source["logical_role"] == "interim"]
    if interim:
        selected.append(max(interim, key=lambda source: source["available_at"]))
    selected.extend(
        source
        for source in current
        if source["logical_role"] in {"dividend_policy", "dividend_plan", "implementation"}
    )
    return sorted(selected, key=lambda source: (source["available_at"], source["announcement_id"]))


def prepare_forward_evidence(
    *,
    top_rows: list[dict[str, Any]],
    run_date: str,
    evidence_root: Path,
    client,
    download_bytes: Callable[[str], bytes],
) -> list[dict[str, Any]]:
    selected = [row for row in top_rows if str(row.get("selected", "是")) == "是"]
    if len(selected) > 10:
        raise ValueError("forward evidence accepts at most ten formal selected rows")
    evidence_root = Path(evidence_root)
    evidence_root.mkdir(parents=True, exist_ok=True)
    year = int(str(run_date)[:4])
    results: list[dict[str, Any]] = []
    for row in selected:
        code = str(row["ts_code"])
        company_dir = evidence_root / code
        company_dir.mkdir(parents=True, exist_ok=True)
        try:
            resolved_stock = client.resolve_stock(code)
            if resolved_stock is None:
                manifest = {
                    "ts_code": code,
                    "company_name": row.get("name", ""),
                    "run_date": run_date,
                    "status": "data_gap",
                    "reason": "CNinfo实体映射缺失",
                    "documents": [],
                }
            else:
                raw = collect_candidate_announcements(
                    client,
                    code,
                    start_date=f"{year - 6}0101",
                    end_date=str(run_date),
                )
                sources = _select_sources(resolve_announcements(raw, cutoff_at=str(run_date)))
                documents = []
                for source in sources:
                    role_dir = "reports" if source["logical_role"] in {"annual", "interim"} else "announcements"
                    stored = store_document(
                        directory=company_dir / role_dir,
                        announcement_id=source["announcement_id"],
                        logical_role=source["logical_role"],
                        suffix=".pdf",
                        content=download_bytes(source["source_url"]),
                    )
                    documents.append(
                        {
                            **source,
                            "source_final_url": source["source_url"],
                            "retrieved_at": dt.datetime.now(dt.UTC).isoformat(),
                            "source_file": f"{role_dir}/{stored['source_file']}",
                            "sha256": stored["sha256"],
                            "cache_status": stored["cache_status"],
                        }
                    )
                manifest = {
                    "ts_code": code,
                    "company_name": row.get("name", ""),
                    "rank": row.get("rank"),
                    "run_date": run_date,
                    "cutoff_at": f"{run_date}T23:59:59+08:00",
                    "generated_at": dt.datetime.now(dt.UTC).isoformat(),
                    "status": "normal" if documents else "data_gap",
                    "reason": "" if documents else "截止日内未找到前瞻分红原始证据",
                    "documents": documents,
                }
        except Exception as exc:
            manifest = {
                "ts_code": code,
                "company_name": row.get("name", ""),
                "run_date": run_date,
                "status": "failed",
                "reason": str(exc),
                "documents": [],
            }
        atomic_write_text(
            company_dir / "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2),
        )
        results.append(manifest)
    return results
