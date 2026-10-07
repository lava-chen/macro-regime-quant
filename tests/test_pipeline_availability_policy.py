import os
from pathlib import Path

import pandas as pd
import yaml

from macro_regime_quant.pipeline import load_monthly_panel


def test_china_panel_can_include_only_officially_confirmed_release_dates(
    tmp_path: Path,
):
    raw = tmp_path / "raw"
    raw.mkdir()
    china = raw / "china"
    china.mkdir()
    pd.DataFrame(
        {
            "observation_date": ["2025-01-31", "2025-02-28", "2025-03-31"],
            "available_date": ["2025-02-15", "2025-03-15", ""],
            "availability_basis": ["official_release", "fixed_lag", "unknown"],
            "availability_evidence_url": [
                "https://stats.gov.cn/release/january",
                "",
                "",
            ],
            "value": [5.0, 9.0, 99.0],
        }
    ).to_csv(china / "series.csv", index=False)
    (china / "series.meta.yaml").write_text(
        yaml.safe_dump(
            {
                "series_key": "cn_example",
                "source_name": "National Bureau of Statistics of China",
                "source_url": "https://www.stats.gov.cn/sj/zxfb/",
                "frequency": "monthly",
                "unit": "percent_yoy",
                "downloaded_at": "2025-04-01T00:00:00Z",
                "reported_as": "yoy_percent",
                "revision_policy": "frozen_release_snapshot",
                "availability_lag_days": 15,
            }
        ),
        encoding="utf-8",
    )

    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(
        """series:
  cn_example:
    provider: csv
    symbol: china/series.csv
    kind: macro
    frequency: monthly
    release_lag_days: 18
""",
        encoding="utf-8",
    )
    project = tmp_path / "project"
    project.mkdir()
    (project / "data").mkdir()
    (project / "data" / "raw").symlink_to(raw, target_is_directory=True)

    old_cwd = Path.cwd()
    try:
        os.chdir(project)
        confirmed = load_monthly_panel(
            catalog_path=catalog,
            keys=["cn_example"],
            start="2025-01-01",
            end="2025-03-31",
            availability_policy="official_release_only",
        )
        estimated = load_monthly_panel(
            catalog_path=catalog,
            keys=["cn_example"],
            start="2025-01-01",
            end="2025-03-31",
            availability_policy="include_estimates",
        )
    finally:
        os.chdir(old_cwd)

    jan, feb, mar = pd.date_range("2025-01-31", periods=3, freq="ME")
    assert pd.isna(confirmed.loc[jan, "cn_example"])
    assert confirmed.loc[feb, "cn_example"] == 5.0
    assert confirmed.loc[mar, "cn_example"] == 5.0
    assert estimated.loc[mar, "cn_example"] == 9.0
