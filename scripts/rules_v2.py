"""Scoring rule v2 (docs/scoring-v2-proposal.md), evaluated in the backtest.

v2 keeps the v1 hard gates and adds:

- G7: nonfinancial debt service. Latest annual EBIT / interest expense at or
  above ``interest_coverage_min``; net debt at or below zero passes outright.
- Optional cross-cycle gate: latest annual deducted profit at most
  ``cycle_gate_max_ratio`` times its 10-year median.

Score = record block (cross-group) and quality block (within group):

- record block: consecutive dividend years (capped), 10-year DPS cut count,
  5-year DPS log-linear growth, ranked across every gate passer;
- quality block: the v1 within-group percentile machinery with the v2
  factor sets (no dividend-yield CV, no OPCFD);
- optional cross-cycle factor: lower latest-profit / 10-year-median ranks
  higher, blended with weight ``cycle_factor_weight``.

The formal screen still runs rule v1; nothing here changes formal outputs.
"""

from __future__ import annotations

import json
import math
from typing import Any

import pandas as pd

import run_a_dividend_strategy as strategy

RULE_VERSION = "v2"
DPS_CUT_RATIO = 0.95
HISTORY_YEARS = 10
MIN_PROFIT_HISTORY_YEARS = 8
CONSECUTIVE_YEARS_CAP = 15
DEFAULT_PARAMS: dict[str, float | None] = {
    "record_block_weight": 0.5,
    "interest_coverage_min": 3.0,
    "cycle_gate_max_ratio": None,
    # 1: the cycle gate applies to nonfinancial companies only. Bank profit is
    # smoothed by provisioning, and a fast-growing bank's latest profit sits
    # far above its 10-year median without any cycle peak.
    "cycle_gate_nonfinancial_only": 0.0,
    "cycle_factor_weight": 0.0,
    # Threshold of the v1 deducted-profit trend gate, for sensitivity runs.
    "profit_dedt_min_change": -0.10,
}
RECORD_FACTOR_WEIGHTS = {
    "consecutive_dividend_years_capped": 1 / 3,
    "dps_cut_count_10y": 1 / 3,
    "dps_cagr_5y": 1 / 3,
}
RECORD_LOWER_IS_BETTER = {"dps_cut_count_10y"}
QUALITY_FACTOR_WEIGHTS = {
    "nonfinancial": {"roe": 0.40, "droe": 0.20, "fcf_dividend_coverage_3y": 0.40},
    "bank": {
        "roe": 0.20,
        "droe": 0.10,
        "bank_npl_quality": 0.20,
        "bank_provision_quality": 0.15,
        "bank_capital_resilience": 0.15,
        "bank_nim_quality": 0.10,
        "bank_cost_income_ratio": 0.10,
    },
    "financial": {"roe": 0.65, "droe": 0.35},
}
EXTRA_CSV_FIELDS = [
    "rule_version",
    "dps_cut_count_10y",
    "dps_cut_years_10y",
    "consecutive_dividend_years_capped",
    "profit_dedt_10y_observations",
    "profit_dedt_10y_median",
    "profit_dedt_to_10y_median",
    "profit_dedt_10y_max_drawdown",
    "ebit_to_interest_latest",
    "netdebt_latest",
    "ebit_to_interest_source",
    "interest_coverage_gate_status",
    "cycle_gate_status",
    "record_score",
    "quality_score",
    "cycle_percentile",
]


def resolve_params(overrides: dict[str, Any] | None = None) -> dict[str, float | None]:
    params = dict(DEFAULT_PARAMS)
    for key, value in (overrides or {}).items():
        if key not in params:
            raise ValueError(f"unknown v2 parameter: {key}")
        params[key] = None if value in (None, "", "none", "None") else float(value)
    return params


def quality_weights(bank_mode: str) -> dict[str, dict[str, float]]:
    weights = {group: dict(values) for group, values in QUALITY_FACTOR_WEIGHTS.items()}
    if bank_mode == "structured_only":
        common = {factor: weight for factor, weight in weights["bank"].items() if not factor.startswith("bank_")}
        total = sum(common.values())
        weights["bank"] = {factor: weight / total for factor, weight in common.items()}
    return weights


# ---------------------------------------------------------------------------
# Factors
# ---------------------------------------------------------------------------


def _int_keyed(payload: Any) -> dict[int, float]:
    if isinstance(payload, str):
        payload = json.loads(payload) if payload else {}
    return {int(year): float(value) for year, value in (payload or {}).items()}


def dps_cut_profile(annual_dps: dict[int, float], anchor: int, years: int = HISTORY_YEARS) -> dict[str, Any]:
    """Count years in the last ``years`` whose comparable DPS fell below 95% of the prior year.

    A year counts only when the prior year paid, so a company's first payment
    is never a cut; a paying year followed by no payment is.
    """
    cuts = []
    for year in range(anchor - years + 1, anchor + 1):
        prior = annual_dps.get(year - 1, 0.0)
        if prior > 0 and annual_dps.get(year, 0.0) < DPS_CUT_RATIO * prior:
            cuts.append(year)
    return {"dps_cut_count_10y": len(cuts), "dps_cut_years_10y": ",".join(map(str, cuts))}


