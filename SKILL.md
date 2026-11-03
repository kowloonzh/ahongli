---
name: ahongli
description: Use when screening the latest CSI 300 constituents for quality-dividend candidates with auditable hard gates and separate bank/financial/nonfinancial scoring, followed by evidence-backed forward DPS and target-yield analysis for the formal Top10 by default.
---

# AHongli Dividend Strategy

## Purpose

Screen the latest 沪深300 constituents for durable quality-dividend candidates, then analyze the formal Top10's forward DPS and target yield by default. This is not a current-yield chase or investment instruction. A company must pass all hard gates before its factor score can qualify it for the formal Top10.

Read [references/strategy-spec.md](references/strategy-spec.md) before changing factor calculations, thresholds, weights, or selection behavior. Read [references/data-contract.md](references/data-contract.md) before changing Tushare fields, company-profile caching, or output columns.

Read [references/bank-metrics.md](references/bank-metrics.md) when collecting or evaluating bank-specific asset-quality, margin, provision, or regulatory-capital metrics. These metrics are part of the formal bank hard gates and score.

Read [references/insurance-metrics.md](references/insurance-metrics.md) when collecting or evaluating insurer solvency, parent-company cash, subsidiary remittances, stress sensitivity, or forward DPS. Insurance safety gates are formal; insurance scoring remains in the other-financial model until point-in-time history supports separate weights.

## Required Skill

Use `$tushare` for constituents, company profiles, market history, dividends, financial statements, audit results, and all structured evidence. This strategy does not consume subjective `financial-report-reader` analysis reports. Bank-only NPL, provision, NIM, efficiency, and regulatory-capital metrics are parsed deterministically from original annual reports.

## Workflow

Run these stages in order and persist the count entering and leaving each stage.

### 1. Refresh the 300-stock universe and profiles

Fetch the CSI official run-date `000300` constituent file and require exactly 300 unique constituents and a snapshot date matching the latest trading day or the preceding trading day. For a one-trading-day lag, report the date and warn that same-day adjustments have not been verified. Fetch Tushare `index_weight` separately as a monthly snapshot; do not invent a weight for newly added stocks. Archive the official source file and its hash. Reuse the run-date archive without network access when its SHA-256 matches and its snapshot date still qualifies; otherwise download it with up to three attempts. The official file contains only its latest snapshot, so reject historical dates it cannot substantiate. At the same time fetch Tushare `stock_company` for `SSE` and `SZSE`, join by `ts_code`, and require non-empty `main_business`, `business_scope`, and `introduction` for all 300 companies.

`main_business` is the primary direct-property evidence. `stock_basic.industry` is a fast explicit-industry signal. `business_scope` and `introduction` are retained for audit but do not independently exclude a construction, materials, or financial company merely because their broad legal scope mentions property.

### 2. Market preflight

Fetch or reuse five-year `daily_basic.dv_ttm` data.

A trading day is determined when `daily_basic.close` is present and positive. Tushare leaves `dv_ttm` blank when the trailing twelve months contain no cash dividend, so a blank `dv_ttm` on a determined day counts as 0%, not as missing; a day without a close stays undetermined. Never drop no-dividend periods from the denominator.

Hard market preflight:

- at least 1000 determined dividend-yield trading days;
- determined-day coverage at least 80%;
- `dv_ttm >= 3%` on at least 80% of determined days (entry line). The 3% is an absolute strategy choice and does not float with interest rates.
- Hysteresis: a member of the previous formal Top10 is retained down to 75%. The previous Top10 is the latest `hs300-dividend-top10-YYYYMMDD.csv` under `{data_root}/a_dividend_outputs/YYYYMMDD/` dated strictly before `run_date`; record `prior_formal_top10_member`, `prior_formal_top10_source`, and `dividend_yield_required_ratio` per row. With no earlier formal Top10, every company uses the 80% entry line.

Current price, current dividend yield, and the pass ratio may be displayed but never scored. Five-year yield CV is calculated here; only rule v1 scores it.

### 3. Structured financial gates and scoring

