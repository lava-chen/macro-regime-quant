from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Frequency = Literal["daily", "weekly", "monthly", "quarterly"]
SeriesKind = Literal["macro", "market", "rate", "fx", "commodity"]


@dataclass(frozen=True)
class SeriesSpec:
    """Stable description of one research series.

    observation_date is when the economic quantity belongs to.
    available_date is when the strategy is allowed to know it.
    """

    key: str
    provider: str
    symbol: str
    kind: SeriesKind
    frequency: Frequency
    release_lag_days: int = 0
    notes: str = ""
