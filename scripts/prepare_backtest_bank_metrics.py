#!/usr/bin/env python3
"""Prepare multi-year bank report metrics for the historical backtest (B2).

Downloads the annual reports each bank needs (the three fiscal years before
every rebalance date at which it passed the market preflight), extracts them
with pdftotext and parses them with the production parser. Output is one CSV
with one row per bank and fiscal year plus the announcement date, so the
backtest can use only reports published by each rebalance date.

For each fiscal year the A-share report is read first; metrics it does not
yield (some PDFs carry broken font maps, some reports omit a ratio) are taken
from the H-share copy and then the 年度报告摘要, and each filled metric records
its source title. 拨贷比 still missing is derived as 不良贷款率 × 拨备覆盖率.
Reports live under ``{data_root}/a_dividend_backtest/bank_reports/`` and never
touch the formal run cache.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import prepare_bank_metrics as formal_preparer  # noqa: E402

parser_module = formal_preparer.parser_module
SHANGHAI = timezone(timedelta(hours=8))
HISTORY_FILENAME = "bank_quality_metrics_history.csv"


def announcement_date(milliseconds: Any) -> str:
    try:
        return datetime.fromtimestamp(int(milliseconds) / 1000, SHANGHAI).strftime("%Y%m%d")
    except (TypeError, ValueError):
        return ""


def derive_loan_provision_ratio(row: dict[str, Any]) -> dict[str, Any]:
    """Fill 拨贷比 from the identity 拨贷比 = 不良贷款率 × 拨备覆盖率 when a report omits it."""
    npl, coverage = row.get("npl_ratio"), row.get("provision_coverage_ratio")
    if row.get("loan_provision_ratio") is not None or npl is None or coverage is None:
        return row
    derived = dict(row)
    derived["loan_provision_ratio"] = round(float(npl) * float(coverage) / 100.0, 4)
    evidence = json.loads(derived.get("metric_evidence_json") or "{}")
    evidence["loan_provision_ratio"] = {"derived": "npl_ratio * provision_coverage_ratio / 100"}
    derived["metric_evidence_json"] = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
    derived["missing_metrics"] = [metric for metric in derived["missing_metrics"] if metric != "loan_provision_ratio"]
    derived["data_quality"] = "normal" if not derived["missing_metrics"] else "data_gap"
    return derived


def is_annual_summary(title: str, year: int) -> bool:
    return str(year) in title and "摘要" in title and not any(
        token in title.lower() for token in ["英文", "更正", "修订", "取消", "xbrl"]
    )


def ranked_announcements(downloader, stock_code: str, year: int, market: str) -> list[dict[str, Any]]:
    """One fiscal year's annual documents in fill order: A-share report, H-share copy, then summary.

    The formal downloader searches ``{year}年年度报告`` and only falls back to other
    wordings when that finds nothing, so a summary hit hides reports titled
    ``{year}年度报告`` or ``{year}年年报``; here the whole year's category is listed.
    """
    if market == "hke":
        announcements = downloader._query_annual_announcements(stock_code, year, market)
    else:
        announcements = downloader.query_announcements(
            {
                "stock": [stock_code],
                "category": ["category_ndbg_szsh"],
                "searchkey": "",
                "seDate": f"{year + 1}-01-01~{year + 1}-12-31",
            },
            market,
        )
    def title(announcement: dict[str, Any]) -> str:
        return str(announcement.get("announcementTitle", ""))

    main = [item for item in announcements if downloader.is_main_annual_report(title(item), year, market)]
    main.sort(key=lambda item: "H股" in title(item))
    summaries = [item for item in announcements if item not in main and is_annual_summary(title(item), year)]
    return main + summaries


def merge_metric_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Take each metric from the first document that discloses it, recording that document."""
    merged = {**rows[0], "missing_metrics": list(rows[0]["missing_metrics"])}
    evidence = json.loads(merged.get("metric_evidence_json") or "{}")
    used = [rows[0]]
    for row in rows[1:]:
        filled = False
        row_evidence = json.loads(row.get("metric_evidence_json") or "{}")
        for metric in list(merged["missing_metrics"]):
            if row.get(metric) is None:
                continue
            merged[metric] = row[metric]
            item = row_evidence.get(metric)
            evidence[metric] = {**(item if isinstance(item, dict) else {"evidence": item}), "source_title": row.get("title", "")}
            merged["missing_metrics"].remove(metric)
            filled = True
        if filled:
            used.append(row)
    merged["data_quality"] = "normal" if not merged["missing_metrics"] else "data_gap"
    merged["metric_evidence_json"] = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
    # Point in time: the merged row is only known once every document it draws on is out.
    merged["announcement_date"] = max(row["announcement_date"] for row in used if row.get("announcement_date")) if any(
        row.get("announcement_date") for row in used
    ) else ""
    merged["title"] = " + ".join(row.get("title", "") for row in used)
    merged["source_file"] = ";".join(str(row.get("source_file", "")) for row in used)
    return derive_loan_provision_ratio(merged)


