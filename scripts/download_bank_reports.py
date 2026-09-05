#!/usr/bin/env python3
"""Download CNinfo financial reports and write an agent-friendly manifest."""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any

try:
    import httpx
except ImportError:  # pragma: no cover - exercised by CLI users
    httpx = None


def _load_transient_retry():
    path = Path(__file__).with_name("cninfo_client.py")
    spec = importlib.util.spec_from_file_location("bank_cninfo_client", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module._with_transient_retry


_with_transient_retry = _load_transient_retry()


SKILL_DIR = Path(__file__).resolve().parents[1]
STOCKS_JSON = SKILL_DIR / "assets" / "stocks.json"
CNINFO_TZ = dt.timezone(dt.timedelta(hours=8), "Asia/Shanghai")
PERIODIC_REPORT_CONFIGS = [
    ("q1", "category_yjdbg_szsh", "一季度报告", 1, 1, 12, 31),
    ("semi", "category_bndbg_szsh", "半年度报告", 1, 1, 12, 31),
    ("q3", "category_sjdbg_szsh", "三季度报告", 1, 1, 12, 31),
]
PERIODIC_PERIOD_END = {
    "q1": (3, 31),
    "semi": (6, 30),
    "q3": (9, 30),
}


def to_chinese_year(year: int) -> str:
    mapping = {
        "0": "零",
        "1": "一",
        "2": "二",
        "3": "三",
        "4": "四",
        "5": "五",
        "6": "六",
        "7": "七",
        "8": "八",
        "9": "九",
    }
    return "".join(mapping[d] for d in str(year))


def clean_filename(value: str) -> str:
    value = value.replace("*", "s").replace("/", "-").replace("\\", "-")
    return "".join(c for c in value if c.isalnum() or c in "._-")


def parse_as_of_date(value: str) -> dt.date:
    if not re.fullmatch(r"\d{8}", value):
        raise argparse.ArgumentTypeError("--as-of-date must use YYYYMMDD format")
    try:
        return dt.datetime.strptime(value, "%Y%m%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid --as-of-date: {value}") from exc


def format_as_of_date(value: dt.date) -> str:
    return value.strftime("%Y%m%d")


def format_report_cutoff(value: dt.date) -> str:
    return f"{value:%Y-%m-%d} 23:59:59 Asia/Shanghai"


def parse_announcement_date(announcement_time: Any) -> dt.date | None:
    if announcement_time is None:
        return None
    if isinstance(announcement_time, int | float):
        seconds = float(announcement_time) / 1000 if announcement_time > 10_000_000_000 else float(announcement_time)
        return dt.datetime.fromtimestamp(seconds, CNINFO_TZ).date()

    text = str(announcement_time).strip()
    if not text:
        return None
    if re.fullmatch(r"\d{13}", text):
        return dt.datetime.fromtimestamp(int(text) / 1000, CNINFO_TZ).date()
    if re.fullmatch(r"\d{10}", text):
        return dt.datetime.fromtimestamp(int(text), CNINFO_TZ).date()
    if re.fullmatch(r"\d{8}", text):
        return dt.datetime.strptime(text, "%Y%m%d").date()

    match = re.search(r"(\d{4})[-年/](\d{1,2})[-月/](\d{1,2})", text)
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    return dt.date(year, month, day)


def is_announced_by(announcement: dict[str, Any], as_of_date: dt.date) -> bool:
    announcement_date = parse_announcement_date(announcement.get("announcementTime"))
    return announcement_date is not None and announcement_date <= as_of_date


def periodic_period_end(report_type: str, fiscal_year: int) -> dt.date:
    month, day = PERIODIC_PERIOD_END[report_type]
    return dt.date(fiscal_year, month, day)


def load_stocks(path: Path = STOCKS_JSON) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


class CnInfoDownloader:
    """Small CNinfo client for A-share and Hong Kong periodic reports."""

    def __init__(self, stocks: dict[str, dict[str, Any]] | None = None):
        self.market_to_stocks = stocks if stocks is not None else load_stocks()
        self.cookies = {}
        if value := os.environ.get("CNINFO_JSESSIONID", "").strip():
            self.cookies["JSESSIONID"] = value
        if value := os.environ.get("CNINFO_INSERT_COOKIE", "").strip():
            self.cookies["insert_cookie"] = value
        self.headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://www.cninfo.com.cn",
            "Referer": "https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search&lastPage=index",
        }
        self.query_url = "https://www.cninfo.com.cn/new/hisAnnouncement/query"

    def find_stock(self, stock_input: str) -> tuple[str | None, dict[str, Any] | None, str | None]:
        for market, market_stocks in self.market_to_stocks.items():
            if stock_input in market_stocks:
                return stock_input, market_stocks[stock_input], market

        for market, market_stocks in self.market_to_stocks.items():
            for code, info in market_stocks.items():
                if info.get("zwjc") == stock_input:
                    return code, info, market
        return None, None, None

    def query_announcements(self, filter_params: dict[str, Any], market: str) -> list[dict[str, Any]]:
        if httpx is None:
            raise RuntimeError("httpx is required. Install with: python3 -m pip install httpx")

        stock_code = filter_params["stock"][0]
        stock_info = self._stock_info(stock_code)
        if not stock_info:
            return []

        payload = self._build_payload(stock_code, stock_info, market, filter_params)
        announcements: list[dict[str, Any]] = []

        has_more = True
        while has_more:
            payload["pageNum"] += 1

            def request_page():
                timeout = httpx.Timeout(connect=10.0, read=15.0, write=10.0, pool=10.0)
                with httpx.Client(headers=self.headers, cookies=self.cookies, timeout=timeout) as client:
                    response = client.post(self.query_url, data=payload)
                    response.raise_for_status()
                    return response.json()

            try:
                data = _with_transient_retry(request_page)
            except Exception as exc:
                exc.add_note(
                    "CNinfo announcement query failed: "
                    f"stock={stock_code}, page={payload['pageNum']}, seDate={payload.get('seDate', '')}"
                )
                raise
            has_more = data.get("hasMore", False)
            announcements.extend(data.get("announcements") or [])

        return announcements

    def _stock_info(self, stock_code: str) -> dict[str, Any] | None:
        for market_stocks in self.market_to_stocks.values():
            if stock_code in market_stocks:
                return market_stocks[stock_code]
        return None

    def _build_payload(
        self,
        stock_code: str,
        stock_info: dict[str, Any],
        market: str,
        filter_params: dict[str, Any],
    ) -> dict[str, Any]:
        if market == "hke":
            category = ""
            searchkey = ""
        else:
            category = ";".join(filter_params.get("category", []))
            searchkey = filter_params.get("searchkey", "")

        return {
            "pageNum": 0,
            "pageSize": 30,
            "column": market,
            "tabName": "fulltext",
            "plate": "",
            "stock": f"{stock_code},{stock_info['orgId']}",
            "searchkey": searchkey,
            "secid": "",
            "category": category,
            "trade": "",
            "seDate": filter_params.get("seDate", ""),
            "sortName": "",
            "sortType": "",
            "isHLtitle": False,
        }

    def is_main_annual_report(self, title: str, year: int, market: str) -> bool:
        chinese_year = to_chinese_year(year)
        if market == "hke":
            has_year = str(year) in title or chinese_year in title
            is_annual = any(
                token in title
                for token in [
                    "年年度报告",
                    "年度报告",
                    "年度业绩公布",
                    "年度业绩公告",
                    "年度之业绩公布",
                    "年报",
                ]
            )
            blocked = [
                "summary",
                "xbrl",
                "摘要",
                "英文",
                "季度",
                "半年度",
                "中期",
                "可持续",
                "股东会",
                "董事会",
                "月报",
                "月表",
                "股息",
                "业绩快报",
            ]
            return has_year and is_annual and not any(token in title.lower() for token in blocked)

        if f"{year}年年度报告" not in title and f"{year}年度报告" not in title and f"{year}年年报" not in title:
            return False
        return not any(token in title.lower() for token in ["摘要", "英文", "更正", "修订", "xbrl"])

    def is_main_periodic_report(self, title: str, report_type: str, year: int, market: str) -> bool:
        return self._periodic_report_score(title, report_type, year, market) is not None

    def _periodic_report_score(self, title: str, report_type: str, year: int, market: str) -> int | None:
        if any(token in title for token in ["摘要", "英文", "更正", "修订", "取消"]):
            return None
        patterns = {
            "q1": ["一季度", "第一季度"],
            "semi": ["半年度报告", "中期报告"],
            "q3": ["三季度", "第三季度"],
        }
        if not any(token in title for token in patterns[report_type]):
            return None

        if market != "hke":
            year_tokens = [f"{year}年", f"{year} 年", str(year)]
            if not any(token in title for token in year_tokens):
                return None

        score = 100
        if any(token in title for token in ["更新后", "补充更正后", "修订稿"]):
            score -= 20
        if "公告" in title and "报告" not in title:
            score -= 10
        if "报告" in title:
            score += 10
        if str(year) in title:
            score += 5
        if report_type == "q1" and any(token in title for token in ["一季度报告", "第一季度报告"]):
            score += 5
        return score

    def select_main_periodic_announcement(
        self, announcements: list[dict[str, Any]], report_type: str, year: int, market: str
    ) -> dict[str, Any] | None:
        scored: list[tuple[int, int, dict[str, Any]]] = []
        for index, announcement in enumerate(announcements):
            score = self._periodic_report_score(announcement.get("announcementTitle", ""), report_type, year, market)
            if score is not None:
                scored.append((score, -index, announcement))
        if not scored:
            return None
        return max(scored, key=lambda item: (item[0], item[1]))[2]

    def download_annual_reports(
        self, stock_code: str, years: list[int], output_dir: Path, market: str
    ) -> list[dict[str, Any]]:
        reports: list[dict[str, Any]] = []
        for year in years:
            if market == "hke":
                start, end = f"{year}-01-01", f"{year + 1}-06-30"
                filter_params = {"stock": [stock_code], "category": [], "searchkey": "", "seDate": f"{start}~{end}"}
            else:
                start, end = f"{year + 1}-03-01", f"{year + 1}-06-30"
                filter_params = {
                    "stock": [stock_code],
                    "category": ["category_ndbg_szsh"],
                    "searchkey": f"{year}年年度报告",
                    "seDate": f"{start}~{end}",
                }

            announcements = self.query_announcements(filter_params, market)
            if not announcements and market != "hke":
                filter_params["searchkey"] = f"{year}年度报告"
                announcements = self.query_announcements(filter_params, market)

            for announcement in announcements:
                if self.is_main_annual_report(announcement["announcementTitle"], year, market):
                    path = self.download_pdf(announcement, output_dir)
                    if path:
                        reports.append(self._report_record("annual", year, announcement, path))
                    break
        return reports

    def download_periodic_reports(self, stock_code: str, year: int, output_dir: Path, market: str) -> list[dict[str, Any]]:
        candidates: list[tuple[dt.date, dt.date, str, dict[str, Any]]] = []
        for report_type, category, search_term, start_month, start_day, end_month, end_day in PERIODIC_REPORT_CONFIGS:
            start, end = f"{year}-{start_month:02d}-{start_day:02d}", f"{year}-{end_month:02d}-{end_day:02d}"
            if market == "hke":
                filter_params = {"stock": [stock_code], "category": [], "searchkey": "", "seDate": f"{start}~{end}"}
            else:
                filter_params = {
                    "stock": [stock_code],
                    "category": [category],
                    "searchkey": search_term,
                    "seDate": f"{start}~{end}",
                }
            announcement = self.select_main_periodic_announcement(
                self.query_announcements(filter_params, market), report_type, year, market
            )
            if announcement:
                announcement_date = parse_announcement_date(announcement.get("announcementTime")) or dt.date.min
                candidates.append((periodic_period_end(report_type, year), announcement_date, report_type, announcement))

        if not candidates:
            return []
        _, _, report_type, announcement = max(candidates, key=lambda item: (item[0], item[1]))
        path = self.download_pdf(announcement, output_dir)
        if not path:
            return []
        return [self._report_record(report_type, year, announcement, path)]

    def download_as_of_reports(
        self, stock_code: str, as_of_date: dt.date, output_dir: Path, market: str, include_periodic: bool = True
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        excluded_future: list[dict[str, Any]] = []
        excluded_keys: set[tuple[Any, str, int]] = set()
        reports = self._download_as_of_annual_reports(stock_code, as_of_date, output_dir, market, excluded_future, excluded_keys)
        if include_periodic:
            periodic = self._download_as_of_latest_periodic_report(
                stock_code, as_of_date, output_dir, market, excluded_future, excluded_keys
            )
            if periodic:
                reports.append(periodic)

        selection_metadata = {
            "as_of_date": format_as_of_date(as_of_date),
            "selection_mode": "as_of_date",
            "report_cutoff": format_report_cutoff(as_of_date),
            "excluded_future_announcements": excluded_future,
        }
        return reports, selection_metadata

    def _download_as_of_annual_reports(
        self,
        stock_code: str,
        as_of_date: dt.date,
        output_dir: Path,
        market: str,
        excluded_future: list[dict[str, Any]],
        excluded_keys: set[tuple[Any, str, int]],
    ) -> list[dict[str, Any]]:
        reports: list[dict[str, Any]] = []
        for year in range(as_of_date.year - 1, as_of_date.year - 16, -1):
            announcements = self._query_annual_announcements(stock_code, year, market)
            selected = self._select_annual_announcement_by_cutoff(
                announcements, year, market, as_of_date, excluded_future, excluded_keys
            )
            if not selected:
                continue
            path = self.download_pdf(selected, output_dir)
            if path:
                reports.append(self._report_record("annual", year, selected, path))
            if len(reports) >= 5:
                break
        return reports

    def _query_annual_announcements(self, stock_code: str, year: int, market: str) -> list[dict[str, Any]]:
        if market == "hke":
            start, end = f"{year}-01-01", f"{year + 1}-06-30"
            filter_params = {"stock": [stock_code], "category": [], "searchkey": "", "seDate": f"{start}~{end}"}
        else:
            start, end = f"{year + 1}-03-01", f"{year + 1}-06-30"
            filter_params = {
                "stock": [stock_code],
                "category": ["category_ndbg_szsh"],
                "searchkey": f"{year}年年度报告",
                "seDate": f"{start}~{end}",
            }

        announcements = self.query_announcements(filter_params, market)
        if not announcements and market != "hke":
            filter_params["searchkey"] = f"{year}年度报告"
            announcements = self.query_announcements(filter_params, market)
        return announcements

    def _select_annual_announcement_by_cutoff(
        self,
        announcements: list[dict[str, Any]],
        year: int,
        market: str,
        as_of_date: dt.date,
        excluded_future: list[dict[str, Any]],
        excluded_keys: set[tuple[Any, str, int]],
    ) -> dict[str, Any] | None:
        selected = None
        for announcement in announcements:
            if not self.is_main_annual_report(announcement.get("announcementTitle", ""), year, market):
                continue
            if is_announced_by(announcement, as_of_date):
                selected = announcement
                break
            self._append_excluded_future(
                excluded_future, excluded_keys, "annual", year, announcement, "announcement_time_after_as_of_date"
            )
        return selected

    def _download_as_of_latest_periodic_report(
        self,
        stock_code: str,
        as_of_date: dt.date,
        output_dir: Path,
        market: str,
        excluded_future: list[dict[str, Any]],
        excluded_keys: set[tuple[Any, str, int]],
    ) -> dict[str, Any] | None:
        candidates: list[tuple[dt.date, dt.date, str, int, dict[str, Any]]] = []
        for year in range(as_of_date.year, as_of_date.year - 3, -1):
            for report_type, category, search_term, start_month, start_day, end_month, end_day in PERIODIC_REPORT_CONFIGS:
                start = f"{year}-{start_month:02d}-{start_day:02d}"
                end = f"{year}-{end_month:02d}-{end_day:02d}"
                if market == "hke":
                    filter_params = {"stock": [stock_code], "category": [], "searchkey": "", "seDate": f"{start}~{end}"}
                else:
                    filter_params = {
                        "stock": [stock_code],
                        "category": [category],
                        "searchkey": search_term,
                        "seDate": f"{start}~{end}",
                    }
                for announcement in self.query_announcements(filter_params, market):
                    score = self._periodic_report_score(
                        announcement.get("announcementTitle", ""), report_type, year, market
                    )
                    if score is None:
                        continue
                    announcement_date = parse_announcement_date(announcement.get("announcementTime"))
                    if announcement_date is None:
                        continue
                    if announcement_date > as_of_date:
                        self._append_excluded_future(
                            excluded_future,
                            excluded_keys,
                            report_type,
                            year,
                            announcement,
                            "announcement_time_after_as_of_date",
                        )
                        continue
                    candidates.append((periodic_period_end(report_type, year), announcement_date, report_type, year, announcement))

        if not candidates:
            return None
        _, _, report_type, year, announcement = max(candidates, key=lambda item: (item[0], item[1]))
        path = self.download_pdf(announcement, output_dir)
        if not path:
            return None
        return self._report_record(report_type, year, announcement, path)

    def _append_excluded_future(
        self,
        excluded_future: list[dict[str, Any]],
        excluded_keys: set[tuple[Any, str, int]],
        report_type: str,
        fiscal_year: int,
        announcement: dict[str, Any],
        reason: str,
    ) -> None:
        key = (announcement.get("announcementId"), report_type, fiscal_year)
        if key in excluded_keys:
            return
        excluded_keys.add(key)
        excluded_future.append(
            {
                "report_type": report_type,
                "fiscal_year": fiscal_year,
                "title": announcement.get("announcementTitle"),
                "announcement_id": announcement.get("announcementId"),
                "announcement_time": announcement.get("announcementTime"),
                "reason": reason,
            }
        )

    def download_pdf(self, announcement: dict[str, Any], output_dir: Path) -> Path | None:
        if announcement.get("adjunctType") != "PDF":
            return None
        if httpx is None:
            raise RuntimeError("httpx is required. Install with: python3 -m pip install httpx")

        filename = clean_filename(
            f"{announcement['secCode']}_{announcement['secName']}_{announcement['announcementTitle']}_{announcement['announcementId']}.pdf"
        )
        path = output_dir / filename
        if path.exists():
            return path

        url = f"https://static.cninfo.com.cn/{announcement['adjunctUrl']}"

        def request_pdf() -> bytes:
            timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
            with httpx.Client(headers=self.headers, cookies=self.cookies, timeout=timeout) as client:
                response = client.get(url)
                response.raise_for_status()
                return response.content

        try:
            content = _with_transient_retry(request_pdf)
        except Exception as exc:
            exc.add_note(
                "CNinfo PDF download failed: "
                f"stock={announcement.get('secCode', '')}, announcement_id={announcement.get('announcementId', '')}"
            )
            raise
        path.write_bytes(content)
        time.sleep(random.uniform(0.5, 1.5))
        return path

    def _report_record(
        self, report_type: str, fiscal_year: int, announcement: dict[str, Any], path: Path
    ) -> dict[str, Any]:
        return {
            "report_type": report_type,
            "fiscal_year": fiscal_year,
            "title": announcement.get("announcementTitle"),
            "announcement_id": announcement.get("announcementId"),
            "announcement_time": announcement.get("announcementTime"),
            "path": str(path),
        }


def build_manifest(
    stock_code: str,
    stock_name: str,
    market: str,
    output_dir: Path,
    reports: list[dict[str, Any]],
    selection_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    manifest_reports = []
    seen_paths: set[str] = set()
    for report in reports:
        report_path = Path(report["path"]).resolve()
        path_value = os.path.relpath(report_path, output_dir)
        if path_value in seen_paths:
            continue
        seen_paths.add(path_value)
        manifest_reports.append(
            {
                **{k: v for k, v in report.items() if k != "path"},
                "path": path_value,
                "format": report_path.suffix.lower().lstrip("."),
            }
        )

    manifest = {
        "stock_code": stock_code,
        "stock_name": stock_name,
        "market": market,
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "output_dir": str(output_dir),
        "reports": manifest_reports,
    }
    if selection_metadata:
        manifest.update(selection_metadata)
    return manifest


def default_years(now: dt.datetime | None = None) -> list[int]:
    current_year = (now or dt.datetime.now()).year
    return list(range(current_year - 5, current_year))


def parse_years(value: str | None) -> list[int]:
    if not value:
        return default_years()
    years: list[int] = []
    for part in re.split(r"[, ]+", value.strip()):
        if part:
            years.append(int(part))
    return years


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stock", help="Stock code or Chinese short name, e.g. 600519 or 贵州茅台")
    parser.add_argument("--out", type=Path, default=Path("reports"), help="Output directory")
    parser.add_argument("--annual-years", help="Comma or space separated fiscal years. Default: last 5 completed years")
    parser.add_argument(
        "--as-of-date",
        type=parse_as_of_date,
        help="Historical replay cutoff in YYYYMMDD format; only announcements on or before this date are selected",
    )
    parser.add_argument("--skip-periodic", action="store_true", help="Only download annual reports")
    args = parser.parse_args(argv)

    if args.as_of_date and args.annual_years:
        parser.error("--annual-years cannot be combined with --as-of-date")

    output_dir = args.out.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    downloader = CnInfoDownloader()
    stock_code, stock_info, market = downloader.find_stock(args.stock)
    if not stock_code or not stock_info or not market:
        print(f"Stock not found: {args.stock}", file=sys.stderr)
        return 1

    stock_name = stock_info.get("zwjc", stock_code)
    selection_metadata = None
    if args.as_of_date:
        reports, selection_metadata = downloader.download_as_of_reports(
            stock_code, args.as_of_date, output_dir, market, include_periodic=not args.skip_periodic
        )
    else:
        reports = downloader.download_annual_reports(stock_code, parse_years(args.annual_years), output_dir, market)
        if not args.skip_periodic:
            current_year = dt.datetime.now().year
            periodic = downloader.download_periodic_reports(stock_code, current_year, output_dir, market)
            if not periodic:
                periodic = downloader.download_periodic_reports(stock_code, current_year - 1, output_dir, market)
            reports.extend(periodic)

    manifest = build_manifest(stock_code, stock_name, market, output_dir, reports, selection_metadata=selection_metadata)
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0 if reports else 2


if __name__ == "__main__":
    raise SystemExit(main())
