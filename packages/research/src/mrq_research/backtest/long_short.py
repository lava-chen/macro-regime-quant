"""Long/short backtest engine.

The long-only engine in :mod:`mrq_research.backtest.engine` refuses negative
weights, and its cash accounting assumes an unallocated remainder rather than
short proceeds. A pair trade is neither: one leg is short, the short proceeds
collateralise the long leg, and the borrow fee is a real cost of carrying the
position.

The accounting contract is otherwise identical to the long-only engine, so a
result from either engine is read the same way:

- return row ``t`` measures the close(t-1)-to-close(t) interval;
- a signal dated ``t`` executes at close(t + execution_lag_periods);
- the return ending on the fill date is earned by the old holdings;
- holdings drift with relative realised prices between fills;
- trading costs are charged on traded notional at the fill, borrow fees accrue
  on the short leg over the interval it was held.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .metrics import sharpe, summary


@dataclass(frozen=True)
class LongShortConfig:
    """Costs and conventions for a long/short backtest."""

    transaction_cost_bps: float = 5.0
    slippage_bps: float = 2.0
    borrow_annual_bps: float = 50.0
    execution_lag_periods: int = 1
    periods_per_year: int = 252
    max_gross_exposure: float = 1.0
    risk_free_rate: float = 0.0

    def __post_init__(self) -> None:
        if not np.isfinite(self.transaction_cost_bps) or self.transaction_cost_bps < 0:
            raise ValueError("transaction_cost_bps must be finite and non-negative")
        if not np.isfinite(self.slippage_bps) or self.slippage_bps < 0:
            raise ValueError("slippage_bps must be finite and non-negative")
        if not np.isfinite(self.borrow_annual_bps) or self.borrow_annual_bps < 0:
            raise ValueError("borrow_annual_bps must be finite and non-negative")
        if not isinstance(self.execution_lag_periods, int) or self.execution_lag_periods < 0:
            raise ValueError("execution_lag_periods must be a non-negative integer")
        if not isinstance(self.periods_per_year, int) or self.periods_per_year <= 0:
            raise ValueError("periods_per_year must be a positive integer")
        if not np.isfinite(self.max_gross_exposure) or self.max_gross_exposure <= 0:
            raise ValueError("max_gross_exposure must be finite and positive")
        if not np.isfinite(self.risk_free_rate):
            raise ValueError("risk_free_rate must be finite")


@dataclass
class LongShortResult:
    returns: pd.Series
    gross_returns: pd.Series
    turnover: pd.Series
    trading_costs: pd.Series
    borrow_costs: pd.Series
    effective_weights: pd.DataFrame
    ending_weights: pd.DataFrame
    metrics: dict[str, float]


def _validate_targets(
    target_weights: pd.DataFrame,
    columns: pd.Index,
    max_gross_exposure: float,
) -> pd.DataFrame:
    if target_weights.empty:
        raise ValueError("At least one target-weight instruction is required")
    if not target_weights.index.is_unique:
        raise ValueError("Target-weight instruction dates must be unique")
    if target_weights.isna().any().any():
        raise ValueError("Target weights contain NaN")
    values = target_weights.to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("Target weights must be finite")
    gross = np.abs(values).sum(axis=1)
    if (gross > max_gross_exposure + 1e-8).any():
        raise ValueError(
            "Target weights exceed the configured gross exposure limit; "
            "a short leg must be collateralised, not invented"
        )
    if (values.sum(axis=1) > 1.0 + 1e-8).any():
        raise ValueError("Target weights cannot sum above 1; the remainder is cash")
    return target_weights.sort_index().reindex(columns=columns, fill_value=0.0)


def run_long_short_backtest(
    prices: pd.DataFrame,
    target_weights: pd.DataFrame,
    config: LongShortConfig | None = None,
) -> LongShortResult:
    """Run a long/short backtest over an aligned price panel.

    Short proceeds are held as zero-yield cash, so the account's gross
    exposure is ``sum(abs(weights))`` and its net exposure is ``sum(weights)``.
    Borrow fees accrue daily on the short leg's market value and are charged
    before trading costs, matching the order in which they are actually paid.
    """

    config = config or LongShortConfig()
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
    target_weights = _validate_targets(
        target_weights, prices.columns, config.max_gross_exposure
    )
    if len(target_weights.index.difference(prices.index)):
        raise ValueError("Target-weight dates must align with the price calendar")

    asset_returns = prices.pct_change(fill_method=None).fillna(0.0)
    instructions = target_weights.reindex(prices.index).shift(config.execution_lag_periods)

    effective = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    ending = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    gross = pd.Series(0.0, index=prices.index, dtype=float)
    turnover = pd.Series(0.0, index=prices.index, dtype=float)
    trading_costs = pd.Series(0.0, index=prices.index, dtype=float)
    borrow_costs = pd.Series(0.0, index=prices.index, dtype=float)
    net = pd.Series(0.0, index=prices.index, dtype=float)

    holdings = pd.Series(0.0, index=prices.columns, dtype=float)
    cost_rate = (config.transaction_cost_bps + config.slippage_bps) / 10_000.0
    borrow_rate = config.borrow_annual_bps / 10_000.0 / config.periods_per_year

    for position, trade_date in enumerate(prices.index):
        period_returns = asset_returns.loc[trade_date]
        instruction = instructions.iloc[position]

        effective.loc[trade_date] = holdings
        gross.iloc[position] = float((holdings * period_returns).sum())
        # Cash is the unallocated remainder plus the proceeds of any short leg.
        # It earns nothing, which is the conservative assumption for a
        # collateral account.
        gross_growth = 1.0 + gross.iloc[position]

        short_fraction = float(np.clip(-holdings.to_numpy(dtype=float), 0.0, None).sum())
        borrow_fraction = short_fraction * borrow_rate
        borrow_costs.iloc[position] = borrow_fraction

        drifted = holdings * (1.0 + period_returns) / gross_growth
        cost_fraction = 0.0
        if instruction.notna().all():
            target = instruction.astype(float)
            turnover.iloc[position] = float((target - drifted).abs().sum())
            cost_fraction = turnover.iloc[position] * cost_rate
            holdings = target
        else:
            holdings = drifted
        trading_costs.iloc[position] = cost_fraction

        net_growth = gross_growth * (1.0 - borrow_fraction) * (1.0 - cost_fraction)
        net.iloc[position] = net_growth - 1.0
        if net_growth <= 0:
            raise ValueError("Borrow and transaction costs made the account non-positive")
        ending.loc[trade_date] = holdings

    metrics = summary(net, config.periods_per_year)
    metrics["sharpe"] = sharpe(net, config.periods_per_year, rf=config.risk_free_rate)
    periods = max(1, len(prices) - 1)
    metrics["cagr"] = float((1.0 + net).prod()) ** (config.periods_per_year / periods) - 1.0
    metrics["total_turnover"] = float(turnover.sum())
    metrics["total_trading_cost"] = float(trading_costs.sum())
    metrics["total_borrow_cost"] = float(borrow_costs.sum())
    metrics["average_gross_exposure"] = float(
        effective.abs().sum(axis=1).mean()
    )
    metrics["average_net_exposure"] = float(effective.sum(axis=1).mean())

    return LongShortResult(
        returns=net.rename("strategy_return"),
        gross_returns=gross.rename("gross_return"),
        turnover=turnover.rename("turnover"),
        trading_costs=trading_costs.rename("trading_cost"),
        borrow_costs=borrow_costs.rename("borrow_cost"),
        effective_weights=effective,
        ending_weights=ending,
        metrics=metrics,
    )
