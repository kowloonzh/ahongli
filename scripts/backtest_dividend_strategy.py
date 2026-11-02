#!/usr/bin/env python3
"""Point-in-time historical backtest of the AHongli formal screen.

Each rebalance date is screened with only the data that was public on that
date: CSI 300 membership from the monthly index-weight snapshot, financial
statements filtered by announcement date, implemented dividends filtered by
event date, and five years of prices ending on the rebalance date. The screen
itself is the production code path (``run_a_dividend_strategy``); this module
only supplies as-of-date frames and measures what happened afterwards.

Raw Tushare history is fetched once per security and cached under
``<data root>/a_dividend_backtest/raw/<fetch end date>/`` so a backtest can be
rerun offline.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import random  # noqa: E402
from contextlib import contextmanager  # noqa: E402

import run_a_dividend_strategy as strategy  # noqa: E402
import rules_v2  # noqa: E402

BACKTEST_ROOT_NAME = "a_dividend_backtest"
DEFAULT_PERIODS = [f"{year}1101" for year in range(2016, 2026)]
MARKET_HISTORY_START = "20100101"
STATEMENT_HISTORY_START_YEAR = 2006
# Tushare caps fina_indicator and balancesheet at 100 rows per call; four-year
# announcement windows stay well below that.
STATEMENT_WINDOW_YEARS = 4
DIVIDEND_CUT_RATIO = 0.95
CUT_HORIZON_YEARS = 2
GROWTH_HORIZON_YEARS = 3
BENCHMARKS = {
    "H00922.CSI": "中证红利全收益",
    "H00300.CSI": "沪深300全收益",
}
FETCH_ATTEMPTS = 3
FETCH_RETRY_BASE_SECONDS = 2.0
STATEMENT_INTERFACES = {
    "income": ("income", strategy.INCOME_FIELDS),
    "balance": ("balancesheet", strategy.BALANCE_FIELDS),
    "cashflow": ("cashflow", strategy.CASHFLOW_FIELDS),
    "fina_indicator": ("fina_indicator", strategy.FINA_INDICATOR_FIELDS),
    "audit": ("fina_audit", None),
    "income_interest": ("income", rules_v2.INCOME_INTEREST_FIELDS),
}
STRING_COLUMNS = {"ts_code", "con_code", "update_flag", "report_type", "div_proc", "comp_type"}
RULE_VERSIONS = ("v1", "v2")
BANK_MODES = ("structured_only", "metrics_file")
PERTURBATION_LOW = 0.8
PERTURBATION_HIGH = 1.2
PERTURBATION_SEED = 20161101


# ---------------------------------------------------------------------------
# Rule versions
# ---------------------------------------------------------------------------


def _normalized(weights: dict[str, float]) -> dict[str, float]:
    total = sum(weights.values())
    return {factor: weight / total for factor, weight in weights.items()}


def factor_weights_for(rule_version: str, bank_mode: str) -> dict[str, dict[str, float]]:
    """Scoring weights for one rule version.

    Without bank report metrics (``structured_only``) the bank-specific
    factors cannot be computed, so banks are scored on the remaining common
    factors, renormalized. The report states this limitation.
    """
    if rule_version == "v2":
        return rules_v2.quality_weights(bank_mode)
    if rule_version != "v1":
        raise ValueError(f"unknown rule version: {rule_version}")
    weights = {group: dict(values) for group, values in strategy.default_factor_weights().items()}
    if bank_mode == "structured_only":
        weights["bank"] = _normalized(
            {factor: weight for factor, weight in weights["bank"].items() if not factor.startswith("bank_")}
        )
    return weights


# ---------------------------------------------------------------------------
# Raw data cache
# ---------------------------------------------------------------------------


def read_cached_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        header = pd.read_csv(path, nrows=0, encoding="utf-8-sig").columns
    except pd.errors.EmptyDataError:
        return pd.DataFrame()
    dtype = {column: str for column in header if column.endswith("date") or column in STRING_COLUMNS}
    return pd.read_csv(path, dtype=dtype, encoding="utf-8-sig")


def statement_windows(fetch_end: str) -> list[tuple[str, str]]:
    end_year = int(fetch_end[:4])
    windows = []
    year = STATEMENT_HISTORY_START_YEAR
    while year <= end_year:
        last = min(end_year, year + STATEMENT_WINDOW_YEARS - 1)
        windows.append((f"{year}0101", fetch_end if last == end_year else f"{last}1231"))
        year = last + 1
    return windows


class RawStore:
    """Fetch-once cache of full Tushare histories, keyed by interface and code."""

    def __init__(
        self,
        root: Path,
        fetch_end: str,
        client_factory: Callable[[], Any] | None = None,
        *,
        sleep_seconds: float = 0.2,
        offline: bool = False,
    ) -> None:
        self.root = Path(root)
        self.fetch_end = fetch_end
        self._client_factory = client_factory or strategy.tushare_pro
        self._client = None
        self._client_lock = threading.Lock()
        self.sleep_seconds = sleep_seconds
        self.offline = offline
        self._memory: dict[tuple[str, str], pd.DataFrame] = {}
        self._memory_lock = threading.Lock()

    @property
    def client(self):
        with self._client_lock:
            if self._client is None:
                self._client = self._client_factory()
            return self._client

    def _call(self, method: str, **kwargs) -> pd.DataFrame:
        for attempt in range(1, FETCH_ATTEMPTS + 1):
            try:
                data = getattr(self.client, method)(**kwargs)
                if self.sleep_seconds:
                    time.sleep(self.sleep_seconds)
                return pd.DataFrame() if data is None else pd.DataFrame(data)
            except Exception as error:
                if attempt == FETCH_ATTEMPTS:
                    raise RuntimeError(f"{method} {kwargs.get('ts_code', '')} 已重试{FETCH_ATTEMPTS}次：{error}") from error
                time.sleep(FETCH_RETRY_BASE_SECONDS * 2 ** (attempt - 1))
        raise AssertionError("unreachable")

    def _fetch(self, interface: str, key: str) -> pd.DataFrame:
        end = self.fetch_end
        if interface in {"daily", "daily_basic", "adj_factor"}:
            return self._call(interface, ts_code=key, start_date=MARKET_HISTORY_START, end_date=end)
        if interface == "index_daily":
            return self._call("index_daily", ts_code=key, start_date=MARKET_HISTORY_START, end_date=end)
        if interface == "dividend":
            return self._call("dividend", ts_code=key, fields=strategy.DIVIDEND_FIELDS)
        if interface in STATEMENT_INTERFACES:
            method, fields = STATEMENT_INTERFACES[interface]
            frames = []
            for start, stop in statement_windows(end):
                kwargs = {"ts_code": key, "start_date": start, "end_date": stop}
                if fields:
                    kwargs["fields"] = fields
                frames.append(self._call(method, **kwargs))
            frames = [frame for frame in frames if not frame.empty]
            return pd.concat(frames, ignore_index=True).drop_duplicates() if frames else pd.DataFrame()
        if interface == "index_weight":
            period = key
            start = (datetime.strptime(period, "%Y%m%d") - timedelta(days=45)).strftime("%Y%m%d")
            return self._call("index_weight", index_code=strategy.INDEX_CODE, start_date=start, end_date=period)
        if interface == "stock_basic":
            frames = [
                self._call(
                    "stock_basic",
                    exchange="",
                    list_status=status,
                    fields="ts_code,symbol,name,area,industry,market,list_date,list_status,delist_date",
                )
                for status in ("L", "D", "P")
            ]
            return pd.concat(frames, ignore_index=True).drop_duplicates("ts_code")
        if interface == "stock_company":
            frames = [
                self._call(
                    "stock_company",
                    exchange=exchange,
                    fields="ts_code,exchange,introduction,main_business,business_scope",
                )
                for exchange in ("SSE", "SZSE")
            ]
            return pd.concat(frames, ignore_index=True).drop_duplicates("ts_code", keep="last")
        raise ValueError(f"unknown interface: {interface}")

    def frame(self, interface: str, key: str = "all") -> pd.DataFrame:
        cache_key = (interface, key)
        with self._memory_lock:
            if cache_key in self._memory:
                return self._memory[cache_key]
        path = self.root / interface / f"{key}.csv"
        stale = path.exists() and bool(self._missing_columns(interface, path))
        if not path.exists() or stale:
            if self.offline:
                detail = f"缺少字段{self._missing_columns(interface, path)}" if stale else "缺少缓存"
                raise RuntimeError(f"离线模式{detail}：{path}")
            data = self._fetch(interface, key)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            data.to_csv(tmp, index=False, encoding="utf-8-sig")
            tmp.replace(path)
        data = read_cached_csv(path)
        with self._memory_lock:
            self._memory[cache_key] = data
        return data

    @staticmethod
    def _missing_columns(interface: str, path: Path) -> list[str]:
        """Requested statement fields absent from a cache written by older code."""
        fields = STATEMENT_INTERFACES.get(interface, (None, None))[1]
        if not fields:
            return []
        with path.open(encoding="utf-8-sig") as handle:
            header = handle.readline().strip()
        if not header:
            return []
        present = set(header.split(","))
        return [field for field in fields.split(",") if field not in present]

    def prefetch(self, requests: list[tuple[str, str]], max_workers: int) -> dict[tuple[str, str], Exception]:
        failures: dict[tuple[str, str], Exception] = {}

        def run(request: tuple[str, str]) -> None:
            try:
                self.frame(*request)
            except Exception as error:  # recorded per security; the screen marks a data gap
                failures[request] = error

        with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
            list(executor.map(run, requests))
        return failures


# ---------------------------------------------------------------------------
# Point-in-time slicing
# ---------------------------------------------------------------------------


def _date_text(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.replace(r"\D", "", regex=True).str[:8]


def slice_market(frame: pd.DataFrame, period: str) -> pd.DataFrame:
    """Five years of daily rows ending on the rebalance date, as the formal run fetches."""
    if frame.empty or "trade_date" not in frame.columns:
        return frame
    dates = _date_text(frame["trade_date"])
    start = strategy.five_year_start(period)
    return frame[dates.ge(start) & dates.le(period)].reset_index(drop=True)


def pit_statement(frame: pd.DataFrame, period: str) -> pd.DataFrame:
    """Statement rows for periods ended and announced by the rebalance date."""
    if frame.empty or "end_date" not in frame.columns:
        return pd.DataFrame(columns=frame.columns)
    ended = frame[_date_text(frame["end_date"]).le(period)]
    return strategy.dedupe_statement_rows(ended, period)


def period_constituents(store: RawStore, period: str) -> pd.DataFrame:
    """CSI 300 members in the latest monthly weight snapshot on or before ``period``."""
    weights = store.frame("index_weight", period)
    if weights.empty or "trade_date" not in weights.columns:
        raise RuntimeError(f"{period}未取得沪深300月度权重")
    eligible = weights[_date_text(weights["trade_date"]).le(period)]
    snapshot = _date_text(eligible["trade_date"]).max()
    members = eligible[_date_text(eligible["trade_date"]).eq(snapshot)].drop_duplicates("con_code")
    if len(members) != strategy.EXPECTED_CONSTITUENT_COUNT:
        raise RuntimeError(f"{period}月度权重快照{snapshot}只有{len(members)}只成分股")
    basic = store.frame("stock_basic")
    merged = members[["con_code", "weight"]].merge(
        basic, left_on="con_code", right_on="ts_code", how="left"
    )
    missing = merged["ts_code"].isna()
    if missing.any():
        raise RuntimeError(
            f"{period}成分股未匹配上市公司资料：" + "、".join(merged.loc[missing, "con_code"].head(5))
        )
    merged["trade_date"] = snapshot
    merged["weight_trade_date"] = snapshot
    merged["index_code"] = strategy.INDEX_CODE
    profiles = store.frame("stock_company")
    if not profiles.empty:
        merged = merged.merge(
            profiles[["ts_code", "introduction", "main_business", "business_scope"]],
            on="ts_code",
            how="left",
        )
    for field in strategy.COMPANY_PROFILE_FIELDS:
        if field not in merged.columns:
            merged[field] = ""
        merged[field] = merged[field].fillna("").astype(str)
    merged["company_profile_source"] = "Tushare stock_company（当前资料，非时点数据）"
    return merged.sort_values("weight", ascending=False).reset_index(drop=True)


def load_constituents_override(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna("")
    if "ts_code" not in frame.columns:
        raise RuntimeError(f"成分股文件缺少ts_code：{path}")
    return frame


# ---------------------------------------------------------------------------
# One rebalance date
# ---------------------------------------------------------------------------


def prior_top_from_codes(period: str, codes: list[str], source: str) -> dict[str, Any]:
    return {"run_date": period, "source": source, "codes": set(codes)}


def load_prior_top_file(path: Path) -> dict[str, Any]:
    frame = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
    codes = set(frame.get("ts_code", pd.Series(dtype=str)).dropna().astype(str).str.strip()) - {""}
    match = strategy.re.search(r"(\d{8})", path.name)
    return {"run_date": match.group(1) if match else "", "source": str(path), "codes": codes}


@contextmanager
def profit_trend_threshold(value: float | None):
    """Run the production profit-trend gate with another threshold."""
    original = strategy.MIN_PROFIT_DEDT_THREE_YEAR_CHANGE
    if value is not None:
        strategy.MIN_PROFIT_DEDT_THREE_YEAR_CHANGE = float(value)
    try:
        yield
    finally:
        strategy.MIN_PROFIT_DEDT_THREE_YEAR_CHANGE = original


def screen_period(
    period: str,
    store: RawStore,
    constituents: pd.DataFrame,
    *,
    prior_top: dict[str, Any] | None,
    factor_weights: dict[str, dict[str, float]],
    bank_metrics_file: Path | None = None,
    rule_version: str = "v1",
    rule_params: dict[str, Any] | None = None,
    bank_mode: str = "structured_only",
) -> list[dict[str, Any]]:
    """Run the screen for one rebalance date on as-of-date frames.

    Rule v1 is the production screen unchanged. Rule v2 adds its factors and
    gates to each structured record, then replaces the scoring.
    """
    codes = constituents["ts_code"].astype(str).tolist()

    def market(prefix: str, code: str) -> pd.DataFrame:
        try:
            return slice_market(store.frame(prefix, code), period)
        except Exception:
            return pd.DataFrame()

    profiles = {
        code: strategy.market_profile_for(code, market("daily", code), market("daily_basic", code), prior_top)
        for code in codes
    }

    def statement(interface: str, code: str) -> pd.DataFrame:
        return pit_statement(store.frame(interface, code), period)

    common: dict[str, dict[str, pd.DataFrame] | Exception] = {}
    for code in codes:
        try:
            common[code] = {"income": statement("income", code), "balance": statement("balance", code)}
        except Exception as error:
            common[code] = error
    details: dict[str, dict[str, pd.DataFrame] | Exception] = {}
    for code, profile in profiles.items():
        if not profile["market_preflight_passed"]:
            continue
        if isinstance(common[code], Exception):
            details[code] = common[code]
            continue
        try:
            details[code] = {
                **common[code],
                "dividends": store.frame("dividend", code),
                "fina_indicator": statement("fina_indicator", code),
                "cashflow": statement("cashflow", code),
                "audit": statement("audit", code),
            }
        except Exception as error:
            details[code] = error

    threshold = rules_v2.resolve_params(rule_params)["profit_dedt_min_change"] if rule_version == "v2" else None
    with profit_trend_threshold(threshold):
        records = strategy.build_structured_factor_records(period, constituents, profiles, common, details)
    structured = {str(record["ts_code"]): record for record in records}
    if bank_metrics_file is not None:
        bank_codes = {
            code
            for code, record in structured.items()
            if bool(record.get("is_bank")) and bool(record.get("market_preflight_passed"))
        }
        if bank_codes:
            bank_profiles = strategy.load_bank_quality_profiles(bank_metrics_file, bank_codes)
            for code, bank_profile in bank_profiles.items():
                structured[code] = strategy.merge_bank_quality_gate(structured[code], bank_profile)
    scorer = None
    if rule_version == "v2":
        params = rules_v2.resolve_params(rule_params)
        for code, record in structured.items():
            frames = details.get(code)
            fina = frames.get("fina_indicator") if isinstance(frames, dict) else None
            try:
                income_interest = statement("income_interest", code) if isinstance(frames, dict) else None
            except Exception:
                income_interest = None
            structured[code] = rules_v2.apply_v2(record, fina, params, income_interest)
        scorer = lambda rows: rules_v2.score_v2(rows, params, bank_mode, group_weights=factor_weights)
    return strategy.build_candidate_rows(
        None,
        structured_factors=structured,
        prior_formal_top=prior_top,
        constituents=constituents,
        frame_loader=market,
        factor_weights=factor_weights,
        scorer=scorer,
    )


# ---------------------------------------------------------------------------
# Weight-perturbation robustness
# ---------------------------------------------------------------------------


def rescored_top_codes(
    rows: list[dict[str, Any]],
    *,
    rule_version: str,
    bank_mode: str,
    group_weights: dict[str, dict[str, float]],
    record_weights: dict[str, float] | None = None,
    rule_params: dict[str, Any] | None = None,
) -> list[str]:
    """Top10 after rescoring one period's rows; gate results stay as computed."""
    scoring_input = [row for row in rows if bool(row.get("market_preflight_passed"))]
    if rule_version == "v2":
        scored = rules_v2.score_v2(
            scoring_input,
            rules_v2.resolve_params(rule_params),
            bank_mode,
            record_weights=record_weights,
            group_weights=group_weights,
        )
    else:
        scored = strategy.score_cross_section(scoring_input, factor_weights=group_weights)
    for row in scored:
        eligible = bool(row.get("structured_gate_passed")) and strategy.finite_float(row.get("dividend_score_total")) is not None
        row["selected"] = "是" if eligible else "否"
    return [str(row["ts_code"]) for row in strategy.formal_top_candidates(scored)]


