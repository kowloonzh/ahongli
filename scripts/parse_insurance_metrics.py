#!/usr/bin/env python3
"""Parse auditable insurance dividend-safety metrics from periodic reports."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any


EXTRACTOR_VERSION = "insurance-report-layout-regex-v1"
MANDATORY_ANNUAL_FIELDS = [
    "group_core_solvency_ratio",
    "group_comprehensive_solvency_ratio",
    "group_core_capital",
    "group_actual_capital",
    "group_minimum_capital",
    "parent_available_funds_end",
    "parent_subsidiary_dividends_received",
    "parent_external_dividends_paid",
    "operating_profit_parent",
    "operating_profit_payout_ratio",
]
MANDATORY_LATEST_FIELDS = [
    "group_core_solvency_ratio",
    "group_comprehensive_solvency_ratio",
    "group_core_capital",
    "group_actual_capital",
    "group_minimum_capital",
]


def _numbers(text: str) -> list[float]:
    values: list[float] = []
    for raw in re.findall(r"\(?-?\d[\d,]*(?:\.\d+)?\)?", text):
        negative = raw.startswith("(") and raw.endswith(")")
        cleaned = raw.strip("()").replace(",", "")
        value = float(cleaned)
        values.append(-value if negative else value)
    return values


def _evidence(
    *, source_file: str, source_document_id: str, available_date: str,
    page: int, raw: str, definition: str, reporting_entity: str,
) -> dict[str, Any]:
    normalized = re.sub(r"\s+", " ", raw).strip()[:1000]
    identity = "|".join(
        [source_document_id, str(page), definition, reporting_entity, normalized]
    )
    return {
        "evidence_id": hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24],
        "source_file": source_file,
        "source_document_id": source_document_id,
        "available_date": available_date,
        "page": page,
        "raw_evidence": normalized,
        "definition": definition,
        "reporting_entity": reporting_entity,
        "extractor_version": EXTRACTOR_VERSION,
    }


def _find_label_value(
    pages: list[str], label: str, *, section: str = "",
) -> tuple[float | None, int | None, str]:
    candidates: list[tuple[int, int, float, str]] = []
    for page_number, page in enumerate(pages, start=1):
        if section and section not in page:
            continue
        lines = page.splitlines()
        for index, line in enumerate(lines):
            if label not in line:
                continue
            values = _numbers(line.split(label, 1)[1])
            if not values:
                continue
            context = " ".join(part.strip() for part in lines[max(0, index - 2):index + 2])
            score = (100 if line.strip().startswith(label) else 0) + (30 if section else 0)
            candidates.append((score, -page_number, values[0], context))
    if not candidates:
        return None, None, ""
    _, negative_page, value, raw = max(candidates, key=lambda item: (item[0], item[1]))
    return value, -negative_page, raw


def _find_group_solvency(pages: list[str]) -> tuple[dict[str, float], dict[str, tuple[int, str]]]:
    aliases = {
        "group_core_capital": "核心资本",
        "group_actual_capital": "实际资本",
        "group_minimum_capital": "最低资本",
        "group_core_solvency_ratio": "核心偿付能力充足率",
        "group_comprehensive_solvency_ratio": "综合偿付能力充足率",
    }
    result: dict[str, float] = {}
    evidence: dict[str, tuple[int, str]] = {}
    for field, label in aliases.items():
        value, page, raw = _find_label_value(pages, label, section="集团偿付能力")
        if value is None:
            value, page, raw = _find_label_value(pages, label)
        if value is not None and page is not None:
            result[field] = value
            evidence[field] = (page, raw)
    return result, evidence


def _find_material_subsidiaries(pages: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    best: tuple[int, list[dict[str, Any]], dict[str, Any]] | None = None
    for page_number, page in enumerate(pages, start=1):
        lines = page.splitlines()
        for index, line in enumerate(lines):
            entities = re.findall(r"[\u4e00-\u9fff]{2,12}(?:寿险|产险|养老险|健康险)", line)
            if len(entities) < 2 or "集团" not in line:
                continue
            window = lines[index:index + 18]
            rows: dict[str, list[float]] = {}
            for candidate in window:
                for key, label in [
                    ("core_capital", "核心资本"),
                    ("actual_capital", "实际资本"),
                    ("minimum_capital", "最低资本"),
                    ("core_solvency_ratio", "核心偿付能力充足率"),
                    ("comprehensive_solvency_ratio", "综合偿付能力充足率"),
                ]:
                    if candidate.strip().startswith(label):
                        rows[key] = _numbers(candidate.split(label, 1)[1])
            column_count = 1 + len(entities)
            if not all(len(rows.get(key, [])) >= column_count for key in rows):
                continue
            if not all(key in rows for key in ["core_solvency_ratio", "comprehensive_solvency_ratio"]):
                continue
            records = []
            for position, entity in enumerate(entities, start=1):
                records.append({
                    "reporting_entity": entity,
                    **{
                        key: values[position] * 1_000_000
                        if key in {"core_capital", "actual_capital", "minimum_capital"}
                        else values[position]
                        for key, values in rows.items()
                    },
                    "capital_currency": "CNY",
                })
            raw = re.sub(r"\s+", " ", "\n".join(window)).strip()[:1600]
            score = len(rows) * 10 + len(records)
            candidate = (score, records, {"page": page_number, "raw_evidence": raw})
            if best is None or candidate[0] > best[0]:
                best = candidate
    if best is not None:
        return best[1], best[2]

    records: list[dict[str, Any]] = []
    evidence_parts: list[str] = []
    evidence_page = 0
    for page_number, page in enumerate(pages, start=1):
        if "偿付能力" not in page:
            continue
        lines = page.splitlines()
        header_entities: list[str] = []
        for line in lines:
            found = re.findall(r"[\u4e00-\u9fff]{2,12}(?:寿险|产险|养老险|健康险)", line)
            if len(found) > len(header_entities):
                header_entities = found
        if header_entities:
            rows: dict[str, list[float]] = {}
            for line in lines:
                for key, label in [
                    ("core_capital", "核心资本"), ("actual_capital", "实际资本"),
                    ("minimum_capital", "最低资本"),
                    ("core_solvency_ratio", "核心偿付能力充足率"),
                    ("comprehensive_solvency_ratio", "综合偿付能力充足率"),
                ]:
                    if line.strip().startswith(label):
                        rows[key] = _numbers(line.split(label, 1)[1])
            if all(len(rows.get(key, [])) >= 2 * len(header_entities) for key in [
                "core_solvency_ratio", "comprehensive_solvency_ratio"
            ]):
                for position, entity in enumerate(header_entities):
                    records.append({
                        "reporting_entity": entity,
                        **{
                            key: values[position * 2] * 1_000_000
                            if key in {"core_capital", "actual_capital", "minimum_capital"}
                            else values[position * 2]
                            for key, values in rows.items()
                            if len(values) >= 2 * len(header_entities)
                        },
                        "capital_currency": "CNY",
                    })
                evidence_parts.append(page)
                evidence_page = evidence_page or page_number
                continue
        entity_match = re.search(
            r"截至20\d{2}年\d{1,2}月\d{1,2}日[，,]([^，,。]{2,16}(?:寿险|产险|养老险|健康险))的",
            page,
        )
        if entity_match:
            entity = entity_match.group(1)
            values: dict[str, float] = {}
            for key, label in [
                ("core_capital", "核心资本"), ("actual_capital", "实际资本"),
                ("minimum_capital", "最低资本"),
                ("core_solvency_ratio", "核心偿付能力充足率"),
                ("comprehensive_solvency_ratio", "综合偿付能力充足率"),
            ]:
                value, _, _ = _find_label_value([page], label)
                if value is not None:
                    values[key] = value
            if "core_solvency_ratio" in values and "comprehensive_solvency_ratio" in values:
                records.append({
                    "reporting_entity": entity,
                    **{
                        key: value * 1_000_000
                        if key in {"core_capital", "actual_capital", "minimum_capital"}
                        else value
                        for key, value in values.items()
                    },
                    "capital_currency": "CNY",
                })
                evidence_parts.append(page)
                evidence_page = evidence_page or page_number
    deduped: dict[str, dict[str, Any]] = {}
    for record in records:
        # Issuer tables normally put the reporting-period column before the
        # comparative column. Keep that first occurrence for each entity.
        deduped.setdefault(record["reporting_entity"], record)
    if not deduped:
        return [], {}
    return list(deduped.values()), {
        "page": evidence_page,
        "raw_evidence": re.sub(r"\s+", " ", " ".join(evidence_parts)).strip()[:1600],
    }


def _find_stress_scenarios(pages: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    scenarios: list[dict[str, Any]] = []
    evidence: dict[str, Any] = {}
    for page_number, page in enumerate(pages, start=1):
        if "偿付能力充足率的影响" not in page:
            continue
        lines = page.splitlines()
        header_index = max(
            range(len(lines)),
            key=lambda i: len(re.findall(r"[\u4e00-\u9fff]{2,12}(?:集团|寿险|产险)", lines[i])),
            default=-1,
        )
        if header_index >= 0 and len(re.findall(
            r"[\u4e00-\u9fff]{2,12}(?:集团|寿险|产险)", lines[header_index]
        )) < 3:
            header_index = None
        if header_index is None:
            continue
        header = lines[header_index]
        entities = re.findall(r"[\u4e00-\u9fff]{2,12}(?:集团|寿险|产险)", header)
        unique_entities = []
        for entity in entities:
            if entity not in unique_entities:
                unique_entities.append(entity)
        if len(unique_entities) < 3:
            unique_entities = ["保险集团", "主要寿险子公司", "主要产险子公司"]
        else:
            unique_entities = unique_entities[:3]
        for line in lines[header_index + 1:header_index + 12]:
            if not any(token in line for token in ["利率下降", "权益资产"]):
                continue
            changes = [
                (-float(value) if direction == "下降" else float(value))
                for direction, value in re.findall(r"(下降|上升)\s*([0-9]+(?:\.[0-9]+)?)\s*个百分点", line)
            ]
            if len(changes) < 6:
                continue
            if "利率下降" in line:
                scenario_match = re.search(r"当期利率下降\s*[0-9]+(?:\.[0-9]+)?\s*个基点", line)
                scenario_name = scenario_match.group(0).replace(" ", "") if scenario_match else "当期利率下降（幅度见原文）"
            else:
                scenario_match = re.search(r"权益资产公允价值下跌\s*[0-9]+(?:\.[0-9]+)?%", line)
                scenario_name = scenario_match.group(0).replace(" ", "") if scenario_match else "权益资产公允价值下跌（幅度见原文）"
            for position, entity in enumerate(unique_entities):
                scenarios.append({
                    "scenario": scenario_name,
                    "reporting_entity": entity,
                    "core_change_pct_points": changes[position],
                    "comprehensive_change_pct_points": changes[position + 3],
                    "combination_rule": "standalone_only",
                })
            evidence = {
                "page": page_number,
                "raw_evidence": re.sub(r"\s+", " ", " ".join(lines[header_index:header_index + 12])).strip()[:1600],
            }
    deduped = {
        (item["scenario"], item["reporting_entity"]): item
        for item in scenarios
    }
    return list(deduped.values()), evidence


def _find_risk_rating(pages: list[str]) -> tuple[str | None, int | None, str]:
    pattern = re.compile(
        r"风险综合评级(?:结果)?(?:为|是|：|:)?\s*([ABCD](?:\+{1,2}|-)?)类?",
        re.IGNORECASE,
    )
    for page_number, page in enumerate(pages, start=1):
        match = pattern.search(page)
        if match:
            return match.group(1).upper(), page_number, match.group(0)
    return None, None, ""


def _find_dividend_restriction(pages: list[str]) -> tuple[bool | None, int | None, str]:
    pattern = re.compile(
        r"(?:监管机构|金融监管总局|国家金融监督管理总局)[^。；\n]{0,80}"
        r"(?:限制|禁止|暂停)[^。；\n]{0,30}(?:分红|利润分配)"
    )
    for page_number, page in enumerate(pages, start=1):
        match = pattern.search(page)
        if match:
            return True, page_number, match.group(0)
    return None, None, ""


def _find_operating_profit(pages: list[str]) -> tuple[float | None, int | None, str]:
    pattern = re.compile(
        r"归属于母公司股东的营运利润\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*亿元"
    )
    for page_number, page in enumerate(pages, start=1):
        match = pattern.search(page)
        if match:
            return float(match.group(1).replace(",", "")) * 100_000_000, page_number, match.group(0)
    return None, None, ""


def _find_operating_payout(pages: list[str], year: int) -> tuple[float | None, int | None, str]:
    narrative = re.compile(
        r"(?:基于|按)归母营运利润计算的现金分红比例为\s*([0-9]+(?:\.[0-9]+)?)%"
    )
    for page_number, page in enumerate(pages, start=1):
        if "按归母营运利润计算" in page:
            line = next(
                (line for line in page.splitlines() if re.match(rf"^\s*{year}年\s+", line)),
                "",
            )
            values = _numbers(re.sub(rf"^\s*{year}年", "", line))
            if len(values) >= 4:
                return values[3] / 100, page_number, line
        match = narrative.search(page)
        if match and year:
            return float(match.group(1)) / 100, page_number, match.group(0)
    return None, None, ""


def parse_insurance_metrics(
    text: str, *, ts_code: str = "", period: str = "", report_type: str = "annual",
    source_file: str = "", source_original_file: str = "",
    source_document_id: str = "", available_date: str = "",
) -> dict[str, Any]:
    pages = str(text or "").split("\f")
    year = int(period[:4]) if len(period) >= 4 and period[:4].isdigit() else 0
    result: dict[str, Any] = {
        "ts_code": ts_code,
        "period": period,
        "report_type": report_type,
        "available_date": available_date,
        "source_document_id": source_document_id,
        "source_file": source_file,
    }
    metric_evidence: dict[str, Any] = {}
    group, group_evidence = _find_group_solvency(pages)
    result.update(group)
    for field in ["group_core_capital", "group_actual_capital", "group_minimum_capital"]:
        if field in result:
            result[field] *= 1_000_000
    for field, (page, raw) in group_evidence.items():
        metric_evidence[field] = _evidence(
            source_file=source_file, source_document_id=source_document_id,
            available_date=available_date, page=page, raw=raw,
            definition=field, reporting_entity="insurance_group",
        )

    parent_labels = {
        "parent_available_funds_start": "期初可动用资金余额",
        "parent_subsidiary_dividends_received": "子公司分红",
        "parent_external_dividends_paid": "集团对外分红",
        "parent_subsidiary_investments": "对子公司的投资",
        "parent_financing_proceeds": "所得款净额",
        "parent_other_cashflows": "其他",
        "parent_available_funds_end": "期末可动用资金余额",
    }
    for field, label in parent_labels.items():
        value, page, raw = _find_label_value(pages, label, section="集团母公司可动用资金")
        if value is None or page is None:
            continue
        if field in {"parent_external_dividends_paid", "parent_subsidiary_investments"}:
            value = abs(value)
        result[field] = value * 1_000_000
        metric_evidence[field] = _evidence(
            source_file=source_file, source_document_id=source_document_id,
            available_date=available_date, page=page, raw=raw,
            definition=field + " (CNY; disclosed table unit CNY million)",
            reporting_entity="group_parent",
        )

    subsidiaries, subsidiary_evidence = _find_material_subsidiaries(pages)
    result["material_subsidiaries_json"] = json.dumps(subsidiaries, ensure_ascii=False, sort_keys=True)
    if subsidiary_evidence:
        metric_evidence["material_subsidiaries"] = _evidence(
            source_file=source_file, source_document_id=source_document_id,
            available_date=available_date, page=subsidiary_evidence["page"],
            raw=subsidiary_evidence["raw_evidence"], definition="material insurance subsidiary capital and solvency",
            reporting_entity="material_insurance_subsidiaries",
        )

    stresses, stress_evidence = _find_stress_scenarios(pages)
    result["stress_scenarios_json"] = json.dumps(stresses, ensure_ascii=False, sort_keys=True)
    if stress_evidence:
        metric_evidence["stress_scenarios"] = _evidence(
            source_file=source_file, source_document_id=source_document_id,
            available_date=available_date, page=stress_evidence["page"], raw=stress_evidence["raw_evidence"],
            definition="issuer disclosed standalone solvency sensitivity; scenarios must not be combined",
            reporting_entity="insurance_group_and_material_subsidiaries",
        )

    rating, page, raw = _find_risk_rating(pages)
    if rating is not None and page is not None:
        result["risk_comprehensive_rating"] = rating
        metric_evidence["risk_comprehensive_rating"] = _evidence(
            source_file=source_file, source_document_id=source_document_id,
            available_date=available_date, page=page, raw=raw,
            definition="publicly disclosed risk comprehensive rating",
            reporting_entity="applicable_insurance_entity",
        )
    restriction, page, raw = _find_dividend_restriction(pages)
    if restriction is not None and page is not None:
        result["regulatory_dividend_restriction"] = restriction
        metric_evidence["regulatory_dividend_restriction"] = _evidence(
            source_file=source_file, source_document_id=source_document_id,
            available_date=available_date, page=page, raw=raw,
            definition="explicit issuer-specific regulatory dividend restriction",
            reporting_entity="applicable_insurance_entity",
        )

    operating_profit, page, raw = _find_operating_profit(pages)
    if operating_profit is not None and page is not None:
        result["operating_profit_parent"] = operating_profit
        metric_evidence["operating_profit_parent"] = _evidence(
            source_file=source_file, source_document_id=source_document_id,
            available_date=available_date, page=page, raw=raw,
            definition="issuer-defined parent-attributable operating profit", reporting_entity="insurance_group",
        )
    payout, page, raw = _find_operating_payout(pages, year)
    if payout is not None and page is not None:
        result["operating_profit_payout_ratio"] = payout
        metric_evidence["operating_profit_payout_ratio"] = _evidence(
            source_file=source_file, source_document_id=source_document_id,
            available_date=available_date, page=page, raw=raw,
            definition="cash dividends / parent-attributable operating profit", reporting_entity="insurance_group",
        )

    if all(result.get(field) is not None for field in [
        "parent_available_funds_start", "parent_subsidiary_dividends_received",
        "parent_external_dividends_paid", "parent_available_funds_end",
    ]):
        start = float(result["parent_available_funds_start"])
        received = float(result["parent_subsidiary_dividends_received"])
        paid = float(result["parent_external_dividends_paid"])
        investments = float(result.get("parent_subsidiary_investments") or 0)
        financing = float(result.get("parent_financing_proceeds") or 0)
        other = float(result.get("parent_other_cashflows") or 0)
        expected_end = start + received + financing - paid - investments + other
        actual_end = float(result["parent_available_funds_end"])
        tolerance = max(1_000_000.0, abs(actual_end) * 0.0001)
        result["parent_funds_bridge_residual"] = actual_end - expected_end
        result["parent_funds_bridge_reconciled"] = abs(actual_end - expected_end) <= tolerance
        result["subsidiary_dividend_coverage"] = received / paid if paid > 0 else None
        result["ending_available_funds_coverage"] = actual_end / paid if paid > 0 else None
        result["financing_share_of_external_dividends"] = financing / paid if paid > 0 else None

    required = MANDATORY_ANNUAL_FIELDS if report_type == "annual" else MANDATORY_LATEST_FIELDS
    missing = [field for field in required if result.get(field) is None]
    if report_type == "annual" and not subsidiaries:
        missing.append("material_subsidiaries")
    def file_hash(path_value: str) -> str:
        path = Path(path_value) if path_value else None
        return hashlib.sha256(path.read_bytes()).hexdigest() if path and path.exists() else ""

    source_sha256 = file_hash(source_original_file)
    extracted_text_sha256 = file_hash(source_file)
    for item in metric_evidence.values():
        item["source_original_file"] = source_original_file
        item["source_sha256"] = source_sha256
        item["extracted_text_sha256"] = extracted_text_sha256
    result["source_original_file"] = source_original_file
    result["source_sha256"] = source_sha256
    result["extracted_text_sha256"] = extracted_text_sha256
    result["missing_metrics"] = missing
    result["data_quality"] = "normal" if not missing else "data_gap"
    result["metric_evidence_json"] = json.dumps(metric_evidence, ensure_ascii=False, sort_keys=True)
    return result


def write_csv(records: list[dict[str, Any]], path: Path) -> None:
    fields = [
        "ts_code", "period", "report_type", "available_date", "source_document_id",
        "group_core_capital", "group_actual_capital", "group_minimum_capital",
        "group_core_solvency_ratio", "group_comprehensive_solvency_ratio",
        "material_subsidiaries_json", "risk_comprehensive_rating",
        "parent_available_funds_start", "parent_subsidiary_dividends_received",
        "parent_external_dividends_paid", "parent_subsidiary_investments",
        "parent_financing_proceeds", "parent_other_cashflows", "parent_available_funds_end",
        "parent_funds_bridge_residual", "parent_funds_bridge_reconciled",
        "subsidiary_dividend_coverage", "ending_available_funds_coverage",
        "financing_share_of_external_dividends", "operating_profit_parent",
        "operating_profit_payout_ratio", "stress_scenarios_json",
        "regulatory_dividend_restriction", "data_quality", "missing_metrics",
        "source_file", "source_original_file", "source_sha256", "extracted_text_sha256",
        "metric_evidence_json",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            row = dict(record)
            row["missing_metrics"] = json.dumps(row.get("missing_metrics", []), ensure_ascii=False)
            writer.writerow({field: row.get(field, "") for field in fields})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("--ts-code", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    records = []
    for path in args.reports:
        year = re.search(r"(20\d{2})", path.name)
        period = f"{year.group(1)}1231" if year else ""
        records.append(parse_insurance_metrics(
            path.read_text(encoding="utf-8", errors="replace"),
            ts_code=args.ts_code, period=period, source_file=str(path.resolve()),
        ))
    write_csv(records, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
