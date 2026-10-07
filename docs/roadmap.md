# Roadmap

The platform has two parallel tracks: finish a trustworthy macro baseline while building shared infrastructure for valuation and capital-flow research. New engines must not weaken point-in-time safety.

## Macro v1 — verified state history

Completed foundation:
- US Growth / Inflation / Real-Rate / Liquidity factors;
- China Growth / Inflation / Credit-Liquidity definitions;
- past-only expanding z-scores;
- rule-based four-regime classifier;
- China snapshot validator and release-date evidence policy;
- US regime-conditioned 3M / 6M / 12M forward returns.

Remaining:
- collect and source-check China official historical values and release evidence;
- label unrecoverable availability dates as unknown rather than inventing dates;
- generate verified 2005–2026 China and US factor/regime histories;
- qualitatively audit known episodes such as 2008, 2020 and 2022.

## Platform v0 — shared financial research foundation

Current:
- stable company/security identity layer;
- point-in-time company fundamental contract;
- DCF / Reverse DCF baseline;
- company-type valuation router;
- observed versus inferred flow contract;
- stock-flow accounting utilities;
- transparent cross-engine signal composition.

Next:
- entity/universe registry loader and validation;
- canonical fundamental metrics and financial-statement snapshot format;
- company quality / leverage / capital-allocation features;
- residual-income valuation for banks;
- AFFO/NAV framework for REITs;
- first observed daily flow datasets and flow-score research;
- model-run/version schema suitable for Postgres/Supabase.

## Macro v2 — China x US cross-market mapping

- China x US joint state without blindly treating all 16 quadrant combinations as equally meaningful;
- USD / real-rate / commodity / global-liquidity transmission layer;
- China / US / Hong Kong / gold / bond / commodity / FX forward-return mapping;
- benchmark comparisons and walk-forward validation.

## Valuation v1 — broad company coverage

- point-in-time financial statement ingestion;
- general-company DCF and Reverse DCF reports;
- valuation bands and margin-of-safety research;
- sector/business-model routing;
- cross-sectional value and quality backtests;
- no optimized score weights until out-of-sample baselines exist.

## Flow v1 — capital movement baseline

- distinguish observed versus inferred flows;
- ETF/fund AUM flow after valuation adjustment;
- margin / futures / options / issuance / buyback / ownership inputs where data permits;
- source -> destination flow edges with confidence;
- daily/weekly Flow Score with full decomposition;
- no claim of complete real-time visibility into all capital.

## Unified v1 — expected return and risk

Only after the component engines have independent evidence:

- evaluate Valuation x Macro interactions;
- evaluate Valuation x Flow interactions;
- add Risk Engine;
- build Portfolio Engine;
- compare slow strategic and fast tactical layers;
- require walk-forward / point-in-time validation before production promotion.

## Later — probabilistic models

Only after the interpretable baselines are stable:
- HMM / Markov switching;
- learned factor weights;
- state-space models;
- risk-budgeted allocation;
- model stability and sensitivity reports.
