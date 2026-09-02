#!/usr/bin/env python3
"""Classify and time-filter official disclosure documents."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any


CNINFO_TZ = dt.timezone(dt.timedelta(hours=8), "Asia/Shanghai")


def classify_announcement_title(title: str) -> str | None:
    text = str(title or "")
    if any(token in text for token in ["权益分派实施公告", "利润分配实施公告", "分红派息实施公告"]):
        return "implementation"
    if any(token in text for token in ["股东分红回报规划", "股东回报规划", "分红政策"]):
        return "dividend_policy"
    if any(token in text for token in ["利润分配预案", "利润分配方案", "分红派息方案"]):
        return "dividend_plan"
    if any(token in text for token in ["半年度报告", "中期报告"]):
        return "interim"
    if "年度报告" in text or "年年报" in text:
        return "annual"
    return None


def _parse_datetime(value: Any, *, end_of_day: bool = False) -> tuple[dt.datetime, str]:
    if isinstance(value, int | float) or re.fullmatch(r"\d{13}", str(value or "")):
        number = float(value)
        seconds = number / 1000 if number > 10_000_000_000 else number
        return dt.datetime.fromtimestamp(seconds, CNINFO_TZ), "timestamp"
    text = str(value or "").strip()
    if re.fullmatch(r"\d{8}", text):
        parsed = dt.datetime.strptime(text, "%Y%m%d").replace(tzinfo=CNINFO_TZ)
        if end_of_day:
            parsed = parsed.replace(hour=23, minute=59, second=59)
        return parsed, "date_only"
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
        try:
            parsed = dt.datetime.strptime(text, fmt).replace(tzinfo=CNINFO_TZ)
            quality = "timestamp" if "%H" in fmt else "date_only"
            if end_of_day and quality == "date_only":
                parsed = parsed.replace(hour=23, minute=59, second=59)
            return parsed, quality
        except ValueError:
            continue
    raise ValueError(f"unsupported datetime: {value}")


def resolve_announcements(
    announcements: list[dict[str, Any]],
    *,
    cutoff_at: str,
) -> list[dict[str, Any]]:
    cutoff, _ = _parse_datetime(cutoff_at, end_of_day=True)
    results: list[dict[str, Any]] = []
    for announcement in announcements:
        role = classify_announcement_title(announcement.get("announcementTitle", ""))
        if not role:
            continue
        available, quality = _parse_datetime(announcement.get("announcementTime"))
        if available > cutoff:
            continue
        adjunct = str(announcement.get("adjunctUrl") or "").lstrip("/")
        results.append(
            {
                "announcement_id": str(announcement.get("announcementId") or ""),
                "source_title": str(announcement.get("announcementTitle") or ""),
                "logical_role": role,
                "announcement_date": available.strftime("%Y%m%d"),
                "available_date": available.strftime("%Y%m%d"),
                "available_at": available.isoformat(),
                "available_time_quality": quality,
                "source_url": f"https://static.cninfo.com.cn/{adjunct}" if adjunct else "",
                "adjunct_type": str(announcement.get("adjunctType") or "PDF"),
                "supersedes_source_id": "",
                "superseded_by_source_id": "",
            }
        )
    results.sort(key=lambda row: (row["available_at"], row["announcement_id"]))
    previous_by_title: dict[tuple[str, str], dict[str, Any]] = {}
    for row in results:
        normalized_title = re.sub(r"[（(]?(修订稿|修订版|更正后|更新后)[）)]?", "", row["source_title"])
        key = (row["logical_role"], normalized_title)
        previous = previous_by_title.get(key)
        if previous:
            row["supersedes_source_id"] = previous["announcement_id"]
            previous["superseded_by_source_id"] = row["announcement_id"]
        previous_by_title[key] = row
    return results
