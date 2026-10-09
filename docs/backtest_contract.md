# Backtest contract v1

## Purpose

A transparent long-horizon allocation test, not a high-frequency execution simulator. The research stack owns the reusable engine; the CLI package owns strategy storage and the authenticated chat-facing API.

The workflow is:

1. load price observations and, for macro-driven ideas, join by `available_date`;
2. compute a signal or fixed target weights on a signal date;
3. apply the execution lag so the signal cannot earn the same-period return;
4. trade only when a target-weight instruction is issued;
5. let holdings drift with relative realized returns between trades;
6. charge costs on absolute traded notional;
7. save the exact market input snapshot and report for replay.

## Anti-leakage rules

1. **Signal date != observation date.** Macro inputs are joined by `available_date`.
2. **No same-close execution.** `execution_lag_periods=1` is the default.
3. **No full-sample normalization later.** Rolling/expanding z-scores only use information available at t.
4. **No revised-data fantasy.** Prefer vintages/publication snapshots for materially revised data.
5. **No benchmark cherry-picking.** Fix benchmarks and universe before evaluating variants.
6. **No hidden costs.** Turnover and cost assumptions are explicit.
7. **No silent price repair.** Incomplete/non-positive prices are rejected by the engine; the workbench aligns requested assets on common observed dates and reports dropped rows. It never forward-fills prices.

## Accounting

For asset returns `r_t` and pre-trade portfolio weights `w_{t-1}`:

```text
gross_t = w_{t-1} · r_t
target turnover_t = sum(abs(target_weight_t - pre_trade_weight_t))
cost_t = target turnover_t * cost_bps / 10_000
net_t = gross_t - cost_t
```

Sparse target instructions are shifted by the execution lag. A forward-filled target is not interpreted as a daily trade. After each period return, invested weights drift by asset performance. If weights sum below one, the remainder is zero-return cash. Portfolio metrics use daily data, 252 trading periods per year, and a 0% risk-free rate for Sharpe.

The workbench supports long-only target weights with `buy_and_hold`, monthly, quarterly, or annual rebalancing. An optional `CashFlowPlan` adds a weekly contribution, one-time cumulative-TWR take-profit tiers, and peak-to-trough drawdown exposure caps. Each close-derived take-profit or drawdown signal executes at the next available close. Weekly deposits use the last available session in the selected week and are excluded from time-weighted returns and drawdown; XIRR uses all deposits and ending account value. Profit-taking proceeds remain in zero-yield cash unless a drawdown recovery rebalances exposure back to target. Drawdown caps reduce exposure but cannot guarantee a maximum loss. Taxes, cash yield, market impact, short positions, and margin remain outside scope.

Example risk plan for a 50/50 GLD/QQQ strategy: contribute USD 50 weekly, take 10%, 15%, and 20% of then-current risk holdings when cumulative time-weighted return first reaches 20%, 35%, and 50%, respectively; cap risky exposure at 50% after a 15% drawdown and 25% after a 25% drawdown. Both take-profit and drawdown rules are configurable and are assumptions, not recommended defaults.

## Reproducibility and caveats

Each run stores the strategy definition, requested/actual dates, provider, price basis, retrieval timestamp, source hashes, aligned input-price SHA-256, code version, assumptions, metrics, monthly/annual returns, and drawdown. The exact input price snapshot and JSON/Markdown reports are retained under the configured local state directory.

Yahoo auto-adjusted history has no point-in-time vintage. A stored snapshot reproduces the run, but does not prove what adjusted-price values were historically visible at each past date. Use a licensed, point-in-time price source for historical signal research that depends on price revisions or corporate actions.

## Validation ladder

1. synthetic deterministic tests for execution lag, drift, turnover, and costs;
2. buy-and-hold sanity check;
3. equal-weight monthly rebalance and benchmark comparison;
4. simple 60/40 allocation;
5. only then introduce macro signals;
6. walk-forward/out-of-sample evaluation before any HMM or ML upgrade.