def profit_cycle_profile(annual_profit: dict[int, float], anchor: int, years: int = HISTORY_YEARS) -> dict[str, Any]:
    """Latest deducted profit against its own 10-year history."""
    window = [annual_profit[year] for year in range(anchor - years + 1, anchor + 1) if year in annual_profit]
    result: dict[str, Any] = {
        "profit_dedt_10y_observations": len(window),
        "profit_dedt_10y_median": None,
        "profit_dedt_to_10y_median": None,
        "profit_dedt_10y_max_drawdown": None,
    }
    latest = annual_profit.get(anchor)
    if len(window) < MIN_PROFIT_HISTORY_YEARS or latest is None:
        return result
    median = float(pd.Series(window).median())
    peak = None
    drawdown = 0.0
    for value in window:
        if peak is not None and peak > 0:
            drawdown = min(drawdown, (value - peak) / peak)
        peak = value if peak is None else max(peak, value)
    result["profit_dedt_10y_median"] = median
    result["profit_dedt_10y_max_drawdown"] = drawdown
    if median > 0:
        result["profit_dedt_to_10y_median"] = latest / median
    elif latest > 0:
        result["profit_dedt_to_10y_median"] = math.inf
    return result


INCOME_INTEREST_FIELDS = "ts_code,ann_date,f_ann_date,end_date,report_type,ebit,fin_exp_int_exp,total_profit,update_flag"


def _annual_row(frame: pd.DataFrame | None, anchor: int) -> pd.Series | None:
    if frame is None or frame.empty or "end_date" not in frame.columns:
        return None
    end_dates = frame["end_date"].astype(str).str.replace(r"\D", "", regex=True).str[:8]
    rows = frame[end_dates.eq(f"{anchor}1231")]
    return None if rows.empty else rows.iloc[-1]


