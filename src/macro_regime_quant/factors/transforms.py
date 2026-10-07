from __future__ import annotations

import pandas as pd


def pct_change(series: pd.Series, periods: int = 1) -> pd.Series:
    """Percentage change without implicit filling."""

    return series.astype(float).pct_change(periods=periods, fill_method=None)


def yoy_change(series: pd.Series, periods_per_year: int = 12) -> pd.Series:
    """Year-over-year percentage change for regularly sampled data."""

    return pct_change(series, periods=periods_per_year)


def expanding_zscore(
    series: pd.Series,
    min_periods: int = 24,
    use_prior_history_only: bool = True,
) -> pd.Series:
    """Standardize a time series with expanding statistics only.

    With use_prior_history_only=True (default), the observation at t is
    normalized using mean/std estimated strictly through t-1.
    """

    x = series.astype(float)
    history = x.shift(1) if use_prior_history_only else x
    mean = history.expanding(min_periods=min_periods).mean()
    std = history.expanding(min_periods=min_periods).std(ddof=1)
    return ((x - mean) / std).where(std > 0)
