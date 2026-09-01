# Formal Bank Metrics

## Purpose

Collect bank-specific quality evidence without restoring a subjective company-quality label. The parser reads original annual-report text and produces auditable structured values for the formal bank hard gates and bank-only score.

## Source priority

1. Tushare remains the source for ROE, ROA, profit, general statements, audit opinion, interest income/expense, and fields that can be reliably derived such as cost-income ratio.
2. Original CNinfo periodic-report PDFs are the source for NPL, provisions, NIM, regulatory capital, attention loans, and overdue loans.
3. Do not approximate regulatory capital with accounting equity, NIM with total assets, or provision coverage with current-period impairment expense.

The vendored `assets/stocks.json` contains all 24 banks in the current CSI 300 snapshot instead of copying the upstream 11MB full-market map. Refresh this compact map whenever the CSI 300 bank universe changes; a missing bank mapping is a data gap and must not be silently skipped.

## Required metrics

| Field | Direction | Meaning |
|---|---|---|
| `npl_ratio` | lower | non-performing loan ratio |
| `provision_coverage_ratio` | higher | provisions / NPL balance |
| `loan_provision_ratio` | hard gate at 2.5%, not scored | provisions / total loans |
| `net_interest_margin` | higher/stable | disclosed NIM or net interest yield |
| `cost_income_ratio` | lower | operating efficiency |
| `core_tier1_capital_ratio` | higher | core Tier-1 capital adequacy |
| `tier1_capital_ratio` | higher | Tier-1 capital adequacy |
| `capital_adequacy_ratio` | higher | total regulatory capital adequacy |

Optional fields: attention-loan ratio, overdue-loan ratio, property-loan NPL ratio, and personal-loan NPL ratio.

## Parsing rules

- Normalize simplified/traditional metric names.
- Prefer consolidated/group tables over bank-only, subsidiary, product, or narrative examples.
- Prefer table rows containing multiple comparable periods.
- Read the first current-period value after the metric label; never take a percentage that appears before the label.
- Keep source file, extracted page, matched label, and raw evidence.
- Required missing fields produce `data_gap`, never zero.
- Review any metric whose evidence is a subsidiary, product segment, or single-loan category before accepting it.

## Current validation

The formal parser was validated on 60 annual reports: the 20 market-preflight banks for 2023, 2024, and 2025. All eight required metrics were extracted with `data_quality=normal` after accounting for wrapped table rows, chart continuations, shortened labels, simplified/traditional aliases, and narrative percentage collisions. Revalidate this coverage whenever the run date changes the three-year annual window or the preflight bank set.