def interest_coverage_profile(
    fina_indicator: pd.DataFrame | None,
    anchor: int,
    income_interest: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """EBIT / interest and net debt from the anchor-year annual report.

    Tushare leaves ``ebit_to_interest`` blank for some companies that do have
    interest expense, so a blank ratio falls back to the income statement:
    EBIT (or total profit + interest expense) / interest expense.
    """
    result: dict[str, Any] = {"ebit_to_interest_latest": None, "netdebt_latest": None, "ebit_to_interest_source": ""}
    row = _annual_row(fina_indicator, anchor)
    if row is not None:
        result["netdebt_latest"] = strategy.finite_float(row.get("netdebt"))
        coverage = strategy.finite_float(row.get("ebit_to_interest"))
        if coverage is not None:
            result["ebit_to_interest_latest"] = coverage
            result["ebit_to_interest_source"] = "fina_indicator"
            return result
    income = _annual_row(income_interest, anchor)
    if income is None:
        return result
    interest = strategy.finite_float(income.get("fin_exp_int_exp"))
    ebit = strategy.finite_float(income.get("ebit"))
    if ebit is None:
        profit = strategy.finite_float(income.get("total_profit"))
        ebit = profit + interest if profit is not None and interest is not None else None
    if interest is not None and interest <= 0:
        result["ebit_to_interest_source"] = "income:no_interest_expense"
    elif interest is not None and ebit is not None:
        result["ebit_to_interest_latest"] = ebit / interest
        result["ebit_to_interest_source"] = "income"
    return result


def v2_factor_fields(
    record: dict[str, Any],
    fina_indicator: pd.DataFrame | None,
    income_interest: pd.DataFrame | None = None,
) -> dict[str, Any]:
    anchor = strategy.finite_float(record.get("dividend_anchor_year"))
    fields: dict[str, Any] = {"rule_version": RULE_VERSION}
    consecutive = strategy.finite_float(record.get("consecutive_dividend_years"))
    fields["consecutive_dividend_years_capped"] = (
        min(consecutive, CONSECUTIVE_YEARS_CAP) if consecutive is not None else None
    )
    if anchor is None:
        return fields
    anchor_year = int(anchor)
    fields.update(dps_cut_profile(_int_keyed(record.get("annual_comparable_dps_json")), anchor_year))
    fields.update(profit_cycle_profile(_int_keyed(record.get("annual_profit_dedt_json")), anchor_year))
    if not bool(record.get("is_financial")):
        fields.update(interest_coverage_profile(fina_indicator, anchor_year, income_interest))
    return fields


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------


def interest_coverage_gate(record: dict[str, Any], minimum: float) -> tuple[str, str]:
    if bool(record.get("is_financial")):
        return "not_applicable", ""
    netdebt = strategy.finite_float(record.get("netdebt_latest"))
    coverage = strategy.finite_float(record.get("ebit_to_interest_latest"))
    if netdebt is not None and netdebt <= 0:
        return "passed", ""
    if record.get("ebit_to_interest_source") == "income:no_interest_expense":
        return "passed", ""
    if coverage is not None:
        if coverage >= minimum:
            return "passed", ""
        return "hard_gate_failed", f"EBIT利息覆盖{coverage:.2f}倍，低于{minimum:g}倍且存在净债务"
    return "data_gap", "缺少最新年报EBIT利息覆盖倍数"


def cycle_gate(record: dict[str, Any], max_ratio: float | None, nonfinancial_only: bool = False) -> tuple[str, str]:
    if max_ratio is None:
        return "not_applied", ""
    if nonfinancial_only and bool(record.get("is_financial")):
        return "not_applicable", ""
    ratio = strategy.finite_float(record.get("profit_dedt_to_10y_median"))
    if ratio is None and record.get("profit_dedt_to_10y_median") == math.inf:
        ratio = math.inf
    if ratio is None:
        # Too little history to judge a cycle; short history is already
        # penalized through the dividend-record block.
        return "insufficient_history", ""
    if ratio > max_ratio:
        return "hard_gate_failed", f"最新扣非利润为10年中位数的{ratio:.2f}倍，超过{max_ratio:g}倍（疑似周期高点）"
    return "passed", ""


def apply_v2(
    record: dict[str, Any],
    fina_indicator: pd.DataFrame | None,
    params: dict[str, float | None],
    income_interest: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Add v2 factors and gates to one structured record (copy)."""
    updated = {**record, **v2_factor_fields(record, fina_indicator, income_interest)}
    coverage_status, coverage_reason = interest_coverage_gate(updated, float(params["interest_coverage_min"]))
    cycle_status, cycle_reason = cycle_gate(
        updated, params["cycle_gate_max_ratio"], bool(params["cycle_gate_nonfinancial_only"])
    )
    updated["interest_coverage_gate_status"] = coverage_status
    updated["cycle_gate_status"] = cycle_status
    if not bool(updated.get("structured_gate_passed")):
        return updated
    for status, reason in ((coverage_status, coverage_reason), (cycle_status, cycle_reason)):
        if status in {"hard_gate_failed", "data_gap"}:
            updated["structured_gate_passed"] = False
            updated["structured_gate_status"] = status
            updated["structured_gate_reason"] = reason
            break
    return updated


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _percentiles(frame: pd.DataFrame, field: str, *, higher_is_better: bool) -> pd.Series:
    work = pd.DataFrame({field: pd.to_numeric(frame[field], errors="coerce") if field in frame else float("nan")}, index=frame.index)
    strategy._assign_percentile(work, field, "_pct", higher_is_better=higher_is_better)
    return work["_pct"]


def score_v2(
    rows: list[dict[str, Any]],
    params: dict[str, float | None],
    bank_mode: str,
    *,
    record_weights: dict[str, float] | None = None,
    group_weights: dict[str, dict[str, float]] | None = None,
) -> list[dict[str, Any]]:
    """Score market-preflight passers under rule v2.

    The quality block keeps the v1 reference pools (all preflight passers of
    the group). The record block and the cycle factor rank only structured
    gate passers, across groups.
    """
    if not rows:
        return []
    record_weights = record_weights or RECORD_FACTOR_WEIGHTS
    quality = strategy.score_cross_section(rows, factor_weights=group_weights or quality_weights(bank_mode))
    frame = pd.DataFrame(quality)
    gated = frame[frame["structured_gate_passed"].fillna(False).astype(bool)] if "structured_gate_passed" in frame else frame.iloc[0:0]
    record_parts = {}
    for factor in record_weights:
        record_parts[factor] = _percentiles(gated, factor, higher_is_better=factor not in RECORD_LOWER_IS_BETTER)
    record_score = sum(record_parts[factor] * weight for factor, weight in record_weights.items()) / sum(record_weights.values())
    ratio = (
        pd.to_numeric(gated["profit_dedt_to_10y_median"], errors="coerce").replace(math.inf, 1e12)
        if "profit_dedt_to_10y_median" in gated
        else pd.Series(float("nan"), index=gated.index)
    )
    cycle = _percentiles(pd.DataFrame({"r": ratio}, index=gated.index), "r", higher_is_better=False).fillna(50.0)
    block_weight = float(params["record_block_weight"])
    cycle_weight = float(params["cycle_factor_weight"] or 0.0)
    records = frame.to_dict("records")
    for index, record in zip(frame.index, records):
        quality_score = strategy.finite_float(record.get("dividend_score_total"))
        record["quality_score"] = quality_score
        record["record_score"] = round(float(record_score[index]), 2) if index in record_score.index and pd.notna(record_score[index]) else None
        record["cycle_percentile"] = round(float(cycle[index]), 2) if index in cycle.index else None
        if quality_score is None or record["record_score"] is None:
            missing = [factor for factor in record_weights if index in gated.index and pd.isna(record_parts[factor].get(index))]
            record["dividend_score_total"] = None
            if missing:
                record["score_reason"] = "缺少计分因子：" + "、".join(missing)
            continue
        base = block_weight * record["record_score"] + (1 - block_weight) * quality_score
        total = (1 - cycle_weight) * base + cycle_weight * record["cycle_percentile"]
        record["dividend_score_total"] = round(total, 2)
        record["score_reason"] = "v2：跨组分红记录与组内经营质量加权"
    return records
