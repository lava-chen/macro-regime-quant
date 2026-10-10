import json
from datetime import date, timedelta

import pandas as pd
import pytest
from mrq_cli.backtest_workbench.cashflow import _xirr
from mrq_cli.backtest_workbench.models import CashFlowPlan, StrategySpec
from mrq_cli.backtest_workbench.report import build_backtest_report
from mrq_cli.backtest_workbench.runner import run_portfolio_backtest


def test_cash_flow_strategy_serializes_and_validates_ordered_rules():
    payload = {
        "name": "Configured DCA",
        "weights": {"GLD": 0.5, "QQQ": 0.5},
        "cash_flow": {
            "weekly_contribution_amount": 50,
            "contribution_day": "FRI",
            "reinvest_cash": True,
            "take_profit_tiers": [
                {"return_threshold": 0.2, "sell_fraction": 0.1},
                {"return_threshold": 0.35, "sell_fraction": 0.15},
            ],
            "drawdown_rules": [
                {"trigger_drawdown": 0.15, "max_invested_weight": 0.5},
                {"trigger_drawdown": 0.25, "max_invested_weight": 0.25},
            ],
        },
    }
    strategy = StrategySpec.from_dict(payload)
    assert StrategySpec.from_dict(strategy.to_dict()) == strategy
    with pytest.raises(ValueError, match="increasing return_threshold"):
        StrategySpec.from_dict(
            {
                **payload,
                "cash_flow": {
                    **payload["cash_flow"],
                    "take_profit_tiers": list(reversed(payload["cash_flow"]["take_profit_tiers"])),
                },
            }
        )


def test_idle_cash_reinvests_on_next_weekly_deposit_date():
    dates = pd.to_datetime(
        [
            "2025-01-06",
            "2025-01-07",
            "2025-01-08",
            "2025-01-09",
            "2025-01-10",
            "2025-01-13",
            "2025-01-14",
            "2025-01-15",
            "2025-01-16",
            "2025-01-17",
        ]
    )
    prices = pd.DataFrame(
        {"GLD": [100, 100, 120, 120, 120, 132, 132, 132, 132, 132]},
        index=dates,
    )
    base = {
        "name": "Cash redeployment test",
        "weights": {"GLD": 1.0},
        "start_date": "2025-01-06",
        "end_date": "2025-01-17",
        "initial_capital": 1000,
        "transaction_cost_bps": 0,
    }
    rules = ({"return_threshold": 0.15, "sell_fraction": 0.25},)
    held = StrategySpec(
        **base,
        cash_flow=CashFlowPlan(weekly_contribution_amount=10, take_profit_tiers=rules),
    )
    reinvested = StrategySpec(
        **base,
        cash_flow=CashFlowPlan(
            weekly_contribution_amount=10,
            take_profit_tiers=rules,
            reinvest_cash=True,
        ),
    )

    held_run = run_portfolio_backtest(prices, held)
    reinvested_run = run_portfolio_backtest(prices, reinvested)

    assert held_run.cash_flow_details["ending_cash"] == pytest.approx(300)
    assert reinvested_run.cash_flow_details["ending_cash"] == pytest.approx(0)
    assert reinvested_run.account_equity.iloc[-1] > held_run.account_equity.iloc[-1]
    assert any(
        event["event"] == "cash_reinvestment_executed"
        and event["execution_date"] == "2025-01-10"
        for event in reinvested_run.cash_flow_details["events"]
    )
    assert any(
        trade["kind"] == "cash_reinvestment" and trade["date"] == "2025-01-10"
        for trade in reinvested_run.cash_flow_details["trades"]
    )


def test_sale_proceeds_wait_until_a_later_contribution_date_to_reinvest():
    dates = pd.to_datetime(
        [
            "2025-01-06",
            "2025-01-07",
            "2025-01-08",
            "2025-01-09",
            "2025-01-10",
            "2025-01-13",
            "2025-01-14",
            "2025-01-15",
            "2025-01-16",
            "2025-01-17",
        ]
    )
    prices = pd.DataFrame(
        {"GLD": [100, 100, 100, 120, 125, 125, 125, 125, 125, 125]}, index=dates
    )
    strategy = StrategySpec(
        name="No same-close sale reinvestment",
        weights={"GLD": 1.0},
        initial_capital=1000,
        transaction_cost_bps=0,
        cash_flow=CashFlowPlan(
            weekly_contribution_amount=10,
            reinvest_cash=True,
            take_profit_tiers=({"return_threshold": 0.15, "sell_fraction": 0.25},),
        ),
    )

    run = run_portfolio_backtest(prices, strategy)

    reinvestments = [
        event
        for event in run.cash_flow_details["events"]
        if event["event"] == "cash_reinvestment_executed"
    ]
    assert [event["execution_date"] for event in reinvestments] == ["2025-01-17"]


