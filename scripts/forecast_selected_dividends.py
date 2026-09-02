#!/usr/bin/env python3
"""Build forward-dividend forecasts from normalized Top10 evidence."""

from __future__ import annotations

from collections import Counter
import statistics
from typing import Any

from forward_dividend_models import (
    compute_ttm_profit,
    forward_twelve_month_eligible_dps,
    forecast_company,
    target_yield_from_analysis,
    valuation_from_target_yield,
)


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "是", "yes"}


def _facts(evidence: dict[str, Any], fact_type: str) -> list[dict[str, Any]]:
    return [
        fact
        for fact in evidence.get("normalized_facts", [])
        if fact.get("fact_type") == fact_type and fact.get("value") is not None
    ]


def _latest(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not records:
        return None
    latest_period = max(str(fact.get("period") or "") for fact in records)
    latest = [fact for fact in records if str(fact.get("period") or "") == latest_period]
    values = [float(fact["value"]) for fact in latest]
    counts = Counter(values)
    highest_count = max(counts.values())
    consensus_value = max(value for value, count in counts.items() if count == highest_count)
    return next(fact for fact in latest if float(fact["value"]) == consensus_value)


def _derived_payout_ratios(
    evidence: dict[str, Any],
    *,
    profit_fact_type: str,
) -> dict[str, float]:
    profits = _facts(evidence, profit_fact_type)
    shares = _facts(evidence, "total_shares")
    ratios: dict[str, float] = {}
    events_by_period: dict[str, list[dict[str, Any]]] = {}
    for event in evidence.get("dividend_events", []):
        if event.get("regular_or_special") != "regular":
            continue
        if event.get("status") not in {None, "", "implementation"}:
            continue
        period = str(event.get("fiscal_period") or "")
        if period:
            events_by_period.setdefault(period, []).append(event)
    for period, events in events_by_period.items():
        profit = _latest([fact for fact in profits if str(fact.get("period")) == period])
        share = _latest([fact for fact in shares if str(fact.get("period")) == period])
        if not profit or not share or float(profit["value"]) <= 0 or float(share["value"]) <= 0:
            continue
        full_year = [event for event in events if event.get("distribution_phase") == "full_year"]
        if full_year:
            dps = float(full_year[-1]["cash_dividend_per_share_pre_tax"])
        else:
            dps = sum(float(event["cash_dividend_per_share_pre_tax"]) for event in events)
        ratio = dps * float(share["value"]) / float(profit["value"])
        if 0 < ratio < 1:
            ratios[period] = ratio
    return ratios


def _model_facts(
    evidence: dict[str, Any],
    run_date: str,
    company: dict[str, Any] | None = None,
) -> dict[str, Any]:
    forecast_year = int(str(run_date)[:4])
    profits = _facts(evidence, "net_profit_parent")
    annual = _latest(
        [fact for fact in profits if str(fact.get("period", "")).endswith("1231") and int(str(fact["period"])[:4]) < forecast_year]
    )
    current = _latest(
        [fact for fact in profits if int(str(fact.get("period", "0"))[:4]) == forecast_year and not str(fact["period"]).endswith("1231")]
    )
    prior_comparables = _facts(evidence, "net_profit_parent_prior_comparable")
    prior = None
    if current:
        expected_period = f"{forecast_year - 1}{str(current['period'])[4:]}"
        prior = next((fact for fact in prior_comparables if str(fact.get("period")) == expected_period), None)
    shares = _latest(_facts(evidence, "total_shares"))
    policies = [
        fact
        for fact in _facts(evidence, "official_payout_floor")
        if (fact.get("valid_from") is None or int(fact["valid_from"]) <= forecast_year)
        and (fact.get("valid_to") is None or int(fact["valid_to"]) >= forecast_year)
    ]
    policy = _latest(policies)
    result: dict[str, Any] = {"forecast_fiscal_year": forecast_year}
    input_fact_ids: list[str] = []
    input_event_ids: list[str] = []
    announced = [
        event
        for event in evidence.get("dividend_events", [])
        if str(event.get("fiscal_period", "")).startswith(str(forecast_year))
        and event.get("regular_or_special") == "regular"
        and event.get("status") in {"dividend_plan", "implementation"}
    ]
    full_year = [event for event in announced if event.get("distribution_phase") == "full_year"]
    phased = [event for event in announced if event.get("distribution_phase") in {"interim", "final"}]
    if full_year:
        result["announced_regular_dps"] = float(full_year[-1]["cash_dividend_per_share_pre_tax"])
    elif {event.get("distribution_phase") for event in phased} == {"interim", "final"}:
        result["announced_regular_dps"] = sum(
            float(event["cash_dividend_per_share_pre_tax"])
            for event in phased
        )
    if annual and current and prior:
        input_fact_ids.extend(
            str(fact["fact_id"])
            for fact in [annual, current, prior]
            if fact.get("fact_id")
        )
        ttm = compute_ttm_profit(
            prior_fy_profit=float(annual["value"]),
            prior_same_period_profit=float(prior["value"]),
            current_same_period_profit=float(current["value"]),
        )
        change = abs(ttm - float(annual["value"]))
        result["forecast_profit_scenarios"] = {
            "low": min(float(annual["value"]), ttm),
            "base": ttm,
            "high": ttm + change,
        }
    operating_profits = _facts(evidence, "operating_profit_parent")
    operating_annual = _latest(
        [fact for fact in operating_profits if str(fact.get("period", "")).endswith("1231") and int(str(fact["period"])[:4]) < forecast_year]
    )
    operating_current = _latest(
        [fact for fact in operating_profits if int(str(fact.get("period", "0"))[:4]) == forecast_year and not str(fact["period"]).endswith("1231")]
    )
    operating_prior = None
    if operating_current:
        expected_period = f"{forecast_year - 1}{str(operating_current['period'])[4:]}"
        operating_prior = next(
            (
                fact
                for fact in _facts(evidence, "operating_profit_parent_prior_comparable")
                if str(fact.get("period")) == expected_period
            ),
            None,
        )
    if operating_annual and operating_current and operating_prior:
        input_fact_ids.extend(
            str(fact["fact_id"])
            for fact in [operating_annual, operating_current, operating_prior]
            if fact.get("fact_id")
        )
        operating_ttm = compute_ttm_profit(
            prior_fy_profit=float(operating_annual["value"]),
            prior_same_period_profit=float(operating_prior["value"]),
            current_same_period_profit=float(operating_current["value"]),
        )
        operating_change = abs(operating_ttm - float(operating_annual["value"]))
        result["operating_profit_scenarios"] = {
            "low": min(float(operating_annual["value"]), operating_ttm),
            "base": operating_ttm,
            "high": operating_ttm + operating_change,
        }
    if shares:
        result["forecast_total_shares"] = float(shares["value"])
        if shares.get("fact_id"):
            input_fact_ids.append(str(shares["fact_id"]))
    if policy:
        result["official_payout_ratio"] = float(policy["value"])
        result["payout_method"] = "policy_derived"
        if policy.get("fact_id"):
            input_fact_ids.append(str(policy["fact_id"]))
    is_bank = _truthy((company or {}).get("is_bank")) or "银行" in str((company or {}).get("industry") or "")
    is_insurance = "保险" in str((company or {}).get("industry") or "")
    if "official_payout_ratio" not in result and (is_bank or is_insurance):
        payout_facts = [
            fact
            for fact in _facts(evidence, "historical_payout_ratio")
            if 0 < float(fact["value"]) < 1
        ]
        latest_by_period: dict[str, float] = {}
        payout_fact_id_by_period: dict[str, str] = {}
        for fact in payout_facts:
            period_key = str(fact.get("period") or "")
            latest_by_period[period_key] = float(fact["value"])
            if fact.get("fact_id"):
                payout_fact_id_by_period[period_key] = str(fact["fact_id"])
        derived = _derived_payout_ratios(
            evidence,
            profit_fact_type="operating_profit_parent" if is_insurance else "net_profit_parent",
        )
        for period, value in derived.items():
            latest_by_period.setdefault(period, value)
        selected_periods = sorted(latest_by_period)[-3:]
        latest_values = [latest_by_period[period] for period in selected_periods]
        if len(latest_values) == 3:
            result["official_payout_ratio"] = statistics.median(latest_values)
            result["payout_method"] = "historical_payout"
            input_fact_ids.extend(
                payout_fact_id_by_period[period]
                for period in selected_periods
                if period in payout_fact_id_by_period
            )
            input_event_ids.extend(
                str(event["event_id"])
                for event in evidence.get("dividend_events", [])
                if str(event.get("fiscal_period") or "") in selected_periods and event.get("event_id")
            )
    result["evidence_completeness"] = "complete" if (
        "announced_regular_dps" in result
        or all(key in result for key in ["forecast_profit_scenarios", "forecast_total_shares", "official_payout_ratio"])
        or all(key in result for key in ["operating_profit_scenarios", "forecast_total_shares", "official_payout_ratio"])
    ) else "missing"
    result["forecast_input_fact_ids"] = list(dict.fromkeys(input_fact_ids))
    result["forecast_input_event_ids"] = list(dict.fromkeys(input_event_ids))
    return result


def forecast_selected_dividends(
    *,
    top_rows: list[dict[str, Any]],
    evidence_by_code: dict[str, dict[str, Any]],
    run_date: str,
    risk_free_rate: float = 0.017,
    risk_free_rate_date: str = "20260831",
    risk_free_rate_source: str = "ChinaBond 10Y China Government Bond yield",
) -> list[dict[str, Any]]:
    selected = [row for row in top_rows if str(row.get("selected", "是")) == "是"]
    if len(selected) > 10:
        raise ValueError("forward forecast accepts at most ten selected rows")
    results: list[dict[str, Any]] = []
    for row in selected:
        code = str(row["ts_code"])
        evidence = evidence_by_code.get(code, {})
        if evidence.get("stage_status") == "failed":
            forecast = {
                "forecast_fiscal_year": int(str(run_date)[:4]),
                "forecast_fy_regular_dps_low": None,
                "forecast_fy_regular_dps_base": None,
                "forecast_fy_regular_dps_high": None,
                "forecast_method": "",
                "model_id": "",
                "model_version": "",
                "evidence_completeness": "missing",
                "forecast_uncertainty": "not_estimable",
                "forecast_status": "failed",
                "forecast_reason": str(evidence.get("stage_reason") or "前瞻证据准备或解析失败"),
                "forecast_input_fact_ids": [],
                "forecast_input_event_ids": [],
            }
        else:
            model_facts = _model_facts(evidence, run_date, row)
            forecast = forecast_company(company=row, facts=model_facts)
        target = target_yield_from_analysis(company=row, risk_free_rate=risk_free_rate)
        valuation: dict[str, Any]
        base_dps = forecast.get("forecast_fy_regular_dps_base")
        quote_price = row.get("current_price")
        if base_dps is not None and quote_price not in {None, ""}:
            valuation = valuation_from_target_yield(
                base_dps=float(base_dps),
                quote_price=float(quote_price),
                target_yield_low=float(target["target_yield_low"]),
                target_yield_high=float(target["target_yield_high"]),
            )
        else:
            valuation = {
                "expected_dividend_yield": (
                    float(base_dps) / float(quote_price)
                    if base_dps is not None and quote_price not in {None, ""}
                    else None
                ),
                "target_price_low": None,
                "target_price_high": None,
                "target_status": "not_determined",
                "target_display_label": "暂不判断",
            }
        forward_12m = (
            forward_twelve_month_eligible_dps(
                events=evidence.get("dividend_events", []),
                quote_date=str(row.get("price_date")),
            )
            if row.get("price_date") not in {None, ""}
            else None
        )
        results.append(
            {
                **row,
                "quote_price": row.get("current_price"),
                "quote_date": row.get("price_date"),
                "instrument_ts_code": code,
                "share_class": "A",
                "quote_currency": "CNY",
                "dividend_currency": "CNY",
                "forecast_share_denominator_scope": "all_ordinary_shares",
                "fx_rate": 1.0,
                "fx_rate_date": row.get("price_date"),
                "fx_source": "identity:CNY/CNY",
                "source_dividend_yield": row.get("current_dividend_yield"),
                "source_dividend_yield_definition": "Tushare daily_basic.dv_ttm",
                "risk_free_rate_date": risk_free_rate_date,
                "risk_free_rate_source": risk_free_rate_source,
                **forecast,
                "forward_12m_eligible_dps": forward_12m,
                **target,
                **valuation,
            }
        )
    return results
