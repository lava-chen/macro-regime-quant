from __future__ import annotations

from collections.abc import Iterable

import pandas as pd


def forward_returns(
    prices: pd.DataFrame,
    horizons: Iterable[int] = (3, 6, 12),
) -> pd.DataFrame:
    """Compute simple forward returns from month-end price t to t+h.

    Output columns are a two-level MultiIndex: (asset, horizon_months).
    """

    prices = prices.sort_index().astype(float)
    blocks: dict[tuple[str, int], pd.Series] = {}

    for asset in prices.columns:
        for horizon in horizons:
            if horizon <= 0:
                raise ValueError("Forward-return horizons must be positive")
            blocks[(asset, int(horizon))] = prices[asset].shift(-horizon) / prices[asset] - 1.0

    out = pd.DataFrame(blocks, index=prices.index)
    out.columns = pd.MultiIndex.from_tuples(
        out.columns,
        names=["asset", "horizon_months"],
    )
    return out


def regime_return_summary(
    regimes: pd.Series,
    returns: pd.DataFrame,
) -> pd.DataFrame:
    """Summarize forward-return distributions conditional on the macro regime."""

    if not isinstance(returns.columns, pd.MultiIndex):
        raise TypeError("returns must have MultiIndex columns from forward_returns()")

    joined = returns.copy()
    joined["__regime__"] = regimes.reindex(joined.index)

    rows: list[dict[str, object]] = []
    for (asset, horizon), series in returns.items():
        frame = pd.DataFrame(
            {
                "regime": joined["__regime__"],
                "return": series,
            }
        ).dropna()

        for regime, group in frame.groupby("regime", observed=True):
            r = group["return"].astype(float)
            rows.append(
                {
                    "regime": str(regime),
                    "asset": str(asset),
                    "horizon_months": int(horizon),
                    "count": int(r.count()),
                    "mean": float(r.mean()),
                    "median": float(r.median()),
                    "std": float(r.std(ddof=1)),
                    "positive_rate": float((r > 0).mean()),
                }
            )

    return pd.DataFrame(rows).sort_values(
        ["horizon_months", "asset", "regime"]
    ).reset_index(drop=True)
