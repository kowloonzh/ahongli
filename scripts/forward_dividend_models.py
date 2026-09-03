#!/usr/bin/env python3
"""Pure calculations for the forward-dividend analysis stage."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "是", "yes"}


def compute_ttm_profit(
    *,
    prior_fy_profit: float,
    prior_same_period_profit: float,
    current_same_period_profit: float,
) -> float:
    return prior_fy_profit - prior_same_period_profit + current_same_period_profit


def compute_dps_scenarios(
    *,
    profit_scenarios: dict[str, float],
    payout_ratio: float,
    forecast_total_shares: float,
) -> dict[str, float]:
    if not 0 < payout_ratio <= 1:
        raise ValueError("payout_ratio must be greater than zero and at most one")
    if forecast_total_shares <= 0:
        raise ValueError("forecast_total_shares must be positive")
    return {
        scenario: profit * payout_ratio / forecast_total_shares
        for scenario, profit in profit_scenarios.items()
    }


def _date(value: Any):
    return datetime.strptime(str(value), "%Y%m%d").date()


def forward_twelve_month_eligible_dps(
    *,
    events: list[dict[str, Any]],
    quote_date: str,
) -> float | None:
    start = _date(quote_date)
    end = start + timedelta(days=365)
    eligible = [
        float(event["cash_dividend_per_share_pre_tax"])
        for event in events
        if event.get("regular_or_special") == "regular"
        and event.get("ex_dividend_date")
        and start < _date(event["ex_dividend_date"]) <= end
    ]
    return sum(eligible) if eligible else None


def _ratio(value: Any) -> float | None:
    if value in {None, "", "未取得"}:
        return None
    number = float(value)
    return number / 100 if abs(number) > 1 else number


def _round_half_percent(value: float) -> float:
    return round(value / 0.005) * 0.005


def target_yield_from_analysis(
    *,
    company: dict[str, Any],
    risk_free_rate: float,
) -> dict[str, float | str]:
    """Derive a decision yield band from required return less sustainable growth."""
    if not 0 <= risk_free_rate < 0.20:
        raise ValueError("risk_free_rate must be a decimal between zero and 0.20")
    industry = str(company.get("industry") or "")
    if "水力发电" in industry:
        category = "stable_hydropower"
        spread_low, spread_high = 0.063, 0.073
        growth_floor, growth_cap = 0.02, 0.04
    elif "银行" in industry or _truthy(company.get("is_bank")):
        category = "bank"
        spread_low, spread_high = 0.063, 0.083
        growth_floor, growth_cap = 0.02, 0.03
    elif "保险" in industry:
        category = "insurance"
        spread_low, spread_high = 0.063, 0.083
        growth_floor, growth_cap = 0.02, 0.03
    elif "电信运营" in industry:
        category = "telecom"
        spread_low, spread_high = 0.063, 0.083
        growth_floor, growth_cap = 0.02, 0.03
    else:
        category = "other"
        spread_low, spread_high = 0.073, 0.093
        growth_floor, growth_cap = 0.01, 0.03

    roe = _ratio(company.get("roe"))
    payout = _ratio(company.get("latest_payout_ratio"))
    dps_cagr = _ratio(company.get("dps_cagr_5y"))
    growth_candidates = []
    if dps_cagr is not None:
        growth_candidates.append(max(0.0, dps_cagr))
    if roe is not None and payout is not None:
        growth_candidates.append(max(0.0, roe * (1 - payout)))
    raw_growth = min(growth_candidates) if growth_candidates else growth_floor
    sustainable_growth = min(growth_cap, max(growth_floor, raw_growth))
    required_low = risk_free_rate + spread_low
    required_high = risk_free_rate + spread_high
    target_low = _round_half_percent(max(0.0, required_low - sustainable_growth))
    target_high = _round_half_percent(max(target_low + 0.005, required_high - sustainable_growth))
    return {
        "target_yield_model_id": "required_return_minus_growth_v1",
        "target_yield_model_version": "1",
        "target_yield_category": category,
        "risk_free_rate": risk_free_rate,
        "equity_and_company_risk_spread_low": spread_low,
        "equity_and_company_risk_spread_high": spread_high,
        "required_return_low": required_low,
        "required_return_high": required_high,
        "sustainable_dividend_growth": sustainable_growth,
        "target_yield_low": target_low,
        "target_yield_high": target_high,
        "target_yield_basis": "要求总回报率减可持续分红增长率",
    }


def valuation_from_target_yield(
    *,
    base_dps: float,
    quote_price: float,
    target_yield_low: float,
    target_yield_high: float,
) -> dict[str, float | str | None]:
    expected_yield = base_dps / quote_price
    if expected_yield > target_yield_high:
        status = "above_target_yield"
        label = "高于目标，核查风险"
    elif expected_yield < target_yield_low:
        status = "below_target_yield"
        label = "未达目标区间"
    else:
        status = "within_target_yield"
        label = "进入目标区间"
    return {
        "expected_dividend_yield": expected_yield,
        "target_price_low": base_dps / target_yield_high,
        "target_price_high": base_dps / target_yield_low,
        "target_status": status,
        "target_display_label": label,
    }


def forecast_company(
    *,
    company: dict[str, Any],
    facts: dict[str, Any],
) -> dict[str, Any]:
    if facts.get("announced_regular_dps") is not None:
        dps = float(facts["announced_regular_dps"])
        return {
            "forecast_fiscal_year": facts.get("forecast_fiscal_year"),
            "forecast_fy_regular_dps_low": dps,
            "forecast_fy_regular_dps_base": dps,
            "forecast_fy_regular_dps_high": dps,
            "forecast_method": "announced",
            "model_id": "announced_regular_dps_v1",
            "model_version": "1",
            "evidence_completeness": "complete",
            "forecast_uncertainty": "low",
            "forecast_status": "announced",
            "forecast_reason": "已公告完整常规DPS",
            "forecast_input_fact_ids": facts.get("forecast_input_fact_ids", []),
            "forecast_input_event_ids": facts.get("forecast_input_event_ids", []),
        }
    industry = str(company.get("industry") or "")
    insurance_required = ["operating_profit_scenarios", "official_payout_ratio", "forecast_total_shares"]
    if "保险" in industry and all(facts.get(field) is not None for field in insurance_required):
        dps = compute_dps_scenarios(
            profit_scenarios=facts["operating_profit_scenarios"],
            payout_ratio=float(facts["official_payout_ratio"]),
            forecast_total_shares=float(facts["forecast_total_shares"]),
        )
        return {
            "forecast_fiscal_year": facts.get("forecast_fiscal_year"),
            "forecast_fy_regular_dps_low": dps["low"],
            "forecast_fy_regular_dps_base": dps["base"],
            "forecast_fy_regular_dps_high": dps["high"],
            "forecast_method": facts.get("payout_method", "policy_derived"),
            "model_id": (
                "insurance_operating_profit_historical_payout_v2"
                if facts.get("payout_method") == "historical_payout"
                else "insurance_operating_profit_policy_v1"
            ),
            "model_version": "2" if facts.get("payout_method") == "historical_payout" else "1",
            "evidence_completeness": facts.get("evidence_completeness", "complete"),
            "forecast_uncertainty": "high" if facts.get("payout_method") == "historical_payout" else "medium",
            "forecast_status": "modelled",
            "forecast_reason": (
                "三年原始证据派息率中位数与营运利润情景"
                if facts.get("payout_method") == "historical_payout"
                else "正式股东回报政策与营运利润情景"
            ),
            "forecast_profit": facts["operating_profit_scenarios"]["base"],
            "forecast_payout_ratio": float(facts["official_payout_ratio"]),
            "forecast_payout_ratio_low": float(facts["official_payout_ratio"]),
            "forecast_payout_ratio_high": float(facts["official_payout_ratio"]),
            "forecast_total_shares": float(facts["forecast_total_shares"]),
            "forecast_input_fact_ids": facts.get("forecast_input_fact_ids", []),
            "forecast_input_event_ids": facts.get("forecast_input_event_ids", []),
        }
    required = ["forecast_profit_scenarios", "official_payout_ratio", "forecast_total_shares"]
    if all(facts.get(field) is not None for field in required):
        is_bank = _truthy(company.get("is_bank")) or "银行" in str(company.get("industry") or "")
        if is_bank:
            capital_ok = (
                _truthy(company.get("bank_quality_gate_passed"))
                and float(company.get("bank_core_tier1_capital_ratio") or 0) >= 7.5
                and float(company.get("bank_tier1_capital_ratio") or 0) >= 8.5
                and float(company.get("bank_capital_adequacy_ratio") or 0) >= 10.5
            )
            if not capital_ok:
                return {
                    "forecast_fiscal_year": facts.get("forecast_fiscal_year"),
                    "forecast_fy_regular_dps_low": None,
                    "forecast_fy_regular_dps_base": None,
                    "forecast_fy_regular_dps_high": None,
                    "forecast_method": "policy_derived",
                    "model_id": "bank_profit_policy_capital_v1",
                    "model_version": "1",
                    "evidence_completeness": "missing",
                    "forecast_uncertainty": "not_estimable",
                    "forecast_status": "data_gap",
                    "forecast_reason": "银行前瞻资本约束证据不完整或未通过",
                    "forecast_input_fact_ids": facts.get("forecast_input_fact_ids", []),
                    "forecast_input_event_ids": facts.get("forecast_input_event_ids", []),
                }
        payout_scenarios = facts.get("payout_ratio_scenarios")
        if payout_scenarios:
            dps = {
                scenario: float(facts["forecast_profit_scenarios"][scenario])
                * float(payout_scenarios[scenario])
                / float(facts["forecast_total_shares"])
                for scenario in ["low", "base", "high"]
            }
        else:
            dps = compute_dps_scenarios(
                profit_scenarios=facts["forecast_profit_scenarios"],
                payout_ratio=float(facts["official_payout_ratio"]),
                forecast_total_shares=float(facts["forecast_total_shares"]),
            )
        return {
            "forecast_fiscal_year": facts.get("forecast_fiscal_year"),
            "forecast_fy_regular_dps_low": dps["low"],
            "forecast_fy_regular_dps_base": dps["base"],
            "forecast_fy_regular_dps_high": dps["high"],
            "forecast_method": facts.get("payout_method", "policy_derived"),
            "model_id": (
                "bank_policy_history_capital_v1"
                if is_bank and facts.get("payout_method") == "policy_and_history"
                else
                "bank_historical_payout_capital_v1"
                if is_bank and facts.get("payout_method") == "historical_payout"
                else "bank_profit_policy_capital_v1"
                if is_bank
                else "regular_profit_policy_v1"
            ),
            "model_version": "1",
            "evidence_completeness": facts.get("evidence_completeness", "complete"),
            "forecast_uncertainty": "medium",
            "forecast_status": "modelled",
            "forecast_reason": (
                "正式派息下限作为低情景、三年实际派息率作为基准情景并检查资本约束"
                if is_bank and facts.get("payout_method") == "policy_and_history"
                else
                "三年原始证据派息率中位数、可复算利润情景与资本约束"
                if is_bank and facts.get("payout_method") == "historical_payout"
                else "正式派息政策与可复算利润情景"
            ),
            "forecast_profit": facts["forecast_profit_scenarios"]["base"],
            "forecast_payout_ratio": float(facts["official_payout_ratio"]),
            "forecast_payout_ratio_low": float(payout_scenarios["low"]) if payout_scenarios else float(facts["official_payout_ratio"]),
            "forecast_payout_ratio_high": float(payout_scenarios["high"]) if payout_scenarios else float(facts["official_payout_ratio"]),
            "forecast_total_shares": float(facts["forecast_total_shares"]),
            "forecast_input_fact_ids": facts.get("forecast_input_fact_ids", []),
            "forecast_input_event_ids": facts.get("forecast_input_event_ids", []),
        }
    if any(facts.get(field) is not None for field in required):
        missing = [field for field in required if facts.get(field) is None]
        return {
            "forecast_fiscal_year": facts.get("forecast_fiscal_year"),
            "forecast_fy_regular_dps_low": None,
            "forecast_fy_regular_dps_base": None,
            "forecast_fy_regular_dps_high": None,
            "forecast_method": "policy_derived",
            "model_id": "regular_profit_policy_v1",
            "model_version": "1",
            "evidence_completeness": "missing",
            "forecast_uncertainty": "not_estimable",
            "forecast_status": "data_gap",
            "forecast_reason": "缺少强制事实：" + "、".join(missing),
            "forecast_input_fact_ids": facts.get("forecast_input_fact_ids", []),
            "forecast_input_event_ids": facts.get("forecast_input_event_ids", []),
        }
    if any(marker in industry for marker in ["发电", "电信运营", "银行", "保险"]):
        return {
            "forecast_fiscal_year": facts.get("forecast_fiscal_year"),
            "forecast_fy_regular_dps_low": None,
            "forecast_fy_regular_dps_base": None,
            "forecast_fy_regular_dps_high": None,
            "forecast_method": "",
            "model_id": "",
            "model_version": "",
            "evidence_completeness": "missing",
            "forecast_uncertainty": "not_estimable",
            "forecast_status": "data_gap",
            "forecast_reason": "受支持模型缺少强制原始证据",
            "forecast_input_fact_ids": facts.get("forecast_input_fact_ids", []),
            "forecast_input_event_ids": facts.get("forecast_input_event_ids", []),
        }
    return {
        "forecast_fiscal_year": facts.get("forecast_fiscal_year"),
        "forecast_fy_regular_dps_low": None,
        "forecast_fy_regular_dps_base": None,
        "forecast_fy_regular_dps_high": None,
        "forecast_method": "",
        "model_id": "",
        "model_version": "",
        "evidence_completeness": facts.get("evidence_completeness", "missing"),
        "forecast_uncertainty": "not_estimable",
        "forecast_status": "unsupported",
        "forecast_reason": "当前版本没有适用的前瞻分红模型",
        "forecast_input_fact_ids": facts.get("forecast_input_fact_ids", []),
        "forecast_input_event_ids": facts.get("forecast_input_event_ids", []),
    }
