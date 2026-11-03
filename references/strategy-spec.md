# Quality-Dividend Strategy Specification

## Dividend state

Use a single `dividend_anchor_year` across payout, continuity, recent-cut, and CAGR calculations. Prefer the latest completed fiscal year when its status is determined. If it is merely not implemented yet, fall back one year only. An explicit no-dividend decision anchors that year at zero and cannot be bypassed.

Keep only implemented cash events available by `run_date`. Use tax-inclusive `cash_div_tax`; ignore implemented stock-only events. Sum interim, special, and final cash events by fiscal `end_date`. Before summing, drop Tushare rows that repeat the same event and differ only by `ann_date` (identical fiscal period, progress, cash/stock amounts, base share, record/ex/pay dates, and implementation announcement date); summing such duplicates doubles DPS and cash totals.

Cash-dividend total is `cash_div_tax * base_share * 10000`. A verified record-date total-share fallback is allowed only when `base_share` is missing and must be identified as fallback evidence.

Normalize historical DPS for bonus issues, capital-reserve transfers, and splits. Persist raw and comparable annual DPS. If corporate-action evidence is insufficient, mark a data gap rather than compare raw DPS silently.

## Market preflight

Daily yield uses `daily_basic.dv_ttm` on determined days, meaning days with a positive `close`. A blank `dv_ttm` on a determined day is 0% because Tushare leaves it blank when the trailing twelve months contain no cash dividend. Data sufficiency requires at least 1000 determined days and determined-day coverage of at least 80%. The entry line is `dv_ttm >= 3%` on at least 80% of determined days; a previous formal Top10 member (latest earlier-dated formal Top10 in the data root) is retained down to 75%.

## Structured hard gates

Annual payout ratio is actual tax-inclusive cash-dividend total divided by annual parent-attributable net profit. Average the three annual ratios arithmetically.

Build quarterly TTM profit from cumulative PRC statements:

- FY: current full-year value;
- Q1/H1/Q3: prior FY + current YTD - prior-year same-period YTD.

TTM ROE is TTM parent-attributable profit divided by the average parent equity at the current and prior-year same period. DROE is latest TTM ROE minus prior-year same-period TTM ROE. The stability gate uses the standard deviation of twelve TTM ROEs and keeps the CSI 300 lowest 80%.

Profit trend requires four positive annual `profit_dedt` values and `latest / three-years-earlier - 1 >= -10%`. The 10% tolerance separates ordinary fluctuation from a lowered profit base; it does not remove base-year dependence.

Recent-cut protection requires latest comparable DPS divided by the preceding-three-year median comparable DPS to be at least 70%.

For nonfinancial companies, annual CFO/dividend must be at least 1 in the latest year and have a three-year median at least 1.

A non-standard latest annual audit opinion fails. Direct property development, sales, or commercial-property operation is a portfolio exclusion determined from cached `stock_basic.industry` and `stock_company.main_business`. Broad legal `business_scope` text is audit evidence only and must not independently exclude construction, materials, or financial companies. There is no subjective company-quality category gate.

Banks must also have complete parsed values for the latest three audited annual reports. The latest NPL ratio must be at most 2%, provision coverage at least 150%, actual loan provision ratio at least 1.5% (the lower end of the regulatory dynamic range; provision coverage ≥150% already implies loan provision ≥1.5 × NPL, so a higher fixed floor would only penalize low-NPL banks), core Tier-1 capital adequacy at least 7.5%, Tier-1 capital adequacy at least 8.5%, and total capital adequacy at least 10.5%. These are strategy hard gates, not points.

Insurers must have three complete annual insurance records and the latest available interim or quarterly record, all cutoff by announcement availability. Require explicit insurance-group, group-parent and material-subsidiary binding. Group and each material insurance subsidiary must meet core solvency 50% and comprehensive solvency 100%; a disclosed C/D risk rating, explicit regulatory dividend restriction, or an issuer-disclosed standalone group stress below either floor fails. Independent rate and equity stresses are never added. Missing subjects, periods, capital facts, stress facts or a reconciled parent available-funds bridge is `data_gap`.

## Scoring formulas

- `ROE`: latest TTM ROE.
- `DROE`: latest TTM ROE - prior-year same-period TTM ROE, displayed raw. The scored value is `droe_standardized = DROE (rounded to 0.1pp) / roe_stability_12q`, so insurers' larger ROE swings do not dominate; zero volatility with zero DROE scores as 0.
- `OPCFD`: TTM CFO / interest-bearing debt at the same period end, where interest-bearing debt is `st_borr + lt_borr + bond_payable + non_cur_liab_due_1y` (blank components on an existing balance-sheet row count as 0). Payables and contract liabilities are excluded because they reflect bargaining power, not repayment pressure. With zero interest-bearing debt, `opcfd` is blank, `interest_bearing_debt` is 0, and the company ranks at the top of the reference pool when TTM CFO is positive and at the bottom otherwise. Nonfinancial only.
- `FCF dividend coverage`: `sum_3y(CFO - c_pay_acq_const_fiolta) / sum_3y(actual cash dividends)`; nonfinancial only.
- `Yield CV`: five-year daily `dv_ttm` population standard deviation / mean; lower is better.
- `Consecutive years`: actual uninterrupted implemented cash-dividend years through the anchor year; no ten-year cap. It measures the A-share comparable record only, so A+H companies that listed or returned to A shares recently are understated.
- `DPS CAGR` (`dps_cagr_5y`): `exp(slope) - 1`, where `slope` is the OLS slope of `ln(comparable DPS)` on fiscal year over five consecutive fiscal observations, all of which must be positive. This is less sensitive to the endpoint years than a two-point CAGR.

Percentiles are average-tie ranks without winsorizing. Before ranking, round ROE and DROE to 0.1pp, bank NPL change and NIM change to 0.05pp, and DPS growth to 1pp, so differences below disclosure precision tie.
- `Bank NPL quality`: 80% latest NPL reverse percentile plus 20% reverse percentile of latest minus three-years-earlier NPL.
- `Bank provision quality`: latest provision-coverage percentile only. Actual loan provision ratio must be at least 1.5% as a hard gate and receives no score. `Excess loan provision` is `actual loan provision ratio - max(1.5%, 1.5 × latest NPL ratio)` and is an audit field only.
- `Bank capital resilience`: 60% core Tier-1 percentile plus 40% total-capital-adequacy percentile.
- `Bank NIM quality`: 70% latest-NIM percentile plus 30% percentile of latest minus three-years-earlier NIM.
- `Bank cost-income`: latest disclosed cost-income ratio, lower is better.

Use the bank, other-financial, and nonfinancial weights and standardization process in `SKILL.md`. Other-financial common factors use all financial passers as their percentile reference; their own weight model remains separate. Only all-gate passers may appear in the formal Top10. At most five Top10 members may share one `stock_basic.industry`; a capped company keeps its score and rank, is marked `concentration_capped`, and the next eligible company fills the slot.

Insurance retains the other-financial score after its safety gate. Separate insurance factor weights are research-only until point-in-time multi-issuer history demonstrates useful early detection of regular-DPS cuts or capital/coverage deterioration without excessive stable-dividend exclusions.

## Removed factors

Do not score current price, current dividend yield, the five-year pass ratio, one-year price volatility, text dividend safety, dividend financing, the old five-year execution composite, company-quality category, direct-property relevance, or total-cash-dividend CAGR. Do not apply old quality score caps.
