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
    "SPY": "标普 500 指数 ETF",
    "QQQ": "纳斯达克 100 指数 ETF",
    "GLD": "黄金 ETF",
    "TLT": "20 年以上美国国债 ETF",
    "DBC": "大宗商品 ETF",
}
CHINA_SNAPSHOTS = {
    "cn_cpi": ("居民消费价格指数同比", "percent_yoy"),
    "cn_ppi": ("工业生产者出厂价格指数同比", "percent_yoy"),
    "cn_industrial_production": ("规模以上工业增加值同比", "percent_yoy"),
    "cn_retail_sales": ("社会消费品零售总额同比", "percent_yoy"),
    "cn_fixed_asset_investment": ("固定资产投资累计同比", "percent_yoy"),
    "cn_pmi_new_orders": ("制造业采购经理指数新订单", "index_points"),
    "cn_m1": ("M1 货币供应量同比", "percent_yoy"),
    "cn_m2": ("M2 货币供应量同比", "percent_yoy"),
    "cn_tsf_stock_yoy": ("社会融资规模存量同比", "percent_yoy"),
}
US_LABELS = {
    "us_cpi": ("美国居民消费价格指数", "index_points"),
    "us_core_cpi": ("美国核心居民消费价格指数", "index_points"),
    "us_core_pce": ("美国核心个人消费支出价格指数", "index_points"),
    "us_ppi_all_commodities": ("美国全商品生产者价格指数", "index_points"),
    "us_10y": ("美国 10 年期国债收益率", "percent"),
    "us_10y_real": ("美国 10 年期实际收益率", "percent"),
    "us_unemployment": ("美国失业率", "percent"),
    "us_nfci": ("美国金融状况指数", "index_points"),
    "us_m2": ("美国 M2 货币供应量", "billions_usd"),
    "us_high_yield_oas": ("美国高收益债信用利差", "percent"),
}
FACTOR_LABELS = {
    "growth": "增长",
    "inflation": "通胀",
    "liquidity": "流动性",
    "real_rate": "实际利率",
}
COUNTRY_LABELS = {"China": "中国", "United States": "美国"}
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


