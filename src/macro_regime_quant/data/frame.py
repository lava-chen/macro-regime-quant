from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

#: How the available_date of a row was established. Ordered from strongest to weakest
#: evidence; anything other than ``official_release`` should be reported alongside results.
AVAILABILITY_BASES: frozenset[str] = frozenset(
    {
        "official_release",
        "official_schedule",
        "fixed_lag",
        "unverified",
        "unknown",
    }
)

#: Bases that carry real publication evidence rather than an assumption.
EVIDENCED_BASES: frozenset[str] = frozenset({"official_release", "official_schedule"})

#: Columns every normalized observation frame carries.
REQUIRED_COLUMNS: frozenset[str] = frozenset({"observation_date", "value", "availability_basis"})

#: Columns a normalized frame may carry.
OPTIONAL_COLUMNS: frozenset[str] = frozenset(
    {"available_date", "availability_evidence_url", "availability_lag_days"}
)


class FrameContractError(ValueError):
    """A frame does not satisfy the observation-frame contract."""


@dataclass(frozen=True)
class FrameContractReport:
    ok: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    rows: int
    has_available_date: bool


def check_frame_contract(frame: pd.DataFrame, *, require_available_date: bool = False) -> (
    FrameContractReport
):
    """Inspect a frame against the observation-frame contract without raising.

    Returns every violation at once, so a caller can report all of them instead of
    fixing one error per run.
    """

    errors: list[str] = []
    warnings: list[str] = []

    missing = REQUIRED_COLUMNS - set(frame.columns)
    if missing:
        errors.append(f"missing required column(s) {sorted(missing)}")

    unknown = set(frame.columns) - REQUIRED_COLUMNS - OPTIONAL_COLUMNS
    if unknown:
        warnings.append(f"unrecognized extra column(s) {sorted(unknown)}")

    if "observation_date" in frame.columns:
        observation = pd.to_datetime(frame["observation_date"], errors="coerce")
        if observation.isna().any():
            errors.append("observation_date contains unparseable values")
        elif observation.duplicated().any():
            dupes = observation[observation.duplicated()].dt.strftime("%Y-%m-%d").unique()
            errors.append(f"duplicate observation_date value(s) {sorted(dupes)[:5]}")

    if "value" in frame.columns and pd.to_numeric(frame["value"], errors="coerce").isna().any():
        errors.append("value contains non-numeric or missing entries")

    has_available = "available_date" in frame.columns
    if require_available_date and not has_available:
        errors.append("frame must carry available_date under this policy")

    if "availability_basis" in frame.columns:
        basis = frame["availability_basis"].astype("string").str.strip()
        invalid = basis.isna() | ~basis.isin(AVAILABILITY_BASES)
        if invalid.any():
            bad = sorted(basis.loc[invalid].dropna().unique().tolist())
            errors.append(f"invalid availability_basis {bad or ['<missing>']}; allowed {sorted(AVAILABILITY_BASES)}")

        if has_available and not errors:
            available = pd.to_datetime(frame["available_date"], errors="coerce")
            observation = pd.to_datetime(frame["observation_date"], errors="coerce")
            needs_date = basis.ne("unknown")
            if (needs_date & available.isna()).any():
                errors.append("rows with a basis other than 'unknown' require an available_date")
            if (basis.eq("unknown") & available.notna()).any():
                errors.append("rows with basis 'unknown' must not carry an available_date")
            if (available.notna() & (available < observation)).any():
                errors.append("available_date cannot be earlier than observation_date")
            unevidenced = basis.isin(AVAILABILITY_BASES - EVIDENCED_BASES) & available.notna()
            if unevidenced.any():
                warnings.append(
                    f"{int(unevidenced.sum())} row(s) use an unevidenced availability_basis; "
                    "results built on them are indicative, not publication-quality"
                )

    return FrameContractReport(
        ok=not errors,
        errors=tuple(errors),
        warnings=tuple(warnings),
        rows=len(frame),
        has_available_date=has_available,
    )


def assert_frame_contract(frame: pd.DataFrame, *, require_available_date: bool = False) -> None:
    report = check_frame_contract(frame, require_available_date=require_available_date)
    if not report.ok:
        raise FrameContractError("; ".join(report.errors))


def normalize_observation_frame(
    frame: pd.DataFrame,
    *,
    key: str = "<frame>",
    default_basis: str = "unknown",
) -> pd.DataFrame:
    """Give any provider output the single normalized shape downstream code expects.

    Normalization is explicit and lossy-auditable:

    * ``observation_date`` and ``value`` are coerced to canonical types;
    * a missing ``availability_basis`` is filled with ``default_basis`` rather than
      being dropped, so a row can never silently lose its provenance label;
    * rows are sorted by ``observation_date`` so downstream code can assume order.

    It never invents an ``available_date``. A provider that cannot supply one keeps
    ``availability_basis='unknown'``, which the availability policies exclude by
    default. Guessing here is exactly the look-ahead bug this project exists to avoid.
    """

    if "observation_date" not in frame.columns or "value" not in frame.columns:
        raise FrameContractError(
            f"{key}: provider output must contain observation_date and value, "
            f"got {sorted(frame.columns)}"
        )

    out = frame.copy()
    out["observation_date"] = pd.to_datetime(out["observation_date"], errors="raise")
    out["value"] = pd.to_numeric(out["value"], errors="coerce")

    if "available_date" in out.columns:
        out["available_date"] = pd.to_datetime(out["available_date"], errors="coerce")

    if "availability_basis" not in out.columns:
        out["availability_basis"] = default_basis
    else:
        out["availability_basis"] = out["availability_basis"].astype("string").str.strip()
        missing_basis = out["availability_basis"].isna()
        if missing_basis.any():
            out.loc[missing_basis, "availability_basis"] = default_basis

    if default_basis == "unverified" and "available_date" in out.columns:
        # Provider handed us dates but no provenance: label them, do not trust them.
        out.loc[out["availability_basis"] == "unknown", "availability_basis"] = "unknown"

    ordered = ["observation_date", "value"]
    for column in ("available_date", "availability_basis", "availability_evidence_url"):
        if column in out.columns:
            ordered.append(column)
    rest = [c for c in out.columns if c not in ordered]

    return out[ordered + rest].sort_values("observation_date").reset_index(drop=True)
