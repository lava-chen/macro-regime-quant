from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .models import StrategySpec
from .runner import PortfolioRun


def build_backtest_report(
    run_id: str,
    strategy: StrategySpec,
    portfolio_run: PortfolioRun,
    *,
    price_sources: list[dict[str, object]],
    benchmarks: dict[str, dict[str, float | None]],
    input_sha256: str,
    generated_at: str,
    code_version: str,
) -> dict[str, Any]:
    returns = portfolio_run.result.returns
    cash_flow_details = portfolio_run.cash_flow_details
    if portfolio_run.account_equity is not None and portfolio_run.unit_nav is not None:
        equity = portfolio_run.account_equity
        drawdown_basis = portfolio_run.unit_nav
        total_return = float(portfolio_run.unit_nav.iloc[-1] - 1.0)
    else:
        equity = strategy.initial_capital * (1.0 + returns).cumprod()
        drawdown_basis = equity
        total_return = float(equity.iloc[-1] / strategy.initial_capital - 1.0)
    gross_growth = float((1.0 + portfolio_run.result.gross_returns).prod())
    drawdown = drawdown_basis / drawdown_basis.cummax() - 1.0
    monthly = returns.groupby(returns.index.to_period("M")).apply(lambda values: (1 + values).prod() - 1)
    annual = returns.groupby(returns.index.year).apply(lambda values: (1 + values).prod() - 1)
    result_metrics = dict(portfolio_run.result.metrics)
    final_value = float(equity.iloc[-1])
    result_metrics.update(
        {
            "total_return": total_return,
            "gross_total_return": gross_growth - 1.0,
            "initial_capital": float(strategy.initial_capital),
            "ending_value": final_value,
            "total_turnover": float(portfolio_run.result.turnover.sum()),
            "observations": int(max(0, len(returns) - 1)),
        }
    )
    if cash_flow_details is not None:
        result_metrics.update(
            {
                "money_weighted_return_xirr": cash_flow_details["xirr"],
                "total_contributions": cash_flow_details["total_contributions"],
                "cash_balance": cash_flow_details["ending_cash"],
                "risk_assets_value": cash_flow_details["ending_risk_assets"],
                "transaction_costs": cash_flow_details["transaction_costs"],
                "trade_count": cash_flow_details["trade_count"],
            }
        )
    report = {
        "schema_version": 1,
        "backtest_id": run_id,
        "generated_at": generated_at,
        "code_version": code_version,
        "strategy": strategy.to_dict(),
        "period": {
            "requested_start": strategy.start_date,
            "requested_end": strategy.end_date,
            "actual_start": portfolio_run.prices.index.min().date().isoformat(),
            "actual_end": portfolio_run.prices.index.max().date().isoformat(),
        },
        "metrics": _json_safe(result_metrics),
        "benchmarks": _json_safe(benchmarks),
        "annual_returns": [
            {"year": int(year), "return": float(value)} for year, value in annual.items()
        ],
        "monthly_returns": [
            {"month": str(period), "return": float(value)} for period, value in monthly.items()
        ],
        "equity_curve_monthly": _sample_equity(equity, strategy.initial_capital),
        "drawdown": {
            "max_drawdown": _json_float(drawdown.min()),
            "basis": "unitized NAV excluding external contributions" if cash_flow_details else "account equity",
            "episode": _drawdown_episode(drawdown_basis, drawdown),
            "monthly_series": _sample_series(drawdown, "drawdown"),
        },
        "weights_month_end": _month_end_weights(portfolio_run.result.ending_weights),
        "data": {
            "price_sources": price_sources,
            "aligned_observations": len(portfolio_run.prices),
            "dropped_incomplete_rows": int(portfolio_run.dropped_incomplete_rows),
            "price_snapshot_sha256": input_sha256,
            "source_data_is_point_in_time_vintage": False,
        },
        "methodology": {
            "price_basis": "Adjusted close where supplied; no missing prices are filled.",
            "execution": "Signal at close t takes effect for the next available close-to-close return.",
            "rebalancing": strategy.rebalance_frequency,
            "transaction_cost": "Cost in basis points multiplied by gross traded notional (absolute weight change).",
            "portfolio": "Long-only; weights drift between target-weight instructions; residual weight is zero-return cash.",
            "sharpe": "Daily annualization with 252 periods/year and a 0% risk-free rate.",
            "cash_flows": (
                "No recurring deposits or withdrawals are modeled."
                if cash_flow_details is None
                else "Weekly deposits are added at the selected week-ending close and excluded from time-weighted return and drawdown."
            ),
            "taxes": "Taxes, fund premiums, and market impact beyond the configured transaction cost are not modeled.",
        },
        "diagnostics": {
            "signal_dates": list(portfolio_run.signal_dates),
            "average_weights": {
                column: float(value)
                for column, value in portfolio_run.result.effective_weights.iloc[1:].mean().items()
            },
        },
    }
    if cash_flow_details is not None:
        report["cash_flow"] = _json_safe(cash_flow_details)
    return report


