"""Append-only, provenance-preserving China macro collection utilities."""

from __future__ import annotations

import csv
import json
import re
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

from .china_harvest import NBS_SERIES, PBOC_SERIES, collect_nbs_snapshots, collect_pboc_snapshots

SHANGHAI = ZoneInfo("Asia/Shanghai")
LEDGER_NAME = "china_macro_snapshots.csv"
STATE_NAME = "china_macro_watch_state.json"
WATCH_SERIES = {**NBS_SERIES, **PBOC_SERIES}
WATCH_SERIES["cn_10y"] = {
    "series_key": "cn_10y",
    "filename": "10y_government_yield.csv",
    "unit": "percent",
    "reported_as": "yield_percent",
}
WATCH_COLUMNS = [
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
    "quality_flags",
    "source_title",
]
ANOMALY_LIMITS = {
    "cn_pmi_new_orders": 8.0,
    "cn_cpi": 3.0,
    "cn_core_cpi": 3.0,
    "cn_ppi": 5.0,
    "cn_industrial_production": 8.0,
    "cn_retail_sales": 8.0,
    "cn_retail_sales_ytd": 8.0,
    "cn_fixed_asset_investment": 8.0,
    "cn_m1": 10.0,
    "cn_m2": 10.0,
    "cn_tsf_stock_yoy": 10.0,
}
METHODOLOGY_TERMS = re.compile(r"统计口径|统计范围|统计制度|口径调整|修订|调整", re.IGNORECASE)


def _now() -> datetime:
    return datetime.now(SHANGHAI).replace(microsecond=0)


def _iso_date(value: Any) -> str:
    if (
        value is None
        or pd.isna(value)
        or str(value).strip().lower()
        in {
            "",
            "nan",
            "nat",
            "unknown",
        }
    ):
        return "unknown"
    return pd.Timestamp(value).date().isoformat()


def _iso_timestamp(value: Any) -> str:
    if value is None or pd.isna(value) or str(value).strip().lower() in {"", "unknown"}:
        return "unknown"
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize(SHANGHAI)
    else:
        timestamp = timestamp.tz_convert(SHANGHAI)
    return timestamp.isoformat(timespec="seconds")


def _quality_flags(row: dict[str, Any], *, seeded: bool = False) -> list[str]:
    flags: list[str] = []
    if row.get("availability_basis") == "official_release" and row.get("availability_evidence_url"):
        flags.append("release_date_has_official_evidence")
    else:
        flags.append("release_date_unknown")
    period = str(row.get("observation_period", ""))
    if "/" in period:
        flags.append("multi_month_period")
    if re.fullmatch(r"\d{4}-01/\d{4}-02", period):
        flags.append("joint_period_release")
    if row.get("series_id") in {"cn_fixed_asset_investment", "cn_retail_sales_ytd"}:
        flags.append("cumulative_ytd_yoy_as_reported")
    if METHODOLOGY_TERMS.search(str(row.get("source_title", ""))):
        flags.append("methodology_change_review")
    if seeded:
        flags.append("seeded_from_frozen_pr13_snapshot")
    return flags


def _validate_record(record: dict[str, Any]) -> None:
    missing = [name for name in WATCH_COLUMNS if name not in record]
    if missing:
        raise ValueError(f"China watch row missing required fields: {missing}")
    if not str(record["series_id"]).strip() or not str(record["observation_period"]).strip():
        raise ValueError("series_id and observation_period must be populated")
    if not str(record["unit"]).strip():
        raise ValueError(f"{record['series_id']}: unit must be populated")
    if not str(record["source_value_url"]).startswith(("https://", "http://")):
        raise ValueError(f"{record['series_id']}: source_value_url must be an HTTP(S) URL")
    basis = str(record["availability_basis"])
    release_date = _iso_date(record["available_date"])
    evidence = str(record["availability_evidence_url"] or "")
    if basis == "official_release":
        if release_date == "unknown" or not evidence.startswith(("https://", "http://")):
            raise ValueError("official_release rows require a proven date and evidence URL")
    elif basis == "unknown" and release_date != "unknown":
        raise ValueError("unknown availability rows must not carry a release date")
    elif basis != "unknown" and release_date == "unknown":
        raise ValueError(f"{basis} rows require available_date")
    if str(record["vintage"]) != "unknown":
        pd.Timestamp(record["vintage"])
    if str(record["retrieved_at"]) != "unknown":
        pd.Timestamp(record["retrieved_at"])
    try:
        flags = json.loads(str(record["quality_flags"]))
    except json.JSONDecodeError as exc:
        raise ValueError("quality_flags must be a JSON array") from exc
    if not isinstance(flags, list):
        raise TypeError("quality_flags must be a JSON array")
    if pd.isna(pd.to_numeric(record["value"], errors="coerce")):
        raise ValueError(f"{record['series_id']}: value must be numeric")


