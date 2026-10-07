"""Contract tests.

Every DataProvider must return the same normalized shape, and that shape must be
honest about what it does not know. These tests are the executable version of the
"no same-period signal execution" rule: a provider that cannot say when a value became
known is labeled ``unknown`` and is excluded by the strict availability policies.
"""

from pathlib import Path

import pandas as pd
import pytest

from macro_regime_quant.data.frame import (
    AVAILABILITY_BASES,
    FrameContractError,
    assert_frame_contract,
    check_frame_contract,
    normalize_observation_frame,
)
from macro_regime_quant.data.models import SeriesSpec
from macro_regime_quant.data.providers.base import DataProvider
from macro_regime_quant.data.providers.csv import CsvProvider

DAILY_SPEC = SeriesSpec(
    key="test_daily",
    provider="csv",
    symbol="series.csv",
    kind="macro",
    frequency="monthly",
    release_lag_days=10,
)


def _write_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)


# --------------------------------------------------------------------------- #
# normalize_observation_frame
# --------------------------------------------------------------------------- #


def test_normalize_fills_missing_basis_with_unknown_not_a_guess():
    raw = pd.DataFrame({"observation_date": ["2025-01-31"], "value": [1.0]})
    out = normalize_observation_frame(raw, key="test")

    assert_frame_contract(out)
    # Crucially: no available_date is invented. Guessing one is look-ahead bias.
    assert "available_date" not in out.columns
    assert out.loc[0, "availability_basis"] == "unknown"


def test_normalize_preserves_supplied_available_date():
    raw = pd.DataFrame(
        {
            "observation_date": ["2025-01-31", "2025-02-28"],
            "available_date": ["2025-02-15", "2025-03-15"],
            "value": [5.0, 5.2],
        }
    )
    out = normalize_observation_frame(raw, default_basis="unverified")

    assert_frame_contract(out)
    assert out.loc[0, "available_date"] == pd.Timestamp("2025-02-15")
    assert set(out["availability_basis"]) == {"unverified"}


def test_normalize_does_not_relabel_explicit_basis():
    raw = pd.DataFrame(
        {
            "observation_date": ["2025-01-31"],
            "available_date": ["2025-02-15"],
            "value": [1.0],
            "availability_basis": ["official_release"],
        }
    )
    out = normalize_observation_frame(raw, default_basis="unverified")
    assert out.loc[0, "availability_basis"] == "official_release"


def test_normalize_sorts_by_observation_date():
    raw = pd.DataFrame(
        {"observation_date": ["2025-03-31", "2025-01-31"], "value": [3.0, 1.0]}
    )
    out = normalize_observation_frame(raw)
    assert list(out["observation_date"]) == [
        pd.Timestamp("2025-01-31"),
        pd.Timestamp("2025-03-31"),
    ]


def test_normalize_rejects_frame_without_core_columns():
    with pytest.raises(FrameContractError, match="observation_date"):
        normalize_observation_frame(pd.DataFrame({"date": ["2025-01-31"], "v": [1.0]}))


def test_normalize_rejects_unparseable_observation_date():
    with pytest.raises(ValueError):
        normalize_observation_frame(
            pd.DataFrame({"observation_date": ["not-a-date"], "value": [1.0]})
        )


# --------------------------------------------------------------------------- #
# check_frame_contract
# --------------------------------------------------------------------------- #


def test_contract_rejects_available_before_observation():
    frame = normalize_observation_frame(
        pd.DataFrame(
            {
                "observation_date": ["2025-02-28"],
                "available_date": ["2025-01-01"],
                "value": [1.0],
                "availability_basis": ["official_release"],
            }
        )
    )
    report = check_frame_contract(frame)
    assert not report.ok
    assert any("earlier than observation_date" in e for e in report.errors)


def test_contract_rejects_duplicate_observation_dates():
    frame = normalize_observation_frame(
        pd.DataFrame({"observation_date": ["2025-01-31", "2025-01-31"], "value": [1.0, 2.0]})
    )
    report = check_frame_contract(frame)
    assert not report.ok
    assert any("duplicate observation_date" in e for e in report.errors)