def build_markdown_report(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    strategy = report["strategy"]
    lines = [
        f"# Backtest: {strategy['name']}",
        "",
        f"Run ID: `{report['backtest_id']}` · {report['period']['actual_start']} to {report['period']['actual_end']}",
        "",
        "## Results",
        "",
        "| Metric | Result |",
        "|---|---:|",
        f"| Total return | {_percent(metrics['total_return'])} |",
        f"| CAGR | {_percent(metrics['cagr'])} |",
        f"| Annualized volatility | {_percent(metrics['vol'])} |",
        f"| Sharpe (rf = 0%) | {_number(metrics['sharpe'])} |",
        f"| Maximum drawdown | {_percent(metrics['max_drawdown'])} |",
        f"| Ending value | {metrics['ending_value']:,.2f} |",
        f"| Total turnover | {_number(metrics['total_turnover'])}× NAV |",
    ]
    if "total_contributions" in metrics:
        lines.extend(
            [
                f"| Total contributions | {metrics['total_contributions']:,.2f} |",
                f"| Money-weighted return (XIRR) | {_percent(metrics['money_weighted_return_xirr'])} |",
                f"| Cash balance | {metrics['cash_balance']:,.2f} |",
                f"| Transaction costs paid | {metrics['transaction_costs']:,.2f} |",
            ]
        )
    lines.extend(
        [
        "",
        "## Assumptions",
        "",
        f"- Target weights: {', '.join(f'{k} {v:.1%}' for k, v in strategy['weights'].items())}",
        f"- Rebalance: {strategy['rebalance_frequency']}; transaction cost: {strategy['transaction_cost_bps']} bps per traded notional.",
        "- Close-derived risk signals execute at the next available close; no tax or market impact beyond the fee assumption.",
        "- Price snapshots and checksums are retained with this run; provider history is not a point-in-time vintage.",
        "",
        ]
    )
    cash_flow = report.get("cash_flow")
    if cash_flow:
        lines.extend(
            [
                "## Cash flows and risk controls",
                "",
                f"- Weekly deposit: {cash_flow['weekly_contribution_amount']:,.2f} on {cash_flow['contribution_day']} close; {cash_flow['contribution_count']} deposits.",
                f"- Total contributed: {cash_flow['total_contributions']:,.2f}; ending cash: {cash_flow['ending_cash']:,.2f}.",
                f"- Trades: {cash_flow['trade_count']}; fees paid: {cash_flow['transaction_costs']:,.2f}.",
                f"- Take-profit and drawdown event records: {len(cash_flow['events'])}; unexecuted signals at end: {len(cash_flow['pending_events'])}.",
                "- Deposits are excluded from time-weighted return and drawdown; XIRR includes each deposit and terminal account value.",
                "- Profit-taking thresholds use cumulative time-weighted return; each tier triggers once and sells the stated fraction of then-current risk holdings.",
                "- Drawdown rules cap risky exposure until unitized NAV reaches a new high. The caps reduce exposure; they do not guarantee a loss limit.",
                "",
            ]
        )
    return "\n".join(lines)


def _sample_equity(equity: pd.Series, initial_capital: float) -> list[dict[str, object]]:
    sampled = equity.groupby(equity.index.to_period("M")).tail(1)
    dates = list(sampled.index)
    if len(dates) > 360:
        stride = max(1, len(dates) // 360)
        dates = dates[::stride]
        if sampled.index[-1] not in dates:
            dates.append(sampled.index[-1])
    points = [{"date": equity.index[0].date().isoformat(), "value": float(initial_capital)}]
    points.extend({"date": index.date().isoformat(), "value": float(sampled.loc[index])} for index in dates)
    return points


def _month_end_weights(weights: pd.DataFrame) -> list[dict[str, object]]:
    month_end = weights.groupby(weights.index.to_period("M")).tail(1)
    return [
        {
            "date": index.date().isoformat(),
            "weights": {column: float(value) for column, value in row.items()},
        }
        for index, row in month_end.iterrows()
    ]


def _drawdown_episode(equity: pd.Series, drawdown: pd.Series) -> dict[str, object] | None:
    if equity.empty or float(drawdown.min()) >= 0:
        return None
    trough = drawdown.idxmin()
    prior = equity.loc[:trough]
    peak = prior.idxmax()
    peak_value = float(equity.loc[peak])
    recovery_dates = equity.index[(equity.index > trough) & (equity >= peak_value)]
    recovery = recovery_dates[0] if len(recovery_dates) else None
    return {
        "peak_date": peak.date().isoformat(),
        "trough_date": trough.date().isoformat(),
        "recovery_date": recovery.date().isoformat() if recovery is not None else None,
        "peak_value": peak_value,
        "trough_value": float(equity.loc[trough]),
        "depth": float(drawdown.loc[trough]),
        "duration_to_trough_days": int((trough - peak).days),
        "recovered": recovery is not None,
    }


def _sample_series(values: pd.Series, key: str) -> list[dict[str, object]]:
    sampled = values.groupby(values.index.to_period("M")).tail(1)
    return [{"date": index.date().isoformat(), key: float(value)} for index, value in sampled.items()]


def _percent(value: object) -> str:
    number = _json_float(value)
    return "n/a" if number is None else f"{number:.2%}"


def _number(value: object) -> str:
    number = _json_float(value)
    return "n/a" if number is None else f"{number:.3f}"


def _json_float(value: object) -> float | None:
    if value is None:
        return None
    converted = float(value)
    return converted if np.isfinite(converted) else None


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.floating, float)):
        return _json_float(value)
    if isinstance(value, np.integer):
        return int(value)
    return value
