from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml

REQUIRED_METADATA = {
    "series_key",
    "source_name",
    "source_url",
    "frequency",
    "unit",
    "downloaded_at",
}


@dataclass(frozen=True)
class SnapshotValidation:
    rows: int
    first_observation: pd.Timestamp
    last_observation: pd.Timestamp
    has_available_date: bool
    metadata: dict[str, object]


def metadata_path(csv_path: str | Path) -> Path:
    path = Path(csv_path)
    return path.with_suffix(".meta.yaml")


def validate_snapshot(
    csv_path: str | Path,
    *,
    require_available_date: bool = True,
) -> SnapshotValidation:
    """Validate a frozen macro snapshot and its metadata sidecar."""

    path = Path(csv_path)
    meta_path = metadata_path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    if not meta_path.exists():
        raise FileNotFoundError(meta_path)

    metadata = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
    missing_meta = REQUIRED_METADATA - set(metadata)
    if missing_meta:
        raise ValueError(f"Snapshot metadata missing: {sorted(missing_meta)}")

    frame = pd.read_csv(path)
    required = {"observation_date", "value"}
    if not required.issubset(frame.columns):
        raise ValueError(f"Snapshot CSV must contain {sorted(required)}")
    if require_available_date and "available_date" not in frame.columns:
        raise ValueError("Point-in-time snapshots must include available_date")

    observation = pd.to_datetime(frame["observation_date"], errors="coerce")
    if observation.isna().any():
        raise ValueError("Invalid observation_date in snapshot")
    if observation.duplicated().any():
        raise ValueError("Duplicate observation_date in snapshot")

    values = pd.to_numeric(frame["value"], errors="coerce")
    if values.isna().any():
        raise ValueError("Snapshot contains non-numeric or missing values")

    has_available = "available_date" in frame.columns
    if has_available:
        available = pd.to_datetime(frame["available_date"], errors="coerce")
        if available.isna().any():
            raise ValueError("Invalid available_date in snapshot")
        if (available < observation).any():
            raise ValueError("available_date cannot be earlier than observation_date")

    return SnapshotValidation(
        rows=len(frame),
        first_observation=observation.min(),
        last_observation=observation.max(),
        has_available_date=has_available,
        metadata=metadata,
    )
