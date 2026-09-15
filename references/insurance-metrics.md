# Insurance Safety And Forward-Dividend Contract

## Subjects and periods

Keep insurance group, group parent, and each material life/property/health/pension subsidiary as separate entities. Select only documents whose formal availability date is on or before the run date. A formal record needs the latest three complete annual periods and the latest available periodic disclosure. Never fill a missing subject or period from a different entity.

## Original facts

Retain group core capital, actual capital, minimum capital, core solvency ratio and comprehensive solvency ratio; applicable public risk ratings; material-subsidiary capital and ratios; and each issuer-disclosed rate/equity solvency sensitivity. Evidence includes definition, entity, period, availability date, unit, announcement ID, source file, page, raw excerpt, extractor version and evidence ID.

For the group parent, retain beginning and ending available funds, subsidiary dividends received, external dividends actually paid, subsidiary investments, debt/equity financing and other major uses. Reconcile the disclosed bridge. `subsidiary dividends / external dividends` and `ending funds / external dividends` are research coverage measures. Financing is disclosed separately and excluded from recurring coverage.

Keep the fiscal year to which a dividend belongs separate from the calendar period in which the parent paid cash. Do not use one as the other.

## Formal safety gate

Core solvency must be at least 50% and comprehensive solvency at least 100% for the group and each material insurance subsidiary. Apply the same floors to every issuer-disclosed standalone group stress result. A public C/D risk rating or an explicit issuer-specific regulatory dividend restriction fails. Missing mandatory evidence, unreconciled parent cash, entity confusion, unit conflict or period gaps is `data_gap`.

Additional buffers and trends are research fields. Do not invent elimination thresholds beyond a regulatory or accounting basis. Do not add independent rate and equity stresses.

## Forward DPS version 3

For each scenario:

`DPS = max(announced interim regular DPS, min(operating profit × payout ratio / shares, non-financing parent cash / shares))`

Low/base/high use separate profit and payout ratios. The low cash scenario additionally reserves the latest complete annual amount invested into subsidiaries as an evidenced proxy for higher funding needs; this is a forecast constraint, not a formal elimination threshold. Build payout scenarios from the latest three complete annual operating-profit payout ratios and compatible formal policy evidence. Solvency percentages are safety conditions, not distributable amounts. A capital amount limit may be added only when original evidence identifies capital and the effect of the proposed dividend.

The model returns `data_gap` when mandatory safety, cash, capital, historical payout, shares or operating-profit scenario evidence is absent. Its outputs are transaction B and do not change transaction A's formal score or rank.

## Historical research

Use `simulate_insurance_scoring.py` to create point-in-time observations and subsequent regular-DPS labels. Future DPS is an outcome only. Report gate-only and independent-score simulations separately. Keep formal other-financial weights until a multi-issuer sample has enough known outcomes and actual cuts to assess detection and stable-dividend false exclusions.
