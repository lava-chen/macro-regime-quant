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

    # The first target fills at the Feb-3 close, after the earlier GLD jump.
    assert run.result.effective_weights.loc[dates[1], "GLD"] == 0.0
    assert run.result.returns.loc[dates[1]] == 0.0
    assert run.result.ending_weights.loc[dates[1], "GLD"] == 0.5
    # The Feb-28 to Mar-3 move is earned by the old allocation. The month-end
    # signal then restores the target at the Mar-3 close for later intervals.
    assert run.result.gross_returns.loc[dates[3]] == 0.5
    assert run.result.ending_weights.loc[dates[3], "GLD"] == 0.5
    assert run.result.ending_weights.loc[dates[3], "QQQ"] == 0.5
    assert run.dropped_incomplete_rows == 0


def test_runner_trims_price_rows_outside_shared_history():
    dates = pd.date_range("2025-01-01", periods=3, freq="D")
    prices = pd.DataFrame(
        {"GLD": [float("nan"), 101.0, 102.0], "QQQ": [100.0, 101.0, 102.0]},
        index=dates,
    )
    strategy = StrategySpec(name="50-50", weights={"GLD": 0.5, "QQQ": 0.5})

    run = run_portfolio_backtest(prices, strategy)

    assert len(run.prices) == 2
    assert run.dropped_incomplete_rows == 1


def test_runner_rejects_internal_price_gaps_without_market_calendar():
    dates = pd.date_range("2025-01-01", periods=3, freq="D")
    prices = pd.DataFrame(
        {"GLD": [100.0, 101.0, 102.0], "QQQ": [100.0, float("nan"), 102.0]},
        index=dates,
    )
    strategy = StrategySpec(name="50-50", weights={"GLD": 0.5, "QQQ": 0.5})

    with pytest.raises(ValueError, match="refusing to bridge the gap without an explicit market calendar"):
        run_portfolio_backtest(prices, strategy)


def test_runner_rejects_missing_asset_or_short_history():
    dates = pd.date_range("2025-01-01", periods=2, freq="D")
    strategy = StrategySpec(name="50-50", weights={"GLD": 0.5, "QQQ": 0.5})
    with pytest.raises(ValueError, match="missing strategy assets"):
        run_portfolio_backtest(pd.DataFrame({"GLD": [1, 2]}, index=dates), strategy)
    with pytest.raises(ValueError, match="two common complete"):
        run_portfolio_backtest(pd.DataFrame({"GLD": [1, None], "QQQ": [1, None]}, index=dates), strategy)


def test_cash_weight_is_residual_and_is_not_charged_as_a_traded_asset():
    dates = pd.date_range("2025-01-01", periods=3, freq="D")
    prices = pd.DataFrame(
        {"SPY": [100.0, 100.0, 110.0], "GLD": [100.0, 100.0, 100.0]}, index=dates
    )
    strategy = StrategySpec(
        name="70 cash",
        weights={"CASH": 0.7, "SPY": 0.2, "GLD": 0.1},
        transaction_cost_bps=10,
    )

    run = run_portfolio_backtest(prices, strategy)

    assert list(run.prices.columns) == ["SPY", "GLD"]
    assert run.result.turnover.loc[dates[1]] == pytest.approx(0.3)
    assert run.result.returns.loc[dates[1]] == pytest.approx(-0.0003)
    assert run.result.gross_returns.loc[dates[2]] == pytest.approx(0.02)
    assert run.result.effective_weights.loc[dates[2], "SPY"] == pytest.approx(0.2)
    assert run.result.effective_weights.loc[dates[2], "GLD"] == pytest.approx(0.1)
    assert run.result.effective_weights.loc[dates[2], "CASH"] == pytest.approx(0.7)
    assert run.result.ending_weights.loc[dates[2]].sum() == pytest.approx(1.0)
