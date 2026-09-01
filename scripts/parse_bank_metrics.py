#!/usr/bin/env python3
"""Parse auditable bank-quality metrics from extracted periodic-report text."""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any


REQUIRED_METRICS = [
    "npl_ratio",
    "provision_coverage_ratio",
    "loan_provision_ratio",
    "net_interest_margin",
    "cost_income_ratio",
    "core_tier1_capital_ratio",
    "tier1_capital_ratio",
    "capital_adequacy_ratio",
]

METRIC_ALIASES = {
    "npl_ratio": ["不良贷款率", "不良贷款比率"],
    "provision_coverage_ratio": ["拨备覆盖率"],
    "loan_provision_ratio": ["贷款拨备率", "拨贷比", "拨备率"],
    "net_interest_margin": ["净息差", "净利息收益率"],
    "cost_income_ratio": ["成本收入比"],
    "core_tier1_capital_ratio": ["核心一级资本充足率", "核心一级资本比率"],
    "tier1_capital_ratio": ["一级资本充足率", "一级资本比率"],
    "capital_adequacy_ratio": ["资本充足率", "资本比率"],
    "attention_loan_ratio": ["关注类贷款占比", "关注类贷款比例"],
    "overdue_loan_ratio": ["逾期贷款率", "逾期贷款比率"],
    "real_estate_npl_ratio": ["房地产业不良贷款率", "房地产贷款不良率"],
    "personal_loan_npl_ratio": ["个人贷款不良率"],
}

TRADITIONAL_TRANSLATION = str.maketrans(
    {
        "貸": "贷",
        "撥": "拨",
        "備": "备",
        "覆": "覆",
        "蓋": "盖",
        "淨": "净",
        "資": "资",
        "產": "产",
        "級": "级",
        "關": "关",
        "注": "注",
        "類": "类",
        "個": "个",
        "業": "业",
        "額": "额",
        "務": "务",
        "營": "营",
        "餘": "余",
        "與": "与",
    }
)

PERCENT_RE = re.compile(r"(?<![\d.])(-?\d+(?:\.\d+)?)\s*[%％]")
DECIMAL_RE = re.compile(r"(?<![\d.])(-?\d+\.\d+)(?!\d)")
LEADING_REGULATORY_MINIMUM_RE = re.compile(
    r"^\s*[（(]?\s*[≥＞>≤＜<]\s*-?\d+(?:\.\d+)?\s*[%％]?\s*[）)]?"
)


def normalize_text(value: str) -> str:
    return (
        str(value or "")
        .translate(TRADITIONAL_TRANSLATION)
        .replace("％", "%")
        .replace("\u3000", " ")
    )


def _capital_label_allowed(metric: str, line: str, alias: str) -> bool:
    if metric == "tier1_capital_ratio" and "核心一级" in line:
        return False
    if metric == "capital_adequacy_ratio" and (
        "核心一级资本" in line or "一级资本" in line
    ):
        return False
    return alias in line


def _candidate_score(line: str, alias: str, percentages: list[str]) -> int:
    compact = line.strip()
    score = min(len(percentages), 3) * 20
    if compact.startswith(alias):
        # A metric-labelled table row is stronger evidence than narrative text
        # that happens to contain several unrelated percentage changes.
        score += 100
    tail = line.split(alias, 1)[1] if alias in line else ""
    if PERCENT_RE.search(tail) or DECIMAL_RE.search(tail):
        # A complete row is safer than a chart whose labels and values were
        # emitted on separate lines in a different visual order.
        score += 80
    if "  " in line or "\t" in line:
        score += 10
    return score


def _numbers_after_alias(line: str, following: list[str], alias: str) -> list[str]:
    tail = line.split(alias, 1)[1]
    # Regulatory tables often place the applicable minimum before the actual
    # current-period value, e.g. “贷款拨备率 ≥1.8 2.44 2.45”.  The threshold is
    # evidence for a gate, not the bank's disclosed metric.
    tail = LEADING_REGULATORY_MINIMUM_RE.sub(" ", tail, count=1)
    values = PERCENT_RE.findall(tail) or DECIMAL_RE.findall(tail)
    if values:
        return values
    # PDF table extraction commonly wraps a parenthetical qualifier onto the
    # next line and places the values after it (for example 中国银行's
    # “成本收入比（境内 / 监管口径，%） 28.77”).  Keep the continuation window
    # bounded so narrative mentions cannot scan arbitrarily far for a number.
    continuation = " ".join(part.strip() for part in following if part.strip())
    return PERCENT_RE.findall(continuation) or DECIMAL_RE.findall(continuation)


