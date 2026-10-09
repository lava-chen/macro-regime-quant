"""PIT-safe China macro factor and daily-watch report builder."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from mrq_data.china_harvest import NBS_SERIES, PBOC_SERIES
from mrq_data.china_watch import _quality_audit, read_watch_ledger

from .macro.factors.transforms import expanding_zscore
from .macro.regimes import classify_regime
from .pipeline import _transform_monthly, build_country_factors, load_factor_config

SERIES = {**NBS_SERIES, **PBOC_SERIES}
SERIES["cn_10y"] = {
    "series_key": "cn_10y",
    "filename": "10y_government_yield.csv",
    "unit": "percent",
}
LABELS = {
    "cn_industrial_production": "工业增加值同比",
    "cn_retail_sales": "社会消费品零售总额单月同比",
    "cn_retail_sales_ytd": "社会消费品零售总额累计同比",
    "cn_fixed_asset_investment": "固定资产投资累计同比",
    "cn_pmi_new_orders": "制造业 PMI 新订单",
    "cn_cpi": "CPI 同比",
    "cn_core_cpi": "核心 CPI 同比",
    "cn_ppi": "PPI 同比",
    "cn_m1": "M1 同比",
    "cn_m2": "M2 同比",
    "cn_tsf_stock_yoy": "社融存量同比",
    "cn_10y": "10 年国债收益率",
}
COMPONENT_LABELS = {
    "industrial_production_yoy": "工业增加值",
    "retail_sales_yoy": "社零单月同比",
    "fixed_asset_investment_ytd_yoy": "固定资产投资累计同比",
    "pmi_new_orders": "PMI 新订单",
    "cpi_yoy": "CPI",
    "core_cpi_yoy": "核心 CPI",
    "ppi_yoy": "PPI",
    "m1_yoy": "M1",
    "m2_yoy": "M2",
    "tsf_stock_yoy": "社融存量",
    "cn_10y_yield_change": "10 年国债收益率变化",
    "ex_post_real_rate_proxy": "事后实际利率代理",
}
NOT_COLLECTED = [
    {"series_id": "cn_gdp", "name": "GDP", "stage": "next phase"},
    {"series_id": "cn_industrial_profit", "name": "工业企业利润", "stage": "next phase"},
    {"series_id": "cn_unemployment", "name": "城镇调查失业率", "stage": "next phase"},
    {"series_id": "cn_property", "name": "房地产投资/销售", "stage": "next phase"},
    {"series_id": "cn_10y", "name": "10 年国债收益率", "stage": "source adapter pending"},
]
REGIME_LABELS = {
    "goldilocks": "增长改善 / 通胀回落",
    "reflation": "增长改善 / 通胀上行",
    "stagflation": "增长走弱 / 通胀上行",
    "recession": "增长走弱 / 通胀回落",
}


def _json_value(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (float, int)):
        return float(value)
    return value


def _display_unit(unit: str) -> str:
    return {
        "percent_yoy": "%",
        "percent_ytd_yoy": "%",
        "yield_percent": "%",
        "percent": "%",
        "index_points": "点",
    }.get(unit, unit)


def _display_change_unit(unit: str) -> str:
    return "个百分点" if unit in {"percent_yoy", "percent_ytd_yoy"} else _display_unit(unit)


def _as_of_date(value: str | date | None) -> pd.Timestamp:
    cutoff = pd.Timestamp(value or pd.Timestamp.now(tz="Asia/Shanghai").date()).normalize()
    today = pd.Timestamp.now(tz="Asia/Shanghai").tz_localize(None).normalize()
    if cutoff > today:
        raise ValueError("as_of cannot be in the future")
    return cutoff


def _eligible_rows(ledger: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    if ledger.empty:
        return pd.DataFrame()
    frame = ledger.copy()
    frame["_available"] = pd.to_datetime(frame["available_date"], errors="coerce")
    frame["_vintage"] = pd.to_datetime(frame["vintage"], errors="coerce")
    frame["_retrieved"] = pd.to_datetime(frame["retrieved_at"], errors="coerce")
    frame["_effective"] = frame[["_available", "_vintage"]].max(axis=1)
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    frame = frame[
        frame["availability_basis"].eq("official_release")
        & frame["_available"].notna()
        & frame["_vintage"].notna()
        & frame["value"].notna()
        & (frame["_available"] <= cutoff)
        & (frame["_vintage"] <= cutoff)
    ].copy()
    if frame.empty:
        return frame
    return frame


def _eligible_versions(ledger: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    frame = _eligible_rows(ledger, cutoff)
    if frame.empty:
        return frame
    frame = frame.sort_values(
        ["series_id", "observation_period", "_vintage", "_retrieved"], na_position="first"
    )
    return frame.drop_duplicates(["series_id", "observation_period"], keep="last")


def build_vintage_aware_monthly_panel(
    ledger: pd.DataFrame,
    *,
    as_of: str | date,
) -> pd.DataFrame:
    """At each month end, use only releases and revisions knowable by that date."""

    cutoff = _as_of_date(as_of)
    versions = _eligible_rows(ledger, cutoff)
    if versions.empty:
        return pd.DataFrame()
    first = versions["_effective"].min().to_period("M").to_timestamp("M")
    last = cutoff.to_period("M").to_timestamp("M")
    month_ends = pd.date_range(first, last, freq="ME")
    columns = sorted(set(versions["series_id"]))
    panel = pd.DataFrame(index=month_ends, columns=columns, dtype=float)
    for month_end in month_ends:
        active = versions[versions["_effective"] <= month_end]
        if active.empty:
            continue
        active = active.sort_values(
            ["series_id", "observation_period", "_vintage", "_retrieved"],
            na_position="first",
        )
        active = active.drop_duplicates(["series_id", "observation_period"], keep="last")
        active = active.sort_values(["series_id", "observation_date", "_effective", "_available"])
        current = active.drop_duplicates("series_id", keep="last")
        for _, row in current.iterrows():
            panel.loc[month_end, row["series_id"]] = float(row["value"])
    panel.index.name = "date"

    # Derived ex-post real-yield proxy: nominal China 10Y yield minus headline CPI YoY.
    # Leave it absent when the official 10Y source is not available.
    if {"cn_10y", "cn_cpi"}.issubset(panel.columns):
        panel["cn_real_rate_proxy"] = panel["cn_10y"] - panel["cn_cpi"]
    return panel


def _latest_metric_rows(ledger: pd.DataFrame, cutoff: pd.Timestamp) -> dict[str, dict[str, Any]]:
    versions = _eligible_versions(ledger, cutoff)
    result: dict[str, dict[str, Any]] = {}
    for series_id in SERIES:
        group = versions[versions["series_id"] == SERIES[series_id]["series_key"]].copy()
        if group.empty:
            stored = ledger[ledger["series_id"] == SERIES[series_id]["series_key"]]
            stored = stored.copy()
            stored["_retrieved"] = pd.to_datetime(stored["retrieved_at"], errors="coerce")
            unknown = stored[
                stored["availability_basis"].eq("unknown") & (stored["_retrieved"] <= cutoff)
            ]
            if not unknown.empty:
                last_unknown = unknown.iloc[-1]
                result[SERIES[series_id]["series_key"]] = {
                    "name": LABELS.get(
                        SERIES[series_id]["series_key"], SERIES[series_id]["series_key"]
                    ),
                    "status": "unknown_release_date",
                    "observation_period": last_unknown["observation_period"],
                    "available_date": None,
                    "value": float(last_unknown["value"]),
                    "unit": last_unknown["unit"],
                    "availability_evidence_url": None,
                    "used_in_factors": False,
                }
                continue
            result[SERIES[series_id]["series_key"]] = {
                "name": LABELS.get(
                    SERIES[series_id]["series_key"], SERIES[series_id]["series_key"]
                ),
                "status": "not_collected",
                "unit": SERIES[series_id]["unit"],
            }
            continue
        group = group.sort_values(["observation_date", "_effective", "_available"])
        current = group.iloc[-1]
        prior = group.iloc[-2] if len(group) > 1 else None
        current_value = float(current["value"])
        previous_value = float(prior["value"]) if prior is not None else None
        item = {
            "name": LABELS.get(current["series_id"], current["series_id"]),
            "status": "available",
            "observation_period": current["observation_period"],
            "observation_date": current["observation_date"],
            "available_date": current["available_date"],
            "value": current_value,
            "previous_observation_period": prior["observation_period"]
            if prior is not None
            else None,
            "previous_value": previous_value,
            "change_vs_previous_release": current_value - previous_value
            if prior is not None
            else None,
            "comparison_basis": "previous published observation; periods may span multiple months",
            "unit": current["unit"],
            "reported_as": (
                "cumulative_ytd_yoy"
                if current["series_id"] in {"cn_fixed_asset_investment", "cn_retail_sales_ytd"}
                else current["unit"]
            ),
            "source_value_url": current["source_value_url"],
            "availability_evidence_url": current["availability_evidence_url"],
            "quality_flags": json.loads(current["quality_flags"] or "[]"),
        }
        result[current["series_id"]] = item
    return result


def _factor_contributions(
    panel: pd.DataFrame,
    factor_cfg: dict[str, Any],
    *,
    min_z_history: int,
) -> dict[str, Any]:
    if panel.empty:
        return {"status": "insufficient_data", "score": None, "contributions": {}}
    when = panel.index[-1]
    zscores: dict[str, float] = {}
    weights: dict[str, float] = {}
    for name, cfg in factor_cfg["components"].items():
        source = cfg["source"]
        if source not in panel:
            continue
        if cfg.get("transform") == "real_rate_proxy":
            infl = cfg.get("inflation_source")
            if infl not in panel:
                continue
            transformed = panel[source] - panel[infl]
        else:
            transformed = _transform_monthly(
                panel[source], cfg.get("transform", "level"), cfg.get("periods")
            )
        z = expanding_zscore(transformed * float(cfg.get("sign", 1.0)), min_periods=min_z_history)
        value = z.loc[when]
        if pd.notna(value):
            zscores[name] = float(value)
            weights[name] = float(cfg.get("weight", 1.0))
    if len(zscores) < int(factor_cfg.get("min_components", len(factor_cfg["components"]))):
        return {"status": "insufficient_data", "score": None, "contributions": {}}
    total_weight = sum(weights.values())
    contributions = {name: value * weights[name] / total_weight for name, value in zscores.items()}
    return {
        "status": "available",
        "score": sum(contributions.values()),
        "state": "above_expanding_mean"
        if sum(contributions.values()) > 0
        else "below_expanding_mean",
        "contributions": contributions,
    }


def build_china_macro_report(
    *,
    ledger_path: str | Path = "data/raw/china/china_macro_snapshots.csv",
    factor_config_path: str | Path = "config/factors.yaml",
    output_dir: str | Path = "reports/china_macro",
    as_of: str | date | None = None,
    min_z_history: int = 36,
    collection_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the latest JSON/Markdown report with past-only, revision-aware factors."""

    if min_z_history < 2:
        raise ValueError("min_z_history must be at least 2")
    cutoff = _as_of_date(as_of)
    ledger = read_watch_ledger(ledger_path)
    panel = build_vintage_aware_monthly_panel(ledger, as_of=cutoff.date())
    factor_config = load_factor_config(factor_config_path)["china"]
    factors = (
        build_country_factors(
            panel,
            factor_config,
            min_z_history=min_z_history,
            allow_missing_components=True,
        )
        if not panel.empty
        else pd.DataFrame()
    )

    factor_rows: dict[str, Any] = {}
    for name, cfg in factor_config.items():
        values = factors[name].dropna() if name in factors else pd.Series(dtype=float)
        latest = float(values.iloc[-1]) if not values.empty else None
        previous = float(values.iloc[-2]) if len(values) > 1 else None
        factor_rows[name] = {
            "status": "available" if latest is not None else "insufficient_data",
            "score": latest,
            "previous_score": previous,
            "change": latest - previous if latest is not None and previous is not None else None,
            "state": (
                "above_expanding_mean"
                if latest is not None and latest > 0
                else "below_expanding_mean"
                if latest is not None
                else "insufficient_data"
            ),
            "minimum_prior_observations": min_z_history,
            "contributions": _factor_contributions(panel, cfg, min_z_history=min_z_history)[
                "contributions"
            ],
        }

    if not factors.empty and {"growth", "inflation"}.issubset(factors.columns):
        regime_history = classify_regime(factors["growth"], factors["inflation"]).dropna()
    else:
        regime_history = pd.Series(dtype="string")
    if len(regime_history):
        current_regime = str(regime_history.iloc[-1])
        previous_regime = str(regime_history.iloc[-2]) if len(regime_history) > 1 else None
        regime = {
            "status": "available",
            "code": current_regime,
            "label": REGIME_LABELS.get(current_regime, current_regime),
            "previous_code": previous_regime,
            "changed": previous_regime is not None and previous_regime != current_regime,
            "method": "existing Growth/Inflation four-quadrant classifier",
            "liquidity_and_real_rate_are_context_only": True,
        }
    else:
        regime = {
            "status": "insufficient_data",
            "code": None,
            "label": "insufficient_data",
            "previous_code": None,
            "changed": False,
            "method": "existing Growth/Inflation four-quadrant classifier",
            "reason": "Growth and Inflation do not both meet the past-only minimum history.",
            "liquidity_and_real_rate_are_context_only": True,
        }

    metrics = _latest_metric_rows(ledger, cutoff)
    known = _eligible_versions(ledger, cutoff)
    represented = {key for key, group in known.groupby("series_id") if len(group)}
    official_rows = known
    coverage = {
        "configured_series": len(SERIES),
        "series_with_official_rows": len(represented),
        "series_without_official_rows": sorted(
            {spec["series_key"] for spec in SERIES.values()} - represented
        ),
        "official_release_rows": len(official_rows),
        "official_release_evidence_rate": (
            float(
                official_rows["availability_evidence_url"]
                .astype(str)
                .str.startswith(("https://", "http://"))
                .mean()
            )
            if not official_rows.empty
            else 0.0
        ),
        "unknown_release_date_rows_in_ledger": int(
            (ledger["availability_basis"] == "unknown").sum()
        )
        if not ledger.empty
        else 0,
    }
    archive_updated = None
    if not ledger.empty:
        retrieved = pd.to_datetime(ledger["retrieved_at"], errors="coerce").dropna()
        archive_updated = retrieved.max().isoformat() if not retrieved.empty else None
    if collection_audit is None:
        audit_path = Path(output_dir) / "collection_audit.json"
        audit = json.loads(audit_path.read_text(encoding="utf-8")) if audit_path.exists() else {}
    else:
        audit = collection_audit
    quality = audit.get("quality") or _quality_audit(ledger, [], cutoff.date().isoformat())
    report = {
        "schema_version": "china-macro-watch-report/1",
        "as_of": cutoff.date().isoformat(),
        "generated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(timespec="seconds"),
        "data_updated_at": archive_updated,
        "classification": {
            "observed_data": "Official reported values and evidenced publication dates.",
            "model_inference": "Past-only standardized factors and the Growth/Inflation quadrant label.",
            "trading_advice": False,
        },
        "metrics": metrics,
        "factors": factor_rows,
        "macro_state": regime,
        "coverage": coverage,
        "quality": quality,
        "collection": {
            "status": audit.get("status", "not_run"),
            "last_attempt_at": audit.get("finished_at"),
            "errors": audit.get("errors", []),
            "new_rows_appended": audit.get("new_rows_appended", 0),
        },
        "not_collected": [
            item
            for item in NOT_COLLECTED
            if metrics.get(item["series_id"], {}).get("status") != "available"
        ],
        "research_hypotheses": [
            {
                "hypothesis": "在增长因子持续改善的阶段，检验 A 股周期行业相对宽基指数的后续收益。",
                "type": "research hypothesis; no trade recommendation",
                "required_validation": [
                    "point-in-time prices",
                    "transaction costs",
                    "walk-forward out-of-sample",
                ],
            },
            {
                "hypothesis": "在通胀和流动性因子共同变化的阶段，检验债券、商品与股票的相对表现。",
                "type": "research hypothesis; no trade recommendation",
                "required_validation": [
                    "source release timing",
                    "historical revisions",
                    "out-of-sample stability",
                ],
            },
        ],
        "pit_policy": {
            "availability": "Rows without official release-date evidence are excluded from factor inputs.",
            "vintage": "At each month end, a revision is visible only from its stored vintage onward.",
            "standardization": "expanding_zscore uses history through t-1; the current row never enters its own mean/std.",
            "joint_jan_feb": "Joint-period labels remain intact; no January observation is synthesized.",
        },
    }
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "latest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (out / "latest.md").write_text(_render_markdown(report), encoding="utf-8")
    return report