def _perturbed(weights: dict[str, float], rng: random.Random) -> dict[str, float]:
    return _normalized_or_empty({factor: weight * rng.uniform(PERTURBATION_LOW, PERTURBATION_HIGH) for factor, weight in weights.items()})


def _normalized_or_empty(weights: dict[str, float]) -> dict[str, float]:
    return _normalized(weights) if weights else {}


def perturbation_overlaps(
    rows: list[dict[str, Any]],
    *,
    rule_version: str,
    bank_mode: str,
    group_weights: dict[str, dict[str, float]],
    rule_params: dict[str, Any] | None,
    draws: int,
    seed: int,
) -> dict[str, Any]:
    """Overlap of the Top10 under ``draws`` random weight perturbations.

    Every weight is multiplied by U(0.8, 1.2) and renormalized within its
    block; for v2 the record/quality block split is perturbed the same way.
    """
    base = rescored_top_codes(
        rows,
        rule_version=rule_version,
        bank_mode=bank_mode,
        group_weights=group_weights,
        record_weights=rules_v2.RECORD_FACTOR_WEIGHTS if rule_version == "v2" else None,
        rule_params=rule_params,
    )
    rng = random.Random(seed)
    overlaps: list[int] = []
    for _ in range(draws):
        groups = {group: _perturbed(weights, rng) for group, weights in group_weights.items()}
        record = None
        params = dict(rule_params or {})
        if rule_version == "v2":
            record = _perturbed(rules_v2.RECORD_FACTOR_WEIGHTS, rng)
            block = float(rules_v2.resolve_params(rule_params)["record_block_weight"])
            split = _perturbed({"record": block, "quality": 1 - block}, rng)
            params["record_block_weight"] = split["record"]
        top = rescored_top_codes(
            rows,
            rule_version=rule_version,
            bank_mode=bank_mode,
            group_weights=groups,
            record_weights=record,
            rule_params=params,
        )
        overlaps.append(len(set(top) & set(base)))
    mean = sum(overlaps) / len(overlaps) if overlaps else None
    return {
        "base_top": base,
        "perturbation_draws": draws,
        "perturbation_mean_overlap": mean,
        "perturbation_mean_overlap_ratio": mean / len(base) if mean is not None and base else None,
        "perturbation_min_overlap": min(overlaps) if overlaps else None,
    }


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------


