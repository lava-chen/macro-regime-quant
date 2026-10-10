from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from mrq_research.backtest.engine import BacktestResult
from mrq_research.backtest.metrics import summary

from .models import StrategySpec
from .runner import PortfolioRun


def run_cashflow_portfolio_backtest(
    prices: pd.DataFrame,
    strategy: StrategySpec,
) -> PortfolioRun:
    """Run weekly deposits, one-time profit tiers, and drawdown exposure caps.

    Deposits are added at the close of the last available trading session for the
    selected weekday. Close-derived risk signals trade on the next available close.
    External deposits are excluded from unitized (time-weighted) returns and drawdown.
    """

    plan = strategy.cash_flow
    if plan is None:
        raise ValueError("A cash_flow plan is required")
    symbol_weights = {symbol: weight for symbol, weight in strategy.weights.items() if symbol != "CASH"}
    if not symbol_weights or sum(symbol_weights.values()) <= 0:
        raise ValueError("Cash-flow strategies need at least one positive-risk asset weight")
    risk_weight_sum = sum(symbol_weights.values())
    base_exposure = min(1.0, risk_weight_sum)
    asset_weights = {symbol: weight / risk_weight_sum for symbol, weight in symbol_weights.items()}
    missing = sorted(set(asset_weights) - set(prices.columns))
    if missing:
        raise ValueError(f"Price data is missing strategy assets: {missing}")
    selected = prices.loc[:, list(asset_weights)].sort_index().copy()
    if not selected.index.is_unique:
        raise ValueError("Price dates must be unique")
    original_rows = len(selected)
    selected = selected.dropna(how="any")
    if len(selected) < 2:
        raise ValueError("Fewer than two common complete price dates are available")
    selected = selected.astype(float)
    values = selected.to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("Prices must be complete, finite, and positive")

    index = pd.DatetimeIndex(selected.index)
    weekdays = {"MON": "MON", "TUE": "TUE", "WED": "WED", "THU": "THU", "FRI": "FRI"}
    contribution_periods = index.to_period(f"W-{weekdays[plan.contribution_day]}")
    contribution_dates = set(pd.Series(index, index=index).groupby(contribution_periods).last().tolist())

    rate = strategy.transaction_cost_bps / 10_000.0
    quantities = pd.Series(0.0, index=selected.columns, dtype=float)
    cash = 0.0
    active_exposure = base_exposure
    pending_guard: dict[str, object] | None = None
    pending_take_profits: list[dict[str, object]] = []
    triggered_tiers: set[int] = set()
    events: list[dict[str, object]] = []

    returns = pd.Series(0.0, index=index, name="strategy_return")
    gross_returns = pd.Series(0.0, index=index, name="gross_return")
    turnover = pd.Series(0.0, index=index, name="turnover")
    transaction_costs = pd.Series(0.0, index=index, name="transaction_cost")
    contributions = pd.Series(0.0, index=index, name="contribution")
    cash_series = pd.Series(0.0, index=index, name="cash")
    equity_series = pd.Series(0.0, index=index, name="equity")
    unit_nav = pd.Series(1.0, index=index, name="unit_nav")
    weights = pd.DataFrame(0.0, index=index, columns=[*selected.columns, "CASH"])
    previous_equity = 0.0
    high_water = 1.0
    total_contributions = 0.0
    total_trade_count = 0
    total_transaction_costs = 0.0
    trade_log: list[dict[str, object]] = []

    for position, trade_date in enumerate(index):
        prices_at_close = selected.loc[trade_date]
        weekly_flow = plan.weekly_contribution_amount if trade_date in contribution_dates else 0.0
        flow = weekly_flow
        if position == 0:
            flow += strategy.initial_capital
            total_contributions += strategy.initial_capital + weekly_flow
            cash += strategy.initial_capital
            # The initial deposit is invested at the first available close.
            gross, fees, legs = _buy_budget(
                quantities,
                cash,
                prices_at_close,
                strategy.initial_capital * base_exposure,
                asset_weights,
                rate,
            )
            cash -= gross + fees
            daily_gross_return = 0.0
            traded_notional = gross
            total_trade_count += legs
            trade_log.extend(_trade_rows(trade_date, "initial_investment", gross, fees, legs))
            if weekly_flow:
                cash += weekly_flow
                risk_value = float((quantities * prices_at_close).sum())
                equity_before_buy = risk_value + cash
                budget = min(weekly_flow, max(0.0, equity_before_buy * active_exposure - risk_value))
                gross, cost, legs = _buy_budget(
                    quantities, cash, prices_at_close, budget, asset_weights, rate
                )
                cash -= gross + cost
                traded_notional += gross
                fees += cost
                total_trade_count += legs
                trade_log.extend(_trade_rows(trade_date, "weekly_contribution", gross, cost, legs))
        else:
            marked_risk_value = float((quantities * prices_at_close).sum())
            daily_gross_return = (marked_risk_value + cash - previous_equity) / previous_equity
            traded_notional = 0.0
            fees = 0.0

            # Add deposits at the close. Their principal is removed from the
            # daily return below, so it cannot create artificial gains or drawdown.
            if flow:
                cash += flow
                total_contributions += flow

            if pending_take_profits:
                for order in pending_take_profits:
                    fraction = float(order["sell_fraction"])
                    gross, cost, legs = _sell_fraction(quantities, cash, prices_at_close, fraction, rate)
                    cash += gross - cost
                    traded_notional += gross
                    fees += cost
                    total_trade_count += legs
                    trade_log.extend(_trade_rows(trade_date, "take_profit", gross, cost, legs))
                    events.append(
                        {
                            "event": "take_profit_executed",
                            "signal_date": order["signal_date"],
                            "execution_date": trade_date.date().isoformat(),
                            "return_threshold": order["return_threshold"],
                            "sell_fraction": fraction,
                            "gross_sold": gross,
                            "transaction_cost": cost,
                        }
                    )
                pending_take_profits = []

            if pending_guard is not None:
                exposure = float(pending_guard["max_invested_weight"])
                cash, gross, cost, legs = _rebalance_exposure(
                    quantities, cash, prices_at_close, asset_weights, exposure, rate
                )
                traded_notional += gross
                fees += cost
                total_trade_count += legs
                trade_log.extend(_trade_rows(trade_date, "drawdown_control", gross, cost, legs))
                events.append(
                    {
                        "event": "drawdown_control_executed",
                        "signal_date": pending_guard["signal_date"],
                        "execution_date": trade_date.date().isoformat(),
                        "max_invested_weight": exposure,
                        "gross_traded": gross,
                        "transaction_cost": cost,
                    }
                )
                pending_guard = None

            if plan.reinvest_cash and flow:
                # Cash from earlier sales is redeployed on the contribution cadence,
                # subject to the active drawdown exposure cap. New contributions are
                # kept separate so the reinvestment effect can be measured directly.
                prior_cash = max(0.0, cash - flow)
                equity_before_buy = float((quantities * prices_at_close).sum() + cash)
                current_risk = float((quantities * prices_at_close).sum())
                capacity = max(0.0, equity_before_buy * active_exposure - current_risk)
                budget = min(prior_cash, capacity)
                gross, cost, legs = _buy_budget(
                    quantities, cash, prices_at_close, budget, asset_weights, rate
                )
                cash -= gross + cost
                traded_notional += gross
                fees += cost
                total_trade_count += legs
                trade_log.extend(_trade_rows(trade_date, "cash_reinvestment", gross, cost, legs))
                if gross > 0:
                    events.append(
                        {
                            "event": "cash_reinvestment_executed",
                            "execution_date": trade_date.date().isoformat(),
                            "gross_invested": gross,
                            "transaction_cost": cost,
                            "max_invested_weight": active_exposure,
                        }
                    )

            if flow:
                equity_before_buy = float((quantities * prices_at_close).sum() + cash)
                current_risk = float((quantities * prices_at_close).sum())
                capacity = max(0.0, equity_before_buy * active_exposure - current_risk)
                budget = min(flow, capacity)
                gross, cost, legs = _buy_budget(
                    quantities, cash, prices_at_close, budget, asset_weights, rate
                )
                cash -= gross + cost
                traded_notional += gross
                fees += cost
                total_trade_count += legs
                trade_log.extend(_trade_rows(trade_date, "weekly_contribution", gross, cost, legs))

        current_risk_value = float((quantities * prices_at_close).sum())
        if cash < -1e-6:
            raise ValueError("Cash-flow accounting produced negative cash")
        # Fees and proportional rebalances may leave a tiny negative IEEE-754
        # residue. Clamp only that sub-cent numerical noise before marking equity.
        cash = max(0.0, cash)
        equity = current_risk_value + cash
        if equity <= 0:
            raise ValueError("Cash-flow accounting produced non-positive equity")
        if position == 0:
            daily_return = (equity - flow) / flow
            unit_nav.iloc[position] = 1.0 + daily_return
        else:
            daily_return = (equity - flow) / previous_equity - 1.0
            unit_nav.iloc[position] = unit_nav.iloc[position - 1] * (1.0 + daily_return)
        if daily_return <= -1.0 or not np.isfinite(daily_return):
            raise ValueError("Cash-flow return made portfolio value non-positive or non-finite")

        returns.iloc[position] = daily_return
        gross_returns.iloc[position] = daily_gross_return
        turnover.iloc[position] = traded_notional / max(equity, 1e-12)
        transaction_costs.iloc[position] = fees
        contributions.iloc[position] = flow
        cash_series.iloc[position] = cash
        equity_series.iloc[position] = equity
        weights.loc[trade_date, selected.columns] = quantities * prices_at_close / equity
        weights.loc[trade_date, "CASH"] = cash / equity
        previous_equity = equity
        total_transaction_costs += fees

        if unit_nav.iloc[position] > high_water:
            high_water = float(unit_nav.iloc[position])
            if active_exposure < base_exposure:
                active_exposure = base_exposure
                pending_guard = {
                    "signal_date": trade_date.date().isoformat(),
                    "max_invested_weight": base_exposure,
                }
                events.append(
                    {
                        "event": "drawdown_control_rearmed",
                        "signal_date": trade_date.date().isoformat(),
                        "execution_date": None,
                        "max_invested_weight": base_exposure,
                    }
                )
        drawdown = float(unit_nav.iloc[position] / high_water - 1.0)

        cumulative_return = float(unit_nav.iloc[position] - 1.0)
        for tier_index, tier in enumerate(plan.take_profit_tiers):
            if tier_index not in triggered_tiers and cumulative_return >= tier.return_threshold:
                triggered_tiers.add(tier_index)
                pending_take_profits.append(
                    {
                        "signal_date": trade_date.date().isoformat(),
                        "return_threshold": tier.return_threshold,
                        "sell_fraction": tier.sell_fraction,
                    }
                )
                events.append(
                    {
                        "event": "take_profit_triggered",
                        "signal_date": trade_date.date().isoformat(),
                        "execution_date": None,
                        "return_threshold": tier.return_threshold,
                        "sell_fraction": tier.sell_fraction,
                    }
                )

        matched_rules = [rule for rule in plan.drawdown_rules if drawdown <= -rule.trigger_drawdown]
        if matched_rules:
            rule = matched_rules[-1]
            capped_exposure = min(rule.max_invested_weight, base_exposure)
            if capped_exposure < active_exposure:
                active_exposure = capped_exposure
                pending_guard = {
                    "signal_date": trade_date.date().isoformat(),
                    "max_invested_weight": capped_exposure,
                }
                events.append(
                    {
                        "event": "drawdown_control_triggered",
                        "signal_date": trade_date.date().isoformat(),
                        "execution_date": None,
                        "drawdown": drawdown,
                        "trigger_drawdown": rule.trigger_drawdown,
                        "max_invested_weight": capped_exposure,
                    }
                )

    returns_for_metrics = returns.iloc[1:] if len(returns) > 1 else returns
    metrics = summary(returns_for_metrics, 252)
    interval_count = max(1, len(selected) - 1)
    time_weighted_growth = float(unit_nav.iloc[-1])
    metrics["cagr"] = time_weighted_growth ** (252.0 / interval_count) - 1.0
    drawdowns = unit_nav / unit_nav.cummax() - 1.0
    metrics["max_drawdown"] = float(drawdowns.min())
    metrics["calmar"] = (
        float(metrics["cagr"] / abs(metrics["max_drawdown"]))
        if metrics["max_drawdown"] < 0
        else float("nan")
    )
    cash_flows = [(index[0].date(), -strategy.initial_capital)]
    cash_flows.extend(
        (day.date(), -plan.weekly_contribution_amount)
        for day in index
        if day in contribution_dates
    )
    cash_flows.append((index[-1].date(), equity_series.iloc[-1]))
    xirr = _xirr(cash_flows)
    details: dict[str, object] = {
        "weekly_contribution_amount": plan.weekly_contribution_amount,
        "contribution_day": plan.contribution_day,
        "reinvest_cash": plan.reinvest_cash,
        "cash_reinvestment_rule": (
            "Redeploy previously idle cash on the next selected weekly contribution date, "
            "subject to the active max invested weight."
            if plan.reinvest_cash
            else "Hold sale proceeds and other idle cash without reinvestment."
        ),
        "contribution_count": len(contribution_dates),
        "total_contributions": total_contributions,
        "ending_cash": float(cash_series.iloc[-1]),
        "ending_risk_assets": float(equity_series.iloc[-1] - cash_series.iloc[-1]),
        "transaction_costs": total_transaction_costs,
        "trade_count": total_trade_count,
        "xirr": xirr,
        "events": events,
        "trades": trade_log,
        "pending_events": [
            *[
                {
                    "event": "take_profit_pending_at_end",
                    **order,
                }
                for order in pending_take_profits
            ],
            *([{"event": "drawdown_control_pending_at_end", **pending_guard}] if pending_guard else []),
        ],
    }
    effective_weights = weights.copy()
    ending_weights = weights.copy()
    result = BacktestResult(
        returns=returns,
        gross_returns=gross_returns,
        turnover=turnover,
        effective_weights=effective_weights,
        ending_weights=ending_weights,
        metrics=metrics,
    )
    return PortfolioRun(
        result=result,
        prices=selected,
        dropped_incomplete_rows=original_rows - len(selected),
        signal_dates=tuple(date_value.date().isoformat() for date_value in index),
        cash_flow_details=details,
        account_equity=equity_series,
        unit_nav=unit_nav,
    )


