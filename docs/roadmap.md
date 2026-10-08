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

Current collection implementation:
- original-release collectors for the indexed NBS and PBOC archives;
- row-level value-source and publication-date evidence URLs;
- explicit partial-source baseline mode that preserves configured component minimums;
- collected snapshots: NBS activity/prices/PMI from 2021-09/10 to 2026-08/09; PBOC M1/M2 from 2009-11 to 2026-08; TSF stock from 2014-12 to 2025-08;
- all currently collected rows have source-confirmed dates; the snapshots remain incomplete and several series have period gaps;
- [2026-10-07 quality report](research/china_official_snapshot_quality_2026-10.md) and [reproducible audit notebook](../notebooks/china_snapshot_quality.ipynb) document coverage, known gaps, factor/state availability, the US episode check, and point-in-time limitations.
- US vintage work is in [open PR #12](https://github.com/lava-chen/macro-regime-quant/pull/12): four inflation series have a quarterly archive in that branch, while the remaining 14 US series still lack archived vintages.

Remaining:
- recover China official history before the current archive floors (NBS about 2021-09; PBOC M1/M2 about 2009-11), and locate omitted PBOC 2025-01 rows and TSF reports after 2025-08;
- acquire core CPI, China 10-year government yields, Customs and SAFE sources;
- label unrecoverable availability dates as unknown rather than inventing dates;
- extend China factors and regime history beyond the current 2024-11 start;
- add China/Hong Kong assets, USD/CNY, cross-asset forward returns, transaction costs, benchmarks, and walk-forward/out-of-sample checks;
- improve the Yahoo market-data path and complete US forward-return mapping after resolving provider rate limits;
- qualitatively audit known US episodes such as 2008, 2020 and 2022 and China episodes once source history permits.

Do not call the China 2005–2026 state history complete until those source and coverage gaps are closed. HMM / Markov switching remains deferred until a rule-based point-in-time baseline has stable out-of-sample evidence.

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