def test_weekly_cash_flows_are_excluded_from_time_weighted_return():
    dates = pd.to_datetime(
        [
            "2025-01-06",
            "2025-01-07",
            "2025-01-08",
            "2025-01-09",
            "2025-01-10",
            "2025-01-13",
            "2025-01-14",
            "2025-01-15",
            "2025-01-16",
            "2025-01-17",
        ]
    )
    prices = pd.DataFrame({"GLD": [100, 100, 100, 100, 110, 110, 110, 110, 110, 121]}, index=dates)
    strategy = StrategySpec(
        name="Weekly flow test",
        weights={"GLD": 1.0},
        start_date="2025-01-06",
        end_date="2025-01-17",
        initial_capital=100,
        transaction_cost_bps=0,
        cash_flow=CashFlowPlan(weekly_contribution_amount=10),
    )

    result = run_portfolio_backtest(prices, strategy)

    assert result.cash_flow_details is not None
    assert result.cash_flow_details["contribution_count"] == 2
    assert result.cash_flow_details["total_contributions"] == 120
    assert result.account_equity.iloc[-1] == pytest.approx(142)
    assert result.unit_nav.iloc[-1] == pytest.approx(1.21)
    assert result.result.metrics["max_drawdown"] == pytest.approx(0)
    assert result.cash_flow_details["xirr"] > 0


def test_external_flow_equity_identity_and_initial_fee_drawdown():
    dates = pd.to_datetime(["2025-01-06", "2025-01-07"])
    prices = pd.DataFrame({"GLD": [100.0, 100.0]}, index=dates)
    strategy = StrategySpec(
        name="Cash flow ledger identity",
        weights={"GLD": 1.0},
        initial_capital=100,
        transaction_cost_bps=100,
        cash_flow=CashFlowPlan(weekly_contribution_amount=10),
    )

    run = run_portfolio_backtest(prices, strategy)

    prior_equity = run.account_equity.iloc[0]
    day_one_flow = run.cash_flow_details["weekly_contribution_amount"]
    assert run.account_equity.iloc[1] == pytest.approx(
        prior_equity * (1.0 + run.result.returns.iloc[1]) + day_one_flow
    )
    assert run.result.metrics["max_drawdown"] == pytest.approx(run.unit_nav.min() - 1.0)
    assert run.result.metrics["max_drawdown"] < 0


def test_xirr_matches_a_hand_calculated_four_year_cashflow():
    start = date(2020, 1, 1)
    end = start + timedelta(days=1461)

    assert _xirr([(start, -100.0), (end, 146.41)]) == pytest.approx(0.10, abs=1e-10)


def test_take_profit_threshold_triggers_once_and_executes_next_session():
    dates = pd.to_datetime(["2025-01-06", "2025-01-07", "2025-01-08", "2025-01-09"])
    prices = pd.DataFrame({"GLD": [100, 100, 125, 130]}, index=dates)
    strategy = StrategySpec(
        name="Profit ladder",
        weights={"GLD": 1.0},
        initial_capital=1000,
        transaction_cost_bps=0,
        cash_flow=CashFlowPlan(
            weekly_contribution_amount=1,
            take_profit_tiers=({"return_threshold": 0.2, "sell_fraction": 0.1},),
        ),
    )

    result = run_portfolio_backtest(prices, strategy)

    events = result.cash_flow_details["events"]
    triggered = [event for event in events if event["event"] == "take_profit_triggered"]
    executed = [event for event in events if event["event"] == "take_profit_executed"]
    assert len(triggered) == 1
    assert len(executed) == 1
    assert executed[0]["execution_date"] == "2025-01-09"
    assert executed[0]["gross_sold"] == pytest.approx(130)


