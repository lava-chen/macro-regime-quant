from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..models import SeriesSpec
from .base import DataProvider


class CsvProvider(DataProvider):
    """Read a frozen snapshot. Preferred baseline for reproducible research."""

    def __init__(self, root: str | Path = "data/raw") -> None:
        self.root = Path(root)

    def fetch(
        self,
        spec: SeriesSpec,
        start: str | None = None,
        end: str | None = None,
    ) -> pd.DataFrame:
        path = self.root / spec.symbol
        df = pd.read_csv(path)
        required = {"observation_date", "value"}
        if not required.issubset(df.columns):
            raise ValueError(f"{path} must contain {sorted(required)}")
        df["observation_date"] = pd.to_datetime(df["observation_date"])
        if start:
            df = df[df["observation_date"] >= pd.Timestamp(start)]
        if end:
            df = df[df["observation_date"] <= pd.Timestamp(end)]
        return df[["observation_date", "value"]].sort_values("observation_date")
