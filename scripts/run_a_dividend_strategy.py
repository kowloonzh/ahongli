#!/usr/bin/env python3
"""Screen CSI 300 constituents for a quality-dividend strategy."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import requests


SKILL_DIR = Path(__file__).resolve().parents[1]
DATA_ROOT_ENV = "AHONGLI_DATA_ROOT"
INDEX_CODE = "000300.SH"
INDEX_NAME = "沪深300"
CSI_CONSTITUENTS_URL = "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/cons/000300cons.xls"
EXPECTED_CONSTITUENT_COUNT = 300
OUTPUT_ROOT_NAME = "a_dividend_outputs"
MIN_DIVIDEND_YIELD_VALID_DAYS = 1000
MIN_DIVIDEND_YIELD_DATA_COVERAGE = 0.80
TOP_CANDIDATE_COUNT = 10
STRUCTURED_FACTORS_FILENAME = "structured_factors.csv"
BANK_METRICS_FILENAME = "bank_quality_metrics.csv"
MIN_BANK_LOAN_PROVISION_RATIO = 1.5
DIVIDEND_YIELD_THRESHOLD = 3.0
DIVIDEND_YIELD_ENTRY_RATIO = 0.80
DIVIDEND_YIELD_RETENTION_RATIO = 0.75
MIN_PROFIT_DEDT_THREE_YEAR_CHANGE = -0.10
MAX_TOP_CANDIDATES_PER_INDUSTRY = 5
# Banks share one macro and rate cycle, so they get a tighter cap (2026-11-02).
MAX_TOP_BANKS = 3
RULE_VERSIONS = ("v1", "v2")
# Formal default since 2026-11-02: rule v2 with the nonfinancial cross-cycle
# gate (docs/scoring-v2-backtest-comparison.md). v1 stays selectable.
DEFAULT_RULE_VERSION = "v2"
STRUCTURED_HISTORY_YEARS = 7
# v2 compares the latest deducted profit with its 10-year median, so the
# fina_indicator history reaches further back, fetched in short windows to
# stay under the interface's per-call row limit.
PROFIT_HISTORY_YEARS = 12
STATEMENT_WINDOW_YEARS = 4
INTEREST_HISTORY_YEARS = 4
INTEREST_BEARING_DEBT_FIELDS = [
    "st_borr",
    "lt_borr",
    "bond_payable",
    "non_cur_liab_due_1y",
]
# Ranking precision: differences finer than disclosure precision are noise.
FACTOR_PRECISION_STEPS = {
    "roe": 0.1,
    "droe": 0.1,
    "bank_npl_change_3y": 0.05,
    "bank_nim_change_3y": 0.05,
    "dps_cagr_5y": 0.01,
}
# Scored source field when it differs from the factor name.
FACTOR_SOURCE_FIELDS = {
    "droe": "droe_standardized",
    "opcfd": "opcfd_rank_value",
}
COMPANY_PROFILE_FIELDS = [
    "main_business",
    "business_scope",
    "introduction",
    "company_profile_source",
    "company_profile_fetch_date",
]
NONFINANCIAL_FACTOR_WEIGHTS = {
    "roe": 0.20,
    "droe": 0.10,
    "opcfd": 0.15,
    "fcf_dividend_coverage_3y": 0.20,
    "dividend_yield_cv_5y": 0.10,
    "consecutive_dividend_years": 0.10,
    "dps_cagr_5y": 0.15,
}
FINANCIAL_FACTOR_WEIGHTS = {
    "roe": 0.30,
    "droe": 0.15,
    "dividend_yield_cv_5y": 0.20,
    "consecutive_dividend_years": 0.20,
    "dps_cagr_5y": 0.15,
}
BANK_FACTOR_WEIGHTS = {
    "roe": 0.15,
    "droe": 0.05,
    "bank_npl_quality": 0.15,
    "bank_provision_quality": 0.10,
    "bank_capital_resilience": 0.10,
    "bank_nim_quality": 0.10,
    "bank_cost_income_ratio": 0.05,
    "dividend_yield_cv_5y": 0.10,
    "consecutive_dividend_years": 0.10,
    "dps_cagr_5y": 0.10,
}
FACTOR_PERCENTILE_FIELDS = {
    "roe": "roe_percentile",
    "droe": "droe_percentile",
    "opcfd": "opcfd_percentile",
    "fcf_dividend_coverage_3y": "fcf_dividend_coverage_percentile",
    "dividend_yield_cv_5y": "dividend_yield_cv_percentile",
    "consecutive_dividend_years": "consecutive_dividend_years_percentile",
    "dps_cagr_5y": "dps_cagr_5y_percentile",
    "bank_npl_quality": "bank_npl_quality_percentile",
    "bank_provision_quality": "bank_provision_quality_percentile",
    "bank_capital_resilience": "bank_capital_resilience_percentile",
    "bank_nim_quality": "bank_nim_quality_percentile",
    "bank_cost_income_ratio": "bank_cost_income_ratio_percentile",
}
CSV_FIELDS = [
    "top10_rank",
    "rank",
    "dividend_score_total",
    "score_reason",
    "selected",
    "selected_reason",
    "selection_status",
    "market_preflight_passed",
    "structured_gate_passed",
    "ts_code",
    "name",
    "industry",
    "is_financial",
    "is_bank",
    "weight",
    "current_price",
    "price_date",
    "price_low_5y",
    "price_low_date_5y",
    "price_high_5y",
    "price_high_date_5y",
    "current_dividend_yield",
    "dividend_yield_min_5y",
    "dividend_yield_p10_5y",
    "dividend_yield_median_5y",
    "dividend_yield_max_5y",
    "dividend_yield_observation_days_5y",
    "dividend_yield_valid_days_5y",
    "dividend_yield_data_coverage_5y",
    "dividend_yield_data_sufficient",
    "dividend_yield_ge3_ratio",
    "dividend_yield_cv_5y",
    "long_term_dividend_above_3",
    "dividend_yield_required_ratio",
    "prior_formal_top10_member",
    "prior_formal_top10_source",
    "dividend_anchor_year",
    "consecutive_dividend_years",
    "latest_dps_tax",
    "prior3_median_dps_tax",
    "latest_dps_to_prior3_median",
    "dps_cagr_5y",
    "three_year_continuous_dividend_passed",
    "three_year_average_payout_ratio",
    "latest_payout_ratio",
    "payout_ratio_gate_passed",
    "roe_stability_12q",
    "roe_stability_percentile",
    "roe_stability_gate_passed",
    "profit_dedt_latest",
    "profit_dedt_three_year_change",
    "profit_trend_gate_passed",
    "latest_cashflow_dividend_coverage",
    "median_cashflow_dividend_coverage_3y",
    "cashflow_dividend_gate_passed",
    "audit_result",
    "audit_gate_passed",
    "main_business",
    "business_scope",
    "introduction",
    "company_profile_source",
    "company_profile_fetch_date",
    "portfolio_scope_gate_passed",
    "portfolio_scope_gate_status",
    "portfolio_scope_gate_reason",
    "real_estate_relevance_level",
    "real_estate_relevance_reason",
    "real_estate_related",
    "roe",
    "roe_percentile",
    "roe_score_contribution",
    "droe",
    "droe_standardized",
    "droe_percentile",
    "droe_score_contribution",
    "opcfd",
    "interest_bearing_debt",
    "opcfd_percentile",
    "opcfd_score_contribution",
    "fcf_dividend_coverage_3y",
    "fcf_dividend_coverage_percentile",
    "fcf_dividend_coverage_3y_score_contribution",
    "dividend_yield_cv_percentile",
    "dividend_yield_cv_5y_score_contribution",
    "consecutive_dividend_years_percentile",
    "consecutive_dividend_years_score_contribution",
    "dps_cagr_5y_percentile",
    "dps_cagr_5y_score_contribution",
    "bank_quality_data_quality",
    "bank_quality_gate_passed",
    "bank_quality_gate_reason",
    "bank_metrics_start_period",
    "bank_metrics_latest_period",
    "bank_npl_ratio",
    "bank_npl_change_3y",
    "bank_npl_quality_percentile",
    "bank_npl_quality_score_contribution",
    "bank_provision_coverage_ratio",
    "bank_loan_provision_ratio",
    "bank_required_loan_provision_ratio",
    "bank_excess_loan_provision_ratio",
    "bank_provision_quality_percentile",
    "bank_provision_quality_score_contribution",
    "bank_core_tier1_capital_ratio",
    "bank_tier1_capital_ratio",
    "bank_capital_adequacy_ratio",
    "bank_capital_resilience_percentile",
    "bank_capital_resilience_score_contribution",
    "bank_net_interest_margin",
    "bank_nim_change_3y",
    "bank_nim_quality_percentile",
    "bank_nim_quality_score_contribution",
    "bank_cost_income_ratio",
    "bank_cost_income_ratio_percentile",
    "bank_cost_income_ratio_score_contribution",
    "data_quality",
    "data_quality_reason",
]


def compact_date(value: Any) -> str:
    text = str(value or "").strip()
    match = re.search(r"(\d{4})[-/.年]?(\d{1,2})[-/.月]?(\d{1,2})", text)
    if match:
        y, m, d = match.groups()
        return f"{y}{int(m):02d}{int(d):02d}"
    digits = re.sub(r"\D", "", text)
    return digits[:8] if len(digits) >= 8 else datetime.now().strftime("%Y%m%d")


def five_year_start(run_date: str) -> str:
    dt = datetime.strptime(compact_date(run_date), "%Y%m%d")
    return (dt - timedelta(days=365 * 5 + 10)).strftime("%Y%m%d")


def resolve_data_root(
    data_root: Path | str | None = None,
    *,
    environ: dict[str, str] | None = None,
    cwd: Path | None = None,
    invocation_skill_dir: Path | None = None,
) -> Path:
    environment = os.environ if environ is None else environ
    working_directory = Path.cwd() if cwd is None else Path(cwd)

    def absolute(path: Path | str) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = working_directory / candidate
        return candidate.resolve()

    if data_root is not None:
        return absolute(data_root)
    if configured := str(environment.get(DATA_ROOT_ENV, "")).strip():
        return absolute(configured)

    invoked_from = (
        Path(invocation_skill_dir)
        if invocation_skill_dir is not None
        else Path(__file__).absolute().parents[1]
    )
    agents_dir = next(
        (parent for parent in (invoked_from, *invoked_from.parents) if parent.name == ".agents"),
        None,
    )
    configured_home = str(environment.get("HOME", "")).strip()
    home_directory = Path(configured_home).expanduser().resolve() if configured_home else Path.home().resolve()
    is_project_local_install = (
        agents_dir is not None and agents_dir.parent.resolve() != home_directory
    )
    workspace_root = agents_dir.parent if is_project_local_install else working_directory
    return (workspace_root / "data").resolve()


def output_dir(run_date: str, data_root: Path | str | None = None) -> Path:
    return resolve_data_root(data_root) / OUTPUT_ROOT_NAME / compact_date(run_date)


def build_latest_constituents(
    weights: pd.DataFrame,
    stock_basic: pd.DataFrame,
    data_end_date: str,
) -> pd.DataFrame:
    if weights.empty or "trade_date" not in weights.columns:
        raise RuntimeError(f"未取得{INDEX_NAME}成分股权重数据")
    eligible = weights[weights["trade_date"].astype(str) <= compact_date(data_end_date)].copy()
    if eligible.empty:
        raise RuntimeError(f"{data_end_date}之前没有可用的{INDEX_NAME}成分股快照")
    latest_date = str(eligible["trade_date"].max())
    latest = eligible[eligible["trade_date"].astype(str) == latest_date].copy()
    left_code = "con_code" if "con_code" in latest.columns else "ts_code"
    if left_code not in latest.columns:
        raise RuntimeError(f"{INDEX_NAME}成分股数据缺少证券代码字段")
    if not stock_basic.empty and "ts_code" in stock_basic.columns:
        latest = latest.merge(
            stock_basic,
            left_on=left_code,
            right_on="ts_code",
            how="left",
            suffixes=("", "_basic"),
        )
        if "ts_code_basic" in latest.columns:
            latest["ts_code"] = latest["ts_code_basic"]
        missing_basic = latest["ts_code"].isna()
        if missing_basic.any():
            codes = "、".join(latest.loc[missing_basic, left_code].astype(str).head(10).tolist())
            raise RuntimeError(
                f"成分股快照包含未匹配在上市公司资料的证券：{codes}；"
                "快照可能未反映临时调整，请核实最新成分股名单"
            )
    else:
        latest["ts_code"] = latest[left_code]
    latest["index_code"] = INDEX_CODE
    latest["trade_date"] = latest_date
    required = [
        "index_code",
        "con_code",
        "trade_date",
        "weight",
        "weight_trade_date",
        "ts_code",
        "symbol",
        "name",
        "area",
        "industry",
        "market",
        "list_date",
    ]
    for column in required:
        if column not in latest.columns:
            latest[column] = ""
    latest["weight"] = pd.to_numeric(latest["weight"], errors="coerce")
    latest = latest[required].drop_duplicates(subset=["ts_code"]).sort_values("weight", ascending=False)
    if len(latest) != EXPECTED_CONSTITUENT_COUNT:
        raise RuntimeError(
            f"预期取得{EXPECTED_CONSTITUENT_COUNT}只{INDEX_NAME}成分股，实际取得{len(latest)}只"
        )
    return latest.reset_index(drop=True)


def tushare_pro():
    try:
        import tushare as ts
    except ImportError as exc:
        raise RuntimeError("缺少 tushare 包，无法拉取沪深300数据") from exc
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if not token:
        raise RuntimeError("缺少 TUSHARE_TOKEN，无法拉取沪深300数据")
    return ts.pro_api(token)


CSI_DOWNLOAD_ATTEMPTS = 3
CSI_DOWNLOAD_TIMEOUT_SECONDS = 30


def _archived_csi_source(market: Path, valid_dates: list[str]) -> bytes | None:
    """Return the archived CSI file when its hash and snapshot date still qualify.

    The live CSI URL only serves the latest snapshot, so a rerun for an earlier
    run date can be substantiated only by the file archived at that time.
    """
    source_file = market / "csi_constituents_source.xls"
    meta_file = market / "csi_constituents_source.json"
    if not source_file.exists() or not meta_file.exists():
        return None
    try:
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    raw = source_file.read_bytes()
    if hashlib.sha256(raw).hexdigest() != meta.get("sha256"):
        return None
    if str(meta.get("snapshot_date") or "") not in valid_dates:
        return None
    return raw


def _download_csi_source() -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, CSI_DOWNLOAD_ATTEMPTS + 1):
        try:
            response = requests.get(CSI_CONSTITUENTS_URL, timeout=CSI_DOWNLOAD_TIMEOUT_SECONDS)
            response.raise_for_status()
            return response.content
        except requests.RequestException as error:
            last_error = error
            if attempt < CSI_DOWNLOAD_ATTEMPTS:
                delay = 2 ** attempt
                print(f"中证成分股文件下载失败（第{attempt}次：{type(error).__name__}），{delay}秒后重试", flush=True)
                time.sleep(delay)
    raise RuntimeError(f"中证成分股文件下载失败，已重试{CSI_DOWNLOAD_ATTEMPTS}次：{last_error}") from last_error


def fetch_current_constituents(client, run_date: str, market: Path) -> pd.DataFrame:
    end = compact_date(run_date)
    start = (datetime.strptime(end, "%Y%m%d") - timedelta(days=30)).strftime("%Y%m%d")
    calendar = client.trade_cal(exchange="SSE", start_date=start, end_date=end, is_open="1")
    if calendar is None or calendar.empty:
        raise RuntimeError("无法确认运行日最近交易日，不能验证成分股日期")
    trading_dates = sorted(set(calendar["cal_date"].dropna().astype(str)))
    expected_date = trading_dates[-1]
    raw = _archived_csi_source(market, trading_dates[-2:])
    reused = raw is not None
    if raw is None:
        raw = _download_csi_source()
    frame = pd.read_excel(io.BytesIO(raw), dtype=str)
    date_col, index_col = "日期Date", "指数代码 Index Code"
    code_col, exchange_col = "成份券代码Constituent Code", "交易所Exchange"
    for col in (date_col, index_col, code_col, exchange_col):
        if col not in frame.columns:
            raise RuntimeError(f"中证成分股文件缺少字段: {col}")
    dates = frame[date_col].dropna().astype(str).unique().tolist()
    if len(dates) != 1 or dates[0] not in trading_dates[-2:]:
        raise RuntimeError(f"中证成分股文件日期{dates}，须为最近交易日{expected_date}或其前一个交易日；数据过期或不适用于历史运行")
    snapshot_date = dates[0]
    lag = int(snapshot_date != expected_date)
    warning = "名单滞后一个交易日，尚未核验当天生效的指数调整，名单可能发生变化" if lag else ""
    if warning:
        print(f"警告：{warning}（名单日期{snapshot_date}，最近交易日{expected_date}）", flush=True)
    if not frame[index_col].astype(str).str.zfill(6).eq("000300").all():
        raise RuntimeError("中证成分股文件指数代码不一致")
    exchange = frame[exchange_col].map({"上海证券交易所": "SH", "深圳证券交易所": "SZ"})
    if exchange.isna().any():
        raise RuntimeError("中证成分股文件存在未知交易所")
    codes = frame[code_col].astype(str).str.zfill(6) + "." + exchange
    if len(codes) != EXPECTED_CONSTITUENT_COUNT or codes.nunique() != EXPECTED_CONSTITUENT_COUNT:
        raise RuntimeError("中证成分股文件数量或唯一性校验失败")
    if reused:
        print(f"复用已存档中证成分股文件（名单日期{snapshot_date}，SHA-256校验通过）", flush=True)
        return pd.DataFrame({"con_code": codes, "trade_date": snapshot_date, "index_code": INDEX_CODE})
    (market / "csi_constituents_source.xls").write_bytes(raw)
    (market / "csi_constituents_source.json").write_text(json.dumps({"url": CSI_CONSTITUENTS_URL, "snapshot_date": snapshot_date, "latest_trade_date": expected_date, "lag_trading_days": lag, "warning": warning, "sha256": hashlib.sha256(raw).hexdigest(), "rows": len(codes)}, ensure_ascii=False, indent=2), encoding="utf-8")
    return pd.DataFrame({"con_code": codes, "trade_date": snapshot_date, "index_code": INDEX_CODE})


def _load_constituents_file(path: Path, data_end_date: str) -> pd.DataFrame:
    data = pd.read_csv(path, dtype=str)
    if "ts_code" not in data.columns and "con_code" in data.columns:
        data["ts_code"] = data["con_code"]
    if "con_code" not in data.columns and "ts_code" in data.columns:
        data["con_code"] = data["ts_code"]
    if "index_code" not in data.columns:
        data["index_code"] = INDEX_CODE
    if "trade_date" not in data.columns:
        data["trade_date"] = compact_date(data_end_date)
    if "weight" not in data.columns:
        data["weight"] = ""
    required = [
        "index_code",
        "con_code",
        "trade_date",
        "weight",
        "weight_trade_date",
        "ts_code",
        "symbol",
        "name",
        "area",
        "industry",
        "market",
        "list_date",
        *COMPANY_PROFILE_FIELDS,
    ]
    for column in required:
        if column not in data.columns:
            data[column] = ""
    data["index_code"] = INDEX_CODE
    data = data[required].drop_duplicates(subset=["ts_code"])
    if len(data) != EXPECTED_CONSTITUENT_COUNT:
        raise RuntimeError(
            f"预期取得{EXPECTED_CONSTITUENT_COUNT}只{INDEX_NAME}成分股，实际取得{len(data)}只"
        )
    return data.reset_index(drop=True)


def fetch_company_profiles(client, run_date: str) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    fields = "ts_code,exchange,introduction,main_business,business_scope"
    for exchange in ["SSE", "SZSE"]:
        data = client.stock_company(exchange=exchange, fields=fields)
        frames.append(pd.DataFrame() if data is None else pd.DataFrame(data))
    profiles = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if profiles.empty or "ts_code" not in profiles.columns:
        raise RuntimeError("未取得Tushare stock_company上市公司主营资料")
    profiles = profiles.drop_duplicates("ts_code", keep="last")
    for field in ["main_business", "business_scope", "introduction"]:
        if field not in profiles.columns:
            profiles[field] = ""
        profiles[field] = profiles[field].fillna("").astype(str).str.strip()
    profiles["company_profile_source"] = "Tushare stock_company"
    profiles["company_profile_fetch_date"] = compact_date(run_date)
    return profiles[["ts_code", *COMPANY_PROFILE_FIELDS]]


def enrich_constituents_with_company_profiles(
    constituents: pd.DataFrame,
    profiles: pd.DataFrame,
) -> pd.DataFrame:
    base = constituents.drop(columns=COMPANY_PROFILE_FIELDS, errors="ignore")
    merged = base.merge(profiles, on="ts_code", how="left")
    missing = merged[
        merged["main_business"].fillna("").astype(str).str.strip().eq("")
        | merged["business_scope"].fillna("").astype(str).str.strip().eq("")
        | merged["introduction"].fillna("").astype(str).str.strip().eq("")
    ]
    if not missing.empty:
        examples = "、".join(missing["ts_code"].fillna("<missing>").astype(str).head(5).tolist())
        raise RuntimeError(
            f"stock_company主营资料未覆盖{len(missing)}只沪深300公司，示例：{examples}"
        )
    return merged


def fetch_hs300_market_data(
    run_date: str,
    start_market: str,
    sleep_seconds: float,
    *,
    pro=None,
    market_dir: Path | None = None,
    refresh_constituents: bool = False,
    fixed_constituents_file: Path | None = None,
    max_workers: int = 4,
) -> Path:
    data_end_date = compact_date(run_date)
    market = market_dir or (output_dir(run_date) / "market_data")
    market.mkdir(parents=True, exist_ok=True)
    client = pro or tushare_pro()
    cached_constituents = market / "constituents.csv"

    if fixed_constituents_file is not None:
        constituents = _load_constituents_file(fixed_constituents_file, data_end_date)
    else:
        end_dt = datetime.strptime(data_end_date, "%Y%m%d")
        weights = client.index_weight(
            index_code=INDEX_CODE,
            start_date=(end_dt - timedelta(days=180)).strftime("%Y%m%d"),
            end_date=data_end_date,
        )
        weights = pd.DataFrame() if weights is None else pd.DataFrame(weights)
        if weights.empty or "trade_date" not in weights or "con_code" not in weights:
            raise RuntimeError("未取得沪深300月度权重")
        eligible = weights[weights["trade_date"].astype(str) <= data_end_date]
        if eligible.empty:
            raise RuntimeError("运行日前无可用月度权重")
        weight_date = eligible["trade_date"].astype(str).max()
        monthly = eligible[eligible["trade_date"].astype(str).eq(weight_date)].copy()
        if len(monthly) != EXPECTED_CONSTITUENT_COUNT or monthly["con_code"].nunique() != EXPECTED_CONSTITUENT_COUNT:
            raise RuntimeError("月度权重快照不完整或存在重复证券")
        monthly.to_csv(market / "monthly_index_weight.csv", index=False, encoding="utf-8-sig")
        membership = fetch_current_constituents(client, data_end_date, market)
        membership = membership.merge(monthly[["con_code", "weight"]], on="con_code", how="left", validate="one_to_one")
        membership["weight_trade_date"] = weight_date
        stock_basic = client.stock_basic(
            exchange="",
            list_status="L",
            fields="ts_code,symbol,name,area,industry,market,list_date",
        )
        stock_basic = pd.DataFrame() if stock_basic is None else pd.DataFrame(stock_basic)
        constituents = build_latest_constituents(membership, stock_basic, data_end_date)

    profile_complete = bool(
        all(field in constituents.columns for field in ["main_business", "business_scope", "introduction"])
        and constituents["main_business"].fillna("").astype(str).str.strip().ne("").all()
        and constituents["business_scope"].fillna("").astype(str).str.strip().ne("").all()
        and constituents["introduction"].fillna("").astype(str).str.strip().ne("").all()
    )
    if not profile_complete:
        constituents = enrich_constituents_with_company_profiles(
            constituents,
            fetch_company_profiles(client, data_end_date),
        )
    constituents.to_csv(cached_constituents, index=False, encoding="utf-8-sig")
    constituents.to_csv(
        market / "constituents_full_latest.csv",
        index=False,
        encoding="utf-8-sig",
    )

    requests = []
    for ts_code in constituents["ts_code"].dropna().astype(str):
        for interface in ("daily", "daily_basic"):
            path = market / f"{interface}_{ts_code}_{start_market}_{data_end_date}.csv"
            if not path.exists() or path.stat().st_size == 0:
                requests.append((interface, ts_code, path))

    def fetch_one(request: tuple[str, str, Path]) -> None:
        interface, ts_code, path = request
        data = getattr(client, interface)(
            ts_code=ts_code,
            start_date=start_market,
            end_date=data_end_date,
        )
        frame = pd.DataFrame() if data is None else pd.DataFrame(data)
        frame.to_csv(path, index=False, encoding="utf-8-sig")
        if sleep_seconds:
            time.sleep(sleep_seconds)

    with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as executor:
        list(executor.map(fetch_one, requests))
    return market


def find_csv(market_dir: Path, prefix: str, ts_code: str) -> Path | None:
    matches = sorted(market_dir.glob(f"{prefix}_{ts_code}_*.csv"))
    return matches[-1] if matches else None


def read_market_csv(market_dir: Path, prefix: str, ts_code: str) -> pd.DataFrame:
    path = find_csv(market_dir, prefix, ts_code)
    if path is None or not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, dtype={"trade_date": str})
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def numeric_series(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(dtype=float)
    return pd.to_numeric(df[column], errors="coerce").dropna()


def latest_row(df: pd.DataFrame) -> pd.Series | None:
    if df.empty or "trade_date" not in df.columns:
        return None
    copy = df.copy()
    copy["_trade_date"] = pd.to_numeric(copy["trade_date"], errors="coerce")
    copy = copy.dropna(subset=["_trade_date"]).sort_values("_trade_date", ascending=False)
    if copy.empty:
        return None
    return copy.iloc[0]


def finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def fmt_num(value: Any, digits: int = 2) -> str:
    number = finite_float(value)
    return f"{number:.{digits}f}" if number is not None else "未取得"


def price_profile(daily: pd.DataFrame) -> dict[str, Any]:
    row = latest_row(daily)
    lows = numeric_series(daily, "low")
    highs = numeric_series(daily, "high")
    result: dict[str, Any] = {
        "current_price": finite_float(row.get("close")) if row is not None else None,
        "price_date": str(row.get("trade_date")) if row is not None else "",
        "price_low_5y": None,
        "price_low_date_5y": "",
        "price_high_5y": None,
        "price_high_date_5y": "",
    }
    if not lows.empty:
        idx = lows.idxmin()
        result["price_low_5y"] = float(lows.loc[idx])
        result["price_low_date_5y"] = str(daily.loc[idx, "trade_date"])
    if not highs.empty:
        idx = highs.idxmax()
        result["price_high_5y"] = float(highs.loc[idx])
        result["price_high_date_5y"] = str(daily.loc[idx, "trade_date"])
    return result


def daily_dividend_yield_series(daily_basic: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """Return deduplicated observations and the daily TTM yield on determined days.

    Tushare leaves ``dv_ttm`` blank when the trailing 12 months contain no cash
    dividend. A blank yield on a day with a valid close is therefore 0%, not a
    missing observation; only days without a close remain undetermined.
    """
    observations = daily_basic
    if "trade_date" in observations.columns:
        observations = observations.drop_duplicates(subset=["trade_date"], keep="last")
    field = "dv_ttm" if "dv_ttm" in observations.columns else "dv_ratio"
    if field not in observations.columns:
        return observations, pd.Series(dtype=float)
    raw = pd.to_numeric(observations[field], errors="coerce")
    if "close" not in observations.columns:
        return observations, raw.dropna()
    close = pd.to_numeric(observations["close"], errors="coerce")
    determined = close.notna() & close.gt(0)
    return observations, raw.where(raw.notna(), 0.0)[determined]


def dividend_yield_profile(daily_basic: pd.DataFrame) -> dict[str, Any]:
    observations, series = daily_dividend_yield_series(daily_basic)
    observation_days = len(observations)
    valid_days = len(series)
    data_coverage = valid_days / observation_days if observation_days else 0.0
    data_sufficient = bool(
        valid_days >= MIN_DIVIDEND_YIELD_VALID_DAYS
        and data_coverage >= MIN_DIVIDEND_YIELD_DATA_COVERAGE
    )
    row = latest_row(observations)
    current = None
    if row is not None:
        if row.name in series.index:
            current = finite_float(series.loc[row.name])
        else:
            field = "dv_ttm" if "dv_ttm" in observations.columns else "dv_ratio"
            current = finite_float(row.get(field))
    if series.empty:
        return {
            "current_dividend_yield": current,
            "dividend_yield_min_5y": None,
            "dividend_yield_p10_5y": None,
            "dividend_yield_median_5y": None,
            "dividend_yield_max_5y": None,
            "dividend_yield_observation_days_5y": observation_days,
            "dividend_yield_valid_days_5y": valid_days,
            "dividend_yield_data_coverage_5y": round(data_coverage, 4),
            "dividend_yield_data_sufficient": data_sufficient,
            "dividend_yield_ge3_ratio": 0.0,
            "dividend_yield_ge3_ratio_exact": 0.0,
            "dividend_yield_cv_5y": None,
            "dividend_yield_required_ratio": DIVIDEND_YIELD_ENTRY_RATIO,
            "long_term_dividend_above_3": False,
            "dividend_stable": False,
        }
    mean = float(series.mean())
    cv = float(series.std(ddof=0) / mean) if mean else None
    ge3_ratio = float((series >= DIVIDEND_YIELD_THRESHOLD).mean())
    p10 = float(series.quantile(0.10))
    return {
        "current_dividend_yield": current,
        "dividend_yield_min_5y": float(series.min()),
        "dividend_yield_p10_5y": p10,
        "dividend_yield_median_5y": float(series.median()),
        "dividend_yield_max_5y": float(series.max()),
        "dividend_yield_observation_days_5y": observation_days,
        "dividend_yield_valid_days_5y": valid_days,
        "dividend_yield_data_coverage_5y": round(data_coverage, 4),
        "dividend_yield_data_sufficient": data_sufficient,
        "dividend_yield_ge3_ratio": round(ge3_ratio, 4),
        "dividend_yield_ge3_ratio_exact": ge3_ratio,
        "dividend_yield_cv_5y": round(cv, 4) if cv is not None else None,
        "dividend_yield_required_ratio": DIVIDEND_YIELD_ENTRY_RATIO,
        "long_term_dividend_above_3": bool(
            data_sufficient and ge3_ratio >= DIVIDEND_YIELD_ENTRY_RATIO
        ),
        "dividend_stable": bool(cv is not None and cv <= 0.35),
    }


def empty_prior_formal_top() -> dict[str, Any]:
    return {"run_date": "", "source": "", "codes": set()}


def load_prior_formal_top(
    run_date: str,
    data_root: Path | str | None = None,
) -> dict[str, Any]:
    """Load the latest formal Top10 dated strictly before ``run_date``."""
    current = compact_date(run_date)
    root = output_dir(current, data_root=data_root).parent
    if not root.is_dir():
        return empty_prior_formal_top()
    for candidate in sorted(
        (path for path in root.iterdir() if path.is_dir() and re.fullmatch(r"\d{8}", path.name)),
        key=lambda path: path.name,
        reverse=True,
    ):
        if candidate.name >= current:
            continue
        top_path = candidate / f"hs300-dividend-top10-{candidate.name}.csv"
        if not top_path.is_file():
            continue
        frame = pd.read_csv(top_path, dtype=str, encoding="utf-8-sig")
        codes = set(frame.get("ts_code", pd.Series(dtype=str)).dropna().astype(str).str.strip())
        return {"run_date": candidate.name, "source": str(top_path), "codes": codes - {""}}
    return empty_prior_formal_top()


def apply_dividend_yield_hysteresis(
    profile: dict[str, Any],
    ts_code: str,
    prior_formal_top: dict[str, Any] | None,
) -> dict[str, Any]:
    """Prior formal Top10 members keep their place down to the retention ratio."""
    prior = prior_formal_top or empty_prior_formal_top()
    member = ts_code in prior.get("codes", set())
    required = DIVIDEND_YIELD_RETENTION_RATIO if member else DIVIDEND_YIELD_ENTRY_RATIO
    ratio = finite_float(profile.get("dividend_yield_ge3_ratio_exact"))
    if ratio is None:
        ratio = finite_float(profile.get("dividend_yield_ge3_ratio"))
    profile["prior_formal_top10_member"] = member
    profile["prior_formal_top10_source"] = str(prior.get("source") or "")
    profile["dividend_yield_required_ratio"] = required
    profile["long_term_dividend_above_3"] = bool(
        profile.get("dividend_yield_data_sufficient")
        and ratio is not None
        and ratio >= required
    )
    return profile


def real_estate_relevance(row: dict[str, Any]) -> dict[str, Any]:
    """Apply the user's portfolio-policy exclusion for direct property businesses.

    This is intentionally not an exposure or cycle score: construction,
    building-material and financial businesses may be property-related but are
    judged by the general quality and dividend-safety factors instead.
    """
    industry = str(row.get("industry", "")).strip()
    main_business = str(row.get("main_business", "")).strip()
    direct_industry_markers = ["全国地产", "区域地产", "房地产开发", "房地产服务"]
    direct_main_business_markers = [
        "主营业务为房地产开发",
        "主营业务是房地产开发",
        "房地产开发与商品房销售",
        "房地产开发、经营与销售",
        "住宅开发与销售",
        "商业地产开发与运营",
    ]
    if any(marker in industry for marker in direct_industry_markers) or any(
        marker in main_business for marker in direct_main_business_markers
    ):
        return {
            "real_estate_relevance_score": 100,
            "real_estate_relevance_level": "直接地产主业",
            "real_estate_relevance_reason": "投资范围约束：行业或已解析主营明确为房地产开发、销售或商业地产运营",
        }
    return {
        "real_estate_relevance_score": 0,
        "real_estate_relevance_level": "非直接地产主业",
        "real_estate_relevance_reason": "未确认房地产开发、销售或商业地产运营为主业；不对上下游或金融敞口额外扣分",
    }


def is_real_estate_related(row: dict[str, Any]) -> bool:
    score = finite_float(row.get("real_estate_relevance_score"))
    if score is None:
        score = finite_float(real_estate_relevance(row).get("real_estate_relevance_score"))
    return bool(score is not None and score >= 60)


def has_long_term_dividend_above_3(row: dict[str, Any]) -> bool:
    if "long_term_dividend_above_3" in row:
        return bool(row.get("long_term_dividend_above_3"))
    ratio = finite_float(row.get("dividend_yield_ge3_ratio"))
    required = finite_float(row.get("dividend_yield_required_ratio")) or DIVIDEND_YIELD_ENTRY_RATIO
    return bool(ratio is not None and ratio >= required)


def evaluate_market_dividend_preconditions(row: dict[str, Any]) -> str | None:
    price = finite_float(row.get("current_price"))
    if price is None:
        return "当前价未取得"
    current_yield = finite_float(row.get("current_dividend_yield"))
    if current_yield is None:
        return "当前股息率未取得"
    if row.get("dividend_yield_data_sufficient") is False:
        valid_days = int(finite_float(row.get("dividend_yield_valid_days_5y")) or 0)
        coverage = finite_float(row.get("dividend_yield_data_coverage_5y")) or 0.0
        return (
            "近五年股息率有效数据不足："
            f"有效{valid_days}个交易日、覆盖率{coverage:.1%}；"
            f"要求至少{MIN_DIVIDEND_YIELD_VALID_DAYS}个交易日且覆盖率"
            f">={MIN_DIVIDEND_YIELD_DATA_COVERAGE:.0%}"
        )
    if not has_long_term_dividend_above_3(row):
        ratio = finite_float(row.get("dividend_yield_ge3_ratio"))
        ratio_text = "未取得" if ratio is None else f"{ratio:.1%}"
        required = finite_float(row.get("dividend_yield_required_ratio")) or DIVIDEND_YIELD_ENTRY_RATIO
        suffix = "（上期正式Top10成员保留线）" if row.get("prior_formal_top10_member") else ""
        return f"近五年股息率>=3%的交易日占比为{ratio_text}，低于{required:.0%}{suffix}"
    return None


DIVIDEND_EVENT_IDENTITY_FIELDS = [
    "ts_code",
    "end_date",
    "div_proc",
    "cash_div_tax",
    "stk_div",
    "stk_bo_rate",
    "stk_co_rate",
    "base_share",
    "record_date",
    "ex_date",
    "pay_date",
    "imp_ann_date",
]


def dedupe_dividend_events(dividends: pd.DataFrame) -> pd.DataFrame:
    """Drop Tushare rows that repeat one event and differ only by ann_date.

    Tushare can return the same implemented distribution twice with different
    announcement dates. Summing both doubles the fiscal-year DPS and cash total,
    falsely failing the recent-cut gate and distorting payout, coverage and CAGR.
    """
    if dividends.empty:
        return dividends
    fields = [field for field in DIVIDEND_EVENT_IDENTITY_FIELDS if field in dividends.columns]
    if not fields:
        return dividends
    key = dividends[fields].astype(str).replace({"nan": "", "None": "", "NaN": "", "<NA>": ""})
    return dividends.loc[~key.duplicated(keep="first")]


def _implemented_cash_dividend_events(dividends: pd.DataFrame, run_date: str) -> pd.DataFrame:
    """Normalize implemented Tushare cash-dividend events available by run date."""
    if dividends.empty or "cash_div_tax" not in dividends.columns:
        return pd.DataFrame()
    events = dividends.copy()
    if "div_proc" in events.columns:
        events = events[events["div_proc"].astype(str).str.strip().eq("实施")]
    dates = pd.Series("", index=events.index, dtype=str)
    for column in ["imp_ann_date", "record_date", "ex_date", "pay_date", "ann_date"]:
        if column in events.columns:
            candidate = events[column].astype(str).str.replace(r"\D", "", regex=True).str[:8]
            dates = dates.mask(dates.eq(""), candidate)
    events["_event_date"] = dates
    events["_cash_div_tax"] = pd.to_numeric(events["cash_div_tax"], errors="coerce")
    base_share = (
        pd.to_numeric(events["base_share"], errors="coerce")
        if "base_share" in events.columns
        else pd.Series(float("nan"), index=events.index)
    )
    events["_cash_dividend_total_yuan"] = events["_cash_div_tax"] * base_share * 10_000.0
    events = events[
        events["_event_date"].str.fullmatch(r"\d{8}", na=False)
        & events["_event_date"].le(compact_date(run_date))
        & events["_cash_div_tax"].gt(0)
    ].sort_values("_event_date")
    if "end_date" in events.columns:
        events["_end_date"] = events["end_date"].astype(str).str.replace(r"\D", "", regex=True).str[:8]
    else:
        events["_end_date"] = ""
    return events


def _stock_dividend_multiplier(row: pd.Series) -> float:
    stock_dividend = finite_float(row.get("stk_div"))
    if stock_dividend is None:
        stock_dividend = (finite_float(row.get("stk_bo_rate")) or 0.0) + (
            finite_float(row.get("stk_co_rate")) or 0.0
        )
    return max(1.0, 1.0 + float(stock_dividend or 0.0))


def dividend_history_profile(
    dividends: pd.DataFrame,
    run_date: str,
    *,
    explicit_no_dividend_years: set[int] | None = None,
) -> dict[str, Any]:
    """Build one auditable, tax-inclusive dividend history on a comparable-share basis."""
    explicit_zero = set(explicit_no_dividend_years or set())
    dividends = dedupe_dividend_events(dividends)
    run_year = datetime.strptime(compact_date(run_date), "%Y%m%d").year
    latest_complete_year = run_year - 1
    events = _implemented_cash_dividend_events(dividends, run_date)
    annual_raw: dict[int, float] = {}
    annual_total: dict[int, float] = {}
    annual_multipliers: dict[int, list[float]] = {}
    stock_events = dividends.copy()
    if not stock_events.empty:
        if "div_proc" in stock_events.columns:
            stock_events = stock_events[
                stock_events["div_proc"].astype(str).str.strip().eq("实施")
            ]
        available_date = pd.Series("", index=stock_events.index, dtype=str)
        for column in ["imp_ann_date", "record_date", "ex_date", "pay_date", "ann_date"]:
            if column in stock_events.columns:
                candidate = stock_events[column].fillna("").astype(str).str.replace(r"\D", "", regex=True).str[:8]
                available_date = available_date.mask(available_date.eq(""), candidate)
        stock_events["_event_date"] = available_date
        stock_events["_end_date"] = (
            stock_events["end_date"].astype(str).str.replace(r"\D", "", regex=True).str[:8]
            if "end_date" in stock_events.columns
            else ""
        )
        stock_events = stock_events[
            stock_events["_event_date"].str.fullmatch(r"\d{8}", na=False)
            & stock_events["_event_date"].le(compact_date(run_date))
            & stock_events["_end_date"].str.fullmatch(r"\d{8}", na=False)
        ]
        for _, event in stock_events.iterrows():
            multiplier = _stock_dividend_multiplier(event)
            if multiplier > 1.0:
                annual_multipliers.setdefault(int(str(event["_end_date"])[:4]), []).append(multiplier)
    if not events.empty:
        for _, event in events.iterrows():
            end_date = str(event.get("_end_date", ""))
            if not re.fullmatch(r"\d{8}", end_date):
                continue
            year = int(end_date[:4])
            annual_raw[year] = annual_raw.get(year, 0.0) + float(event["_cash_div_tax"])
            total = finite_float(event.get("_cash_dividend_total_yuan"))
            if total is not None:
                annual_total[year] = annual_total.get(year, 0.0) + total

    if latest_complete_year in explicit_zero:
        anchor_year = latest_complete_year
    elif annual_raw.get(latest_complete_year, 0.0) > 0:
        anchor_year = latest_complete_year
    elif annual_raw.get(latest_complete_year - 1, 0.0) > 0:
        anchor_year = latest_complete_year - 1
    else:
        return {
            "dividend_anchor_year": None,
            "annual_raw_dps": annual_raw,
            "annual_comparable_dps": {},
            "annual_cash_dividend_total_yuan": annual_total,
            "consecutive_dividend_years": 0,
            "latest_dps_tax": None,
            "prior3_median_dps_tax": None,
            "latest_dps_to_prior3_median": None,
            "dps_cagr_5y": None,
            "three_year_continuous_dividend_passed": False,
            "data_quality": "data_gap",
            "data_quality_reason": "最近两个完整财年均未取得已实施现金分红，无法确定分红锚定年度",
        }

    annual_comparable: dict[int, float] = {}
    for year, raw_dps in annual_raw.items():
        adjustment = 1.0
        for action_year in range(year, anchor_year + 1):
            for multiplier in annual_multipliers.get(action_year, []):
                adjustment *= multiplier
        annual_comparable[year] = raw_dps / adjustment
    for year in explicit_zero:
        if year <= anchor_year:
            annual_comparable[year] = 0.0

    consecutive_years = 0
    cursor = anchor_year
    while annual_comparable.get(cursor, 0.0) > 0:
        consecutive_years += 1
        cursor -= 1

    latest = annual_comparable.get(anchor_year, 0.0)
    prior_three = [annual_comparable.get(year, 0.0) for year in range(anchor_year - 3, anchor_year)]
    prior_median = float(pd.Series(prior_three).median()) if all(value > 0 for value in prior_three) else None
    latest_ratio = latest / prior_median if prior_median and prior_median > 0 else None
    five_years = list(range(anchor_year - 4, anchor_year + 1))
    five_values = [annual_comparable.get(year, 0.0) for year in five_years]
    cagr = None
    if all(value > 0 for value in five_values):
        cagr = log_linear_growth_rate(five_values)
    return {
        "dividend_anchor_year": anchor_year,
        "annual_raw_dps": annual_raw,
        "annual_comparable_dps": annual_comparable,
        "annual_cash_dividend_total_yuan": annual_total,
        "consecutive_dividend_years": consecutive_years,
        "latest_dps_tax": latest,
        "prior3_median_dps_tax": prior_median,
        "latest_dps_to_prior3_median": latest_ratio,
        "dps_cagr_5y": cagr,
        "three_year_continuous_dividend_passed": consecutive_years >= 3,
        "data_quality": "normal",
        "data_quality_reason": "Tushare已实施含税现金分红，按财年合并并对送转股进行可比口径调整",
    }


def log_linear_growth_rate(values: list[float]) -> float | None:
    """Annual growth implied by the OLS slope of ln(value) on consecutive years."""
    if len(values) < 2 or any(value <= 0 for value in values):
        return None
    logs = [math.log(value) for value in values]
    count = len(logs)
    mean_x = (count - 1) / 2.0
    mean_y = sum(logs) / count
    numerator = sum((index - mean_x) * (value - mean_y) for index, value in enumerate(logs))
    denominator = sum((index - mean_x) ** 2 for index in range(count))
    return math.exp(numerator / denominator) - 1.0


def _period_value_map(frame: pd.DataFrame, field: str) -> dict[str, float]:
    if frame.empty or "end_date" not in frame.columns or field not in frame.columns:
        return {}
    copy = frame.copy()
    copy["_end_date"] = copy["end_date"].astype(str).str.replace(r"\D", "", regex=True).str[:8]
    copy["_value"] = pd.to_numeric(copy[field], errors="coerce")
    copy = copy.dropna(subset=["_value"]).drop_duplicates("_end_date", keep="last")
    return {str(row["_end_date"]): float(row["_value"]) for _, row in copy.iterrows()}


def _prior_year_period(end_date: str) -> str:
    return f"{int(end_date[:4]) - 1}{end_date[4:]}" if re.fullmatch(r"\d{8}", end_date) else ""


def _prior_fiscal_year_end(end_date: str) -> str:
    return f"{int(end_date[:4]) - 1}1231" if re.fullmatch(r"\d{8}", end_date) else ""


def build_ttm_series(frame: pd.DataFrame, field: str, output_field: str) -> pd.DataFrame:
    """Convert cumulative PRC periodic statements to trailing-twelve-month values."""
    values = _period_value_map(frame, field)
    rows: list[dict[str, Any]] = []
    for end_date in sorted(values):
        if end_date.endswith("1231"):
            ttm_value = values[end_date]
        else:
            previous_fy = values.get(_prior_fiscal_year_end(end_date))
            prior_ytd = values.get(_prior_year_period(end_date))
            if previous_fy is None or prior_ytd is None:
                continue
            ttm_value = previous_fy + values[end_date] - prior_ytd
        rows.append({"end_date": end_date, output_field: ttm_value})
    return pd.DataFrame(rows)


def interest_bearing_debt_at(balance: pd.DataFrame, end_date: str) -> float | None:
    """Short + long borrowings + bonds payable + non-current liabilities due in one year.

    A blank component on an existing balance-sheet row means the company has
    no such liability; a missing period row is a data gap.
    """
    if balance.empty or "end_date" not in balance.columns:
        return None
    frame = balance.copy()
    frame["_end_date"] = frame["end_date"].astype(str).str.replace(r"\D", "", regex=True).str[:8]
    period = frame[frame["_end_date"].eq(end_date)]
    if period.empty:
        return None
    row = period.iloc[-1]
    total = 0.0
    for field in INTEREST_BEARING_DEBT_FIELDS:
        value = finite_float(row.get(field)) if field in period.columns else None
        total += max(0.0, value or 0.0)
    return total


def build_ttm_roe_series(income: pd.DataFrame, balance: pd.DataFrame) -> pd.DataFrame:
    profits = build_ttm_series(income, "n_income_attr_p", "ttm_parent_net_profit")
    equities = _period_value_map(balance, "total_hldr_eqy_exc_min_int")
    rows: list[dict[str, Any]] = []
    for _, record in profits.iterrows():
        end_date = str(record["end_date"])
        current_equity = equities.get(end_date)
        prior_equity = equities.get(_prior_year_period(end_date))
        if current_equity is None or prior_equity is None:
            continue
        average_equity = (current_equity + prior_equity) / 2.0
        if average_equity <= 0:
            continue
        ttm_profit = float(record["ttm_parent_net_profit"])
        rows.append(
            {
                "end_date": end_date,
                "ttm_parent_net_profit": ttm_profit,
                "average_parent_equity": average_equity,
                "ttm_roe": ttm_profit / average_equity * 100.0,
            }
        )
    return pd.DataFrame(rows)


def latest_roe_metrics(series: pd.DataFrame) -> dict[str, Any]:
    if series.empty or "end_date" not in series.columns or "ttm_roe" not in series.columns:
        return {"roe": None, "droe": None, "roe_period": ""}
    frame = series.copy().sort_values("end_date")
    latest = frame.iloc[-1]
    end_date = str(latest["end_date"])
    prior = frame[frame["end_date"].astype(str).eq(_prior_year_period(end_date))]
    prior_roe = finite_float(prior.iloc[-1]["ttm_roe"]) if not prior.empty else None
    latest_roe = finite_float(latest["ttm_roe"])
    return {
        "roe": latest_roe,
        "droe": latest_roe - prior_roe if latest_roe is not None and prior_roe is not None else None,
        "roe_period": end_date,
    }


def payout_ratio_gate(annual_ratios: dict[int, float]) -> dict[str, Any]:
    if len(annual_ratios) < 3:
        return {"passed": False, "status": "data_gap", "three_year_average_payout_ratio": None, "latest_payout_ratio": None}
    years = sorted(annual_ratios)[-3:]
    values = [finite_float(annual_ratios[year]) for year in years]
    if any(value is None for value in values):
        return {"passed": False, "status": "data_gap", "three_year_average_payout_ratio": None, "latest_payout_ratio": None}
    numeric = [float(value) for value in values if value is not None]
    average = round(sum(numeric) / 3.0, 12)
    latest = numeric[-1]
    passed = 0.10 < average < 1.0 and 0.0 < latest < 1.0
    return {"passed": passed, "status": "passed" if passed else "hard_gate_failed", "three_year_average_payout_ratio": average, "latest_payout_ratio": latest}


def profit_trend_gate(annual_profit_dedt: dict[int, float]) -> dict[str, Any]:
    if len(annual_profit_dedt) < 4:
        return {"passed": False, "status": "data_gap", "three_year_change": None}
    years = sorted(annual_profit_dedt)[-4:]
    values = [finite_float(annual_profit_dedt[year]) for year in years]
    if any(value is None for value in values):
        return {"passed": False, "status": "data_gap", "three_year_change": None}
    numeric = [float(value) for value in values if value is not None]
    change = numeric[-1] / numeric[0] - 1.0 if numeric[0] > 0 else None
    passed = (
        all(value > 0 for value in numeric)
        and change is not None
        and round(change, 12) >= MIN_PROFIT_DEDT_THREE_YEAR_CHANGE
    )
    return {"passed": passed, "status": "passed" if passed else "hard_gate_failed", "three_year_change": change}


def cashflow_dividend_gate(annual_coverage: dict[int, float]) -> dict[str, Any]:
    if len(annual_coverage) < 3:
        return {"passed": False, "status": "data_gap", "latest_cashflow_dividend_coverage": None, "median_cashflow_dividend_coverage_3y": None}
    years = sorted(annual_coverage)[-3:]
    values = [finite_float(annual_coverage[year]) for year in years]
    if any(value is None for value in values):
        return {"passed": False, "status": "data_gap", "latest_cashflow_dividend_coverage": None, "median_cashflow_dividend_coverage_3y": None}
    numeric = [float(value) for value in values if value is not None]
    latest = numeric[-1]
    median = float(pd.Series(numeric).median())
    passed = latest >= 1.0 and median >= 1.0
    return {"passed": passed, "status": "passed" if passed else "hard_gate_failed", "latest_cashflow_dividend_coverage": latest, "median_cashflow_dividend_coverage_3y": median}


def audit_opinion_gate(audit_result: Any) -> dict[str, Any]:
    result = str(audit_result or "").strip()
    if not result:
        return {"passed": False, "status": "data_gap", "reason": "审计意见未取得"}
    passed = result == "标准无保留意见"
    return {"passed": passed, "status": "passed" if passed else "hard_gate_failed", "reason": f"审计意见={result}"}


def default_factor_weights() -> dict[str, dict[str, float]]:
    return {
        "bank": BANK_FACTOR_WEIGHTS,
        "financial": FINANCIAL_FACTOR_WEIGHTS,
        "nonfinancial": NONFINANCIAL_FACTOR_WEIGHTS,
    }


def scoring_group(row: dict[str, Any]) -> str:
    if bool(row.get("is_bank")):
        return "bank"
    if bool(row.get("is_financial")):
        return "financial"
    return "nonfinancial"


def weighted_quality_score(
    row: dict[str, Any],
    factor_weights: dict[str, dict[str, float]] | None = None,
) -> dict[str, Any]:
    weights = (factor_weights or default_factor_weights())[scoring_group(row)]
    contributions: dict[str, float] = {}
    missing: list[str] = []
    for factor, weight in weights.items():
        percentile = finite_float(row.get(FACTOR_PERCENTILE_FIELDS[factor]))
        if percentile is None:
            missing.append(factor)
            continue
        contributions[factor] = percentile * weight
    if missing:
        return {"dividend_score_total": None, "score_contributions": contributions, "score_reason": "缺少计分因子：" + "、".join(missing)}
    total = sum(contributions.values())
    return {"dividend_score_total": round(total, 2), "score_contributions": {key: round(value, 2) for key, value in contributions.items()}, "score_reason": "银行、其他金融与非金融分组百分位加权"}


def _assign_percentile(
    frame: pd.DataFrame,
    source_field: str,
    output_field: str,
    *,
    higher_is_better: bool,
) -> None:
    series = pd.to_numeric(frame.get(source_field), errors="coerce")
    step = FACTOR_PRECISION_STEPS.get(source_field)
    if step is not None:
        series = precision_bucket(series, step)
    if series.dropna().empty:
        frame[output_field] = float("nan")
        return
    frame[output_field] = series.rank(
        method="average",
        pct=True,
        ascending=higher_is_better,
    ) * 100.0


def _build_bank_composite_percentiles(frame: pd.DataFrame) -> None:
    specs = {
        "bank_npl_ratio": False,
        "bank_npl_change_3y": False,
        "bank_provision_coverage_ratio": True,
        "bank_core_tier1_capital_ratio": True,
        "bank_capital_adequacy_ratio": True,
        "bank_net_interest_margin": True,
        "bank_nim_change_3y": True,
    }
    for field, higher_is_better in specs.items():
        _assign_percentile(
            frame,
            field,
            f"{field}_percentile",
            higher_is_better=higher_is_better,
        )
    frame["bank_npl_quality_percentile"] = (
        frame["bank_npl_ratio_percentile"] * 0.80
        + frame["bank_npl_change_3y_percentile"] * 0.20
    )
    frame["bank_provision_quality_percentile"] = frame[
        "bank_provision_coverage_ratio_percentile"
    ]
    frame["bank_capital_resilience_percentile"] = (
        frame["bank_core_tier1_capital_ratio_percentile"] * 0.60
        + frame["bank_capital_adequacy_ratio_percentile"] * 0.40
    )
    frame["bank_nim_quality_percentile"] = (
        frame["bank_net_interest_margin_percentile"] * 0.70
        + frame["bank_nim_change_3y_percentile"] * 0.30
    )


def precision_bucket(series: pd.Series, step: float) -> pd.Series:
    """Round to the disclosure precision so sub-precision gaps tie in rank."""
    numeric = pd.to_numeric(series, errors="coerce")
    return (numeric / step).round() * step


def prepare_scoring_inputs(frame: pd.DataFrame) -> pd.DataFrame:
    """Derive ranked inputs: standardized DROE and the OPCFD rank value."""
    prepared = frame.copy()
    droe = precision_bucket(
        prepared.get("droe", pd.Series(float("nan"), index=prepared.index)),
        FACTOR_PRECISION_STEPS["droe"],
    )
    volatility = pd.to_numeric(
        prepared.get("roe_stability_12q", pd.Series(float("nan"), index=prepared.index)),
        errors="coerce",
    )
    standardized = droe / volatility.where(volatility > 0)
    standardized = standardized.mask(volatility.eq(0) & droe.notna(), 0.0)
    prepared["droe_standardized"] = standardized
    opcfd = pd.to_numeric(
        prepared.get("opcfd", pd.Series(float("nan"), index=prepared.index)), errors="coerce"
    )
    debt = pd.to_numeric(
        prepared.get("interest_bearing_debt", pd.Series(float("nan"), index=prepared.index)),
        errors="coerce",
    )
    cfo = pd.to_numeric(
        prepared.get("ttm_cfo_latest", pd.Series(float("nan"), index=prepared.index)),
        errors="coerce",
    )
    # No interest-bearing debt: positive operating cash flow ranks at the top
    # of the reference pool, non-positive cash flow at the bottom.
    prepared["opcfd_rank_value"] = (
        opcfd.mask(debt.eq(0) & cfo.gt(0), float("inf"))
        .mask(debt.eq(0) & cfo.le(0), float("-inf"))
    )
    return prepared


def _ranking_source(factor: str) -> str:
    return FACTOR_SOURCE_FIELDS.get(factor, factor)


def score_cross_section(
    rows: list[dict[str, Any]],
    factor_weights: dict[str, dict[str, float]] | None = None,
) -> list[dict[str, Any]]:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return []
    weights_by_group = factor_weights or default_factor_weights()
    frame = prepare_scoring_inputs(frame)
    factors = sorted(set().union(*(set(weights) for weights in weights_by_group.values())))
    frame["_scoring_group"] = "nonfinancial"
    frame.loc[frame["is_financial"].fillna(False).astype(bool), "_scoring_group"] = "financial"
    if "is_bank" in frame.columns:
        frame.loc[frame["is_bank"].fillna(False).astype(bool), "_scoring_group"] = "bank"
    result_frames: list[pd.DataFrame] = []
    for group_name, group in frame.groupby("_scoring_group", sort=False):
        scored = group.copy()
        applicable = weights_by_group[group_name]
        if group_name == "financial":
            # Other-financial can be a one-company cohort after the dividend
            # preflight (currently only 中国平安). Keep its distinct weight
            # model, but benchmark its common factors against all financial
            # preflight passers so a singleton does not mechanically score 100.
            reference = frame[frame["is_financial"].fillna(False).astype(bool)].copy()
            for factor in applicable:
                percentile_field = FACTOR_PERCENTILE_FIELDS[factor]
                _assign_percentile(
                    reference,
                    _ranking_source(factor),
                    percentile_field,
                    higher_is_better=factor != "dividend_yield_cv_5y",
                )
                scored[percentile_field] = reference.loc[scored.index, percentile_field]
            result_frames.append(scored)
            continue
        if group_name == "bank" and any(factor.startswith("bank_") for factor in applicable):
            _build_bank_composite_percentiles(scored)
        for factor in factors:
            if factor not in applicable:
                continue
            percentile_field = FACTOR_PERCENTILE_FIELDS[factor]
            if percentile_field in scored.columns and factor.startswith("bank_") and factor != "bank_cost_income_ratio":
                continue
            _assign_percentile(
                scored,
                _ranking_source(factor),
                percentile_field,
                higher_is_better=factor not in {"dividend_yield_cv_5y", "bank_cost_income_ratio"},
            )
        result_frames.append(scored)
    combined = pd.concat(result_frames).sort_index()
    combined = combined.drop(
        columns=[column for column in combined.columns if column == "opcfd_rank_value"]
    )
    records = combined.to_dict("records")
    for record in records:
        score = weighted_quality_score(record, weights_by_group)
        record.update(score)
        for factor, contribution in score["score_contributions"].items():
            record[f"{factor}_score_contribution"] = contribution
    return records


def dedupe_statement_rows(frame: pd.DataFrame, run_date: str) -> pd.DataFrame:
    """Keep the latest consolidated revision that was public by ``run_date``."""
    if frame.empty or "end_date" not in frame.columns:
        return pd.DataFrame(columns=frame.columns)
    result = frame.copy()
    if "report_type" in result.columns:
        report_type = result["report_type"].astype(str).str.replace(".0", "", regex=False).str.strip()
        result = result[report_type.eq("1")]
    result["_end_date"] = result["end_date"].astype(str).str.replace(r"\D", "", regex=True).str[:8]
    announcement = pd.Series("", index=result.index, dtype=str)
    for column in ["f_ann_date", "ann_date"]:
        if column in result.columns:
            candidate = result[column].fillna("").astype(str).str.replace(r"\D", "", regex=True).str[:8]
            announcement = announcement.mask(announcement.eq(""), candidate)
    result["_available_date"] = announcement
    cutoff = compact_date(run_date)
    result = result[
        result["_end_date"].str.fullmatch(r"\d{8}", na=False)
        & (result["_available_date"].eq("") | result["_available_date"].le(cutoff))
    ]
    sort_fields = ["_end_date", "_available_date"]
    if "update_flag" in result.columns:
        result["_update_order"] = pd.to_numeric(result["update_flag"], errors="coerce").fillna(0)
        sort_fields.append("_update_order")
    return result.sort_values(sort_fields).drop_duplicates("_end_date", keep="last").drop(
        columns=[column for column in ["_end_date", "_available_date", "_update_order"] if column in result.columns]
    )


def structured_gate_profile(row: dict[str, Any]) -> dict[str, Any]:
    """Evaluate only gates backed by market and structured Tushare evidence."""
    if not bool(row.get("market_preflight_passed")):
        return {"structured_gate_passed": False, "structured_gate_status": "hard_gate_failed", "structured_gate_reason": "未通过五年股息率市场预筛"}
    if str(row.get("structured_data_quality") or "") != "normal":
        return {"structured_gate_passed": False, "structured_gate_status": "data_gap", "structured_gate_reason": str(row.get("structured_data_reason") or "结构化财务数据不足")}
    scope = portfolio_scope_gate_profile(row)
    if not scope["portfolio_scope_gate_passed"]:
        return {
            "structured_gate_passed": False,
            "structured_gate_status": "hard_gate_failed",
            "structured_gate_reason": scope["portfolio_scope_gate_reason"],
            **scope,
        }
    checks = [
        ("three_year_continuous_dividend_passed", "最近3年未连续实施现金分红"),
        ("payout_ratio_gate_passed", "支付率不在策略范围"),
        ("roe_stability_gate_passed", "12季度TTM ROE稳定性未进入沪深300最低80%"),
        ("profit_trend_gate_passed", "三年扣非归母净利润降幅超过10%或存在非正年度"),
        ("audit_gate_passed", "最新年度审计意见不是标准无保留意见"),
    ]
    if not bool(row.get("is_financial")):
        checks.append(("cashflow_dividend_gate_passed", "经营现金流分红覆盖未达标"))
    for field, reason in checks:
        if not bool(row.get(field)):
            return {"structured_gate_passed": False, "structured_gate_status": "hard_gate_failed", "structured_gate_reason": reason}
    recent_ratio = finite_float(row.get("latest_dps_to_prior3_median"))
    if recent_ratio is None:
        return {"structured_gate_passed": False, "structured_gate_status": "data_gap", "structured_gate_reason": "近期分红削减比率数据不足"}
    if recent_ratio < 0.70:
        return {"structured_gate_passed": False, "structured_gate_status": "hard_gate_failed", "structured_gate_reason": f"最新每股分红较前三年中位数削减超过30%（比率{recent_ratio:.2f}）"}
    return {
        "structured_gate_passed": True,
        "structured_gate_status": "passed",
        "structured_gate_reason": "通过全部结构化硬门槛，进入质量红利评分",
        **scope,
    }


def portfolio_scope_gate_profile(row: dict[str, Any]) -> dict[str, Any]:
    relevance = real_estate_relevance(row)
    related = finite_float(relevance.get("real_estate_relevance_score")) == 100.0
    return {
        "portfolio_scope_gate_passed": not related,
        "portfolio_scope_gate_status": "hard_gate_failed" if related else "passed",
        "portfolio_scope_gate_reason": (
            relevance["real_estate_relevance_reason"]
            if related
            else "结构化主营资料未确认直接地产主业"
        ),
        **relevance,
    }


def industry_cap(industry: str) -> int:
    return MAX_TOP_BANKS if is_bank_industry(industry) else MAX_TOP_CANDIDATES_PER_INDUSTRY


def _industry_capped_top(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fill Top10 by score, skipping companies whose industry already holds the cap."""
    selected = sorted(
        (row for row in rows if str(row.get("selected")) == "是"),
        key=lambda row: finite_float(row.get("dividend_score_total")) or float("-inf"),
        reverse=True,
    )
    top: list[dict[str, Any]] = []
    capped: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for row in selected:
        if len(top) >= TOP_CANDIDATE_COUNT:
            break
        industry = str(row.get("industry") or "").strip()
        if industry and counts.get(industry, 0) >= industry_cap(industry):
            capped.append(row)
            continue
        if industry:
            counts[industry] = counts.get(industry, 0) + 1
        top.append(row)
    return top, capped


