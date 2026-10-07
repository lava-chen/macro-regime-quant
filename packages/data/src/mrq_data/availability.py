from __future__ import annotations

import pandas as pd


def attach_available_date(
    frame: pd.DataFrame,
    release_lag_days: int,
    observation_col: str = "observation_date",
    availability_basis: str = "fixed_lag",
) -> pd.DataFrame:
    """Attach a transparently labeled availability-date estimate.

    This is deliberately explicit. For serious macro backtests, replace fixed lags
    with actual publication calendars or vintage databases (e.g. ALFRED).
    """

    out = frame.copy()
    out[observation_col] = pd.to_datetime(out[observation_col])
    out["available_date"] = out[observation_col] + pd.to_timedelta(release_lag_days, unit="D")
    out["availability_basis"] = availability_basis
    return out
