from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml
from mrq_core.contracts import AVAILABILITY_BASES

REQUIRED_METADATA = {
    "series_key",
    "source_name",
    "source_url",
    "frequency",
    "unit",
    "downloaded_at",
    "reported_as",
    "revision_policy",
}

__all__ = [
    "AVAILABILITY_BASES",
    "REQUIRED_METADATA",
    "SnapshotValidation",
    "metadata_path",
    "validate_snapshot",
]


@dataclass(frozen=True)
class SnapshotValidation:
    rows: int
    first_observation: pd.Timestamp
    last_observation: pd.Timestamp
    has_available_date: bool
    metadata: dict[str, object]
    availability_basis_counts: dict[str, int]


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
    required = {"observation_date", "value", "availability_basis"}
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

    basis = frame["availability_basis"].astype("string").str.strip()
    invalid_basis = basis.isna() | ~basis.isin(AVAILABILITY_BASES)
    if invalid_basis.any():
        bad = sorted(basis.loc[invalid_basis].dropna().unique().tolist())
        raise ValueError(f"Invalid or missing availability_basis: {bad or ['<missing>']}")

    has_available = "available_date" in frame.columns
    if has_available:
        available = pd.to_datetime(frame["available_date"], errors="coerce")
        needs_date = basis.ne("unknown")
        if (needs_date & available.isna()).any():
            raise ValueError("Non-unknown availability_basis requires available_date")
        if (basis.eq("unknown") & available.notna()).any():
            raise ValueError("Unknown availability_basis must not contain an available_date")
        earliest_valid_date = observation.copy()
        if "observation_period" in frame.columns:
            period_prefix = (
                frame["observation_period"]
                .astype("string")
                .str.extract(r"^(\d{4}-\d{2})", expand=False)
            )
            period_start = pd.to_datetime(period_prefix + "-01", errors="coerce")
            earliest_valid_date = period_start.fillna(observation)
        if (available.notna() & (available < earliest_valid_date)).any():
            message = (
                "available_date cannot be earlier than the observation period start"
                if "observation_period" in frame.columns
                else "available_date cannot be earlier than observation_date"
            )
            raise ValueError(message)
    else:
        available = pd.Series(pd.NaT, index=frame.index)
        if basis.ne("unknown").any():
            raise ValueError(
                "Snapshots without available_date may only use unknown availability_basis"
            )

    if "source_value_url" in frame.columns:
        value_sources = frame["source_value_url"].astype("string").str.strip()
        if value_sources.isna().any() or value_sources.eq("").any():
            raise ValueError("source_value_url must be populated when the column is present")
        if not value_sources.str.startswith(("https://", "http://")).all():
            raise ValueError("source_value_url must be an http(s) URL")

    evidence = (
        frame["availability_evidence_url"].astype("string").str.strip()
        if "availability_evidence_url" in frame.columns
        else pd.Series(pd.NA, index=frame.index, dtype="string")
    )
    evidence_required = basis.isin({"official_release", "official_schedule"})
    if (evidence_required & evidence.isna()).any() or (evidence_required & evidence.eq("")).any():
        raise ValueError("Official release/schedule rows require availability_evidence_url")
    if (
        evidence_required.any()
        and not evidence.loc[evidence_required].str.startswith(("https://", "http://")).all()
    ):
        raise ValueError("availability_evidence_url must be an http(s) URL")

    if basis.eq("fixed_lag").any():
        lag = metadata.get("availability_lag_days")
        if not isinstance(lag, (int, float)) or lag < 0:
            raise ValueError(
                "Fixed-lag snapshots require non-negative availability_lag_days metadata"
            )
        expected = observation + pd.to_timedelta(lag, unit="D")
        if (
            not available.loc[basis.eq("fixed_lag")]
            .reset_index(drop=True)
            .equals(expected.loc[basis.eq("fixed_lag")].reset_index(drop=True))
        ):
            raise ValueError("fixed_lag available_date values do not match availability_lag_days")

    return SnapshotValidation(
        rows=len(frame),
        first_observation=observation.min(),
        last_observation=observation.max(),
        has_available_date=bool(has_available and available.notna().any()),
        metadata=metadata,
        availability_basis_counts={
            key: int(value)
            for key, value in basis.value_counts()
            .reindex(sorted(AVAILABILITY_BASES), fill_value=0)
            .items()
        },
    )