def test_contract_rejects_unknown_basis_carrying_a_date():
    frame = normalize_observation_frame(
        pd.DataFrame(
            {
                "observation_date": ["2025-01-31"],
                "available_date": ["2025-02-15"],
                "value": [1.0],
                "availability_basis": ["unknown"],
            }
        )
    )
    report = check_frame_contract(frame)
    assert not report.ok
    assert any("must not carry an available_date" in e for e in report.errors)


def test_contract_warns_but_does_not_fail_on_unevidenced_basis():
    frame = normalize_observation_frame(
        pd.DataFrame(
            {
                "observation_date": ["2025-01-31"],
                "available_date": ["2025-02-15"],
                "value": [1.0],
                "availability_basis": ["fixed_lag"],
            }
        )
    )
    report = check_frame_contract(frame)
    assert report.ok
    assert any("indicative" in w for w in report.warnings)


def test_contract_can_require_available_date():
    frame = normalize_observation_frame(
        pd.DataFrame({"observation_date": ["2025-01-31"], "value": [1.0]})
    )
    assert not check_frame_contract(frame, require_available_date=True).ok
    with pytest.raises(FrameContractError):
        assert_frame_contract(frame, require_available_date=True)


def test_contract_reports_every_violation_at_once():
    # Duplicate observation dates AND a missing value column AND no basis label.
    frame = pd.DataFrame({"observation_date": ["2025-01-31", "2025-01-31"]})
    report = check_frame_contract(frame)

    assert not report.ok
    # Both problems surface in one pass, so a caller fixes everything in one go.
    assert any("duplicate observation_date" in e for e in report.errors)
    missing = [e for e in report.errors if "missing required column" in e]
    assert len(missing) == 1
    assert "availability_basis" in missing[0] and "value" in missing[0]


# --------------------------------------------------------------------------- #
# Provider conformance
# --------------------------------------------------------------------------- #


def test_every_default_provider_implements_the_interface():
    from macro_regime_quant.data.registry import default_provider_registry

    for name, provider in default_provider_registry().items():
        assert isinstance(provider, DataProvider), name


def test_csv_provider_output_satisfies_the_contract(tmp_path: Path):
    _write_csv(
        tmp_path / DAILY_SPEC.symbol,
        pd.DataFrame(
            {
                "observation_date": ["2025-01-31", "2025-02-28"],
                "available_date": ["2025-02-15", "2025-03-15"],
                "value": [5.0, 5.2],
            }
        ),
    )
    out = CsvProvider(root=tmp_path).fetch(DAILY_SPEC)
    normalized = normalize_observation_frame(out, key=DAILY_SPEC.key)
    assert_frame_contract(normalized)


def test_csv_provider_without_available_date_is_labeled_unknown(tmp_path: Path):
    _write_csv(
        tmp_path / DAILY_SPEC.symbol,
        pd.DataFrame({"observation_date": ["2025-01-31"], "value": [5.0]}),
    )
    out = CsvProvider(root=tmp_path).fetch(DAILY_SPEC)
    normalized = normalize_observation_frame(out, key=DAILY_SPEC.key)

    # Not an error, but not usable under official_release_only either.
    assert_frame_contract(normalized)
    assert set(normalized["availability_basis"]) == {"unknown"}


def test_normalizer_is_idempotent(tmp_path: Path):
    _write_csv(
        tmp_path / DAILY_SPEC.symbol,
        pd.DataFrame(
            {
                "observation_date": ["2025-02-28", "2025-01-31"],
                "available_date": ["2025-03-15", "2025-02-15"],
                "value": [5.2, 5.0],
            }
        ),
    )
    once = normalize_observation_frame(CsvProvider(root=tmp_path).fetch(DAILY_SPEC))
    twice = normalize_observation_frame(once)
    pd.testing.assert_frame_equal(once, twice)


def test_availability_bases_are_ordered_from_strong_to_weak():
    assert {"official_release", "official_schedule"} <= AVAILABILITY_BASES
    assert "unverified" in AVAILABILITY_BASES and "unknown" in AVAILABILITY_BASES