def last_complete_fiscal_year(asof: str) -> int:
    """Latest fiscal year whose final dividend has normally been implemented by ``asof``."""
    year, month = int(asof[:4]), int(asof[4:6])
    return year - 1 if month >= 9 else year - 2


def _dividend_history(dividends: pd.DataFrame, evaluation_date: str) -> tuple[dict[int, float], dict[int, float]]:
    """Comparable fiscal-year DPS and total cash dividends implemented by ``evaluation_date``."""
    profile = strategy.dividend_history_profile(dividends, evaluation_date)
    # No dividend in the two years before the evaluation date leaves no anchor
    # for share-count adjustment; the raw history still shows the stop.
    values = profile.get("annual_comparable_dps") or profile.get("annual_raw_dps") or {}
    totals = profile.get("annual_cash_dividend_total_yuan") or {}
    return (
        {int(year): float(value) for year, value in values.items()},
        {int(year): float(value) for year, value in totals.items()},
    )


def _comparable_dps(dividends: pd.DataFrame, evaluation_date: str) -> dict[int, float]:
    return _dividend_history(dividends, evaluation_date)[0]


def is_dividend_cut(year: int, dps: dict[int, float], totals: dict[int, float]) -> bool:
    """A year with no cash dividend, or both DPS and total cash below the cut ratio.

    Requiring the total to fall as well keeps a share issue (for example the
    2025 Ministry of Finance recapitalization of large banks) that dilutes DPS
    while the payout is maintained from counting as a cut. Without both totals
    the per-share test alone decides.
    """
    current, prior = dps.get(year, 0.0), dps.get(year - 1, 0.0)
    if current <= 0:
        return True
    if prior <= 0 or current >= DIVIDEND_CUT_RATIO * prior:
        return False
    total, prior_total = totals.get(year), totals.get(year - 1)
    if total is not None and prior_total is not None and prior_total > 0:
        return total < DIVIDEND_CUT_RATIO * prior_total
    return True


