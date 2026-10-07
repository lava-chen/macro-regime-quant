import pandas as pd

from macro_regime_quant.backtest.engine import BacktestConfig, run_backtest


def test_execution_lag_blocks_same_period_return():
    idx = pd.date_range("2025-01-01", periods=4, freq="D")
    prices = pd.DataFrame({"A": [100.0, 110.0, 121.0, 133.1]}, index=idx)

    # Signal on day 2 requests 100% A. With lag=1, day-2 return must not be captured.
    weights = pd.DataFrame({"A": [1.0]}, index=[idx[1]])
    result = run_backtest(
        prices,
        weights,
        BacktestConfig(transaction_cost_bps=0.0, execution_lag_periods=1),
    )

    assert result.effective_weights.loc[idx[1], "A"] == 0.0
    assert result.effective_weights.loc[idx[2], "A"] == 1.0
    assert result.returns.loc[idx[1]] == 0.0
    assert round(result.returns.loc[idx[2]], 10) == 0.1


def test_transaction_cost_is_explicit():
    idx = pd.date_range("2025-01-01", periods=3, freq="D")
    prices = pd.DataFrame({"A": [100.0, 100.0, 100.0]}, index=idx)
    weights = pd.DataFrame({"A": [1.0]}, index=[idx[0]])

    result = run_backtest(
        prices,
        weights,
        BacktestConfig(transaction_cost_bps=10.0, execution_lag_periods=1),
    )

    # First effective purchase costs 10 bps of portfolio value.
    assert round(result.returns.loc[idx[1]], 6) == -0.001
