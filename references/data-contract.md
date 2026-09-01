# Data And Output Contract

## Tushare fields

- `index_weight`: latest `000300.SH` constituents and weights.
- `stock_basic`: name, industry, listing metadata, and explicit industry classification.
- `stock_company`: `main_business`, `business_scope`, and `introduction` for every constituent.
- `daily`, `daily_basic.dv_ttm`: display prices and five-year yield history.
- `dividend`: `end_date`, `div_proc`, `cash_div_tax`, `base_share`, implementation dates, and stock-dividend/transfer fields.
- `income.n_income_attr_p`: parent-attributable profit and TTM construction.
- `fina_indicator.profit_dedt`: four-year adjusted-profit trend.
- `cashflow.n_cashflow_act`, `cashflow.c_pay_acq_const_fiolta`: CFO, TTM CFO, and FCF.
- `balancesheet.total_liab`, `balancesheet.total_hldr_eqy_exc_min_int`: OPCFD and TTM ROE denominators.
- `fina_audit.audit_result`: latest annual audit gate.

Financial API date-range inputs filter announcement dates. Query through `run_date`, then select statement periods from returned `end_date`. Use consolidated statements, keep only records available by `run_date`, and deduplicate revisions deterministically. Page or query per stock when a full-period endpoint can truncate.

## Required caches

Persist source query parameters, fetch timestamp, fields, row counts, failed segments, and deduplication decisions. The constituent cache must contain all mandatory company-profile fields for exactly 300 unique codes. Market-preflight failures do not require detailed financial-factor caches beyond common benchmark data.

## Output states

- `selected`: all mandatory evidence present and every hard gate passed.
- `hard_gate_failed`: evidence is sufficient and at least one hard condition failed.
- `data_gap`: mandatory evidence is missing, stale, truncated, or unparseable.

Formal output cannot rank a `data_gap` or `hard_gate_failed` row as selected. Every non-selected row must retain the first decisive reason plus auditable gate fields.

The complete CSV contains exactly 300 unique rows. The Top10 CSV contains at most ten selected rows sorted by total score descending. Current price and current yield may remain display fields but have no score contribution.

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
