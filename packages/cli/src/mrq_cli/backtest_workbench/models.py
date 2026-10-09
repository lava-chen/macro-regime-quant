from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from datetime import date
from typing import Literal

RebalanceFrequency = Literal["buy_and_hold", "monthly", "quarterly", "annual"]
_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9.^=_-]{1,24}$")


@dataclass(frozen=True)
class StrategySpec:
    """A transparent long-only target-weight strategy definition."""

    name: str
    weights: dict[str, float]
    rebalance_frequency: RebalanceFrequency = "monthly"
    start_date: str | None = None
    end_date: str | None = None
    initial_capital: float = 10_000.0
    transaction_cost_bps: float = 5.0

    def __post_init__(self) -> None:
        if not self.name.strip() or len(self.name.strip()) > 100:
            raise ValueError("Strategy name must contain 1 to 100 characters")
        if self.rebalance_frequency not in {"buy_and_hold", "monthly", "quarterly", "annual"}:
            raise ValueError("rebalance_frequency must be buy_and_hold, monthly, quarterly, or annual")
        if not self.weights:
            raise ValueError("At least one asset weight is required")
        normalized: dict[str, float] = {}
        for raw_symbol, raw_weight in self.weights.items():
            symbol = str(raw_symbol).strip().upper()
            if not _SYMBOL_PATTERN.fullmatch(symbol):
                raise ValueError(f"Invalid asset symbol: {raw_symbol!r}")
            weight = float(raw_weight)
            if not math.isfinite(weight) or weight < 0:
                raise ValueError(f"Weight for {symbol} must be finite and non-negative")
            if symbol in normalized:
                raise ValueError(f"Duplicate asset symbol after normalization: {symbol}")
            normalized[symbol] = weight
        if not math.isclose(sum(normalized.values()), 1.0, rel_tol=0.0, abs_tol=1e-8):
            raise ValueError("Asset weights must sum to exactly 1.0")
        if not math.isfinite(self.initial_capital) or self.initial_capital <= 0:
            raise ValueError("initial_capital must be finite and greater than zero")
        if not math.isfinite(self.transaction_cost_bps) or self.transaction_cost_bps < 0:
            raise ValueError("transaction_cost_bps must be finite and non-negative")

        start = _validate_date(self.start_date, "start_date")
        end = _validate_date(self.end_date, "end_date")
        if start and end and start > end:
            raise ValueError("start_date must be on or before end_date")
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "weights", normalized)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> StrategySpec:
        return cls(
            name=str(value["name"]),
            weights={str(k): float(v) for k, v in dict(value["weights"]).items()},
            rebalance_frequency=str(value.get("rebalance_frequency", "monthly")),  # type: ignore[arg-type]
            start_date=_optional_string(value.get("start_date")),
            end_date=_optional_string(value.get("end_date")),
            initial_capital=float(value.get("initial_capital", 10_000.0)),
            transaction_cost_bps=float(value.get("transaction_cost_bps", 5.0)),
        )


def _validate_date(value: str | None, field: str) -> str | None:
    if value is None or value == "":
        return None
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must use YYYY-MM-DD") from exc
    return parsed.isoformat()


def _optional_string(value: object) -> str | None:
    return None if value is None else str(value)
