"""Pipeline-level point-in-time enforcement tests.

The contract in `data/frame.py` is only worth anything if it is actually on the
path data travels to reach the panel. These tests pin that down with deliberately
unsafe fixtures, because a backtest that silently ingests look-ahead data is
exactly the failure this project exists to prevent.
"""

from pathlib import Path

import mrq_engines.pipeline as pipeline_mod
import pandas as pd
import pytest
import yaml
from mrq_core.contracts import FrameContractError
from mrq_data.providers.csv import CsvProvider
from mrq_engines.pipeline import load_monthly_panel

CATALOG = """
series:
  cn_demo:
    provider: csv
    symbol: china/demo.csv
    kind: macro
    frequency: monthly
    release_lag_days: 15
"""

CLEAN = pd.DataFrame(
    {
        "observation_date": ["2024-11-30", "2024-12-31", "2025-01-31"],
        "available_date": ["2024-12-15", "2025-01-15", "2025-02-15"],
        "value": [4.0, 4.5, 5.0],
        "availability_basis": ["official_release"] * 3,
        "availability_evidence_url": ["https://stats.gov.cn/a"] * 3,
    }
)

# First row claims it was knowable *before* the period it describes.
LOOK_AHEAD = CLEAN.copy()
LOOK_AHEAD.loc[0, "available_date"] = "2024-11-01"


@pytest.fixture(autouse=True)
def _point_csv_provider_at_tmp_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The default registry hardcodes root='data/raw'; point it at the fixture dir."""

    monkeypatch.setattr(
        pipeline_mod, "default_provider_registry", lambda: {"csv": CsvProvider(root=tmp_path)}
    )


@pytest.fixture
def catalog(tmp_path: Path) -> Path:
    path = tmp_path / "catalog.yaml"
    path.write_text(CATALOG, encoding="utf-8")
    return path


SNAPSHOT_META = {
    "series_key": "cn_demo",
    "source_name": "fixture",
    "source_url": "https://stats.gov.cn/demo",
    "frequency": "monthly",
    "unit": "percent_yoy",
    "downloaded_at": "2025-02-01T00:00:00Z",
    "reported_as": "yoy",
    "revision_policy": "as_published",
    "availability_lag_days": 15,
}


def _write_snapshot(root: Path, frame: pd.DataFrame, *, with_meta: bool = True) -> None:
    path = root / "china" / "demo.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    if with_meta:
        path.with_suffix(".meta.yaml").write_text(yaml.safe_dump(SNAPSHOT_META), encoding="utf-8")


def test_pipeline_rejects_look_ahead_under_default_policy(tmp_path: Path, catalog: Path):
    """Regression: the default 'all' policy used to let look-ahead rows through.

    Only csv + a strict policy ran `validate_snapshot`, so with the default
    policy a series whose available_date preceded its observation_date reached
    the panel untouched.
    """

    _write_snapshot(tmp_path, LOOK_AHEAD)
    with pytest.raises(FrameContractError, match="earlier than observation_date"):
        load_monthly_panel(catalog, ["cn_demo"], start="2024-01-01", end="2025-12-31")


def test_pipeline_accepts_clean_data(tmp_path: Path, catalog: Path):
    _write_snapshot(tmp_path, CLEAN)
    panel = load_monthly_panel(catalog, ["cn_demo"], start="2024-01-01", end="2025-12-31")
    assert not panel.empty
    assert "cn_demo" in panel.columns


def test_pipeline_rejects_evidenced_basis_without_evidence_url(tmp_path: Path, catalog: Path):
    stripped = CLEAN.drop(columns=["availability_evidence_url"])
    _write_snapshot(tmp_path, stripped)
    with pytest.raises(FrameContractError, match="availability_evidence_url"):
        load_monthly_panel(catalog, ["cn_demo"], start="2024-01-01", end="2025-12-31")


def test_pipeline_rejects_duplicate_observation_dates(tmp_path: Path, catalog: Path):
    duplicated = pd.concat([CLEAN, CLEAN.tail(1)], ignore_index=True)
    _write_snapshot(tmp_path, duplicated)
    with pytest.raises(FrameContractError, match="duplicate observation_date"):
        load_monthly_panel(catalog, ["cn_demo"], start="2024-01-01", end="2025-12-31")


def test_strict_policy_rejects_unevidenced_rows(tmp_path: Path, catalog: Path):
    unevidenced = CLEAN.copy()
    unevidenced["availability_basis"] = "fixed_lag"
    unevidenced = unevidenced.drop(columns=["availability_evidence_url"])
    _write_snapshot(tmp_path, unevidenced, with_meta=True)
    # Under the strictest policy those rows must not survive into the panel.
    panel = load_monthly_panel(
        catalog, ["cn_demo"], start="2024-01-01", end="2025-12-31",
        availability_policy="official_release_only",
    )
    assert panel.empty or panel["cn_demo"].isna().all()