For market-preflight passers, fetch or reuse Tushare `dividend`, `income`, `fina_indicator`, `cashflow`, `balancesheet`, and `fina_audit` evidence. Obtain enough structured ROE inputs for all 300 companies to calculate the CSI 300 lowest-volatility 80% benchmark. Retry each company's structured fetch up to three times with backoff; only a failure after all attempts becomes that company's `data_gap`, so one transient timeout must not silently remove a company from the ranking.

Apply every structured hard gate, including direct-property exclusion from the cached company profile. Score complete passers directly; do not query disclosure periods, generate reports, or parse subjective company-quality labels.

### 4. Persist formal outputs

Write the complete 300-stock candidates, formal Top10, Markdown, HTML, and audit caches before starting forward analysis. These files are transaction A and must remain valid even if the later forward stage fails.

### 5. Analyze forward dividends by default

After transaction A succeeds, the main runner starts the independent forward-dividend transaction for the persisted formal Top10. It prepares original evidence, forecasts supported companies, calculates expected and target dividend yields, and writes the separate forward outputs. A forward failure may fail the combined command and update its own status file, but must never roll back or modify the formal candidates, Top10, ranks, scores, gates, or factor contributions.

## Hard Gates

Hard gates do not contribute points:

- five-year yield persistence and data sufficiency;
- three consecutive implemented cash-dividend fiscal years;
- `10% <` three-year average annual payout ratio `< 100%` and `0% <` latest payout ratio `< 100%`;
- twelve quarterly TTM ROE observations with standard deviation in the CSI 300 lowest 80%;
- four positive annual `profit_dedt` observations with `latest / three-years-earlier - 1 >= -10%` (tolerates ordinary fluctuation, rejects a lowered profit base);
- latest comparable DPS / prior-three-year median comparable DPS at least 70%;
- nonfinancial companies: latest CFO/dividend at least 1 and three-year median at least 1;
- latest audited annual opinion is standard unqualified;
- no direct property-development, property-sales, or commercial-property-operation main business.
- rule v2 (default), nonfinancial companies only: latest annual EBIT / interest expense at least 3 (net debt at or below zero passes outright; a blank Tushare `ebit_to_interest` falls back to income-statement EBIT / interest expense; missing both is `data_gap`);
- rule v2 (default), nonfinancial companies only: latest annual `profit_dedt` at most twice its 10-year median (cross-cycle gate against cyclical profit peaks; fewer than eight annual observations is not judged);
- banks additionally require three complete audited annual observations for all eight bank metrics, latest NPL ratio at most 2%, provision coverage at least 150%, actual loan provision ratio at least 1.5%, core Tier-1 capital at least 7.5%, Tier-1 capital at least 8.5%, and total capital adequacy at least 10.5%.
- insurers additionally require three complete annual observations and the latest available periodic disclosure; clear group and material-subsidiary entity binding; group and material-subsidiary core solvency at least 50% and comprehensive solvency at least 100%; no disclosed C/D risk rating or explicit regulatory dividend restriction; and the same regulatory floors under each issuer-disclosed standalone group stress. Do not combine independent stresses.

There is no company-quality category gate.

## Top10 Portfolio Constraint

Fill the formal Top10 by total score descending, but admit at most five companies with the same `stock_basic.industry` (banks included; a three-bank cap was backtested on 2026-11-02 and rejected because it only left slots empty). A company skipped by this cap keeps its score and full-universe `rank`, is marked `concentration_capped` with an industry-cap reason, and the next eligible company fills the slot. `top10_rank` is the position inside the formal Top10 (1–10, no gaps); reports and the forward table show it as 排名, with the full-universe rank kept for audit. The cap changes only Top10 membership, never scores or hard gates.
 Missing mandatory structured evidence is `data_gap`, not zero and not a hard-gate failure. Do not create a formal ranking while a required factor remains a data gap.

## Scoring

The formal screen runs rule v2 by default (`--rule-version v2`, adopted 2026-11-02 after the 2016–2025 backtest in `docs/scoring-v2-backtest-comparison.md`; definitions in `docs/scoring-v2-proposal.md` and `scripts/rules_v2.py`). `--rule-version v1` reproduces the earlier rule. Every output records its rule version, and a structured-factor cache built under one rule version is never reused for another.

