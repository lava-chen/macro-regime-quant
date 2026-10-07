from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class SignalComponent:
    name: str
    score: float
    weight: float
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Signal name is required")
        if not isfinite(self.score):
            raise ValueError("Signal score must be finite")
        if self.weight < 0:
            raise ValueError("Signal weight cannot be negative")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")


def combine_signals(
    components: tuple[SignalComponent, ...],
    *,
    min_components: int = 1,
) -> float:
    """Confidence-adjusted weighted average of independent engine scores."""

    usable = [
        component
        for component in components
        if component.weight > 0 and component.confidence > 0
    ]
    if len(usable) < min_components:
        raise ValueError("Not enough usable signal components")

    denominator = sum(
        component.weight * component.confidence for component in usable
    )
    if denominator <= 0:
        raise ValueError("Effective signal weight must be positive")

    numerator = sum(
        component.score * component.weight * component.confidence
        for component in usable
    )
    return numerator / denominator
