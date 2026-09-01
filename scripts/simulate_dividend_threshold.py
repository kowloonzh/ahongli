#!/usr/bin/env python3
"""Simulate an alternative five-year yield threshold without touching formal output."""

from __future__ import annotations

import argparse
import importlib.util
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import pandas as pd


SCRIPT_DIR = Path(__file__).resolve().parent


def _load_runner():
    path = SCRIPT_DIR / "run_a_dividend_strategy.py"
    spec = importlib.util.spec_from_file_location("dividend_strategy_runner", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = _load_runner()


def threshold_slug(threshold: float) -> str:
    return f"{threshold:g}".replace(".", "_") + "pct"


def simulation_output_dir(run_date: str, threshold: float) -> Path:
    return runner.output_dir(run_date).with_name(
        f"{runner.compact_date(run_date)}_sim_yield_{threshold_slug(threshold)}"
    )


def yield_threshold_profile(frame: pd.DataFrame, threshold: float) -> dict[str, Any]:
    observations = frame.drop_duplicates("trade_date", keep="last") if "trade_date" in frame.columns else frame
    field = "dv_ttm" if "dv_ttm" in observations.columns else "dv_ratio"
    series = pd.to_numeric(observations.get(field), errors="coerce").dropna()
    observation_days = len(observations)
    valid_days = len(series)
    return {
        "observation_days": observation_days,
        "valid_days": valid_days,
        "coverage": valid_days / observation_days if observation_days else 0.0,
        "threshold_ratio": float((series >= threshold).mean()) if valid_days else 0.0,
    }


def _save_structured_records(
    records: dict[str, dict[str, Any]],
    expected_codes: list[str],
    path: Path,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([records[code] for code in expected_codes if code in records]).to_csv(
        path,
        index=False,
        encoding="utf-8-sig",
    )


def ensure_all_structured_factors(
    run_date: str,
    market_dir: Path,
    *,
    cache_path: Path,
    skip_fetch: bool,
    max_workers: int,
    sleep_seconds: float,
) -> dict[str, dict[str, Any]]:
    constituents = pd.read_csv(market_dir / "constituents.csv", dtype=str).fillna("")
    expected_codes = constituents["ts_code"].astype(str).tolist()
    cached = runner.load_structured_factor_data(cache_path)
    if set(cached) == set(expected_codes):
        return cached

    base = runner.ensure_structured_factor_data(
        run_date,
        market_dir,
        skip_fetch=skip_fetch,
        sleep_seconds=sleep_seconds,
        max_workers=max_workers,
    )
    records = dict(cached)
    for code, record in base.items():
        if str(record.get("structured_data_quality", "")) == "normal":
            records.setdefault(code, dict(record))
    remaining = [code for code in expected_codes if code not in records]
    if not remaining:
        return records
    if skip_fetch:
        raise RuntimeError(
            f"全量结构化模拟缓存缺少{len(remaining)}家公司；请取消--skip-fetch补齐"
        )

    stability = pd.Series(
        {
            code: runner.finite_float(record.get("roe_stability_12q"))
            for code, record in base.items()
        }
    ).dropna()
    cutoff = float(stability.quantile(0.80)) if not stability.empty else None
    by_code = constituents.set_index("ts_code")
    client = runner.tushare_pro()

    def fetch(code: str) -> dict[str, Any]:
        item = by_code.loc[code]
        frames = runner.fetch_structured_source_frames(
            client,
            code,
            run_date,
            include_candidate_details=True,
        )
        if sleep_seconds:
            time.sleep(sleep_seconds)
        record = runner.structured_factor_record_from_frames(
            ts_code=code,
            industry=str(item["industry"]),
            main_business=str(item["main_business"]),
            business_scope=str(item["business_scope"]),
            introduction=str(item["introduction"]),
            run_date=run_date,
            dividends=frames["dividends"],
            income=frames["income"],
            fina_indicator=frames["fina_indicator"],
            cashflow=frames["cashflow"],
            balance=frames["balance"],
            audit=frames["audit"],
            market_preflight_passed=True,
            roe_stability_cutoff=cutoff,
        )
        record["roe_stability_percentile"] = base.get(code, {}).get("roe_stability_percentile")
        record["company_profile_source"] = str(item.get("company_profile_source", ""))
        record["company_profile_fetch_date"] = str(item.get("company_profile_fetch_date", ""))
        return record

    failures: list[str] = []
    for start in range(0, len(remaining), 20):
        batch = remaining[start : start + 20]
        with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
            futures = {executor.submit(fetch, code): code for code in batch}
            for future in as_completed(futures):
                code = futures[future]
                try:
                    records[code] = future.result()
                except Exception as exc:
                    failures.append(f"{code}:{exc}")
        _save_structured_records(records, expected_codes, cache_path)
    if failures:
        raise RuntimeError("全量结构化模拟取数失败：" + "；".join(failures))
    if set(records) != set(expected_codes):
        raise RuntimeError("全量结构化模拟缓存未覆盖完整沪深300")
    return records


def run_simulation(
    run_date: str,
    threshold: float,
    *,
    skip_fetch: bool = False,
    refresh_constituents: bool = False,
    max_workers: int = 4,
    sleep_seconds: float = 0.2,
    all_structured_cache: Path | None = None,
    bank_metrics_cache: Path | None = None,
) -> Path:
    if threshold <= 0:
        raise ValueError("yield threshold must be positive")
    run_date = runner.compact_date(run_date)
    formal_dir = runner.output_dir(run_date)
    out = simulation_output_dir(run_date, threshold)
    out.mkdir(parents=True, exist_ok=True)
    market_dir = runner.ensure_market_data(
        run_date,
        skip_fetch=skip_fetch,
        refresh_constituents=refresh_constituents,
        fixed_constituents_file=None,
        sleep_seconds=sleep_seconds,
        max_workers=max_workers,
    )
    structured_cache = all_structured_cache or (
        formal_dir / "market_data" / "structured_factors_all.csv"
    )
    structured = ensure_all_structured_factors(
        run_date,
        market_dir,
        cache_path=Path(structured_cache),
        skip_fetch=skip_fetch,
        max_workers=max_workers,
        sleep_seconds=sleep_seconds,
    )
    constituents = pd.read_csv(market_dir / "constituents.csv", dtype=str).fillna("")
    profiles: dict[str, dict[str, Any]] = {}
    passed: dict[str, bool] = {}
    for code in constituents["ts_code"].astype(str):
        profile = yield_threshold_profile(
            runner.read_market_csv(market_dir, "daily_basic", code),
            threshold,
        )
        profiles[code] = profile
        passed[code] = bool(
            profile["valid_days"] >= runner.MIN_DIVIDEND_YIELD_VALID_DAYS
            and profile["coverage"] >= runner.MIN_DIVIDEND_YIELD_DATA_COVERAGE
            and profile["threshold_ratio"] >= 0.80
        )
        structured[code]["market_preflight_passed"] = passed[code]

    bank_codes = set(
        constituents.loc[
            constituents["industry"].str.contains("银行", na=False)
            & constituents["ts_code"].map(passed),
            "ts_code",
        ]
    )
    bank_path = Path(bank_metrics_cache) if bank_metrics_cache else out / "market_data" / runner.BANK_METRICS_FILENAME
    if bank_codes:
        bank_profiles = runner.ensure_bank_quality_profiles(
            bank_path,
            bank_codes,
            run_date,
        )
        for code, profile in bank_profiles.items():
            structured[code] = runner.merge_bank_quality_gate(structured[code], profile)

    def evaluate(row: dict[str, Any]) -> str | None:
        code = str(row.get("ts_code", ""))
        profile = profiles.get(code, {})
        if runner.finite_float(row.get("current_price")) is None:
            return "当前价未取得"
        if runner.finite_float(row.get("current_dividend_yield")) is None:
            return "当前股息率未取得"
        if not passed.get(code):
            return (
                f"近五年股息率>={threshold:g}%的交易日占比为"
                f"{profile.get('threshold_ratio', 0):.1%}，低于80%"
            )
        return None

    runner.evaluate_market_dividend_preconditions = evaluate
    rows = runner.build_candidate_rows(
        market_dir,
        structured_factors=structured,
    )
    ratio_field = f"dividend_yield_ge_{str(threshold).replace('.', '_')}_ratio"
    for row in rows:
        row[ratio_field] = round(profiles[row["ts_code"]]["threshold_ratio"], 4)
    top = runner.formal_top_candidates(rows)
    fields = [*runner.CSV_FIELDS, ratio_field]

    def ready(row: dict[str, Any]) -> dict[str, str]:
        result = runner.csv_ready(row)
        result[ratio_field] = f"{row[ratio_field]:.4f}"
        return result

    pd.DataFrame([ready(row) for row in rows], columns=fields).to_csv(
        out / f"hs300-dividend-candidates-yield-{threshold_slug(threshold)}-{run_date}.csv",
        index=False,
        encoding="utf-8-sig",
    )
    pd.DataFrame([ready(row) for row in top], columns=fields).to_csv(
        out / f"hs300-dividend-top10-yield-{threshold_slug(threshold)}-{run_date}.csv",
        index=False,
        encoding="utf-8-sig",
    )
    pd.DataFrame(
        [
            {
                "universe": len(rows),
                "market_passers": sum(passed.values()),
                "selected": sum(row.get("selected") == "是" for row in rows),
                "bank_count": len(bank_codes),
                "yield_threshold": threshold,
            }
        ]
    ).to_csv(out / "simulation_stage_counts.csv", index=False, encoding="utf-8-sig")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-date", required=True)
    parser.add_argument("--yield-threshold", required=True, type=float)
    parser.add_argument("--skip-fetch", action="store_true")
    parser.add_argument("--refresh-constituents", action="store_true")
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--sleep-seconds", type=float, default=0.2)
    parser.add_argument("--all-structured-cache", type=Path)
    parser.add_argument("--bank-metrics-cache", type=Path)
    args = parser.parse_args()
    output = run_simulation(
        args.run_date,
        args.yield_threshold,
        skip_fetch=args.skip_fetch,
        refresh_constituents=args.refresh_constituents,
        max_workers=args.max_workers,
        sleep_seconds=args.sleep_seconds,
        all_structured_cache=args.all_structured_cache,
        bank_metrics_cache=args.bank_metrics_cache,
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
