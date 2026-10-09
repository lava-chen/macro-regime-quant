from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from mrq_data.china_watch import WATCH_COLUMNS, append_live_records
from mrq_engines.china_macro_report import (
    build_china_macro_report,
    build_vintage_aware_monthly_panel,
)

ROOT = Path(__file__).resolve().parents[2]


def _row(value: float, vintage: str) -> dict[str, str | float]:
    return {
        "series_id": "cn_cpi",
        "observation_period": "2024-01",
        "observation_date": "2024-01-31",
        "available_date": "2024-02-08",
        "availability_basis": "official_release",
        "retrieved_at": f"{vintage}T12:00:00+08:00",
        "value": value,
        "unit": "percent_yoy",
        "source_value_url": "https://www.stats.gov.cn/release/cpi.html",
        "availability_evidence_url": "https://www.stats.gov.cn/release/cpi.html",
        "vintage": vintage,
        "quality_flags": "[]",
        "source_title": "CPI",
    }


def test_revision_only_enters_the_monthly_panel_at_its_vintage():
    ledger = pd.DataFrame(
        [_row(0.8, "2024-02-08"), _row(1.0, "2025-01-15")],
        columns=WATCH_COLUMNS,
    )
    panel = build_vintage_aware_monthly_panel(ledger, as_of="2025-02-01")

    assert panel.loc["2024-12-31", "cn_cpi"] == 0.8
    assert panel.loc["2025-01-31", "cn_cpi"] == 1.0


def test_historical_report_returns_insufficient_data_instead_of_a_regime(tmp_path: Path):
    ledger = pd.DataFrame([_row(0.8, "2024-02-08")], columns=WATCH_COLUMNS)
    ledger_path = tmp_path / "china_macro_snapshots.csv"
    ledger.to_csv(ledger_path, index=False)

    report = build_china_macro_report(
        ledger_path=ledger_path,
        factor_config_path=ROOT / "config/factors.yaml",
        output_dir=tmp_path / "report",
        as_of="2024-12-31",
        min_z_history=12,
    )

    assert report["macro_state"]["status"] == "insufficient_data"
    assert report["macro_state"]["code"] is None
    assert report["factors"]["real_rate"]["status"] == "insufficient_data"
    saved = json.loads((tmp_path / "report/latest.json").read_text(encoding="utf-8"))
    assert saved["pit_policy"]["vintage"].startswith("At each month end")


def test_pr13_snapshot_smoke_builds_evidenced_report(tmp_path: Path):
    ledger_path = tmp_path / "china_macro_snapshots.csv"
    appended, _ = append_live_records(
        ledger_path,
        [],
        raw_dir=ROOT / "data/raw/china",
    )
    assert appended == []

    report = build_china_macro_report(
        ledger_path=ledger_path,
        factor_config_path=ROOT / "config/factors.yaml",
        output_dir=tmp_path / "report",
        min_z_history=36,
    )

    assert report["coverage"]["official_release_rows"] == 863
    assert report["coverage"]["official_release_evidence_rate"] == 1.0
    assert report["metrics"]["cn_retail_sales"]["observation_period"] == "2026-06"
    assert report["metrics"]["cn_retail_sales"]["value"] == 1.3
    assert report["metrics"]["cn_retail_sales"]["reported_as"] == "percent_yoy"
    assert report["metrics"]["cn_retail_sales_ytd"]["observation_period"] == "2026-01/2026-08"
    assert report["metrics"]["cn_retail_sales_ytd"]["value"] == 1.1
    assert report["metrics"]["cn_retail_sales_ytd"]["reported_as"] == "cumulative_ytd_yoy"
    assert "cn_retail_sales" in {item["series_id"] for item in report["quality"]["stale_series"]}
    assert "cn_tsf_stock_yoy" in {item["series_id"] for item in report["quality"]["stale_series"]}
    assert report["factors"]["real_rate"]["status"] == "insufficient_data"
    assert report["macro_state"]["status"] in {"available", "insufficient_data"}
    for metric in report["metrics"].values():
        if metric["status"] == "available":
            assert metric["available_date"]
            assert metric["availability_evidence_url"].startswith("https://")
    assert (tmp_path / "report/latest.json").is_file()
    assert (tmp_path / "report/latest.md").is_file()
    markdown = (tmp_path / "report/latest.md").read_text(encoding="utf-8")
    assert "## 因子贡献" in markdown
    assert "固定资产投资累计同比" in markdown
