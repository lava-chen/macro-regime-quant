from __future__ import annotations

from collections.abc import Mapping

import pandas as pd


def build_composite_factor(
    components: pd.DataFrame,
    weights: Mapping[str, float] | None = None,
    min_components: int | None = None,
) -> pd.Series:
    """Create a weighted composite from already standardized components."""

    if components.empty:
        return pd.Series(dtype=float, name="factor")

    if weights is None:
        weights = {column: 1.0 for column in components.columns}

    unknown = set(weights) - set(components.columns)
    if unknown:
        raise KeyError(f"Weights reference unknown components: {sorted(unknown)}")

    weight_series = pd.Series(weights, dtype=float).reindex(components.columns).fillna(0.0)
    if (weight_series < 0).any():
        raise ValueError("v1 composite factors require non-negative weights")
    if weight_series.sum() <= 0:
        raise ValueError("At least one positive component weight is required")

    available = components.notna()
    effective_weights = available.mul(weight_series, axis=1)
    denom = effective_weights.sum(axis=1).replace(0.0, pd.NA)
    weighted_sum = components.fillna(0.0).mul(weight_series, axis=1).sum(axis=1)
    factor = weighted_sum / denom

    required = min_components if min_components is not None else len(components.columns)
    factor = factor.where(available.sum(axis=1) >= required)
    factor.name = "factor"
    return factor.astype(float)