def delisted_by(basic_row: dict[str, Any] | None, date: str) -> str:
    if not basic_row:
        return ""
    delist = str(basic_row.get("delist_date") or "").strip()
    delist = "".join(character for character in delist if character.isdigit())[:8]
    return delist if delist and delist <= date else ""


def dividend_outcome(
    dividends: pd.DataFrame,
    anchor_year: int,
    asof: str,
    *,
    delist_date: str = "",
) -> dict[str, Any]:
    """Cuts in the next ``CUT_HORIZON_YEARS`` fiscal years and three-year DPS growth.

    A cut is a fiscal year whose comparable DPS and total cash dividend are both
    below ``DIVIDEND_CUT_RATIO`` of the prior year, or a year without any cash
    dividend (see ``is_dividend_cut``). Only fiscal years complete by ``asof``
    are evaluated.
    """
    last_complete = last_complete_fiscal_year(asof)
    result: dict[str, Any] = {
        "anchor_year": anchor_year,
        "cut_years_evaluated": 0,
        "cut": None,
        "cut_years": "",
        "dps_growth_3y": None,
        "delisted": bool(delist_date),
        "delist_date": delist_date,
        "outcome_note": "",
    }
    if delist_date:
        result["outcome_note"] = f"{delist_date}退市，不计入分红结果"
        return result
    cut_years = [anchor_year + offset for offset in range(1, CUT_HORIZON_YEARS + 1) if anchor_year + offset <= last_complete]
    if cut_years:
        evaluation = min(asof, f"{cut_years[-1] + 1}1231")
        dps, totals = _dividend_history(dividends, evaluation)
        cuts = [year for year in cut_years if is_dividend_cut(year, dps, totals)]
        result["cut_years_evaluated"] = len(cut_years)
        result["cut"] = bool(cuts)
        result["cut_years"] = ",".join(str(year) for year in cuts)
        for year in [anchor_year, *cut_years]:
            result[f"dps_{year}"] = dps.get(year, 0.0)
    growth_end = anchor_year + GROWTH_HORIZON_YEARS
    if growth_end <= last_complete:
        dps = _comparable_dps(dividends, min(asof, f"{growth_end + 1}1231"))
        start, end = dps.get(anchor_year, 0.0), dps.get(growth_end, 0.0)
        if start > 0:
            result["dps_growth_3y"] = (end / start) ** (1 / GROWTH_HORIZON_YEARS) - 1 if end > 0 else -1.0
    return result


