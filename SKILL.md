---
name: ahongli
description: Use when screening the latest CSI 300 constituents for quality-dividend candidates with auditable hard gates and separate bank/financial/nonfinancial scoring, followed by evidence-backed forward DPS and target-yield analysis for the formal Top10 by default.
---

# AHongli Dividend Strategy

## Purpose

Screen the latest 沪深300 constituents for durable quality-dividend candidates, then analyze the formal Top10's forward DPS and target yield by default. This is not a current-yield chase or investment instruction. A company must pass all hard gates before its factor score can qualify it for the formal Top10.

Read [references/strategy-spec.md](references/strategy-spec.md) before changing factor calculations, thresholds, weights, or selection behavior. Read [references/data-contract.md](references/data-contract.md) before changing Tushare fields, company-profile caching, or output columns.

Read [references/bank-metrics.md](references/bank-metrics.md) when collecting or evaluating bank-specific asset-quality, margin, provision, or regulatory-capital metrics. These metrics are part of the formal bank hard gates and score.

## Required Skill

Use `$tushare` for constituents, company profiles, market history, dividends, financial statements, audit results, and all structured evidence. This strategy does not consume subjective `financial-report-reader` analysis reports. Bank-only NPL, provision, NIM, efficiency, and regulatory-capital metrics are parsed deterministically from original annual reports.

## Workflow

Run these stages in order and persist the count entering and leaving each stage.

### 1. Refresh the 300-stock universe and profiles

Fetch or reuse the exact latest `000300.SH` snapshot and require exactly 300 unique constituents. At the same time fetch Tushare `stock_company` for `SSE` and `SZSE`, join by `ts_code`, and require non-empty `main_business`, `business_scope`, and `introduction` for all 300 companies.

`main_business` is the primary direct-property evidence. `stock_basic.industry` is a fast explicit-industry signal. `business_scope` and `introduction` are retained for audit but do not independently exclude a construction, materials, or financial company merely because their broad legal scope mentions property.

### 2. Market preflight

Fetch or reuse five-year `daily_basic.dv_ttm` data.

Hard market preflight:

- at least 1000 valid dividend-yield trading days;
- valid-data coverage at least 80%;
- `dv_ttm >= 3%` on at least 80% of valid days.

Current price, current dividend yield, and the pass ratio may be displayed but never scored. Five-year yield CV is calculated here and scored later.

### 3. Structured financial gates and scoring

For market-preflight passers, fetch or reuse Tushare `dividend`, `income`, `fina_indicator`, `cashflow`, `balancesheet`, and `fina_audit` evidence. Obtain enough structured ROE inputs for all 300 companies to calculate the CSI 300 lowest-volatility 80% benchmark.

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
- four positive annual `profit_dedt` observations with latest not below three years earlier;
- latest comparable DPS / prior-three-year median comparable DPS at least 70%;
- nonfinancial companies: latest CFO/dividend at least 1 and three-year median at least 1;
- latest audited annual opinion is standard unqualified;
- no direct property-development, property-sales, or commercial-property-operation main business.
- banks additionally require three complete audited annual observations for all eight bank metrics, latest NPL ratio at most 2%, provision coverage at least 150%, actual loan provision ratio at least 2.5%, core Tier-1 capital at least 7.5%, Tier-1 capital at least 8.5%, and total capital adequacy at least 10.5%.

There is no company-quality category gate. Missing mandatory structured evidence is `data_gap`, not zero and not a hard-gate failure. Do not create a formal ranking while a required factor remains a data gap.

## Scoring

Use market-preflight passers as the scoring reference pool. Winsorize each factor at 2.5% and 97.5%. Bank-specific and bank common-factor percentiles use the bank pool; nonfinancial percentiles use the nonfinancial pool. Other-financial companies retain their own weight model but benchmark common factors against all financial passers, preventing a one-company insurance cohort from mechanically scoring 100. Reverse five-year yield CV, bank NPL level/change, and bank cost-income ratio. Do not redistribute missing weights per company.

Nonfinancial weights:

- latest TTM ROE 20%;
- DROE 10%;
- OPCFD 15%;
- three-year cumulative FCF/dividend coverage 20%;
- five-year yield CV 10%;
- actual consecutive dividend years 10%;
- five-year comparable DPS CAGR 15%.

Financial weights:

- latest TTM ROE 30%;
- DROE 15%;
- five-year yield CV 20%;
- actual consecutive dividend years 20%;
- five-year comparable DPS CAGR 15%.

Bank weights:

- latest TTM ROE 15%;
- DROE 5%;
- NPL quality 15% (`80% × latest NPL reverse percentile + 20% × three-year NPL change reverse percentile`);
- provision quality 10% (provision-coverage percentile only); actual loan provision ratio is a separate hard gate and is not scored;
- capital resilience 10% (`60% × core Tier-1 percentile + 40% × total capital adequacy percentile`);
- NIM quality 10% (`70% × latest NIM percentile + 30% × three-year NIM change percentile`);
- cost-income ratio 5% (lower is better);
- five-year yield CV 10%;
- actual consecutive dividend years 10%;
- five-year comparable DPS CAGR 10%.

