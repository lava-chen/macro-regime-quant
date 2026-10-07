import pandas as pd

from macro_regime_quant.data.availability import attach_available_date


def test_release_lag_creates_later_information_date():
    df = pd.DataFrame({"observation_date": ["2025-01-31"], "value": [100.0]})
    out = attach_available_date(df, release_lag_days=14)
    assert out.loc[0, "available_date"] == pd.Timestamp("2025-02-14")
    assert out.loc[0, "availability_basis"] == "fixed_lag"
