"""Run a reproducible 2x2x2 ablation of GLD/QQQ cash-flow controls.

The script reads committed-format local snapshots only; it never fetches prices.
Raw Yahoo files must remain in private personal-use storage and are not added to Git.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from itertools import product
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from mrq_cli.backtest_workbench.models import (
    CashFlowPlan,
    DrawdownRule,
    StrategySpec,
    TakeProfitTier,
)
from mrq_cli.backtest_workbench.prices import load_market_prices
from mrq_cli.backtest_workbench.runner import PortfolioRun, run_portfolio_backtest

TP_TIERS = (
    TakeProfitTier(return_threshold=0.20, sell_fraction=0.10),
    TakeProfitTier(return_threshold=0.35, sell_fraction=0.15),
    TakeProfitTier(return_threshold=0.50, sell_fraction=0.20),
)
DD_RULES = (
    DrawdownRule(trigger_drawdown=0.15, max_invested_weight=0.50),
    DrawdownRule(trigger_drawdown=0.25, max_invested_weight=0.25),
)
METRICS = (
    "twr_total_return",
    "xirr",
    "cagr",
    "vol",
    "sharpe",
    "max_drawdown",
    "ending_value",
    "ending_cash",
    "transaction_costs",
)


def run_ablation(
    *,
    project_root: Path,
    start_date: str,
    end_date: str,
    initial_capital: float,
    weekly_contribution: float,
    transaction_cost_bps: float,
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame, bytes]:
    _validate_local_snapshots(project_root)
    prices, price_sources = load_market_prices(
        ["GLD", "QQQ"],
        start=start_date,
        end=end_date,
        project_root=project_root,
    )
    aligned = prices.loc[:, ["GLD", "QQQ"]].dropna(how="any").sort_index()
    if len(aligned) < 2:
        raise ValueError("The requested dates contain fewer than two common GLD/QQQ prices")
    if aligned.isna().any().any() or (aligned <= 0).any().any():
        raise ValueError("Aligned price snapshot has missing or non-positive values")
    aligned_bytes = aligned.to_csv(index_label="date", float_format="%.12g").encode("utf-8")
    aligned_sha256 = hashlib.sha256(aligned_bytes).hexdigest()

    summaries: list[dict[str, Any]] = []
    monthly_returns: dict[str, pd.Series] = {}
    daily_series: dict[str, pd.Series] = {}

    for take_profit, drawdown, reinvest in product((False, True), repeat=3):
        experiment_id = _experiment_id(take_profit, drawdown, reinvest)
        strategy = StrategySpec(
            name=experiment_id,
            weights={"GLD": 0.5, "QQQ": 0.5},
            rebalance_frequency="buy_and_hold",
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
            transaction_cost_bps=transaction_cost_bps,
            cash_flow=CashFlowPlan(
                weekly_contribution_amount=weekly_contribution,
                contribution_day="FRI",
                reinvest_cash=reinvest,
                take_profit_tiers=TP_TIERS if take_profit else (),
                drawdown_rules=DD_RULES if drawdown else (),
            ),
        )
        run = run_portfolio_backtest(prices, strategy)
        summary = _summarize_run(
            experiment_id,
            take_profit,
            drawdown,
            reinvest,
            run,
        )
        summaries.append(summary)
        returns = run.result.returns
        monthly_returns[experiment_id] = returns.groupby(returns.index.to_period("M")).apply(
            lambda values: (1.0 + values).prod() - 1.0
        )
        unit_nav = run.unit_nav
        account_equity = run.account_equity
        if unit_nav is None or account_equity is None:
            raise RuntimeError(f"Cash-flow run {experiment_id} omitted account series")
        weights = run.result.ending_weights
        cash = weights["CASH"]
        drawdown_series = unit_nav / unit_nav.cummax() - 1.0
        for column, series in {
            f"{experiment_id}_daily_twr_return": returns,
            f"{experiment_id}_unit_nav": unit_nav,
            f"{experiment_id}_account_equity": account_equity,
            f"{experiment_id}_cash_weight": cash,
            f"{experiment_id}_risk_asset_weight": weights.drop(columns="CASH").sum(axis=1),
            f"{experiment_id}_portfolio_weight_sum": weights.sum(axis=1),
            f"{experiment_id}_drawdown": drawdown_series,
        }.items():
            daily_series[column] = series

    monthly = pd.DataFrame(monthly_returns).sort_index()
    monthly.index.name = "month"
    daily = pd.DataFrame(daily_series).sort_index()
    daily.index.name = "date"
    summary_by_id = {row["experiment_id"]: row for row in summaries}
    no_cash_controls = summary_by_id["TP0_DD0_RI0"]
    reinvest_without_sales = summary_by_id["TP0_DD0_RI1"]
    identity_metrics = ("twr_total_return", "xirr", "cagr", "max_drawdown", "ending_value")
    if any(
        not np.isclose(
            no_cash_controls[metric],
            reinvest_without_sales[metric],
            rtol=0.0,
            atol=1e-12,
        )
        for metric in identity_metrics
    ):
        raise RuntimeError("Cash-reinvestment toggle changed a run with no sale proceeds")
    cash_columns = [column for column in daily if column.endswith("_cash_weight")]
    min_cash_weight = float(daily[cash_columns].min().min())
    if min_cash_weight < -1e-10:
        raise RuntimeError(f"A portfolio series has negative cash weight: {min_cash_weight}")
    portfolio_weight_columns = daily.filter(like="_portfolio_weight_sum")
    max_weight_sum_error = float((portfolio_weight_columns - 1.0).abs().max().max())
    if max_weight_sum_error > 1e-10:
        raise RuntimeError(f"Portfolio weights do not sum to one: {max_weight_sum_error}")
    max_unit_nav = max(abs(float(value)) for value in daily.filter(like="_unit_nav").to_numpy().ravel())
    if not np.isfinite(max_unit_nav):
        raise RuntimeError("A portfolio unit NAV is non-finite")
    paired_effects = _paired_effects(summaries)
    raw_hashes = {
        source.symbol: source.content_sha256 for source in price_sources
    }
    historical_reference_check = _historical_reference_check(
        aligned_sha256=aligned_sha256,
        summaries=summaries,
        start_date=start_date,
        end_date=end_date,
        initial_capital=initial_capital,
        weekly_contribution=weekly_contribution,
        transaction_cost_bps=transaction_cost_bps,
    )
    focused_comparisons = _focused_comparisons(summaries)
    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "data": {
            "symbols": ["GLD", "QQQ"],
            "price_basis": "Local Yahoo auto-adjusted daily close (adjusted_close)",
            "point_in_time_vintage": False,
            "start_date": aligned.index.min().date().isoformat(),
            "end_date": aligned.index.max().date().isoformat(),
            "observations": len(aligned),
            "join": "Inner join of common observed sessions; no fill or interpolation.",
            "source_files_sha256": raw_hashes,
            "aligned_snapshot_sha256": aligned_sha256,
            "price_sources": [source.to_dict() for source in price_sources],
        },
        "assumptions": {
            "initial_capital": initial_capital,
            "weekly_contribution": weekly_contribution,
            "contribution_day": "FRI (last available close in each Friday-ending trading week)",
            "weights": {"GLD": 0.5, "QQQ": 0.5},
            "transaction_cost_bps": transaction_cost_bps,
            "take_profit_tiers": [tier.__dict__ for tier in TP_TIERS],
            "drawdown_rules": [rule.__dict__ for rule in DD_RULES],
            "cash_reinvestment": (
                "On each contribution date, reinvest eligible idle cash from earlier periods "
                "before investing that day's new deposit, subject to the active drawdown cap."
            ),
            "take_profit_execution": "Signal at close; sell at the next available close.",
            "drawdown_execution": "Signal at close; apply exposure cap at the next available close.",
            "cash_yield": 0.0,
            "taxes_and_market_impact": "Excluded; cost is applied to gross traded notional.",
        },
        "experiment_design": [
            "Full 2x2x2 factorial: each of take-profit, drawdown control, and cash reinvestment is independently enabled or disabled.",
            "Every variant uses the same prices, start/end dates, initial capital, weekly contributions, and transaction costs.",
            "Factor effects are paired differences averaged across the four settings of the other two factors; interactions remain possible.",
        ],
        "experiments": summaries,
        "paired_effects": paired_effects,
        "focused_comparisons": focused_comparisons,
        "quality_checks": {
            "no_control_reinvestment_identity": True,
            "minimum_cash_weight": min_cash_weight,
            "maximum_weight_sum_error": max_weight_sum_error,
            "daily_unit_nav_finite": True,
            "aligned_snapshot_sha256": aligned_sha256,
            "historical_reference_check": historical_reference_check,
        },
        "artifacts": {
            "aligned_snapshot": "aligned_market_snapshot.csv",
            "daily_portfolio_series": "daily_portfolio_series.csv",
            "monthly_returns": "monthly_returns.csv",
        },
        "limitations": [
            "One historical window and one parameter set; results are descriptive, not proof of a persistent edge.",
            "Adjusted closes are retrospectively revised and are not a point-in-time vintage.",
            "Cash yield is zero; no Treasury alternative, currency conversion, tax, fund premium/discount, or market impact is modeled.",
            "The 15% and 25% drawdown triggers are exposure controls, not a 10% maximum drawdown guarantee.",
        ],
    }
    return report, daily, monthly, aligned_bytes


def _historical_reference_check(
    *,
    aligned_sha256: str,
    summaries: list[dict[str, Any]],
    start_date: str,
    end_date: str,
    initial_capital: float,
    weekly_contribution: float,
    transaction_cost_bps: float,
) -> dict[str, Any]:
    if (
        aligned_sha256 != "2ea570c4ea6ca77c043f00abb8a408f3277a11a4338078be347c1e667ecc3062"
        or start_date != "2004-11-18"
        or end_date != "2026-10-09"
        or initial_capital != 10_000.0
        or weekly_contribution != 50.0
        or transaction_cost_bps != 5.0
    ):
        return {"status": "not_applicable", "reason": "Snapshot or base assumptions differ."}

    expected = {
        "TP0_DD0_RI0": {
            "twr_total_return": 16.134561169114992,
            "xirr": 0.14415869889949434,
            "ending_value": 540_958.5624888747,
            "max_drawdown": -0.3094826432477483,
            "transaction_costs": 33.558220889555194,
        },
        "TP1_DD1_RI0": {
            "twr_total_return": 6.8155653051770715,
            "xirr": 0.1046859333403484,
            "ending_value": 294_031.96016691526,
            "max_drawdown": -0.23690445830617868,
            "transaction_costs": 287.7487513358448,
        },
    }
    actual_by_id = {row["experiment_id"]: row for row in summaries}
    comparisons: list[dict[str, Any]] = []
    for experiment_id, metrics in expected.items():
        actual = actual_by_id[experiment_id]
        differences = {metric: float(actual[metric] - value) for metric, value in metrics.items()}
        passed = all(
            abs(differences[metric]) <= (0.01 if metric in {"ending_value", "transaction_costs"} else 1e-10)
            for metric in metrics
        )
        comparisons.append(
            {
                "experiment_id": experiment_id,
                "status": "passed" if passed else "failed",
                "expected": metrics,
                "observed": {metric: actual[metric] for metric in metrics},
                "difference": differences,
            }
        )
    status = "passed" if all(row["status"] == "passed" for row in comparisons) else "failed"
    if status == "failed":
        raise RuntimeError("Historical reproduction check differs from the saved reference report")
    return {"status": status, "comparisons": comparisons}


def _focused_comparisons(summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {row["experiment_id"]: row for row in summaries}
    pairs = (
        ("止盈单独效果", "TP0_DD0_RI0", "TP1_DD0_RI0"),
        ("回撤限仓单独效果", "TP0_DD0_RI0", "TP0_DD1_RI0"),
        ("止盈条件下增加回撤限仓", "TP1_DD0_RI0", "TP1_DD1_RI0"),
        ("止盈产生的现金再投资", "TP1_DD0_RI0", "TP1_DD0_RI1"),
        ("止盈与回撤同时启用时再投资", "TP1_DD1_RI0", "TP1_DD1_RI1"),
    )
    return [
        {
            "comparison": label,
            "baseline_id": baseline_id,
            "variant_id": variant_id,
            "delta": {
                metric: float(by_id[variant_id][metric] - by_id[baseline_id][metric])
                for metric in METRICS
            },
        }
        for label, baseline_id, variant_id in pairs
    ]


def _summarize_run(
    experiment_id: str,
    take_profit: bool,
    drawdown: bool,
    reinvest: bool,
    run: PortfolioRun,
) -> dict[str, Any]:
    if run.unit_nav is None or run.account_equity is None or run.cash_flow_details is None:
        raise RuntimeError(f"Cash-flow run {experiment_id} omitted required accounting details")
    details = run.cash_flow_details
    events = details["events"]
    trades = details["trades"]
    event_counts = {
        "take_profit_executed": sum(event["event"] == "take_profit_executed" for event in events),
        "drawdown_control_executed": sum(
            event["event"] == "drawdown_control_executed" for event in events
        ),
        "cash_reinvestment_executed": sum(
            event["event"] == "cash_reinvestment_executed" for event in events
        ),
    }
    return {
        "experiment_id": experiment_id,
        "take_profit_enabled": take_profit,
        "drawdown_control_enabled": drawdown,
        "cash_reinvestment_enabled": reinvest,
        "twr_total_return": float(run.unit_nav.iloc[-1] - 1.0),
        "xirr": details["xirr"],
        "cagr": float(run.result.metrics["cagr"]),
        "vol": float(run.result.metrics["vol"]),
        "sharpe": float(run.result.metrics["sharpe"]),
        "max_drawdown": float(run.result.metrics["max_drawdown"]),
        "ending_value": float(run.account_equity.iloc[-1]),
        "total_contributions": float(details["total_contributions"]),
        "ending_cash": float(details["ending_cash"]),
        "ending_cash_weight": float(run.result.ending_weights["CASH"].iloc[-1]),
        "transaction_costs": float(details["transaction_costs"]),
        "trade_count": int(details["trade_count"]),
        "total_turnover": float(run.result.turnover.sum()),
        "event_counts": event_counts,
        "events": events,
        "trades": trades,
        "pending_events": details["pending_events"],
    }


def _paired_effects(summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_flags = {
        (
            row["take_profit_enabled"],
            row["drawdown_control_enabled"],
            row["cash_reinvestment_enabled"],
        ): row
        for row in summaries
    }
    effects: list[dict[str, Any]] = []
    feature_positions = {
        "take_profit": 0,
        "drawdown_control": 1,
        "cash_reinvestment": 2,
    }
    for feature, position in feature_positions.items():
        other_positions = [index for index in range(3) if index != position]
        matched: list[dict[str, Any]] = []
        for other_values in product((False, True), repeat=2):
            disabled = [False, False, False]
            enabled = [False, False, False]
            disabled[position] = False
            enabled[position] = True
            for index, value in zip(other_positions, other_values, strict=True):
                disabled[index] = value
                enabled[index] = value
            without = by_flags[tuple(disabled)]
            with_feature = by_flags[tuple(enabled)]
            matched.append(
                {
                    "other_features": {
                        key: bool(enabled[flag_position])
                        for key, flag_position in feature_positions.items()
                        if key != feature
                    },
                    "without": without["experiment_id"],
                    "with": with_feature["experiment_id"],
                    "delta": {
                        metric: float(with_feature[metric] - without[metric])
                        for metric in METRICS
                    },
                }
            )
        average = {
            metric: float(np.mean([pair["delta"][metric] for pair in matched]))
            for metric in METRICS
        }
        effects.append({"feature": feature, "average_paired_delta": average, "pairs": matched})
    return effects


def _experiment_id(take_profit: bool, drawdown: bool, reinvest: bool) -> str:
    return f"TP{int(take_profit)}_DD{int(drawdown)}_RI{int(reinvest)}"


def _validate_local_snapshots(project_root: Path) -> None:
    snapshot_root = project_root / "data" / "raw" / "market"
    required_metadata = {
        "origin_provider",
        "source_url",
        "retrieved_at",
        "request_start",
        "request_end_exclusive",
        "price_field",
    }
    for symbol in ("GLD", "QQQ"):
        csv_path = snapshot_root / f"{symbol}.csv"
        metadata_path = snapshot_root / f"{symbol}.csv.meta.json"
        if not csv_path.is_file() or not metadata_path.is_file():
            raise FileNotFoundError(
                f"Expected private snapshot and metadata: {csv_path} and {metadata_path}"
            )
        metadata = json.loads(metadata_path.read_text("utf-8"))
        missing = sorted(required_metadata - metadata.keys())
        if missing:
            raise ValueError(f"{metadata_path} is missing provenance fields: {missing}")
        if metadata["price_field"] != "adjusted_close":
            raise ValueError(f"{metadata_path} must identify adjusted_close as its price field")


def _render_markdown(report: dict[str, Any]) -> str:
    data = report["data"]
    assumptions = report["assumptions"]
    lines = [
        "# GLD / QQQ 策略机制消融报告",
        "",
        f"**区间：** {data['start_date']} 至 {data['end_date']}；共同交易日 {data['observations']:,} 个。",
        f"**快照 SHA-256：** `{data['aligned_snapshot_sha256']}`",
        "",
        "## 设计",
        "",
        "全因子 2×2×2：分别开关阶梯止盈、回撤限仓、现金再投资。八种组合共用同一行情快照、每周投入、费率、日期区间与权重。新贡献当周按目标 50/50 投入；已持有闲置现金只在启用再投资时，按每周节奏、且在满足当前回撤限仓后投入。收盘触发的止盈/回撤信号次个可用交易日执行。",
        "",
        f"参数：初始 ${assumptions['initial_capital']:,.0f}；每周 ${assumptions['weekly_contribution']:,.0f}；交易费用 {assumptions['transaction_cost_bps']:.1f} bps；止盈档位 20/35/50%（卖出当时风险持仓的 10/15/20%）；回撤限仓 15%→50%、25%→25%。现金收益率为 0%。",
        "",
        "## 八种组合",
        "",
        "| 组合 | 止盈 | 回撤限仓 | 现金再投资 | 期末账户 | XIRR | TWR 总收益 | CAGR | 年化波动 | 夏普 | 最大回撤 | 现金占比 | 费用 | 交易笔数 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report["experiments"]:
        lines.append(
            "| {experiment_id} | {tp} | {dd} | {ri} | ${ending_value:,.0f} | {xirr:.2%} | {twr_total_return:.2%} | {cagr:.2%} | {vol:.2%} | {sharpe:.3f} | {max_drawdown:.2%} | {ending_cash_weight:.1%} | ${transaction_costs:,.0f} | {trade_count:,} |".format(
                tp="开" if row["take_profit_enabled"] else "关",
                dd="开" if row["drawdown_control_enabled"] else "关",
                ri="开" if row["cash_reinvestment_enabled"] else "关",
                **row,
            )
        )
    lines.extend(
        [
            "",
            "## 单机制的配对平均差异",
            "",
            "以下为每个机制开/关的配对差值，再对其他两个机制的四种状态取平均。最大回撤差值为正表示回撤变浅。该平均值不消除机制交互，也不能外推为未来效果。",
            "",
            "| 机制 | Δ XIRR | Δ TWR | Δ最大回撤 | Δ期末账户 | Δ费用 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for effect in report["paired_effects"]:
        delta = effect["average_paired_delta"]
        feature_label = {
            "take_profit": "阶梯止盈",
            "drawdown_control": "回撤限仓",
            "cash_reinvestment": "现金再投资",
        }[effect["feature"]]
        lines.append(
            f"| {feature_label} | {delta['xirr'] * 100:+.2f} pp | {delta['twr_total_return'] * 100:+.2f} pp | {delta['max_drawdown'] * 100:+.2f} pp | ${delta['ending_value']:+,.0f} | ${delta['transaction_costs']:+,.0f} |"
        )
    lines.extend(
        [
            "",
            "## 关键配对",
            "",
            "| 比较 | 对照 | 变体 | Δ期末账户 | ΔXIRR | Δ最大回撤 |",
            "|---|---|---|---:|---:|---:|",
        ]
    )
    for comparison in report["focused_comparisons"]:
        delta = comparison["delta"]
        lines.append(
            f"| {comparison['comparison']} | {comparison['baseline_id']} | {comparison['variant_id']} | ${delta['ending_value']:+,.0f} | {delta['xirr'] * 100:+.2f} pp | {delta['max_drawdown'] * 100:+.2f} pp |"
        )
    reference = report["quality_checks"]["historical_reference_check"]
    lines.extend(["", "## 历史报告独立复现", ""])
    if reference["status"] == "passed":
        lines.append(
            "旧报告中的 DCA-only 与「止盈 + 回撤限仓」两组结果均使用同一行情文件和实现重新计算，并通过冻结参考值核验（期末金额误差不超过 $0.01，其余误差不超过 1e-10）。"
        )
        for comparison in reference["comparisons"]:
            observed = comparison["observed"]
            lines.append(
                f"- `{comparison['experiment_id']}`：期末 ${observed['ending_value']:,.2f}；XIRR {observed['xirr']:.4%}；TWR {observed['twr_total_return']:.4%}；最大回撤 {observed['max_drawdown']:.4%}。"
            )
    else:
        lines.append("当前输入快照或假设与旧报告不同，未执行旧报告黄金值核验。")
    lines.append("对照 sanity check：不产生销售现金时，现金再投资开关结果相同；八组日度现金权重非负、资产加现金权重合计为 100%。")
    lines.extend(
        [
            "",
            "## 数据来源与可复现文件",
            "",
            "| 标的 | 原始 CSV SHA-256 | 覆盖区间 | 观察数 | 获取时间 |",
            "|---|---|---|---:|---|",
        ]
    )
    for source in data["price_sources"]:
        lines.append(
            f"| {source['symbol']} | `{source['content_sha256']}` | {source['first_date']}–{source['last_date']} | {source['observations']:,} | {source['retrieved_at']} |"
        )
    lines.extend(
        [
            "",
            "- `aligned_market_snapshot.csv`：两只资产 inner join 后的精确输入矩阵。",
            "- `daily_portfolio_series.csv`：八种组合的逐日 TWR、单位净值、账户净值、现金权重和回撤。",
            "- `monthly_returns.csv`：八种组合的完整月度 TWR 序列。",
            "- `report.json`：参数、全部汇总指标、每个实验的事件与交易日志、配对差异。",
            "- `manifest.json`：来源、原始文件哈希、对齐输入哈希与复现设置。",
            "",
            "复现命令（先将私有 `GLD.csv`、`QQQ.csv` 及对应 `.meta.json` 放入 `data/raw/market/`）：",
            "",
            "```bash",
            "uv sync --locked --all-packages --group dev",
            "uv run --locked python scripts/backtest_mechanism_ablation.py \\",
            "  --project-root . \\",
            "  --start-date 2004-11-18 \\",
            "  --end-date 2026-10-09 \\",
            "  --output-dir reports/backtests/gld_qqq_mechanism_ablation",
            "```",
            "",
            "## 限制",
            "",
            "这是单一历史区间、单组参数的描述性回测，不是稳健性或样本外证明。复权价不是 point-in-time vintage；未计税、现金利息、短债收益、汇兑、基金溢折价和额外冲击成本。15%/25% 规则是仓位限制，不能保证 10% 最大回撤。",
        ]
    )
    return "\n".join(lines) + "\n"


def _git_commit(project_root: Path) -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            capture_output=True,
            check=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root containing the configured catalog and private data/raw/market files.",
    )
    parser.add_argument("--start-date", default="2004-11-18")
    parser.add_argument("--end-date", default="2026-10-09")
    parser.add_argument("--initial-capital", type=float, default=10_000.0)
    parser.add_argument("--weekly-contribution", type=float, default=50.0)
    parser.add_argument("--transaction-cost-bps", type=float, default=5.0)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/backtests/gld_qqq_mechanism_ablation"),
    )
    args = parser.parse_args()
    project_root = args.project_root.expanduser().resolve()
    output_dir = args.output_dir.expanduser()
    if not output_dir.is_absolute():
        output_dir = (project_root / output_dir).resolve()
    report, daily, monthly, aligned_bytes = run_ablation(
        project_root=project_root,
        start_date=args.start_date,
        end_date=args.end_date,
        initial_capital=args.initial_capital,
        weekly_contribution=args.weekly_contribution,
        transaction_cost_bps=args.transaction_cost_bps,
    )
    report["code_commit"] = _git_commit(project_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "aligned_market_snapshot.csv").write_bytes(aligned_bytes)
    daily.to_csv(output_dir / "daily_portfolio_series.csv", float_format="%.12g")
    monthly.to_csv(output_dir / "monthly_returns.csv", float_format="%.12g")
    (output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (output_dir / "manifest.json").write_text(
        json.dumps({"data": report["data"], "assumptions": report["assumptions"], "code_commit": report["code_commit"]}, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (output_dir / "report.md").write_text(_render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "ok",
                "output_dir": str(output_dir),
                "observations": report["data"]["observations"],
                "aligned_snapshot_sha256": report["data"]["aligned_snapshot_sha256"],
                "experiment_count": len(report["experiments"]),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
