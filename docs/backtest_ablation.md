# GLD / QQQ mechanism ablation

`scripts/backtest_mechanism_ablation.py` compares eight runs in a complete 2×2×2 design. The three independent switches are:

1. cumulative-TWR stepped profit-taking;
2. peak-to-trough drawdown exposure caps;
3. redeployment of idle sale proceeds.

All variants use the same local GLD/QQQ adjusted-close snapshots, common observed dates, 50/50 target mix, starting amount, weekly contributions, and transaction costs. The default example is USD 10,000 initial capital, USD 50 each Friday-ending week, 5 bps per trade, 20/35/50% one-time take-profit triggers selling 10/15/20% of current risk holdings, and 15/25% drawdown triggers that cap invested assets at 50/25% of account value.

`reinvest_cash=false` leaves sale proceeds in zero-yield cash. When enabled, cash left from earlier periods is redeployed at the next weekly contribution date, before that date's new contribution, up to the active drawdown exposure cap. New weekly contributions remain a separate flow in the accounting. The policy does not assume that cash earns interest or that a trigger predicts a market bottom.

The script reports each of the eight combinations and paired differences for each switch, averaging over all settings of the other two switches. It also records event/trade logs, daily unitized NAV, account equity, cash/risk weights, and monthly time-weighted returns. The paired averages summarize interactions; they do not establish an out-of-sample causal edge.

## Reproduction

The public repository does not contain Yahoo market-price files. Place the locally retained `GLD.csv`, `QQQ.csv`, and their `.csv.meta.json` provenance files under `data/raw/market/`, then run:

```bash
uv sync --locked --all-packages --group dev
uv run --locked python scripts/backtest_mechanism_ablation.py \
  --project-root . \
  --start-date 2004-11-18 \
  --end-date 2026-10-09 \
  --output-dir reports/backtests/gld_qqq_mechanism_ablation
```

The output directory contains an aligned input-price snapshot, a SHA-256 manifest, full JSON and Markdown reports, daily series, and monthly returns. The program checks that the no-control reinvestment toggle is an identity and, for the frozen reference inputs and assumptions, verifies the earlier DCA-only and combined-control results. Do not commit privately licensed price snapshots or generated files to the public repository.

The checks are descriptive for this one historic interval and parameter set. Adjusted closes are revised price histories, not point-in-time vintages. The included 15%/25% exposure rules do not target or guarantee a 10% maximum drawdown.