def _find_metric(
    pages: list[str],
    metric: str,
    aliases: list[str],
    source_file: str,
) -> tuple[float | None, dict[str, Any] | None]:
    candidates: list[tuple[int, int, float, dict[str, Any]]] = []
    for page_number, page in enumerate(pages, start=1):
        lines = normalize_text(page).splitlines()
        for index, line in enumerate(lines):
            for alias in aliases:
                if not _capital_label_allowed(metric, line, alias):
                    continue
                following = lines[index + 1 : index + 3]
                window_lines = [line, *following]
                window = " ".join(part.strip() for part in window_lines if part.strip())
                percentages = _numbers_after_alias(line, following, alias)
                if not percentages:
                    continue
                value = float(percentages[0])
                evidence = {
                    "source_file": source_file,
                    "page": page_number,
                    "label": alias,
                    "raw_evidence": re.sub(r"\s+", " ", window).strip()[:500],
                }
                candidates.append(
                    (_candidate_score(line, alias, percentages), -page_number, value, evidence)
                )
    if not candidates:
        return None, None
    _, _, value, evidence = max(candidates, key=lambda item: (item[0], item[1]))
    return value, evidence


def parse_bank_metrics(
    text: str,
    *,
    ts_code: str = "",
    period: str = "",
    source_file: str = "",
) -> dict[str, Any]:
    pages = str(text or "").split("\f")
    evidence: dict[str, dict[str, Any]] = {}
    result: dict[str, Any] = {
        "ts_code": ts_code,
        "period": period,
        "source_file": source_file,
    }
    for metric, aliases in METRIC_ALIASES.items():
        value, metric_evidence = _find_metric(pages, metric, aliases, source_file)
        result[metric] = value
        if metric_evidence is not None:
            evidence[metric] = metric_evidence
    missing = [metric for metric in REQUIRED_METRICS if result.get(metric) is None]
    result["missing_metrics"] = missing
    result["data_quality"] = "normal" if not missing else "data_gap"
    result["metric_evidence_json"] = json.dumps(
        evidence, ensure_ascii=False, sort_keys=True
    )
    return result


def infer_period(path: Path) -> str:
    name = path.name
    match = re.search(r"(20\d{2}).*?(年度|年报)", name)
    if match:
        return f"{match.group(1)}1231"
    match = re.search(r"(20\d{2}).*?(半年度|半年|中期)", name)
    if match:
        return f"{match.group(1)}0630"
    return ""


def infer_ts_code(path: Path) -> str:
    match = re.search(r"(?<!\d)([036]\d{5})(?!\d)", path.name)
    if not match:
        return ""
    suffix = ".SH" if match.group(1).startswith("6") else ".SZ"
    return match.group(1) + suffix


def parse_file(path: Path) -> dict[str, Any]:
    return parse_bank_metrics(
        path.read_text(encoding="utf-8", errors="replace"),
        ts_code=infer_ts_code(path),
        period=infer_period(path),
        source_file=str(path.resolve()),
    )


def write_csv(records: list[dict[str, Any]], path: Path) -> None:
    fields = [
        "ts_code",
        "period",
        *METRIC_ALIASES.keys(),
        "data_quality",
        "missing_metrics",
        "source_file",
        "metric_evidence_json",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            row = dict(record)
            row["missing_metrics"] = json.dumps(
                row.get("missing_metrics", []), ensure_ascii=False
            )
            writer.writerow({field: row.get(field, "") for field in fields})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    records = [parse_file(path) for path in args.reports]
    if args.out:
        write_csv(records, args.out)
        print(args.out.resolve())
    else:
        print(json.dumps(records, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
