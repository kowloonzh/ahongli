# Data And Output Contract

## Tushare fields

- `index_weight`: latest monthly `000300.SH` weights; `weight_trade_date` records their date and new constituents have empty weights.
- CSI official `000300cons.xls`: complete run-date membership, validated against the latest or preceding SSE trading day, with any one-day lag and unverified same-day adjustments disclosed, and archived with URL and SHA-256. This live file is not a historical source; reject a different date. A run-date archive whose hash matches and whose snapshot date still qualifies is reused instead of downloading again.
- `stock_basic`: name, industry, listing metadata, and explicit industry classification.
- `stock_company`: `main_business`, `business_scope`, and `introduction` for every constituent.
- `daily`, `daily_basic.dv_ttm`: display prices and five-year yield history.
- `dividend`: `end_date`, `div_proc`, `cash_div_tax`, `base_share`, implementation dates, and stock-dividend/transfer fields.
- `income.n_income_attr_p`: parent-attributable profit and TTM construction.
- `fina_indicator.profit_dedt`: four-year adjusted-profit trend.
- `cashflow.n_cashflow_act`, `cashflow.c_pay_acq_const_fiolta`: CFO, TTM CFO, and FCF.
- `balancesheet.st_borr`, `lt_borr`, `bond_payable`, `non_cur_liab_due_1y`: interest-bearing debt, the OPCFD denominator; `balancesheet.total_hldr_eqy_exc_min_int`: TTM ROE denominator; `total_liab` is retained for audit.
- `fina_audit.audit_result`: latest annual audit gate.

Financial API date-range inputs filter announcement dates. Query through `run_date`, then select statement periods from returned `end_date`. Use consolidated statements, keep only records available by `run_date`, and deduplicate revisions deterministically. Page or query per stock when a full-period endpoint can truncate.

## Required caches

Persist source query parameters, fetch timestamp, fields, row counts, failed segments, and deduplication decisions. The constituent cache must contain all mandatory company-profile fields for exactly 300 unique codes. Market-preflight failures do not require detailed financial-factor caches beyond common benchmark data.

All mutable runtime data lives under a persistent `{data_root}` outside the installed skill: formal and forward results under `{data_root}/a_dividend_outputs/`, downloaded/extracted bank reports under `{data_root}/_bank_report_cache/`, and insurance reports under `{data_root}/_insurance_report_cache/`. Resolve `{data_root}` from explicit CLI input, then `AHONGLI_DATA_ROOT`, then the project workspace. A packaged skill without `.git` uses a deterministic hash of runtime scripts and assets as its replay code identity; never create `.git` only to satisfy replay metadata.

## Output states

- `selected`: all mandatory evidence present and every hard gate passed.
- `hard_gate_failed`: evidence is sufficient and at least one hard condition failed.
- `concentration_capped`: passed every hard gate but was skipped from the Top10 because its industry already holds five members; score and rank are retained.
- `data_gap`: mandatory evidence is missing, stale, truncated, or unparseable.

Formal output cannot rank a `data_gap` or `hard_gate_failed` row as selected. Every non-selected row must retain the first decisive reason plus auditable gate fields.

The complete CSV contains exactly 300 unique rows. The Top10 CSV contains at most ten selected rows sorted by total score descending, with at most five per `stock_basic.industry`. Current price and current yield may remain display fields but have no score contribution.

## Required bank metrics cache

The deterministic bank parser writes these required fields from original annual-report text:

- `npl_ratio`
- `provision_coverage_ratio`
- `loan_provision_ratio`
- `net_interest_margin`
- `cost_income_ratio`
- `core_tier1_capital_ratio`
- `tier1_capital_ratio`
- `capital_adequacy_ratio`

Optional extensions include attention-loan, overdue-loan, real-estate NPL, and personal-loan NPL ratios. Every parsed metric keeps `source_file`, page, label, and raw evidence in `metric_evidence_json`. Missing required fields set `data_quality=data_gap`.

`bank_quality_metrics.csv` must contain exactly the latest three audited annual periods for every market-preflight bank. A bank with incomplete rows cannot enter a formal run. Parsed source paths and evidence remain in the cache; the candidate CSV carries the latest raw metrics, three-year changes, gate result, composite percentiles, and weighted contributions.

When the cache is missing or incomplete, the runner prepares it automatically from original CNinfo annual reports available by `run_date`. Reuse the shared `_bank_report_cache` for PDFs and extracted text, but rebuild the run-date CSV and revalidate exact bank-code and three-year coverage before scoring.

## Required insurance metrics cache

`insurance_quality_metrics.csv` contains the latest three complete annual records plus the latest available periodic record for every market-preflight insurer. Every record carries `period`, `report_type`, `available_date`, group capital amounts and solvency ratios, material-subsidiary JSON, standalone stress JSON, risk rating when public and applicable, original source path/announcement ID, and `metric_evidence_json` with page, excerpt, definition, entity, extractor version and evidence ID.

Annual records additionally carry group-parent beginning/ending available funds, subsidiary dividends received, external cash dividends paid, subsidiary investments, financing and other uses, the reconciled bridge residual, coverage ratios, parent-attributable operating profit and operating-profit payout ratio. These cash-period facts are separate from fiscal-year dividend events. Financing is retained as a source but excluded from recurring coverage and the forward cash cap. Future-dated documents, incomplete periods, conflicting entity/units or unparsable mandatory facts are `data_gap`.

## Independent Forward-Dividend Outputs

