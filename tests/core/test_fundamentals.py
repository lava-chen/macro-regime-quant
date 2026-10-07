import pandas as pd
from mrq_core.asof import fundamentals_asof


def test_fundamentals_asof_uses_latest_value_known_by_cutoff():
    frame = pd.DataFrame(
        {
            "company_id": ["US:X", "US:X", "US:X"],
            "metric": ["revenue", "revenue", "revenue"],
            "period_end": ["2024-12-31", "2025-03-31", "2025-06-30"],
            "available_date": ["2025-02-15", "2025-05-10", "2025-08-10"],
            "value": [100.0, 110.0, 120.0],
        }
    )

    result = fundamentals_asof(frame, "2025-06-30")
    assert len(result) == 1
    assert result.iloc[0]["value"] == 110.0
