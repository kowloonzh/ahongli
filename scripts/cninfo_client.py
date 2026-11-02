#!/usr/bin/env python3
"""Shared CNinfo client for bank and forward-dividend evidence."""

from __future__ import annotations

import json
import os
import random
import re
import time
from pathlib import Path
from typing import Any, Callable

try:
    import httpx
except ImportError:  # pragma: no cover - surfaced by build_page_fetcher
    httpx = None


TRANSIENT_HTTP_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})


def _is_transient_error(exc: Exception) -> bool:
    if isinstance(exc, OSError):
        return True
    if httpx is None:
        return False
    if hasattr(httpx, "TransportError") and isinstance(exc, httpx.TransportError):
        return True
    if hasattr(httpx, "HTTPStatusError") and isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in TRANSIENT_HTTP_STATUS_CODES
    return False


def _with_transient_retry(operation, *, attempts: int = 4, base_delay: float = 0.2):
    for attempt in range(attempts):
        try:
            return operation()
        except Exception as exc:
            if not _is_transient_error(exc) or attempt == attempts - 1:
                raise
            delay = base_delay * (2**attempt) * random.uniform(0.8, 1.2)
            time.sleep(delay)
    raise RuntimeError("unreachable")


def load_stock_maps(*paths: Path) -> dict[str, dict[str, dict[str, Any]]]:
    combined: dict[str, dict[str, dict[str, Any]]] = {}
    for path in paths:
        path = Path(path)
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        for market, entries in payload.items():
            combined.setdefault(market, {}).update(entries)
    return combined


CNINFO_STOCK_LIST_URL = "https://www.cninfo.com.cn/new/data/szse_stock.json"
CNINFO_STOCK_LIST_MARKET = "szse"
MIN_CNINFO_STOCK_LIST_ENTRIES = 1000
_ORG_ID_PATTERN = re.compile(r"[0-9A-Za-z]{6,20}")
_STOCK_CODE_PATTERN = re.compile(r"\d{6}")


def parse_cninfo_stock_list(payload: Any) -> dict[str, dict[str, Any]]:
    """Validate CNinfo's public A-share list and index A-share entries by code."""
    entries = payload.get("stockList") if isinstance(payload, dict) else None
    if not isinstance(entries, list) or len(entries) < MIN_CNINFO_STOCK_LIST_ENTRIES:
        raise ValueError("CNinfo股票列表格式无效或条目过少")
    indexed: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        code = str(entry.get("code") or "")
        org_id = str(entry.get("orgId") or "")
        if entry.get("category") != "A股":
            continue
        if not _STOCK_CODE_PATTERN.fullmatch(code) or not _ORG_ID_PATTERN.fullmatch(org_id):
            continue
        indexed[code] = {
            "code": code,
            "pinyin": str(entry.get("pinyin") or ""),
            "category": "A股",
            "orgId": org_id,
            "zwjc": str(entry.get("zwjc") or ""),
        }
    if len(indexed) < MIN_CNINFO_STOCK_LIST_ENTRIES:
        raise ValueError("CNinfo股票列表有效A股条目过少")
    return indexed


def download_cninfo_stock_list() -> bytes:
    if httpx is None:
        raise RuntimeError("httpx is required for CNinfo downloads")

    def request() -> bytes:
        response = httpx.get(
            CNINFO_STOCK_LIST_URL,
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"},
            timeout=60.0,
        )
        response.raise_for_status()
        return response.content

    return _with_transient_retry(request)


def cached_cninfo_stock_list_loader(
    cache_path: Path,
    *,
    download: Callable[[], bytes] = download_cninfo_stock_list,
) -> Callable[[], dict[str, dict[str, Any]]]:
    """Reuse the run's archived CNinfo list, otherwise download and archive it once."""
    cache_path = Path(cache_path)

    def load() -> dict[str, dict[str, Any]]:
        if cache_path.exists():
            try:
                return parse_cninfo_stock_list(json.loads(cache_path.read_text(encoding="utf-8")))
            except (ValueError, json.JSONDecodeError):
                pass
        raw = download()
        indexed = parse_cninfo_stock_list(json.loads(raw.decode("utf-8")))
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache_path.with_suffix(cache_path.suffix + ".tmp")
        temporary.write_bytes(raw)
        temporary.replace(cache_path)
        return indexed

    return load


