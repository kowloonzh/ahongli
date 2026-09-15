#!/usr/bin/env python3
"""Prepare audited insurance dividend-safety metrics for a strategy run."""

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
SKILL_DIR = SCRIPT_DIR.parent


def _load_sibling(module_name: str, filename: str):
    path = SCRIPT_DIR / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


parser_module = _load_sibling("insurance_metric_parser", "parse_insurance_metrics.py")


def _merged_stock_map() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for filename in ["stocks.json", "forward_stocks.json"]:
        path = SKILL_DIR / "assets" / filename
        if not path.exists():
            continue
        for market, stocks in json.loads(path.read_text(encoding="utf-8")).items():
            result.setdefault(market, {}).update(stocks)
    return result


def _default_cache_root(output_path: Path) -> Path:
    absolute = output_path.absolute()
    output_root = next(
        (parent for parent in absolute.parents if parent.name == "a_dividend_outputs"),
        None,
    )
    return (output_root.parent if output_root is not None else absolute.parent) / "_insurance_report_cache"


def select_required_rows(
    records: list[dict[str, Any]], required_codes: set[str], as_of_date: str,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    errors: list[str] = []
    for code in sorted(required_codes):
        company = [record for record in records if str(record.get("ts_code")) == code]
        eligible = [
            record for record in company
            if str(record.get("available_date") or "")
            and str(record.get("available_date")) <= as_of_date
        ]
        annual = sorted(
            [record for record in eligible if str(record.get("report_type")) == "annual"],
            key=lambda record: str(record.get("period")),
        )[-3:]
        if len(annual) != 3:
            errors.append(f"{code}:缺少最近3个完整年度")
            continue
        for record in annual:
            if str(record.get("data_quality")) != "normal":
                errors.append(
                    f"{code}:{record.get('period')}缺少"
                    + "、".join(record.get("missing_metrics") or ["可验证保险专项指标"])
                )
        periodic = sorted(
            [
                record for record in eligible
                if str(record.get("report_type")) != "annual"
                and str(record.get("available_date") or "") <= as_of_date
            ],
            key=lambda record: str(record.get("period")),
        )
        if periodic:
            latest = periodic[-1]
            if str(latest.get("data_quality")) != "normal":
                errors.append(
                    f"{code}:{latest.get('period')}缺少"
                    + "、".join(latest.get("missing_metrics") or ["最新期间保险专项指标"])
                )
            selected.extend([*annual, latest])
        else:
            selected.extend(annual)
    if errors:
        raise RuntimeError("保险专项指标准备失败：" + "；".join(errors))
    return selected


def _prepare_one_insurer(
    code: str, *, as_of_date: str, cache_root: Path,
) -> list[dict[str, Any]]:
    downloader_module = _load_sibling(
        f"insurance_report_downloader_{code}", "download_bank_reports.py"
    )
    extractor_module = _load_sibling(
        f"insurance_report_extractor_{code}", "extract_bank_reports.py"
    )
    stock_code = code.split(".", 1)[0]
    downloader = downloader_module.CnInfoDownloader(stocks=_merged_stock_map())
    resolved_code, stock_info, market = downloader.find_stock(stock_code)
    if not resolved_code or not stock_info or not market:
        raise RuntimeError(f"保险公司CNinfo映射缺失：{code}")

    report_dir = cache_root / stock_code
    text_dir = cache_root / f"{stock_code}_text"
    report_dir.mkdir(parents=True, exist_ok=True)
    cutoff = datetime.strptime(as_of_date, "%Y%m%d").date()
    reports, metadata = downloader.download_as_of_reports(
        resolved_code, cutoff, report_dir, market, include_periodic=True,
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
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    extracted = extractor_module.extract_manifest(
        manifest_path, text_dir, backend="pdftotext", write_chunks=True,
    )
    records: list[dict[str, Any]] = []
    period_ends = {"q1": "0331", "semi": "0630", "q3": "0930", "annual": "1231"}
    for report in extracted.get("reports", []):
        text_path = report.get("text_path_absolute")
        report_type = str(report.get("report_type") or "")
        if not text_path or report_type not in period_ends:
            continue
        announcement_date = downloader_module.parse_announcement_date(
            report.get("announcement_time")
        )
        records.append(parser_module.parse_insurance_metrics(
            Path(text_path).read_text(encoding="utf-8", errors="replace"),
            ts_code=code,
            period=f"{int(report['fiscal_year'])}{period_ends[report_type]}",
            report_type=report_type,
            source_file=str(Path(text_path).resolve()),
            source_original_file=str((report_dir / str(report.get("path") or "")).resolve()),
            source_document_id=str(report.get("announcement_id") or ""),
            available_date=announcement_date.strftime("%Y%m%d") if announcement_date else "",
        ))
    return records


def prepare_insurance_metrics_cache(
    *, output_path: Path, insurance_codes: set[str], as_of_date: str,
    cache_root: Path | None = None, max_workers: int = 2,
) -> Path:
    output_path = Path(output_path).absolute()
    cache = Path(cache_root).absolute() if cache_root else _default_cache_root(output_path)
    cache.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(insurance_codes)))) as executor:
        batches = list(executor.map(
            lambda code: _prepare_one_insurer(code, as_of_date=as_of_date, cache_root=cache),
            sorted(insurance_codes),
        ))
    records = select_required_rows(
        [record for batch in batches for record in batch], insurance_codes, as_of_date,
    )
    parser_module.write_csv(records, output_path)
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--insurance-codes", required=True, nargs="+")
    parser.add_argument("--cache-root", type=Path)
    parser.add_argument("--max-workers", type=int, default=2)
    args = parser.parse_args()
    print(prepare_insurance_metrics_cache(
        output_path=args.output,
        insurance_codes=set(args.insurance_codes),
        as_of_date=args.as_of_date,
        cache_root=args.cache_root,
        max_workers=args.max_workers,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
