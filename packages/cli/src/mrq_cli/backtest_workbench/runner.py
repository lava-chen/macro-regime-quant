from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from mrq_research.backtest.engine import BacktestConfig, BacktestResult, run_backtest

from .models import StrategySpec


@dataclass(frozen=True)
class PortfolioRun:
    result: BacktestResult
    prices: pd.DataFrame
    dropped_incomplete_rows: int
    signal_dates: tuple[str, ...]
    cash_flow_details: dict[str, object] | None = None
    account_equity: pd.Series | None = None
    unit_nav: pd.Series | None = None


def run_portfolio_backtest(prices: pd.DataFrame, strategy: StrategySpec) -> PortfolioRun:
    if strategy.cash_flow is not None:
        from .cashflow import run_cashflow_portfolio_backtest

        return run_cashflow_portfolio_backtest(prices, strategy)

    symbols = [symbol for symbol in strategy.weights if symbol != "CASH"]
    missing = sorted(set(symbols) - set(prices.columns))
    if missing:
        raise ValueError(f"Price data is missing strategy assets: {missing}")
    selected = prices.loc[:, symbols].sort_index()
    if not selected.index.is_unique:
        raise ValueError("Price dates must be unique")
    original_rows = len(selected)
    selected = selected.dropna(how="any")
    if len(selected) < 2:
        raise ValueError("Fewer than two common complete price dates are available")
    if "CASH" in strategy.weights:
        selected["CASH"] = 1.0
    selected = selected.astype(float)
    target_weights = _target_instructions(selected.index, strategy)
    backtest = run_backtest(
        selected,
        target_weights,
        BacktestConfig(
            transaction_cost_bps=strategy.transaction_cost_bps,
            execution_lag_periods=1,
            periods_per_year=252,
            allow_cash=True,
        ),
    )
    return PortfolioRun(
        result=backtest,
        prices=selected,
        dropped_incomplete_rows=original_rows - len(selected),
        signal_dates=tuple(date.date().isoformat() for date in target_weights.index),
    )


def _target_instructions(index: pd.Index, strategy: StrategySpec) -> pd.DataFrame:
    dates = pd.DatetimeIndex(index)
    if dates.empty:
        raise ValueError("No price dates are available")
    symbols = list(strategy.weights)
    rows: dict[pd.Timestamp, dict[str, float]] = {dates[0]: dict(strategy.weights)}
    for position in range(1, len(dates)):
        if _is_rebalance_date(dates[position - 1], dates[position], strategy.rebalance_frequency):
            # A signal at the prior close is effective over the next close-to-close return.
            rows[dates[position - 1]] = dict(strategy.weights)
    return pd.DataFrame.from_dict(rows, orient="index").reindex(columns=symbols).sort_index()


def _is_rebalance_date(previous: pd.Timestamp, current: pd.Timestamp, frequency: str) -> bool:
    if frequency == "buy_and_hold":
        return False
    if frequency == "monthly":
        return previous.to_period("M") != current.to_period("M")
    if frequency == "quarterly":
        return previous.to_period("Q") != current.to_period("Q")
    if frequency == "annual":
        return previous.year != current.year
    raise ValueError(f"Unsupported rebalance frequency: {frequency}")
