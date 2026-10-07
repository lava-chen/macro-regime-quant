from __future__ import annotations

import pandas as pd

from ..models import SeriesSpec
from .base import DataProvider


class YahooProvider(DataProvider):
    """Convenience market-data adapter via yfinance.

    Good for prototyping, not the final source of truth for institutional-grade tests.
    """

    def fetch(
        self,
        spec: SeriesSpec,
        start: str | None = None,
        end: str | None = None,
    ) -> pd.DataFrame:
        try:
            import yfinance as yf
        except ImportError as exc:
            raise RuntimeError("Install with: pip install 'macro-regime-quant[data]'") from exc

        raw = yf.download(spec.symbol, start=start, end=end, auto_adjust=True, progress=False)
        if raw.empty:
            raise ValueError(f"No Yahoo data returned for {spec.symbol}")
        col = "Close"
        series = raw[col]
        if isinstance(series, pd.DataFrame):
            series = series.iloc[:, 0]
        return pd.DataFrame(
            {
                "observation_date": pd.to_datetime(series.index).tz_localize(None),
                "value": series.to_numpy(),
            }
        )
