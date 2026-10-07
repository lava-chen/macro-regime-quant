"""End-to-end smoke tests for the monthly panel.

Every other test in this repository exercises one function with one hand-built
fixture. None of them notice when the panel comes out empty. That gap is not
hypothetical: a branch condition that could never be true sent every
date-less provider down the wrong path, `available_date` stayed NaT for all of
history, and the pipeline returned an empty frame while the whole suite stayed
green. These tests assert the panel actually has rows.

The providers are stubbed so the suite stays offline and deterministic.
"""

from __future__ import annotations

from pathlib import Path

import mrq_engines.pipeline as pipeline_mod
import numpy as np
import pandas as pd
import pytest
from mrq_engines.pipeline import load_monthly_panel

CATALOG = """
series:
  growth_src:
    provider: dateless
    symbol: GROWTH
    kind: macro
    frequency: monthly
    release_lag_days: 18
  dated_src:
    provider: dated
    symbol: DATED
    kind: macro
    frequency: monthly
    release_lag_days: 10
  contradictory:
    provider: contradictory
    symbol: BAD
    kind: macro
    frequency: monthly
    release_lag_days: 10
"""

OBS = pd.date_range("2015-01-31", periods=36, freq="ME")


class _DatelessProvider:
    """A provider that knows nothing about publication dates (FRED, Yahoo, ...)."""

    def fetch(self, spec, start=None, end=None):
        return pd.DataFrame({"observation_date": OBS, "value": np.arange(36, dtype=float) + 100})


class _DatedProvider:
    """A provider that reports real availability (CSV snapshots, ALFRED)."""

    def fetch(self, spec, start=None, end=None):
        return pd.DataFrame(
            {
                "observation_date": OBS,
                "available_date": OBS + pd.Timedelta(days=spec.release_lag_days),
                "value": np.arange(36, dtype=float) + 200,
                "availability_basis": "fixed_lag",
            }
        )


class _ContradictoryProvider(_DatelessProvider):
    """Claims publication evidence while supplying no date at all."""

    def fetch(self, spec, start=None, end=None):
        frame = super().fetch(spec, start, end)
        frame["availability_basis"] = "official_release"
        return frame


@pytest.fixture
def catalog(tmp_path: Path) -> Path:
    path = tmp_path / "catalog.yaml"
    path.write_text(CATALOG, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _stub_providers(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        pipeline_mod,
        "default_provider_registry",
        lambda: {
            "dateless": _DatelessProvider(),
            "dated": _DatedProvider(),
            "contradictory": _ContradictoryProvider(),
        },
    )


def test_panel_from_a_dateless_provider_is_not_empty(catalog: Path):
    """Regression: dateless providers must still get an available_date.

    A provider that returns only observation_date/value is the common case.
    Without the catalogue's release lag the panel came out empty.
    """

    panel = load_monthly_panel(catalog, ["growth_src"], start="2015-01-01", end="2018-01-01")
    assert not panel.empty
    assert panel["growth_src"].notna().any()
    assert len(panel) >= 12


def test_first_period_is_empty_because_the_data_was_not_published_yet(catalog: Path):
    """The opening gap is the point-in-time discipline working, not a bug.

    On 2015-01-31 the January observation is not knowable yet — the catalogue
    lag puts its availability on 2015-02-18. A panel that filled this cell
    would be leaking the future.
    """

    panel = load_monthly_panel(catalog, ["growth_src"], start="2015-01-01", end="2018-01-01")
    assert pd.isna(panel.loc["2015-01-31", "growth_src"])
    assert panel["growth_src"].notna().sum() == len(panel) - 1


def test_every_subsequent_period_is_populated(catalog: Path):
    """Once the lag elapses the series is continuous — no internal holes."""

    panel = load_monthly_panel(catalog, ["growth_src"], start="2015-01-01", end="2018-01-01")
    after_first = panel["growth_src"].iloc[1:]
    assert after_first.notna().all()
    assert after_first.index.is_monotonic_increasing


def test_panel_from_a_dated_provider_is_not_empty(catalog: Path):
    panel = load_monthly_panel(catalog, ["dated_src"], start="2015-01-01", end="2018-01-01")
    assert not panel.empty
    assert panel["dated_src"].notna().any()


def test_mixed_panel_joins_both_kinds(catalog: Path):
    panel = load_monthly_panel(
        catalog, ["growth_src", "dated_src"], start="2015-01-01", end="2018-01-01"
    )
    assert set(panel.columns) == {"growth_src", "dated_src"}
    # Both series drop exactly their first (not-yet-published) period.
    assert panel.notna().sum().to_dict() == {
        "growth_src": len(panel) - 1,
        "dated_src": len(panel) - 1,
    }


def test_evidence_without_a_date_is_rejected(catalog: Path):
    """A row asserting official_release while carrying no date is a contradiction."""

    with pytest.raises(ValueError, match="requires an available_date"):
        load_monthly_panel(catalog, ["contradictory"], start="2015-01-01", end="2018-01-01")


def test_unknown_key_raises(catalog: Path):
    with pytest.raises(KeyError):
        load_monthly_panel(catalog, ["nope"], start="2015-01-01", end="2018-01-01")