def _buy_budget(
    quantities: pd.Series,
    cash: float,
    prices: pd.Series,
    budget: float,
    weights: dict[str, float],
    rate: float,
) -> tuple[float, float, int]:
    if budget <= 0 or cash <= 0:
        return 0.0, 0.0, 0
    gross_budget = min(budget / (1.0 + rate), cash / (1.0 + rate))
    gross_total = 0.0
    fees = 0.0
    legs = 0
    for symbol, weight in weights.items():
        notional = gross_budget * weight
        if notional <= 1e-10:
            continue
        quantities.loc[symbol] += notional / float(prices.loc[symbol])
        gross_total += notional
        fees += notional * rate
        legs += 1
    return gross_total, fees, legs


def _sell_fraction(
    quantities: pd.Series,
    cash: float,
    prices: pd.Series,
    fraction: float,
    rate: float,
) -> tuple[float, float, int]:
    gross_total = 0.0
    fees = 0.0
    legs = 0
    for symbol in quantities.index:
        quantity = float(quantities.loc[symbol]) * fraction
        notional = quantity * float(prices.loc[symbol])
        if notional <= 1e-10:
            continue
        quantities.loc[symbol] -= quantity
        gross_total += notional
        fees += notional * rate
        legs += 1
    cash += gross_total - fees
    return gross_total, fees, legs


