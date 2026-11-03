"""Conservative original-document extraction for the cash-payout history model.

No issuer codes or forecast values belong here. Unsupported layouts remain gaps.
"""
from __future__ import annotations

import re

NUMBER = r"[0-9][0-9,]*(?:\.[0-9]+)?"
DPS = rf"每10股(?:派发|派送)(?:现金红利|现金股利|现金分红|现金)(?:人民币)?({NUMBER})元"
SPECIAL = r"特别(?:现金)?(?:股息|分红|股利)|特殊股息"


def compact(text):
    return re.sub(r"\s+", "", text)


def number(value):
    return float(value.replace(",", ""))


def distribution_terms(document, pages):
    """Use the first gross distribution in an implementation's main paragraph.

    Do not scan the tax examples on later pages, nor mistake 特别提示 for a
    special dividend. Annual plans corroborate completeness but cannot fund history.
    """
    if document.get("logical_role") != "implementation":
        return None
    title = compact(document.get("source_title", ""))
    if re.search(SPECIAL, title) or any(t in title for t in ["取消", "更正公告", "H股"]):
        return None
    year = re.search(r"(20\d{2})年?(?:年度|年中期|中期|半年度)", title)
    if not year:
        return None
    for page in pages[:1]:
        text = compact(page["text"])
        match = re.search(DPS, text.split("扣税后")[0])
        if not match or re.search(SPECIAL, text):
            return None
        phase = "interim" if any(t in title for t in ["中期", "半年度"]) else "final"
        amount = re.search(rf"分红总金额为({NUMBER})元", text[match.end():match.end()+100])
        return {"period": year.group(1) + "1231", "distribution_phase": phase,
                "dps": number(match.group(1))/10,
                "cash": number(amount.group(1)) if amount else None,
                "scope": "A" if "A股" in title or "A股" in text[:match.start()] else "all_ordinary_shares",
                "source_document_id": document["announcement_id"],
                "source_file": document["source_file"], "source_page_or_location": page["page"],
                "raw_evidence": page["text"], "available_at": document.get("available_at", "")}
    return None