def formal_top_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _industry_capped_top(rows)[0]


def load_rules_v2():
    """Import rules_v2 against this module even when it runs as ``__main__``."""
    sys.modules.setdefault("run_a_dividend_strategy", sys.modules[__name__])
    script_dir = str(Path(__file__).resolve().parent)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    import rules_v2

    return rules_v2


def formal_rule_params(rule_version: str) -> dict[str, Any]:
    if rule_version not in RULE_VERSIONS:
        raise ValueError(f"未知规则版本：{rule_version}")
    if rule_version == "v1":
        return {}
    rules_v2 = load_rules_v2()
    return rules_v2.resolve_params(rules_v2.FORMAL_RULE_PARAMS)


def is_financial_industry(industry: Any) -> bool:
    text = str(industry or "")
    return any(marker in text for marker in ["银行", "保险", "证券", "多元金融", "金融服务"])


def is_bank_industry(industry: Any) -> bool:
    return "银行" in str(industry or "")


def bank_quality_profile(records: list[dict[str, Any]]) -> dict[str, Any]:
    frame = pd.DataFrame(records)
    required = [
        "npl_ratio",
        "provision_coverage_ratio",
        "loan_provision_ratio",
        "net_interest_margin",
        "cost_income_ratio",
        "core_tier1_capital_ratio",
        "tier1_capital_ratio",
        "capital_adequacy_ratio",
    ]
    if frame.empty or "period" not in frame.columns:
        return {
            "bank_quality_data_quality": "data_gap",
            "bank_quality_gate_passed": False,
            "bank_quality_gate_reason": "缺少银行最近3年专项指标",
        }
    frame["_period"] = frame["period"].astype(str).str.replace(r"\D", "", regex=True).str[:8]
    frame = frame[frame["_period"].str.endswith("1231", na=False)].sort_values("_period").tail(3)
    missing: list[str] = []
    if len(frame) < 3:
        missing.append("最近3个完整年度")
    for field in required:
        if field not in frame.columns or pd.to_numeric(frame[field], errors="coerce").isna().any():
            missing.append(field)
    if "data_quality" in frame.columns and not frame["data_quality"].astype(str).eq("normal").all():
        missing.append("原始解析质量")
    if missing:
        return {
            "bank_quality_data_quality": "data_gap",
            "bank_quality_gate_passed": False,
            "bank_quality_gate_reason": "银行专项指标缺少：" + "、".join(dict.fromkeys(missing)),
        }
    numeric = {field: pd.to_numeric(frame[field], errors="coerce").tolist() for field in required}
    latest = {field: values[-1] for field, values in numeric.items()}
    result = {
        "bank_quality_data_quality": "normal",
        "bank_metrics_start_period": str(frame.iloc[0]["_period"]),
        "bank_metrics_latest_period": str(frame.iloc[-1]["_period"]),
        "bank_npl_ratio": latest["npl_ratio"],
        "bank_npl_change_3y": latest["npl_ratio"] - numeric["npl_ratio"][0],
        "bank_provision_coverage_ratio": latest["provision_coverage_ratio"],
        "bank_loan_provision_ratio": latest["loan_provision_ratio"],
        "bank_required_loan_provision_ratio": max(
            MIN_BANK_LOAN_PROVISION_RATIO,
            latest["npl_ratio"] * 1.5,
        ),
        "bank_net_interest_margin": latest["net_interest_margin"],
        "bank_nim_change_3y": latest["net_interest_margin"] - numeric["net_interest_margin"][0],
        "bank_cost_income_ratio": latest["cost_income_ratio"],
        "bank_core_tier1_capital_ratio": latest["core_tier1_capital_ratio"],
        "bank_tier1_capital_ratio": latest["tier1_capital_ratio"],
        "bank_capital_adequacy_ratio": latest["capital_adequacy_ratio"],
    }
    result["bank_excess_loan_provision_ratio"] = (
        result["bank_loan_provision_ratio"]
        - result["bank_required_loan_provision_ratio"]
    )
    failures: list[str] = []
    if result["bank_npl_ratio"] > 2.0:
        failures.append(f"不良贷款率{result['bank_npl_ratio']:.2f}%高于2%")
    if result["bank_provision_coverage_ratio"] < 150.0:
        failures.append(f"拨备覆盖率{result['bank_provision_coverage_ratio']:.2f}%低于150%")
    if result["bank_loan_provision_ratio"] < MIN_BANK_LOAN_PROVISION_RATIO:
        failures.append(
            f"贷款拨备率{result['bank_loan_provision_ratio']:.2f}%低于"
            f"{MIN_BANK_LOAN_PROVISION_RATIO:.1f}%"
        )
    if (
        result["bank_core_tier1_capital_ratio"] < 7.5
        or result["bank_tier1_capital_ratio"] < 8.5
        or result["bank_capital_adequacy_ratio"] < 10.5
    ):
        failures.append("资本充足率低于通用资本安全底线")
    result["bank_quality_gate_passed"] = not failures
    result["bank_quality_gate_reason"] = (
        "通过银行资产质量、拨备和资本安全底线"
        if not failures
        else "；".join(failures)
    )
    return result