def _rebalance_exposure(
    quantities: pd.Series,
    cash: float,
    prices: pd.Series,
    weights: dict[str, float],
    exposure: float,
    rate: float,
) -> tuple[float, float, float, int]:
    current_values = quantities * prices.reindex(quantities.index)
    equity = float(current_values.sum() + cash)
    targets = pd.Series(
        {symbol: equity * exposure * weight for symbol, weight in weights.items()},
        index=quantities.index,
        dtype=float,
    )
    deltas = targets - current_values
    sell_total = float((-deltas.clip(upper=0)).sum())
    buy_total = float(deltas.clip(lower=0).sum())
    available = max(0.0, cash + sell_total * (1.0 - rate))
    buy_scale = min(1.0, available / (buy_total * (1.0 + rate))) if buy_total > 0 else 0.0
    traded = 0.0
    fees = 0.0
    legs = 0
    for symbol, delta in deltas.items():
        price = float(prices.loc[symbol])
        if delta < -1e-10:
            notional = -float(delta)
            quantities.loc[symbol] -= notional / price
            cash += notional * (1.0 - rate)
            traded += notional
            fees += notional * rate
            legs += 1
        elif delta > 1e-10:
            notional = float(delta) * buy_scale
            if notional <= 1e-10:
                continue
            quantities.loc[symbol] += notional / price
            cash -= notional * (1.0 + rate)
            traded += notional
            fees += notional * rate
            legs += 1
    return cash, traded, fees, legs


