# GLD / QQQ mechanism ablation

`scripts/backtest_mechanism_ablation.py` compares eight runs in a complete 2×2×2 design. The three independent switches are:

1. cumulative-TWR stepped profit-taking;
2. peak-to-trough drawdown exposure caps;
3. redeployment of idle sale proceeds.

All variants use the same local GLD/QQQ adjusted-close snapshots, common observed dates, 50/50 target mix, starting amount, weekly contributions, and transaction costs. The default example is USD 10,000 initial capital, USD 50 each Friday-ending week, 5 bps per trade, 20/35/50% one-time take-profit triggers selling 10/15/20% of current risk holdings, and 15/25% drawdown triggers that cap invested assets at 50/25% of account value.

`reinvest_cash=false` leaves sale proceeds in zero-yield cash. When enabled, cash already held at the prior close is redeployed at the next weekly contribution date, before that date's new contribution, up to the active drawdown exposure cap. Proceeds from sales executed at the contribution close are not available to reinvest until a later contribution date. New weekly contributions remain a separate flow in the accounting. The policy does not assume that cash earns interest or that a trigger predicts a market bottom.

Initial cash-flow capital is invested at the first available close. Close-derived take-profit and drawdown signals fill at the next available close; the return ending on that fill date belongs to the old holdings. Internal missing price observations in the shared asset-history window stop the run; no exchange holiday is inferred or filled.

The script reports each of the eight combinations and paired differences for each switch, averaging over all settings of the other two switches. It also records event/trade logs, daily unitized NAV, account equity, cash/risk weights, and monthly time-weighted returns. The paired averages summarize interactions; they do not establish an out-of-sample causal edge.

## Reproduction

The public repository does not contain Yahoo market-price files. Place the locally retained `GLD.csv`, `QQQ.csv`, and their `.csv.meta.json` provenance files under `data/raw/market/`, then run:

```bash
uv sync --locked --all-packages --group dev
uv run --locked python scripts/backtest_mechanism_ablation.py \
  --project-root . \
  --start-date 2004-11-18 \
  --end-date 2026-10-09 \
  --output-dir reports/backtests/gld_qqq_mechanism_ablation_p0_v2
```

The output directory contains an aligned input-price snapshot, a SHA-256 manifest, full JSON and Markdown reports, daily series, and monthly returns. The program checks that the no-control reinvestment toggle is an identity and compares all eight corrected variants with the preserved legacy report. That comparison is descriptive, not a golden-value assertion: variants that reinvest take-profit proceeds change because same-close proceeds are deferred to a later contribution date. Do not commit privately licensed price snapshots or generated files to the public repository.

The checks are descriptive for this one historic interval and parameter set. Adjusted closes are revised price histories, not point-in-time vintages. The included 15%/25% exposure rules do not target or guarantee a 10% maximum drawdown.
