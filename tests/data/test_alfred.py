"""Tests for the ALFRED point-in-time adapter.

Two properties matter more than anything else here:

1. ``available_date`` must never exceed the requested vintage. If it does, a
   backtest sees a value before that value could have existed.
2. The request must actually hit ALFRED. ``fred.stlouisfed.org`` accepts a
   ``vintage_date`` argument and silently ignores it, returning the latest
   revised history — the exact look-ahead this provider exists to avoid, and it
   fails silently. That is why the host is asserted, not just the result.
"""

from __future__ import annotations

import pandas as pd
import pytest
from mrq_core.contracts import check_frame_contract
from mrq_core.types import SeriesSpec
from mrq_data.providers import alfred as alfred_mod
from mrq_data.providers.alfred import ALFRED_BASE, AlfredProvider, latest_vintage_date

SPEC = SeriesSpec(
    key="us_core_cpi",
    provider="alfred",
    symbol="CPIAUCSL",
    kind="macro",
    frequency="monthly",
    release_lag_days=14,
)


@pytest.fixture
def alfred(monkeypatch: pytest.MonkeyPatch) -> AlfredProvider:
    """Deterministic stand-in for the network, recording the URL it was given."""

    requested: list[str] = []

    payload = pd.DataFrame(
        {
            "observation_date": ["2019-11-01", "2019-12-01", "2020-01-01"],
            "CPIAUCSL": [257.971, 257.974, 258.115],
        }
    )

    def fake_read_csv(url, *args, **kwargs):
        requested.append(url)
        return payload.copy()

    monkeypatch.setattr(alfred_mod.pd, "read_csv", fake_read_csv)
    provider = AlfredProvider()
    provider._requested_urls = requested  # type: ignore[attr-defined]
    return provider


def test_request_targets_alfred_not_fred(alfred: AlfredProvider):
    """Regression: the fred.stlouisfed.org graph endpoint ignores vintage_date."""

    alfred.fetch(SPEC, vintage_date="2015-01-01")
    url = alfred._requested_urls[0]  # type: ignore[attr-defined]
    assert url.startswith("https://alfred.stlouisfed.org/")
    # Substring check would pass either way, since the alfred host contains
    # "fred.stlouisfed.org" as a suffix.
    assert not url.startswith("https://fred.stlouisfed.org/")
    assert "vintage_date=2015-01-01" in url


def test_base_url_is_the_alfred_host():
    assert ALFRED_BASE.startswith("https://alfred.stlouisfed.org/")


def test_available_date_never_exceeds_the_vintage(alfred: AlfredProvider):
    frame = alfred.fetch(SPEC, vintage_date="2019-12-01")
    # 2020-01 would be knowable on 2020-01-15 by the lag estimate, but the
    # vintage says nobody knew it before 2019-12-01. The clamp must win.
    assert (frame["available_date"] <= frame["vintage_date"]).all()
    late = frame[frame["observation_date"] > "2019-12-01"]
    assert (late["available_date"] == pd.Timestamp("2019-12-01")).all()


def test_clamp_never_delays_when_the_lag_is_earlier(alfred: AlfredProvider):
    frame = alfred.fetch(SPEC, vintage_date="2024-01-01")
    row = frame[frame["observation_date"] == "2019-11-01"].iloc[0]
    assert row["available_date"] == pd.Timestamp("2019-11-15")


def test_rows_are_labelled_fixed_lag_never_official_release(alfred: AlfredProvider):
    """A vintage proves an upper bound, not a publication date.

    Claiming `official_release` would let these rows through the strictest
    availability policy on the strength of evidence we do not have.
    """

    frame = alfred.fetch(SPEC, vintage_date="2019-12-01")
    assert set(frame["availability_basis"]) == {"fixed_lag"}


def test_vintage_is_recorded_for_audit(alfred: AlfredProvider):
    frame = alfred.fetch(SPEC, vintage_date="2019-12-01")
    assert set(frame["vintage_date"]) == {pd.Timestamp("2019-12-01")}


def test_output_satisfies_the_frame_contract(alfred: AlfredProvider):
    frame = alfred.fetch(SPEC, vintage_date="2024-01-01")
    assert check_frame_contract(frame).ok


def test_start_and_end_filter_observations(alfred: AlfredProvider):
    frame = alfred.fetch(SPEC, start="2019-12-01", end="2020-01-01", vintage_date="2024-01-01")
    assert list(frame["observation_date"]) == [
        pd.Timestamp("2019-12-01"),
        pd.Timestamp("2020-01-01"),
    ]


def test_empty_result_has_the_normalized_shape(alfred: AlfredProvider):
    frame = alfred.fetch(SPEC, start="2030-01-01", vintage_date="2024-01-01")
    assert frame.empty
    assert set(frame.columns) >= {
        "observation_date",
        "value",
        "available_date",
        "availability_basis",
        "vintage_date",
    }


def test_default_vintage_is_today(monkeypatch: pytest.MonkeyPatch):
    seen: list[str] = []
    payload = pd.DataFrame({"observation_date": ["2020-01-01"], "CPIAUCSL": [258.0]})
    monkeypatch.setattr(
        alfred_mod.pd,
        "read_csv",
        lambda url, *a, **k: (seen.append(url), payload.copy())[1],
    )
    AlfredProvider().fetch(SPEC)
    assert pd.Timestamp.now().normalize().date().isoformat() in seen[0]


def test_latest_vintage_date_is_the_earliest_vintage_held():
    """A backtest cannot start before the oldest snapshot you actually own."""

    early = pd.DataFrame({"vintage_date": [pd.Timestamp("2020-01-01")]})
    late = pd.DataFrame({"vintage_date": [pd.Timestamp("2024-01-01")]})
    assert latest_vintage_date("x", [late, early]) == pd.Timestamp("2020-01-01")
    assert latest_vintage_date("x", []) is None


def test_registry_exposes_alfred():
    from mrq_data.registry import default_provider_registry

    assert "alfred" in default_provider_registry()
