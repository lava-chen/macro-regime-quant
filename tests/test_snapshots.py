from pathlib import Path

import pandas as pd
import pytest
import yaml

from macro_regime_quant.data.snapshots import validate_snapshot


def _write_snapshot(tmp_path: Path, include_available: bool = True) -> Path:
    csv_path = tmp_path / "series.csv"
    data = {
        "observation_date": ["2025-01-31", "2025-02-28"],
        "value": [5.0, 5.2],
    }
    if include_available:
        data["available_date"] = ["2025-02-15", "2025-03-15"]
    pd.DataFrame(data).to_csv(csv_path, index=False)

    metadata = {
        "series_key": "example",
        "source_name": "Official source",
        "source_url": "https://example.invalid/release",
        "frequency": "monthly",
        "unit": "percent",
        "downloaded_at": "2025-04-01T00:00:00Z",
    }
    csv_path.with_suffix(".meta.yaml").write_text(
        yaml.safe_dump(metadata),
        encoding="utf-8",
    )
    return csv_path


def test_point_in_time_snapshot_requires_available_date(tmp_path: Path):
    path = _write_snapshot(tmp_path)
    result = validate_snapshot(path)
    assert result.rows == 2
    assert result.has_available_date


def test_snapshot_rejects_missing_available_date_in_strict_mode(tmp_path: Path):
    path = _write_snapshot(tmp_path, include_available=False)
    with pytest.raises(ValueError, match="available_date"):
        validate_snapshot(path)