def test_drawdown_rules_reduce_exposure_and_rearm_at_a_new_high():
    dates = pd.to_datetime(
        ["2025-01-06", "2025-01-07", "2025-01-08", "2025-01-09", "2025-01-10", "2025-01-13", "2025-01-14"]
    )
    prices = pd.DataFrame({"GLD": [100, 100, 80, 70, 80, 100, 250]}, index=dates)
    strategy = StrategySpec(
        name="Drawdown guard",
        weights={"GLD": 1.0},
        initial_capital=1000,
        transaction_cost_bps=0,
        cash_flow=CashFlowPlan(
            weekly_contribution_amount=1,
            drawdown_rules=(
                {"trigger_drawdown": 0.15, "max_invested_weight": 0.5},
                {"trigger_drawdown": 0.25, "max_invested_weight": 0.25},
            ),
        ),
    )

    result = run_portfolio_backtest(prices, strategy)

    events = result.cash_flow_details["events"]
    assert any(event["event"] == "drawdown_control_triggered" for event in events)
    assert any(event["event"] == "drawdown_control_executed" for event in events)
    assert any(event["event"] == "drawdown_control_rearmed" for event in events)
    assert result.result.ending_weights.loc[dates[3], "GLD"] <= 0.51


def test_transaction_costs_and_report_capture_external_flows():
    dates = pd.to_datetime(["2025-01-06", "2025-01-07", "2025-01-08", "2025-01-09"])
    prices = pd.DataFrame({"GLD": [100, 100, 100, 100]}, index=dates)
    strategy = StrategySpec(
        name="Fee test",
        weights={"GLD": 1.0},
        initial_capital=1000,
        transaction_cost_bps=5,
        cash_flow=CashFlowPlan(weekly_contribution_amount=10),
    )
    run = run_portfolio_backtest(prices, strategy)
    report = build_backtest_report(
        "bt_cashflowtest",
        strategy,
        run,
        price_sources=[],
        benchmarks={},
        input_sha256="a" * 64,
        generated_at="2026-10-09T00:00:00+00:00",
        code_version="test",
    )

    json.dumps(report, allow_nan=False)
    assert report["metrics"]["transaction_costs"] > 0
    assert report["metrics"]["total_contributions"] == 1010
    assert report["drawdown"]["basis"] == "unitized NAV excluding external contributions"
    assert report["cash_flow"]["contribution_count"] == 1


def test_cashflow_strategy_executes_monthly_rebalance_after_month_boundary():
    dates = pd.to_datetime(["2025-01-30", "2025-01-31", "2025-02-03", "2025-02-04"])
    prices = pd.DataFrame(
        {"GLD": [100.0, 200.0, 200.0, 200.0], "QQQ": [100.0, 100.0, 100.0, 100.0]},
        index=dates,
    )
    strategy = StrategySpec(
        name="DCA with monthly rebalance",
        weights={"GLD": 0.5, "QQQ": 0.5},
        rebalance_frequency="monthly",
        initial_capital=1000,
        transaction_cost_bps=0,
        cash_flow=CashFlowPlan(weekly_contribution_amount=1),
    )

    run = run_portfolio_backtest(prices, strategy)

    executions = [
        event for event in run.cash_flow_details["events"] if event["event"] == "periodic_rebalance_executed"
    ]
    assert [event["execution_date"] for event in executions] == ["2025-02-03"]
    assert run.result.ending_weights.loc[dates[2], "GLD"] == pytest.approx(0.5)
    assert run.result.ending_weights.loc[dates[2], "QQQ"] == pytest.approx(0.5)


def test_cashflow_strategy_executes_annual_rebalance_after_year_boundary():
    dates = pd.to_datetime(
        ["2025-01-02", "2025-01-03", "2025-12-31", "2026-01-02", "2026-01-05"]
    )
    prices = pd.DataFrame(
        {"GLD": [100.0, 100.0, 200.0, 200.0, 200.0], "QQQ": [100.0] * 5},
        index=dates,
    )
    strategy = StrategySpec(
        name="DCA with annual rebalance",
        weights={"GLD": 0.5, "QQQ": 0.5},
        rebalance_frequency="annual",
        initial_capital=1000,
        transaction_cost_bps=0,
        cash_flow=CashFlowPlan(weekly_contribution_amount=1),
    )

    run = run_portfolio_backtest(prices, strategy)

    executions = [
        event for event in run.cash_flow_details["events"] if event["event"] == "periodic_rebalance_executed"
    ]
    assert [event["execution_date"] for event in executions] == ["2026-01-02"]
    assert run.result.ending_weights.loc[dates[3], "GLD"] == pytest.approx(0.5)
    assert run.result.ending_weights.loc[dates[3], "QQQ"] == pytest.approx(0.5)
