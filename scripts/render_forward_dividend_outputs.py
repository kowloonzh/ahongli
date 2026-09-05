#!/usr/bin/env python3
"""Render independent forward-dividend CSV, Markdown, HTML, and detail pages."""

from __future__ import annotations

import csv
import html
import io
import json
import os
import tempfile
from pathlib import Path
from typing import Any


FORWARD_FIELDS = [
    "rank", "ts_code", "name", "dividend_score_total", "quote_price", "quote_date",
    "instrument_ts_code", "share_class", "quote_currency", "dividend_currency",
    "forecast_share_denominator_scope", "fx_rate", "fx_rate_date", "fx_source", "source_dividend_yield",
    "source_dividend_yield_definition", "forecast_fiscal_year",
    "forecast_fy_regular_dps_low", "forecast_fy_regular_dps_base",
    "forecast_fy_regular_dps_high", "forward_12m_eligible_dps",
    "next_dividend_date_status", "next_dividend_event_id",
    "next_dividend_record_date", "next_dividend_ex_date", "next_dividend_payment_date",
    "next_dividend_evidence_source_ids",
    "expected_dividend_yield", "target_yield_low", "target_yield_high",
    "target_price_low", "target_price_high", "target_status", "target_display_label",
    "target_yield_model_id", "target_yield_model_version", "target_yield_category",
    "target_yield_policy_id", "target_yield_policy_sha256",
    "risk_free_rate", "risk_free_rate_date", "risk_free_rate_source",
    "equity_and_company_risk_spread_low", "equity_and_company_risk_spread_high",
    "required_return_low", "required_return_high", "sustainable_dividend_growth",
    "target_yield_basis",
    "forecast_method", "model_id", "model_version", "evidence_completeness",
    "forecast_uncertainty", "forecast_status", "forecast_reason",
    "forecast_profit_low", "forecast_profit", "forecast_profit_high",
    "forecast_payout_ratio_low", "forecast_payout_ratio", "forecast_payout_ratio_high",
    "forecast_total_shares", "announced_dividend_floor",
    "special_dividend_excluded", "forecast_input_fact_ids", "forecast_input_event_ids",
    "forecast_selection_decisions",
    "evidence_detail_path",
]


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _value(row: dict[str, Any], field: str) -> Any:
    value = row.get(field)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return "" if value is None else value


def _number(value: Any, digits: int = 2) -> str:
    if value in {None, ""}:
        return "—"
    return f"{float(value):.{digits}f}"


def _percent(value: Any, *, stored_as_ratio: bool = True) -> str:
    if value in {None, ""}:
        return "—"
    number = float(value) * (100 if stored_as_ratio else 1)
    return f"{number:.2f}%"


def _display_range(low: Any, high: Any, *, percent: bool = False) -> str:
    if low in {None, ""} or high in {None, ""}:
        return "—"
    formatter = _percent if percent else _number
    return f"{formatter(low)}–{formatter(high)}"


def _display_date(value: Any) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return "—"


def format_next_dividend_payment(row: dict[str, Any]) -> str:
    if row.get("next_dividend_payment_date") not in {None, ""}:
        return _display_date(row.get("next_dividend_payment_date"))
    if row.get("next_dividend_date_status") == "pending_implementation":
        return "待实施公告"
    return "—"


