"""Pair-trading statistics: hedge ratios, cointegration evidence, mean reversion.

Everything here is a pure function over an already-aligned two-price panel.
Nothing fetches data, sizes positions or charges costs — those belong to the
layers above. The one rule this module enforces everywhere is point-in-time
discipline: a hedge ratio observed on date ``t`` may only use price history
strictly before ``t``, because the ratio is an input to the trade that fills
after ``t``.
"""

from .models import PairSpec, SpreadSpec
from .signals import PairSignalSpec, zscore_state_machine
from .stats import (
    AdfResult,
    CointegrationResult,
    align_pair_prices,
    build_spread,
    cointegration_test,
    half_life,
    rolling_hedge_ratio,
    rolling_zscore,
)

__all__ = [
    "AdfResult",
    "CointegrationResult",
    "PairSignalSpec",
    "PairSpec",
    "SpreadSpec",
    "align_pair_prices",
    "build_spread",
    "cointegration_test",
    "half_life",
    "rolling_hedge_ratio",
    "rolling_zscore",
    "zscore_state_machine",
]