Do not cap consecutive dividend years at ten. Financial companies do not receive zero for OPCFD or FCF coverage; those factors are not applicable and their separate model already totals 100%.

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

Combine all bank rows into `a_dividend_outputs/{YYYYMMDD}/market_data/bank_quality_metrics.csv`. The parser must retain source file, page, and raw evidence. The formal run stops if any preflight bank lacks three complete annual observations or any required metric; missing values are `data_gap`, never zero. Refresh the compact stock map when the preflight bank set changes.

For a standalone batch repair:

```bash
python3 scripts/prepare_bank_metrics.py \
  --output a_dividend_outputs/YYYYMMDD/market_data/bank_quality_metrics.csv \
  --as-of-date YYYYMMDD --bank-codes 600036.SH 601398.SH
```

## Run

The main command runs both the formal screen and forward-dividend analysis by default:

```bash
python3 scripts/run_a_dividend_strategy.py \
  --run-date YYYYMMDD
```

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
  --run-date YYYYMMDD --yield-threshold 2.5
```

Simulation output goes to `a_dividend_outputs/{YYYYMMDD}_sim_yield_{threshold}/`. Never present a simulation as the formal strategy result.

## Outputs

Write under `a_dividend_outputs/{YYYYMMDD}/`:

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
  --top10 a_dividend_outputs/YYYYMMDD/hs300-dividend-top10-YYYYMMDD.csv
```

After the run status, the command prints a compact A-share Top10 main table to stdout with only the formal rank, company, score, A-share quote, base forecast DPS, expected yield, next payment date, target position, target-yield range, and target-price range. The persisted CSV and per-company detail remain the auditable sources of truth for model inputs, DPS scenarios, dividend-event dates, evidence completeness, and uncertainty.

Both the main CLI and the default `run_pipeline()` API print the standard forward Top10 table immediately after forward analysis completes. When reporting a completed run to the user, reproduce this standard ten-column table without dropping or reordering columns: rank, company, score, A-share quote, base forecast DPS, expected dividend yield, next payment date, target position, target-yield range, and target-price range. The payment column shows the exact `next_dividend_payment_date` only when formally evidenced, `待实施公告` for a current dividend plan without implementation dates, and `—` when no qualifying event exists. Never estimate an exact payment date from historical cadence. Keep record and ex-dividend dates plus event/source IDs in CSV and company detail instead of expanding the main table. Do not replace the standard table with a narrower hand-built or Rich table. Additional commentary may follow it.

Use `--skip-prepare` only when every applicable Top10 evidence directory already contains a validated `forecast-evidence.json`. Forward results use independent `announced`, `modelled`, `data_gap`, `unsupported`, and `failed` states. Missing or unsupported forecasts keep numeric columns empty and retain the original formal rank and score.

The forward stage writes separate CSV, Markdown, HTML, run-status, source, fact, event, result, and per-company evidence artifacts. Derive the target-yield range as required total return minus sustainable dividend growth, using a dated risk-free rate and versioned industry risk/growth parameters. Never infer the target range from current price, current yield, or historical yield percentiles. Display only neutral target-position labels; the corresponding target-price range is a mechanical DPS/yield conversion, not intrinsic value or an investment instruction.

Every successful run must write `market_data/forward-dividend-replay-manifest.json` plus the exact policy snapshot. The manifest records the clean Git commit, runner parameters, Top10 hash, per-company evidence and original source-document hashes, policy hash, and forward CSV hash. Verify an archived run offline with `python3 scripts/forward_replay.py --manifest <path>`; hash mismatch, code mismatch, or any recomputed CSV field mismatch fails verification.

Each normalized fact references a stable `evidence_span_id`; each span retains the source document ID, page, raw excerpt, and extractor version. Persist low/base/high profit and payout inputs plus fact-selection decisions. After actual regular DPS is disclosed, use `evaluate_forecast_accuracy.py` to retain signed/absolute error, relative error, range coverage, and model version.

Bank target-yield ranges add a 0.5 percentage-point safety margin to the lower bound only. Equivalently, use a 6.8% bank risk-spread lower bound instead of 6.3%, while retaining the 8.3% upper bound; for example, `5%–7%` becomes `5.5%–7%` and `6%–8%` becomes `6.5%–8%`. Do not apply this adjustment to insurance, telecom, utilities, or other non-bank companies.

Bind payout evidence to complete fiscal years. A full-year DPS that already includes an interim dividend must not be added to that interim dividend again, and a historical restatement footnote must not be assigned to the current report year. When a bank policy provides only a payout floor and three complete annual actual payout ratios are available, use the floor for the low scenario, the three-year median for the base scenario, and the three-year maximum for the high scenario, subject to the existing capital checks.

AHongli is an A-share strategy. Forward outputs must use the formal `.SH` / `.SZ` instrument, A-share pre-tax DPS, and CNY quote/dividend currency. Never mix H-share or HKD dividends into A-share forward yield or twelve-month eligible DPS. Consolidated profit may use all ordinary shares as the forecast denominator when the distribution applies to all ordinary shares, but record that explicitly as `forecast_share_denominator_scope=all_ordinary_shares`.
