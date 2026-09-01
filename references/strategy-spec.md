# Quality-Dividend Strategy Specification

## Dividend state

Use a single `dividend_anchor_year` across payout, continuity, recent-cut, and CAGR calculations. Prefer the latest completed fiscal year when its status is determined. If it is merely not implemented yet, fall back one year only. An explicit no-dividend decision anchors that year at zero and cannot be bypassed.

Keep only implemented cash events available by `run_date`. Use tax-inclusive `cash_div_tax`; ignore implemented stock-only events. Sum interim, special, and final cash events by fiscal `end_date`.

Cash-dividend total is `cash_div_tax * base_share * 10000`. A verified record-date total-share fallback is allowed only when `base_share` is missing and must be identified as fallback evidence.

Normalize historical DPS for bonus issues, capital-reserve transfers, and splits. Persist raw and comparable annual DPS. If corporate-action evidence is insufficient, mark a data gap rather than compare raw DPS silently.

## Structured hard gates

Annual payout ratio is actual tax-inclusive cash-dividend total divided by annual parent-attributable net profit. Average the three annual ratios arithmetically.

Build quarterly TTM profit from cumulative PRC statements:

- FY: current full-year value;
- Q1/H1/Q3: prior FY + current YTD - prior-year same-period YTD.

TTM ROE is TTM parent-attributable profit divided by the average parent equity at the current and prior-year same period. DROE is latest TTM ROE minus prior-year same-period TTM ROE. The stability gate uses the standard deviation of twelve TTM ROEs and keeps the CSI 300 lowest 80%.

Profit trend requires four positive annual `profit_dedt` values and `latest / three-years-earlier - 1 >= 0`.

Recent-cut protection requires latest comparable DPS divided by the preceding-three-year median comparable DPS to be at least 70%.

For nonfinancial companies, annual CFO/dividend must be at least 1 in the latest year and have a three-year median at least 1.

A non-standard latest annual audit opinion fails. Direct property development, sales, or commercial-property operation is a portfolio exclusion determined from cached `stock_basic.industry` and `stock_company.main_business`. Broad legal `business_scope` text is audit evidence only and must not independently exclude construction, materials, or financial companies. There is no subjective company-quality category gate.

Banks must also have complete parsed values for the latest three audited annual reports. The latest NPL ratio must be at most 2%, provision coverage at least 150%, actual loan provision ratio at least 2.5%, core Tier-1 capital adequacy at least 7.5%, Tier-1 capital adequacy at least 8.5%, and total capital adequacy at least 10.5%. These are strategy hard gates, not points.

## Scoring formulas

- `ROE`: latest TTM ROE.
- `DROE`: latest TTM ROE - prior-year same-period TTM ROE.
- `OPCFD`: TTM CFO / latest total liabilities; nonfinancial only.
- `FCF dividend coverage`: `sum_3y(CFO - c_pay_acq_const_fiolta) / sum_3y(actual cash dividends)`; nonfinancial only.
- `Yield CV`: five-year daily `dv_ttm` population standard deviation / mean; lower is better.
- `Consecutive years`: actual uninterrupted implemented cash-dividend years through the anchor year; no ten-year cap.
- `DPS CAGR`: `(latest comparable DPS / oldest comparable DPS)^(1/4) - 1` over five fiscal observations.
- `Bank NPL quality`: 80% latest NPL reverse percentile plus 20% reverse percentile of latest minus three-years-earlier NPL.
- `Bank provision quality`: latest provision-coverage percentile only. Actual loan provision ratio must be at least 2.5% as a hard gate and receives no score. `Excess loan provision` is `actual loan provision ratio - max(2.5%, 1.5 × latest NPL ratio)` and is an audit field only.
- `Bank capital resilience`: 60% core Tier-1 percentile plus 40% total-capital-adequacy percentile.
- `Bank NIM quality`: 70% latest-NIM percentile plus 30% percentile of latest minus three-years-earlier NIM.
- `Bank cost-income`: latest disclosed cost-income ratio, lower is better.

Use the bank, other-financial, and nonfinancial weights and standardization process in `SKILL.md`. Other-financial common factors use all financial passers as their percentile reference; their own weight model remains separate. Only all-gate passers may appear in the formal Top10.

## Removed factors

Do not score current price, current dividend yield, the five-year pass ratio, one-year price volatility, text dividend safety, dividend financing, the old five-year execution composite, company-quality category, direct-property relevance, or total-cash-dividend CAGR. Do not apply old quality score caps.
