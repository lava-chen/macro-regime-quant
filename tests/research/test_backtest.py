import pandas as pd
from mrq_research.backtest.engine import BacktestConfig, run_backtest


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


def test_holdings_drift_between_sparse_rebalance_instructions():
    idx = pd.date_range("2025-01-01", periods=3, freq="D")
    prices = pd.DataFrame(
        {"A": [100.0, 200.0, 200.0], "B": [100.0, 100.0, 100.0]}, index=idx
    )
    weights = pd.DataFrame({"A": [0.5], "B": [0.5]}, index=[idx[0]])

    result = run_backtest(
        prices,
        weights,
        BacktestConfig(transaction_cost_bps=0.0, execution_lag_periods=1),
    )

    assert result.returns.loc[idx[1]] == 0.5
    assert result.effective_weights.loc[idx[1], "A"] == 0.5
    assert round(result.effective_weights.loc[idx[2], "A"], 8) == round(2 / 3, 8)
    assert round(result.effective_weights.loc[idx[2], "B"], 8) == round(1 / 3, 8)
    assert round(result.ending_weights.loc[idx[1], "A"], 8) == round(2 / 3, 8)


def test_rebalance_turnover_uses_drifted_current_weights():
    idx = pd.date_range("2025-01-01", periods=3, freq="D")
    prices = pd.DataFrame(
        {"A": [100.0, 200.0, 200.0], "B": [100.0, 100.0, 100.0]}, index=idx
    )
    weights = pd.DataFrame(
        {"A": [0.5, 0.5], "B": [0.5, 0.5]}, index=[idx[0], idx[1]]
    )

    result = run_backtest(
        prices,
        weights,
        BacktestConfig(transaction_cost_bps=10.0, execution_lag_periods=1),
    )

    assert round(result.turnover.loc[idx[2]], 8) == round(1 / 3, 8)
    assert round(result.returns.loc[idx[2]], 8) == round(-(1 / 3) * 10 / 10_000, 8)


def test_invalid_prices_and_unaligned_signals_fail_loudly():
    idx = pd.date_range("2025-01-01", periods=2, freq="D")
    weights = pd.DataFrame({"A": [1.0]}, index=[idx[0]])
    incomplete = pd.DataFrame({"A": [100.0, float("nan")]}, index=idx)
    try:
        run_backtest(incomplete, weights)
    except ValueError as exc:
        assert "complete and finite" in str(exc)
    else:
        raise AssertionError("incomplete price rows must not be silently filled")

    prices = pd.DataFrame({"A": [100.0, 101.0]}, index=idx)
    weekend_signal = pd.DataFrame({"A": [1.0]}, index=[pd.Timestamp("2025-01-05")])
    try:
        run_backtest(prices, weekend_signal)
    except ValueError as exc:
        assert "align with the price calendar" in str(exc)
    else:
        raise AssertionError("off-calendar signals must not be silently dropped")


def test_metrics_annualize_only_observed_price_intervals():
    idx = pd.date_range("2025-01-01", periods=2, freq="D")
    prices = pd.DataFrame({"A": [100.0, 110.0]}, index=idx)
    weights = pd.DataFrame({"A": [1.0]}, index=[idx[0]])

    result = run_backtest(
        prices,
        weights,
        BacktestConfig(transaction_cost_bps=0.0, execution_lag_periods=1),
    )

    expected = 1.1**252 - 1
    assert abs(result.metrics["cagr"] - expected) / expected < 1e-12
