from __future__ import annotations

import pandas as pd


def monthly_asof(
    frame: pd.DataFrame,
    start: str | None = None,
    end: str | None = None,
) -> pd.Series:
    """Return the latest value actually available at each calendar month-end.

    Required columns: available_date, value.
    This is the canonical bridge from mixed-frequency releases to the monthly model.
    """

    required = {"available_date", "value"}
    if not required.issubset(frame.columns):
        raise ValueError(f"frame must contain {sorted(required)}")

    data = frame.loc[:, ["available_date", "value"]].copy()
    data["available_date"] = pd.to_datetime(data["available_date"])
    data["value"] = pd.to_numeric(data["value"], errors="coerce")
    data = data.dropna().sort_values("available_date")
    data = data.drop_duplicates("available_date", keep="last")

    if data.empty:
        return pd.Series(dtype=float, name="value")

    first = pd.Timestamp(start) if start else data["available_date"].min()
    last = pd.Timestamp(end) if end else data["available_date"].max()
    month_ends = pd.date_range(first, last, freq="ME")

    source = data.set_index("available_date")["value"]
    union = source.index.union(month_ends).sort_values()
    out = source.reindex(union).ffill().reindex(month_ends)
    out.index.name = "date"
    out.name = "value"
    return out.astype(float)
