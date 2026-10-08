"""Export public terminal data without promoting unverified research results.

The exporter is intentionally a thin adapter around the existing data and
factor pipeline. Public series are written to stable JSON contracts; failed
providers update health metadata but never replace the last valid data file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

ASSETS = ("SPY", "QQQ", "GLD", "TLT", "DBC")
ASSET_LABELS = {
    "SPY": "S&P 500 ETF",
    "QQQ": "Nasdaq 100 ETF",
    "GLD": "Gold ETF",
    "TLT": "20+ Year Treasury ETF",
    "DBC": "Broad Commodity ETF",
}
CHINA_SNAPSHOTS = {
    "cn_cpi": ("China CPI YoY", "percent_yoy"),
    "cn_ppi": ("China PPI YoY", "percent_yoy"),
    "cn_industrial_production": ("China Industrial Production YoY", "percent_yoy"),
    "cn_retail_sales": ("China Retail Sales YoY", "percent_yoy"),
    "cn_fixed_asset_investment": ("China Fixed Asset Investment YTD YoY", "percent_yoy"),
    "cn_pmi_new_orders": ("China PMI New Orders", "index_points"),
    "cn_m1": ("China M1 YoY", "percent_yoy"),
    "cn_m2": ("China M2 YoY", "percent_yoy"),
    "cn_tsf_stock_yoy": ("China TSF Stock YoY", "percent_yoy"),
}
US_LABELS = {
    "us_cpi": ("US CPI", "index_points"),
    "us_core_cpi": ("US Core CPI", "index_points"),
    "us_core_pce": ("US Core PCE", "index_points"),
    "us_ppi_all_commodities": ("US PPI All Commodities", "index_points"),
    "us_10y": ("US 10Y Treasury Yield", "percent"),
    "us_10y_real": ("US 10Y Real Yield", "percent"),
    "us_unemployment": ("US Unemployment Rate", "percent"),
    "us_nfci": ("US Financial Conditions Index", "index_points"),
    "us_m2": ("US M2", "billions_usd"),
    "us_high_yield_oas": ("US High Yield OAS", "percent"),
}
FACTOR_LABELS = {
    "growth": "Growth",
    "inflation": "Inflation",
    "liquidity": "Liquidity",
    "real_rate": "Real Rate",
}
DOMAIN_NAMES = ("market_prices", "china_macro", "us_macro")


@dataclass
class SourceResult:
    """Fresh data and metadata from one independently refreshable source."""

    series: list[dict[str, Any]] = field(default_factory=list)
    regimes: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _date(value: Any) -> str:
    return pd.Timestamp(value).date().isoformat()


def _git_commit() -> str:
    if os.getenv("GITHUB_SHA"):
        return os.environ["GITHUB_SHA"]
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "local"


def _model_version() -> str:
    path = Path("config/factors.yaml")
    if not path.exists():
        return "macro-v1"
    short_hash = hashlib.sha256(path.read_bytes()).hexdigest()[:10]
    return f"macro-v1+{short_hash}"


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
        temp_path = Path(handle.name)
    temp_path.replace(path)


def _clean_value(value: Any) -> float | int | str | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (int,)):
        return int(value)
    if isinstance(value, (float,)):
        return float(value) if math.isfinite(value) else None
    if hasattr(value, "item"):
        return _clean_value(value.item())
    return str(value)


def _coverage(points: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [point for point in points if point.get("value") is not None]
    return {
        "observations": len(valid),
        "from": valid[0]["date"] if valid else None,
        "through": valid[-1]["date"] if valid else None,
        "latest_available_date": max(
            (point.get("available_date") or point["date"] for point in valid),
            default=None,
        ),
    }


def _series(
    *,
    series_id: str,
    label: str,
    domain: str,
    unit: str,
    source: str,
    source_url: str | None,
    points: list[dict[str, Any]],
    country: str | None = None,
    frequency: str = "monthly",
    kind: str = "macro_observation",
    pit_status: str,
    updated_at: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    normalized = [
        point
        for point in points
        if point.get("value") is not None and point.get("date") is not None
    ]
    normalized.sort(key=lambda point: point["date"])
    return {
        "id": series_id,
        "label": label,
        "domain": domain,
        "country": country,
        "kind": kind,
        "unit": unit,
        "frequency": frequency,
        "source": source,
        "source_url": source_url,
        "pit_status": pit_status,
        "updated_at": updated_at,
        "coverage": _coverage(normalized),
        "notes": notes,
        "points": normalized,
    }


def _frame_points(
    frame: pd.Series | pd.DataFrame,
    *,
    column: str | None = None,
    basis: str,
    volume: pd.Series | None = None,
) -> list[dict[str, Any]]:
    values = frame[column] if column and isinstance(frame, pd.DataFrame) else frame
    points: list[dict[str, Any]] = []
    for index, value in values.items():
        clean = _clean_value(value)
        if clean is None:
            continue
        point: dict[str, Any] = {
            "date": _date(index),
            "available_date": _date(index),
            "availability_basis": basis,
            "value": clean,
        }
        if volume is not None:
            point["volume"] = int(volume.loc[index]) if index in volume.index and pd.notna(volume.loc[index]) else None
        points.append(point)
    return points


def _download_market_frames(previous: list[dict[str, Any]]) -> dict[str, pd.DataFrame]:
    try:
        import yfinance as yf
    except ImportError as exc:  # pragma: no cover - action installs the data extra
        raise RuntimeError("Yahoo refresh requires the optional mrq-data[data] extra") from exc

    last_dates = [
        pd.Timestamp(series["points"][-1]["date"])
        for series in previous
        if series.get("domain") == "markets" and series.get("points")
    ]
    options: dict[str, Any] = {
        "tickers": " ".join(ASSETS),
        "interval": "1d",
        "auto_adjust": True,
        "progress": False,
        "threads": False,
        "group_by": "ticker",
        "multi_level_index": True,
    }
    if last_dates:
        # Re-fetch a short overlap to repair the latest trading days without
        # downloading the full ETF history on every scheduled run.
        options["start"] = (min(last_dates) - pd.Timedelta(days=14)).date().isoformat()
    else:
        options["period"] = "max"
    raw = yf.download(**options)
    if raw is None or raw.empty:
        raise RuntimeError("Yahoo Finance returned an empty market-data response")

    frames: dict[str, pd.DataFrame] = {}
    for ticker in ASSETS:
        if isinstance(raw.columns, pd.MultiIndex):
            if ticker in raw.columns.get_level_values(0):
                ticker_frame = raw[ticker]
            elif ticker in raw.columns.get_level_values(-1):
                ticker_frame = raw.xs(ticker, axis=1, level=-1)
            else:
                continue
        else:
            if len(ASSETS) != 1:
                continue
            ticker_frame = raw
        if "Close" not in ticker_frame.columns:
            continue
        frame = pd.DataFrame(index=ticker_frame.index)
        frame["value"] = ticker_frame["Close"]
        frame["volume"] = ticker_frame.get("Volume")
        frame = frame.dropna(subset=["value"])
        if not frame.empty:
            frame.index = pd.to_datetime(frame.index).tz_localize(None)
            frames[ticker] = frame
    if not frames:
        raise RuntimeError("Yahoo Finance response had no usable adjusted-close series")
    return frames


def _merge_market_points(
    previous: list[dict[str, Any]],
    fresh: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    points = {point["date"]: point for point in previous}
    points.update({point["date"]: point for point in fresh})
    return [points[key] for key in sorted(points)]


def _build_market_source(previous: list[dict[str, Any]], *, updated_at: str) -> SourceResult:
    frames = _download_market_frames(previous)
    old_by_symbol = {
        series.get("symbol"): series
        for series in previous
        if series.get("domain") == "markets"
    }
    missing = [symbol for symbol in ASSETS if symbol not in frames]
    series: list[dict[str, Any]] = []
    for symbol, frame in frames.items():
        fresh = _frame_points(
            frame,
            column="value",
            basis="market_close_yahoo_adjusted_history",
            volume=frame["volume"],
        )
        old = old_by_symbol.get(symbol, {})
        merged = _merge_market_points(old.get("points", []), fresh)
        series.append(
            _series(
                series_id=f"market:{symbol}",
                label=symbol,
                domain="markets",
                country="US",
                kind="asset_price",
                unit="USD",
                frequency="daily",
                source="Yahoo Finance via yfinance",
                source_url=f"https://finance.yahoo.com/quote/{symbol}/",
                pit_status="adjusted_history_without_vintage_archive",
                points=merged,
                updated_at=updated_at,
                notes=(
                    "Adjusted close includes later corporate-action adjustments. Volume is ordinary "
                    "reported share volume; no buyer/seller direction is available."
                ),
            )
        )
    if missing:
        raise PartialSourceError(
            f"Yahoo response omitted {', '.join(missing)}; updated available symbols only",
            partial=series,
        )
    return SourceResult(
        series=series,
        metadata={"symbols": sorted(frames), "frequency": "daily", "incremental_overlap_days": 14},
    )


class PartialSourceError(RuntimeError):
    def __init__(self, message: str, *, partial: SourceResult | list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.partial = partial if isinstance(partial, SourceResult) else SourceResult(series=partial)


def _build_china_source(*, updated_at: str) -> SourceResult:
    from mrq_engines.pipeline import build_china_baseline

    raw, factors, regimes = build_china_baseline(
        start="2005-01-01", allow_partial_sources=True
    )
    exported: list[dict[str, Any]] = []
    latest_snapshot_dates: list[str] = []
    found_snapshot_ids: set[str] = set()
    snapshot_root = Path("data/raw/china")
    for series_id, (label, unit) in CHINA_SNAPSHOTS.items():
        path = snapshot_root / f"{series_id.removeprefix('cn_')}.csv"
        if series_id == "cn_fixed_asset_investment":
            path = snapshot_root / "fixed_asset_investment_ytd_yoy.csv"
        elif series_id == "cn_industrial_production":
            path = snapshot_root / "industrial_production_yoy.csv"
        elif series_id == "cn_retail_sales":
            path = snapshot_root / "retail_sales_yoy.csv"
        elif series_id == "cn_pmi_new_orders":
            path = snapshot_root / "pmi_new_orders.csv"
        elif series_id == "cn_cpi":
            path = snapshot_root / "cpi_yoy.csv"
        elif series_id == "cn_ppi":
            path = snapshot_root / "ppi_yoy.csv"
        elif series_id == "cn_m1":
            path = snapshot_root / "m1_yoy.csv"
        elif series_id == "cn_m2":
            path = snapshot_root / "m2_yoy.csv"
        elif series_id == "cn_tsf_stock_yoy":
            path = snapshot_root / "tsf_stock_yoy.csv"
        if not path.exists():
            continue
        found_snapshot_ids.add(series_id)
        metadata_path = path.with_suffix(".meta.yaml")
        metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
        frame = pd.read_csv(path)
        points: list[dict[str, Any]] = []
        for row in frame.to_dict(orient="records"):
            try:
                value = float(row["value"])
            except (TypeError, ValueError, KeyError):
                continue
            point = {
                "date": _date(row["observation_date"]),
                "available_date": row.get("available_date") or None,
                "availability_basis": row.get("availability_basis") or "unknown",
                "value": value,
                "source_url": row.get("source_value_url") or None,
                "availability_evidence_url": row.get("availability_evidence_url") or None,
            }
            points.append(point)
        if points:
            latest_snapshot_dates.extend(
                point["available_date"] for point in points if point.get("available_date")
            )
        exported.append(
            _series(
                series_id=f"macro:{series_id}",
                label=label,
                domain="macro_china",
                country="China",
                kind="macro_observation",
                unit=unit,
                frequency="monthly",
                source=metadata.get("source_name", "Official NBS/PBOC release snapshots"),
                source_url=metadata.get("source_url"),
                pit_status="frozen_official_release_snapshots_partial_coverage",
                points=points,
                updated_at=metadata.get("downloaded_at") or updated_at,
                notes=metadata.get("notes"),
            )
        )

    for factor in factors.columns:
        points = _frame_points(
            factors[factor], basis="official_release_as_of_monthly_panel"
        )
        exported.append(
            _series(
                series_id=f"factor:china:{factor}",
                label=f"China {FACTOR_LABELS.get(factor, factor.title())}",
                domain="macro_china",
                country="China",
                kind="macro_factor",
                unit="z-score",
                source="macro-regime-quant China baseline",
                source_url="https://github.com/lava-chen/macro-regime-quant/blob/main/config/factors.yaml",
                pit_status="official_release_as_of_panel_partial_coverage",
                points=points,
                updated_at=updated_at,
                notes="Factor values use the available official-release snapshot set; history is partial.",
            )
        )
    regime_points = [
        {"date": _date(index), "regime": str(value), "available_date": _date(index)}
        for index, value in regimes.dropna().items()
    ]
    regime_data = {
        "country": "China",
        "updated_at": updated_at,
        "pit_status": "official_release_as_of_panel_partial_coverage",
        "definition": "Growth × Inflation quadrant",
        "history": regime_points,
    }
    source_result = SourceResult(
        series=exported,
        regimes=[regime_data],
        metadata={
            "snapshot_series": len([item for item in exported if item["kind"] == "macro_observation"]),
            "latest_official_available_date": max(latest_snapshot_dates, default=None),
            "missing_configured_inputs": ["China 10Y yield", "China core CPI"],
            "availability_policy": "official_release_only",
            "snapshot_note": "Nine frozen official series; the current archive is partial and uneven.",
            "raw_panel_columns": list(raw.columns),
        },
    )
    missing_snapshots = sorted(set(CHINA_SNAPSHOTS) - found_snapshot_ids)
    if missing_snapshots:
        raise PartialSourceError(
            f"China snapshot set is missing {', '.join(missing_snapshots)}; kept prior values for those series",
            partial=source_result,
        )
    return source_result


def _build_us_source(*, updated_at: str) -> SourceResult:
    from mrq_engines.pipeline import build_us_baseline

    raw, factors, regimes = build_us_baseline(start="2000-01-01")
    catalog = yaml.safe_load(Path("config/data_catalog.yaml").read_text(encoding="utf-8"))["series"]
    series: list[dict[str, Any]] = []
    for key in raw.columns:
        if key not in US_LABELS:
            continue
        label, unit = US_LABELS[key]
        spec = catalog.get(key, {})
        symbol = spec.get("symbol", key)
        points = _frame_points(raw[key], basis="approximate_release_lag_latest_vintage")
        series.append(
            _series(
                series_id=f"macro:{key}",
                label=label,
                domain="macro_us",
                country="United States",
                kind="macro_observation",
                unit=unit,
                source=f"FRED {symbol} (latest vintage)",
                source_url=f"https://fred.stlouisfed.org/series/{symbol}",
                points=points,
                updated_at=updated_at,
                pit_status="latest_revised_values_approximate_release_lag_not_pit_safe",
                notes=spec.get("notes") or "Latest revised history; configured release lag is approximate.",
            )
        )
    for factor in factors.columns:
        points = _frame_points(factors[factor], basis="approximate_release_lag_latest_vintage")
        series.append(
            _series(
                series_id=f"factor:us:{factor}",
                label=f"US {FACTOR_LABELS.get(factor, factor.title())}",
                domain="macro_us",
                country="United States",
                kind="macro_factor",
                unit="z-score",
                source="macro-regime-quant US baseline / latest FRED vintage",
                source_url="https://github.com/lava-chen/macro-regime-quant/blob/main/config/factors.yaml",
                points=points,
                updated_at=updated_at,
                pit_status="latest_revised_values_approximate_release_lag_not_pit_safe",
                notes="Not safe for historical as-of research until ALFRED vintages are wired into the panel.",
            )
        )
    regime_points = [
        {"date": _date(index), "regime": str(value), "available_date": _date(index)}
        for index, value in regimes.dropna().items()
    ]
    if not series:
        raise RuntimeError("FRED pipeline returned no exportable macro series")
    return SourceResult(
        series=series,
        regimes=[
            {
                "country": "United States",
                "updated_at": updated_at,
                "pit_status": "latest_revised_values_not_pit_safe",
                "definition": "Growth × Inflation quadrant",
                "history": regime_points,
            }
        ],
        metadata={
            "series_count": len(series),
            "frequency": "monthly panel; FRED observations are latest revised values",
            "pit_status": "not_pit_safe",
            "warning": "Configured release lags are approximations, not publication-date evidence.",
        },
    )


def _merge_group(
    existing: list[dict[str, Any]], new: list[dict[str, Any]], domain: str
) -> list[dict[str, Any]]:
    replacements = {record["id"]: record for record in new}
    kept = [record for record in existing if record.get("domain") != domain]
    return kept + list(replacements.values())


def _merge_partial_group(
    existing: list[dict[str, Any]], new: list[dict[str, Any]], domain: str
) -> list[dict[str, Any]]:
    merged = {record["id"]: record for record in existing if record.get("domain") == domain}
    merged.update({record["id"]: record for record in new})
    return [record for record in existing if record.get("domain") != domain] + list(merged.values())


def _merge_regimes(
    existing: list[dict[str, Any]], new: list[dict[str, Any]], country: str
) -> list[dict[str, Any]]:
    replacements = {record["country"]: record for record in new}
    kept = [record for record in existing if record.get("country") != country]
    return kept + list(replacements.values())


def _status_for(
    prior: dict[str, Any],
    *,
    state: str,
    attempted_at: str,
    metadata: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    status = dict(prior)
    status.update({"state": state, "last_attempt_at": attempted_at})
    if state == "success":
        status.pop("note", None)
        status["last_success_at"] = attempted_at
        status["error"] = None
    elif state == "failed":
        status.pop("note", None)
        status["error"] = (error or "Source refresh failed")[:600]
    if metadata:
        status.update(metadata)
    return status


def _build_sources(updated_at: str, previous: list[dict[str, Any]]) -> dict[str, Callable[[], SourceResult]]:
    return {
        "market_prices": lambda: _build_market_source(previous, updated_at=updated_at),
        "china_macro": lambda: _build_china_source(updated_at=updated_at),
        "us_macro": lambda: _build_us_source(updated_at=updated_at),
    }


def export_terminal_data(
    output_dir: str | Path,
    *,
    skip_markets: bool = False,
    skip_us: bool = False,
    china_harvest_state: str | None = None,
    source_builders: dict[str, Callable[[], SourceResult]] | None = None,
    attempted_at: str | None = None,
) -> dict[str, Any]:
    """Refresh source groups independently and preserve last-good groups."""

    out = Path(output_dir)
    previous_series_doc = _read_json(out / "series.json", {"series": []})
    previous_regime_doc = _read_json(out / "regimes.json", {"countries": []})
    previous_summary = _read_json(out / "summary.json", {})
    current_series = list(previous_series_doc.get("series", []))
    current_regimes = list(previous_regime_doc.get("countries", []))
    now = attempted_at or _now()
    builders = source_builders or _build_sources(now, current_series)
    states: dict[str, Any] = dict(previous_summary.get("sources", {}))
    errors: list[str] = []
    source_to_domain = {
        "market_prices": "markets",
        "china_macro": "macro_china",
        "us_macro": "macro_us",
    }
    skipped = {
        "market_prices": skip_markets,
        "china_macro": False,
        "us_macro": skip_us,
    }
    updated_country = {"china_macro": "China", "us_macro": "United States"}

    for source_name in DOMAIN_NAMES:
        prior_status = states.get(source_name, {})
        if skipped[source_name]:
            states[source_name] = _status_for(
                prior_status, state="not_run", attempted_at=now,
                metadata={"note": "This source was intentionally skipped for this run."},
            )
            continue
        try:
            result = builders[source_name]()
        except PartialSourceError as exc:
            result = exc.partial
            errors.append(f"{source_name}: {exc}")
            states[source_name] = _status_for(
                prior_status, state="failed", attempted_at=now,
                metadata=result.metadata, error=str(exc),
            )
            domain = source_to_domain[source_name]
            current_series = _merge_partial_group(current_series, result.series, domain)
            if source_name in updated_country and result.regimes:
                current_regimes = _merge_regimes(
                    current_regimes, result.regimes, updated_country[source_name]
                )
            continue
        except Exception as exc:  # noqa: BLE001 - preserve last-good data for any provider failure
            message = f"{source_name}: {type(exc).__name__}: {exc}"
            errors.append(message)
            states[source_name] = _status_for(
                prior_status, state="failed", attempted_at=now, error=message
            )
            continue

        domain = source_to_domain[source_name]
        current_series = _merge_group(current_series, result.series, domain)
        if source_name in updated_country:
            current_regimes = _merge_regimes(
                current_regimes, result.regimes, updated_country[source_name]
            )
        states[source_name] = _status_for(
            prior_status, state="success", attempted_at=now,
            metadata=result.metadata,
        )

    if china_harvest_state == "failed":
        errors.append("China official archive refresh failed; previous frozen snapshots were retained.")
        states["china_snapshot_refresh"] = _status_for(
            states.get("china_snapshot_refresh", {}),
            state="failed",
            attempted_at=now,
            error="Official NBS/PBOC archive refresh failed; prior snapshot files were retained.",
        )
    elif china_harvest_state == "success":
        states["china_snapshot_refresh"] = _status_for(
            states.get("china_snapshot_refresh", {}), state="success", attempted_at=now
        )
    else:
        states["china_snapshot_refresh"] = _status_for(
            states.get("china_snapshot_refresh", {}),
            state="not_run",
            attempted_at=now,
            metadata={"note": "Official releases are scanned monthly or by manual dispatch."},
        )

    non_success = [name for name in DOMAIN_NAMES if states.get(name, {}).get("state") != "success"]
    successful_this_run = [
        name
        for name in DOMAIN_NAMES
        if states.get(name, {}).get("last_attempt_at") == now
        and states.get(name, {}).get("state") == "success"
    ]
    if china_harvest_state == "failed":
        overall = "partial"
    elif not non_success:
        overall = "success"
    elif successful_this_run:
        overall = "partial"
    else:
        overall = "failed"

    factor_config = Path("config/factors.yaml")
    china_coverage = {
        record["id"]: record["coverage"]
        for record in current_series
        if record.get("domain") == "macro_china" and record.get("kind") == "macro_observation"
    }
    summary: dict[str, Any] = {
        "schema_version": "1.0",
        "status": overall,
        "visibility": "public_market_and_macro_series_only",
        "attempted_at": now,
        "last_success_at": max(
            (
                status["last_success_at"]
                for status in states.values()
                if status.get("last_success_at")
            ),
            default=None,
        ),
        "source_commit": _git_commit(),
        "workflow_run_id": os.getenv("GITHUB_RUN_ID"),
        "model_version": _model_version(),
        "factor_config_present": factor_config.exists(),
        "series_count": len(current_series),
        "regime_country_count": len(current_regimes),
        "sources": states,
        "china_snapshot_coverage": china_coverage,
        "china_harvest_state": china_harvest_state or "not_run",
        "errors": errors,
        "pit_gate": {
            "china": "Official release snapshots are frozen but incomplete and unevenly covered.",
            "united_states": "Latest revised FRED history with approximate lags is not PIT-safe.",
            "alfred": "Draft PR #17 archive is not connected to the historical research pipeline.",
            "backtests": "No eligible PIT-validated backtest output is exported.",
        },
    }
    if current_series or not (out / "series.json").exists():
        _write_json_atomic(
            out / "series.json",
            {
                "schema_version": "1.0",
                "generated_at": now if successful_this_run else previous_series_doc.get("generated_at"),
                "source_commit": summary["source_commit"],
                "model_version": summary["model_version"],
                "series": current_series,
            },
        )
    if current_regimes or not (out / "regimes.json").exists():
        _write_json_atomic(
            out / "regimes.json",
            {
                "schema_version": "1.0",
                "generated_at": now if successful_this_run else previous_regime_doc.get("generated_at"),
                "countries": current_regimes,
            },
        )
    if not (out / "backtests.json").exists():
        _write_json_atomic(
            out / "backtests.json",
            {
                "schema_version": "1.0",
                "status": "not_exported",
                "results": [],
                "message": "No PIT-validated backtest output is available for display.",
                "limitations": [
                    "ALFRED archive in Draft PR #17 is not integrated into the historical panel.",
                    "Issue #18 PIT acceptance criteria remain open.",
                    "No validated walk-forward or out-of-sample run is present in reports/.",
                ],
            },
        )
    _write_json_atomic(out / "summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export Macro Quant Terminal public data")
    parser.add_argument("--output", default="data/exports/terminal-v0.1")
    parser.add_argument("--skip-markets", action="store_true", help="Do not query Yahoo Finance")
    parser.add_argument("--skip-us", action="store_true", help="Do not query FRED")
    parser.add_argument(
        "--china-harvest-state",
        choices=["success", "failed", "not_run"],
        default="not_run",
        help="Outcome from the optional official China snapshot refresh step.",
    )
    args = parser.parse_args(argv)
    summary = export_terminal_data(
        args.output,
        skip_markets=args.skip_markets,
        skip_us=args.skip_us,
        china_harvest_state=args.china_harvest_state,
    )
    print(
        json.dumps(
            {"status": summary["status"], "sources": summary["sources"], "errors": summary["errors"]},
            ensure_ascii=False,
        )
    )
    return 1 if summary["status"] == "failed" else 0
