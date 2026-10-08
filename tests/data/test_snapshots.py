from pathlib import Path

import pandas as pd
import pytest
import yaml
from mrq_data.snapshots import validate_snapshot


def _write_snapshot(tmp_path: Path, include_available: bool = True) -> Path:
    csv_path = tmp_path / "series.csv"
    data = {
        "observation_date": ["2025-01-31", "2025-02-28"],
        "availability_basis": ["official_release", "official_release"],
        "availability_evidence_url": [
            "https://stats.gov.cn/release/january",
            "https://stats.gov.cn/release/february",
        ],
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
        "reported_as": "yoy_percent",
        "revision_policy": "frozen_release_snapshot",
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
    assert result.availability_basis_counts["official_release"] == 2


def test_monthly_period_release_may_precede_month_end_but_not_period_start(tmp_path: Path):
    path = _write_snapshot(tmp_path)
    frame = pd.read_csv(path)
    frame["observation_period"] = ["2025-01", "2025-02"]
    frame.loc[0, "available_date"] = "2025-01-27"
    frame.to_csv(path, index=False)

    result = validate_snapshot(path)
    assert result.rows == 2

    frame.loc[0, "available_date"] = "2024-12-31"
    frame.to_csv(path, index=False)
    with pytest.raises(ValueError, match="observation period start"):
        validate_snapshot(path)


def test_snapshot_requires_source_value_url_to_be_http(tmp_path: Path):
    path = _write_snapshot(tmp_path)
    frame = pd.read_csv(path)
    frame["source_value_url"] = "file:///tmp/source"
    frame.to_csv(path, index=False)
    with pytest.raises(ValueError, match=r"source_value_url must be an http\(s\) URL"):
        validate_snapshot(path)


def test_snapshot_rejects_missing_available_date_in_strict_mode(tmp_path: Path):
    path = _write_snapshot(tmp_path, include_available=False)
    with pytest.raises(ValueError, match="available_date"):
        validate_snapshot(path)


def test_unknown_release_dates_are_explicit_and_excluded_from_point_in_time_rows(
    tmp_path: Path,
):
    path = _write_snapshot(tmp_path)
    frame = pd.read_csv(path)
    frame["availability_basis"] = ["official_release", "unknown"]
    frame["availability_evidence_url"] = ["https://stats.gov.cn/release/january", ""]
    frame.loc[1, "available_date"] = ""
    frame.to_csv(path, index=False)

    mixed = validate_snapshot(path)
    assert mixed.availability_basis_counts["official_release"] == 1
    assert mixed.availability_basis_counts["unknown"] == 1
    assert mixed.has_available_date

    frame["availability_basis"] = "unknown"
    frame["availability_evidence_url"] = ""
    frame["available_date"] = ""
    frame.to_csv(path, index=False)
    result = validate_snapshot(path)
    assert result.availability_basis_counts["unknown"] == 2
    assert not result.has_available_date


def test_snapshot_rejects_a_date_labeled_unknown(tmp_path: Path):
    path = _write_snapshot(tmp_path)
    frame = pd.read_csv(path)
    frame["availability_basis"] = "unknown"
    frame["availability_evidence_url"] = ""
    frame.to_csv(path, index=False)

    with pytest.raises(ValueError, match="must not contain an available_date"):
        validate_snapshot(path)


def test_snapshot_rejects_release_date_without_evidence(tmp_path: Path):
    path = _write_snapshot(tmp_path)
    frame = pd.read_csv(path)
    frame["availability_evidence_url"] = ""
    frame.to_csv(path, index=False)

    with pytest.raises(ValueError, match="availability_evidence_url"):
        validate_snapshot(path)


def test_fixed_lag_snapshot_accepts_matching_lag_metadata(tmp_path: Path):
    path = _write_snapshot(tmp_path)
    frame = pd.read_csv(path)
    frame["availability_basis"] = "fixed_lag"
    frame["availability_evidence_url"] = ""
    frame["available_date"] = ["2025-02-14", "2025-03-14"]
    frame.to_csv(path, index=False)
    metadata_path = path.with_suffix(".meta.yaml")
    metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
    metadata["availability_lag_days"] = 14
    metadata_path.write_text(yaml.safe_dump(metadata), encoding="utf-8")

    result = validate_snapshot(path)
    assert result.availability_basis_counts["fixed_lag"] == 2
