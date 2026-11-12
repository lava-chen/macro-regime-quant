from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Literal

PriceKind = Literal["log", "level"]
BetaMethod = Literal["ols", "fixed"]
HedgeMode = Literal["beta_neutral", "dollar_neutral"]


@dataclass(frozen=True)
class SpreadSpec:
    """How a pair's spread is constructed.

    ``lookback`` is the estimation window for the hedge ratio and the
    z-score window at the same time. It is also the minimum amount of history
    the pair needs before the strategy is allowed to trade.
    """

    lookback: int = 60
    price_kind: PriceKind = "log"
    beta_method: BetaMethod = "ols"
    fixed_beta: float = 1.0
    detrend: bool = True
    min_periods: int | None = None

    def __post_init__(self) -> None:
        if self.lookback < 5:
            raise ValueError("lookback must be at least 5 periods to be estimable")
        if self.price_kind not in {"log", "level"}:
            raise ValueError("price_kind must be 'log' or 'level'")
        if self.beta_method not in {"ols", "fixed"}:
            raise ValueError("beta_method must be 'ols' or 'fixed'")
        if not isfinite(self.fixed_beta) or self.fixed_beta <= 0:
            raise ValueError("fixed_beta must be finite and positive")
        if self.min_periods is not None and self.min_periods < 2:
            raise ValueError("min_periods must be at least 2 when set")

    @property
    def effective_min_periods(self) -> int:
        return self.min_periods if self.min_periods is not None else self.lookback


@dataclass(frozen=True)
class PairSignalSpec:
    """Entry, exit and risk limits for the z-score state machine."""

    entry_z: float = 2.0
    exit_z: float = 0.5
    stop_z: float | None = None
    max_holding_days: int | None = None

    def __post_init__(self) -> None:
        if not isfinite(self.entry_z) or self.entry_z <= 0:
            raise ValueError("entry_z must be finite and positive")
        if not isfinite(self.exit_z) or self.exit_z < 0:
            raise ValueError("exit_z must be finite and non-negative")
        if self.exit_z >= self.entry_z:
            raise ValueError("exit_z must be smaller than entry_z, otherwise the position can never be left")
        if self.stop_z is not None and (not isfinite(self.stop_z) or self.stop_z <= self.entry_z):
            raise ValueError("stop_z must be finite and larger than entry_z")
        if self.max_holding_days is not None and self.max_holding_days < 1:
            raise ValueError("max_holding_days must be at least 1 when set")


@dataclass(frozen=True)
class PairSpec:
    """A tradable pair: two symbols plus the rules that define the trade."""

    asset_a: str
    asset_b: str
    spread: SpreadSpec = SpreadSpec()
    signal: PairSignalSpec = PairSignalSpec()
    hedge_mode: HedgeMode = "beta_neutral"

    def __post_init__(self) -> None:
        a = self.asset_a.strip().upper()
        b = self.asset_b.strip().upper()
        if not a or not b:
            raise ValueError("asset_a and asset_b are required")
        if a == b:
            raise ValueError("a pair needs two different assets")
        object.__setattr__(self, "asset_a", a)
        object.__setattr__(self, "asset_b", b)
        if self.hedge_mode not in {"beta_neutral", "dollar_neutral"}:
            raise ValueError("hedge_mode must be 'beta_neutral' or 'dollar_neutral'")

    @property
    def name(self) -> str:
        return f"{self.asset_a}/{self.asset_b}"
