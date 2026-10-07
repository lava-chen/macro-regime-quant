import pandas as pd

from macro_regime_quant.regimes import classify_regime


def test_four_regime_mapping_and_missing_values():
    idx = pd.date_range("2025-01-31", periods=5, freq="ME")
    growth = pd.Series([1.0, 1.0, -1.0, -1.0, float("nan")], index=idx)
    inflation = pd.Series([-1.0, 1.0, 1.0, -1.0, 0.0], index=idx)

    regime = classify_regime(growth, inflation)
    assert regime.tolist() == [
        "goldilocks",
        "reflation",
        "stagflation",
        "recession",
        pd.NA,
    ]
