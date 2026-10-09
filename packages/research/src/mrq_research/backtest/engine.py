from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .metrics import max_drawdown, summary


@dataclass(frozen=True)
class BacktestConfig:
    transaction_cost_bps: float = 5.0
    execution_lag_periods: int = 1
    periods_per_year: int = 252
    allow_cash: bool = True


@dataclass
class BacktestResult:
    returns: pd.Series
    gross_returns: pd.Series
    turnover: pd.Series
    effective_weights: pd.DataFrame
    ending_weights: pd.DataFrame
    metrics: dict[str, float]


def _validate_weights(weights: pd.DataFrame, allow_cash: bool) -> None:
    if weights.empty:
        raise ValueError("At least one target-weight instruction is required")
    if not weights.index.is_unique:
        raise ValueError("Target-weight instruction dates must be unique")
    if weights.isna().any().any():
        raise ValueError("Target weights contain NaN")
    if not np.isfinite(weights.to_numpy(dtype=float)).all():
        raise ValueError("Target weights must be finite")
    if (weights < -1e-12).any().any():
        raise ValueError("v0 engine is long-only; negative weights are not supported")
    sums = weights.sum(axis=1)
    if allow_cash:
        if (sums > 1.0 + 1e-8).any():
            raise ValueError("Long-only weights cannot sum above 1 when allow_cash=True")
    elif not ((sums - 1.0).abs() < 1e-8).all():
        raise ValueError("Weights must sum to 1 when allow_cash=False")


def run_backtest(
    prices: pd.DataFrame,
    target_weights: pd.DataFrame,
    config: BacktestConfig | None = None,
) -> BacktestResult:
    """Run a transparent close-to-next-period backtest.

    Contract:
    - prices: rows are observation dates, columns are assets;
    - target_weights: sparse or dense desired weights indexed by signal date;
    - a signal generated on t becomes effective only after `execution_lag_periods`;
    - trading cost is charged on absolute portfolio turnover.

    This simple contract is intentionally conservative and easy to audit.
    """

    config = config or BacktestConfig()
    if not np.isfinite(config.transaction_cost_bps) or config.transaction_cost_bps < 0:
        raise ValueError("transaction_cost_bps must be finite and non-negative")
    if not isinstance(config.execution_lag_periods, int) or config.execution_lag_periods < 0:
        raise ValueError("execution_lag_periods must be a non-negative integer")
    if not isinstance(config.periods_per_year, int) or config.periods_per_year <= 0:
        raise ValueError("periods_per_year must be a positive integer")
    if prices.empty or len(prices) < 2:
        raise ValueError("At least two price observations are required")
    if not prices.index.is_unique:
        raise ValueError("Price dates must be unique")
    prices = prices.sort_index().astype(float)
    if prices.isna().any().any() or not np.isfinite(prices.to_numpy()).all():
        raise ValueError("Prices must be complete and finite; align data before backtesting")
    if (prices <= 0).any().any():
        raise ValueError("Prices must be greater than zero")
    target_weights = target_weights.sort_index().reindex(columns=prices.columns, fill_value=0.0)
    _validate_weights(target_weights, config.allow_cash)
    if len(target_weights.index.difference(prices.index)):
        raise ValueError("Target-weight dates must align with the price calendar")

    asset_returns = prices.pct_change(fill_method=None).fillna(0.0)

    # Shift only actual instructions. Forward-filled targets are not daily trades;
    # between instructions, holdings drift as relative asset prices change.
    instructions = target_weights.reindex(prices.index).shift(config.execution_lag_periods)
    effective = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    ending_weights = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    gross = pd.Series(0.0, index=prices.index, dtype=float)
    turnover = pd.Series(0.0, index=prices.index, dtype=float)
    net = pd.Series(0.0, index=prices.index, dtype=float)

    holdings = pd.Series(0.0, index=prices.columns, dtype=float)
    for position, trade_date in enumerate(prices.index):
        instruction = instructions.iloc[position]
        if instruction.notna().all():
            target = instruction.astype(float)
            turnover.iloc[position] = float((target - holdings).abs().sum())
            holdings = target
        effective.loc[trade_date] = holdings
        gross.iloc[position] = float((holdings * asset_returns.loc[trade_date]).sum())
        cost = turnover.iloc[position] * (config.transaction_cost_bps / 10_000.0)
        net.iloc[position] = gross.iloc[position] - cost
        if net.iloc[position] <= -1.0:
            raise ValueError("Return and transaction costs make portfolio value non-positive")

        # Unallocated capital is zero-return cash when allow_cash=True.
        cash = max(0.0, 1.0 - float(holdings.sum()))
        portfolio_growth = float((holdings * (1.0 + asset_returns.loc[trade_date])).sum() + cash)
        if portfolio_growth <= 0:
            raise ValueError("Portfolio value became non-positive")
        holdings = holdings * (1.0 + asset_returns.loc[trade_date]) / portfolio_growth
        ending_weights.loc[trade_date] = holdings

    metrics = summary(net.iloc[1:] if len(net) > 1 else net, config.periods_per_year)
    periods = max(1, len(prices) - 1)
    growth = float((1.0 + net).prod())
    metrics["cagr"] = growth ** (config.periods_per_year / periods) - 1.0
    metrics["max_drawdown"] = max_drawdown(net)
    metrics["calmar"] = (
        float(metrics["cagr"] / abs(metrics["max_drawdown"]))
        if metrics["max_drawdown"] < 0
        else float("nan")
    )

    return BacktestResult(
        returns=net.rename("strategy_return"),
        gross_returns=gross.rename("gross_return"),
        turnover=turnover.rename("turnover"),
        effective_weights=effective,
        ending_weights=ending_weights,
        metrics=metrics,
    )
