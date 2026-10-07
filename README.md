# macro-regime-quant

A from-scratch, long-horizon macro regime research framework for China, the US, and global cross-asset markets.

The project is intentionally built in layers:

1. **Data layer** — reproducible, provenance-aware macro and market data.
2. **Backtest layer** — point-in-time-safe portfolio simulation with execution lag and transaction costs.
3. **Macro model** — later: growth/inflation/liquidity factors, AD-AS interpretation, regime classification.
4. **Allocation layer** — later: China/US/global asset allocation and risk budgeting.

## v0 scope

This first version only establishes the two foundations that should not be rewritten every time the model evolves:

- data catalog + provider interfaces;
- a simple but explicit backtest engine and performance metrics.

## Design principles

- **No look-ahead by construction.** Signals are applied with an execution lag.
- **Point-in-time first.** Macro observations need an `available_date`; a crude release lag is supported now, vintage data comes later.
- **Frozen raw inputs are first-class.** CSV/Parquet snapshots are the reproducibility baseline even when live providers exist.
- **Provider is not the model.** FRED / Yahoo / AKShare / official downloads are adapters behind a stable schema.
- **Long horizon.** Default research frequency is monthly; target holding horizons are 3M/6M/12M.

## Repository structure

```text
config/                 data and asset catalogs
docs/                   data/backtest contracts
src/macro_regime_quant/
  data/                  schemas, catalog, providers
  backtest/              engine and metrics
tests/                   synthetic tests for leakage and accounting
```

## Planned v1

- China growth/inflation/credit/liquidity factors;
- US growth/inflation/real-rate/liquidity factors;
- global USD/commodity/global-PMI transmission layer;
- four-regime baseline, then HMM / Markov switching only after the rule model is validated.