Use market-preflight passers as the reference pool for the quality block. Do not winsorize. Before ranking, round factors to their disclosure precision so sub-precision gaps tie: ROE and DROE 0.1pp, bank NPL change and NIM change 0.05pp, DPS growth 1pp. Rank with average ties. Bank-specific and bank common-factor percentiles use the bank pool; nonfinancial percentiles use the nonfinancial pool. Other-financial companies benchmark common factors against all financial passers, preventing a one-company insurance cohort from mechanically scoring 100. Reverse bank NPL level/change and bank cost-income ratio. Do not redistribute missing weights per company.

Rule v2 total = 50% dividend record + 50% operating quality.

Dividend record, ranked across all structured-gate passers regardless of group (each 1/3):

- consecutive dividend years, capped at 15;
- comparable-DPS cuts in the last 10 fiscal years (a year below 95% of the prior year, or no dividend; fewer is better);
- five-year comparable DPS CAGR.

Insurance companies continue to use the other-financial weights after passing the insurance safety gate. Run `simulate_insurance_scoring.py` for point-in-time research; do not activate separate formal insurance weights unless the research sample contains enough issuers and subsequent DPS-cut events.

Operating quality, ranked within group:

- nonfinancial: latest TTM ROE 40%, DROE 20%, three-year cumulative FCF/dividend coverage 40%;
- bank: latest TTM ROE 20%, DROE 10%, NPL quality 20% (`80% × latest NPL reverse percentile + 20% × three-year NPL change reverse percentile`), provision quality 15% (provision-coverage percentile; the actual loan provision ratio is a hard gate, not scored), capital resilience 15% (`60% × core Tier-1 percentile + 40% × total capital adequacy percentile`), NIM quality 10% (`70% × latest NIM percentile + 30% × three-year NIM change percentile`), cost-income ratio 10% (lower is better);
- other financial: latest TTM ROE 65%, DROE 35%.

