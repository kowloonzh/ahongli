#!/usr/bin/env python3
"""Prepare the audited three-year bank-metric cache for a strategy run."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent


def _load_sibling(module_name: str, filename: str):
    path = SCRIPT_DIR / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


parser_module = _load_sibling("bank_metric_parser", "parse_bank_metrics.py")
REQUIRED_METRICS = parser_module.REQUIRED_METRICS


def select_latest_three_complete_annual_rows(
    records: list[dict[str, Any]],
    required_codes: set[str],
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    errors: list[str] = []
    for code in sorted(required_codes):
        by_period: dict[str, dict[str, Any]] = {}
        for record in records:
            if str(record.get("ts_code", "")) != code:
                continue
            period = str(record.get("period", ""))[:8]
            if period.endswith("1231"):
                by_period[period] = record
        latest = [by_period[period] for period in sorted(by_period)[-3:]]
        if len(latest) != 3:
            errors.append(f"{code}:缺少最近3个完整年度")
            continue
        for record in latest:
            missing = [field for field in REQUIRED_METRICS if record.get(field) in {None, ""}]
            if str(record.get("data_quality", "")) != "normal" or missing:
                errors.append(
                    f"{code}:{record.get('period')}缺少"
                    + "、".join(missing or ["可验证解析结果"])
                )
        selected.extend(latest)
    if errors:
        raise RuntimeError("银行专项指标准备失败：" + "；".join(errors))
    return selected


def _default_cache_root(output_path: Path) -> Path:
    parents = output_path.resolve().parents
    output_root = parents[2] if len(parents) > 2 else output_path.resolve().parent
    return output_root / "_bank_report_cache"


def _prepare_one_bank(
    code: str,
    *,
    as_of_date: str,
    cache_root: Path,
) -> list[dict[str, Any]]:
    downloader_module = _load_sibling(
        f"bank_report_downloader_{code}", "download_bank_reports.py"
    )
    extractor_module = _load_sibling(
        f"bank_report_extractor_{code}", "extract_bank_reports.py"
    )
    stock_code = code.split(".", 1)[0]
    stocks = downloader_module.load_stocks()
    downloader = downloader_module.CnInfoDownloader(stocks=stocks)
    resolved_code, stock_info, market = downloader.find_stock(stock_code)
    if not resolved_code or not stock_info or not market:
        raise RuntimeError(f"银行映射缺失：{code}")

    report_dir = cache_root / stock_code
    text_dir = cache_root / f"{stock_code}_text"
    report_dir.mkdir(parents=True, exist_ok=True)
    cutoff = datetime.strptime(as_of_date, "%Y%m%d").date()
    reports, metadata = downloader.download_as_of_reports(
        resolved_code,
        cutoff,
        report_dir,
        market,
        include_periodic=False,
    )
    manifest = downloader_module.build_manifest(
        resolved_code,
        str(stock_info.get("zwjc") or stock_code),
        market,
        report_dir,
        reports,
        selection_metadata=metadata,
    )
    manifest_path = report_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    extracted = extractor_module.extract_manifest(
        manifest_path,
        text_dir,
        backend="pdftotext",
        write_chunks=True,
    )
    return [
        parser_module.parse_file(Path(report["text_path_absolute"]))
        for report in extracted.get("reports", [])
        if report.get("report_type") == "annual" and report.get("text_path_absolute")
    ]


def prepare_bank_metrics_cache(
    *,
    output_path: Path,
    bank_codes: set[str],
    as_of_date: str,
    cache_root: Path | None = None,
    max_workers: int = 4,
) -> Path:
    output_path = Path(output_path).resolve()
    cache = Path(cache_root).resolve() if cache_root else _default_cache_root(output_path)
    cache.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(bank_codes)))) as executor:
        batches = list(
            executor.map(
                lambda code: _prepare_one_bank(
                    code,
                    as_of_date=as_of_date,
                    cache_root=cache,
                ),
                sorted(bank_codes),
            )
        )
    records = select_latest_three_complete_annual_rows(
        [record for batch in batches for record in batch],
        bank_codes,
    )
    parser_module.write_csv(records, output_path)
    return output_path


def main() -> int:
    arg_parser = argparse.ArgumentParser(description=__doc__)
    arg_parser.add_argument("--output", required=True, type=Path)
    arg_parser.add_argument("--as-of-date", required=True)
    arg_parser.add_argument("--bank-codes", required=True, nargs="+")
    arg_parser.add_argument("--cache-root", type=Path)
    arg_parser.add_argument("--max-workers", type=int, default=4)
    args = arg_parser.parse_args()
    path = prepare_bank_metrics_cache(
        output_path=args.output,
        bank_codes=set(args.bank_codes),
        as_of_date=args.as_of_date,
        cache_root=args.cache_root,
        max_workers=args.max_workers,
    )
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
