#!/usr/bin/env python3
"""Historical, point-in-time research for insurance safety gates and scoring factors."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent


def _load_strategy():
    path = SCRIPT_DIR / "run_a_dividend_strategy.py"
    spec = importlib.util.spec_from_file_location("insurance_history_strategy", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _json(value: Any, default: Any) -> Any:
    try:
        parsed = json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default
    return parsed


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def annual_point_in_time_rows(
    insurance_records: list[dict[str, Any]], dps_by_code: dict[str, dict[str, float]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in insurance_records:
        if str(record.get("report_type")) != "annual" or str(record.get("data_quality")) != "normal":
            continue
        code = str(record.get("ts_code") or "")
        year = int(str(record.get("period") or "0")[:4])
        subsidiaries = _json(record.get("material_subsidiaries_json"), [])
        stresses = _json(record.get("stress_scenarios_json"), [])
        core = _float(record.get("group_core_solvency_ratio"))
        comprehensive = _float(record.get("group_comprehensive_solvency_ratio"))
        subsidiary_core = [_float(item.get("core_solvency_ratio")) for item in subsidiaries]
        subsidiary_comprehensive = [_float(item.get("comprehensive_solvency_ratio")) for item in subsidiaries]
        group_stresses = [item for item in stresses if "集团" in str(item.get("reporting_entity") or "")]
        required_complete = (
            core is not None and comprehensive is not None and subsidiaries and stresses
            and all(value is not None for value in subsidiary_core + subsidiary_comprehensive)
        )
        if required_complete:
            stressed_core = min([core, *[core + float(item.get("core_change_pct_points") or 0) for item in group_stresses]])
            stressed_comprehensive = min([comprehensive, *[comprehensive + float(item.get("comprehensive_change_pct_points") or 0) for item in group_stresses]])
            gate_passed = (
                core >= 50 and comprehensive >= 100
                and min(float(value) for value in subsidiary_core) >= 50
                and min(float(value) for value in subsidiary_comprehensive) >= 100
                and stressed_core >= 50 and stressed_comprehensive >= 100
            )
            gate_status = "passed" if gate_passed else "hard_gate_failed"
        else:
            stressed_core = stressed_comprehensive = None
            gate_status = "data_gap"
            gate_passed = False
        current_dps = dps_by_code.get(code, {}).get(str(year))
        future_dps = dps_by_code.get(code, {}).get(str(year + 1))
        outcome_known = current_dps is not None and future_dps is not None and year + 1 < 2026
        rows.append({
            "ts_code": code,
            "as_of_date": record.get("available_date"),
            "fiscal_year": year,
            "gate_status": gate_status,
            "group_core_solvency_ratio": core,
            "group_comprehensive_solvency_ratio": comprehensive,
            "stressed_core_solvency_ratio": stressed_core,
            "stressed_comprehensive_solvency_ratio": stressed_comprehensive,
            "subsidiary_min_core_solvency_ratio": min(subsidiary_core) if subsidiary_core and all(v is not None for v in subsidiary_core) else None,
            "subsidiary_min_comprehensive_solvency_ratio": min(subsidiary_comprehensive) if subsidiary_comprehensive and all(v is not None for v in subsidiary_comprehensive) else None,
            "operating_profit_parent": _float(record.get("operating_profit_parent")),
            "operating_profit_payout_ratio": _float(record.get("operating_profit_payout_ratio")),
            "subsidiary_dividend_coverage": _float(record.get("subsidiary_dividend_coverage")),
            "ending_available_funds_coverage": _float(record.get("ending_available_funds_coverage")),
            "current_regular_dps": current_dps,
            "next_year_regular_dps": future_dps,
            "outcome_known": outcome_known,
            "subsequent_dps_cut": bool(outcome_known and future_dps < current_dps),
            "gate_would_exclude": not gate_passed,
        })
    return rows


RESEARCH_WEIGHTS = {
    "operating_profit_stability": 0.10,
    "operating_profit_change": 0.10,
    "solvency_buffer": 0.10,
    "solvency_trend": 0.05,
    "stressed_solvency_buffer": 0.10,
    "parent_available_funds_coverage": 0.10,
    "subsidiary_dividend_coverage": 0.10,
    "consecutive_dividend_years": 0.10,
    "dps_cagr_5y": 0.15,
    "dividend_yield_cv_5y": 0.10,
}


def build_research_score_rows(
    history: list[dict[str, Any]], candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    candidate_map = {row.get("ts_code"): row for row in candidates}
    by_code: dict[str, list[dict[str, Any]]] = {}
    for row in history:
        by_code.setdefault(str(row["ts_code"]), []).append(row)
    rows: list[dict[str, Any]] = []
    for code, observations in sorted(by_code.items()):
        observations.sort(key=lambda row: int(row["fiscal_year"]))
        latest = observations[-1]
        prior = observations[-2] if len(observations) >= 2 else None
        profits = [float(row["operating_profit_parent"]) for row in observations if row.get("operating_profit_parent")]
        profit_mean = sum(profits) / len(profits) if profits else None
        profit_cv = (
            (sum((value - profit_mean) ** 2 for value in profits) / len(profits)) ** 0.5 / profit_mean
            if profit_mean and len(profits) >= 2 else None
        )
        current_profit = _float(latest.get("operating_profit_parent"))
        prior_profit = _float(prior.get("operating_profit_parent")) if prior else None
        core = _float(latest.get("group_core_solvency_ratio"))
        comprehensive = _float(latest.get("group_comprehensive_solvency_ratio"))
        prior_core = _float(prior.get("group_core_solvency_ratio")) if prior else None
        prior_comprehensive = _float(prior.get("group_comprehensive_solvency_ratio")) if prior else None
        stressed_core = _float(latest.get("stressed_core_solvency_ratio"))
        stressed_comprehensive = _float(latest.get("stressed_comprehensive_solvency_ratio"))
        candidate = candidate_map.get(code, {})
        rows.append({
            "ts_code": code,
            "latest_as_of_date": latest.get("as_of_date"),
            "operating_profit_stability": None if profit_cv is None else -profit_cv,
            "operating_profit_change": (
                current_profit / prior_profit - 1 if current_profit is not None and prior_profit else None
            ),
            "solvency_buffer": min(core - 50, comprehensive - 100) if core is not None and comprehensive is not None else None,
            "solvency_trend": min(core - prior_core, comprehensive - prior_comprehensive) if None not in {core, comprehensive, prior_core, prior_comprehensive} else None,
            "stressed_solvency_buffer": min(stressed_core - 50, stressed_comprehensive - 100) if stressed_core is not None and stressed_comprehensive is not None else None,
            "parent_available_funds_coverage": _float(latest.get("ending_available_funds_coverage")),
            "subsidiary_dividend_coverage": _float(latest.get("subsidiary_dividend_coverage")),
            "consecutive_dividend_years": _float(candidate.get("consecutive_dividend_years")),
            "dps_cagr_5y": _float(candidate.get("dps_cagr_5y")),
            "dividend_yield_cv_5y": _float(candidate.get("dividend_yield_cv_5y")),
            "research_score": None,
        })
    if len(rows) < 5:
        return rows
    for factor, weight in RESEARCH_WEIGHTS.items():
        available = sorted(float(row[factor]) for row in rows if row.get(factor) is not None)
        if len(available) != len(rows):
            return rows
        for row in rows:
            value = float(row[factor])
            rank = sum(item < value for item in available) + 0.5 * sum(item == value for item in available)
            percentile = rank / len(available)
            if factor == "dividend_yield_cv_5y":
                percentile = 1 - percentile
            row["research_score"] = (row.get("research_score") or 0) + percentile * weight * 100
    return rows


def run_research(*, candidates_path: Path, insurance_metrics_path: Path,
                 structured_factors_path: Path, output_dir: Path) -> dict[str, Any]:
    strategy = _load_strategy()
    candidates = _read_csv(candidates_path)
    metrics = _read_csv(insurance_metrics_path)
    structured = _read_csv(structured_factors_path)
    dps_by_code = {
        row["ts_code"]: {str(year): float(value) for year, value in _json(row.get("annual_comparable_dps_json"), {}).items()}
        for row in structured if row.get("ts_code")
    }
    history = annual_point_in_time_rows(metrics, dps_by_code)
    output_dir.mkdir(parents=True, exist_ok=True)
    history_path = output_dir / "insurance-history-observations.csv"
    if history:
        with history_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(history[0]))
            writer.writeheader()
            writer.writerows(history)
    research_scores = build_research_score_rows(history, candidates)
    score_path = output_dir / "insurance-research-score-simulation.csv"
    if research_scores:
        with score_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(research_scores[0]))
            writer.writeheader()
            writer.writerows(research_scores)

    insurance_codes = sorted({row.get("ts_code", "") for row in metrics})
    current_profiles = {
        code: strategy.insurance_quality_profile([row for row in metrics if row.get("ts_code") == code])
        for code in insurance_codes
    }
    qualified = [row for row in candidates if row.get("selected") in {"是", "True", "true", "1"}]
    selected_before = sorted(
        qualified,
        key=lambda row: int(float(row.get("rank") or 9999)),
    )[:10]
    eligible = [
        row for row in qualified
        if not (row.get("ts_code") in current_profiles and not current_profiles[row["ts_code"]].get("insurance_quality_gate_passed"))
    ]
    selected_gate_only = sorted(eligible, key=lambda row: (-float(row["dividend_score_total"]), row["ts_code"]))[:10]
    outcomes = [row for row in history if row["outcome_known"]]
    cuts = [row for row in outcomes if row["subsequent_dps_cut"]]
    false_kills = [row for row in outcomes if row["gate_would_exclude"] and not row["subsequent_dps_cut"]]
    insurer_count = len(set(row["ts_code"] for row in history))
    enough_evidence = insurer_count >= 5 and len(outcomes) >= 10 and len(cuts) >= 2
    recommendation = (
        "可进入独立保险评分权重的下一轮样本外验证"
        if enough_evidence else "历史发行人和DPS削减事件不足，不修改正式保险评分权重"
    )
    summary = {
        "insurance_issuers": insurer_count,
        "historical_point_in_time_rows": len(history),
        "known_next_year_dps_outcomes": len(outcomes),
        "subsequent_dps_cuts": len(cuts),
        "stable_dividend_false_kills": len(false_kills),
        "top10_before": [row["ts_code"] for row in selected_before],
        "top10_gate_only": [row["ts_code"] for row in selected_gate_only],
        "independent_scoring_activated": enough_evidence,
        "research_weights": RESEARCH_WEIGHTS,
        "recommendation": recommendation,
    }
    (output_dir / "insurance-scoring-validation.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# 保险专项历史评分验证", "",
        "所有历史行按原报告可用日期形成；下一年度DPS只用于事后标签，不进入当时的门槛或因子。", "",
        f"- 发行人：{insurer_count}",
        f"- 历史时点：{len(history)}",
        f"- 已知下一年度DPS结果：{len(outcomes)}（削减 {len(cuts)}）",
        f"- 稳定分红误杀：{len(false_kills)}",
        f"- 只加安全门槛的Top10变化：{summary['top10_before']} → {summary['top10_gate_only']}",
        f"- 独立保险评分：{'启用研究建议' if enough_evidence else '不启用'}",
        f"- 建议：{recommendation}", "",
        "候选因子已在历史观察表保留：营运利润及派息率、集团及压力后偿付能力、主要子公司偿付能力、母公司资金覆盖和子公司上缴覆盖。",
    ]
    (output_dir / "insurance-scoring-validation.md").write_text("\n".join(lines), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--insurance-metrics", required=True, type=Path)
    parser.add_argument("--structured-factors", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run_research(
        candidates_path=args.candidates,
        insurance_metrics_path=args.insurance_metrics,
        structured_factors_path=args.structured_factors,
        output_dir=args.output_dir,
    ), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
