from pathlib import Path

import pytest

from macro_regime_quant.data.catalog import (
    CatalogError,
    check_catalog_consistency,
    load_catalog,
)
from macro_regime_quant.data.models import SeriesSpec

VALID_ENTRY = """
series:
  cn_cpi:
    provider: csv
    symbol: china/cpi_yoy.csv
    kind: macro
    frequency: monthly
    release_lag_days: 10
    notes: "China CPI year-on-year."
"""


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "catalog.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_load_catalog_roundtrip(tmp_path: Path):
    specs = load_catalog(_write(tmp_path, VALID_ENTRY))
    assert set(specs) == {"cn_cpi"}
    spec = specs["cn_cpi"]
    assert isinstance(spec, SeriesSpec)
    assert spec.provider == "csv"
    assert spec.release_lag_days == 10


def test_unknown_field_reports_key_and_suggestion(tmp_path: Path):
    path = _write(
        tmp_path,
        """
series:
  cn_cpi:
    provider: csv
    symbol: china/cpi_yoy.csv
    kind: macro
    frequency: monthly
    releas_lag_days: 10
""",
    )
    with pytest.raises(CatalogError) as exc:
        load_catalog(path)
    message = str(exc.value)
    assert "series.cn_cpi" in message
    assert "releas_lag_days" in message
    assert "release_lag_days" in message


def test_missing_required_fields_are_listed(tmp_path: Path):
    path = _write(tmp_path, "series:\n  cn_cpi:\n    provider: csv\n")
    with pytest.raises(CatalogError) as exc:
        load_catalog(path)
    message = str(exc.value)
    assert "missing required field" in message
    for field in ("symbol", "kind", "frequency"):
        assert field in message


def test_invalid_enum_value_lists_allowed_choices(tmp_path: Path):
    path = _write(
        tmp_path,
        """
series:
  cn_cpi:
    provider: csv
    symbol: x.csv
    kind: macro
    frequency: dailyly
""",
    )
    with pytest.raises(CatalogError) as exc:
        load_catalog(path)
    assert "dailyly" in str(exc.value)
    assert "monthly" in str(exc.value)


def test_negative_release_lag_is_rejected(tmp_path: Path):
    path = _write(
        tmp_path,
        """
series:
  cn_cpi:
    provider: csv
    symbol: x.csv
    kind: macro
    frequency: monthly
    release_lag_days: -5
""",
    )
    with pytest.raises(CatalogError, match="look-ahead"):
        load_catalog(path)


def test_unknown_top_level_section_is_rejected(tmp_path: Path):
    path = _write(tmp_path, "series: {}\nseires: {}\n")
    with pytest.raises(CatalogError, match="seires"):
        load_catalog(path)


def test_missing_file_raises_catalog_error(tmp_path: Path):
    with pytest.raises(CatalogError, match="not found"):
        load_catalog(tmp_path / "nope.yaml")


def test_empty_catalog_is_allowed(tmp_path: Path):
    assert load_catalog(_write(tmp_path, "series: {}\n")) == {}


def test_shipped_catalog_loads_clean():
    """The committed catalog must stay valid and internally consistent."""

    specs = load_catalog(Path(__file__).resolve().parents[1] / "config" / "data_catalog.yaml")
    assert specs, "shipped catalog must not be empty"
    assert check_catalog_consistency(specs) == []


def test_consistency_flags_duplicate_symbols():
    shared = {"provider": "fred", "symbol": "CPI", "kind": "macro", "frequency": "monthly"}
    specs = {
        "a": SeriesSpec(key="a", **shared),
        "b": SeriesSpec(key="b", **shared),
    }
    warnings = check_catalog_consistency(specs)
    assert any("multiple keys" in w for w in warnings)


def test_consistency_flags_zero_lag_on_slow_series():
    specs = {
        "a": SeriesSpec(
            key="a", provider="csv", symbol="x.csv", kind="macro", frequency="monthly"
        )
    }
    assert any("release_lag_days=0" in w for w in check_catalog_consistency(specs))