class CnInfoClient:
    def __init__(
        self,
        *,
        stocks: dict[str, dict[str, dict[str, Any]]],
        stock_list_loader: Callable[[], dict[str, dict[str, Any]]] | None = None,
    ):
        self.stocks = stocks
        self._stock_list_loader = stock_list_loader
        self._stock_list: dict[str, dict[str, Any]] | None = None

    def resolve_stock(self, stock_input: str) -> dict[str, Any] | None:
        needle = str(stock_input).split(".", 1)[0]
        for market, entries in self.stocks.items():
            if needle in entries:
                return {"stock_code": needle, "market": market, **entries[needle]}
            for code, info in entries.items():
                if info.get("zwjc") == stock_input:
                    return {"stock_code": code, "market": market, **info}
        if self._stock_list_loader is None or not _STOCK_CODE_PATTERN.fullmatch(needle):
            return None
        if self._stock_list is None:
            self._stock_list = self._stock_list_loader()
        entry = self._stock_list.get(needle)
        if entry is None:
            return None
        return {
            "stock_code": needle,
            "market": CNINFO_STOCK_LIST_MARKET,
            **entry,
            "mapping_source": "cninfo_stock_list",
        }

    def build_page_fetcher(self) -> Callable[[dict[str, Any]], dict[str, Any]]:
        if httpx is None:
            raise RuntimeError("httpx is required for CNinfo downloads")
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://www.cninfo.com.cn",
            "Referer": "https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
        }
        cookies = {}
        if value := os.environ.get("CNINFO_JSESSIONID", "").strip():
            cookies["JSESSIONID"] = value
        if value := os.environ.get("CNINFO_INSERT_COOKIE", "").strip():
            cookies["insert_cookie"] = value

        def fetch(payload: dict[str, Any]) -> dict[str, Any]:
            def request():
                with httpx.Client(headers=headers, cookies=cookies, timeout=60.0) as client:
                    response = client.post(
                        "https://www.cninfo.com.cn/new/hisAnnouncement/query",
                        data=payload,
                    )
                    response.raise_for_status()
                    return response.json()

            return _with_transient_retry(request)

        return fetch

    def download_bytes(self, url: str) -> bytes:
        if httpx is None:
            raise RuntimeError("httpx is required for CNinfo downloads")
        headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
        cookies = {}
        if value := os.environ.get("CNINFO_JSESSIONID", "").strip():
            cookies["JSESSIONID"] = value
        if value := os.environ.get("CNINFO_INSERT_COOKIE", "").strip():
            cookies["insert_cookie"] = value
        def request():
            with httpx.Client(headers=headers, cookies=cookies, timeout=60.0) as client:
                response = client.get(url)
                response.raise_for_status()
                return response.content

        return _with_transient_retry(request)

    def query_announcements(
        self,
        stock_input: str,
        *,
        start_date: str,
        end_date: str,
        searchkey: str = "",
        category: str = "",
        fetch_page: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        stock = self.resolve_stock(stock_input)
        if stock is None:
            raise ValueError(f"CNinfo stock mapping missing: {stock_input}")
        def api_date(value: str) -> str:
            text = str(value)
            if re.fullmatch(r"\d{8}", text):
                return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
            return text

        payload = {
            "pageNum": 0,
            "pageSize": 30,
            "column": stock["market"],
            "tabName": "fulltext",
            "plate": "",
            "stock": f"{stock['stock_code']},{stock['orgId']}",
            "searchkey": searchkey,
            "secid": "",
            "category": category,
            "trade": "",
            "seDate": f"{api_date(start_date)}~{api_date(end_date)}",
            "sortName": "",
            "sortType": "",
            "isHLtitle": False,
        }
        fetch = fetch_page or self.build_page_fetcher()
        announcements: list[dict[str, Any]] = []
        has_more = True
        while has_more:
            payload["pageNum"] += 1
            data = fetch(payload)
            announcements.extend(data.get("announcements") or [])
            has_more = bool(data.get("hasMore"))
        return announcements
