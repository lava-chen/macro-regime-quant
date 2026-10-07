from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

Frequency = Literal["daily", "weekly", "monthly", "quarterly"]
SeriesKind = Literal["macro", "market", "rate", "fx", "commodity"]

#: Allowed values per spec field, used for config validation and error messages.
FREQUENCIES_AND_KINDS: dict[str, tuple[str, ...]] = {
    "frequency": get_args(Frequency),
    "kind": get_args(SeriesKind),
}

#: Every field a catalog entry may declare. Anything else is a typo, not a feature.
CATALOG_FIELDS: frozenset[str] = frozenset(
    {
        "provider",
        "symbol",
        "kind",
        "frequency",
        "release_lag_days",
        "notes",
    }
)


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

    def __post_init__(self) -> None:
        if not self.key or not self.provider or not self.symbol:
            raise ValueError("key, provider and symbol are required")
        if self.kind not in FREQUENCIES_AND_KINDS["kind"]:
            raise ValueError(
                f"Unknown kind {self.kind!r}; choose one of {sorted(FREQUENCIES_AND_KINDS['kind'])}"
            )
        if self.frequency not in FREQUENCIES_AND_KINDS["frequency"]:
            raise ValueError(
                f"Unknown frequency {self.frequency!r}; "
                f"choose one of {sorted(FREQUENCIES_AND_KINDS['frequency'])}"
            )
        if self.release_lag_days < 0:
            raise ValueError(
                f"{self.key}: release_lag_days must be >= 0, got {self.release_lag_days}. "
                "A negative lag would make data available before it was observed, "
                "which is look-ahead bias."
            )
