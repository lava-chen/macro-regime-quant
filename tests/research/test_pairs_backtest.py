import numpy as np
import pandas as pd
import pytest
from mrq_engines.pairs import PairSignalSpec, PairSpec, SpreadSpec
from mrq_research.backtest.long_short import LongShortConfig, run_long_short_backtest
from mrq_research.pairs import backtest_pair, build_pair_instructions, combine_pair_returns


def _cointegrated_pair(periods: int = 900, seed: int = 17) -> pd.DataFrame:
    """Two prices on one random walk, so their log ratio wanders around zero.

    The daily volatility is deliberately realistic: a synthetic pair whose
    legs move 40% a day would be levered into a margin call by any honest
    accounting, which would say nothing about the strategy.
    """

    rng = np.random.default_rng(seed)
    index = pd.date_range("2016-01-01", periods=periods, freq="B")
    common = rng.normal(0.0, 0.010, periods).cumsum()
    a = 60.0 * np.exp(0.4 * common + rng.normal(0.0, 0.010, periods))
    b = 45.0 * np.exp(0.4 * common + rng.normal(0.0, 0.010, periods))
    return pd.DataFrame({"A": a, "B": b}, index=index)


def test_long_short_engine_matches_a_hand_calculated_ledger():
    index = pd.date_range("2025-01-01", periods=3, freq="B")
    prices = pd.DataFrame(
        {"A": [100.0, 110.0, 121.0], "B": [100.0, 90.0, 81.0]},
        index=index,
    )
    weights = pd.DataFrame({"A": [0.5], "B": [-0.5]}, index=[index[0]])

    result = run_long_short_backtest(
        prices,
        weights,
        LongShortConfig(transaction_cost_bps=10.0, slippage_bps=0.0, borrow_annual_bps=0.0),
    )

    # Day 0 is flat, day 1 pays the fill cost, day 2 earns the spread move.
    assert result.returns.iloc[0] == pytest.approx(0.0)
    assert result.returns.iloc[1] == pytest.approx(-0.001)
    assert result.returns.iloc[2] == pytest.approx(0.1)
    assert result.turnover.iloc[1] == pytest.approx(1.0)
    assert result.effective_weights.loc[index[1], "A"] == pytest.approx(0.0)
    assert result.ending_weights.loc[index[1], "A"] == pytest.approx(0.5)
    assert result.ending_weights.loc[index[1], "B"] == pytest.approx(-0.5)


def test_borrow_fee_is_charged_on_the_short_leg_only():
    index = pd.date_range("2025-01-01", periods=3, freq="B")
    prices = pd.DataFrame(
        {"A": [100.0, 100.0, 100.0], "B": [100.0, 100.0, 100.0]},
        index=index,
    )
    weights = pd.DataFrame({"A": [0.5], "B": [-0.5]}, index=[index[0]])
    config = LongShortConfig(
        transaction_cost_bps=0.0,
        slippage_bps=0.0,
        borrow_annual_bps=50.0,
        periods_per_year=252,
    )

    result = run_long_short_backtest(prices, weights, config)

    expected_fraction = 0.5 * 50.0 / 10_000.0 / 252
    assert result.borrow_costs.iloc[2] == pytest.approx(expected_fraction)
    assert result.returns.iloc[2] == pytest.approx(-expected_fraction)


def test_long_short_engine_refuses_leverage_and_missing_prices():
    index = pd.date_range("2025-01-01", periods=2, freq="B")
    prices = pd.DataFrame({"A": [100.0, 101.0], "B": [50.0, 51.0]}, index=index)
    overleveraged = pd.DataFrame({"A": [1.5], "B": [-1.5]}, index=[index[0]])
    with pytest.raises(ValueError, match="gross exposure"):
        run_long_short_backtest(prices, overleveraged)

    incomplete = pd.DataFrame(
        {"A": [100.0, float("nan")], "B": [50.0, 51.0]},
        index=index,
    )
    with pytest.raises(ValueError, match="complete and finite"):
        run_long_short_backtest(incomplete, pd.DataFrame({"A": [0.5], "B": [-0.5]}, index=[index[0]]))


