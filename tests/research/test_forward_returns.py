import pandas as pd
from mrq_research.forward_returns import (
    forward_returns,
    regime_return_summary,
)


def test_forward_returns_use_future_month_end_prices_only():
    idx = pd.date_range("2025-01-31", periods=5, freq="ME")
    prices = pd.DataFrame({"A": [100.0, 110.0, 121.0, 121.0, 133.1]}, index=idx)

    fwd = forward_returns(prices, horizons=(1, 3))

    assert round(fwd.loc[idx[0], ("A", 1)], 8) == 0.10
    assert round(fwd.loc[idx[0], ("A", 3)], 8) == 0.21
    assert pd.isna(fwd.loc[idx[-1], ("A", 1)])


def test_regime_summary_is_conditioned_on_state_at_start_date():
    idx = pd.date_range("2025-01-31", periods=5, freq="ME")
    prices = pd.DataFrame({"A": [100.0, 110.0, 121.0, 110.0, 121.0]}, index=idx)
    regimes = pd.Series(
        ["goldilocks", "goldilocks", "recession", "recession", "goldilocks"],
        index=idx,
    )

    fwd = forward_returns(prices, horizons=(1,))
    summary = regime_return_summary(regimes, fwd)

    gold = summary[
        (summary["regime"] == "goldilocks")
        & (summary["asset"] == "A")
        & (summary["horizon_months"] == 1)
    ].iloc[0]
    assert gold["count"] == 2
    assert round(gold["mean"], 8) == 0.10
