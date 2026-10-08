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
    {
        "available_date",
        "availability_evidence_url",
        "availability_lag_days",
        "observation_period",
        "source_value_url",
    }
)


class FrameContractError(ValueError):
    """A frame does not satisfy the observation-frame contract."""


@dataclass(frozen=True)
class FrameContractReport:
    ok: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    rows: int
    #: True when at least one row carries a usable available_date. Distinct from
    #: ``has_available_date_column``, which only says the column exists.
    has_available_date: bool
    has_available_date_column: bool


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
    available_values = (
        pd.to_datetime(frame["available_date"], errors="coerce").notna()
        if has_available
        else pd.Series(False, index=frame.index)
    )
    if require_available_date and not bool(available_values.any()):
        detail = (
            "column is present but every entry is empty"
            if has_available
            else "column is absent"
        )
        errors.append(f"frame must carry usable available_date under this policy ({detail})")

    if "availability_basis" in frame.columns:
        basis = frame["availability_basis"].astype("string").str.strip()
        invalid = basis.isna() | ~basis.isin(AVAILABILITY_BASES)
        if invalid.any():
            bad = sorted(basis.loc[invalid].dropna().unique().tolist())
            errors.append(f"invalid availability_basis {bad or ['<missing>']}; allowed {sorted(AVAILABILITY_BASES)}")

        if has_available and "observation_date" in frame.columns:
            available = pd.to_datetime(frame["available_date"], errors="coerce")
            observation = pd.to_datetime(frame["observation_date"], errors="coerce")
            needs_date = basis.ne("unknown")
            if (needs_date & available.isna()).any():
                errors.append("rows with a basis other than 'unknown' require an available_date")
            if (basis.eq("unknown") & available.notna()).any():
                errors.append("rows with basis 'unknown' must not carry an available_date")
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
                errors.append(message)

            if "source_value_url" in frame.columns:
                value_sources = frame["source_value_url"].astype("string").str.strip()
                if value_sources.isna().any() or value_sources.eq("").any():
                    errors.append("source_value_url must be populated when the column is present")
                elif not value_sources.str.startswith(("https://", "http://")).all():
                    errors.append("source_value_url must be an http(s) URL")
            if "availability_evidence_url" in frame.columns:
                evidence = frame["availability_evidence_url"].astype("string").str.strip()
                needs_evidence = basis.isin(EVIDENCED_BASES)
                if (needs_evidence & (evidence.isna() | evidence.eq(""))).any():
                    errors.append(
                        "rows with an evidenced basis require availability_evidence_url"
                    )
                elif not evidence.loc[needs_evidence].str.startswith(("https://", "http://")).all():
                    errors.append("availability_evidence_url must be an http(s) URL")
            elif basis.isin(EVIDENCED_BASES).any():
                errors.append(
                    "rows with an evidenced basis require an availability_evidence_url column"
                )

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
        has_available_date=bool(available_values.any()),
        has_available_date_column=has_available,
    )


def assert_frame_contract(frame: pd.DataFrame, *, require_available_date: bool = False) -> None:
    report = check_frame_contract(frame, require_available_date=require_available_date)
    if not report.ok:
        raise FrameContractError("; ".join(report.errors))


def normalize_observation_frame(
    frame: pd.DataFrame,
    *,
    key: str = "<frame>",
    dated_rows_basis: str = "unverified",
) -> pd.DataFrame:
    """Give any provider output the single normalized shape downstream code expects.

    Normalization is explicit and auditable:

    * ``observation_date`` and ``value`` are coerced to canonical types;
    * rows are sorted by ``observation_date`` (stably) so downstream code can
      assume order;
    * every row ends up carrying an ``availability_basis`` label, so provenance
      can never be silently dropped.

    The availability label is decided **per row**, matching the rule already
    implemented in :meth:`CsvProvider.fetch`:

    ==========================  ===============
    row has ``available_date``  label
    ==========================  ===============
    no                           ``unknown``
    yes, no basis column         ``dated_rows_basis``
    yes, basis present           kept as given
    ==========================  ===============

    Frame-level inference ("this frame has some dates, so label them all
    unverified") is deliberately not used: a series where only the first rows
    have a known publication date would then hand an evidence label to rows
    that have none.

    It never invents an ``available_date``. Guessing one here is exactly the
    look-ahead bug this project exists to prevent.

    ``dated_rows_basis`` cannot be an evidenced basis (``official_release`` /
    ``official_schedule``). Those may only be attached row by row, together with
    an ``availability_evidence_url`` — a blanket default would manufacture
    publication evidence out of nothing, and ``official_release`` is the single
    label the strictest availability policy admits.
    """

    if dated_rows_basis not in AVAILABILITY_BASES:
        raise FrameContractError(
            f"{key}: dated_rows_basis={dated_rows_basis!r} is not a known basis; "
            f"allowed {sorted(AVAILABILITY_BASES)}"
        )
    if dated_rows_basis in EVIDENCED_BASES:
        raise FrameContractError(
            f"{key}: dated_rows_basis={dated_rows_basis!r} asserts publication evidence. "
            "An evidenced basis must be set per row alongside availability_evidence_url, "
            "never applied as a blanket default."
        )

    if "observation_date" not in frame.columns or "value" not in frame.columns:
        raise FrameContractError(
            f"{key}: provider output must contain observation_date and value, "
            f"got {sorted(frame.columns)}"
        )

    out = frame.copy()
    out["observation_date"] = pd.to_datetime(out["observation_date"], errors="raise")
    out["value"] = pd.to_numeric(out["value"], errors="coerce")

    dated = pd.Series(False, index=out.index)
    if "available_date" in out.columns:
        out["available_date"] = pd.to_datetime(out["available_date"], errors="coerce")
        dated = out["available_date"].notna()

    # A blank cell counts as "no label" — a human leaving a cell empty is not an
    # assertion of anything, and treating it as one would smuggle in evidence.
    if "availability_basis" in out.columns:
        basis = out["availability_basis"].astype("string").str.strip()
        labeled = basis.notna() & basis.ne("")
        out["availability_basis"] = basis
    else:
        out["availability_basis"] = pd.Series(pd.NA, index=out.index, dtype="string")
        labeled = pd.Series(False, index=out.index)

    out.loc[~labeled & dated, "availability_basis"] = dated_rows_basis
    out.loc[~labeled & ~dated, "availability_basis"] = "unknown"

    ordered = ["observation_date", "value"]
    for column in (
        "available_date",
        "availability_basis",
        "availability_evidence_url",
        "availability_lag_days",
    ):
        if column in out.columns:
            ordered.append(column)
    rest = [c for c in out.columns if c not in ordered]

    return (
        out[ordered + rest]
        .sort_values("observation_date", kind="stable")
        .reset_index(drop=True)
    )
