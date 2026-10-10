import pandas as pd
import pytest
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

    # The order fills at the day-3 close. The day-2 to day-3 price move
    # belongs to the old (cash) holdings; the new holding starts next interval.
    assert result.effective_weights.loc[idx[2], "A"] == 0.0
    assert result.ending_weights.loc[idx[2], "A"] == 1.0
    assert result.effective_weights.loc[idx[3], "A"] == 1.0
    assert result.returns.loc[idx[2]] == 0.0
    assert round(result.returns.loc[idx[3]], 10) == 0.1


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


def test_immediate_entry_fee_is_counted_from_initial_account_value():
    idx = pd.date_range("2025-01-01", periods=2, freq="D")
    prices = pd.DataFrame({"A": [100.0, 100.0]}, index=idx)
    weights = pd.DataFrame({"A": [1.0]}, index=[idx[0]])

    result = run_backtest(
        prices,
        weights,
        BacktestConfig(transaction_cost_bps=100.0, execution_lag_periods=0),
    )

    assert result.returns.iloc[0] == pytest.approx(-0.01)
    assert result.metrics["max_drawdown"] == pytest.approx(-0.01)


def test_two_asset_accounting_matches_hand_calculation_and_rebalance_cost():
    idx = pd.date_range("2025-01-01", periods=4, freq="D")
    prices = pd.DataFrame(
        {"A": [100.0, 200.0, 220.0, 220.0], "B": [100.0, 100.0, 110.0, 220.0]}, index=idx
    )
    weights = pd.DataFrame(
        {"A": [0.5, 0.5], "B": [0.5, 0.5]}, index=[idx[0], idx[2]]
    )

    result = run_backtest(
        prices,
        weights,
        BacktestConfig(transaction_cost_bps=100.0, execution_lag_periods=1),
    )

    # Independent ledger: cash through day 1, invest 50/50 at that close,
    # earn 10% on day 2, then earn 50% on day 3. At the day-3 close the
    # drifted 1/3-vs-2/3 account is reset and pays 1% on 1/3 turnover.
    expected_d3 = 1.5 * (1.0 - (1.0 / 3.0) * 0.01) - 1.0
    assert result.returns.iloc[:3].tolist() == pytest.approx([0.0, -0.01, 0.1])
    assert result.returns.iloc[3] == pytest.approx(expected_d3)
    assert result.turnover.loc[idx[3]] == pytest.approx(1 / 3)
    assert result.ending_weights.loc[idx[3], "A"] == pytest.approx(0.5)
    assert result.ending_weights.loc[idx[3], "B"] == pytest.approx(0.5)
    assert (1 + result.returns).prod() == pytest.approx(0.99 * 1.1 * (1 + expected_d3))


def test_rebalance_turnover_uses_drifted_current_weights():
    idx = pd.date_range("2025-01-01", periods=4, freq="D")
    prices = pd.DataFrame(
        {"A": [100.0, 100.0, 200.0, 200.0], "B": [100.0, 100.0, 100.0, 100.0]}, index=idx
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
    assert round(result.returns.loc[idx[2]], 8) == round(1.5 * (1 - (1 / 3) * 10 / 10_000) - 1, 8)


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

    extra_asset = pd.DataFrame({"A": [0.5], "MISSING": [0.5]}, index=[idx[0]])
    try:
        run_backtest(prices, extra_asset)
    except ValueError as exc:
        assert "assets with no price series" in str(exc)
    else:
        raise AssertionError("target assets without prices must not be silently discarded")


def test_metrics_annualize_only_observed_price_intervals():
    idx = pd.date_range("2025-01-01", periods=3, freq="D")
    prices = pd.DataFrame({"A": [100.0, 110.0, 121.0]}, index=idx)
    weights = pd.DataFrame({"A": [1.0]}, index=[idx[0]])

    result = run_backtest(
        prices,
        weights,
        BacktestConfig(transaction_cost_bps=0.0, execution_lag_periods=1),
    )

    expected = 1.1 ** (252 / 2) - 1
    assert abs(result.metrics["cagr"] - expected) / expected < 1e-12
