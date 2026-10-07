from __future__ import annotations

from pathlib import Path

import pandas as pd
from mrq_engines.pipeline import build_us_baseline, load_monthly_panel

from .forward_returns import forward_returns, regime_return_summary

DEFAULT_US_ASSETS = [
    "sp500_proxy",
    "nasdaq_proxy",
    "us_long_treasury_proxy",
    "gold_proxy",
    "commodity_proxy",
]


def analyze_us_regimes(
    catalog_path: str | Path = "config/data_catalog.yaml",
    factor_path: str | Path = "config/factors.yaml",
    start: str = "2000-01-01",
    end: str | None = None,
    min_z_history: int = 36,
    asset_keys: list[str] | None = None,
    horizons: tuple[int, ...] = (3, 6, 12),
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Build US factors and map regimes to future cross-asset returns.

    This remains a research baseline because the macro side still uses latest-vintage
    FRED history with approximate release lags.
    """

    end = end or pd.Timestamp.today().strftime("%Y-%m-%d")
    asset_keys = asset_keys or DEFAULT_US_ASSETS

    _, factors, regimes = build_us_baseline(
        catalog_path=catalog_path,
        factor_path=factor_path,
        start=start,
        end=end,
        min_z_history=min_z_history,
    )
    prices = load_monthly_panel(
        catalog_path=catalog_path,
        keys=asset_keys,
        start=start,
        end=end,
    )
    fwd = forward_returns(prices, horizons=horizons)
    summary = regime_return_summary(regimes, fwd)

    state = factors.copy()
    state["regime"] = regimes
    return state, prices, fwd, summary
