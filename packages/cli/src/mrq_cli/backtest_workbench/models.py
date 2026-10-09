from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from datetime import date
from itertools import pairwise
from typing import Literal

RebalanceFrequency = Literal["buy_and_hold", "monthly", "quarterly", "annual"]
ContributionDay = Literal["MON", "TUE", "WED", "THU", "FRI"]
_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9.^=_-]{1,24}$")


@dataclass(frozen=True)
class TakeProfitTier:
    """A one-time portfolio TWR trigger and fraction of current risk holdings to sell."""

    return_threshold: float
    sell_fraction: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.return_threshold) or self.return_threshold <= 0:
            raise ValueError("take-profit return_threshold must be finite and positive")
        if not math.isfinite(self.sell_fraction) or not 0 < self.sell_fraction <= 1:
            raise ValueError("take-profit sell_fraction must be in (0, 1]")


@dataclass(frozen=True)
class DrawdownRule:
    """Risk exposure cap activated by a peak-to-trough time-weighted drawdown."""

    trigger_drawdown: float
    max_invested_weight: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.trigger_drawdown) or not 0 < self.trigger_drawdown < 1:
            raise ValueError("drawdown trigger_drawdown must be in (0, 1)")
        if not math.isfinite(self.max_invested_weight) or not 0 <= self.max_invested_weight <= 1:
            raise ValueError("drawdown max_invested_weight must be in [0, 1]")


@dataclass(frozen=True)
class CashFlowPlan:
    """Recurring deposits and portfolio-level risk controls for an investment plan."""

    weekly_contribution_amount: float
    contribution_day: ContributionDay = "FRI"
    take_profit_tiers: tuple[TakeProfitTier, ...] = ()
    drawdown_rules: tuple[DrawdownRule, ...] = ()

    def __post_init__(self) -> None:
        amount = float(self.weekly_contribution_amount)
        if not math.isfinite(amount) or amount <= 0:
            raise ValueError("weekly_contribution_amount must be finite and positive")
        day = str(self.contribution_day).upper()
        if day not in {"MON", "TUE", "WED", "THU", "FRI"}:
            raise ValueError("contribution_day must be a US trading weekday from MON to FRI")
        tiers = tuple(_take_profit_tier(value) for value in self.take_profit_tiers)
        if any(a.return_threshold >= b.return_threshold for a, b in pairwise(tiers)):
            raise ValueError("take-profit tiers must be ordered by increasing return_threshold")
        rules = tuple(_drawdown_rule(value) for value in self.drawdown_rules)
        if any(a.trigger_drawdown >= b.trigger_drawdown for a, b in pairwise(rules)):
            raise ValueError("drawdown rules must be ordered by increasing trigger_drawdown")
        if any(a.max_invested_weight <= b.max_invested_weight for a, b in pairwise(rules)):
            raise ValueError("deeper drawdown rules must reduce max_invested_weight")
        object.__setattr__(self, "weekly_contribution_amount", amount)
        object.__setattr__(self, "contribution_day", day)
        object.__setattr__(self, "take_profit_tiers", tiers)
        object.__setattr__(self, "drawdown_rules", rules)

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> CashFlowPlan:
        return cls(
            weekly_contribution_amount=float(value["weekly_contribution_amount"]),
            contribution_day=str(value.get("contribution_day", "FRI")),  # type: ignore[arg-type]
            take_profit_tiers=tuple(
                _take_profit_tier(row) for row in value.get("take_profit_tiers", [])  # type: ignore[arg-type]
            ),
            drawdown_rules=tuple(
                _drawdown_rule(row) for row in value.get("drawdown_rules", [])  # type: ignore[arg-type]
            ),
        )


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
    cash_flow: CashFlowPlan | None = None

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
        cash_flow = self.cash_flow
        if isinstance(cash_flow, dict):
            cash_flow = CashFlowPlan.from_dict(cash_flow)
        elif cash_flow is not None and not isinstance(cash_flow, CashFlowPlan):
            raise TypeError("cash_flow must be a CashFlowPlan or object")
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "weights", normalized)
        object.__setattr__(self, "cash_flow", cash_flow)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> StrategySpec:
        raw_cash_flow = value.get("cash_flow")
        if raw_cash_flow is not None and not isinstance(raw_cash_flow, dict):
            raise TypeError("cash_flow must be an object")
        return cls(
            name=str(value["name"]),
            weights={str(k): float(v) for k, v in dict(value["weights"]).items()},
            rebalance_frequency=str(value.get("rebalance_frequency", "monthly")),  # type: ignore[arg-type]
            start_date=_optional_string(value.get("start_date")),
            end_date=_optional_string(value.get("end_date")),
            initial_capital=float(value.get("initial_capital", 10_000.0)),
            transaction_cost_bps=float(value.get("transaction_cost_bps", 5.0)),
            cash_flow=CashFlowPlan.from_dict(raw_cash_flow) if raw_cash_flow is not None else None,
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


def _take_profit_tier(value: object) -> TakeProfitTier:
    if isinstance(value, TakeProfitTier):
        return value
    if not isinstance(value, dict):
        raise TypeError("take-profit tiers must be objects")
    return TakeProfitTier(
        return_threshold=float(value["return_threshold"]),
        sell_fraction=float(value["sell_fraction"]),
    )


def _drawdown_rule(value: object) -> DrawdownRule:
    if isinstance(value, DrawdownRule):
        return value
    if not isinstance(value, dict):
        raise TypeError("drawdown rules must be objects")
    return DrawdownRule(
        trigger_drawdown=float(value["trigger_drawdown"]),
        max_invested_weight=float(value["max_invested_weight"]),
    )