def _trade_rows(
    trade_date: pd.Timestamp,
    kind: str,
    notional: float,
    fees: float,
    legs: int,
) -> list[dict[str, object]]:
    if legs == 0:
        return []
    return [
        {
            "date": trade_date.date().isoformat(),
            "kind": kind,
            "gross_notional": notional,
            "transaction_cost": fees,
            "asset_legs": legs,
        }
    ]


def _xirr(cash_flows: list[tuple[date, float]]) -> float | None:
    if len(cash_flows) < 2:
        return None
    origin = cash_flows[0][0]
    dated = [(int((day - origin).days), float(amount)) for day, amount in cash_flows]
    if not any(amount < 0 for _, amount in dated) or not any(amount > 0 for _, amount in dated):
        return None

    def npv(rate: float) -> float:
        return sum(amount / ((1.0 + rate) ** (days / 365.25)) for days, amount in dated)

    low = -0.9999
    high = 1.0
    low_value = npv(low)
    high_value = npv(high)
    while low_value * high_value > 0 and high < 1_000_000:
        high = high * 2.0 + 1.0
        high_value = npv(high)
    if low_value * high_value > 0:
        return None
    for _ in range(160):
        middle = (low + high) / 2.0
        middle_value = npv(middle)
        if abs(middle_value) < 1e-8:
            return middle
        if low_value * middle_value <= 0:
            high = middle
            high_value = middle_value
        else:
            low = middle
            low_value = middle_value
    return (low + high) / 2.0