def _legacy_records(raw_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for spec in WATCH_SERIES.values():
        csv_path = raw_dir / spec["filename"]
        if not csv_path.exists():
            continue
        meta_path = csv_path.with_suffix(".meta.yaml")
        if not meta_path.exists():
            raise FileNotFoundError(f"Frozen China snapshot has no metadata sidecar: {meta_path}")
        metadata = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
        retrieved_at = _iso_timestamp(metadata.get("downloaded_at"))
        frame = pd.read_csv(csv_path, dtype={"observation_period": "string"}).fillna("")
        for row in frame.to_dict(orient="records"):
            row_basis = str(row.get("availability_basis") or "unknown")
            available = _iso_date(row.get("available_date"))
            if row_basis == "unknown":
                available = "unknown"
            record: dict[str, Any] = {
                "series_id": spec["series_key"],
                "observation_period": str(
                    row.get("observation_period") or _iso_date(row["observation_date"])[:7]
                ),
                "observation_date": _iso_date(row["observation_date"]),
                "available_date": available,
                "availability_basis": row_basis,
                "retrieved_at": retrieved_at,
                "value": float(row["value"]),
                "unit": str(metadata.get("unit") or spec["unit"]),
                "source_value_url": str(row.get("source_value_url", "")),
                "availability_evidence_url": str(row.get("availability_evidence_url", "")),
                # PR #13 freezes each original release; its evidenced publication date is
                # the historical vintage. Later detected edits receive a conservative
                # first-observed vintage in append_live_records().
                "vintage": available,
                "source_title": str(row.get("source_title", "")),
            }
            record["quality_flags"] = json.dumps(
                _quality_flags(record, seeded=True), ensure_ascii=False
            )
            _validate_record(record)
            records.append(record)
    return records


def read_watch_ledger(path: str | Path) -> pd.DataFrame:
    ledger_path = Path(path)
    if not ledger_path.exists():
        return pd.DataFrame(columns=WATCH_COLUMNS)
    frame = pd.read_csv(ledger_path, dtype=str, keep_default_na=False)
    missing = sorted(set(WATCH_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError(f"China macro ledger missing columns: {missing}")
    return frame[WATCH_COLUMNS]


def append_live_records(
    ledger_path: str | Path,
    records: list[dict[str, Any]],
    *,
    raw_dir: str | Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Append new releases/revisions without replacing any previously stored row."""

    ledger = Path(ledger_path)
    legacy = _legacy_records(Path(raw_dir)) if not ledger.exists() else []
    if legacy:
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=WATCH_COLUMNS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(legacy)

    existing = read_watch_ledger(ledger)
    by_period: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in existing.to_dict(orient="records"):
        by_period.setdefault((row["series_id"], row["observation_period"]), []).append(row)

    appended: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    now = _now()
    for source in records:
        record = {key: source.get(key, "") for key in WATCH_COLUMNS}
        record["available_date"] = _iso_date(record["available_date"])
        record["observation_date"] = _iso_date(record["observation_date"])
        record["retrieved_at"] = _iso_timestamp(record["retrieved_at"] or now.isoformat())
        prior = by_period.get((str(record["series_id"]), str(record["observation_period"])), [])
        latest = max(
            prior,
            key=lambda item: (
                item["vintage"] if item["vintage"] != "unknown" else "0000-00-00",
                item["retrieved_at"],
            ),
            default=None,
        )
        flags = _quality_flags(record)
        if latest is not None:
            value_changed = abs(float(latest["value"]) - float(record["value"])) > 1e-12
            date_changed = latest["available_date"] != record["available_date"]
            provenance_changed = any(
                latest[key] != str(record[key])
                for key in ("source_value_url", "availability_evidence_url")
            )
            if not (value_changed or date_changed or provenance_changed):
                continue
            record["vintage"] = now.date().isoformat()
            flags.append("historical_revision" if value_changed else "provenance_update")
            event_type = "historical_revision" if value_changed else "provenance_update"
            event = {
                "event": event_type,
                "series_id": record["series_id"],
                "observation_period": record["observation_period"],
                "old_value": float(latest["value"]),
                "new_value": float(record["value"]),
                "old_vintage": latest["vintage"],
                "new_vintage": record["vintage"],
            }
        else:
            record["vintage"] = (
                record["available_date"]
                if record["availability_basis"] == "official_release"
                else "unknown"
            )
            event_type = "new_release"
            event = {
                "event": event_type,
                "series_id": record["series_id"],
                "observation_period": record["observation_period"],
                "value": float(record["value"]),
                "vintage": record["vintage"],
            }
        record["quality_flags"] = json.dumps(flags, ensure_ascii=False)
        _validate_record(record)
        appended.append(record)
        events.append(event)
        by_period.setdefault((record["series_id"], record["observation_period"]), []).append(record)

    if appended:
        ledger.parent.mkdir(parents=True, exist_ok=True)
        write_header = not ledger.exists() or ledger.stat().st_size == 0
        with ledger.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=WATCH_COLUMNS, lineterminator="\n")
            if write_header:
                writer.writeheader()
            writer.writerows(appended)
    return appended, events


def _records_from_stage(stage: Path, retrieved_at: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for spec in WATCH_SERIES.values():
        csv_path = stage / spec["filename"]
        if not csv_path.exists():
            continue
        metadata_path = csv_path.with_suffix(".meta.yaml")
        metadata = (
            yaml.safe_load(metadata_path.read_text(encoding="utf-8")) or {}
            if metadata_path.exists()
            else {}
        )
        source_retrieved_at = _iso_timestamp(metadata.get("downloaded_at") or retrieved_at)
        frame = pd.read_csv(csv_path, dtype={"observation_period": "string"}).fillna("")
        for row in frame.to_dict(orient="records"):
            basis = str(row.get("availability_basis") or "unknown")
            available = _iso_date(row.get("available_date"))
            if basis == "unknown":
                available = "unknown"
            record = {
                "series_id": spec["series_key"],
                "observation_period": str(
                    row.get("observation_period") or _iso_date(row["observation_date"])[:7]
                ),
                "observation_date": _iso_date(row["observation_date"]),
                "available_date": available,
                "availability_basis": basis,
                "retrieved_at": source_retrieved_at,
                "value": float(row["value"]),
                "unit": spec["unit"],
                "source_value_url": str(row.get("source_value_url", "")),
                "availability_evidence_url": str(row.get("availability_evidence_url", "")),
                "vintage": available,
                "source_title": str(row.get("source_title", "")),
            }
            record["quality_flags"] = json.dumps(_quality_flags(record), ensure_ascii=False)
            records.append(record)
    return records


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _summarize_collector(source: str, result: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    errors = [
        f"{source}: {item.get('error', 'fetch failed')}" for item in result.get("fetch_errors", [])
    ]
    unparsed = result.get("unparsed_candidates", [])
    if unparsed:
        errors.append(
            f"{source}: {len(unparsed)} official release candidate(s) could not be parsed"
        )
    if not int(result.get("candidate_articles", 0)):
        errors.append(
            f"{source}: no official release candidates found; check archive pagination and parsing"
        )
    summary = {
        "listing_pages_scanned": result.get("listing_pages_scanned", 0),
        "candidate_articles": result.get("candidate_articles", 0),
        "fetch_error_count": len(result.get("fetch_errors", [])),
        "unparsed_candidate_count": len(unparsed),
        "series": result.get("series", {}),
        "downloaded_at": result.get("downloaded_at"),
    }
    return summary, errors


def _quality_audit(
    ledger: pd.DataFrame,
    events: list[dict[str, Any]],
    as_of: str,
) -> dict[str, Any]:
    known = ledger.copy()
    if known.empty:
        return {
            "new_releases": [],
            "historical_revisions": [],
            "provenance_updates": [],
            "stale_series": [],
            "missing_periods": [],
            "unusual_publication_lags": [],
            "large_changes_for_review": [],
            "methodology_change_reviews": [],
            "unknown_release_dates": 0,
        }
    known["_available"] = pd.to_datetime(known["available_date"], errors="coerce")
    known["_vintage"] = pd.to_datetime(known["vintage"], errors="coerce")
    cutoff = pd.Timestamp(as_of)
    latest = known[
        known["_available"].notna()
        & known["_vintage"].notna()
        & (known["_available"] <= cutoff)
        & (known["_vintage"] <= cutoff)
    ].copy()
    latest = latest.sort_values(["series_id", "observation_period", "_vintage", "retrieved_at"])
    latest = latest.drop_duplicates(["series_id", "observation_period"], keep="last")
    stale: list[dict[str, Any]] = []
    missing_periods: list[dict[str, Any]] = []
    delays: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []
    method_reviews: list[dict[str, Any]] = []
    for series_id, group in latest.groupby("series_id"):
        group = group.sort_values("_available")
        final = group.iloc[-1]
        age = (cutoff - final["_available"]).days
        # Official monthly publication schedules are intentionally not treated as facts.
        # 75 days is only a stale-data alert threshold for this monthly watch.
        if age > 75:
            stale.append(
                {
                    "series_id": series_id,
                    "latest_period": final["observation_period"],
                    "age_days": age,
                }
            )
        covered_months: set[pd.Period] = set()
        for period_text in group["observation_period"].astype(str):
            spans = re.match(r"^(\d{4}-\d{2})(?:/(\d{4}-\d{2}))?$", period_text)
            if not spans:
                continue
            first_period = pd.Period(spans.group(1), freq="M")
            last_period = pd.Period(spans.group(2), freq="M") if spans.group(2) else first_period
            covered_months.update(pd.period_range(first_period, last_period, freq="M"))
        if len(covered_months) > 1:
            absent = [
                period.strftime("%Y-%m")
                for period in pd.period_range(min(covered_months), max(covered_months), freq="M")
                if period not in covered_months
            ]
            if absent:
                missing_periods.append(
                    {
                        "series_id": series_id,
                        "missing_periods": absent[:48],
                        "truncated": len(absent) > 48,
                        "classification": "unobserved_period; no interpolation applied",
                    }
                )
        if len(group) > 1:
            prior = group.iloc[-2]
            delta = float(final["value"]) - float(prior["value"])
            if abs(delta) >= ANOMALY_LIMITS.get(series_id, 10.0):
                anomalies.append(
                    {
                        "series_id": series_id,
                        "from_period": prior["observation_period"],
                        "to_period": final["observation_period"],
                        "change": delta,
                        "threshold": ANOMALY_LIMITS.get(series_id, 10.0),
                        "classification": "review_only_not_data_error",
                    }
                )
        observation = pd.Timestamp(final["observation_date"])
        lag = (final["_available"].date() - observation.date()).days
        if lag > 45:
            delays.append(
                {
                    "series_id": series_id,
                    "observation_period": final["observation_period"],
                    "publication_lag_days": lag,
                    "classification": "unusually_late_vs_observation_period; no official schedule comparison",
                }
            )
    for row in known.to_dict(orient="records"):
        flags = json.loads(row["quality_flags"] or "[]")
        if "methodology_change_review" in flags:
            method_reviews.append(
                {
                    "series_id": row["series_id"],
                    "observation_period": row["observation_period"],
                    "source_title": row["source_title"],
                    "source_value_url": row["source_value_url"],
                }
            )
    for event in events:
        if event["event"] != "historical_revision":
            continue
        magnitude = abs(float(event["new_value"]) - float(event["old_value"]))
        threshold = ANOMALY_LIMITS.get(event["series_id"], 10.0)
        if magnitude >= threshold:
            anomalies.append(
                {
                    "series_id": event["series_id"],
                    "observation_period": event["observation_period"],
                    "change": float(event["new_value"]) - float(event["old_value"]),
                    "threshold": threshold,
                    "classification": "large_revision_for_review_not_data_error",
                }
            )
    return {
        "new_releases": [event for event in events if event["event"] == "new_release"],
        "historical_revisions": [
            event for event in events if event["event"] == "historical_revision"
        ],
        "provenance_updates": [event for event in events if event["event"] == "provenance_update"],
        "stale_series": stale,
        "missing_periods": missing_periods,
        "unusual_publication_lags": delays,
        "large_changes_for_review": anomalies,
        "methodology_change_reviews": method_reviews,
        "unknown_release_dates": int((known["availability_basis"] == "unknown").sum()),
    }


def run_china_data_watch(
    *,
    raw_dir: str | Path = "data/raw/china",
    report_dir: str | Path = "reports/china_macro",
    start_year: int | None = None,
    end_year: int | None = None,
    max_workers: int = 2,
    full_rescan: bool = False,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Collect NBS/PBOC data, append only new vintages, and persist a retry-safe checkpoint."""

    raw_path = Path(raw_dir)
    raw_path.mkdir(parents=True, exist_ok=True)
    report_path = Path(report_dir)
    state_path = raw_path / STATE_NAME
    state = _load_json(state_path)
    now = _now()
    cutoff = as_of or now.date().isoformat()
    cutoff_date = pd.Timestamp(cutoff).date()
    if cutoff_date > now.date():
        raise ValueError("as_of cannot be in the future")

    previous_full = pd.to_datetime(state.get("last_full_scan_at"), errors="coerce")
    full_scan_due = pd.isna(previous_full) or previous_full < pd.Timestamp(
        now - timedelta(days=180)
    )
    pending_window = state.get("pending_scan_window")
    retry_pending = not full_rescan and start_year is None and bool(pending_window)
    if start_year is not None:
        scan_start = start_year
    elif retry_pending:
        scan_start = int(pending_window["start_year"])
    elif full_rescan or full_scan_due:
        scan_start = 2005
    else:
        scan_start = max(2005, now.year - 1)
    if end_year is not None:
        scan_end = end_year
    elif retry_pending:
        scan_end = int(pending_window["end_year"])
    else:
        scan_end = now.year
    if scan_start > scan_end:
        raise ValueError("start_year must be <= end_year")

    stage_parent = report_path
    stage_parent.mkdir(parents=True, exist_ok=True)
    source_summaries: dict[str, Any] = {}
    errors: list[str] = []
    stage_records: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="china-watch-", dir=stage_parent) as stage_name:
        stage = Path(stage_name)
        for source, collector in (
            ("nbs", collect_nbs_snapshots),
            ("pboc", collect_pboc_snapshots),
        ):
            try:
                if source == "nbs":
                    result = collector(
                        stage,
                        start_year=scan_start,
                        end_year=scan_end,
                        max_workers=max_workers,
                    )
                else:
                    result = collector(
                        stage,
                        start_year=scan_start,
                        end_year=scan_end,
                        max_workers=max_workers,
                    )
                source_summaries[source], source_errors = _summarize_collector(source, result)
                errors.extend(source_errors)
            except Exception as exc:  # noqa: BLE001 - retain every source failure in the audit
                source_summaries[source] = {"failed": True, "error": f"{type(exc).__name__}: {exc}"}
                errors.append(f"{source}: {type(exc).__name__}: {exc}")
        stage_records = _records_from_stage(stage, now.isoformat(timespec="seconds"))

    ledger_path = raw_path / LEDGER_NAME
    appended, events = append_live_records(ledger_path, stage_records, raw_dir=raw_path)
    ledger = read_watch_ledger(ledger_path)
    audit = {
        "schema_version": "china-macro-watch-audit/1",
        "status": "success" if not errors else "failed",
        "started_at": now.isoformat(timespec="seconds"),
        "finished_at": _now().isoformat(timespec="seconds"),
        "as_of": cutoff,
        "scan_window": {
            "start_year": scan_start,
            "end_year": scan_end,
            "full_rescan": scan_start == 2005,
        },
        "time_zone": "Asia/Shanghai",
        "retry_and_resume": (
            "Collectors retry failed requests; a checkpoint advances only after both official sources "
            "complete. A failed run keeps appended rows and persists its scan window for the next run; "
            "append keys make that retry idempotent."
        ),
        "sources": source_summaries,
        "errors": errors,
        "new_rows_appended": len(appended),
        "events": events,
        "quality": _quality_audit(ledger, events, cutoff),
        "ledger": str(ledger_path),
        "ledger_rows": len(ledger),
    }

    if not errors:
        successful_runs = int(state.get("successful_runs", 0)) + 1
        state.update(
            {
                "schema_version": "china-macro-watch-state/1",
                "last_successful_at": audit["finished_at"],
                "successful_runs": successful_runs,
                "last_scan_window": audit["scan_window"],
                "last_full_scan_at": audit["finished_at"]
                if scan_start == 2005
                else state.get("last_full_scan_at"),
                "resume_policy": "repeat failed scan window; existing snapshots remain immutable",
            }
        )
        state.pop("pending_scan_window", None)
        _write_json(state_path, state)
    else:
        # Keep the last successful window so the next run safely retries it.
        state["last_failed_at"] = audit["finished_at"]
        state["last_failure_count"] = int(state.get("last_failure_count", 0)) + 1
        state["pending_scan_window"] = audit["scan_window"]
        _write_json(state_path, state)

    report_path.mkdir(parents=True, exist_ok=True)
    _write_json(report_path / "collection_audit.json", audit)
    return audit


def write_collection_audit_markdown(audit: dict[str, Any], path: str | Path) -> None:
    """Write the concise human-readable companion to collection_audit.json."""

    quality = audit.get("quality", {})
    lines = [
        "# China Macro Data Watch — collection audit",
        "",
        f"- Status: **{audit.get('status', 'unknown')}**",
        f"- Finished: `{audit.get('finished_at', 'unknown')}` (Asia/Shanghai)",
        f"- Window: `{audit.get('scan_window', {}).get('start_year')}`–`{audit.get('scan_window', {}).get('end_year')}`",
        f"- Ledger rows: {audit.get('ledger_rows', 0)}; appended this run: {audit.get('new_rows_appended', 0)}",
        "",
        "## Changes",
        "",
        f"- New releases: {len(quality.get('new_releases', []))}",
        f"- Historical revisions: {len(quality.get('historical_revisions', []))}",
        f"- Provenance updates: {len(quality.get('provenance_updates', []))}",
        f"- Stale series: {len(quality.get('stale_series', []))}",
        f"- Unusual publication lags: {len(quality.get('unusual_publication_lags', []))}",
        f"- Large changes for review: {len(quality.get('large_changes_for_review', []))}",
        f"- Unknown release dates retained: {quality.get('unknown_release_dates', 0)}",
        "",
        "No scheduled date is used as an actual publication date. Stale and large-change flags are review prompts, not data errors.",
    ]
    if audit.get("errors"):
        lines.extend(["", "## Collection errors", ""])
        lines.extend(f"- {error}" for error in audit["errors"])
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
