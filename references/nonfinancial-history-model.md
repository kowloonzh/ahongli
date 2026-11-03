# Nonfinancial Historical Cash-Payout Model

## Scope and precedence

`nonfinancial_historical_payout_v1`, version `1`, is a limited historical sensitivity model, not a company forecast or dividend commitment. The first supported industries are exactly `家用电器` and `家电`; there are no issuer-code exceptions. Require `selection_status=selected`, explicit nonfinancial classification, and standard unqualified audit. Formal ranking and gates do not change.

Announced full-year DPS and existing policy, bank, and insurance models retain precedence. Invoke this model only after the existing route returns `data_gap` or `unsupported`. Other nonfinancial industries do not inherit it automatically. Industry membership alone is not evidence that future earnings or payout will be stable.

## Required evidence and units

For run year Y, obtain original documents available by the end of `run_date`:

- The immediately preceding three annual parent-attributable profits, in CNY, with positive profit in each year.
- Each year's regular cash distribution to all ordinary shareholders, assigned to the dividend's fiscal year, not its payment calendar year. Require implemented gross DPS for each phase plus annual-report total DPS; phase sums must equal that annual total.
- Mixed A/H issuers require complete A+H ordinary cash amounts expressed in CNY and the corresponding all-ordinary profit/share denominator. A-only cash must not stand in for total company distributions. The report's CNY A/H ordinary-dividend note may corroborate an implementation by unambiguous fiscal year, phase and gross DPS. Approximate report amounts retain their original excerpt; no false precision is claimed.
- Latest Y H1 and comparable Y−1 H1 parent profit, annual CFO for each historical year, Y H1 and comparable CFO, and Y−1 annual / Y H1 / comparable capex cash outflows.
- Latest disclosed ordinary shares eligible for distributions, excluding repurchased shares. Do not substitute total listed A shares or a historical capital base; the forecast assumes this latest eligible base remains unchanged.

`implemented_regular_dps`, `annual_regular_dps_total`, `regular_cash_distribution`, `dividend_eligible_shares`, `history_cfo`, `history_capex` and their prior-comparable variants retain stable fact and span IDs, source document, page, units, period and raw excerpt. Cash facts also retain phase, gross DPS, all-ordinary scope and any corroborating implementation IDs.

Every selected fact's source and corroborating source must be available by the analysis cutoff. Cache presence does not waive that check. Reject missing units, future/missing source dates, unsupported currency/scope, quarterly metrics, parent-only cashflow tables, ambiguous dividend-year matches, and conflicting values. Identical repeated pages are not additional distributions. Differences beyond 1 ppm are conflicts; within rounding tolerance, choose latest available source then stable fact ID and retain all candidate IDs. This is not a majority or maximum-value selection rule.

## Calculation

For each historical fiscal year i:

```text
payout_i = total regular cash dividends_i / parent-attributable profit_i
TTM profit = prior FY profit − prior H1 profit + current H1 profit
TTM CFO    = prior FY CFO    − prior H1 CFO    + current H1 CFO
TTM capex  = prior FY capex  − prior H1 capex  + current H1 capex
FCF proxy  = TTM CFO − TTM capex

DPS low  = TTM profit × min(payout_i)    / eligible ordinary shares
DPS base = TTM profit × median(payout_i) / eligible ordinary shares
DPS high = TTM profit × max(payout_i)    / eligible ordinary shares
```

All three scenarios keep the same TTM profit and share base. The payout range is a sensitivity range, not a probability interval. No analyst/blogger target or external DPS enters the formula. Do not add a profit growth uplift without introducing and validating a separate model version.

Require each historical payout in `(0, 100%]`, positive TTM profit and eligible shares, nonnegative TTM capex, positive CFO in each historical year and each comparable interim, latest annual CFO/dividend coverage ≥1, median three-year annual CFO/dividend coverage ≥1, and FCF proxy/base projected cash dividend ≥1. Failure yields `data_gap` with numeric DPS empty, not a silently capped estimate. Conflicting inputs use `evidence_completeness=conflict`.

The FCF proxy does **not** deduct buybacks, acquisitions or debt repayment and is not a complete assessment of capital needs. TTM reported profit may contain nonrecurring effects. The model has no validated forecast-accuracy track record yet. Always set `forecast_uncertainty=high` even when required input completeness is `complete`.

## Output and verification

Keep the standard Top10 main table unchanged. CSV/results carry `forecast_history` (year, profit, annual cash, annual DPS, phase amounts, payout and CFO coverage), `forecast_ttm_bridge`, `forecast_ttm_cfo`, `forecast_ttm_capex`, `forecast_fcf_dividend_coverage`, `forecast_assumptions`, and fact-selection audit. Details show historical ratios, TTM bridge and limitations. Preserve evidence/raw-PDF hashes and model/runtime identity for offline replay.

Target yield is a separate policy calculation. This model must not change risk premium, growth assumptions, or industry target-yield mapping to make the resulting price look attractive. A/H cash reconciliation for historical group payout does not authorize H-share instruments or HKD yields in the A-share main table.

Run the focused and complete regression suites, then verify an isolated recorded snapshot without network or archive overwrite:

```bash
python3 -m unittest discover -s tests -p test_nonfinancial_history.py
python3 -m unittest discover -s tests
python3 scripts/verify_cached_forward.py --archive /absolute/archive/YYYYMMDD \
  --run-date YYYYMMDD --diagnostics-root /absolute/persistent/data/diagnostics
```

The diagnostic reuses hash-verified archived PDFs through the normal prepare step and re-extracts them with `pdftotext`; it does not use `--skip-prepare`, rerank companies, or claim fresh network disclosure coverage. A new isolated directory contains output and a replay manifest. Do not overwrite the formal historical forward result merely to validate code changes.