def extract_history_facts(documents, pages_by_id):
    """Return un-ID'd facts; the main parser attaches stable facts and spans."""
    terms = [t for d in documents if (t := distribution_terms(d, pages_by_id[str(d["announcement_id"])]))]
    facts = []
    def add(kind, value, d, page, period, raw, **extra):
        facts.append({"fact_type": kind, "value": value, "unit": "CNY",
                      "period": period, "source_document_id": d["announcement_id"],
                      "source_file": d["source_file"], "source_page_or_location": page,
                      "raw_evidence": raw, "normalization_rule": "nonfinancial-history-v1", **extra})

    for t in terms:
        d = next(d for d in documents if d["announcement_id"] == t["source_document_id"])
        common = {"distribution_phase": t["distribution_phase"], "regular_or_special": "regular"}
        add("implemented_regular_dps", t["dps"], d, t["source_page_or_location"], t["period"], t["raw_evidence"], unit="CNY/share", **common)
        if t["cash"] is not None and t["scope"] == "all_ordinary_shares":
            add("regular_cash_distribution", t["cash"], d, t["source_page_or_location"], t["period"], t["raw_evidence"], share_scope="all_ordinary_shares", dps=t["dps"], **common)

    for d in documents:
        role = d.get("logical_role")
        if role not in {"annual", "interim"}:
            continue
        year_match = re.search(r"20\d{2}", d.get("source_title", ""))
        if not year_match:
            continue
        year = int(year_match.group())
        period = f"{year}{'1231' if role == 'annual' else '0630'}"
        for page in pages_by_id[str(d["announcement_id"])]:
            raw = page["text"]
            text = compact(raw)
            if page["page"] <= 10 and "利润分配" in text and not re.search(SPECIAL, text):
                m = re.search(DPS, text)
                if m and role == "annual":
                    add("annual_regular_dps_total", number(m.group(1))/10, d, page["page"], period, raw, unit="CNY/share")
            if page["page"] <= 10:
                eligible = re.search(rf"公司总股本({NUMBER})股[^。]{{0,180}}?(?:股本总额|总股数)({NUMBER})股为基数", text)
                if eligible and "回购" in eligible.group(0) and "A股总股本" not in eligible.group(0):
                    if 0 < number(eligible.group(2)) <= number(eligible.group(1)):
                        add("dividend_eligible_shares", number(eligible.group(2)), d, page["page"], period, raw, unit="share", share_scope="all_ordinary_shares")

            # Match explicit value rows only; accounting tables put consolidated
            # current/prior columns first. Do not consume the parent's columns.
            unit_match = re.search(r"(?:单位[：:]?(?:为)?(?:人民币)?|人民币)(百万元|千元|万元|亿元|元)", text)
            unit = unit_match.group(1) if unit_match else ""
            quarterly = bool(re.search(r"分季度|第一季度|第二季度|第三季度|第四季度", text))
            consolidated = ("现金流量表" in text[:500]
                            and ("合并合并公司公司" in text[:500] or "合并现金流量表" in text[:500]))
            parent_only = "公司现金流量表" in text[:500] and not consolidated
            foreign_currency = bool(re.search(r"(?:单位|货币).*?(?:港元|美元|欧元)", text[:500]))
            if not quarterly and not foreign_currency and not parent_only and (consolidated or page["page"] <= 15):
                lines = raw.splitlines()
                for kind, label in [("history_cfo", r"经营活动[^\n]*?现金流量净额"),
                                    ("history_capex", r"购建固定资产[^\n]*?支付\s*的?\s*现金")]:
                    if kind == "history_capex" and not consolidated:
                        continue
                    for index, line in enumerate(lines):
                        if not (("经营活动" in line and "净额" in line) if kind == "history_cfo" else "购建固定资产" in line):
                            continue
                        chunk = line + " " + (lines[index+1] if index+1 < len(lines) else "")
                        match_label = re.search(label, chunk)
                        if not match_label:
                            continue
                        metric_unit = re.search(r"[（(](千元|百万元|万元|亿元|元)[）)]", chunk)
                        scale = {"元": 1, "千元": 1000, "万元": 10000, "百万元": 1000000, "亿元": 100000000}.get(metric_unit.group(1) if metric_unit else unit)
                        if not scale:
                            continue
                        tail = chunk[match_label.end():]
                        tail = re.sub(r"四[（(]\d+[）)](?:[（(][a-z][）)])?", "", tail)
                        tail = re.sub(r"[（(](?:千元|百万元|万元|亿元|元)[）)]", "", tail)
                        vals = re.match(rf"\s*([(-]?{NUMBER}\)?)(?:\s+([(-]?{NUMBER}\)?))?", tail)
                        if not vals:
                            continue
                        for idx in [1, 2]:
                            if not vals.group(idx):
                                continue
                            s = vals.group(idx)
                            value = number(s.strip("()")) * (-1 if s.startswith("(") else 1) * scale
                            if kind == "history_capex":
                                value = abs(value)
                            add(kind if idx == 1 else kind+"_prior_comparable", value, d, page["page"], period if idx == 1 else str(year-1)+period[4:], chunk, reporting_scope="consolidated")
            # Ordinary-dividend notes disclose both A and H amounts in CNY.
            # Bind each payment to an implementation by gross DPS and approval
            # year; ambiguous matches remain gaps, never infer fiscal year alone.
            for block in re.split(r"(?=根据20\d{2}年)", text):
                approval = re.match(r"根据(20\d{2})年", block)
                per_share = re.search(rf"每股人民币({NUMBER})元", block)
                a = re.search(rf"应计A股股利约({NUMBER})元", block)
                h = re.search(rf"应计H股股利约({NUMBER})元", block)
                if not all([approval, per_share, a, h]) or re.search(SPECIAL, block):
                    continue
                dps = number(per_share.group(1))
                candidates = [t for t in terms if abs(t["dps"]-dps)<1e-9 and int(t["period"][:4]) in {int(approval.group(1)), int(approval.group(1))-1}]
                identities = {(t["period"], t["distribution_phase"]) for t in candidates}
                if len(identities) != 1:
                    continue
                fiscal, phase = next(iter(identities))
                add("regular_cash_distribution", number(a.group(1))+number(h.group(1)), d, page["page"], fiscal, raw,
                    share_scope="all_ordinary_shares", distribution_phase=phase, regular_or_special="regular", dps=dps,
                    corroborating_source_ids=sorted({t["source_document_id"] for t in candidates}))
    return facts