def _render_markdown(report: dict[str, Any]) -> str:
    state = report["macro_state"]
    lines = [
        "# China Macro Data Watch",
        "",
        f"**截至 {report['as_of']}** · 数据更新 `{report.get('data_updated_at') or 'unknown'}` · 状态 **{state['label']}**",
        "",
        "## 四因子",
        "",
        "| 因子 | 状态 | 最新 z | 变化 |",
        "|---|---|---:|---:|",
    ]
    for name in ("growth", "inflation", "liquidity", "real_rate"):
        factor = report["factors"].get(name, {})
        score = factor.get("score")
        change = factor.get("change")
        score_text = f"{score:.2f}" if isinstance(score, (int, float)) else "—"
        change_text = f"{change:+.2f}" if isinstance(change, (int, float)) else "—"
        lines.append(
            f"| {name} | {factor.get('status', 'insufficient_data')} | {score_text} | {change_text} |"
        )
    lines.extend(
        [
            "",
            "状态分类使用 Growth / Inflation 四象限；Liquidity 与 Real Rate 提供背景因子。",
            "",
            "## 因子贡献",
            "",
            "| 因子 | 分项贡献（加权 z） |",
            "|---|---|",
        ]
    )
    for name in ("growth", "inflation", "liquidity", "real_rate"):
        contributions = report["factors"].get(name, {}).get("contributions", {})
        contribution_text = (
            "；".join(
                f"{COMPONENT_LABELS.get(component, component)} {value:+.2f}"
                for component, value in contributions.items()
            )
            or "—"
        )
        lines.append(f"| {name} | {contribution_text} |")
    lines.extend(
        [
            "",
            "贡献反映历史标准化分项对因子的数值影响，不代表因果。",
            "",
            "## 最新发布值",
            "",
            "| 指标 | 期间 | 最新值 | 前次 | 变化 | 发布日 |",
            "|---|---|---:|---:|---:|---|",
        ]
    )
    for series_id, metric in report["metrics"].items():
        if metric.get("status") == "not_collected":
            lines.append(f"| {metric.get('name', series_id)} | — | 尚未采集 | — | — | — |")
            continue
        if metric.get("status") == "unknown_release_date":
            unit = _display_unit(str(metric.get("unit", "")))
            lines.append(
                f"| {metric.get('name', series_id)} | {metric.get('observation_period', '—')} | "
                f"{metric.get('value', '—')}{unit} | — | — | unknown |"
            )
            continue
        value = metric["value"]
        previous = metric.get("previous_value")
        change = metric.get("change_vs_previous_release")
        unit = _display_unit(str(metric.get("unit", "")))
        change_unit = _display_change_unit(str(metric.get("unit", "")))
        release = metric.get("available_date", "unknown")
        evidence = metric.get("availability_evidence_url")
        release_cell = f"[{release}]({evidence})" if evidence else "unknown"
        previous_text = f"{previous:g}{unit}" if previous is not None else "—"
        change_text = f"{change:+g}{change_unit}" if change is not None else "—"
        lines.append(
            f"| {metric['name']} | {metric['observation_period']} | {value:g}{unit} | "
            f"{previous_text} | {change_text} | {release_cell} |"
        )
    quality = report.get("quality", {})
    lines.extend(
        [
            "",
            "## 数据质量",
            "",
            f"- 官方序列：{report['coverage']['series_with_official_rows']}/{report['coverage']['configured_series']}；已采集记录中发布日期证据率 {report['coverage']['official_release_evidence_rate']:.0%}。",
            f"- 新发布 {len(quality.get('new_releases', []))}；历史修订 {len(quality.get('historical_revisions', []))}；缺期 {len(quality.get('missing_periods', []))}；过期序列 {len(quality.get('stale_series', []))}；异常变化待复核 {len(quality.get('large_changes_for_review', []))}。",
            "- 发布时间延迟仅按观察期到实际发布日期的跨度筛查；没有将预定发布日期当成实际日期。",
            "",
            "## 研究假设",
            "",
        ]
    )
    lines.extend(
        f"- {item['hypothesis']}（待回测；非买卖建议）" for item in report["research_hypotheses"]
    )
    lines.extend(
        [
            "",
            "**口径**：表中数值和来源链接是客观数据；因子、状态与假设是模型推断。固定资产投资保留累计同比口径；1—2 月联合发布不拆分。发布日期缺证据的记录保留在原始账本，但不进入 PIT 因子。",
            "",
        ]
    )
    return "\n".join(lines)
