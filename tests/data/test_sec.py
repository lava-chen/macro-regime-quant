"""Tests for the SEC EDGAR provider.

The behaviour worth protecting is the vantage point. EDGAR retains every filed
version of a statement, so a backtest dated *t* and one dated today can look at
the same accounting period and see different numbers — that difference is the
restatement, and hiding it is the entire bug this provider exists to prevent.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from mrq_core.asof import FundamentalObservation
from mrq_data.providers.sec import SecProvider

# A period reported twice with different values, and a third filing that repeats
# the second — mirroring what a restatement looks like on EDGAR.
RESTATED_PERIOD = "2017-09-30"

FACTS = {
    "facts": {
        "us-gaap": {
            "NetCashProvidedByUsedInOperatingActivities": {
                "label": "Operating cash flow",
                "units": {
                    "USD": [
                        {"end": RESTATED_PERIOD, "val": 63.598e9, "filed": "2017-11-03",
                         "form": "10-Q", "accn": "000-1"},
                        {"end": RESTATED_PERIOD, "val": 64.225e9, "filed": "2018-11-05",
                         "form": "10-K", "accn": "000-2"},
                        {"end": RESTATED_PERIOD, "val": 64.225e9, "filed": "2019-10-31",
                         "form": "10-K", "accn": "000-3"},
                        # Filed after any vantage point used below.
                        {"end": "2018-03-31", "val": 20.0e9, "filed": "2018-05-01",
                         "form": "10-Q", "accn": "000-4"},
                    ]
                },
            },
            "Assets": {
                "label": "Assets",
                "units": {
                    "USD": [
                        # An 8-K carries fragments, not a comparable statement.
                        {"end": RESTATED_PERIOD, "val": 1.0e9, "filed": "2017-10-01",
                         "form": "8-K", "accn": "000-5"},
                    ]
                },
            },
        }
    }
}

TICKERS = {
    "1": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "2": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp"},
}

METRICS = {"NetCashProvidedByUsedInOperatingActivities": "ocf"}


@pytest.fixture
def sec(tmp_path: Path) -> SecProvider:
    provider = SecProvider(cache_dir=tmp_path, min_interval=0.0)
    provider._get_json = lambda url, name, refresh=False: (  # type: ignore[method-assign]
        TICKERS if "tickers" in name else FACTS
    )
    return provider


def test_as_of_is_mandatory(sec: SecProvider):
    with pytest.raises(ValueError, match="as_of is required"):
        sec.observations("AAPL", METRICS, as_of="")


def test_early_vantage_point_sees_the_original_filing(sec: SecProvider):
    obs = sec.observations("AAPL", METRICS, as_of="2018-01-01")
    assert len(obs) == 1
    assert obs[0].value == pytest.approx(63.598e9)
    assert obs[0].available_date == pd.Timestamp("2017-11-03")


def test_later_vantage_point_sees_the_restatement(sec: SecProvider):
    obs = sec.observations("AAPL", METRICS, as_of="2019-01-01")
    assert obs[0].value == pytest.approx(64.225e9)
    assert obs[0].available_date == pd.Timestamp("2018-11-05")


def test_vintage_only_moves_forward(sec: SecProvider):
    """Once a restatement is visible it stays visible at later vantage points."""

    late = sec.observations("AAPL", METRICS, as_of="2026-01-01")[0]
    assert late.value == pytest.approx(64.225e9)
    assert late.available_date == pd.Timestamp("2019-10-31")


def test_future_filings_are_excluded(sec: SecProvider):
    obs = sec.observations("AAPL", METRICS, as_of="2018-01-01")
    assert all(o.period_end != pd.Timestamp("2018-03-31") for o in obs)


def test_non_periodic_forms_are_excluded(sec: SecProvider):
    obs = sec.observations("AAPL", {"Assets": "assets"}, as_of="2018-01-01")
    assert obs == []


def test_observations_satisfy_the_core_contract(sec: SecProvider):
    for obs in sec.observations("AAPL", METRICS, as_of="2026-01-01"):
        assert isinstance(obs, FundamentalObservation)
        assert obs.available_date >= obs.period_end
        assert obs.company_id == "US:0000320193"
        assert obs.source.startswith("SEC/")


def test_company_id_is_cik_based_not_ticker(sec: SecProvider):
    """Tickers get reused; a CIK does not."""

    assert sec.entity_id("AAPL") == "US:0000320193"
    assert sec.entity_id("msft") == "US:0000789019"


def test_unknown_ticker_raises(sec: SecProvider):
    with pytest.raises(KeyError):
        sec.ticker_to_cik("NOPE")


def test_frame_is_tidy_and_newest_first(sec: SecProvider):
    frame = sec.fundamentals_frame("AAPL", METRICS, as_of="2026-01-01")
    assert list(frame.columns) == [
        "company_id", "metric", "period_end", "available_date", "value", "unit", "source",
    ]
    assert (frame["metric"] == "ocf").all()


def test_empty_result_keeps_the_columns(sec: SecProvider):
    frame = sec.fundamentals_frame("AAPL", {"NoSuchTag": "x"}, as_of="2026-01-01")
    assert frame.empty
    assert "available_date" in frame.columns


def test_facts_payload_is_cached_on_disk(tmp_path: Path):
    calls: list[str] = []

    def fake_get(url, cache_name, refresh=False):
        calls.append(cache_name)
        cache = tmp_path / cache_name
        if cache.exists():
            return json.loads(cache.read_text())
        cache.write_text(json.dumps(FACTS))
        return FACTS

    provider = SecProvider(cache_dir=tmp_path, min_interval=0.0)
    provider._get_json = fake_get  # type: ignore[method-assign]
    provider.company_facts(320193)
    provider.company_facts(320193)
    assert calls == ["CIK0000320193_facts.json", "CIK0000320193_facts.json"]
    assert (tmp_path / "CIK0000320193_facts.json").exists()
