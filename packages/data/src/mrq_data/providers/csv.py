from __future__ import annotations

from pathlib import Path

import pandas as pd
from mrq_core.types import SeriesSpec

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
        columns = ["observation_date", "value"]

        if "observation_period" in df.columns:
            columns.append("observation_period")

        has_available_date = "available_date" in df.columns
        if has_available_date:
            df["available_date"] = pd.to_datetime(df["available_date"], errors="coerce")
            columns.append("available_date")

        if "availability_basis" not in df.columns:
            if has_available_date:
                df["availability_basis"] = "unknown"
                has_date = df["available_date"].notna()
                df.loc[has_date, "availability_basis"] = "unverified"
            else:
                df["availability_basis"] = "unknown"
        columns.append("availability_basis")

        if "availability_evidence_url" in df.columns:
            columns.append("availability_evidence_url")

        if "source_value_url" in df.columns:
            columns.append("source_value_url")

        if start:
            df = df[df["observation_date"] >= pd.Timestamp(start)]
        if end:
            df = df[df["observation_date"] <= pd.Timestamp(end)]

        return df[columns].sort_values("observation_date")
