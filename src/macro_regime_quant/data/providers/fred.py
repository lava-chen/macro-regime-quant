from __future__ import annotations

from urllib.parse import quote

import pandas as pd

from ..models import SeriesSpec
from .base import DataProvider


class FredProvider(DataProvider):
    """Minimal FRED CSV adapter for public series.

    For real point-in-time research, move to ALFRED/vintage retrieval rather than
    treating the latest revised history as if it had been known in the past.
    """

    BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}"

    def fetch(
        self,
        spec: SeriesSpec,
        start: str | None = None,
        end: str | None = None,
    ) -> pd.DataFrame:
        url = self.BASE.format(quote(spec.symbol))
        df = pd.read_csv(url)
        if df.shape[1] != 2:
            raise ValueError(f"Unexpected FRED response for {spec.symbol}")
        df.columns = ["observation_date", "value"]
        df["observation_date"] = pd.to_datetime(df["observation_date"])
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df = df.dropna(subset=["value"])
        if start:
            df = df[df["observation_date"] >= pd.Timestamp(start)]
        if end:
            df = df[df["observation_date"] <= pd.Timestamp(end)]
        return df.reset_index(drop=True)
