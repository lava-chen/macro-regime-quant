from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
import yaml
from mrq_data import china_watch
from mrq_data.china_watch import WATCH_COLUMNS, append_live_records, read_watch_ledger


def _live_record(
    *,
    series_id: str = "cn_cpi",
    period: str = "2024-01",
    value: float = 0.8,
    available_date: str = "2024-02-08",
    basis: str = "official_release",
) -> dict[str, object]:
    return {
        "series_id": series_id,
        "observation_period": period,
        "observation_date": "2024-01-31",
        "available_date": available_date,
        "availability_basis": basis,
        "retrieved_at": "2026-10-09T08:00:00+08:00",
        "value": value,
        "unit": "percent_yoy",
        "source_value_url": "https://www.stats.gov.cn/release/value.html",
        "availability_evidence_url": (
            "https://www.stats.gov.cn/release/value.html" if basis == "official_release" else ""
        ),
        "vintage": available_date if basis == "official_release" else "unknown",
        "quality_flags": "[]",
        "source_title": "居民消费价格数据",
    }


def _seed_one_cpi(raw_dir: Path) -> str:
    raw_dir.mkdir(parents=True)
    csv_path = raw_dir / "cpi_yoy.csv"
    csv_path.write_text(
        "observation_date,observation_period,available_date,availability_basis,"
        "availability_evidence_url,source_value_url,value\n"
        "2024-01-31,2024-01,2024-02-08,official_release,"
        "https://www.stats.gov.cn/release/value.html,"
        "https://www.stats.gov.cn/release/value.html,0.8\n",
        encoding="utf-8",
    )
    (raw_dir / "cpi_yoy.meta.yaml").write_text(
        yaml.safe_dump(
            {
                "downloaded_at": "2026-10-08T12:00:00+08:00",
                "unit": "percent_yoy",
            }
        ),
        encoding="utf-8",
    )
    return csv_path.read_text(encoding="utf-8")


def test_append_is_idempotent_and_keeps_revision_vintages(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    original_csv = _seed_one_cpi(raw_dir)
    ledger_path = raw_dir / china_watch.LEDGER_NAME

    first, events = append_live_records(ledger_path, [_live_record()], raw_dir=raw_dir)
    assert first == []
    assert events == []
    assert len(read_watch_ledger(ledger_path)) == 1
    assert b"\r\n" not in ledger_path.read_bytes()

    second, _ = append_live_records(ledger_path, [_live_record()], raw_dir=raw_dir)
    assert second == []
    assert len(read_watch_ledger(ledger_path)) == 1

    revised, events = append_live_records(ledger_path, [_live_record(value=1.0)], raw_dir=raw_dir)
    assert len(revised) == 1
    assert events[0]["event"] == "historical_revision"
    ledger = read_watch_ledger(ledger_path)
    assert ledger["value"].astype(float).tolist() == [0.8, 1.0]
    assert ledger.iloc[0]["vintage"] == "2024-02-08"
    assert ledger.iloc[1]["vintage"] == pd.Timestamp.now(tz="Asia/Shanghai").date().isoformat()
    assert original_csv == (raw_dir / "cpi_yoy.csv").read_text(encoding="utf-8")


def test_unknown_release_date_is_retained_but_not_guessed(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    ledger_path = raw_dir / china_watch.LEDGER_NAME
    raw_dir.mkdir()
    row = _live_record(
        series_id="cn_core_cpi",
        period="2026-08",
        available_date="unknown",
        basis="unknown",
    )
    added, _ = append_live_records(ledger_path, [row], raw_dir=raw_dir)
    assert len(added) == 1
    saved = read_watch_ledger(ledger_path).iloc[0]
    assert saved["available_date"] == "unknown"
    assert saved["vintage"] == "unknown"
    assert "release_date_unknown" in json.loads(saved["quality_flags"])


def test_official_release_without_evidence_fails_contract(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    row = _live_record()
    row["availability_evidence_url"] = ""
    with pytest.raises(ValueError, match="official_release rows require"):
        append_live_records(raw_dir / china_watch.LEDGER_NAME, [row], raw_dir=raw_dir)


def test_collection_failure_does_not_advance_success_checkpoint(tmp_path: Path, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("official source temporarily unavailable")

    monkeypatch.setattr(china_watch, "collect_nbs_snapshots", fail)
    monkeypatch.setattr(china_watch, "collect_pboc_snapshots", fail)
    raw_dir = tmp_path / "raw"
    reports = tmp_path / "reports"
    audit = china_watch.run_china_data_watch(
        raw_dir=raw_dir,
        report_dir=reports,
        start_year=2026,
        end_year=2026,
    )

    state = json.loads((raw_dir / china_watch.STATE_NAME).read_text(encoding="utf-8"))
    assert audit["status"] == "failed"
    assert len(audit["errors"]) == 2
    assert "last_successful_at" not in state
    assert state["last_failure_count"] == 1
    assert state["pending_scan_window"] == {
        "start_year": 2026,
        "end_year": 2026,
        "full_rescan": False,
    }
    assert (reports / "collection_audit.json").exists()


def test_failed_window_is_reused_on_next_run_and_cleared_after_success(tmp_path: Path, monkeypatch):
    raw_dir = tmp_path / "raw"
    reports = tmp_path / "reports"

    def fail(*args, **kwargs):
        raise RuntimeError("temporary source outage")

    monkeypatch.setattr(china_watch, "collect_nbs_snapshots", fail)
    monkeypatch.setattr(china_watch, "collect_pboc_snapshots", fail)
    first = china_watch.run_china_data_watch(
        raw_dir=raw_dir,
        report_dir=reports,
        start_year=2022,
        end_year=2024,
    )
    assert first["status"] == "failed"

    seen_windows: list[tuple[int, int]] = []

    def succeed(output_dir, *, start_year, end_year, max_workers):
        seen_windows.append((start_year, end_year))
        return {
            "candidate_articles": 1,
            "fetch_errors": [],
            "unparsed_candidates": [],
            "series": {},
            "downloaded_at": "2026-10-09T12:00:00+08:00",
        }

    monkeypatch.setattr(china_watch, "collect_nbs_snapshots", succeed)
    monkeypatch.setattr(china_watch, "collect_pboc_snapshots", succeed)
    second = china_watch.run_china_data_watch(raw_dir=raw_dir, report_dir=reports)

    state = json.loads((raw_dir / china_watch.STATE_NAME).read_text(encoding="utf-8"))
    assert seen_windows == [(2022, 2024), (2022, 2024)]
    assert second["status"] == "success"
    assert "pending_scan_window" not in state


def test_watch_contract_includes_required_provenance_fields():
    assert WATCH_COLUMNS[:11] == [
        "series_id",
        "observation_period",
        "observation_date",
        "available_date",
        "availability_basis",
        "retrieved_at",
        "value",
        "unit",
        "source_value_url",
        "availability_evidence_url",
        "vintage",
    ]
