import pandas as pd
from mrq_data.monthly import monthly_asof


def test_monthly_asof_uses_only_released_values():
    frame = pd.DataFrame(
        {
            "available_date": ["2025-01-15", "2025-02-14", "2025-03-14"],
            "value": [100.0, 110.0, 120.0],
        }
    )

    out = monthly_asof(frame, start="2025-01-01", end="2025-03-31")
    assert out.loc[pd.Timestamp("2025-01-31")] == 100.0
    assert out.loc[pd.Timestamp("2025-02-28")] == 110.0
    assert out.loc[pd.Timestamp("2025-03-31")] == 120.0