The optional forward stage consumes only the already-written formal Top10 and writes independent `hs300-dividend-forward-*` outputs. It must preserve the formal Top10 SHA-256 and mtime for the whole transaction.

Forward numeric columns are numeric or empty; status text never replaces missing numbers. Per-company status is one of `announced`, `modelled`, `data_gap`, `unsupported`, or `failed`. `unsupported` means evidence exists but no model applies; an evidence stage that ended in `data_gap` is reported as `data_gap`. A CNinfo `orgId` resolved from the public stock list is archived in `market_data/cninfo_stock_list.json`. Evidence completeness and forecast uncertainty are separate fields.

Every non-empty forecast must reference original source documents, page evidence, normalized facts, dividend events, a model ID/version, and explicit assumptions. Target-yield outputs must retain the dated risk-free rate, risk-spread range, sustainable-growth input, model ID/version, and mechanically derived target-price range. Historical yield percentiles are not part of the forward-output contract.

The forward Top10 carries the issuer's formally evidenced A-share regular-dividend payment calendar: `next_dividend_date_status`, `next_dividend_event_id`, `next_dividend_record_date`, `next_dividend_ex_date`, `next_dividend_payment_date`, and `next_dividend_evidence_source_ids`. Select the earliest valid `payment_date >= run_date` from implementation events, ordered by payment date (not ex-date), then ex-date and event ID for deterministic ties. Include events already ex-dividend but not yet past their scheduled payment date; exclude special, H-share, non-CNY and known future-announced events. Set `payment_today` on the payment day; later payments are `scheduled`. Display only `YYYY-MM-DD` for both statuses, without claiming actual receipt. A current plan lacking implementation is `pending_implementation` / `待实施公告`, regardless of tentative dates. No qualifying schedule is `no_upcoming_payment` / `—`, not a claim that a historical payment was never announced. CSV keeps dates as `YYYYMMDD`; display labels belong only in Markdown/HTML/console. Never infer dates from historical cadence. This is separate from `forward_12m_eligible_dps`: that field still uses the quote date and excludes already ex-dividend events for a new buyer. A displayed issuer payment is not proof of an investor's entitlement.

Persist `forecast_payout_ratio_low`, `forecast_payout_ratio`, and `forecast_payout_ratio_high` separately when payout assumptions vary by scenario. Complete-year payout facts exclude interim periods and historical footnotes assigned to another year. A `full_year` dividend event supersedes arithmetic addition of its component interim and final events for annual payout reconstruction.

Insurance forward rows also persist `forecast_cash_cap_total`, `forecast_cash_cap_dps`, `forecast_insurance_constraints`, `announced_dividend_floor`, and assumptions explaining that standalone stresses were not combined and solvency percentages were not converted to distributable cash without amount evidence. Missing any mandatory safety or scenario fact yields empty DPS and `data_gap`.

Also persist `forecast_profit_low`, `forecast_profit`, `forecast_profit_high`, `forecast_selection_decisions`, `target_yield_policy_id`, and `target_yield_policy_sha256`. Every normalized fact has an `evidence_span_id` resolving to a span with source document, page, raw excerpt, and extraction backend.

The limited nonfinancial historical model additionally persists `forecast_history`, `forecast_ttm_bridge`, `forecast_ttm_cfo`, `forecast_ttm_capex`, `forecast_fcf_dividend_coverage`, and `forecast_assumptions`. Phase-level cash amounts must reconcile to complete fiscal-year gross DPS and cover all ordinary shares in CNY. Eligible ordinary shares exclude repurchases. Each fact and corroborating implementation is cutoff-checked. Complete inputs do not imply low uncertainty: this route is always high uncertainty and fails closed on conflict or insufficient cashflow coverage. See [model scope, formula and evidence rules](nonfinancial-history-model.md).

The replay manifest is the portable integrity root for a run. It hashes the formal Top10, every per-company `forecast-evidence.json`, every referenced original source document, the copied policy snapshot, and the forward CSV, and records the exact Git commit and runtime parameters. Offline replay must validate all hashes and reproduce every CSV field.

Forward PDF cache: `{data_root}/_forward_report_cache/records/` maps issuer + announcement ID + URL to immutable content-addressed objects. Normal runs query fresh announcement metadata before cache lookup, then verify original SHA-256 and PDF signature before copying. Prior dated manifests seed the shared cache without moving/deleting historical files. Preserve original `retrieved_at`; record `cache_checked_at`, `cache_origin`, `cache_path`, `cache_validation_notes`, `cache_status` (`reused`/`downloaded`) and separate `storage_status`. Missing/corrupt copies try another verified local copy before network fetch. Conflicting historical hashes require refresh; HTML error bodies must not enter the PDF cache. Shared and run copies are independent, not hardlinks. Run-status `pdf_cache` counts describe successful prepared documents, not failed network attempts. The shared cache is optional for replay because each run retains its own source PDFs. A page-extraction sidecar binds source SHA-256, extractor version and page-JSON SHA-256; legacy unverified extraction caches are regenerated once. Source-side silent replacement at an unchanged ID/URL requires explicit standalone `--refresh-documents`.

All forward instruments are A shares. Require `.SH` / `.SZ`, `share_class=A`, `quote_currency=CNY`, and `dividend_currency=CNY`. Mixed A/H disclosures must select explicit A-share pre-tax CNY DPS; H-share or HKD-only events are ineligible. When consolidated profit and payout apply to all ordinary shares, record `forecast_share_denominator_scope=all_ordinary_shares` instead of describing the denominator as A-share capital.
