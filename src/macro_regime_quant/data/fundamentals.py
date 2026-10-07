from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class FundamentalObservation:
    """One point-in-time company fundamental observation.

    period_end is the accounting period the value describes.
    available_date is when an investor could have known the value.
    """

    company_id: str
    metric: str
    period_end: pd.Timestamp
    available_date: pd.Timestamp
    value: float
    unit: str
    source: str

    def __post_init__(self) -> None:
        if self.available_date < self.period_end:
            raise ValueError("available_date cannot be earlier than period_end")
        if not self.company_id or not self.metric or not self.source:
            raise ValueError("company_id, metric and source are required")


def fundamentals_asof(frame: pd.DataFrame, as_of: str | pd.Timestamp) -> pd.DataFrame:
    """Select the latest known observation for each company/metric at an as-of date."""

    required = {
        "company_id",
        "metric",
        "period_end",
        "available_date",
        "value",
    }
    if not required.issubset(frame.columns):
        raise ValueError(f"fundamental frame must contain {sorted(required)}")

    data = frame.copy()
    data["period_end"] = pd.to_datetime(data["period_end"])
    data["available_date"] = pd.to_datetime(data["available_date"])
    cutoff = pd.Timestamp(as_of)

    known = data[data["available_date"] <= cutoff]
    if known.empty:
        return known

    known = known.sort_values(
        ["company_id", "metric", "period_end", "available_date"]
    )
    return (
        known.groupby(["company_id", "metric"], as_index=False, sort=False)
        .tail(1)
        .reset_index(drop=True)
    )
