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
    - Each return dated t measures the close(t-1)-to-close(t) interval.
    - A signal dated t executes at close(t + execution_lag_periods).
    - Market returns dated t are earned by holdings from the prior close.
    - Same-close trades pay costs after the market return and affect later returns.
    - effective_weights are the weights that earn the dated interval;
      ending_weights are the weights after that close's trades.

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
    unknown_assets = sorted(set(target_weights.columns) - set(prices.columns))
    if unknown_assets:
        raise ValueError(f"Target weights reference assets with no price series: {unknown_assets}")
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
    cost_rate = config.transaction_cost_bps / 10_000.0
    for position, trade_date in enumerate(prices.index):
        # Returns stamped with `trade_date` accrue to positions held at the
        # preceding close. On the first observation the prehistory is flat.
        period_returns = asset_returns.loc[trade_date]
        instruction = instructions.iloc[position]
        effective.loc[trade_date] = holdings
        gross.iloc[position] = float((holdings * period_returns).sum())

        # Mark the account to the close before filling any orders scheduled for
        # this close. Unallocated capital is zero-return cash.
        cash = max(0.0, 1.0 - float(holdings.sum()))
        market_growth = float((holdings * (1.0 + period_returns)).sum() + cash)
        if market_growth <= 0:
            raise ValueError("Portfolio value became non-positive")

        drifted_holdings = holdings * (1.0 + period_returns) / market_growth
        cost_fraction = 0.0
        if instruction.notna().all():
            target = instruction.astype(float)
            turnover.iloc[position] = float((target - drifted_holdings).abs().sum())
            cost_fraction = turnover.iloc[position] * cost_rate
            holdings = target
        else:
            holdings = drifted_holdings

        # A proportional fee is paid at the closing fill from post-market
        # equity. This makes the compounded account value equal the dollar
        # ledger: market P&L first, then fee, then target weights.
        net_growth = market_growth * (1.0 - cost_fraction)
        net.iloc[position] = net_growth - 1.0
        if net_growth <= 0 or net.iloc[position] <= -1.0:
            raise ValueError("Return and transaction costs make portfolio value non-positive")
        ending_weights.loc[trade_date] = holdings

    metrics = summary(net, config.periods_per_year)
    periods = max(1, len(prices) - 1)
    growth = float((1.0 + net).prod())
    metrics["cagr"] = growth ** (config.periods_per_year / periods) - 1.0
    # Include the pre-trade unit value of 1.0 so a lag=0 entry fee cannot
    # disappear merely because the first recorded post-trade value is the peak.
    metrics["max_drawdown"] = max_drawdown(pd.concat([pd.Series([0.0]), net], ignore_index=True))
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