def _write_detail(
    row: dict[str, Any],
    detail_path: Path,
    evidence: dict[str, Any] | None = None,
) -> None:
    lines = [
        f"# {row.get('name', '')} 前瞻分红证据",
        "",
        "## 预测结论",
        "",
        f"- 正式排名：{row.get('rank', '')}",
        f"- 正式综合得分：{_number(row.get('dividend_score_total'))}",
        f"- 预测状态：`{row.get('forecast_status', '')}`",
        f"- 预测方法：`{row.get('forecast_method', '')}`",
        f"- 证据完整性：`{row.get('evidence_completeness', '')}`",
        f"- 预测不确定性：`{row.get('forecast_uncertainty', '')}`",
        f"- 状态说明：{row.get('forecast_reason', '')}",
        "",
        "## DPS三情景",
        "",
        f"- 低：{_number(row.get('forecast_fy_regular_dps_low'), 4)}",
        f"- 基准：{_number(row.get('forecast_fy_regular_dps_base'), 4)}",
        f"- 高：{_number(row.get('forecast_fy_regular_dps_high'), 4)}",
        f"- 派息率低/基准/高：{_percent(row.get('forecast_payout_ratio_low'))} / "
        f"{_percent(row.get('forecast_payout_ratio'))} / {_percent(row.get('forecast_payout_ratio_high'))}",
        "",
        "## 下一次派息",
        "",
        f"- 日期状态：`{row.get('next_dividend_date_status', 'not_announced')}`",
        f"- 股权登记日：{_display_date(row.get('next_dividend_record_date'))}",
        f"- 除权除息日：{_display_date(row.get('next_dividend_ex_date'))}",
        f"- 下次现金红利发放日：{format_next_dividend_payment(row)}",
        f"- 分红事件ID：{row.get('next_dividend_event_id') or '—'}",
    ]
    if all(row.get(field) not in {None, ""} for field in ["forecast_profit", "forecast_payout_ratio", "forecast_total_shares"]):
        lines.extend(
            [
                "",
                "## 预测公式",
                "",
                "```text",
                f"{_number(row.get('forecast_profit'))} × {_percent(row.get('forecast_payout_ratio'))} ÷ {_number(row.get('forecast_total_shares'))}",
                f"= {_number(row.get('forecast_fy_regular_dps_base'), 4)} 每股",
                "```",
            ]
        )
    lines.extend([
        "",
        "## 预期股息率与目标区间",
        "",
        f"- 预期股息率：{_percent(row.get('expected_dividend_yield'))}",
        f"- 目标股息率区间：{_display_range(row.get('target_yield_low'), row.get('target_yield_high'), percent=True)}",
        f"- 目标价格区间：{_display_range(row.get('target_price_low'), row.get('target_price_high'))}",
        f"- 当前位置：{row.get('target_display_label', '暂不判断')}",
        f"- 要求总回报率：{_percent(row.get('required_return_low'))}–{_percent(row.get('required_return_high'))}",
        f"- 可持续分红增长率：{_percent(row.get('sustainable_dividend_growth'))}",
        f"- 计算口径：{row.get('target_yield_basis', '')}",
        f"- 无风险利率：{_percent(row.get('risk_free_rate'))}（{row.get('risk_free_rate_date', '')}，{row.get('risk_free_rate_source', '')}）",
        "",
        "## 使用的原始来源",
        "",
    ])
    sources = (evidence or {}).get("source_documents", [])
    if sources:
        lines.extend(["| 公告ID | 文档 | 可用时间 |", "|---|---|---|"])
        for source in sources:
            title = str(source.get("source_title") or "").replace("|", "\\|")
            url = str(source.get("source_url") or "")
            label = f"[{title}]({url})" if url else title
            lines.append(f"| {source.get('announcement_id','')} | {label} | {source.get('available_at','')} |")
    else:
        lines.append("未取得原始来源。")
    facts = (evidence or {}).get("normalized_facts", [])
    lines.extend(["", "## 规范化事实", ""])
    if facts:
        lines.extend(["| 事实ID | 类型 | 期间 | 数值 | 来源 | 页码 |", "|---|---|---|---:|---|---:|"])
        for fact in facts:
            lines.append(
                f"| {fact.get('fact_id','')} | {fact.get('fact_type','')} | {fact.get('period','')} | "
                f"{fact.get('value','')} | {fact.get('source_document_id','')} | {fact.get('source_page_or_location','')} |"
            )
    else:
        lines.append("未取得规范化事实。")
    events = (evidence or {}).get("dividend_events", [])
    lines.extend(["", "## 分红经济事件", ""])
    if events:
        lines.extend(["| 事件ID | 财年 | 阶段 | 常规/特别 | 税前DPS | 状态 | 登记日 | 除息日 | 发放日 |", "|---|---|---|---|---:|---|---|---|---|"])
        for event in events:
            lines.append(
                f"| {event.get('event_id','')} | {event.get('fiscal_period','')} | {event.get('distribution_phase','')} | "
                f"{event.get('regular_or_special','')} | {event.get('cash_dividend_per_share_pre_tax','')} | {event.get('status','')} | "
                f"{_display_date(event.get('record_date'))} | {_display_date(event.get('ex_dividend_date'))} | "
                f"{_display_date(event.get('payment_date'))} |"
            )
    else:
        lines.append("未取得分红经济事件。")
    lines.extend(["", "完整原文片段见同目录 `manifest.json` 与 `forecast-evidence.json`。"])
    _atomic_write(detail_path, "\n".join(lines) + "\n")


def _write_records_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fields = sorted({field for record in records for field in record}) or ["ts_code"]
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for record in records:
        writer.writerow(
            {
                field: json.dumps(value, ensure_ascii=False, sort_keys=True)
                if isinstance(value, (dict, list))
                else "" if value is None else value
                for field, value in record.items()
            }
        )
    _atomic_write(path, "\ufeff" + buffer.getvalue())