def test_pair_backtest_trades_both_legs_and_stays_dollar_neutral():
    prices = _cointegrated_pair()
    spec = PairSpec(
        asset_a="A",
        asset_b="B",
        spread=SpreadSpec(lookback=60),
        signal=PairSignalSpec(entry_z=1.5, exit_z=0.5),
    )

    backtest = backtest_pair(prices, spec, LongShortConfig())

    assert not backtest.instructions.empty
    assert list(backtest.instructions.columns) == ["A", "B"]
    # One leg is long and the other short, and the pair is sized to one unit
    # of gross exposure.
    entry_rows = backtest.instructions[backtest.instructions["A"] != 0.0]
    assert (entry_rows["A"] * entry_rows["B"] <= 0).all()
    assert entry_rows.abs().sum(axis=1).max() == pytest.approx(1.0)
    assert np.isfinite(backtest.result.returns).all()
    # Between fills the two legs drift apart, so the net exposure wanders a
    # little around its target instead of staying pinned to it.
    assert abs(backtest.result.metrics["average_net_exposure"]) < 0.05


def test_pair_backtest_does_not_trade_before_the_lookback_window_is_warm():
    prices = _cointegrated_pair()
    spec = PairSpec(
        asset_a="A",
        asset_b="B",
        spread=SpreadSpec(lookback=120),
        signal=PairSignalSpec(entry_z=1.5, exit_z=0.5),
    )

    instructions, frame = build_pair_instructions(prices, spec)

    assert instructions.index.min() > prices.index[120]
    assert frame["zscore"].iloc[:120].isna().all()


def test_pair_instructions_ignore_prices_after_the_decision_date():
    prices = _cointegrated_pair()
    spec = PairSpec(
        asset_a="A",
        asset_b="B",
        spread=SpreadSpec(lookback=60),
        signal=PairSignalSpec(entry_z=1.5, exit_z=0.5),
    )
    original, _ = build_pair_instructions(prices, spec)

    cut = prices.index[400]
    shocked = prices.copy()
    shocked.loc[shocked.index >= cut, "B"] = shocked.loc[shocked.index >= cut, "B"] * 1.8
    changed, _ = build_pair_instructions(shocked, spec)

    before = original.index[original.index < cut]
    pd.testing.assert_frame_equal(original.loc[before], changed.loc[before])


def test_pair_backtest_reports_the_spread_evidence_it_ran_on():
    prices = _cointegrated_pair()
    spec = PairSpec(
        asset_a="A",
        asset_b="B",
        spread=SpreadSpec(lookback=60),
        signal=PairSignalSpec(entry_z=1.5, exit_z=0.5),
    )

    backtest = backtest_pair(prices, spec)

    diagnostics = backtest.diagnostics
    assert diagnostics.observations == len(prices)
    assert diagnostics.cointegration_statistic < 0
    assert not diagnostics.reversion.empty
    assert backtest.frame["zscore"].notna().any()


def test_combining_pairs_weights_them_equally_and_reports_coverage():
    index = pd.date_range("2025-01-01", periods=5, freq="B")
    first = pd.Series([0.01, 0.02, -0.01, 0.0, 0.03], index=index)
    second = pd.Series([-0.01, 0.0, 0.01, 0.0, -0.03], index=index)
    # The second pair only starts later, so early days have one live pair.
    second = second.copy()
    second.iloc[:2] = float("nan")

    portfolio, metrics = combine_pair_returns({"A/B": first, "C/D": second})

    assert portfolio.iloc[0] == pytest.approx(0.01)
    assert portfolio.iloc[2] == pytest.approx(0.0)
    assert metrics["pairs_in_book"] == pytest.approx(2.0)
    assert np.isfinite(metrics["sharpe"])


def test_combination_counts_the_pairs_that_are_holding_not_the_ones_returning():
    index = pd.date_range("2025-01-01", periods=4, freq="B")
    first = pd.Series([0.01, 0.0, 0.0, -0.01], index=index)
    second = pd.Series([0.0, 0.0, 0.0, 0.0], index=index)
    positions = {
        "A/B": pd.Series([1.0, 0.0, 0.0, -1.0], index=index),
        "C/D": pd.Series([0.0, 0.0, 0.0, 0.0], index=index),
    }

    _, metrics = combine_pair_returns({"A/B": first, "C/D": second}, positions=positions)

    # A flat pair still has a return of 0.0, so counting returns would report
    # both pairs as active on every day.
    assert metrics["average_holding_pairs"] == pytest.approx(0.5)
    assert metrics["maximum_holding_pairs"] == pytest.approx(1.0)
    assert metrics["pairs_in_book"] == pytest.approx(2.0)
