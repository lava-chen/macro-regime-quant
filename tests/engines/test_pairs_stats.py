import numpy as np
import pandas as pd
import pytest
from mrq_engines.pairs import (
    PairSignalSpec,
    SpreadSpec,
    align_pair_prices,
    build_spread,
    cointegration_test,
    half_life,
    rolling_hedge_ratio,
    rolling_zscore,
    zscore_state_machine,
)


def _geometric_pair(periods: int = 600, seed: int = 7) -> pd.DataFrame:
    """Two prices that share one random walk: their log ratio is stationary."""

    rng = np.random.default_rng(seed)
    index = pd.date_range("2018-01-01", periods=periods, freq="B")
    common = rng.normal(0.0, 0.010, periods).cumsum()
    a = 100.0 * np.exp(0.5 * common + rng.normal(0.0, 0.010, periods))
    b = 80.0 * np.exp(0.5 * common + rng.normal(0.0, 0.010, periods))
    return pd.DataFrame({"A": a, "B": b}, index=index)


def test_hedge_ratio_uses_only_earlier_data():
    prices = _geometric_pair()
    log_a = np.log(prices["A"])
    log_b = np.log(prices["B"])
    estimates = rolling_hedge_ratio(log_a, log_b, lookback=60)

    last_date = prices.index[-1]
    window = slice(-61, -1)
    manual_beta = np.polyfit(log_b.iloc[window], log_a.iloc[window], 1)[0]

    assert estimates.at[last_date, "beta"] == pytest.approx(manual_beta, rel=1e-6)


def test_spread_is_stationary_for_a_cointegrated_pair():
    prices = _geometric_pair()
    frame = build_spread(prices, SpreadSpec(lookback=60))
    spread = frame["spread"].dropna()

    assert len(spread) > 400
    # A random walk of the same length would drift far beyond this band.
    assert spread.std() < 0.2
    assert abs(spread.iloc[-1]) < 5 * spread.std()


def test_align_pair_prices_drops_missing_rows_without_filling():
    prices = pd.DataFrame(
        {"A": [100.0, 101.0, 102.0], "B": [50.0, float("nan"), 52.0]},
        index=pd.date_range("2025-01-01", periods=3, freq="B"),
    )
    aligned = align_pair_prices(prices)

    assert list(aligned.index) == [pd.Timestamp("2025-01-01"), pd.Timestamp("2025-01-03")]
    assert aligned["B"].notna().all()


def test_align_pair_prices_rejects_non_positive_prices():
    prices = pd.DataFrame(
        {"A": [100.0, 0.0], "B": [50.0, 51.0]},
        index=pd.date_range("2025-01-01", periods=2, freq="B"),
    )
    with pytest.raises(ValueError, match="positive"):
        align_pair_prices(prices)


def test_zscore_uses_a_trailing_window_only():
    series = pd.Series(np.arange(1.0, 21.0), index=pd.date_range("2025-01-01", periods=20, freq="B"))
    z = rolling_zscore(series, window=10)

    expected = (series.iloc[-1] - series.iloc[-10:].mean()) / series.iloc[-10:].std(ddof=1)
    assert z.iloc[-1] == pytest.approx(expected)


def test_state_machine_enters_and_exits_on_thresholds():
    z = pd.Series(
        [0.0, 2.5, 2.6, 0.2, 0.0, -2.5, -2.4, -0.1],
        index=pd.date_range("2025-01-01", periods=8, freq="B"),
    )
    states = zscore_state_machine(z, PairSignalSpec(entry_z=2.0, exit_z=0.5))

    # A high spread is shorted; a low one is bought.
    assert states["position"].tolist() == [0.0, -1.0, -1.0, 0.0, 0.0, 1.0, 1.0, 0.0]
    assert states["entry"].tolist() == [False, True, False, False, False, True, False, False]
    assert states["exit"].tolist() == [False, False, False, True, False, False, False, True]


def test_state_machine_respects_the_stop_and_holding_limits():
    z = pd.Series(
        [2.5, 3.5, 3.6],
        index=pd.date_range("2025-01-01", periods=3, freq="B"),
    )
    stopped = zscore_state_machine(z, PairSignalSpec(entry_z=2.0, exit_z=0.5, stop_z=3.0))
    assert stopped["position"].tolist() == [-1.0, 0.0, 0.0]

    timed = zscore_state_machine(
        z,
        PairSignalSpec(entry_z=2.0, exit_z=0.5, max_holding_days=1),
    )
    assert timed["position"].tolist() == [-1.0, 0.0, 0.0]


def test_cointegration_test_separates_a_random_walk_from_a_stationary_pair():
    rng = np.random.default_rng(11)
    index = pd.date_range("2018-01-01", periods=800, freq="B")
    walk_a = pd.Series(100.0 * np.exp(rng.normal(0.0, 0.01, 800).cumsum()), index=index)
    walk_b = pd.Series(80.0 * np.exp(rng.normal(0.0, 0.01, 800).cumsum()), index=index)
    unrelated = cointegration_test(walk_a, walk_b)
    assert not unrelated.rejects_unit_root_5pct

    prices = _geometric_pair(periods=800, seed=3)
    related = cointegration_test(np.log(prices["A"]), np.log(prices["B"]))
    assert related.rejects_unit_root_5pct


def test_half_life_recovers_the_ou_reversion_speed():
    index = pd.date_range("2025-01-01", periods=4000, freq="B")
    rng = np.random.default_rng(5)
    kappa = 0.15
    values = np.zeros(len(index))
    shocks = rng.normal(0.0, 0.5, len(index))
    for position in range(1, len(index)):
        values[position] = values[position - 1] - kappa * values[position - 1] + shocks[position]
    spread = pd.Series(values, index=index)

    estimated = half_life(spread)
    assert estimated == pytest.approx(np.log(2) / kappa, rel=0.25)