def render_forward_dividend_outputs(
    *,
    rows: list[dict[str, Any]],
    run_date: str,
    output_dir: Path,
    evidence_by_code: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Path]:
    output_dir = Path(output_dir)
    ordered = sorted(rows, key=lambda row: int(row.get("rank") or 10_000))
    for row in ordered:
        detail = output_dir / "market_data" / "forward_dividend_evidence" / str(row["ts_code"]) / "forecast-detail.md"
        row["evidence_detail_path"] = str(detail.relative_to(output_dir))
        _write_detail(row, detail, (evidence_by_code or {}).get(str(row["ts_code"]), {}))

    csv_path = output_dir / f"hs300-dividend-forward-top10-{run_date}.csv"
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=FORWARD_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for row in ordered:
        writer.writerow({field: _value(row, field) for field in FORWARD_FIELDS})
    _atomic_write(csv_path, "\ufeff" + buffer.getvalue())

    markdown_path = output_dir / f"hs300-dividend-forward-report-{run_date}.md"
    md = [
        f"# AHongli 前瞻分红观察表 {run_date}",
        "",
        "前瞻分红严格后置，不改变正式排名和综合得分。目标股息率由要求总回报率减可持续分红增长率得到，不是投资建议。",
        "",
        "| 排名 | 公司 | 得分 | 股价 | 预期分红 | 预期股息率 | 下次派息日 | 当前位置 | 目标股息率区间 | 目标价格区间 |",
        "|---:|---|---:|---:|---:|---:|---|---|---:|---:|",
    ]
    for row in ordered:
        target_yield = _display_range(
            row.get("target_yield_low"), row.get("target_yield_high"), percent=True
        )
        target_price = _display_range(row.get("target_price_low"), row.get("target_price_high"))
        md.append(
            f"| {row.get('rank','')} | [{row.get('name','')}]({row.get('evidence_detail_path','')}) | "
            f"{_number(row.get('dividend_score_total'))} | {_number(row.get('quote_price'))} | "
            f"{_number(row.get('forecast_fy_regular_dps_base'), 4)} | {_percent(row.get('expected_dividend_yield'))} | "
            f"{format_next_dividend_payment(row)} | {row.get('target_display_label') or '暂不判断'} | "
            f"{target_yield} | {target_price} |"
        )
    _atomic_write(markdown_path, "\n".join(md) + "\n")

    html_path = output_dir / f"hs300-dividend-forward-dashboard-{run_date}.html"
    body = []
    for row in ordered:
        body.append(
            "<tr>"
            f"<td>{html.escape(str(row.get('rank','')))}</td>"
            f"<td><a href='{html.escape(str(row.get('evidence_detail_path','')))}'>{html.escape(str(row.get('name','')))}</a><br><small>{html.escape(str(row.get('ts_code','')))}</small></td>"
            f"<td>{_number(row.get('dividend_score_total'))}</td>"
            f"<td>{_number(row.get('quote_price'))}</td>"
            f"<td>{_number(row.get('forecast_fy_regular_dps_base'),4)}</td>"
            f"<td>{_percent(row.get('expected_dividend_yield'))}</td>"
            f"<td>{html.escape(format_next_dividend_payment(row))}</td>"
            f"<td>{html.escape(str(row.get('target_display_label') or '暂不判断'))}</td>"
            f"<td>{_display_range(row.get('target_yield_low'), row.get('target_yield_high'), percent=True)}</td>"
            f"<td>{_display_range(row.get('target_price_low'), row.get('target_price_high'))}</td>"
            "</tr>"
        )
    document = f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>
<title>AHongli 前瞻分红观察表 {html.escape(run_date)}</title><style>
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:0;background:#f5f7fa;color:#172033}}header,main{{padding:24px 32px}}header{{background:#12344d;color:#fff}}table{{width:100%;border-collapse:collapse;background:#fff}}th,td{{padding:9px;border-bottom:1px solid #e5e7eb;font-size:12px;text-align:left}}th{{background:#eef3f7}}a{{color:#0b63a5}}small{{color:#64748b}}
</style></head><body><header><h1>AHongli 前瞻分红观察表</h1><p>{html.escape(run_date)}｜正式排名与得分不变</p></header><main>
<p>目标股息率区间用于统一观察投入门槛；证据和计算过程保存在公司详情页。</p>
<table><thead><tr><th>排名</th><th>公司</th><th>得分</th><th>股价</th><th>预期分红</th><th>预期股息率</th><th>下次派息日</th><th>当前位置</th><th>目标股息率区间</th><th>目标价格区间</th></tr></thead><tbody>{''.join(body)}</tbody></table>
</main></body></html>"""
    _atomic_write(html_path, document)

    market_dir = output_dir / "market_data"
    sources: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for code, evidence in (evidence_by_code or {}).items():
        sources.extend({"ts_code": code, **record} for record in evidence.get("source_documents", []))
        facts.extend({"ts_code": code, **record} for record in evidence.get("normalized_facts", []))
        events.extend({"ts_code": code, **record} for record in evidence.get("dividend_events", []))
    sources_path = market_dir / "forward-dividend-sources.csv"
    facts_path = market_dir / "forward-dividend-facts.csv"
    events_path = market_dir / "forward-dividend-events.csv"
    results_path = market_dir / "forward-dividend-results.csv"
    _write_records_csv(sources_path, sources)
    _write_records_csv(facts_path, facts)
    _write_records_csv(events_path, events)
    _write_records_csv(results_path, [{field: _value(row, field) for field in FORWARD_FIELDS} for row in ordered])
    return {
        "csv": csv_path,
        "markdown": markdown_path,
        "html": html_path,
        "sources": sources_path,
        "facts": facts_path,
        "events": events_path,
        "results": results_path,
    }
