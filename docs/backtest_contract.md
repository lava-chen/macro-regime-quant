# Backtest contract v0

## What this engine is for

A transparent long-horizon allocation test, not a high-frequency execution simulator.

The intended workflow is:

1. macro data becomes available;
2. features/regime are computed;
3. target weights are produced on a signal date;
4. positions become effective only after an execution lag;
5. portfolio returns are computed from then onward;
6. turnover costs are charged whenever effective weights change.

## Anti-leakage rules

1. **Signal date != observation date.** Macro inputs must be joined by `available_date`.
2. **No same-close execution.** Default `execution_lag_periods=1`.
3. **No full-sample normalization later.** Rolling/expanding z-scores must only use history available at t.
4. **No revised-data fantasy.** Prefer vintages/publication snapshots for data that gets materially revised.
5. **No benchmark cherry-picking.** Benchmarks and asset universe are fixed before evaluating a strategy variant.
6. **No hidden costs.** Turnover and bps cost assumptions are explicit.

## v0 accounting

Given asset return vector `r_t` and effective portfolio weights `w_{t-1}`:

```text
gross_t = w_{t-1} · r_t
turnover_t = sum(abs(w_t - w_{t-1}))
cost_t = turnover_t * cost_bps / 10_000
net_t = gross_t - cost_t
```

Cash is allowed by default and earns zero in v0. Later versions can add a cash/risk-free series.

## Required performance outputs

- CAGR
- annualized volatility
- Sharpe
- max drawdown
- Calmar
- turnover
- regime-conditioned forward return tables (later)

## Validation ladder

1. synthetic deterministic tests;
2. buy-and-hold sanity check;
3. equal-weight monthly rebalance;
4. simple 60/40-style allocation;
5. only then introduce macro signals;
6. walk-forward / out-of-sample evaluation before any HMM or ML upgrade.