DROE is scored as standardized DROE (DROE / the company's own twelve-quarter TTM ROE standard deviation) in every group. DPS growth is the log-linear regression growth rate over five fiscal comparable DPS values. Consecutive years measure the A-share comparable record only, so A+H companies that returned to A shares recently are systematically understated; this is a known, accepted limitation. Financial companies have no FCF coverage factor; their weight model already totals 100%.

The report lists every company whose only failed hard gate was a v2 gate (debt service or cross-cycle), with the reason.

Rule v1 (legacy, `--rule-version v1`) has no v2 gates and scores each group on its own pool: nonfinancial ROE 20%, DROE 10%, OPCFD 15% (TTM CFO / interest-bearing debt; zero debt takes the top unless CFO is not positive), FCF coverage 20%, five-year yield CV 10% (reversed), uncapped consecutive dividend years 10%, DPS CAGR 15%; other financial ROE 30%, DROE 15%, yield CV 20%, consecutive years 20%, DPS CAGR 15%; bank ROE 15%, DROE 5%, NPL quality 15%, provision quality 10%, capital resilience 10%, NIM quality 10%, cost-income 5%, yield CV 10%, consecutive years 10%, DPS CAGR 10%.

## Company Profile Reuse

Company profile text normally changes slowly. Cache it with the constituent snapshot and source/fetch date. Refresh when creating a new run-date universe, when constituents change, when the user requests a profile refresh, or when any mandatory profile field is empty. Do not silently classify an empty profile as non-property.

## Bank Metrics Preparation

Tushare provides general bank profitability fields but not the complete set of disclosed NPL, provision, net-interest-margin, and regulatory-capital ratios. After market preflight, the main runner automatically calls `prepare_bank_metrics.py` when the run-date bank cache is absent or incomplete. It resolves every preflight-passing bank from the compact CSI 300 bank map, downloads only reports available by the run date, extracts original annual-report text, validates the latest three audited years, and writes the combined cache. The individual commands below are diagnostic fallbacks:

```bash
python3 scripts/download_bank_reports.py 601229 \
  --out ./bank_reports/601229 --as-of-date YYYYMMDD
python3 scripts/extract_bank_reports.py \
  ./bank_reports/601229/manifest.json --out ./bank_reports/601229_text --backend pdftotext
python3 scripts/parse_bank_metrics.py \
  ./bank_reports/601229_text/*年度报告*.md --out ./bank_quality_metrics.csv
```

Combine all bank rows into `{data_root}/a_dividend_outputs/{YYYYMMDD}/market_data/bank_quality_metrics.csv`. The shared report cache belongs at `{data_root}/_bank_report_cache`, outside the skill installation. The parser must retain source file, page, and raw evidence. The formal run stops if any preflight bank lacks three complete annual observations or any required metric; missing values are `data_gap`, never zero. Refresh the compact stock map when the preflight bank set changes.

For a standalone batch repair:

```bash
python3 scripts/prepare_bank_metrics.py \
  --output data/a_dividend_outputs/YYYYMMDD/market_data/bank_quality_metrics.csv \
  --as-of-date YYYYMMDD --bank-codes 600036.SH 601398.SH
```

## Insurance Metrics Preparation

For every market-preflight insurance company, the main runner prepares `market_data/insurance_quality_metrics.csv` from original issuer reports available by the run date. It keeps insurance-group, group-parent and material-subsidiary facts separate; retains the latest three complete annual reports plus the latest available interim/quarterly report; and stores page excerpts, announcement IDs, extractor version and stable evidence IDs. Shared files live in `{data_root}/_insurance_report_cache`.

The parent cash bridge distinguishes fiscal-year DPS from cash actually paid in the period. Subsidiary remittances and ending available funds are reported as coverage fields; financing remains visible but never counts as operating dividend coverage. Missing subjects, periods, stresses, bridge reconciliation or required capital values is `data_gap` and stops formal ranking.

```bash
python3 scripts/prepare_insurance_metrics.py \
  --output data/a_dividend_outputs/YYYYMMDD/market_data/insurance_quality_metrics.csv \
  --as-of-date YYYYMMDD --insurance-codes 601318.SH
```

## Run

The main command runs both the formal screen and forward-dividend analysis by default:

```bash
python3 scripts/run_a_dividend_strategy.py \
  --run-date YYYYMMDD
```

Runtime data must never be stored inside the installed skill directory. The data-root precedence is: explicit `--data-root`, `AHONGLI_DATA_ROOT`, then `<workspace>/data` when installed at `<workspace>/.agents/skills/ahongli`; other installations use the invoking working directory's `./data`. Invoke an installed script from the user's project workspace rather than changing into a global skill directory. Report the resolved data root at handoff. For an explicit portable location:

```bash
python3 .agents/skills/ahongli/scripts/run_a_dividend_strategy.py \
  --run-date YYYYMMDD \
  --data-root ./data
```

Do not initialize a temporary `.git` inside an installed skill. Replay manifests use the Git commit in a real checkout and a deterministic runtime-tree SHA-256 in a packaged installation without Git metadata.

This change does not move legacy installation-local data automatically. Before resynchronizing an existing skill, copy any needed `a_dividend_outputs/` and `_bank_report_cache/` into the resolved persistent data root and verify the copied files; do not delete the originals as part of the runner.

Use `--skip-fetch` only when the market, profile, and structured-factor caches cover the applicable stage. Use `--refresh-constituents` when the user asks for a fresh CSI 300 universe.

Use `--skip-forward-dividend` only when the user explicitly wants transaction A without forward DPS and yield outputs:

```bash
python3 scripts/run_a_dividend_strategy.py \
  --run-date YYYYMMDD \
  --skip-forward-dividend
```

## Threshold Simulation

Keep the formal threshold at 3%. For counterfactual threshold research, use the isolated simulator; it builds or reuses all-constituent structured evidence and never writes formal filenames:

```bash
python3 scripts/simulate_dividend_threshold.py \
  --run-date YYYYMMDD --yield-threshold 2.5 --data-root ./data
```

Simulation output goes to `{data_root}/a_dividend_outputs/{YYYYMMDD}_sim_yield_{threshold}/`. Never present a simulation as the formal strategy result.

## Historical Backtest

`scripts/backtest_dividend_strategy.py` reruns the formal screen at past rebalance dates (default: every 1 November, 2016–2025) on point-in-time data and measures what happened afterwards. It calls the same production functions as the formal run; it only supplies as-of-date frames.

```bash
python3 scripts/backtest_dividend_strategy.py --asof YYYYMMDD --data-root ./data
```

- Membership: the latest Tushare monthly `index_weight` snapshot on or before each rebalance date, including companies later removed or delisted.
- Statements: only reports whose period has ended and whose `f_ann_date`/`ann_date` is on or before the rebalance date. Dividends: only events implemented by then. Prices: five years ending on the rebalance date.
- The previous period's backtest Top10 drives the dividend-yield hysteresis.
- `--bank-mode structured_only` (default) skips the bank report gate and scores banks on the common factors, renormalized. `--bank-mode metrics_file --bank-metrics-file PATH` applies a bank metrics history, using only reports announced by each rebalance date. Build it with `scripts/prepare_backtest_bank_metrics.py --data-root {data_root} --runs RUN_ID...` (downloads the needed annual reports under `{data_root}/a_dividend_backtest/bank_reports/`; each metric falls back from the A-share report to the H-share copy and then the annual summary).
- Outcomes: dividend cut rate (within the next two fiscal years, a year whose comparable DPS and total cash dividend are both below 95% of the prior year, or a year with no cash dividend; a share issue that dilutes DPS while the total payout is maintained is not a cut), three-year realized DPS growth, equal-weight total return held to the next rebalance date against 中证红利全收益 (`H00922.CSI`) and 沪深300全收益 (`H00300.CSI`), and turnover. Only fiscal years complete by `--asof` are evaluated.
- Raw history is cached once per security under `{data_root}/a_dividend_backtest/raw/{asof}/`; `--offline` reruns from that cache. Results go to `{data_root}/a_dividend_backtest/runs/{run_id}/` with `report.md`, `summary.csv`, `outcomes.csv`, per-period candidates and Top10, and `manifest.json` recording the code identity. Runs refuse a dirty working tree unless `--allow-dirty` is given.
- Known limits: industry classification and main-business text are current, not historical; no trading costs.
- `--rule-version v2` evaluates the proposed scoring v2 (`docs/scoring-v2-proposal.md`, `scripts/rules_v2.py`): the v1 gates plus G7 debt service (latest annual EBIT / interest at least `interest_coverage_min`, default 3; net debt at or below zero passes; a blank Tushare `ebit_to_interest` falls back to income-statement EBIT / interest expense), then 50% cross-group dividend record (consecutive years capped at 15, 10-year DPS cut count, 5-year DPS growth) and 50% within-group quality (no dividend-yield CV, no OPCFD). In the backtest, candidate cross-cycle rules are off by default (the formal screen fixes `cycle_gate_max_ratio=2` and `cycle_gate_nonfinancial_only=1`): `--rule-param cycle_gate_max_ratio=2` excludes companies whose latest deducted profit exceeds twice its 10-year median; `--rule-param cycle_factor_weight=0.2` blends a cross-group percentile of that ratio into the score. `--rule-param interest_coverage_min=N`, `record_block_weight=W`, `cycle_gate_nonfinancial_only=1` and `profit_dedt_min_change=-0.15` (the v1 profit-trend gate threshold, for sensitivity) are also accepted. `--perturbations N` rescores each period N times with every weight multiplied by U(0.8, 1.2) and renormalized (gates fixed) and reports the mean Top10 overlap. To reproduce the formal rule pass `--rule-version v2 --rule-param cycle_gate_max_ratio=2 --rule-param cycle_gate_nonfinancial_only=1`.
- Pass `--data-root` explicitly (or set `AHONGLI_DATA_ROOT`); run from the skill directory without it, the default resolves inside the skill.

Never present a backtest Top10 as a formal result.

## Outputs

Write under `{data_root}/a_dividend_outputs/{YYYYMMDD}/`:

- `hs300-dividend-candidates-{YYYYMMDD}.csv` with all 300 constituents;
- `hs300-dividend-top10-{YYYYMMDD}.csv` containing only all-gate passers;
- `hs300-dividend-report-{YYYYMMDD}.md`;
- `hs300-dividend-dashboard-{YYYYMMDD}.html`;
- `hs300-dividend-forward-top10-{YYYYMMDD}.csv`;
- `hs300-dividend-forward-report-{YYYYMMDD}.md`;
- `hs300-dividend-forward-dashboard-{YYYYMMDD}.html`;
- auditable market, company-profile, dividend, structured-financial, and factor caches.

Every rejected row needs a specific non-empty reason. Distinguish `data_gap`, `hard_gate_failed`, and `selected`. Show raw gate values, profile source, main business, factor percentiles, weighted contributions, data periods, sources, and quality. Report stage counts: 300 total, market passers, structured passers, and final selections.

Legacy price, current-yield, persistence-ratio, text-safety, dividend-financing, execution-composite, company-quality, and real-estate-relevance scores must not affect the total.

## Forward-Dividend Analysis

Read [docs/forward-dividend-evidence-proposal.md](docs/forward-dividend-evidence-proposal.md) before changing the forward evidence, model, valuation, status, or output contracts.

The forward stage remains a separate transaction that consumes an already-written formal Top10. The main runner invokes it by default after persisting formal outputs. It must never change the formal candidates CSV, Top10 CSV, ranks, scores, gates, or factor contributions. Use the standalone command to rerun or repair only transaction B:

```bash
python3 scripts/run_forward_dividend_analysis.py \
  --run-date YYYYMMDD \
  --top10 data/a_dividend_outputs/YYYYMMDD/hs300-dividend-top10-YYYYMMDD.csv
```

After the run status, the command prints a compact A-share Top10 main table to stdout with only the formal rank, company, score, A-share quote, base forecast DPS, expected yield, next payment date, target position, target-yield range, and target-price range. The persisted CSV and per-company detail remain the auditable sources of truth for model inputs, DPS scenarios, dividend-event dates, evidence completeness, and uncertainty.

Both the main CLI and the default `run_pipeline()` API print the standard forward Top10 table immediately after forward analysis completes. When reporting a completed run to the user, reproduce this standard ten-column table without dropping or reordering columns: rank, company, score, A-share quote, base forecast DPS, expected dividend yield, next payment date, target position, target-yield range, and target-price range. The payment column is the issuer's calendar: select the earliest implemented regular A-share CNY payment with `payment_date >= run_date`, even if already ex-dividend. Show only `YYYY-MM-DD` for both `payment_today` and future `scheduled` payments, `待实施公告` for `pending_implementation`, and `—` for `no_upcoming_payment`. The displayed date describes an announced schedule, not confirmed receipt. Do not label an already-announced payment `not_announced` merely because its ex-date has passed. Keep this calendar separate from `forward_12m_eligible_dps`, which retains its quote-date/ex-dividend entitlement rules. Never estimate an exact payment date from historical cadence. Keep record and ex-dividend dates plus event/source IDs in CSV and company detail instead of expanding the main table. Do not replace the standard table with a narrower hand-built or Rich table. Additional commentary may follow it.

Use `--skip-prepare` only when every applicable Top10 evidence directory already contains a validated `forecast-evidence.json`. Forward results use independent `announced`, `modelled`, `data_gap`, `unsupported`, and `failed` states. Missing or unsupported forecasts keep numeric columns empty and retain the original formal rank and score.

Resolve each Top10 company's CNinfo `orgId` from the vendored `assets/stocks.json` and `assets/forward_stocks.json` first. For any code missing there, fall back to CNinfo's public A-share list, archived per run as `market_data/cninfo_stock_list.json` and reused on later runs for the same date, so a new Top10 entrant is never left without evidence. An evidence stage that ends in `data_gap` (for example no mapping, no documents) is reported as forecast `data_gap` with its reason, never as `unsupported`.

Normal preparation still queries current announcements and applies cutoff/revision selection, but checks PDF caches before downloading. Reuse SHA-256-verified PDFs from `{data_root}/_forward_report_cache/`, the current run, or prior dated forward manifests, keyed by issuer, announcement ID and source URL. Existing historical files remain untouched; populate the shared cache lazily and copy verified bytes into each self-contained run. `cache_status=reused` means no PDF network download; `downloaded` means a network fetch, while `storage_status` describes file writes. Preserve original `retrieved_at` separately from `cache_checked_at`, and report `pdf_cache` counts from run status. PDF changes or damaged page JSON invalidate extracted-text reuse. `--refresh-documents` on the standalone forward runner explicitly bypasses PDF reuse; never combine it with `--skip-prepare`. This PDF cache does not implement cross-date Tushare financial-data reuse.

The forward stage writes separate CSV, Markdown, HTML, run-status, source, fact, event, result, and per-company evidence artifacts. Derive the target-yield range as required total return minus sustainable dividend growth, using a dated risk-free rate and versioned industry risk/growth parameters. Never infer the target range from current price, current yield, or historical yield percentiles. Display only neutral target-position labels; the corresponding target-price range is a mechanical DPS/yield conversion, not intrinsic value or an investment instruction.

Every successful run must write `market_data/forward-dividend-replay-manifest.json` plus the exact policy snapshot. The manifest records the real Git commit when the skill root is a clean checkout, otherwise a deterministic packaged runtime-tree SHA-256; it also records runner parameters, Top10 hash, per-company evidence and original source-document hashes, policy hash, and forward CSV hash. Verify an archived run offline with `python3 scripts/forward_replay.py --manifest <path>`; hash mismatch, code mismatch, or any recomputed CSV field mismatch fails verification.

Insurance uses `insurance_operating_profit_cash_capital_v3`. Low/base/high each have separate operating-profit and payout assumptions. DPS is limited by group-parent ending available funds net of disclosed financing; the low cash scenario also reserves the latest complete annual subsidiary-investment amount as an evidenced higher-funding-need case. Solvency ratios remain pass/fail constraints unless original evidence supports a post-dividend capital amount. An announced current-year interim regular dividend is a floor. Missing insurance safety or cash evidence returns `data_gap`; the forward transaction never edits the formal rank.

Each normalized fact references a stable `evidence_span_id`; each span retains the source document ID, page, raw excerpt, and extractor version. Persist low/base/high profit and payout inputs plus fact-selection decisions. After actual regular DPS is disclosed, use `evaluate_forecast_accuracy.py` to retain signed/absolute error, relative error, range coverage, and model version.

Bank and insurance target-yield ranges add a 0.5 percentage-point safety margin to the lower bound only. Equivalently, use a 6.8% risk-spread lower bound instead of 6.3% for both categories, while retaining the 8.3% upper bound; for example, `5%–7%` becomes `5.5%–7%` and `6%–8%` becomes `6.5%–8%`. Do not apply this adjustment to telecom, utilities, or other non-bank, non-insurance companies.

Bind payout evidence to complete fiscal years. A full-year DPS that already includes an interim dividend must not be added to that interim dividend again, and a historical restatement footnote must not be assigned to the current report year. When a bank policy provides only a payout floor and three complete annual actual payout ratios are available, use the floor for the low scenario, the three-year median for the base scenario, and the three-year maximum for the high scenario, subject to the existing capital checks.

Policy retrieval must cover both `分红回报规划` and `股东回报规划`. A policy based on three-year average distributable profit, cash as a share of total distributions, or distributions including buybacks is not an annual cash-dividend/parent-profit ratio. Retain its basis as `payout_policy_constraint` and explain the model limitation; never substitute that percentage for cash payout. Profit and cashflow extraction must reject year headers, page numbers, adjacent metrics, and quarterly breakdowns as annual/comparable values. Historical payout tables keep the row year, not the enclosing report year.

The narrowly supported `nonfinancial_historical_payout_v1` is available after an existing model returns `data_gap` or `unsupported`, initially only for formally selected, standard-audit nonfinancial `家用电器` / `家电` companies. Before using or changing it, read [the model contract](references/nonfinancial-history-model.md). It requires three consecutive complete fiscal years of original-document cash dividends and parent profit, latest comparable interim profit, eligible all-ordinary share capital, and CFO/capex evidence. Reconcile implemented interim/final gross DPS to annual total; do not mix A-only cash with group profit or double-count interim dividends. Missing, future-dated, conflicting, or insufficient cashflow evidence leaves DPS empty. This is a high-uncertainty historical model, not an official payout policy or universal fallback; never tune it to a blogger's DPS. Persist the annual history, TTM bridge, source/fact choices, coverage, and assumptions. Target-yield policy remains independently versioned and unchanged by this model.

AHongli is an A-share strategy. Forward outputs must use the formal `.SH` / `.SZ` instrument, A-share pre-tax DPS, and CNY quote/dividend currency. Never mix H-share or HKD dividends into A-share forward yield or twelve-month eligible DPS. Consolidated profit may use all ordinary shares as the forecast denominator when the distribution applies to all ordinary shares, but record that explicitly as `forecast_share_denominator_scope=all_ordinary_shares`.