def adjusted_close(store: RawStore, code: str) -> pd.Series:
    daily = store.frame("daily", code)
    factors = store.frame("adj_factor", code)
    if daily.empty or factors.empty:
        return pd.Series(dtype=float)
    merged = daily[["trade_date", "close"]].merge(factors[["trade_date", "adj_factor"]], on="trade_date", how="inner")
    merged["trade_date"] = _date_text(merged["trade_date"])
    series = pd.to_numeric(merged["close"], errors="coerce") * pd.to_numeric(merged["adj_factor"], errors="coerce")
    return pd.Series(series.values, index=merged["trade_date"]).dropna().sort_index()


def index_close(store: RawStore, code: str) -> pd.Series:
    frame = store.frame("index_daily", code)
    if frame.empty:
        return pd.Series(dtype=float)
    return pd.Series(
        pd.to_numeric(frame["close"], errors="coerce").values,
        index=_date_text(frame["trade_date"]),
    ).dropna().sort_index()


def value_on_or_before(series: pd.Series, date: str) -> tuple[float | None, str]:
    eligible = series[series.index <= date]
    if eligible.empty:
        return None, ""
    return float(eligible.iloc[-1]), str(eligible.index[-1])


def holding_return(series: pd.Series, start: str, end: str) -> float | None:
    """Total return from the last close on or before ``start`` to the last close on or before ``end``.

    A security that stops trading (suspension or delisting) is held at its
    last close, i.e. its proceeds earn zero until ``end``.
    """
    begin, begin_date = value_on_or_before(series, start)
    finish, _ = value_on_or_before(series, end)
    if begin is None or finish is None or begin <= 0 or not begin_date:
        return None
    return finish / begin - 1


# ---------------------------------------------------------------------------
# Backtest driver
# ---------------------------------------------------------------------------


def backtest_root(data_root: Path | str | None) -> Path:
    return strategy.resolve_data_root(data_root) / BACKTEST_ROOT_NAME


def _code_identity() -> dict[str, Any]:
    import forward_replay

    return forward_replay._code_identity(SCRIPT_DIR.parent)


