from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd
from mrq_cli.backtest_workbench.models import StrategySpec
from mrq_cli.backtest_workbench.runner import run_portfolio_backtest
from mrq_cli.backtest_workbench.store import StrategyStore


def main() -> None:
    dates = pd.to_datetime(["2020-01-31", "2020-02-03", "2020-02-28", "2020-03-02"])
    prices = pd.DataFrame(
        {"GLD": [100.0, 105.0, 110.0, 108.0], "QQQ": [100.0, 102.0, 98.0, 104.0]},
        index=dates,
    )
    strategy = StrategySpec(
        name="Smoke: Gold Nasdaq 50/50",
        weights={"GLD": 0.5, "QQQ": 0.5},
        rebalance_frequency="monthly",
        initial_capital=10_000,
        transaction_cost_bps=5,
    )
    result = run_portfolio_backtest(prices, strategy)
    assert result.result.returns.notna().all()
    assert result.result.ending_weights.loc[dates[1]].sum() == 1.0
    assert result.result.ending_weights.loc[dates[-1], "GLD"] == 0.5

    with tempfile.TemporaryDirectory() as directory:
        record = StrategyStore(Path(directory) / "strategies").save(strategy)
        assert StrategyStore(Path(directory) / "strategies").get(record.strategy_id).spec == strategy
    print("SMOKE_OK", round(float((1 + result.result.returns).prod() - 1), 6), "return")


if __name__ == "__main__":
    main()