def parse_announcement(
    downloader,
    extractor_module,
    announcement: dict[str, Any],
    *,
    code: str,
    year: int,
    report_dir: Path,
    text_dir: Path,
) -> dict[str, Any] | None:
    pdf = downloader.download_pdf(announcement, report_dir)
    if pdf is None:
        return None
    text_path = text_dir / f"{pdf.stem}.md"
    if not text_path.exists():
        text_dir.mkdir(parents=True, exist_ok=True)
        extractor_module.extract_with_pdftotext(pdf, text_path, None)
    row = parser_module.parse_bank_metrics(
        text_path.read_text(encoding="utf-8", errors="replace"),
        ts_code=code,
        period=f"{year}1231",
        source_file=str(text_path.resolve()),
    )
    row["missing_metrics"] = list(row["missing_metrics"])
    row["announcement_date"] = announcement_date(announcement.get("announcementTime"))
    row["title"] = announcement.get("announcementTitle", "")
    return row


def prepare_one_bank(code: str, years: list[int], *, root: Path) -> list[dict[str, Any]]:
    downloader_module = formal_preparer._load_sibling(f"bt_bank_downloader_{code}", "download_bank_reports.py")
    extractor_module = formal_preparer._load_sibling(f"bt_bank_extractor_{code}", "extract_bank_reports.py")
    stock_code = code.split(".", 1)[0]
    report_dir = root / stock_code
    text_dir = root / f"{stock_code}_text"
    report_dir.mkdir(parents=True, exist_ok=True)
    downloader = downloader_module.CnInfoDownloader(stocks=downloader_module.load_stocks())
    resolved_code, _, market = downloader.find_stock(stock_code)
    if not resolved_code or not market:
        raise RuntimeError(f"银行映射缺失：{code}")
    rows = []
    for year in years:
        parsed = []
        for announcement in ranked_announcements(downloader, resolved_code, year, market):
            row = parse_announcement(
                downloader, extractor_module, announcement,
                code=code, year=year, report_dir=report_dir, text_dir=text_dir,
            )
            if row is None:
                continue
            parsed.append(row)
            if not merge_metric_rows(parsed)["missing_metrics"]:
                break
        if parsed:
            best = merge_metric_rows(parsed)
        else:
            best = {
                "ts_code": code,
                "period": f"{year}1231",
                "data_quality": "data_gap",
                "missing_metrics": ["未找到年报"],
            }
        rows.append(best)
    return rows


def write_history(rows: list[dict[str, Any]], path: Path) -> None:
    fields = [
        "ts_code",
        "period",
        "announcement_date",
        "title",
        *parser_module.METRIC_ALIASES.keys(),
        "data_quality",
        "missing_metrics",
        "source_file",
        "metric_evidence_json",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in sorted(rows, key=lambda item: (item["ts_code"], item["period"])):
            record = dict(row)
            record["missing_metrics"] = json.dumps(record.get("missing_metrics", []), ensure_ascii=False)
            writer.writerow({field: record.get(field, "") for field in fields})


def required_years(candidate_files: list[Path]) -> dict[str, set[int]]:
    """Fiscal years each bank needs: three before every period it passed the preflight."""
    import pandas as pd

    need: dict[str, set[int]] = {}
    for path in candidate_files:
        year = int(path.name[:4])
        frame = pd.read_csv(path, dtype=str)
        banks = frame[(frame["industry"] == "银行") & (frame["market_preflight_passed"] == "是")]
        for code in banks["ts_code"]:
            need.setdefault(str(code), set()).update({year - 3, year - 2, year - 1})
    return need


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--runs", nargs="+", required=True, help="回测运行目录名，用其分期候选表确定所需银行与年度")
    parser.add_argument("--max-workers", type=int, default=3)
    args = parser.parse_args(argv)
    backtest = args.data_root.resolve() / "a_dividend_backtest"
    files = [path for run in args.runs for path in sorted((backtest / "runs" / run / "periods").glob("*-candidates.csv"))]
    need = required_years(files)
    root = backtest / "bank_reports"
    print(f"{len(need)}家银行，共{sum(len(years) for years in need.values())}个银行年度", flush=True)

    def run(item: tuple[str, set[int]]) -> list[dict[str, Any]]:
        code, years = item
        rows = prepare_one_bank(code, sorted(years), root=root)
        print(f"{code}：{len(rows)}份年报，缺口{sum(row['data_quality'] != 'normal' for row in rows)}", flush=True)
        return rows

    with ThreadPoolExecutor(max_workers=max(1, args.max_workers)) as executor:
        batches = list(executor.map(run, sorted(need.items())))
    output = root / HISTORY_FILENAME
    write_history([row for batch in batches for row in batch], output)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
