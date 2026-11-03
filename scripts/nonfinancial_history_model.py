"""Conservative, source-audited historical cash-payout sensitivity model.

This is not a company dividend commitment or a general nonfinancial fallback.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from statistics import median


class EvidenceGap(ValueError):
    def __init__(self, message, completeness="missing"):
        super().__init__(message)
        self.completeness = completeness


def supports_history(company):
    return (company.get("industry") in {"家用电器", "家电"}
            and company.get("selection_status") == "selected"
            and str(company.get("is_financial", "")).lower() in {"否", "false", "0"}
            and company.get("audit_result") in {"标准无保留意见", "标准无保留"})


def forecast_history(company, evidence, run_date):
    """Use last three complete FY distributions and latest comparable interim.

    Fail closed on conflicting facts, missing dates/units/scopes, incomplete
    payments, or cashflow coverage. No value is calibrated to a desired DPS.
    """
    year = int(run_date[:4])
    decisions, used, history = [], [], []
    result = {"forecast_fiscal_year": year, "model_id": "nonfinancial_historical_payout_v1",
              "model_version": "1", "forecast_method": "historical_payout",
              "forecast_status": "data_gap", "forecast_uncertainty": "not_estimable",
              "evidence_completeness": "missing", "forecast_fy_regular_dps_low": None,
              "forecast_fy_regular_dps_base": None, "forecast_fy_regular_dps_high": None,
              "forecast_input_fact_ids": used, "forecast_input_event_ids": [],
              "forecast_selection_decisions": decisions, "forecast_history": history}
    sources = {str(d["announcement_id"]): d for d in evidence.get("source_documents", [])}
    local_tz = timezone(timedelta(hours=8))
    cutoff = datetime.strptime(run_date, "%Y%m%d").replace(tzinfo=local_tz) + timedelta(days=1)
    def available(identity):
        source = sources.get(str(identity), {})
        try:
            timestamp = datetime.fromisoformat(str(source.get("available_at", "")))
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=local_tz)
            return timestamp < cutoff
        except ValueError:
            return False

    facts = []
    for f in evidence.get("normalized_facts", []):
        source_ids = [f.get("source_document_id"), *f.get("corroborating_source_ids", [])]
        if all(available(i) for i in source_ids):
            facts.append(f)
        else:
            decisions.append({"fact_id": f.get("fact_id"), "decision": "rejected",
                              "reason": "source unavailable at analysis cutoff"})

    def choose(kind, period, *, phase=None, role=None):
        candidates = [f for f in facts if f.get("fact_type") == kind and f.get("period") == period
                      and (phase is None or f.get("distribution_phase") == phase)
                      and (role is None or sources[str(f["source_document_id"])].get("logical_role") == role)]
        expected_unit = ("CNY/share" if kind in {"implemented_regular_dps", "annual_regular_dps_total"}
                         else "share" if kind == "dividend_eligible_shares" else "CNY")
        if not candidates:
            raise EvidenceGap(f"缺少 {period} {phase or ''} {kind} 原始证据")
        for f in candidates:
            try:
                valid_number = math.isfinite(float(f.get("value")))
            except (TypeError, ValueError):
                valid_number = False
            if f.get("unit") != expected_unit or not valid_number:
                raise EvidenceGap(f"{period} {kind} 单位或数值不受支持")
            if kind in {"regular_cash_distribution", "dividend_eligible_shares"} and f.get("share_scope") != "all_ordinary_shares":
                raise EvidenceGap(f"{period} {kind} 缺少全部普通股口径")
            if kind.startswith("history_") and f.get("reporting_scope") != "consolidated":
                raise EvidenceGap(f"{period} {kind} 缺少合并现金流口径")
        values = [float(f["value"]) for f in candidates]
        # Only accounting-table rounding is tolerated; no majority/largest wins.
        if max(values)-min(values) > max(1e-8, max(abs(v) for v in values)*1e-6):
            decisions.append({"fact_type": kind, "period": period, "decision": "conflict",
                              "candidate_fact_ids": sorted({f["fact_id"] for f in candidates})})
            raise EvidenceGap(f"{period} {kind} 证据数值冲突", "conflict")
        chosen = sorted(candidates, key=lambda f: (sources[str(f["source_document_id"])].get("available_at", ""), f["fact_id"]))[-1]
        if chosen["fact_id"] not in used:
            used.append(chosen["fact_id"])
        decisions.append({"fact_type": kind, "period": period, "phase": phase,
                          "decision": "selected", "selected_fact_id": chosen["fact_id"],
                          "candidate_fact_ids": sorted({f["fact_id"] for f in candidates}),
                          "rule": "consistent within 1ppm rounding; latest source then stable fact ID"})
        return float(chosen["value"])

    try:
        if not supports_history(company):
            raise EvidenceGap("不属于已验证的非金融历史模型范围")
        for y in range(year-3, year):
            period = f"{y}1231"
            profit = choose("net_profit_parent", period, role="annual")
            total_dps = choose("annual_regular_dps_total", period, role="annual")
            phases = sorted({f.get("distribution_phase") for f in facts
                             if f.get("fact_type") == "implemented_regular_dps" and f.get("period") == period})
            if not phases or any(p not in {"interim", "final"} for p in phases):
                raise EvidenceGap(f"{y} 年常规分红实施证据不完整")
            phase_rows = []
            for phase in phases:
                dps = choose("implemented_regular_dps", period, phase=phase, role="implementation")
                cash = choose("regular_cash_distribution", period, phase=phase)
                # Cash must be reconciled to the SAME gross DPS, not a plan total.
                cash_facts = [f for f in facts if f.get("fact_type") == "regular_cash_distribution"
                              and f.get("period") == period and f.get("distribution_phase") == phase]
                if any(abs(float(f.get("dps", -1))-dps)>1e-8 or f.get("regular_or_special") != "regular" for f in cash_facts):
                    raise EvidenceGap(f"{y} {phase} 常规现金金额与实施DPS冲突", "conflict")
                phase_rows.append({"phase": phase, "dps": dps, "cash": cash})
            if abs(sum(p["dps"] for p in phase_rows)-total_dps)>1e-8:
                raise EvidenceGap(f"{y} 年中期与年末DPS合计不等于全年常规DPS")
            cash = sum(p["cash"] for p in phase_rows)
            if profit <= 0 or not 0 < cash <= profit:
                raise EvidenceGap(f"{y} 年利润非正或常规派息率不在(0,100%]内")
            cfo = choose("history_cfo", period, role="annual")
            history.append({"year": y, "profit": profit, "regular_cash": cash,
                            "annual_dps": total_dps, "payout_ratio": cash/profit,
                            "cfo": cfo, "cfo_dividend_coverage": cfo/cash, "phases": phase_rows})
        period = f"{year}0630"
        prior = f"{year-1}0630"
        current_profit = choose("net_profit_parent", period, role="interim")
        prior_profit = choose("net_profit_parent_prior_comparable", prior, role="interim")
        profit = history[-1]["profit"]-prior_profit+current_profit
        shares = choose("dividend_eligible_shares", period, role="interim")
        current_cfo = choose("history_cfo", period, role="interim")
        prior_cfo = choose("history_cfo_prior_comparable", prior, role="interim")
        cfo = history[-1]["cfo"]-prior_cfo+current_cfo
        annual_capex = choose("history_capex", f"{year-1}1231", role="annual")
        current_capex = choose("history_capex", period, role="interim")
        prior_capex = choose("history_capex_prior_comparable", prior, role="interim")
        capex = annual_capex-prior_capex+current_capex
        if profit <= 0 or shares <= 0 or capex < 0:
            raise EvidenceGap("TTM利润、有效股数或资本开支口径不成立")
        ratios = sorted(h["payout_ratio"] for h in history)
        coverage = (cfo-capex)/(profit*median(ratios))
        result.update(forecast_ttm_cfo=cfo, forecast_ttm_capex=capex,
                      forecast_fcf_dividend_coverage=coverage,
                      forecast_ttm_bridge={"prior_fy_profit": history[-1]["profit"], "prior_interim_profit": prior_profit,
                                           "current_interim_profit": current_profit, "prior_fy_cfo": history[-1]["cfo"],
                                           "prior_interim_cfo": prior_cfo, "current_interim_cfo": current_cfo,
                                           "prior_fy_capex": annual_capex, "prior_interim_capex": prior_capex,
                                           "current_interim_capex": current_capex})
        if (any(h["cfo"] <= 0 for h in history) or current_cfo <= 0 or prior_cfo <= 0
                or history[-1]["cfo_dividend_coverage"] < 1
                or median(h["cfo_dividend_coverage"] for h in history) < 1 or coverage < 1):
            raise EvidenceGap("现金流约束未通过：三年CFO、年度分红覆盖或TTM自由现金流代理不足")
        result.update(forecast_status="modelled", evidence_completeness="complete",
                      forecast_uncertainty="high", forecast_profit=profit, forecast_profit_low=profit,
                      forecast_profit_high=profit, forecast_total_shares=shares,
                      forecast_reason="三年完整常规现金派息率中位数 × TTM归母利润 ÷ 有效普通股数；非公司承诺",
                      forecast_assumptions=["TTM利润作为全年情景锚，不另加增长；有效股数假设维持最新已披露分红基数",
                                            "低/基准/高仅为三年派息率最小值/中位数/最大值敏感性，并非概率置信区间",
                                            "CFO减资本开支仅为自由现金流代理，未扣回购、并购及偿债，不能替代完整资本需求评估",
                                            "历史派息率不是年度政策，不将三年平均可分配利润或含回购政策当现金派息率"])
        for scenario, ratio in zip(["low", "base", "high"], [min(ratios), median(ratios), max(ratios)]):
            result[f"forecast_fy_regular_dps_{scenario}"] = profit*ratio/shares
            result["forecast_payout_ratio" + ("" if scenario == "base" else "_"+scenario)] = ratio
    except EvidenceGap as error:
        result["forecast_reason"] = str(error)
        result["evidence_completeness"] = error.completeness
    return result
