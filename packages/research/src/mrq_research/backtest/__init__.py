"""Backtest engine and performance metrics."""

from .engine import BacktestConfig, BacktestResult, run_backtest
from .metrics import annualized_vol, cagr, equity_curve, max_drawdown, sharpe, summary

__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "annualized_vol",
    "cagr",
    "equity_curve",
    "max_drawdown",
    "run_backtest",
    "sharpe",
    "summary",
]
