"""Compare corrected GLD/QQQ timing and annual rebalancing on private snapshots.

The script reads only existing local market CSVs. It does not download prices and
does not add private input snapshots to Git.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from mrq_cli.backtest_workbench.models import StrategySpec
from mrq_cli.backtest_workbench.prices import load_market_prices
from mrq_cli.backtest_workbench.runner import (
    _is_rebalance_date,
    _select_common_complete_prices,
    run_portfolio_backtest,
)

LEGACY_REFERENCE = {
    "buy_and_hold": {
        "ending_value": 156_542.09,
        "cagr": 0.1342,
        "vol": 0.1558,
        "sharpe": 0.886,
        "max_drawdown": -0.3039,
    },
    "annual": {
        "ending_value": 168_826.58,
        "cagr": 0.1381,
        "vol": 0.1439,
        "sharpe": 0.971,
        "max_drawdown": -0.3265,
    },
}


def run_audit(
    *,
    project_root: Path,
    start_date: str,
    end_date: str,
    initial_capital: float,
    transaction_cost_bps: float,
) -> tuple[dict[str, Any], pd.DataFrame, bytes]:
    prices, sources = load_market_prices(
        ["GLD", "QQQ"],
        start=start_date,
        end=end_date,
        project_root=project_root,
    )
    prices, dropped_rows = _select_common_complete_prices(prices.loc[:, ["GLD", "QQQ"]].sort_index())
    if len(prices) < 2:
        raise ValueError("The requested dates contain fewer than two common complete observations")
    aligned_bytes = prices.to_csv(index_label="date", float_format="%.12g").encode("utf-8")
    aligned_sha256 = hashlib.sha256(aligned_bytes).hexdigest()

    results: dict[str, Any] = {}
    daily: dict[str, pd.Series] = {}
    for frequency in ("buy_and_hold", "annual"):
        strategy = StrategySpec(
            name=f"GLD / QQQ 50/50 {frequency}",
            weights={"GLD": 0.5, "QQQ": 0.5},
            rebalance_frequency=frequency,
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
            transaction_cost_bps=transaction_cost_bps,
        )
        run = run_portfolio_backtest(prices, strategy)
        returns = run.result.returns
        independent = _independent_close_ledger(
            prices,
            frequency=frequency,
            transaction_cost_bps=transaction_cost_bps,
        )
        difference = (returns - independent).abs()
        if not np.allclose(returns.to_numpy(), independent.to_numpy(), rtol=0.0, atol=1e-12):
            raise RuntimeError(f"Independent close ledger disagrees for {frequency}")

        equity = initial_capital * (1.0 + returns).cumprod()
        annual_returns = returns.groupby(returns.index.year).apply(
            lambda values: float((1.0 + values).prod() - 1.0)
        )
        drawdown = equity / equity.cummax().clip(lower=initial_capital) - 1.0
        results[frequency] = {
            "name": strategy.name,
            "rebalance_frequency": frequency,
            "initial_capital": initial_capital,
            "transaction_cost_bps": transaction_cost_bps,
            "ending_value": float(equity.iloc[-1]),
            "total_return": float((1.0 + returns).prod() - 1.0),
            **{key: float(value) for key, value in run.result.metrics.items()},
            "max_drawdown_date": drawdown.idxmin().date().isoformat(),
            "stress_year_returns": {
                str(year): (
                    None
                    if pd.isna(annual_returns.get(year, np.nan))
                    else float(annual_returns.get(year))
                )
                for year in (2008, 2020, 2022)
            },
            "first_fill_date": run.result.ending_weights.index[
                run.result.ending_weights.drop(columns="CASH").sum(axis=1).to_numpy() > 0
            ][0].date().isoformat(),
            "independent_reference": {
                "status": "passed",
                "max_abs_return_difference": float(difference.max()),
            },
            "old_report_comparison": {
                key: {
                    "legacy": float(value),
                    "corrected": float(
                        equity.iloc[-1]
                        if key == "ending_value"
                        else run.result.metrics[key]
                    ),
                    "delta": float(
                        equity.iloc[-1]
                        if key == "ending_value"
                        else run.result.metrics[key]
                    )
                    - float(value),
                }
                for key, value in LEGACY_REFERENCE[frequency].items()
            },
        }
        daily[f"{frequency}_return"] = returns
        daily[f"{frequency}_equity"] = equity
        daily[f"{frequency}_drawdown"] = drawdown
        daily[f"{frequency}_GLD_weight"] = run.result.ending_weights["GLD"]
        daily[f"{frequency}_QQQ_weight"] = run.result.ending_weights["QQQ"]
        daily[f"{frequency}_cash_weight"] = run.result.ending_weights["CASH"]

    daily_frame = pd.DataFrame(daily)
    daily_frame.index.name = "date"
    report = {
        "schema_version": 1,
        "accounting_semantics": "close-fill-after-mark-to-market-v2",
        "generated_at": datetime.now(UTC).isoformat(),
        "code_commit": _git_commit(project_root),
        "data": {
            "symbols": ["GLD", "QQQ"],
            "start_date": prices.index.min().date().isoformat(),
            "end_date": prices.index.max().date().isoformat(),
            "observations": len(prices),
            "dropped_nonoverlap_rows": dropped_rows,
            "aligned_price_sha256": aligned_sha256,
            "price_sources": [source.to_dict() for source in sources],
            "point_in_time_vintage": False,
        },
        "assumptions": {
            "weights": {"GLD": 0.5, "QQQ": 0.5},
            "initial_capital": initial_capital,
            "transaction_cost_bps": transaction_cost_bps,
            "cash_yield": 0.0,
            "signal_execution": "Signal at close t fills at the next available close. The return ending on the fill date belongs to the old holdings.",
            "periodic_rebalance": "On a calendar boundary, signal at the previous available close and fill at the first next-period close.",
        },
        "results": results,
        "quality_checks": {
            "independent_reference_passed": all(
                row["independent_reference"]["status"] == "passed" for row in results.values()
            ),
            "minimum_cash_weight": float(daily_frame.filter(like="cash_weight").min().min()),
            "maximum_weight_sum_error": float(
                max(
                    (daily_frame[[f"{frequency}_GLD_weight", f"{frequency}_QQQ_weight", f"{frequency}_cash_weight"]].sum(axis=1) - 1.0).abs().max()
                    for frequency in results
                )
            ),
            "legacy_results_preserved_as_comparison_only": True,
        },
        "limitations": [
            "Yahoo adjusted prices are revised histories, not point-in-time vintages.",
            "The engine requires an explicitly aligned panel for assets with different market calendars; it does not infer holidays.",
            "The report uses USD assets, zero-yield residual cash, and fixed transaction costs; taxes, FX, impact, and fund premiums are excluded.",
            "Historical drawdowns describe this sample only and do not bound future losses.",
        ],
        "artifacts": {
            "aligned_market_snapshot": "aligned_market_snapshot.csv",
            "daily_series": "daily_series.csv",
        },
    }
    return report, daily_frame, aligned_bytes


def _independent_close_ledger(
    prices: pd.DataFrame,
    *,
    frequency: str,
    transaction_cost_bps: float,
) -> pd.Series:
    """Pure dollar ledger independent of the weighted-return engine."""

    prices = prices.sort_index().astype(float)
    symbols = list(prices.columns)
    held_value = {symbol: 0.0 for symbol in symbols}
    cash = 1.0
    previous_equity = 1.0
    rate = transaction_cost_bps / 10_000.0
    fill_positions = {1}
    for position in range(1, len(prices)):
        if _is_rebalance_date(prices.index[position - 1], prices.index[position], frequency):
            fill_positions.add(position)

    returns: list[float] = []
    for position, trade_date in enumerate(prices.index):
        if position:
            previous_date = prices.index[position - 1]
            for symbol in symbols:
                held_value[symbol] *= float(prices.loc[trade_date, symbol] / prices.loc[previous_date, symbol])
        marked_equity = sum(held_value.values()) + cash
        if position in fill_positions:
            current_weights = {
                symbol: held_value[symbol] / marked_equity for symbol in symbols
            }
            turnover = sum(abs(0.5 - current_weights[symbol]) for symbol in symbols)
            fees = marked_equity * turnover * rate
            ending_equity = marked_equity - fees
            held_value = {symbol: ending_equity * 0.5 for symbol in symbols}
            cash = 0.0
        else:
            ending_equity = marked_equity
        returns.append(ending_equity / previous_equity - 1.0)
        previous_equity = ending_equity
    return pd.Series(returns, index=prices.index, name="independent_return")


def _git_commit(root: Path) -> str | None:
    if configured := os.environ.get("MRQ_CODE_VERSION"):
        return configured
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            check=True,
            text=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=root,
            capture_output=True,
            check=True,
            text=True,
        ).stdout.strip()
        return f"{commit}-dirty" if dirty else commit
    except (OSError, subprocess.CalledProcessError):
        return None


def _markdown(report: dict[str, Any]) -> str:
    data = report["data"]
    lines = [
        "# GLD / QQQ 成交时序修正复核",
        "",
        f"区间：{data['start_date']} 至 {data['end_date']}；共同观察 {data['observations']:,} 个。",
        f"输入 SHA-256：`{data['aligned_price_sha256']}`；代码提交：`{report['code_commit']}`。",
        "",
        "信号在 t 收盘产生，下一可用收盘成交。成交日期收盘前的 close-to-close 行情由旧持仓获得，新持仓从成交后开始计收益。初始策略信号也遵守该规则，因此首个成交在第二个共同收盘。",
        "",
        "## 全样本结果",
        "",
        "| 组合 | 期末金额 | 总收益 | CAGR | 年化波动 | Sharpe | 最大回撤 | 最大回撤日期 | 独立账本最大误差 |",
        "|---|---:|---:|---:|---:|---:|---:|---|---:|",
    ]
    for row in report["results"].values():
        lines.append(
            f"| {row['rebalance_frequency']} | ${row['ending_value']:,.2f} | {row['total_return']:.2%} | {row['cagr']:.2%} | {row['vol']:.2%} | {row['sharpe']:.3f} | {row['max_drawdown']:.2%} | {row['max_drawdown_date']} | {row['independent_reference']['max_abs_return_difference']:.2g} |"
        )
    lines.extend(
        [
            "",
            "## 风险年份收益",
            "",
            "| 年份 | 买入持有 | 年度再平衡 |",
            "|---:|---:|---:|",
        ]
    )
    for year in (2008, 2020, 2022):
        buy_hold_return = report["results"]["buy_and_hold"]["stress_year_returns"][str(year)]
        annual_return = report["results"]["annual"]["stress_year_returns"][str(year)]
        buy_hold_value = "—" if buy_hold_return is None else f"{buy_hold_return:.2%}"
        annual_value = "—" if annual_return is None else f"{annual_return:.2%}"
        lines.append(
            f"| {year} | {buy_hold_value} | {annual_value} |"
        )
    lines.extend(["", "## 与旧报告期末金额的差异", "", "| 组合 | 旧报告 | 修正后 | 差值 |", "|---|---:|---:|---:|"])
    for frequency, row in report["results"].items():
        comparison = row["old_report_comparison"]["ending_value"]
        lines.append(
            f"| {frequency} | ${comparison['legacy']:,.2f} | ${comparison['corrected']:,.2f} | ${comparison['delta']:+,.2f} |"
        )
    lines.extend(
        [
            "",
            "## 检查与限制",
            "",
            "独立美元账本逐日重算每次持仓市值、换仓名义金额和手续费；与引擎每日净收益最大误差需小于 1e-12。旧报告结果只作为差异对照，绝不作为新代码的匹配目标。",
            "",
            *[f"- {note}" for note in report["limitations"]],
            "",
            "复现命令：",
            "",
            "```bash",
            "uv sync --locked --all-packages --group dev",
            "uv run --locked python scripts/backtest_p0_timing_audit.py \\",
            "  --project-root . \\",
            "  --start-date 2004-11-18 \\",
            "  --end-date 2026-10-09 \\",
            "  --output-dir reports/backtests/gld_qqq_close_timing_v2",
            "```",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--start-date", default="2004-11-18")
    parser.add_argument("--end-date", default="2026-10-09")
    parser.add_argument("--initial-capital", type=float, default=10_000.0)
    parser.add_argument("--transaction-cost-bps", type=float, default=5.0)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/backtests/gld_qqq_close_timing_v2"),
    )
    args = parser.parse_args()
    project_root = args.project_root.expanduser().resolve()
    output_dir = args.output_dir.expanduser()
    if not output_dir.is_absolute():
        output_dir = (project_root / output_dir).resolve()
    report, daily, aligned = run_audit(
        project_root=project_root,
        start_date=args.start_date,
        end_date=args.end_date,
        initial_capital=args.initial_capital,
        transaction_cost_bps=args.transaction_cost_bps,
    )
    report["code_commit"] = _git_commit(project_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "aligned_market_snapshot.csv").write_bytes(aligned)
    daily.to_csv(output_dir / "daily_series.csv", float_format="%.12g")
    (output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (output_dir / "report.md").write_text(_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "ok",
                "output_dir": str(output_dir),
                "aligned_price_sha256": report["data"]["aligned_price_sha256"],
                "independent_reference_passed": report["quality_checks"]["independent_reference_passed"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