def _china_note(series_id: str, points: list[dict[str, Any]]) -> str:
    coverage = _coverage(points)
    dates = (
        f"观测区间为 {coverage['from']} 至 {coverage['through']}。"
        if coverage["from"] and coverage["through"]
        else "当前没有可用观测值。"
    )
    notes = {
        "cn_cpi": "数值按每期国家统计局原始发布记录保存，未进行二次转换。索引归档目前从约 2021 年 9 月开始；更早发布尚未系统检索。",
        "cn_ppi": "数值按每期国家统计局原始发布记录保存，未进行二次转换。索引归档目前从约 2021 年 9 月开始；更早发布尚未系统检索。",
        "cn_industrial_production": "数值按每期国家统计局原始发布记录保存，未进行二次转换。1 至 2 月合并发布的数据归入 2 月。索引归档目前从约 2021 年 9 月开始。",
        "cn_retail_sales": "数值按每期国家统计局原始发布记录保存，未进行二次转换。1 至 2 月合并发布的数据归入 2 月；12 月使用当月同比，不以全年总增速替代。",
        "cn_fixed_asset_investment": "数值按每期国家统计局原始发布记录保存。这是年初至今累计同比，不是单月增速；1 至 2 月合并发布的数据归入 2 月。",
        "cn_pmi_new_orders": "数值按每期国家统计局原始发布记录保存，未进行二次转换。索引归档目前从约 2021 年 9 月开始；更早发布尚未系统检索。",
        "cn_m1": "数值和发布日期来自人民银行原始报告。索引归档约从 2009 年开始；M1 定义变化按原始发布保留，未对历史断点回填。2011 年 5 月数值取自人民银行 PDF 附件，网页用于证明发布日期。",
        "cn_m2": "数值和发布日期来自人民银行原始报告。索引归档约从 2009 年开始；2011 年 5 月数值取自人民银行 PDF 附件，网页用于证明发布日期。",
        "cn_tsf_stock_yoy": "数值和发布日期来自人民银行原始报告。社融统计范围和方法变化按原始发布保留；仅纳入报告明确给出的数值。索引归档约从 2009 年开始。",
    }
    return f"{notes.get(series_id, '数值按官方原始发布记录保存。')} {dates}"


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
        raise RuntimeError("更新雅虎财经行情需要安装可选的 mrq-data[data] 依赖") from exc

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
        raise RuntimeError("雅虎财经返回的行情数据为空")

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
        raise RuntimeError("雅虎财经返回的数据中没有可用的复权收盘价序列")
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
                source="雅虎财经（yfinance）",
                source_url=f"https://finance.yahoo.com/quote/{symbol}/",
                pit_status="adjusted_history_without_vintage_archive",
                points=merged,
                updated_at=updated_at,
                notes=(
                    "复权收盘价包含后续公司行动调整。成交量为普通报告股数，不含主动买入或卖出方向。"
                ),
            )
        )
    if missing:
        raise PartialSourceError(
            f"雅虎财经响应缺少 {', '.join(missing)}；仅更新了本次可用的代码",
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
                source=(
                    "中国国家统计局"
                    if metadata.get("source_name") == "National Bureau of Statistics of China"
                    else "中国人民银行"
                    if metadata.get("source_name") == "People's Bank of China"
                    else metadata.get("source_name", "中国官方发布快照")
                ),
                source_url=metadata.get("source_url"),
                pit_status="frozen_official_release_snapshots_partial_coverage",
                points=points,
                updated_at=metadata.get("downloaded_at") or updated_at,
                notes=_china_note(series_id, points),
            )
        )

    for factor in factors.columns:
        points = _frame_points(
            factors[factor], basis="official_release_as_of_monthly_panel"
        )
        exported.append(
            _series(
                series_id=f"factor:china:{factor}",
                label=f"中国{FACTOR_LABELS.get(factor, factor)}",
                domain="macro_china",
                country="China",
                kind="macro_factor",
                unit="z-score",
                source="中国宏观量化基线模型",
                source_url="https://github.com/lava-chen/macro-regime-quant/blob/main/config/factors.yaml",
                pit_status="official_release_as_of_panel_partial_coverage",
                points=points,
                updated_at=updated_at,
                notes="因子值基于现有官方发布快照计算，历史覆盖不完整。",
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
        "definition": "增长 × 通胀四象限",
        "history": regime_points,
    }
    source_result = SourceResult(
        series=exported,
        regimes=[regime_data],
        metadata={
            "snapshot_series": len([item for item in exported if item["kind"] == "macro_observation"]),
            "latest_official_available_date": max(latest_snapshot_dates, default=None),
            "missing_configured_inputs": ["中国 10 年期收益率", "中国核心居民消费价格指数"],
            "availability_policy": "official_release_only",
            "snapshot_note": "已冻结 9 条官方序列；当前归档的历史覆盖不完整且不均匀。",
            "raw_panel_columns": list(raw.columns),
        },
    )
    missing_snapshots = sorted(set(CHINA_SNAPSHOTS) - found_snapshot_ids)
    if missing_snapshots:
        raise PartialSourceError(
            f"中国快照缺少 {', '.join(missing_snapshots)}；这些序列保留此前有效数据",
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
                source=f"FRED（圣路易斯联储数据库）· {symbol}（当前修订值）",
                source_url=f"https://fred.stlouisfed.org/series/{symbol}",
                points=points,
                updated_at=updated_at,
                pit_status="latest_revised_values_approximate_release_lag_not_pit_safe",
                notes="使用 FRED 当前可见的修订历史；配置中的发布日期滞后为近似估算，历史数值可能被修订。",
            )
        )
    for factor in factors.columns:
        points = _frame_points(factors[factor], basis="approximate_release_lag_latest_vintage")
        series.append(
            _series(
                series_id=f"factor:us:{factor}",
                label=f"美国{FACTOR_LABELS.get(factor, factor)}",
                domain="macro_us",
                country="United States",
                kind="macro_factor",
                unit="z-score",
                source="美国宏观量化基线模型 / FRED 当前修订值",
                source_url="https://github.com/lava-chen/macro-regime-quant/blob/main/config/factors.yaml",
                points=points,
                updated_at=updated_at,
                pit_status="latest_revised_values_approximate_release_lag_not_pit_safe",
                notes="在 ALFRED 历史版本接入因子面板前，不可用于安全的历史时点研究。",
            )
        )
    regime_points = [
        {"date": _date(index), "regime": str(value), "available_date": _date(index)}
        for index, value in regimes.dropna().items()
    ]
    if not series:
        raise RuntimeError("FRED 数据处理流程未生成可导出的宏观序列")
    return SourceResult(
        series=series,
        regimes=[
            {
                "country": "United States",
                "updated_at": updated_at,
                "pit_status": "latest_revised_values_not_pit_safe",
                "definition": "增长 × 通胀四象限",
                "history": regime_points,
            }
        ],
        metadata={
            "series_count": len(series),
            "frequency": "月度面板；FRED 观测为当前修订值",
            "pit_status": "not_pit_safe",
            "warning": "配置的发布日期滞后为近似值，并非实际发布日期证据。",
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
        status["error"] = (error or "数据源更新失败")[:600]
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
                metadata={"note": "本次运行按设置跳过了此数据源。"},
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
        errors.append("中国官方数据归档更新失败；已保留此前冻结的快照。")
        states["china_snapshot_refresh"] = _status_for(
            states.get("china_snapshot_refresh", {}),
            state="failed",
            attempted_at=now,
            error="中国国家统计局 / 中国人民银行官方数据归档更新失败；已保留此前快照文件。",
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
            metadata={"note": "官方发布数据按月扫描，也可手动触发更新。"},
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
            "china": "中国官方发布快照已冻结，但数据不完整，历史覆盖也不均匀。",
            "united_states": "美国宏观历史使用 FRED 当前修订值，发布日期滞后为近似估算，不具备历史时点安全性。",
            "alfred": "第 17 号草稿 PR 中的 ALFRED 归档尚未接入历史研究流程。",
            "backtests": "当前没有导出通过历史时点验证的回测结果。",
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
    backtests_path = out / "backtests.json"
    existing_backtests = _read_json(backtests_path, {})
    if existing_backtests.get("status") != "verified":
        _write_json_atomic(
            backtests_path,
            {
                "schema_version": "1.0",
                "status": "not_exported",
                "results": [],
                "message": "当前没有可展示且通过历史时点验证的回测结果。",
                "limitations": [
                    "第 17 号草稿 PR 中的 ALFRED 归档尚未接入历史时点因子面板。",
                    "第 18 号任务的历史时点验证验收条件尚未完成。",
                    "报告目录中没有经过验证的滚动样本外或样本外运行结果。",
                ],
            },
        )
    _write_json_atomic(out / "summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导出宏观量化终端的公开数据")
    parser.add_argument("--output", default="data/exports/terminal-v0.1")
    parser.add_argument("--skip-markets", action="store_true", help="不查询雅虎财经行情")
    parser.add_argument("--skip-us", action="store_true", help="不查询 FRED 数据")
    parser.add_argument(
        "--china-harvest-state",
        choices=["success", "failed", "not_run"],
        default="not_run",
        help="可选的中国官方快照更新步骤的运行结果。",
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