def load_bank_quality_profiles(
    path: Path,
    required_codes: set[str],
) -> dict[str, dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError(f"缺少银行专项指标缓存：{path}")
    frame = pd.read_csv(path, dtype=str).fillna("")
    profiles: dict[str, dict[str, Any]] = {}
    for code in sorted(required_codes):
        records = frame[frame["ts_code"].astype(str).eq(code)].to_dict("records")
        profiles[code] = {"ts_code": code, "is_bank": True, **bank_quality_profile(records)}
    gaps = [code for code, profile in profiles.items() if profile["bank_quality_data_quality"] != "normal"]
    if gaps:
        raise RuntimeError("银行专项指标缓存不完整：" + "、".join(gaps))
    return profiles


def _load_bank_metrics_preparer():
    script = Path(__file__).with_name("prepare_bank_metrics.py")
    if not script.exists():
        raise RuntimeError(f"缺少银行专项自动准备脚本：{script}")
    import importlib.util

    spec = importlib.util.spec_from_file_location("prepare_bank_metrics", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.prepare_bank_metrics_cache


def ensure_bank_quality_profiles(
    path: Path,
    required_codes: set[str],
    run_date: str,
    *,
    preparer=None,
) -> dict[str, dict[str, Any]]:
    try:
        return load_bank_quality_profiles(path, required_codes)
    except RuntimeError:
        prepare_cache = preparer or _load_bank_metrics_preparer()
        prepare_cache(
            output_path=path,
            bank_codes=required_codes,
            as_of_date=compact_date(run_date),
        )
        return load_bank_quality_profiles(path, required_codes)


def merge_bank_quality_gate(
    structured: dict[str, Any],
    bank_profile: dict[str, Any],
) -> dict[str, Any]:
    """Merge the bank-only data contract and hard gate into the common profile."""
    merged = {**structured, **bank_profile, "is_bank": True, "is_financial": True}
    common_passed = bool(structured.get("structured_gate_passed"))
    bank_passed = bool(bank_profile.get("bank_quality_gate_passed"))
    if common_passed and bank_passed:
        return merged
    merged["structured_gate_passed"] = False
    if not common_passed:
        merged["structured_gate_status"] = str(
            structured.get("structured_gate_status") or "hard_gate_failed"
        )
        merged["structured_gate_reason"] = str(
            structured.get("structured_gate_reason") or "通用结构化门槛未通过"
        )
    else:
        merged["structured_gate_status"] = "hard_gate_failed"
        merged["structured_gate_reason"] = str(
            bank_profile.get("bank_quality_gate_reason") or "银行专项硬门槛未通过"
        )
    return merged


def _annual_values(frame: pd.DataFrame, field: str) -> dict[int, float]:
    values = _period_value_map(frame, field)
    return {int(period[:4]): value for period, value in values.items() if period.endswith("1231")}


def structured_factor_record_from_frames(
    *,
    ts_code: str,
    industry: str,
    main_business: str = "",
    business_scope: str = "",
    introduction: str = "",
    run_date: str,
    dividends: pd.DataFrame,
    income: pd.DataFrame,
    fina_indicator: pd.DataFrame,
    cashflow: pd.DataFrame,
    balance: pd.DataFrame,
    audit: pd.DataFrame,
    market_preflight_passed: bool,
    roe_stability_cutoff: float | None,
) -> dict[str, Any]:
    """Calculate one structured factor record from as-of-date source frames."""
    financial = is_financial_industry(industry)
    dividend = dividend_history_profile(dividends, run_date)
    anchor = dividend.get("dividend_anchor_year")
    annual_dividends = dividend.get("annual_cash_dividend_total_yuan", {})
    annual_profit = _annual_values(income, "n_income_attr_p")
    annual_profit_dedt = _annual_values(fina_indicator, "profit_dedt")
    annual_cfo = _annual_values(cashflow, "n_cashflow_act")
    annual_capex = _annual_values(cashflow, "c_pay_acq_const_fiolta")

    payout_ratios: dict[int, float] = {}
    cash_coverage: dict[int, float] = {}
    if isinstance(anchor, int):
        for year in range(anchor - 2, anchor + 1):
            dividend_total = finite_float(annual_dividends.get(year))
            profit = finite_float(annual_profit.get(year))
            cfo = finite_float(annual_cfo.get(year))
            if dividend_total is not None and dividend_total > 0 and profit is not None and profit > 0:
                payout_ratios[year] = dividend_total / profit
            if dividend_total is not None and dividend_total > 0 and cfo is not None:
                cash_coverage[year] = cfo / dividend_total
    payout = payout_ratio_gate(payout_ratios)
    profit_gate = profit_trend_gate(
        {
            year: annual_profit_dedt[year]
            for year in range(anchor - 3, anchor + 1)
            if isinstance(anchor, int) and year in annual_profit_dedt
        }
    ) if isinstance(anchor, int) else {"passed": False, "status": "data_gap", "three_year_change": None}
    cash_gate = (
        {"passed": True, "status": "not_applicable", "latest_cashflow_dividend_coverage": None, "median_cashflow_dividend_coverage_3y": None}
        if financial
        else cashflow_dividend_gate(cash_coverage)
    )

    roe_series = build_ttm_roe_series(income, balance)
    latest_twelve = roe_series.sort_values("end_date").tail(12)
    roe_observations = len(latest_twelve)
    roe_stability = (
        float(pd.to_numeric(latest_twelve["ttm_roe"], errors="coerce").std(ddof=0))
        if roe_observations == 12
        else None
    )
    roe_metrics = latest_roe_metrics(roe_series)
    stability_passed = bool(
        roe_stability is not None
        and roe_stability_cutoff is not None
        and roe_stability <= roe_stability_cutoff
    )

    ttm_cfo_series = build_ttm_series(cashflow, "n_cashflow_act", "ttm_cfo")
    latest_cfo = None
    interest_bearing_debt = None
    if not ttm_cfo_series.empty:
        latest_cfo_row = ttm_cfo_series.sort_values("end_date").iloc[-1]
        latest_cfo = finite_float(latest_cfo_row["ttm_cfo"])
        interest_bearing_debt = interest_bearing_debt_at(balance, str(latest_cfo_row["end_date"]))
    opcfd = (
        latest_cfo / interest_bearing_debt
        if not financial and latest_cfo is not None and interest_bearing_debt is not None and interest_bearing_debt > 0
        else None
    )
    opcfd_available = bool(
        not financial and latest_cfo is not None and interest_bearing_debt is not None
    )

    fcf_coverage = None
    if not financial and isinstance(anchor, int):
        years = range(anchor - 2, anchor + 1)
        if all(year in annual_cfo and year in annual_capex and finite_float(annual_dividends.get(year)) for year in years):
            total_dividends = sum(float(annual_dividends[year]) for year in years)
            if total_dividends > 0:
                fcf_coverage = sum(annual_cfo[year] - annual_capex[year] for year in years) / total_dividends

    audit_rows = audit.copy()
    if not audit_rows.empty and "end_date" in audit_rows.columns:
        audit_rows["_end_date"] = audit_rows["end_date"].astype(str).str.replace(r"\D", "", regex=True).str[:8]
        audit_rows = audit_rows[
            audit_rows["_end_date"].str.endswith("1231", na=False)
            & audit_rows["_end_date"].le(f"{anchor}1231" if isinstance(anchor, int) else compact_date(run_date))
        ].sort_values("_end_date")
    audit_result = str(audit_rows.iloc[-1].get("audit_result", "")) if not audit_rows.empty else ""
    audit_gate = audit_opinion_gate(audit_result)

    missing: list[str] = []
    if dividend.get("data_quality") != "normal":
        missing.append("分红锚定年度")
    if payout["status"] == "data_gap":
        missing.append("三年支付率")
    if roe_observations < 12 or roe_stability_cutoff is None:
        missing.append("12季度TTM ROE")
    if profit_gate["status"] == "data_gap":
        missing.append("四年扣非归母净利润")
    if not financial and cash_gate["status"] == "data_gap":
        missing.append("三年经营现金流覆盖")
    if audit_gate["status"] == "data_gap":
        missing.append("审计意见")
    if not financial and (not opcfd_available or fcf_coverage is None):
        missing.append("非金融现金质量因子")

    record: dict[str, Any] = {
        "ts_code": ts_code,
        "industry": industry,
        "main_business": main_business,
        "business_scope": business_scope,
        "introduction": introduction,
        "is_financial": financial,
        "is_bank": is_bank_industry(industry),
        "market_preflight_passed": bool(market_preflight_passed),
        **{key: value for key, value in dividend.items() if not key.startswith("annual_")},
        "three_year_average_payout_ratio": payout["three_year_average_payout_ratio"],
        "latest_payout_ratio": payout["latest_payout_ratio"],
        "payout_ratio_gate_passed": payout["passed"],
        "roe": roe_metrics["roe"],
        "droe": roe_metrics["droe"],
        "roe_stability_12q": roe_stability,
        "roe_stability_observations": roe_observations,
        "roe_stability_gate_passed": stability_passed,
        "profit_dedt_latest": annual_profit_dedt.get(anchor) if isinstance(anchor, int) else None,
        "profit_dedt_three_year_change": profit_gate["three_year_change"],
        "profit_trend_gate_passed": profit_gate["passed"],
        "latest_cashflow_dividend_coverage": cash_gate["latest_cashflow_dividend_coverage"],
        "median_cashflow_dividend_coverage_3y": cash_gate["median_cashflow_dividend_coverage_3y"],
        "cashflow_dividend_gate_passed": cash_gate["passed"],
        "audit_result": audit_result,
        "audit_gate_passed": audit_gate["passed"],
        "opcfd": opcfd,
        "interest_bearing_debt": interest_bearing_debt if not financial else None,
        "ttm_cfo_latest": latest_cfo if not financial else None,
        "fcf_dividend_coverage_3y": fcf_coverage,
        "annual_comparable_dps_json": json.dumps(
            dividend.get("annual_comparable_dps", {}), ensure_ascii=False, sort_keys=True
        ),
        "annual_cash_dividends_json": json.dumps(
            annual_dividends, ensure_ascii=False, sort_keys=True
        ),
        "annual_payout_ratios_json": json.dumps(
            payout_ratios, ensure_ascii=False, sort_keys=True
        ),
        "annual_profit_dedt_json": json.dumps(
            annual_profit_dedt, ensure_ascii=False, sort_keys=True
        ),
        "annual_cashflow_coverage_json": json.dumps(
            cash_coverage, ensure_ascii=False, sort_keys=True
        ),
        "ttm_roe_12q_json": json.dumps(
            {
                str(item["end_date"]): round(float(item["ttm_roe"]), 8)
                for _, item in latest_twelve.iterrows()
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        "structured_as_of_date": compact_date(run_date),
        "structured_source": "Tushare dividend/income/fina_indicator/cashflow/balancesheet/fina_audit",
        "structured_data_quality": "data_gap" if missing else "normal",
        "structured_data_reason": "缺少：" + "、".join(dict.fromkeys(missing)) if missing else "结构化门槛与计分数据完整",
    }
    record.update(structured_gate_profile(record))
    return record


INCOME_FIELDS = "ts_code,ann_date,f_ann_date,end_date,report_type,n_income_attr_p,update_flag"
BALANCE_FIELDS = (
    "ts_code,ann_date,f_ann_date,end_date,report_type,total_liab,"
    "total_hldr_eqy_exc_min_int,st_borr,lt_borr,bond_payable,"
    "non_cur_liab_due_1y,update_flag"
)
DIVIDEND_FIELDS = (
    "ts_code,end_date,ann_date,div_proc,stk_div,stk_bo_rate,stk_co_rate,"
    "cash_div_tax,record_date,ex_date,pay_date,imp_ann_date,base_date,base_share"
)
FINA_INDICATOR_FIELDS = "ts_code,ann_date,end_date,profit_dedt,ebit_to_interest,netdebt,update_flag"
CASHFLOW_FIELDS = (
    "ts_code,ann_date,f_ann_date,end_date,report_type,n_cashflow_act,"
    "c_pay_acq_const_fiolta,update_flag"
)


INCOME_INTEREST_FIELDS = "ts_code,ann_date,f_ann_date,end_date,report_type,ebit,fin_exp_int_exp,total_profit,update_flag"


def windowed_statement_fetch(method, *, ts_code: str, start_year: int, cutoff: str, fields: str) -> pd.DataFrame:
    """Fetch a statement history in STATEMENT_WINDOW_YEARS-year calls and concatenate."""
    frames = []
    end_year = int(cutoff[:4])
    year = start_year
    while year <= end_year:
        last = min(end_year, year + STATEMENT_WINDOW_YEARS - 1)
        data = method(
            ts_code=ts_code,
            start_date=f"{year}0101",
            end_date=cutoff if last == end_year else f"{last}1231",
            fields=fields,
        )
        frame = pd.DataFrame(data) if data is not None else pd.DataFrame()
        if not frame.empty:
            frames.append(frame)
        year = last + 1
    return pd.concat(frames, ignore_index=True).drop_duplicates() if frames else pd.DataFrame()


def fetch_structured_source_frames(
    client,
    ts_code: str,
    run_date: str,
    *,
    include_candidate_details: bool,
) -> dict[str, pd.DataFrame]:
    """Fetch common ROE inputs for all companies and detailed inputs only for preflight passers."""
    cutoff = compact_date(run_date)
    start_date = f"{int(cutoff[:4]) - STRUCTURED_HISTORY_YEARS}0101"
    income_data = client.income(
        ts_code=ts_code,
        start_date=start_date,
        end_date=cutoff,
        fields=INCOME_FIELDS,
    )
    balance_data = client.balancesheet(
        ts_code=ts_code,
        start_date=start_date,
        end_date=cutoff,
        fields=BALANCE_FIELDS,
    )
    frames = {
        "income": dedupe_statement_rows(pd.DataFrame(income_data), cutoff),
        "balance": dedupe_statement_rows(pd.DataFrame(balance_data), cutoff),
    }
    if not include_candidate_details:
        return frames

    dividend_data = client.dividend(
        ts_code=ts_code,
        fields=DIVIDEND_FIELDS,
    )
    fina_frame = windowed_statement_fetch(
        client.fina_indicator,
        ts_code=ts_code,
        start_year=int(cutoff[:4]) - PROFIT_HISTORY_YEARS,
        cutoff=cutoff,
        fields=FINA_INDICATOR_FIELDS,
    )
    cashflow_data = client.cashflow(
        ts_code=ts_code,
        start_date=start_date,
        end_date=cutoff,
        fields=CASHFLOW_FIELDS,
    )
    audit_data = client.fina_audit(ts_code=ts_code, start_date=start_date, end_date=cutoff)
    # EBIT and interest expense for the v2 debt-service gate (G7) fallback.
    interest_data = client.income(
        ts_code=ts_code,
        start_date=f"{int(cutoff[:4]) - INTEREST_HISTORY_YEARS}0101",
        end_date=cutoff,
        fields=INCOME_INTEREST_FIELDS,
    )
    frames.update(
        {
            "dividends": pd.DataFrame(dividend_data),
            "fina_indicator": dedupe_statement_rows(fina_frame, cutoff),
            "income_interest": dedupe_statement_rows(pd.DataFrame(interest_data), cutoff),
            "cashflow": dedupe_statement_rows(pd.DataFrame(cashflow_data), cutoff),
            "audit": dedupe_statement_rows(pd.DataFrame(audit_data), cutoff),
        }
    )
    return frames


STRUCTURED_FETCH_ATTEMPTS = 3
STRUCTURED_FETCH_RETRY_BASE_SECONDS = 2.0


def fetch_structured_source_frames_with_retry(
    client,
    ts_code: str,
    run_date: str,
    *,
    include_candidate_details: bool,
) -> dict[str, pd.DataFrame]:
    """Retry transient interface failures so one timeout does not become a data gap."""
    for attempt in range(1, STRUCTURED_FETCH_ATTEMPTS + 1):
        try:
            return fetch_structured_source_frames(
                client,
                ts_code,
                run_date,
                include_candidate_details=include_candidate_details,
            )
        except Exception as error:
            if attempt == STRUCTURED_FETCH_ATTEMPTS:
                raise RuntimeError(
                    f"已重试{STRUCTURED_FETCH_ATTEMPTS}次：{error}"
                ) from error
            delay = STRUCTURED_FETCH_RETRY_BASE_SECONDS * 2 ** (attempt - 1)
            print(
                f"{ts_code} 结构化接口失败（第{attempt}次：{type(error).__name__}），{delay:g}秒后重试",
                flush=True,
            )
            time.sleep(delay)
    raise AssertionError("unreachable")


def load_structured_factor_data(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return {}
    try:
        frame = pd.read_csv(path, dtype=str).fillna("")
    except pd.errors.EmptyDataError:
        return {}
    boolean_fields = {
        "is_financial",
        "is_bank",
        "market_preflight_passed",
        "structured_gate_passed",
        "three_year_continuous_dividend_passed",
        "payout_ratio_gate_passed",
        "roe_stability_gate_passed",
        "profit_trend_gate_passed",
        "cashflow_dividend_gate_passed",
        "audit_gate_passed",
    }
    records: dict[str, dict[str, Any]] = {}
    for _, row in frame.iterrows():
        ts_code = str(row.get("ts_code", "")).strip()
        if not ts_code:
            continue
        record: dict[str, Any] = {}
        for key, value in row.items():
            if key in boolean_fields:
                record[key] = str(value).strip().lower() in {"true", "1", "是"}
            else:
                record[key] = value
        records[ts_code] = record
    return records


def market_profile_for(
    ts_code: str,
    daily: pd.DataFrame,
    daily_basic: pd.DataFrame,
    prior_formal_top: dict[str, Any] | None,
) -> dict[str, Any]:
    """Five-year price and dividend-yield profile plus the market preflight result."""
    profile: dict[str, Any] = {"ts_code": ts_code}
    profile.update(price_profile(daily))
    profile.update(dividend_yield_profile(daily_basic))
    apply_dividend_yield_hysteresis(profile, ts_code, prior_formal_top)
    profile["market_preflight_passed"] = evaluate_market_dividend_preconditions(profile) is None
    return profile


def roe_stability_values(
    common: dict[str, dict[str, pd.DataFrame] | Exception],
) -> dict[str, float]:
    values: dict[str, float] = {}
    for code, result in common.items():
        if isinstance(result, Exception):
            continue
        roe_series = build_ttm_roe_series(result["income"], result["balance"])
        latest_twelve = roe_series.sort_values("end_date").tail(12)
        if len(latest_twelve) == 12:
            values[code] = float(
                pd.to_numeric(latest_twelve["ttm_roe"], errors="coerce").std(ddof=0)
            )
    return values


def build_structured_factor_records(
    run_date: str,
    constituents: pd.DataFrame,
    market_profiles: dict[str, dict[str, Any]],
    common: dict[str, dict[str, pd.DataFrame] | Exception],
    details: dict[str, dict[str, pd.DataFrame] | Exception],
) -> list[dict[str, Any]]:
    """Assemble structured gate records from as-of-date source frames.

    ``common`` holds income/balance frames for every constituent (the ROE
    stability cutoff is a cross-section statistic); ``details`` holds the
    remaining frames for market-preflight passers only.
    """
    expected_codes = constituents["ts_code"].astype(str).tolist()
    industries: dict[str, str] = {}
    company_profiles: dict[str, dict[str, str]] = {}
    for _, item in constituents.iterrows():
        code = str(item["ts_code"])
        industries[code] = str(item.get("industry", ""))
        company_profiles[code] = {
            field: str(item.get(field, "") or "").strip()
            for field in COMPANY_PROFILE_FIELDS
        }
    market_passers = {
        code for code, profile in market_profiles.items() if profile["market_preflight_passed"]
    }
    stability_values = roe_stability_values(common)
    cutoff = float(pd.Series(stability_values).quantile(0.80)) if stability_values else None
    stability_percentiles = (
        pd.Series(stability_values).rank(method="average", pct=True, ascending=True) * 100.0
        if stability_values
        else pd.Series(dtype=float)
    )

    records: list[dict[str, Any]] = []
    for code in expected_codes:
        profile = market_profiles[code]
        base = {
            "ts_code": code,
            "industry": industries[code],
            **company_profiles[code],
            "is_financial": is_financial_industry(industries[code]),
            "is_bank": is_bank_industry(industries[code]),
            "market_preflight_passed": bool(profile["market_preflight_passed"]),
            "roe_stability_12q": stability_values.get(code),
            "roe_stability_percentile": finite_float(stability_percentiles.get(code)),
        }
        if code not in market_passers:
            base.update(
                {
                    "structured_data_quality": "not_required",
                    "structured_data_reason": evaluate_market_dividend_preconditions(profile),
                    "structured_gate_passed": False,
                    "structured_gate_status": "hard_gate_failed",
                    "structured_gate_reason": "未通过五年股息率市场预筛",
                }
            )
            records.append(base)
            continue
        detail = details.get(code)
        if isinstance(detail, Exception) or detail is None:
            base.update(
                {
                    "structured_data_quality": "data_gap",
                    "structured_data_reason": f"结构化接口失败：{detail}",
                    "structured_gate_passed": False,
                    "structured_gate_status": "data_gap",
                    "structured_gate_reason": "结构化接口数据不足",
                }
            )
            records.append(base)
            continue
        record = structured_factor_record_from_frames(
            ts_code=code,
            industry=industries[code],
            main_business=company_profiles[code]["main_business"],
            business_scope=company_profiles[code]["business_scope"],
            introduction=company_profiles[code]["introduction"],
            run_date=run_date,
            dividends=detail["dividends"],
            income=detail["income"],
            fina_indicator=detail["fina_indicator"],
            cashflow=detail["cashflow"],
            balance=detail["balance"],
            audit=detail["audit"],
            market_preflight_passed=True,
            roe_stability_cutoff=cutoff,
        )
        record["roe_stability_percentile"] = finite_float(stability_percentiles.get(code))
        records.append(record)

    return records


def apply_rule_version(
    records: list[dict[str, Any]],
    details: dict[str, dict[str, pd.DataFrame] | Exception],
    rule_version: str,
    rule_params: dict[str, Any],
) -> list[dict[str, Any]]:
    """Add the rule version's own factors and gates to the common structured records."""
    if rule_version == "v1":
        return [{**record, "rule_version": "v1"} for record in records]
    rules_v2 = load_rules_v2()
    params_json = json.dumps(rule_params, sort_keys=True)
    updated = []
    for record in records:
        frames = details.get(str(record["ts_code"]))
        frames = frames if isinstance(frames, dict) else {}
        applied = rules_v2.apply_v2(record, frames.get("fina_indicator"), rule_params, frames.get("income_interest"))
        applied["rule_params_json"] = params_json
        updated.append(applied)
    return updated


def ensure_structured_factor_data(
    run_date: str,
    market_dir: Path,
    *,
    skip_fetch: bool,
    limit: int | None = None,
    sleep_seconds: float = 0.2,
    max_workers: int = 4,
    pro=None,
    prior_formal_top: dict[str, Any] | None = None,
    rule_version: str = DEFAULT_RULE_VERSION,
) -> dict[str, dict[str, Any]]:
    rule_params = formal_rule_params(rule_version)
    rule_params_json = json.dumps(rule_params, sort_keys=True)
    constituents = pd.read_csv(market_dir / "constituents.csv", dtype=str).fillna("")
    if limit:
        constituents = constituents.head(limit)
    expected_codes = constituents["ts_code"].astype(str).tolist()
    path = market_dir / STRUCTURED_FACTORS_FILENAME
    if skip_fetch:
        cached = load_structured_factor_data(path)
        missing = [code for code in expected_codes if code not in cached]
        if missing:
            raise RuntimeError(
                f"缓存缺少{len(missing)}只成分股的结构化因子；请取消--skip-fetch刷新"
            )
        # v2 gates need source frames that are not cached, so a cache built
        # under another rule version (or older caches without the column,
        # which are v1) cannot be reused.
        stale = [
            code
            for code in expected_codes
            if (cached[code].get("rule_version") or "v1") != rule_version
            or (rule_version != "v1" and cached[code].get("rule_params_json", "") != rule_params_json)
        ]
        if stale:
            raise RuntimeError(
                f"缓存的结构化因子不是按规则{rule_version}生成（{len(stale)}只）；请取消--skip-fetch刷新"
            )
        return {code: cached[code] for code in expected_codes}

    client = pro or tushare_pro()
    market_profiles = {
        str(item["ts_code"]): market_profile_for(
            str(item["ts_code"]),
            read_market_csv(market_dir, "daily", str(item["ts_code"])),
            read_market_csv(market_dir, "daily_basic", str(item["ts_code"])),
            prior_formal_top,
        )
        for _, item in constituents.iterrows()
    }
    market_passers = {
        code for code, profile in market_profiles.items() if profile["market_preflight_passed"]
    }

    def fetch_common(code: str) -> tuple[str, dict[str, pd.DataFrame] | Exception]:
        try:
            frames = fetch_structured_source_frames_with_retry(
                client,
                code,
                run_date,
                include_candidate_details=False,
            )
            if sleep_seconds:
                time.sleep(sleep_seconds)
            return code, frames
        except Exception as exc:  # preserve per-company data gaps instead of losing the full run
            return code, exc

    common: dict[str, dict[str, pd.DataFrame] | Exception] = {}
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        for code, result in executor.map(fetch_common, expected_codes):
            common[code] = result

    def fetch_detail(code: str) -> tuple[str, dict[str, pd.DataFrame] | Exception]:
        try:
            frames = fetch_structured_source_frames_with_retry(
                client,
                code,
                run_date,
                include_candidate_details=True,
            )
            if sleep_seconds:
                time.sleep(sleep_seconds)
            return code, frames
        except Exception as exc:
            return code, exc

    details: dict[str, dict[str, pd.DataFrame] | Exception] = {}
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        for code, result in executor.map(fetch_detail, sorted(market_passers)):
            details[code] = result

    records = build_structured_factor_records(
        run_date,
        constituents,
        market_profiles,
        common,
        details,
    )
    records = apply_rule_version(records, details, rule_version, rule_params)
    pd.DataFrame(records).to_csv(path, index=False, encoding="utf-8-sig")
    return {str(record["ts_code"]): record for record in records}


def ensure_market_data(
    run_date: str,
    skip_fetch: bool,
    refresh_constituents: bool = False,
    fixed_constituents_file: Path | None = None,
    sleep_seconds: float = 0.2,
    max_workers: int = 4,
    data_root: Path | str | None = None,
) -> Path:
    out = output_dir(run_date, data_root=data_root)
    market = out / "market_data"
    if skip_fetch:
        constituents_path = market / "constituents.csv"
        if not constituents_path.exists():
            raise RuntimeError("--skip-fetch缺少constituents.csv")
        constituents = pd.read_csv(constituents_path, dtype=str).fillna("")
        source_meta = market / "csi_constituents_source.json"
        source_file = market / "csi_constituents_source.xls"
        if not source_meta.exists() or not source_file.exists():
            raise RuntimeError("--skip-fetch缺少经校验的中证成分股源文件与元数据")
        meta = json.loads(source_meta.read_text(encoding="utf-8"))
        if (hashlib.sha256(source_file.read_bytes()).hexdigest() != meta.get("sha256")
                or not constituents["trade_date"].eq(meta.get("snapshot_date")).all()):
            raise RuntimeError("--skip-fetch成分股缓存与中证源文件不一致")
        missing_fields = [
            field
            for field in ["main_business", "business_scope", "introduction"]
            if field not in constituents.columns
            or not constituents[field].astype(str).str.strip().ne("").all()
        ]
        if missing_fields:
            raise RuntimeError(
                "--skip-fetch的成分股缓存缺少完整stock_company资料："
                + "、".join(missing_fields)
            )
        return market
    return fetch_hs300_market_data(
        compact_date(run_date),
        start_market=five_year_start(run_date),
        sleep_seconds=sleep_seconds,
        market_dir=market,
        refresh_constituents=refresh_constituents,
        fixed_constituents_file=fixed_constituents_file,
        max_workers=max_workers,
    )


def build_candidate_rows(
    market_dir: Path | None,
    limit: int | None = None,
    structured_factors: dict[str, dict[str, Any]] | None = None,
    prior_formal_top: dict[str, Any] | None = None,
    *,
    constituents: pd.DataFrame | None = None,
    frame_loader: Callable[[str, str], pd.DataFrame] | None = None,
    factor_weights: dict[str, dict[str, float]] | None = None,
    scorer: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Gate and rank one cross-section.

    ``scorer`` replaces the default rule-v1 scoring (rule v2 passes
    ``rules_v2.score_v2``).
    """
    if constituents is None:
        assert market_dir is not None
        constituents = pd.read_csv(market_dir / "constituents.csv", dtype=str)
    if frame_loader is None:
        assert market_dir is not None
        frame_loader = lambda prefix, code: read_market_csv(market_dir, prefix, code)
    if limit:
        constituents = constituents.head(limit)
    rows: list[dict[str, Any]] = []
    for _, item in constituents.iterrows():
        ts_code = str(item.get("ts_code", "")).strip()
        daily = frame_loader("daily", ts_code)
        daily_basic = frame_loader("daily_basic", ts_code)
        row: dict[str, Any] = {
            "ts_code": ts_code,
            "name": str(item.get("name", "")).strip(),
            "industry": str(item.get("industry", "") or "").strip(),
            "weight": str(item.get("weight", "") or "").strip(),
            **{
                field: str(item.get(field, "") or "").strip()
                for field in COMPANY_PROFILE_FIELDS
            },
        }
        row.update(price_profile(daily))
        row.update(dividend_yield_profile(daily_basic))
        apply_dividend_yield_hysteresis(row, ts_code, prior_formal_top)
        market_reason = evaluate_market_dividend_preconditions(row)
        row["market_preflight_passed"] = market_reason is None
        structured = (structured_factors or {}).get(ts_code, {})
        if structured:
            row.update(structured)
        rows.append(row)

    scoring_input = [row for row in rows if bool(row.get("market_preflight_passed"))]
    scored = (
        scorer(scoring_input)
        if scorer is not None
        else score_cross_section(scoring_input, factor_weights=factor_weights)
    )
    scored_by_code = {row["ts_code"]: row for row in scored}
    finalized: list[dict[str, Any]] = []
    for row in rows:
        ts_code = row["ts_code"]
        if ts_code in scored_by_code:
            row.update(scored_by_code[ts_code])
        market_reason = evaluate_market_dividend_preconditions(row)
        if market_reason:
            row.update(real_estate_relevance(row))
            row["real_estate_related"] = is_real_estate_related(row)
            row["selected"] = "否"
            row["selection_status"] = "hard_gate_failed"
            row["selected_reason"] = market_reason
            row["data_quality"] = "not_required"
            row["data_quality_reason"] = "市场预筛已决定落选，不获取后续结构化明细"
            finalized.append(row)
            continue
        if not row.get("structured_gate_passed"):
            status = str(row.get("structured_gate_status") or "data_gap")
            row.update(real_estate_relevance(row))
            row["real_estate_related"] = is_real_estate_related(row)
            row["selected"] = "否"
            row["selection_status"] = status
            row["selected_reason"] = str(row.get("structured_gate_reason") or "结构化门槛未通过")
            row["data_quality"] = "weak" if status == "data_gap" else "not_required"
            row["data_quality_reason"] = str(row.get("structured_data_reason") or row["selected_reason"])
            finalized.append(row)
            continue

        score = finite_float(row.get("dividend_score_total"))
        row.update(portfolio_scope_gate_profile(row))
        row["real_estate_related"] = is_real_estate_related(row)
        if score is None:
            row["selected"] = "否"
            row["selection_status"] = "data_gap"
            row["selected_reason"] = str(row.get("score_reason") or "计分因子数据不足")
        else:
            row["selected"] = "是"
            row["selection_status"] = "selected"
            row["selected_reason"] = "通过全部硬门槛，进入质量红利正式排名"
        finalized.append(row)

    rows = finalized
    _, capped = _industry_capped_top(rows)
    for row in capped:
        row["selected"] = "否"
        row["selection_status"] = "concentration_capped"
        row["selected_reason"] = (
            f"行业上限：{row.get('industry', '')}已有{industry_cap(str(row.get('industry', '')))}家进入Top10，"
            "保留得分与全量排名，由下一名顺延"
        )
    rows.sort(
        key=lambda record: (
            1
            if record.get("selected") == "是"
            or record.get("selection_status") == "concentration_capped"
            else 0,
            finite_float(record.get("dividend_score_total")) or -1,
        ),
        reverse=True,
    )
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
        row["top10_rank"] = ""
    # Position inside the formal Top10 (1..10, no gaps left by capped companies);
    # ``rank`` stays the full-universe rank for audit.
    for position, row in enumerate(formal_top_candidates(rows), start=1):
        row["top10_rank"] = position
    return rows


def csv_ready(row: dict[str, Any], fields: list[str] | None = None) -> dict[str, str]:
    result: dict[str, str] = {}
    for field in fields or CSV_FIELDS:
        value = row.get(field, "")
        if isinstance(value, bool):
            result[field] = "是" if value else "否"
        elif isinstance(value, float):
            result[field] = fmt_num(value, 4 if "ratio" in field or "cv" in field else 2)
        else:
            result[field] = str(value)
    return result


def write_csv(
    rows: list[dict[str, Any]],
    path: Path,
    extra_fields: list[str] | None = None,
) -> None:
    fields = CSV_FIELDS + [field for field in (extra_fields or []) if field not in CSV_FIELDS]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(csv_ready(row, fields))


def constituent_source_note(path: Path) -> str:
    meta_path = path.parent / "market_data" / "csi_constituents_source.json"
    if not meta_path.exists():
        return "成分股名单来源及日期未记录。"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    note = f"中证成分股名单日期：{meta.get('snapshot_date', '未知')}；最近交易日：{meta.get('latest_trade_date', '未知')}；滞后交易日：{meta.get('lag_trading_days', '未知')}。"
    return note + (f" {meta['warning']}。" if meta.get("warning") else "")


V1_SCORING_NOTE = "非金融评分：ROE 20%、DROE 10%、OPCFD 15%、三年自由现金流覆盖20%、五年股息率CV 10%、连续分红年数10%、五年可比DPS CAGR 15%。其他金融评分：ROE 30%、DROE 15%、五年股息率CV 20%、连续分红年数20%、五年可比DPS CAGR 15%。银行独立评分：ROE 15%、DROE 5%、不良质量15%、拨备质量10%、资本韧性10%、净息差质量10%、成本收入比5%、股息率CV 10%、连续分红10%、DPS CAGR 10%。"
FACTOR_LABELS = {
    "roe": "ROE",
    "droe": "DROE",
    "fcf_dividend_coverage_3y": "三年FCF覆盖",
    "bank_npl_quality": "不良质量",
    "bank_provision_quality": "拨备质量",
    "bank_capital_resilience": "资本韧性",
    "bank_nim_quality": "净息差质量",
    "bank_cost_income_ratio": "成本收入比",
}


def _weights_text(weights: dict[str, float]) -> str:
    return "、".join(f"{FACTOR_LABELS.get(name, name)} {weight * 100:g}%" for name, weight in weights.items())


def rule_notes(rule_version: str) -> tuple[str, str]:
    """(gate note, scoring note) for the report header."""
    common = "本策略先执行五年股息率市场预筛，再执行结构化盈利、支付率、现金流、审计和主营范围硬门槛。主营业务来自Tushare stock_company；直接地产主业排除。硬门槛不计分。"
    if rule_version == "v1":
        return common, V1_SCORING_NOTE
    rules_v2 = load_rules_v2()
    params = rules_v2.resolve_params(rules_v2.FORMAL_RULE_PARAMS)
    gates = (
        common
        + f"规则v2另加两道非金融门槛：EBIT利息覆盖不低于{params['interest_coverage_min']:g}倍（净负债不为正直接通过）；"
        + f"最新扣非利润不超过10年中位数的{params['cycle_gate_max_ratio']:g}倍（排除疑似周期高点）。"
    )
    weights = rules_v2.quality_weights("metrics_file")
    block = float(params["record_block_weight"])
    scoring = (
        f"规则v2评分：总分 = {block * 100:g}% 分红记录 + {(1 - block) * 100:g}% 经营质量。"
        f"分红记录在全部通过门槛的公司之间排名：连续分红年数（封顶{rules_v2.CONSECUTIVE_YEARS_CAP}年）、"
        "近10年DPS削减次数、五年可比DPS CAGR，各占1/3。经营质量在组内排名："
        f"非金融 {_weights_text(weights['nonfinancial'])}；银行 {_weights_text(weights['bank'])}；"
        f"其他金融 {_weights_text(weights['financial'])}。"
    )
    return gates, scoring


def v2_gate_exclusions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Companies whose only failed hard gate was a v2 gate (debt service or cross-cycle)."""
    return [
        row
        for row in rows
        if bool(row.get("market_preflight_passed"))
        and (
            str(row.get("structured_gate_reason", "")).startswith("最新扣非利润为10年中位数")
            or str(row.get("structured_gate_reason", "")).startswith("EBIT利息覆盖")
            or str(row.get("structured_gate_reason", "")) == "缺少最新年报EBIT利息覆盖倍数"
        )
    ]


def write_markdown(
    rows: list[dict[str, Any]],
    run_date: str,
    path: Path,
    *,
    stage_counts: dict[str, int] | None = None,
    rule_version: str = "v1",
) -> None:
    counts = stage_counts or {}
    top = formal_top_candidates(rows)
    gate_note, scoring_note = rule_notes(rule_version)
    lines = [
        f"# 沪深300质量红利策略筛选报告 {run_date}",
        "",
        constituent_source_note(path),
        "",
        f"评分规则：{rule_version}",
        "",
        gate_note,
        "",
        scoring_note,
        "",
        "## 阶段数量",
        "",
        f"- 沪深300总数：{counts.get('universe', len(rows))}",
        f"- 市场预筛通过：{counts.get('market_passers', sum(bool(row.get('market_preflight_passed')) for row in rows))}",
        f"- 结构化门槛通过：{counts.get('structured_passers', sum(bool(row.get('structured_gate_passed')) for row in rows))}",
        f"- 最终入选：{counts.get('selected', len(top))}",
        "",
        "## 正式 Top10",
        "",
    ]
    if rule_version == "v2":
        lines.extend(
            [
                "| 排名 | 全量排名 | 公司 | 代码 | 总分 | 分红记录分 | 经营质量分 | 主营业务 | ROE | DROE | 三年FCF覆盖 | 连续分红年数 | 10年DPS削减次数 | 五年DPS CAGR | 扣非/10年中位数 |",
                "|---:|---:|---|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for row in top:
            lines.append(
                f"| {row.get('top10_rank', '')} | {row.get('rank', '')} | {row.get('name', '')} | {row.get('ts_code', '')} | "
                f"{fmt_num(row.get('dividend_score_total'))} | {fmt_num(row.get('record_score'))} | {fmt_num(row.get('quality_score'))} | "
                f"{str(row.get('main_business', ''))[:40]} | {fmt_num(row.get('roe'))} | {fmt_num(row.get('droe'))} | "
                f"{fmt_num(row.get('fcf_dividend_coverage_3y'))} | {row.get('consecutive_dividend_years', '')} | "
                f"{row.get('dps_cut_count_10y', '')} | {fmt_num((finite_float(row.get('dps_cagr_5y')) or 0) * 100)}% | "
                f"{fmt_num(row.get('profit_dedt_to_10y_median'))} |"
            )
        excluded = v2_gate_exclusions(rows)
        lines.extend(["", "## v2 门槛排除", ""])
        if excluded:
            lines.extend(["| 公司 | 代码 | 行业 | 原因 |", "|---|---|---|---|"])
            for row in excluded:
                lines.append(
                    f"| {row.get('name', '')} | {row.get('ts_code', '')} | {row.get('industry', '')} | {row.get('structured_gate_reason', '')} |"
                )
        else:
            lines.append("本期没有公司仅因 v2 新增门槛落选。")
        lines.extend(["", "## 全量审计", "", "完整300只股票、所有硬门槛值、因子百分位、权重贡献和落选原因见同目录CSV与HTML。"])
        path.write_text("\n".join(lines), encoding="utf-8")
        return
    lines.extend(
        [
            "| 排名 | 全量排名 | 公司 | 代码 | 总分 | 主营业务 | ROE | DROE | OPCFD | 三年FCF覆盖 | 股息率CV | 连续分红年数 | 五年DPS CAGR |",
            "|---:|---:|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in top:
        lines.append(
            f"| {row.get('top10_rank', '')} | {row.get('rank', '')} | {row.get('name', '')} | {row.get('ts_code', '')} | "
            f"{fmt_num(row.get('dividend_score_total'))} | {str(row.get('main_business', ''))[:40]} | "
            f"{fmt_num(row.get('roe'))} | {fmt_num(row.get('droe'))} | {fmt_num(row.get('opcfd'), 4)} | "
            f"{fmt_num(row.get('fcf_dividend_coverage_3y'))} | {fmt_num(row.get('dividend_yield_cv_5y'), 4)} | "
            f"{row.get('consecutive_dividend_years', '')} | {fmt_num((finite_float(row.get('dps_cagr_5y')) or 0) * 100)}% |"
        )
    lines.extend(["", "## 全量审计", "", "完整300只股票、所有硬门槛值、因子百分位、权重贡献和落选原因见同目录CSV与HTML。"])
    path.write_text("\n".join(lines), encoding="utf-8")


def write_html(
    rows: list[dict[str, Any]],
    run_date: str,
    path: Path,
    *,
    stage_counts: dict[str, int] | None = None,
    rule_version: str = "v1",
) -> None:
    counts = stage_counts or {}
    body: list[str] = []
    for row in rows:
        cls = "selected" if row.get("selected") == "是" else ""
        if rule_version == "v2":
            body.append(
                f"<tr class='{cls}'>"
                f"<td>{row.get('rank','')}</td><td>{fmt_num(row.get('dividend_score_total'))}</td>"
                f"<td>{fmt_num(row.get('record_score'))}</td><td>{fmt_num(row.get('quality_score'))}</td>"
                f"<td>{escape(str(row.get('selected','否')))}</td>"
                f"<td><strong>{escape(str(row.get('name','')))}</strong><br><span>{escape(str(row.get('ts_code','')))}</span></td>"
                f"<td>{escape(str(row.get('industry','')))}</td><td>{escape(str(row.get('main_business','未取得')))}</td>"
                f"<td>{fmt_num(row.get('roe'))}</td><td>{fmt_num(row.get('droe'))}</td>"
                f"<td>{fmt_num(row.get('fcf_dividend_coverage_3y'))}</td>"
                f"<td>{escape(str(row.get('consecutive_dividend_years','')))}</td>"
                f"<td>{escape(str(row.get('dps_cut_count_10y','')))}</td>"
                f"<td>{fmt_num((finite_float(row.get('dps_cagr_5y')) or 0)*100)}%</td>"
                f"<td>{fmt_num(row.get('profit_dedt_to_10y_median'))}</td>"
                f"<td>{escape(str(row.get('selection_status','')))}</td>"
                f"<td>{escape(str(row.get('selected_reason','')))}</td></tr>"
            )
            continue
        body.append(
            f"<tr class='{cls}'>"
            f"<td>{row.get('rank','')}</td><td>{fmt_num(row.get('dividend_score_total'))}</td>"
            f"<td>{escape(str(row.get('selected','否')))}</td>"
            f"<td><strong>{escape(str(row.get('name','')))}</strong><br><span>{escape(str(row.get('ts_code','')))}</span></td>"
            f"<td>{escape(str(row.get('industry','')))}</td><td>{escape(str(row.get('main_business','未取得')))}</td>"
            f"<td>{fmt_num(row.get('roe'))}</td><td>{fmt_num(row.get('droe'))}</td>"
            f"<td>{fmt_num(row.get('opcfd'),4)}</td><td>{fmt_num(row.get('fcf_dividend_coverage_3y'))}</td>"
            f"<td>{fmt_num(row.get('dividend_yield_cv_5y'),4)}</td>"
            f"<td>{escape(str(row.get('consecutive_dividend_years','')))}</td>"
            f"<td>{fmt_num((finite_float(row.get('dps_cagr_5y')) or 0)*100)}%</td>"
            f"<td>{escape(str(row.get('selection_status','')))}</td>"
            f"<td>{escape(str(row.get('selected_reason','')))}</td></tr>"
        )
    selected_count = sum(row.get("selected") == "是" for row in rows)
    gate_note, scoring_note = rule_notes(rule_version)
    if rule_version == "v2":
        header_cells = ["排名", "总分", "分红记录分", "经营质量分", "入选", "公司", "行业", "主营业务", "ROE", "DROE", "三年FCF覆盖", "连续分红年数", "10年DPS削减次数", "五年DPS CAGR", "扣非/10年中位数", "状态", "理由"]
        rule_paragraph = f"<p>评分规则 v2。{escape(gate_note)}</p><p>{escape(scoring_note)}</p>"
    else:
        header_cells = ["排名", "总分", "入选", "公司", "行业", "主营业务", "ROE", "DROE", "OPCFD", "三年FCF覆盖", "股息率CV", "连续分红年数", "五年DPS CAGR", "状态", "理由"]
        rule_paragraph = "<p>市场预筛后执行支付率、TTM ROE稳定性、扣非利润、现金流覆盖、审计和主营范围硬门槛；银行另执行三年专项数据完整性、不良率、拨备和资本安全门槛，并使用独立评分模型。主营业务来自Tushare stock_company，直接地产主业排除。连续分红年数不封顶。</p>"
    header_html = "".join(f"<th>{cell}</th>" for cell in header_cells)
    html = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>沪深300质量红利策略 {run_date}</title><style>
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;margin:0;background:#f7f8fa;color:#1f2937}}
header,main{{padding:24px 32px}}header{{background:#102a43;color:#fff}}.summary{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:18px 0}}
.card{{background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:12px}}.card b{{display:block;font-size:22px}}table{{width:100%;border-collapse:collapse;background:#fff}}
th,td{{padding:8px;border-bottom:1px solid #e5e7eb;font-size:12px;vertical-align:top}}th{{background:#eef2f7}}tr.selected{{background:#f0fdf4}}span{{color:#64748b}}
</style></head><body><header><h1>沪深300质量红利策略</h1><p>分析日期：{run_date}｜硬门槛先行｜银行、其他金融与非金融分别评分｜正式 Top10</p></header><main>
<section class="summary"><div class="card">沪深300总数<b>{counts.get('universe',len(rows))}</b></div><div class="card">市场预筛通过<b>{counts.get('market_passers',sum(bool(r.get('market_preflight_passed')) for r in rows))}</b></div>
<div class="card">结构化门槛通过<b>{counts.get('structured_passers',sum(bool(r.get('structured_gate_passed')) for r in rows))}</b></div><div class="card">最终入选<b>{counts.get('selected',selected_count)}</b></div></section>
<p>{escape(constituent_source_note(path))}</p>
{rule_paragraph}
<table><thead><tr>{header_html}</tr></thead>
<tbody>{''.join(body)}</tbody></table></main></body></html>"""
    path.write_text(html, encoding="utf-8")


def run_strategy(
    run_date: str,
    skip_fetch: bool = False,
    refresh_constituents: bool = False,
    fixed_constituents_file: Path | None = None,
    limit: int | None = None,
    sleep_seconds: float = 0.2,
    max_workers: int = 4,
    data_root: Path | str | None = None,
    rule_version: str = DEFAULT_RULE_VERSION,
) -> Path:
    run_date = compact_date(run_date)
    rule_params = formal_rule_params(rule_version)
    persistent_data_root = resolve_data_root(data_root)
    out = output_dir(run_date, data_root=persistent_data_root)
    out.mkdir(parents=True, exist_ok=True)
    market_dir = ensure_market_data(
        run_date,
        skip_fetch=skip_fetch,
        refresh_constituents=refresh_constituents,
        fixed_constituents_file=fixed_constituents_file,
        sleep_seconds=sleep_seconds,
        max_workers=max_workers,
        data_root=persistent_data_root,
    )
    prior_formal_top = load_prior_formal_top(run_date, data_root=persistent_data_root)
    structured_factors = ensure_structured_factor_data(
        run_date,
        market_dir,
        skip_fetch=skip_fetch,
        limit=limit,
        sleep_seconds=sleep_seconds,
        max_workers=max_workers,
        prior_formal_top=prior_formal_top,
        rule_version=rule_version,
    )
    bank_codes = {
        ts_code
        for ts_code, record in structured_factors.items()
        if bool(record.get("is_bank")) and bool(record.get("market_preflight_passed"))
    }
    if bank_codes:
        bank_profiles = ensure_bank_quality_profiles(
            market_dir / BANK_METRICS_FILENAME,
            bank_codes,
            run_date,
        )
        for ts_code, bank_profile in bank_profiles.items():
            structured_factors[ts_code] = merge_bank_quality_gate(
                structured_factors[ts_code],
                bank_profile,
            )
    eligible_codes = {
        ts_code
        for ts_code, record in structured_factors.items()
        if bool(record.get("market_preflight_passed"))
        and bool(record.get("structured_gate_passed"))
    }
    scorer = None
    extra_fields = None
    if rule_version == "v2":
        rules_v2 = load_rules_v2()
        scorer = lambda candidates: rules_v2.score_v2(candidates, rule_params, "metrics_file")
        extra_fields = rules_v2.EXTRA_CSV_FIELDS
    rows = build_candidate_rows(
        market_dir,
        limit=limit,
        structured_factors=structured_factors,
        prior_formal_top=prior_formal_top,
        scorer=scorer,
    )
    stage_counts = {
        "universe": len(rows),
        "market_passers": sum(bool(record.get("market_preflight_passed")) for record in structured_factors.values()),
        "structured_passers": len(eligible_codes),
        "selected": sum(row.get("selected") == "是" for row in rows),
    }
    pd.DataFrame([stage_counts]).to_csv(
        market_dir / "pipeline_stage_counts.csv",
        index=False,
        encoding="utf-8-sig",
    )
    write_csv(rows, out / f"hs300-dividend-candidates-{run_date}.csv", extra_fields=extra_fields)
    write_csv(
        formal_top_candidates(rows),
        out / f"hs300-dividend-top10-{run_date}.csv",
        extra_fields=extra_fields,
    )
    write_markdown(
        rows,
        run_date,
        out / f"hs300-dividend-report-{run_date}.md",
        stage_counts=stage_counts,
        rule_version=rule_version,
    )
    write_html(
        rows,
        run_date,
        out / f"hs300-dividend-dashboard-{run_date}.html",
        stage_counts=stage_counts,
        rule_version=rule_version,
    )
    return out


def _load_forward_dividend_module():
    script = Path(__file__).with_name("run_forward_dividend_analysis.py")
    if not script.exists():
        raise RuntimeError(f"缺少前瞻分红分析脚本：{script}")
    import importlib.util

    script_dir = str(script.parent)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    spec = importlib.util.spec_from_file_location("run_forward_dividend_analysis", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run_pipeline(
    run_date: str,
    skip_fetch: bool = False,
    refresh_constituents: bool = False,
    fixed_constituents_file: Path | None = None,
    limit: int | None = None,
    sleep_seconds: float = 0.2,
    max_workers: int = 4,
    skip_forward_dividend: bool = False,
    emit_forward_table: bool = True,
    data_root: Path | str | None = None,
    *,
    rule_version: str = DEFAULT_RULE_VERSION,
    strategy_runner=None,
    forward_module_loader=None,
) -> dict[str, Any]:
    normalized_run_date = compact_date(run_date)
    persistent_data_root = resolve_data_root(data_root)
    run_formal = strategy_runner or run_strategy
    out = run_formal(
        normalized_run_date,
        skip_fetch=skip_fetch,
        refresh_constituents=refresh_constituents,
        fixed_constituents_file=fixed_constituents_file,
        limit=limit,
        sleep_seconds=sleep_seconds,
        max_workers=max_workers,
        data_root=persistent_data_root,
        rule_version=rule_version,
    )
    result = {"output_dir": out, "forward": None, "forward_console": ""}
    if skip_forward_dividend:
        return result

    load_forward = forward_module_loader or _load_forward_dividend_module
    forward_module = load_forward()
    top10_path = out / f"hs300-dividend-top10-{normalized_run_date}.csv"
    forward = forward_module.run_forward_analysis(
        run_date=normalized_run_date,
        top10_path=top10_path,
        output_dir=out,
    )
    result["forward"] = forward
    result["forward_console"] = forward_module.format_console_top10(
        forward["rows"],
        run_date=normalized_run_date,
    )
    if emit_forward_table:
        print(result["forward_console"])
    return result


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run CSI 300 quality-dividend strategy screen.")
    parser.add_argument("--run-date", default=datetime.now().strftime("%Y%m%d"))
    parser.add_argument("--skip-fetch", action="store_true")
    parser.add_argument(
        "--data-root",
        type=Path,
        help=f"Persistent data directory (default: ${DATA_ROOT_ENV} or the workspace data directory)",
    )
    parser.add_argument(
        "--skip-forward-dividend",
        action="store_true",
        help="Only write the formal strategy outputs; skip the default Top10 forward-dividend stage",
    )
    parser.add_argument("--refresh-constituents", action="store_true")
    parser.add_argument("--fixed-constituents-file", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--sleep-seconds", type=float, default=0.2)
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument(
        "--rule-version",
        choices=RULE_VERSIONS,
        default=DEFAULT_RULE_VERSION,
        help="Scoring rule (default v2 with the nonfinancial cross-cycle gate; v1 is the pre-2026-11 rule)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    result = run_pipeline(
        args.run_date,
        skip_fetch=args.skip_fetch,
        skip_forward_dividend=args.skip_forward_dividend,
        refresh_constituents=args.refresh_constituents,
        fixed_constituents_file=args.fixed_constituents_file,
        limit=args.limit,
        sleep_seconds=args.sleep_seconds,
        max_workers=args.max_workers,
        emit_forward_table=False,
        data_root=args.data_root,
        rule_version=args.rule_version,
    )
    print(result["output_dir"])
    if result["forward"] is not None:
        print(json.dumps(result["forward"]["status"], ensure_ascii=False, indent=2))
        print()
        print(result["forward_console"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
