import pandas as pd
import pytest
from mrq_cli.backtest_workbench.models import StrategySpec
from mrq_cli.backtest_workbench.runner import run_portfolio_backtest


def test_monthly_rebalance_retargets_after_drift():
    dates = pd.to_datetime(["2025-01-31", "2025-02-03", "2025-02-28", "2025-03-03"])
    prices = pd.DataFrame(
        {"GLD": [100.0, 200.0, 200.0, 200.0], "QQQ": [100.0, 100.0, 100.0, 200.0]},
        index=dates,
    )
    strategy = StrategySpec(
        name="50-50 monthly",
        weights={"GLD": 0.5, "QQQ": 0.5},
        rebalance_frequency="monthly",
        transaction_cost_bps=0,
    )

    run = run_portfolio_backtest(prices, strategy)

    # The first 50/50 allocation captures the 100% GLD move, then drifts to 2/3 GLD.
    assert run.result.effective_weights.loc[dates[1], "GLD"] == 0.5
    assert run.result.returns.loc[dates[1]] == 0.5
    # Rebalance at the prior close when March begins.
    assert run.result.effective_weights.loc[dates[3], "GLD"] == 0.5
    assert run.result.effective_weights.loc[dates[3], "QQQ"] == 0.5
    assert run.dropped_incomplete_rows == 0


def test_runner_reports_dropped_non_overlapping_rows():
    dates = pd.date_range("2025-01-01", periods=3, freq="D")
    prices = pd.DataFrame(
        {"GLD": [100.0, 101.0, 102.0], "QQQ": [100.0, float("nan"), 102.0]},
        index=dates,
    )
    strategy = StrategySpec(name="50-50", weights={"GLD": 0.5, "QQQ": 0.5})

    run = run_portfolio_backtest(prices, strategy)

    assert len(run.prices) == 2
    assert run.dropped_incomplete_rows == 1


def test_runner_rejects_missing_asset_or_short_history():
    dates = pd.date_range("2025-01-01", periods=2, freq="D")
    strategy = StrategySpec(name="50-50", weights={"GLD": 0.5, "QQQ": 0.5})
    with pytest.raises(ValueError, match="missing strategy assets"):
        run_portfolio_backtest(pd.DataFrame({"GLD": [1, 2]}, index=dates), strategy)
    with pytest.raises(ValueError, match="two common complete"):
        run_portfolio_backtest(pd.DataFrame({"GLD": [1, None], "QQQ": [1, None]}, index=dates), strategy)
