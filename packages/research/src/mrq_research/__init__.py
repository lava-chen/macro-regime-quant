"""Research layer: signal testing, backtesting and forward-return analysis."""

from .factor_test import (
    DEFAULT_HORIZONS,
    FactorDiagnostics,
    analyse_factor,
    quantile_returns,
)
from .forward_returns import forward_returns, regime_return_summary
from .idea import IdeaError, IdeaResult, evaluate_idea, load_signal_function

__all__ = [
    "DEFAULT_HORIZONS",
    "FactorDiagnostics",
    "IdeaError",
    "IdeaResult",
    "analyse_factor",
    "evaluate_idea",
    "forward_returns",
    "load_signal_function",
    "quantile_returns",
    "regime_return_summary",
]
