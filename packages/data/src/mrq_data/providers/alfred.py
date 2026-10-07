from __future__ import annotations

import pandas as pd
from mrq_core.contracts import normalize_observation_frame
from mrq_core.types import SeriesSpec

from .base import DataProvider

#: ALFRED lives on its own host. The graph endpoint on fred.stlouisfed.org
#: accepts a `vintage_date` parameter and then silently ignores it, which yields
#: the latest revised history — precisely the look-ahead this project exists to
#: prevent. Verified: the same request that returns 2026 rows with
#: vintage_date=2005-01-01 on fred.stlouisfed.org returns 2004 rows on alfred.
ALFRED_BASE = "https://alfred.stlouisfed.org/graph/alfredgraph.csv?id={symbol}&vintage_date={vintage}"


class AlfredProvider(DataProvider):
    """Point-in-time macro adapter backed by ALFRED vintage data.

    A vintage requested for date ``V`` returns, for every observation period, the
    value as it stood on ``V`` — the number a researcher could actually have seen
    then. Macro series get revised, and the revisions are large enough to matter:
    CPIAUCSL for 2020-01 reads 258.820 in the 2020-06 vintage and 259.127 in the
    2026-06 one.

    **What this provider can and cannot claim.** It knows the *upper bound* on
    when a value could have been known: it was published on or before ``V``. It
    does not know the actual publication date, so it cannot label a row
    ``official_release``. Rows are labelled ``fixed_lag`` and dated at
    ``min(observation_date + release_lag_days, V)`` — the catalogue's lag
    estimate, clamped so it can never point past the vintage. That clamp is what
    makes the result safe: it can lose information by being too late, never by
    being too early.

    Use this provider for anything a backtest reads. :class:`FredProvider` is
    fine for exploration and for series that are never revised.
    """

    def fetch(
        self,
        spec: SeriesSpec,
        start: str | None = None,
        end: str | None = None,
        vintage_date: str | pd.Timestamp | None = None,
    ) -> pd.DataFrame:
        """Fetch the vintage of ``spec`` as it stood on ``vintage_date``.

        ``vintage_date`` defaults to today, which is the latest available vintage
        and is therefore equivalent to the plain FRED series. Pass an explicit
        date when reconstructing the information set of a past moment.
        """

        vintage = pd.Timestamp(vintage_date).normalize() if vintage_date else pd.Timestamp.now().normalize()
        url = ALFRED_BASE.format(symbol=spec.symbol, vintage=vintage.date().isoformat())

        raw = pd.read_csv(url)
        if raw.shape[1] != 2:
            raise ValueError(f"Unexpected ALFRED response for {spec.symbol} at {vintage.date()}")

        raw.columns = ["observation_date", "value"]
        raw["observation_date"] = pd.to_datetime(raw["observation_date"])
        raw["value"] = pd.to_numeric(raw["value"], errors="coerce")
        raw = raw.dropna(subset=["value"])

        if start:
            raw = raw[raw["observation_date"] >= pd.Timestamp(start)]
        if end:
            raw = raw[raw["observation_date"] <= pd.Timestamp(end)]

        if raw.empty:
            return pd.DataFrame(
                {
                    "observation_date": pd.Series(dtype="datetime64[ns]"),
                    "value": pd.Series(dtype=float),
                    "available_date": pd.Series(dtype="datetime64[ns]"),
                    "availability_basis": pd.Series(dtype="object"),
                    "vintage_date": pd.Series(dtype="datetime64[ns]"),
                }
            )

        # Latest a row could possibly have been knowable, and the hard ceiling
        # the vintage itself imposes. min() can only ever delay.
        estimated = raw["observation_date"] + pd.to_timedelta(spec.release_lag_days, unit="D")
        raw["available_date"] = estimated.clip(upper=vintage)
        raw["availability_basis"] = "fixed_lag"
        raw["vintage_date"] = vintage

        frame = normalize_observation_frame(raw, key=spec.key)
        return frame


def latest_vintage_date(series_key: str, frames: list[pd.DataFrame]) -> pd.Timestamp | None:
    """Earliest-known vintage across fetched frames — the honest start of a backtest.

    A backtest cannot begin before the first vintage you actually hold: earlier
    history simply was not available to you, no matter what the release calendar
    says. Reading this off the frames keeps the answer tied to the data in hand
    rather than to an assumption.
    """

    dates: list[pd.Timestamp] = []
    for frame in frames:
        if "vintage_date" in frame.columns and not frame.empty:
            dates.extend(pd.to_datetime(frame["vintage_date"], errors="coerce").dropna().tolist())
    if not dates:
        return None
    return min(dates)