def run_backtest(
    *,
    periods: list[str],
    asof: str,
    rule_version: str = "v1",
    rule_params: dict[str, Any] | None = None,
    bank_mode: str = "structured_only",
    bank_metrics_file: Path | None = None,
    constituents_overrides: dict[str, Path] | None = None,
    initial_prior_top: dict[str, Any] | None = None,
    data_root: Path | str | None = None,
    run_id: str | None = None,
    max_workers: int = 4,
    sleep_seconds: float = 0.2,
    offline: bool = False,
    allow_dirty: bool = False,
    perturbations: int = 0,
    store: RawStore | None = None,
    code_identity: dict[str, Any] | None = None,
) -> Path:
    if rule_version not in RULE_VERSIONS:
        raise ValueError(f"unknown rule version: {rule_version}")
    if bank_mode not in BANK_MODES:
        raise ValueError(f"unknown bank mode: {bank_mode}")
    if bank_mode == "metrics_file" and bank_metrics_file is None:
        raise ValueError("bank_mode=metrics_file requires bank_metrics_file")
    identity = code_identity or _code_identity()
    if identity.get("dirty") and not allow_dirty:
        raise RuntimeError("工作区有未提交改动；回测须从干净的提交运行（开发时可加--allow-dirty）")
    periods = sorted(strategy.compact_date(period) for period in periods)
    asof = strategy.compact_date(asof)
    root = backtest_root(data_root)
    store = store or RawStore(root / "raw" / asof, asof, sleep_seconds=sleep_seconds, offline=offline)
    if rule_params and rule_version != "v2":
        raise ValueError("rule parameters apply only to rule v2")
    resolved_params = rules_v2.resolve_params(rule_params) if rule_version == "v2" else {}
    param_tag = "".join(f"-{key}{value}" for key, value in sorted((rule_params or {}).items()))
    run_id = run_id or f"{rule_version}{param_tag}-{bank_mode}-{periods[0]}-{periods[-1]}-asof{asof}"
    out = root / "runs" / run_id
    (out / "periods").mkdir(parents=True, exist_ok=True)
    factor_weights = factor_weights_for(rule_version, bank_mode)
    overrides = constituents_overrides or {}

    constituents_by_period = {
        period: load_constituents_override(overrides[period]) if period in overrides else period_constituents(store, period)
        for period in periods
    }
    all_codes = sorted({code for frame in constituents_by_period.values() for code in frame["ts_code"].astype(str)})
    print(f"回测{len(periods)}期，涉及{len(all_codes)}只证券", flush=True)
    failures = store.prefetch(
        [(interface, code) for code in all_codes for interface in ("daily", "daily_basic", "income", "balance")],
        max_workers,
    )
    # Detail frames for anything that could pass the preflight in some period
    # (the retention ratio is the lowest bar a prior Top10 member faces).
    candidates: set[str] = set()
    for period, constituents in constituents_by_period.items():
        for code in constituents["ts_code"].astype(str):
            try:
                profile = strategy.dividend_yield_profile(slice_market(store.frame("daily_basic", code), period))
            except Exception:
                continue
            ratio = strategy.finite_float(profile.get("dividend_yield_ge3_ratio_exact"))
            if profile.get("dividend_yield_data_sufficient") and ratio is not None and ratio >= strategy.DIVIDEND_YIELD_RETENTION_RATIO:
                candidates.add(code)
    print(f"{len(candidates)}只证券在至少一期接近市场预筛线，拉取财务明细", flush=True)
    failures.update(
        store.prefetch(
            [(interface, code) for code in sorted(candidates) for interface in ("dividend", "fina_indicator", "cashflow", "audit", "adj_factor")]
            + ([("income_interest", code) for code in sorted(candidates)] if rule_version == "v2" else []),
            max_workers,
        )
    )

    prior_top = initial_prior_top
    period_rows: list[dict[str, Any]] = []
    holdings: dict[str, list[dict[str, Any]]] = {}
    for period in periods:
        rows = screen_period(
            period,
            store,
            constituents_by_period[period],
            prior_top=prior_top,
            factor_weights=factor_weights,
            bank_metrics_file=bank_metrics_file if bank_mode == "metrics_file" else None,
            rule_version=rule_version,
            rule_params=rule_params,
            bank_mode=bank_mode,
        )
        top = strategy.formal_top_candidates(rows)
        extra = rules_v2.EXTRA_CSV_FIELDS if rule_version == "v2" else None
        strategy.write_csv(rows, out / "periods" / f"{period}-candidates.csv", extra)
        strategy.write_csv(top, out / "periods" / f"{period}-top10.csv", extra)
        holdings[period] = top
        prior_codes = (prior_top or {}).get("codes", set())
        top_codes = [str(row["ts_code"]) for row in top]
        period_rows.append(
            {
                "period": period,
                "constituent_snapshot": str(constituents_by_period[period]["trade_date"].iloc[0]) if "trade_date" in constituents_by_period[period].columns else "",
                "universe": len(rows),
                "market_passers": sum(bool(row.get("market_preflight_passed")) for row in rows),
                "structured_passers": sum(bool(row.get("structured_gate_passed")) for row in rows),
                "selected": sum(row.get("selected") == "是" for row in rows),
                "data_gaps": sum(row.get("selection_status") == "data_gap" for row in rows),
                "top_count": len(top),
                "banks_in_top": sum(bool(row.get("is_bank")) for row in top),
                "turnover": (len(set(top_codes) - prior_codes) / len(top_codes)) if prior_codes and top_codes else None,
                "top10": "、".join(str(row.get("name", "")) for row in top),
            }
        )
        if perturbations:
            robustness = perturbation_overlaps(
                rows,
                rule_version=rule_version,
                bank_mode=bank_mode,
                group_weights=factor_weights,
                rule_params=rule_params,
                draws=perturbations,
                seed=PERTURBATION_SEED + int(period),
            )
            if robustness["base_top"] != top_codes:
                raise RuntimeError(f"{period}：重新计分的基准Top10与筛选结果不一致")
            period_rows[-1].update({key: value for key, value in robustness.items() if key != "base_top"})
        prior_top = prior_top_from_codes(period, top_codes, f"backtest:{run_id}:{period}")
        print(f"{period}：入选{period_rows[-1]['selected']}家，Top10 {period_rows[-1]['top10']}", flush=True)

    basic = store.frame("stock_basic")
    basic_by_code = {str(row["ts_code"]): row for row in basic.to_dict("records")}
    outcome_rows: list[dict[str, Any]] = []
    benchmark_series = {}
    for code in BENCHMARKS:
        try:
            benchmark_series[code] = index_close(store, code)
        except Exception as error:
            print(f"基准{code}未取得：{error}", flush=True)
            benchmark_series[code] = pd.Series(dtype=float)
    for position, period in enumerate(periods):
        end = periods[position + 1] if position + 1 < len(periods) else asof
        returns: list[float] = []
        for row in holdings[period]:
            code = str(row["ts_code"])
            anchor = strategy.finite_float(row.get("dividend_anchor_year"))
            delist = delisted_by(basic_by_code.get(code), asof)
            try:
                dividends = store.frame("dividend", code)
                outcome = dividend_outcome(dividends, int(anchor), asof, delist_date=delist) if anchor else {"outcome_note": "缺少锚定年度"}
            except Exception as error:
                outcome = {"outcome_note": f"分红数据未取得：{error}"}
            try:
                period_return = holding_return(adjusted_close(store, code), period, end)
            except Exception:
                period_return = None
            if period_return is not None:
                returns.append(period_return)
            outcome_rows.append(
                {
                    "period": period,
                    "holding_end": end,
                    "ts_code": code,
                    "name": row.get("name", ""),
                    "industry": row.get("industry", ""),
                    "rank": row.get("rank", ""),
                    "dividend_score_total": row.get("dividend_score_total", ""),
                    "holding_return": period_return,
                    **outcome,
                }
            )
        summary = next(item for item in period_rows if item["period"] == period)
        summary["holding_end"] = end
        summary["portfolio_return"] = sum(returns) / len(returns) if returns else None
        summary["priced_holdings"] = len(returns)
        for code, label in BENCHMARKS.items():
            summary[f"{code}_return"] = holding_return(benchmark_series[code], period, end)
        evaluated = [item for item in outcome_rows if item["period"] == period and item.get("cut") is not None]
        summary["cut_evaluable"] = len(evaluated)
        summary["cut_count"] = sum(bool(item["cut"]) for item in evaluated)
        growth = [item["dps_growth_3y"] for item in outcome_rows if item["period"] == period and item.get("dps_growth_3y") is not None]
        summary["dps_growth_3y_median"] = float(pd.Series(growth).median()) if growth else None

    summary_frame = pd.DataFrame(period_rows)
    outcomes_frame = pd.DataFrame(outcome_rows)
    summary_frame.to_csv(out / "summary.csv", index=False, encoding="utf-8-sig")
    outcomes_frame.to_csv(out / "outcomes.csv", index=False, encoding="utf-8-sig")
    aggregate = aggregate_metrics(summary_frame, outcomes_frame)
    manifest = {
        "run_id": run_id,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "rule_version": rule_version,
        "bank_mode": bank_mode,
        "bank_metrics_file": str(bank_metrics_file) if bank_metrics_file else "",
        "periods": periods,
        "asof": asof,
        "raw_cache": str(store.root),
        "constituents_overrides": {period: str(path) for period, path in overrides.items()},
        "initial_prior_top": sorted((initial_prior_top or {}).get("codes", [])),
        "factor_weights": factor_weights,
        "rule_params": resolved_params,
        "record_factor_weights": rules_v2.RECORD_FACTOR_WEIGHTS if rule_version == "v2" else {},
        "code_identity": identity,
        "fetch_failures": {f"{interface}:{code}": str(error) for (interface, code), error in failures.items()},
        "perturbations": perturbations,
        "aggregate": aggregate,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    write_report(out / "report.md", manifest, summary_frame, outcomes_frame)
    return out


def _annualized(returns: list[float]) -> float | None:
    if not returns:
        return None
    growth = 1.0
    for value in returns:
        growth *= 1 + value
    return growth ** (1 / len(returns)) - 1


def aggregate_metrics(summary: pd.DataFrame, outcomes: pd.DataFrame) -> dict[str, Any]:
    cut_evaluable = int(summary["cut_evaluable"].sum()) if "cut_evaluable" in summary else 0
    cut_count = int(summary["cut_count"].sum()) if "cut_count" in summary else 0
    growth = outcomes["dps_growth_3y"].dropna() if "dps_growth_3y" in outcomes else pd.Series(dtype=float)
    full_years = summary[summary["holding_end"].isin(summary["period"])] if "holding_end" in summary else summary
    result: dict[str, Any] = {
        "cut_evaluable": cut_evaluable,
        "cut_count": cut_count,
        "cut_rate": cut_count / cut_evaluable if cut_evaluable else None,
        "delisted_holdings": int(outcomes["delisted"].fillna(False).astype(bool).sum()) if "delisted" in outcomes else 0,
        "dps_growth_3y_median": float(growth.median()) if not growth.empty else None,
        "dps_growth_3y_observations": int(len(growth)),
        "mean_turnover": float(summary["turnover"].dropna().mean()) if summary["turnover"].notna().any() else None,
        "full_holding_years": int(len(full_years)),
        "perturbation_mean_overlap": float(summary["perturbation_mean_overlap"].mean()) if "perturbation_mean_overlap" in summary else None,
        "perturbation_mean_overlap_ratio": float(summary["perturbation_mean_overlap_ratio"].mean()) if "perturbation_mean_overlap_ratio" in summary else None,
        "portfolio_annualized": _annualized(full_years["portfolio_return"].dropna().tolist()),
    }
    for code in BENCHMARKS:
        column = f"{code}_return"
        result[f"{code}_annualized"] = _annualized(full_years[column].dropna().tolist()) if column in full_years else None
    return result


def _pct(value: Any, digits: int = 1) -> str:
    number = strategy.finite_float(value)
    return "—" if number is None else f"{number * 100:.{digits}f}%"


def write_report(path: Path, manifest: dict[str, Any], summary: pd.DataFrame, outcomes: pd.DataFrame) -> None:
    aggregate = manifest["aggregate"]
    identity = manifest["code_identity"]
    lines = [
        f"# AHongli 历史回测 {manifest['run_id']}",
        "",
        f"- 规则版本：`{manifest['rule_version']}`；银行口径：`{manifest['bank_mode']}`"
        + (f"；参数：`{json.dumps(manifest['rule_params'], ensure_ascii=False)}`" if manifest.get("rule_params") else ""),
        f"- 调仓日：{manifest['periods'][0]}–{manifest['periods'][-1]}，共{len(manifest['periods'])}期；结果截至{manifest['asof']}",
        f"- 代码：{identity.get('kind')} `{identity.get('revision')}`{'（有未提交改动）' if identity.get('dirty') else ''}",
        "",
        "## 汇总",
        "",
        "| 指标 | 数值 |",
        "|---|---:|",
        f"| 分红削减率（之后{CUT_HORIZON_YEARS}个财年内，每股分红与分红总额均低于上年{DIVIDEND_CUT_RATIO:.0%}，或当年未分红） | {_pct(aggregate['cut_rate'])}（{aggregate['cut_count']} / {aggregate['cut_evaluable']}） |",
        f"| 3 年实际 DPS 增速中位数 | {_pct(aggregate['dps_growth_3y_median'])}（{aggregate['dps_growth_3y_observations']} 个样本） |",
        f"| 持有期内退市 | {aggregate['delisted_holdings']} |",
        f"| 平均年换手率 | {_pct(aggregate['mean_turnover'], 0)} |",
        f"| 等权组合年化全收益（{aggregate['full_holding_years']} 个完整年度） | {_pct(aggregate['portfolio_annualized'])} |",
    ]
    if aggregate.get("perturbation_mean_overlap") is not None:
        lines.append(
            f"| 权重扰动（每个权重×U({PERTURBATION_LOW}, {PERTURBATION_HIGH})后归一，每期{manifest.get('perturbations')}次）Top10 平均重合 | "
            f"{aggregate['perturbation_mean_overlap']:.2f} 家（{_pct(aggregate['perturbation_mean_overlap_ratio'])}） |"
        )
    for code, label in BENCHMARKS.items():
        lines.append(f"| {label}年化 | {_pct(aggregate.get(f'{code}_annualized'))} |")
    lines += [
        "",
        "## 分期",
        "",
        "| 调仓日 | 预筛通过 | 门槛通过 | 数据缺口 | 银行数 | 换手 | 削减 | 3年DPS增速中位数 | 组合收益 | 中证红利全收益 | 沪深300全收益 | Top10 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for item in summary.to_dict("records"):
        lines.append(
            f"| {item['period']} | {item['market_passers']} | {item['structured_passers']} | {item['data_gaps']} | {item['banks_in_top']} | "
            f"{_pct(item.get('turnover'), 0)} | {item.get('cut_count', 0)}/{item.get('cut_evaluable', 0)} | {_pct(item.get('dps_growth_3y_median'))} | "
            f"{_pct(item.get('portfolio_return'))} | {_pct(item.get('H00922.CSI_return'))} | {_pct(item.get('H00300.CSI_return'))} | {item['top10']} |"
        )
    cuts = outcomes[outcomes.get("cut", pd.Series(dtype=object)).fillna(False).astype(bool)] if "cut" in outcomes else outcomes.iloc[0:0]
    lines += ["", "## 入选后削减分红的公司", ""]
    if cuts.empty:
        lines.append("无。")
    else:
        lines += ["| 调仓日 | 公司 | 行业 | 锚定年度 | 削减年度 |", "|---|---|---|---:|---|"]
        for item in cuts.to_dict("records"):
            lines.append(f"| {item['period']} | {item['name']} | {item['industry']} | {int(item['anchor_year'])} | {item['cut_years']} |")
    lines += [
        "",
        "## 口径与局限",
        "",
        "- 成分股取调仓日及以前最近一期 Tushare 月度权重快照，包含后来调出或退市的公司。",
        "- 财务报表只用调仓日前已公告（`f_ann_date`/`ann_date`）且报告期已结束的数据；分红只用调仓日前已实施的事件。",
        "- 行业分类与主营资料取当前 Tushare 资料，不是调仓日当时的分类。",
        "- 银行口径为 `structured_only` 时，银行专项门槛（不良、拨备、资本）未执行，银行只用通用因子计分（权重按比例归一）。",
        f"- 削减判断只覆盖截至{manifest['asof']}已完整的财年；末几期的观察窗口不完整，样本相应减少。",
        "- 收益为后复权价格（含分红再投资）的等权组合，持有至下一调仓日；停牌或退市按最后收盘价持有。不计交易成本。",
    ]
    if manifest["fetch_failures"]:
        lines += ["", f"- 数据获取失败 {len(manifest['fetch_failures'])} 项，见 `manifest.json`。"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _parse_overrides(values: list[str]) -> dict[str, Path]:
    overrides: dict[str, Path] = {}
    for value in values:
        period, _, path = value.partition("=")
        if not path:
            raise argparse.ArgumentTypeError(f"格式应为 PERIOD=PATH：{value}")
        overrides[strategy.compact_date(period)] = Path(path).expanduser().resolve()
    return overrides


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AHongli 时点一致的历史回测")
    parser.add_argument("--periods", default=",".join(DEFAULT_PERIODS), help="逗号分隔的调仓日，默认 2016–2025 每年 11 月 1 日")
    parser.add_argument("--asof", default=datetime.now().strftime("%Y%m%d"), help="原始数据截止日与结果观察截止日")
    parser.add_argument("--rule-version", default="v1", choices=RULE_VERSIONS)
    parser.add_argument("--rule-param", action="append", default=[], metavar="KEY=VALUE", help="v2 参数，如 interest_coverage_min=2、cycle_gate_max_ratio=2")
    parser.add_argument("--bank-mode", default="structured_only", choices=BANK_MODES)
    parser.add_argument("--bank-metrics-file", type=Path)
    parser.add_argument("--constituents-override", action="append", default=[], metavar="PERIOD=PATH")
    parser.add_argument("--prior-top-file", type=Path, help="第一期调仓日的上一期正式 Top10（用于股息率迟滞）")
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--sleep-seconds", type=float, default=0.2)
    parser.add_argument("--offline", action="store_true", help="只用已缓存的原始数据")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--perturbations", type=int, default=0, help="每期权重扰动次数（稳健性检验，默认不做）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    out = run_backtest(
        periods=[period for period in args.periods.split(",") if period.strip()],
        asof=args.asof,
        rule_version=args.rule_version,
        rule_params=dict(item.split("=", 1) for item in args.rule_param) or None,
        bank_mode=args.bank_mode,
        bank_metrics_file=args.bank_metrics_file,
        constituents_overrides=_parse_overrides(args.constituents_override),
        initial_prior_top=load_prior_top_file(args.prior_top_file) if args.prior_top_file else None,
        data_root=args.data_root,
        run_id=args.run_id,
        max_workers=args.max_workers,
        sleep_seconds=args.sleep_seconds,
        offline=args.offline,
        allow_dirty=args.allow_dirty,
        perturbations=args.perturbations,
    )
    print(out)
    print((out / "report.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
