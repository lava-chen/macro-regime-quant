from __future__ import annotations

import pandas as pd
from mrq_core.types import SeriesSpec

from .base import DataProvider


class YahooProvider(DataProvider):
    """Convenience market-data adapter via yfinance.

    Good for prototyping, not the final source of truth for institutional-grade
    tests: these are adjusted closes with no vintage, and a corporate action
    known only later is baked into the whole history.
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
            raise RuntimeError(
                "Install with: uv sync --all-packages --extra data"
            ) from exc

        raw = yf.download(spec.symbol, start=start, end=end, auto_adjust=True, progress=False)
        if raw.empty:
            # yfinance returns an empty frame for several very different reasons,
            # and the distinction decides what the caller should do next.
            raise ValueError(
                f"No data returned for {spec.symbol}. If the symbol is real, the most "
                "likely cause is yfinance rate limiting (yfinance raises "
                "YFRateLimitError and returns an empty frame). Retry later, or point "
                "the series at a CSV snapshot."
            )

        col = "Close"
        if isinstance(raw.columns, pd.MultiIndex):
            # yfinance >= 1.x returns (field, ticker) columns even for one symbol.
            if col not in raw.columns.get_level_values(0):
                raise ValueError(
                    f"Unexpected yfinance columns for {spec.symbol}: {list(raw.columns)[:6]}"
                )
            series = raw[col].iloc[:, 0]
        else:
            series = raw[col]

        return pd.DataFrame(
            {
                "observation_date": pd.to_datetime(series.index).tz_localize(None),
                "value": series.to_numpy(),
            }
        )
