from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FlowEvidence(StrEnum):
    OBSERVED = "observed"
    INFERRED = "inferred"


@dataclass(frozen=True)
class FlowObservation:
    """Directed flow between two economic or asset nodes."""

    as_of_date: str
    source_node: str
    destination_node: str
    amount: float
    currency: str
    evidence: FlowEvidence
    confidence: float
    source: str

    def __post_init__(self) -> None:
        if not self.source_node or not self.destination_node:
            raise ValueError("source_node and destination_node are required")
        if self.source_node == self.destination_node:
            raise ValueError("source and destination nodes must differ")
        if not self.currency or not self.source:
            raise ValueError("currency and source are required")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
