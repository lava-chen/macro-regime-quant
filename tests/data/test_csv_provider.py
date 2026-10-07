from pathlib import Path

import pandas as pd
from mrq_core.types import SeriesSpec
from mrq_data.providers.csv import CsvProvider


def test_csv_provider_preserves_exact_available_date(tmp_path: Path):
    path = tmp_path / "china"
    path.mkdir()
    csv_path = path / "series.csv"
    pd.DataFrame(
        {
            "observation_date": ["2025-01-31", "2025-02-28"],
            "available_date": ["2025-02-15", "2025-03-15"],
            "value": [5.0, 5.2],
        }
    ).to_csv(csv_path, index=False)

    spec = SeriesSpec(
        key="cn_example",
        provider="csv",
        symbol="china/series.csv",
        kind="macro",
        frequency="monthly",
        release_lag_days=99,
    )
    out = CsvProvider(root=tmp_path).fetch(spec)

    assert "available_date" in out.columns
    assert out.loc[0, "available_date"] == pd.Timestamp("2025-02-15")
