"""Tests for the vintage archive.

The archive is what separates a genuine point-in-time backtest from a
current-value series wearing a point-in-time label. These tests pin the
behaviour that makes the difference: a backtest dated t must see the newest
snapshot taken at or before t, and must see nothing at all if the archive
does not reach that far.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from mrq_core.types import SeriesSpec
from mrq_data.providers.alfred import AlfredProvider
from mrq_data.vintages import VintageCoverage, coverage, load_vintage, snapshot_series

SPEC = SeriesSpec(
    key="us_core_cpi",
    provider="alfred",
    symbol="CPIAUCSL",
    kind="macro",
    frequency="monthly",
    release_lag_days=14,
)


class _StubAlfred(AlfredProvider):
    """Returns a value that depends on the vintage, so revisions are visible."""

    def fetch(self, spec, start=None, end=None, vintage_date=None):
        vintage = pd.Timestamp(vintage_date or "2024-01-01")
        base = pd.Timestamp("2020-01-31")
        # A vintage before the first observation legitimately has nothing to
        # report; return the empty frame with its columns intact so callers can
        # tell "no data yet" apart from "malformed response".
        index = pd.date_range(base, vintage, freq="ME")
        # A real vintage only carries observations released before that date;
        # returning later ones would make the archive's whole premise false.
        released = index + pd.Timedelta(days=spec.release_lag_days)
        index = index[released <= vintage]
        return pd.DataFrame(
            {
                "observation_date": index,
                "value": [100.0 + d.year + d.month / 12 - (vintage.year - 2020) * 0.1
                          for d in index],
                "vintage_date": [vintage] * len(index),
                "available_date": index + pd.Timedelta(days=spec.release_lag_days),
                "availability_basis": ["fixed_lag"] * len(index),
            }
        )


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    snapshot_series(
        SPEC,
        start="2020-01-01",
        end="2022-01-01",
        step_months=6,
        root=tmp_path,
        provider=_StubAlfred(),
    )
    return tmp_path


def test_snapshots_land_one_file_per_vintage(archive: Path):
    files = sorted(p.name for p in (archive / "CPIAUCSL").glob("*.csv"))
    assert files == [
        "2020-01-01.csv",
        "2020-07-01.csv",
        "2021-01-01.csv",
        "2021-07-01.csv",
        "2022-01-01.csv",
    ]


def test_as_of_picks_the_newest_vintage_at_or_before_the_date(archive: Path):
    frame = load_vintage("CPIAUCSL", as_of="2021-09-01", root=archive)
    assert frame is not None
    assert frame["vintage_date"].iloc[0] == pd.Timestamp("2021-07-01")


def test_as_of_exact_match_is_used(archive: Path):
    frame = load_vintage("CPIAUCSL", as_of="2021-07-01", root=archive)
    assert frame["vintage_date"].iloc[0] == pd.Timestamp("2021-07-01")


def test_as_of_before_the_archive_returns_nothing(archive: Path):
    """Falling back to today's data here would be the look-ahead this prevents."""

    assert load_vintage("CPIAUCSL", as_of="2019-01-01", root=archive) is None


def test_unknown_series_returns_nothing(archive: Path):
    assert load_vintage("NOSUCHSERIES", as_of="2021-01-01", root=archive) is None


def test_a_backtest_earlier_than_the_data_sees_nothing_it_could_not_know(archive: Path):
    """The value seen for 2020-06 shrinks as the backtest moves forward.

    This is the whole point: a single fetch of today's data would return the
    same number for every row of this table.
    """

    seen = {}
    for when in ("2020-07-01", "2021-01-01", "2022-01-01"):
        frame = load_vintage("CPIAUCSL", as_of=when, root=archive)
        row = frame[frame["observation_date"] == "2020-06-30"]
        seen[when] = float(row["value"].iloc[0]) if len(row) else None

    assert seen["2020-07-01"] is None  # not published yet
    assert seen["2021-01-01"] is not None
    assert seen["2022-01-01"] is not None
    assert seen["2021-01-01"] > seen["2022-01-01"]  # revised downward


def test_rerunning_skips_existing_snapshots(archive: Path):
    again = snapshot_series(
        SPEC,
        start="2020-01-01",
        end="2022-01-01",
        step_months=6,
        root=archive,
        provider=_StubAlfred(),
    )
    assert again == {"captured": 0, "skipped": 5}


def test_coverage_reports_range_and_as_of(archive: Path):
    cov = coverage("CPIAUCSL", root=archive)
    assert isinstance(cov, VintageCoverage)
    assert cov.earliest == pd.Timestamp("2020-01-01")
    assert cov.latest == pd.Timestamp("2022-01-01")
    assert cov.as_of("2021-08-15") == pd.Timestamp("2021-07-01")
    assert cov.as_of("2019-12-31") is None


def test_coverage_of_missing_series_is_empty(tmp_path: Path):
    cov = coverage("NOPE", root=tmp_path)
    assert cov.vintages == ()
    assert cov.earliest is None


def test_snapshots_carry_observation_value_and_vintage(archive: Path):
    frame = load_vintage("CPIAUCSL", as_of="2022-01-01", root=archive)
    assert set(frame.columns) >= {"observation_date", "value", "vintage_date"}
    assert frame["observation_date"].is_monotonic_increasing


def test_bad_step_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="at least 1"):
        snapshot_series(SPEC, start="2020-01-01", end="2021-01-01", step_months=0, root=tmp_path)


def test_start_after_end_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="is after end"):
        snapshot_series(SPEC, start="2022-01-01", end="2020-01-01", root=tmp_path)


def test_symbol_names_are_made_filesystem_safe(tmp_path: Path):
    """A symbol with path characters must not escape the archive root."""

    weird = SeriesSpec(
        key="x", provider="alfred", symbol="../../etc/passwd", kind="macro",
        frequency="monthly", release_lag_days=0,
    )
    snapshot_series(weird, start="2020-01-01", end="2020-01-01", root=tmp_path, provider=_StubAlfred())
    written = list(tmp_path.rglob("*.csv"))
    assert written, "expected a sanitised file to be written"
    assert all(tmp_path in p.parents for p in written)
