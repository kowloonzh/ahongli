#!/usr/bin/env python3
"""Compare archived forward-DPS forecasts with subsequently disclosed actual DPS."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


def evaluate_accuracy(*, forecasts: list[dict[str, Any]], actuals: list[dict[str, Any]]) -> dict[str, Any]:
    actual_by_key = {(str(row["ts_code"]), str(row["fiscal_year"])): row for row in actuals}
    rows = []
    for forecast in forecasts:
        key = (str(forecast["ts_code"]), str(forecast["forecast_fiscal_year"]))
        actual_row = actual_by_key.get(key)
        if not actual_row:
            continue
        low = float(forecast["forecast_fy_regular_dps_low"])
        base = float(forecast["forecast_fy_regular_dps_base"])
        high = float(forecast["forecast_fy_regular_dps_high"])
        actual = float(actual_row["actual_regular_dps"])
        error = base - actual
        rows.append({
            "ts_code": key[0], "name": forecast.get("name", ""), "fiscal_year": key[1],
            "forecast_dps_low": low, "forecast_dps_base": base, "forecast_dps_high": high,
            "actual_regular_dps": actual, "absolute_error": abs(error),
            "signed_error": error, "relative_error": error / actual if actual else None,
            "within_forecast_range": low <= actual <= high,
            "model_id": forecast.get("model_id", ""), "model_version": forecast.get("model_version", ""),
        })
    return {
        "rows": rows,
        "summary": {
            "matched_count": len(rows),
            "within_range_count": sum(bool(row["within_forecast_range"]) for row in rows),
            "mean_absolute_relative_error": (
                sum(abs(float(row["relative_error"])) for row in rows if row["relative_error"] is not None) / len(rows)
                if rows else None
            ),
        },
    }


def _read_csv(path: Path) -> list[dict[str, str]]:
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate AHongli forward-DPS forecast accuracy.")
    parser.add_argument("--forecasts", required=True, type=Path)
    parser.add_argument("--actuals", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(evaluate_accuracy(forecasts=_read_csv(args.forecasts), actuals=_read_csv(args.actuals)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
