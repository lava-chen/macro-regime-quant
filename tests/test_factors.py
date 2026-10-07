import pandas as pd

from macro_regime_quant.factors.composite import build_composite_factor
from macro_regime_quant.factors.transforms import expanding_zscore


def test_expanding_zscore_does_not_change_when_future_is_appended():
    idx = pd.date_range("2020-01-31", periods=8, freq="ME")
    original = pd.Series([1, 2, 3, 4, 5, 6], index=idx[:6], dtype=float)
    extended = pd.Series([1, 2, 3, 4, 5, 6, 1000, -1000], index=idx, dtype=float)

    a = expanding_zscore(original, min_periods=3)
    b = expanding_zscore(extended, min_periods=3).iloc[:6]
    pd.testing.assert_series_equal(a, b, check_names=False)


def test_composite_requires_minimum_components_and_renormalizes_weights():
    idx = pd.date_range("2025-01-31", periods=2, freq="ME")
    components = pd.DataFrame(
        {"a": [1.0, 1.0], "b": [3.0, float("nan")], "c": [5.0, 5.0]}, index=idx
    )
    factor = build_composite_factor(
        components,
        weights={"a": 1.0, "b": 1.0, "c": 2.0},
        min_components=2,
    )

    assert factor.iloc[0] == 3.5
    assert factor.iloc[1] == 11.0 / 3.0
