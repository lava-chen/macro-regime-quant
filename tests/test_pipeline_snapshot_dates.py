from pathlib import Path

import pandas as pd

from macro_regime_quant.pipeline import load_monthly_panel


def test_monthly_panel_prefers_exact_available_date_over_fallback_lag(tmp_path: Path):
    raw = tmp_path / "raw"
    raw.mkdir()
    china = raw / "china"
    china.mkdir()

    pd.DataFrame(
        {
            "observation_date": ["2025-01-31", "2025-02-28"],
            "available_date": ["2025-02-15", "2025-03-15"],
            "value": [5.0, 5.2],
        }
    ).to_csv(china / "series.csv", index=False)

    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(
        """series:
  cn_example:
    provider: csv
    symbol: china/series.csv
    kind: macro
    frequency: monthly
    release_lag_days: 99
""",
        encoding="utf-8",
    )

    # CsvProvider defaults to data/raw, so run in a temporary cwd that mirrors the
    # repository layout expected by the provider.
    project = tmp_path / "project"
    project.mkdir()
    (project / "data").mkdir()
    (project / "data" / "raw").symlink_to(raw, target_is_directory=True)

    old_cwd = Path.cwd()
    try:
        import os

        os.chdir(project)
        panel = load_monthly_panel(
            catalog_path=catalog,
            keys=["cn_example"],
            start="2025-01-01",
            end="2025-03-31",
        )
    finally:
        os.chdir(old_cwd)

    # January observation is released on Feb 15, so it is not visible at Jan month-end.
    assert pd.isna(panel.loc[pd.Timestamp("2025-01-31"), "cn_example"])
    assert panel.loc[pd.Timestamp("2025-02-28"), "cn_example"] == 5.0
    assert panel.loc[pd.Timestamp("2025-03-31"), "cn_example"] == 5.2
