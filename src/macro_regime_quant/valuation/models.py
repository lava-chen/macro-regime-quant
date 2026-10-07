from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class BusinessType(StrEnum):
    GENERAL = "general"
    BANK = "bank"
    INSURANCE = "insurance"
    REIT = "reit"
    COMMODITY = "commodity"
    HIGH_GROWTH = "high_growth"


class ValuationMethod(StrEnum):
    DCF = "dcf"
    REVERSE_DCF = "reverse_dcf"
    RESIDUAL_INCOME = "residual_income"
    EMBEDDED_VALUE = "embedded_value"
    AFFO_NAV = "affo_nav"
    NORMALIZED_CYCLE = "normalized_cycle"


@dataclass(frozen=True)
class ValuationResult:
    company_id: str
    as_of_date: str
    method: ValuationMethod
    value_per_share_low: float
    value_per_share_base: float
    value_per_share_high: float
    market_price: float | None = None
    confidence: float = 1.0
    model_version: str = "valuation-v0"

    def __post_init__(self) -> None:
        if min(
            self.value_per_share_low,
            self.value_per_share_base,
            self.value_per_share_high,
        ) < 0:
            raise ValueError("Valuation outputs cannot be negative")
        if not (
            self.value_per_share_low
            <= self.value_per_share_base
            <= self.value_per_share_high
        ):
            raise ValueError("Valuation band must satisfy low <= base <= high")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")

    @property
    def margin_of_safety(self) -> float | None:
        if self.market_price is None or self.market_price <= 0:
            return None
        return self.value_per_share_base / self.market_price - 1.0
