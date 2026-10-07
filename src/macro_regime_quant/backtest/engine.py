from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .metrics import summary


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
    metrics: dict[str, float]


def _validate_weights(weights: pd.DataFrame, allow_cash: bool) -> None:
    if weights.isna().any().any():
        raise ValueError("Target weights contain NaN")
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
    prices = prices.sort_index().astype(float)
    target_weights = target_weights.sort_index().reindex(columns=prices.columns, fill_value=0.0)
    _validate_weights(target_weights, config.allow_cash)

    asset_returns = prices.pct_change(fill_method=None).fillna(0.0)

    # Map sparse rebalance instructions onto the market calendar, hold until changed,
    # and delay execution to prevent same-close look-ahead.
    effective = target_weights.reindex(prices.index).ffill().fillna(0.0)
    effective = effective.shift(config.execution_lag_periods).fillna(0.0)

    gross = (effective * asset_returns).sum(axis=1)
    turnover = effective.diff().abs().sum(axis=1)
    if len(turnover):
        turnover.iloc[0] = effective.iloc[0].abs().sum()
    costs = turnover * (config.transaction_cost_bps / 10_000.0)
    net = gross - costs

    return BacktestResult(
        returns=net.rename("strategy_return"),
        gross_returns=gross.rename("gross_return"),
        turnover=turnover.rename("turnover"),
        effective_weights=effective,
        metrics=summary(net, config.periods_per_year),
    )
