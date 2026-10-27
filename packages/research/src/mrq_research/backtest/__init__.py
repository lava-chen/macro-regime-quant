"""Backtest engine and performance metrics."""

from .engine import BacktestConfig, BacktestResult, run_backtest
from .long_short import LongShortConfig, LongShortResult, run_long_short_backtest
from .metrics import annualized_vol, cagr, equity_curve, max_drawdown, sharpe, summary

__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "LongShortConfig",
    "LongShortResult",
    "annualized_vol",
    "cagr",
    "equity_curve",
    "max_drawdown",
    "run_backtest",
    "run_long_short_backtest",
    "sharpe",
    "summary",
]
