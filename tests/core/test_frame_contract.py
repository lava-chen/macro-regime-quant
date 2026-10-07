"""Contract tests.

Every DataProvider must return the same normalized shape, and that shape must be
honest about what it does not know. These tests are the executable version of the
"no same-period signal execution" rule: a provider that cannot say when a value became
known is labeled per row, and is excluded by the strict availability policies.

Coverage note: the branches guarded here are the *evidence-grade* gates. A typo in
one of them (e.g. accepting a misspelled basis) is the difference between a strict
policy admitting unevidenced data and refusing it, so each is exercised directly
rather than only through happy paths.
"""

from pathlib import Path

import pandas as pd
import pytest
from mrq_core.contracts import (
    AVAILABILITY_BASES,
    EVIDENCED_BASES,
    FrameContractError,
    assert_frame_contract,
    check_frame_contract,
    normalize_observation_frame,
)
from mrq_core.types import SeriesSpec
from mrq_data.providers.base import DataProvider
from mrq_data.providers.csv import CsvProvider

SPEC = SeriesSpec(
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


def _dated_frame(**overrides) -> pd.DataFrame:
    base = {
        "observation_date": ["2025-01-31"],
        "available_date": ["2025-02-15"],
        "value": [1.0],
    }
    base.update(overrides)
    return pd.DataFrame(base)


# --------------------------------------------------------------------------- #
# normalize_observation_frame — provenance labelling (row level)
# --------------------------------------------------------------------------- #


def test_normalize_labels_undated_rows_unknown():
    out = normalize_observation_frame(pd.DataFrame({"observation_date": ["2025-01-31"], "value": [1.0]}))
    assert out.loc[0, "availability_basis"] == "unknown"
    assert "available_date" not in out.columns  # never invented
    assert_frame_contract(out)


def test_normalize_labels_dated_rows_unverified_by_default():
    out = normalize_observation_frame(_dated_frame())
    assert out.loc[0, "availability_basis"] == "unverified"
    assert_frame_contract(out)


def test_normalize_labels_partially_dated_series_row_by_row():
    """Regression: frame-level inference labelled undated rows 'unverified'.

    A series whose first rows have a known publication date and whose later rows
    do not must not hand an availability label to the rows that have no date.
    This has to match CsvProvider.fetch, which already did it per row.
    """

    frame = pd.DataFrame(
        {
            "observation_date": ["2025-01-31", "2025-02-28", "2025-03-31"],
            "available_date": ["2025-02-15", None, None],
            "value": [1.0, 2.0, 3.0],
        }
    )
    out = normalize_observation_frame(frame)
    assert list(out["availability_basis"]) == ["unverified", "unknown", "unknown"]
    assert check_frame_contract(out).ok


def test_normalize_treats_blank_label_as_no_label():
    """A human leaving a cell empty asserts nothing; it must not become evidence."""

    frame = pd.DataFrame(
        {
            "observation_date": ["2025-01-31", "2025-02-28"],
            "available_date": ["2025-02-15", None],
            "value": [1.0, 2.0],
            "availability_basis": ["", ""],
        }
    )
    out = normalize_observation_frame(frame)
    assert list(out["availability_basis"]) == ["unverified", "unknown"]


def test_normalize_matches_csv_provider_row_by_row():
    """The single normalization entry point must agree with the provider path."""

    payload = pd.DataFrame(
        {
            "observation_date": ["2025-01-31", "2025-02-28", "2025-03-31"],
            "available_date": ["2025-02-15", None, None],
            "value": [1.0, 2.0, 3.0],
        }
    )
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        _write_csv(Path(tmp) / SPEC.symbol, payload)
        via_provider = CsvProvider(root=tmp).fetch(SPEC)
    via_normalize = normalize_observation_frame(payload)
    assert list(via_provider["availability_basis"]) == list(via_normalize["availability_basis"])


def test_normalize_preserves_explicit_basis():
    out = normalize_observation_frame(
        _dated_frame(
            availability_basis=["official_release"],
            availability_evidence_url=["https://stats.gov.cn/cpi"],
        )
    )
    assert out.loc[0, "availability_basis"] == "official_release"
    assert_frame_contract(out)


def test_normalize_preserves_explicit_unknown_without_date():
    out = normalize_observation_frame(
        pd.DataFrame(
            {
                "observation_date": ["2025-01-31"],
                "value": [1.0],
                "availability_basis": ["unknown"],
            }
        )
    )
    assert out.loc[0, "availability_basis"] == "unknown"


def test_normalize_sorts_by_observation_date():
    out = normalize_observation_frame(
        pd.DataFrame({"observation_date": ["2025-03-31", "2025-01-31"], "value": [3.0, 1.0]})
    )
    assert list(out["observation_date"]) == [pd.Timestamp("2025-01-31"), pd.Timestamp("2025-03-31")]


def test_normalize_rejects_frame_without_core_columns():
    with pytest.raises(FrameContractError, match="observation_date"):
        normalize_observation_frame(pd.DataFrame({"date": ["2025-01-31"], "v": [1.0]}))


def test_normalize_rejects_unparseable_observation_date():
    with pytest.raises(ValueError):
        normalize_observation_frame(
            pd.DataFrame({"observation_date": ["not-a-date"], "value": [1.0]})
        )


def test_normalize_is_idempotent(tmp_path: Path):
    _write_csv(
        Path(tmp_path) / SPEC.symbol,
        pd.DataFrame(
            {
                "observation_date": ["2025-02-28", "2025-01-31"],
                "available_date": ["2025-03-15", "2025-02-15"],
                "value": [5.2, 5.0],
            }
        ),
    )
    once = normalize_observation_frame(CsvProvider(root=tmp_path).fetch(SPEC))
    twice = normalize_observation_frame(once)
    pd.testing.assert_frame_equal(once, twice)


# --------------------------------------------------------------------------- #
# dated_rows_basis must not manufacture publication evidence
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("basis", sorted(EVIDENCED_BASES))
def test_normalize_rejects_evidenced_default(basis):
    with pytest.raises(FrameContractError, match="asserts publication evidence"):
        normalize_observation_frame(_dated_frame(), dated_rows_basis=basis)


def test_normalize_rejects_unknown_default_label():
    with pytest.raises(FrameContractError, match="not a known basis"):
        normalize_observation_frame(_dated_frame(), dated_rows_basis="official_releas")


def test_normalize_accepts_unevidenced_default():
    out = normalize_observation_frame(_dated_frame(), dated_rows_basis="fixed_lag")
    assert out.loc[0, "availability_basis"] == "fixed_lag"


# --------------------------------------------------------------------------- #
# check_frame_contract — every gate exercised at least once
# --------------------------------------------------------------------------- #


def test_contract_rejects_available_before_observation():
    out = normalize_observation_frame(_dated_frame(available_date=["2025-01-01"]))
    report = check_frame_contract(out)
    assert not report.ok
    assert any("earlier than observation_date" in e for e in report.errors)


def test_contract_rejects_duplicate_observation_dates():
    out = normalize_observation_frame(
        pd.DataFrame({"observation_date": ["2025-01-31", "2025-01-31"], "value": [1.0, 2.0]})
    )
    assert any("duplicate observation_date" in e for e in check_frame_contract(out).errors)


def test_contract_rejects_unparseable_observation_date():
    frame = pd.DataFrame(
        {"observation_date": ["2025-01-31", "garbage"], "value": [1.0, 2.0], "availability_basis": ["unknown", "unknown"]}
    )
    assert any(
        "unparseable" in e for e in check_frame_contract(frame).errors
    )


def test_contract_rejects_non_numeric_value():
    frame = pd.DataFrame(
        {"observation_date": ["2025-01-31"], "value": ["N/A"], "availability_basis": ["unknown"]}
    )
    assert any(
        "non-numeric" in e for e in check_frame_contract(frame).errors
    )


def test_contract_warns_about_unrecognized_extra_columns():
    frame = pd.DataFrame(
        {
            "observation_date": ["2025-01-31"],
            "value": [1.0],
            "availability_basis": ["unknown"],
            "mystery_col": [1],
        }
    )
    report = check_frame_contract(frame)
    assert report.ok
    assert any("mystery_col" in w for w in report.warnings)


def test_contract_rejects_unknown_basis_carrying_a_date():
    out = normalize_observation_frame(_dated_frame(availability_basis=["unknown"]))
    report = check_frame_contract(out)
    assert not report.ok
    assert any("must not carry an available_date" in e for e in report.errors)


def test_contract_rejects_dated_row_without_date():
    frame = pd.DataFrame(
        {
            "observation_date": ["2025-01-31"],
            "available_date": [None],
            "value": [1.0],
            "availability_basis": ["unverified"],
        }
    )
    report = check_frame_contract(frame)
    assert not report.ok
    assert any("require an available_date" in e for e in report.errors)


def test_contract_rejects_invalid_basis_label():
    """The evidence-grade gate itself: a misspelled basis must never pass."""

    frame = _dated_frame(
        availability_basis=["official_releas"],
        availability_evidence_url=["https://stats.gov.cn/cpi"],
    )
    report = check_frame_contract(frame)
    assert not report.ok
    assert any("invalid availability_basis" in e for e in report.errors)


def test_contract_requires_evidence_url_for_evidenced_basis():
    frame = _dated_frame(availability_basis=["official_release"])
    report = check_frame_contract(frame)
    assert not report.ok
    assert any("availability_evidence_url" in e for e in report.errors)


def test_contract_rejects_non_http_evidence_url():
    frame = _dated_frame(
        availability_basis=["official_release"], availability_evidence_url=["see my notes"]
    )
    assert any("http(s) URL" in e for e in check_frame_contract(frame).errors)


def test_contract_accepts_valid_evidence_url():
    frame = _dated_frame(
        availability_basis=["official_release"],
        availability_evidence_url=["https://stats.gov.cn/sj/zxfb/202501.html"],
    )
    assert check_frame_contract(frame).ok


def test_contract_warns_but_does_not_fail_on_unevidenced_basis():
    out = normalize_observation_frame(_dated_frame(), dated_rows_basis="fixed_lag")
    report = check_frame_contract(out)
    assert report.ok
    assert any("indicative" in w for w in report.warnings)


def test_contract_does_not_suppress_pit_checks_after_earlier_errors():
    """A bad basis label must not hide the more important ordering violation."""

    frame = _dated_frame(available_date=["2025-01-01"], availability_basis=["nonsense"])
    report = check_frame_contract(frame)
    assert not report.ok
    assert any("earlier than observation_date" in e for e in report.errors)


def test_contract_require_available_date_rejects_all_empty_column():
    frame = pd.DataFrame(
        {
            "observation_date": ["2025-01-31", "2025-02-28"],
            "available_date": [pd.NaT, pd.NaT],
            "value": [1.0, 2.0],
            "availability_basis": ["unknown", "unknown"],
        }
    )
    report = check_frame_contract(frame, require_available_date=True)
    assert not report.ok
    assert any("usable available_date" in e for e in report.errors)
    assert report.has_available_date_column is True
    assert report.has_available_date is False


def test_contract_can_require_available_date():
    frame = pd.DataFrame(
        {"observation_date": ["2025-01-31"], "value": [1.0], "availability_basis": ["unknown"]}
    )
    assert not check_frame_contract(frame, require_available_date=True).ok
    with pytest.raises(FrameContractError):
        assert_frame_contract(frame, require_available_date=True)


def test_contract_reports_multiple_violations_at_once():
    frame = pd.DataFrame({"observation_date": ["2025-01-31", "2025-01-31"]})
    report = check_frame_contract(frame)
    assert not report.ok
    assert any("duplicate observation_date" in e for e in report.errors)
    missing = [e for e in report.errors if "missing required column" in e]
    assert len(missing) == 1
    assert "availability_basis" in missing[0] and "value" in missing[0]


# --------------------------------------------------------------------------- #
# Provider conformance — shape of what each adapter actually returns
# --------------------------------------------------------------------------- #


def test_every_default_provider_implements_the_interface():
    from mrq_data.registry import default_provider_registry

    for name, provider in default_provider_registry().items():
        assert isinstance(provider, DataProvider), name


@pytest.mark.parametrize("provider_name", ["fred", "yahoo", "csv"])
def test_provider_output_satisfies_the_contract(provider_name, tmp_path: Path, monkeypatch):
    """Every adapter's real output shape must normalize into a valid frame.

    FRED and Yahoo return no availability columns at all; this is what proves the
    normalizer copes with a provider that cannot speak the provenance vocabulary.
    """

    raw = pd.DataFrame(
        {"observation_date": ["2025-01-31", "2025-02-28"], "value": [100.0, 101.0]}
    )

    if provider_name == "fred":
        import mrq_data.providers.fred as fred_mod

        monkeypatch.setattr(fred_mod.pd, "read_csv", lambda *a, **k: raw.copy())
        from mrq_data.providers.fred import FredProvider

        out = FredProvider().fetch(SPEC)
    elif provider_name == "yahoo":

        class _FakeYf:
            @staticmethod
            def download(symbol, **kwargs):
                frame = raw.copy().set_index("observation_date")
                frame.index = pd.DatetimeIndex(frame.index)
                return pd.DataFrame({"Close": frame["value"]})

        monkeypatch.setitem(
            __import__("sys").modules, "yfinance", type("M", (), {"download": _FakeYf.download})
        )
        from mrq_data.providers.yahoo import YahooProvider

        out = YahooProvider().fetch(SPEC)
    else:
        _write_csv(
            Path(tmp_path) / SPEC.symbol,
            raw.assign(available_date=["2025-02-15", "2025-03-15"]),
        )
        out = CsvProvider(root=tmp_path).fetch(SPEC)

    normalized = normalize_observation_frame(out, key=SPEC.key)
    assert_frame_contract(normalized)
    # Nothing was invented: a provider that cannot date its values says so.
    if provider_name in {"fred", "yahoo"}:
        assert "available_date" not in normalized.columns
        assert set(normalized["availability_basis"]) == {"unknown"}


def test_csv_provider_output_satisfies_the_contract(tmp_path: Path):
    _write_csv(
        Path(tmp_path) / SPEC.symbol,
        pd.DataFrame(
            {
                "observation_date": ["2025-01-31", "2025-02-28"],
                "available_date": ["2025-02-15", "2025-03-15"],
                "value": [5.0, 5.2],
            }
        ),
    )
    normalized = normalize_observation_frame(CsvProvider(root=tmp_path).fetch(SPEC))
    assert_frame_contract(normalized)


def test_csv_provider_without_available_date_is_labeled_unknown(tmp_path: Path):
    _write_csv(
        Path(tmp_path) / SPEC.symbol,
        pd.DataFrame({"observation_date": ["2025-01-31"], "value": [5.0]}),
    )
    normalized = normalize_observation_frame(CsvProvider(root=tmp_path).fetch(SPEC))
    assert_frame_contract(normalized)
    assert set(normalized["availability_basis"]) == {"unknown"}


def test_availability_bases_contains_every_known_label():
    assert EVIDENCED_BASES <= AVAILABILITY_BASES
    assert {"unverified", "unknown", "fixed_lag"} <= AVAILABILITY_BASES
    # The evidenced labels are a strict subset, not a parallel set.
    assert EVIDENCED_BASES.isdisjoint(AVAILABILITY_BASES - EVIDENCED_BASES)
