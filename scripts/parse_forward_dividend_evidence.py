#!/usr/bin/env python3
"""Deterministically parse forward-dividend facts and economic events."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from document_cache import atomic_write_text, file_sha256
from nonfinancial_history_evidence import extract_history_facts


STATUS_ORDER = {"dividend_plan": 1, "dividend_policy": 1, "implementation": 2}
EXTRACTOR_VERSION = "pdftotext-layout-regex-v3"


def split_extracted_pages(text: str) -> list[dict[str, Any]]:
    return [
        {"page": index, "text": page.strip()}
        for index, page in enumerate(text.split("\f"), start=1)
        if page.strip()
    ]


def _compact_chinese_date(text: str, label: str) -> str | None:
    match = re.search(
        rf"{label}\s*[:：]?\s*(20\d{{2}})\s*年\s*(\d{{1,2}})\s*月\s*(\d{{1,2}})\s*日",
        text,
    )
    if not match:
        return None
    year, month, day = map(int, match.groups())
    return f"{year:04d}{month:02d}{day:02d}"


def extract_document_pages(
    document: dict[str, Any],
    company_root: Path,
    *,
    command_runner=subprocess.run,
) -> list[dict[str, Any]]:
    company_root = Path(company_root)
    source_path = company_root / document["source_file"]
    extracted = company_root / "extracted"
    extracted.mkdir(parents=True, exist_ok=True)
    stem = f"{document['announcement_id']}-{document['logical_role']}"
    text_path = extracted / f"{stem}.txt"
    pages_path = extracted / f"{stem}.pages.json"
    cache_path = extracted / f"{stem}.cache.json"
    source_hash = file_sha256(source_path)
    if document.get("sha256") and document["sha256"] != source_hash:
        raise ValueError(f"PDF hash mismatch before extraction: {source_path}")
    try:
        metadata = json.loads(cache_path.read_text(encoding="utf-8"))
        if (metadata.get("source_sha256") == source_hash
                and metadata.get("extractor_version") == EXTRACTOR_VERSION
                and metadata.get("pages_sha256") == file_sha256(pages_path)):
            pages = json.loads(pages_path.read_text(encoding="utf-8"))
            if isinstance(pages, list) and all(isinstance(p, dict) and "page" in p and "text" in p for p in pages):
                return pages
    except (OSError, ValueError, AttributeError):
        pass
    command_runner(
        ["pdftotext", "-layout", str(source_path), str(text_path)],
        check=True,
    )
    pages = split_extracted_pages(text_path.read_text(encoding="utf-8", errors="replace"))
    atomic_write_text(pages_path, json.dumps(pages, ensure_ascii=False, indent=2))
    atomic_write_text(cache_path, json.dumps({"source_sha256": source_hash,
                                            "extractor_version": EXTRACTOR_VERSION,
                                            "pages_sha256": file_sha256(pages_path)}, indent=2))
    return pages


def parse_dividend_event(
    text: str,
    *,
    source: dict[str, Any],
    ts_code: str,
    fiscal_period: str,
) -> dict[str, Any]:
    compact = re.sub(r"\s+", "", text)
    per_ten_pattern = r"每10股[^。；]*?(?:派发|派送|派现|派)(?:现金红利|现金股利|现金股息|现金)?(?:人民币)?([0-9]+(?:\.[0-9]+)?)元"
    per_share_pattern = r"(?:A股)?每股[^。；]*?(?:(?:派发|派送|派现|派)(?:现金红利|现金股利|现金股息|现金)?|现金红利|现金股利|现金股息)(?:人民币)?([0-9]+(?:\.[0-9]+)?)元"
    a_clauses = re.findall(r"[AＡ]股[^。；]{0,100}", compact)
    h_dividend_clause = re.search(r"[HＨ]股[^。；]{0,80}(?:每10股|每股)[^。；]{0,80}(?:现金|股息|红利)", compact)
    per_ten = next((match for clause in a_clauses if (match := re.search(per_ten_pattern, clause))), None)
    per_share = next((match for clause in a_clauses if (match := re.search(per_share_pattern, clause))), None)
    if not per_ten and not per_share and h_dividend_clause:
        raise ValueError("A-share CNY cash dividend evidence not found")
    if not per_ten and not per_share:
        per_ten = re.search(per_ten_pattern, compact)
        per_share = re.search(per_share_pattern, compact)
    if not per_ten and not per_share:
        raise ValueError("cash dividend per share not found")
    base_match = re.search(r"(?:总股本|股本)[^0-9]{0,12}([0-9][0-9,]*)\s*股", text)
    dps = float(per_ten.group(1)) / 10.0 if per_ten else float(per_share.group(1))
    base_shares = int(base_match.group(1).replace(",", "")) if base_match else None
    regular_or_special = "special" if "特别" in text else "regular"
    title = str(source.get("source_title") or "")
    full_year_clauses = re.findall(r"全年[^。；]{0,200}", compact)
    is_full_year_total = False
    for clause in full_year_clauses:
        clause_per_ten = re.search(per_ten_pattern, clause)
        clause_per_share = re.search(per_share_pattern, clause)
        clause_dps = (
            float(clause_per_ten.group(1)) / 10.0
            if clause_per_ten
            else float(clause_per_share.group(1))
            if clause_per_share
            else None
        )
        if clause_dps is not None and abs(clause_dps - dps) < 1e-10:
            is_full_year_total = True
            break
    if is_full_year_total:
        distribution_phase = "full_year"
    elif any(token in title for token in ["半年度", "中期"]):
        distribution_phase = "interim"
    elif any(token in title for token in ["末期", "末次"]):
        distribution_phase = "final"
    elif "年度" in title:
        distribution_phase = "final"
    elif any(token in compact for token in ["全年股息合计", "全年每股股息", "全年现金分红"]):
        distribution_phase = "full_year"
    elif any(token in compact[:500] for token in ["半年度", "中期"]):
        distribution_phase = "interim"
    elif any(token in compact[:500] for token in ["末期", "末次"]):
        distribution_phase = "final"
    else:
        distribution_phase = "unknown"
    key = f"{ts_code}|{fiscal_period}|cash|{regular_or_special}|{distribution_phase}|A|{dps:.10f}"
    date_row = re.search(
        r"[AＡ]\s*股\s+(20\d{2})/(\d{1,2})/(\d{1,2})\s+[－-]\s+"
        r"(20\d{2})/(\d{1,2})/(\d{1,2})\s+(20\d{2})/(\d{1,2})/(\d{1,2})",
        text,
    )
    row_record = row_ex = row_payment = None
    if date_row:
        values = list(map(int, date_row.groups()))
        row_record = f"{values[0]:04d}{values[1]:02d}{values[2]:02d}"
        row_ex = f"{values[3]:04d}{values[4]:02d}{values[5]:02d}"
        row_payment = f"{values[6]:04d}{values[7]:02d}{values[8]:02d}"
    return {
        "event_id": hashlib.sha256(key.encode("utf-8")).hexdigest()[:24],
        "ts_code": ts_code,
        "fiscal_period": fiscal_period,
        "dividend_type": "cash",
        "regular_or_special": regular_or_special,
        "distribution_phase": distribution_phase,
        "cash_dividend_per_share_pre_tax": dps,
        "currency": "CNY",
        "share_class": "A",
        "status": source.get("logical_role", "dividend_plan"),
        "record_date": _compact_chinese_date(text, "股权登记日") or row_record,
        "ex_dividend_date": _compact_chinese_date(text, "除权除息日") or row_ex,
        "payment_date": _compact_chinese_date(text, "现金红利发放日") or row_payment,
        "available_date": re.sub(r"\D", "", str(source.get("available_at") or "")[:10]) or None,
        "distribution_base_shares": base_shares,
        "evidence_source_ids": [str(source.get("announcement_id") or "")],
    }


def merge_dividend_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best_by_source: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for event in events:
        source_id = str((event.get("evidence_source_ids") or [""])[0])
        key = (
            source_id,
            str(event.get("ts_code") or ""),
            str(event.get("fiscal_period") or ""),
            str(event.get("distribution_phase") or ""),
            str(event.get("regular_or_special") or ""),
        )
        current = best_by_source.get(key)
        if current is None or float(event["cash_dividend_per_share_pre_tax"]) > float(current["cash_dividend_per_share_pre_tax"]):
            best_by_source[key] = event
    merged: dict[str, dict[str, Any]] = {}
    for event in best_by_source.values():
        event_id = event["event_id"]
        if event_id not in merged:
            merged[event_id] = {**event, "evidence_source_ids": list(event["evidence_source_ids"])}
            continue
        current = merged[event_id]
        current["evidence_source_ids"].extend(event["evidence_source_ids"])
        if STATUS_ORDER.get(event["status"], 0) > STATUS_ORDER.get(current["status"], 0):
            current["status"] = event["status"]
        if event.get("distribution_base_shares") is not None:
            current["distribution_base_shares"] = event["distribution_base_shares"]
        for field in ["record_date", "ex_dividend_date", "payment_date", "available_date"]:
            if event.get(field):
                current[field] = event[field]
    return list(merged.values())


def parse_policy_facts(
    text: str,
    *,
    source: dict[str, Any],
    ts_code: str,
) -> dict[str, Any]:
    original_text = text
    text = re.sub(r"\s+", "", text)
    period = re.search(
        r"(20\d{2})年?[至—–－-](20\d{2})年",
        text + re.sub(r"\s+", "", str(source.get("source_title") or "")),
    )
    duration = re.search(r"从\s*(20\d{2})\s*年起[，,]?\s*([一二三四五六七八九十\d]+)\s*年内", text)
    ratio = re.search(
        r"(?:以现金方式分配的利润|现金分红比例|现金分红)[^。；%]{0,100}?(?:不低于|不少于|至少)"
        r"[^0-9]{0,12}([0-9]+(?:\.[0-9]+)?)\s*%",
        text,
    )
    if ratio is None and (period or duration):
        ratio = re.search(
            r"(?:以现金方式分配的利润|现金分红比例|现金分红)[^。；%]{0,100}?(?:达到|提升至)"
            r"[^0-9]{0,12}([0-9]+(?:\.[0-9]+)?)\s*%",
            text,
        )
    if ratio is None:
        ratio = re.search(
            r"(?:不低于|至少)[^。；%]{0,80}?([0-9]+(?:\.[0-9]+)?)\s*%[^。；]{0,30}现金分红",
            text,
        )
    stable_future = re.search(r"(20\d{2})\s*年派息率将稳中有升", text)
    prior_rate = re.search(
        r"20\d{2}\s*年全年派息率为\s*([0-9]+(?:\.[0-9]+)?)\s*%",
        text,
    )
    facts: dict[str, Any] = {"ts_code": ts_code}
    policy_context = text + re.sub(r"\s+", "", str(source.get("policy_context") or ""))
    includes_buybacks = bool(re.search(r"回购[^。；]{0,100}(?:视同|纳入)[^。；]{0,30}(?:现金分红|分红)", policy_context))
    # These are not annual cash dividends / parent net profit. Retain the
    # unsupported basis as evidence instead of silently treating it as DPS policy.
    distributable = re.search(r"(?:不低于|不少于|至少)[^。；]{0,25}最近三年[^。；]{0,20}年均可分配利润的([0-9.]+)%", text)
    distribution = re.search(r"现金分红在[^。；]{0,20}利润分配中[^。；]{0,30}?([0-9]+(?:\.[0-9]+)?)%", text)
    constraint = distributable or distribution or (ratio if includes_buybacks else None)
    if constraint:
        facts["payout_policy_constraint"] = {
            "value": float(constraint.group(1)) / 100,
            "unit": "ratio",
            "denominator_basis": (
                "three_year_average_distributable_profit" if distributable else
                "total_profit_distribution" if distribution else "parent_net_profit"
            ),
            "includes_buybacks": includes_buybacks,
            "valid_from": int(period.group(1)) if period else None,
            "valid_to": int(period.group(2)) if period else None,
            "source_document_id": str(source.get("announcement_id") or ""),
            "source_file": str(source.get("source_file") or ""),
            "source_page_or_location": source.get("page"),
            "raw_evidence": original_text.strip(),
        }
    if includes_buybacks or (ratio and re.search(r"可分配利润|利润分配中", ratio.group(0))):
        return facts
    if ratio or (stable_future and prior_rate):
        value = float(ratio.group(1) if ratio else prior_rate.group(1)) / 100.0
        if period:
            valid_from, valid_to = int(period.group(1)), int(period.group(2))
        elif duration:
            chinese_numbers = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
            years = chinese_numbers.get(duration.group(2), int(duration.group(2)) if duration.group(2).isdigit() else 0)
            valid_from = int(duration.group(1))
            valid_to = valid_from + years - 1
        else:
            valid_from = int(stable_future.group(1)) if stable_future else None
            valid_to = valid_from
        facts["official_payout_floor"] = {
            "value": round(value, 8),
            "unit": "ratio",
            "valid_from": valid_from,
            "valid_to": valid_to,
            "source_document_id": str(source.get("announcement_id") or ""),
            "source_file": str(source.get("source_file") or ""),
            "source_page_or_location": source.get("page"),
            "raw_evidence": original_text.strip(),
        }
    return facts


def parse_historical_payout_fact(
    text: str,
    *,
    source: dict[str, Any],
    ts_code: str,
    period: str,
) -> dict[str, Any] | None:
    match = re.search(
        r"(?:本年度(?:公司)?|年度本公司|基于[^。；]{0,30}计算的)?(?:现金分红比例|全年派息率)\s*(?:为|达到|[:：])?\s*"
        r"([0-9]+(?:\.[0-9]+)?)\s*%",
        text,
    )
    table_evidence = None
    if not match:
        # Shenzhen reports put the ratio heading above the year-labelled rows.
        # A row for 2024 in a 2025 report must remain a 2024 fact.
        heading = re.search(r"现金分红总额占归属于上市公司股[\s\S]{0,120}?东的净利润比例", text)
        if not heading:
            return None
        rows = list(re.finditer(r"(?m)^\s*(20\d{2})\s+[^\n%]+?\s+([0-9]+(?:\.[0-9]+)?)\s*%", text[heading.end():]))
        rows = [row for row in rows if not period or row.group(1) <= period[:4]]
        if not rows:
            return None
        row = max(rows, key=lambda item: item.group(1))
        period = row.group(1) + "1231"
        value = float(row.group(2)) / 100
        table_evidence = heading.group(0) + "\n" + row.group(0)
    else:
        value = float(match.group(1)) / 100
    return {
        "value": round(value, 8),
        "unit": "ratio",
        "period": period,
        "source_document_id": str(source.get("announcement_id") or ""),
        "source_file": str(source.get("source_file") or ""),
        "source_page_or_location": source.get("page"),
        "reporting_entity": ts_code,
        "raw_evidence": table_evidence or match.group(0),
    }


def _numeric(value: str) -> float:
    return float(value.replace(",", ""))


def _metric_multiplier(line: str, page_text: str) -> float | None:
    if "亿元" in line or "億元" in line:
        return 100_000_000.0
    if "百万元" in line or "百萬元" in line:
        return 1_000_000.0
    if "千元" in line or "千元" in line:
        return 1_000.0
    if "万元" in line or "萬元" in line:
        return 10_000.0
    if re.search(r"[（(]?元[）)]?", line):
        return 1.0
    header = "\n".join(page_text.splitlines()[:30])
    if "百万元" in header or "百萬元" in header:
        return 1_000_000.0
    if "千元" in header:
        return 1_000.0
    if "亿元" in header or "億元" in header:
        return 100_000_000.0
    if "万元" in header or "萬元" in header:
        return 10_000.0
    if re.search(r"单位\s*[:：]\s*(?:人民币)?元", header):
        return 1.0
    return None


def _extract_metric_series(text: str, labels: list[str]) -> tuple[list[float], str] | None:
    if re.search(r"第一季度[\s\S]{0,150}第二季度", text):
        return None  # A quarterly breakdown is not an annual/comparable series.
    lines = text.splitlines()
    for index, line in enumerate(lines):
        label = next((candidate for candidate in labels if candidate in line), None)
        if not label:
            continue
        segment = line.split(label, 1)[1]
        combined = line
        if not re.search(r"[0-9]", segment):
            # Allow a metric label followed by a numeric row, but not a column
            # header followed by years, another metric, or a page footer.
            if not re.fullmatch(r"[\s（()）人民币百千万亿元]*", segment):
                continue
            following = next((item for item in lines[index + 1:index + 4] if item.strip()), "")
            if not re.match(r"\s*[+-]?[0-9]", following):
                continue
            segment += " " + following
            combined += "\n" + following
        if re.search(r"20\d{2}\s*年", segment):
            continue
        first_number = re.search(r"[+-]?[0-9]", segment)
        if not first_number or not re.fullmatch(r"[\s（()）人民币百千万亿元为是:：]*", segment[:first_number.start()]):
            continue
        value_segment = re.split(r"同比|较上年|增长|下降", segment, maxsplit=1)[0]
        value_segment = re.sub(r"[+-]?[0-9][0-9,]*(?:\.[0-9]+)?\s*%", "", value_segment)
        numbers = re.findall(r"[+-]?[0-9][0-9,]*(?:\.[0-9]+)?", value_segment)
        if any(token in value_segment for token in ["分配股息", "派发股息", "现金分红", "合计"]):
            numbers = numbers[:1]
        multiplier = _metric_multiplier(combined, text)
        if not numbers or multiplier is None:
            continue
        return ([round(_numeric(number) * multiplier, 4) for number in numbers], combined.strip())
    return None


def parse_financial_facts(
    text: str,
    *,
    source: dict[str, Any],
    ts_code: str,
    period: str,
) -> dict[str, Any]:
    profit = _extract_metric_series(
        text,
        [
            "归属于上市公司股东的净利润",
            "归属于母公司股东的净利润",
            "归属于本行股东的净利润",
            "归属于本行普通股股东的净利润",
            "歸屬於本行股東的淨利潤",
            "歸屬於本行普通股股東的淨利潤",
            "归属于公司股东的净利润",
        ],
    )
    shares = re.search(
        r"(?:期末总股本|普通股总股本|总股本|股份总数)\s*(?:[（(]股[）)]\s*)?([0-9][0-9,]*)\s*股?",
        text,
    )
    if shares is None:
        shares = re.search(r"([0-9][0-9,]*)\s*股?普通股为基数", text)
    operating_profit = _extract_metric_series(
        text,
        ["归属于母公司股东的营运利润", "归属于上市公司股东的营运利润"],
    )
    common = {
        "period": period,
        "source_document_id": str(source.get("announcement_id") or ""),
        "source_file": str(source.get("source_file") or ""),
        "source_page_or_location": source.get("page"),
        "reporting_entity": ts_code,
        "share_class": "A",
    }
    facts: dict[str, Any] = {"ts_code": ts_code}
    if profit:
        profit_values, profit_evidence = profit
        facts["net_profit_parent"] = {
            **common,
            "value": profit_values[0],
            "unit": "CNY",
            "raw_evidence": profit_evidence,
        }
        if len(profit_values) > 1 and len(period) == 8:
            prior_period = f"{int(period[:4]) - 1}{period[4:]}"
            facts["net_profit_parent_prior_comparable"] = {
                **common,
                "period": prior_period,
                "value": profit_values[1],
                "unit": "CNY",
                "raw_evidence": profit_evidence,
            }
    if shares:
        facts["total_shares"] = {
            **common,
            "value": int(_numeric(shares.group(1))),
            "unit": "share",
            "raw_evidence": shares.group(0),
        }
    if operating_profit:
        operating_values, operating_evidence = operating_profit
        facts["operating_profit_parent"] = {
            **common,
            "value": operating_values[0],
            "unit": "CNY",
            "raw_evidence": operating_evidence,
        }
        if len(operating_values) > 1 and len(period) == 8:
            facts["operating_profit_parent_prior_comparable"] = {
                **common,
                "period": f"{int(period[:4]) - 1}{period[4:]}",
                "value": operating_values[1],
                "unit": "CNY",
                "raw_evidence": operating_evidence,
            }
    return facts


def _period_from_document(document: dict[str, Any]) -> str:
    match = re.search(r"(20\d{2})", str(document.get("source_title") or ""))
    if not match:
        return ""
    year = match.group(1)
    return f"{year}0630" if document.get("logical_role") == "interim" else f"{year}1231"


def _fact_record(
    fact_type: str,
    fact: dict[str, Any],
    *,
    ts_code: str,
) -> dict[str, Any]:
    span_identity = (
        f"{fact.get('source_document_id')}|{fact.get('source_page_or_location')}|"
        f"{fact.get('raw_evidence')}|{EXTRACTOR_VERSION}"
    )
    evidence_span_id = hashlib.sha256(span_identity.encode("utf-8")).hexdigest()[:24]
    identity = (
        f"{ts_code}|{fact_type}|{fact.get('period')}|{fact.get('source_document_id')}|"
        f"{fact.get('source_page_or_location')}|{fact.get('value')}|{fact.get('unit')}"
    )
    if fact.get("distribution_phase"):
        identity += "|" + str(fact["distribution_phase"])
    return {
        "fact_id": hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24],
        "fact_type": fact_type,
        "evidence_span_id": evidence_span_id,
        **fact,
    }


def parse_company_evidence(
    manifest_path: Path,
    *,
    page_provider=extract_document_pages,
) -> dict[str, Any]:
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    ts_code = str(manifest["ts_code"])
    facts: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    pages_by_id = {}
    for document in manifest.get("documents", []):
        role = document.get("logical_role")
        period = _period_from_document(document)
        previous_unit_context = ""
        pages = list(page_provider(document, manifest_path.parent))
        pages_by_id[str(document["announcement_id"])] = pages
        policy_context = "\n".join(str(page.get("text") or "") for page in pages) if role == "dividend_policy" else ""
        for page in pages:
            source = {
                **document,
                "page": page.get("page"),
                "policy_context": policy_context,
            }
            text = str(page.get("text") or "")
            financial_text = f"{previous_unit_context}\n{text}" if previous_unit_context else text
            parsed = parse_financial_facts(
                financial_text,
                source=source,
                ts_code=ts_code,
                period=period,
            )
            for fact_type in [
                "net_profit_parent",
                "net_profit_parent_prior_comparable",
                "operating_profit_parent",
                "operating_profit_parent_prior_comparable",
                "total_shares",
            ]:
                if fact_type in parsed:
                    facts.append(_fact_record(fact_type, parsed[fact_type], ts_code=ts_code))
            if role in {"dividend_plan", "implementation"}:
                try:
                    events.append(
                        parse_dividend_event(
                            text,
                            source=source,
                            ts_code=ts_code,
                            fiscal_period=period,
                        )
                    )
                except ValueError:
                    pass
            policy_parsed = parse_policy_facts(text, source=source, ts_code=ts_code)
            if "official_payout_floor" in policy_parsed:
                policy = {**policy_parsed["official_payout_floor"], "period": period}
                facts.append(_fact_record("official_payout_floor", policy, ts_code=ts_code))
            if "payout_policy_constraint" in policy_parsed:
                constraint = {**policy_parsed["payout_policy_constraint"], "period": period}
                facts.append(_fact_record("payout_policy_constraint", constraint, ts_code=ts_code))
            payout = parse_historical_payout_fact(
                text,
                source=source,
                ts_code=ts_code,
                period=period,
            )
            if payout:
                facts.append(_fact_record("historical_payout_ratio", payout, ts_code=ts_code))
            unit_lines = [
                line
                for line in text.splitlines()
                if "单位" in line and any(unit in line for unit in ["元", "萬元", "百萬元", "亿元", "億元"])
            ]
            previous_unit_context = "\n".join(unit_lines[-3:])
    for fact in extract_history_facts(manifest.get("documents", []), pages_by_id):
        facts.append(_fact_record(fact["fact_type"], {**fact, "reporting_entity": ts_code}, ts_code=ts_code))
    evidence_spans_by_id: dict[str, dict[str, Any]] = {}
    for fact in facts:
        span_id = str(fact["evidence_span_id"])
        evidence_spans_by_id.setdefault(
            span_id,
            {
                "evidence_span_id": span_id,
                "source_document_id": fact.get("source_document_id"),
                "source_page_or_location": fact.get("source_page_or_location"),
                "raw_evidence": fact.get("raw_evidence"),
                "extraction_backend": EXTRACTOR_VERSION,
            },
        )
    result = {
        "ts_code": ts_code,
        "company_name": manifest.get("company_name", ""),
        "run_date": manifest.get("run_date", ""),
        "stage_status": manifest.get("status", "normal"),
        "stage_reason": manifest.get("reason", ""),
        "source_documents": manifest.get("documents", []),
        "evidence_spans": list(evidence_spans_by_id.values()),
        "normalized_facts": facts,
        "dividend_events": merge_dividend_events(events),
    }
    atomic_write_text(
        manifest_path.parent / "forecast-evidence.json",
        json.dumps(result, ensure_ascii=False, indent=2),
    )
    return result
