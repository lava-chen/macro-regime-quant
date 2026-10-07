from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class EntityKind(StrEnum):
    COMPANY = "company"
    SECURITY = "security"
    FUND = "fund"
    SECTOR = "sector"
    ASSET_CLASS = "asset_class"
    INVESTOR_BUCKET = "investor_bucket"


class SecurityType(StrEnum):
    COMMON_STOCK = "common_stock"
    ADR = "adr"
    ETF = "etf"
    BOND = "bond"
    FUTURE = "future"
    FX = "fx"
    INDEX = "index"


@dataclass(frozen=True)
class Company:
    """Economic company identity independent of any one listing."""

    company_id: str
    name: str
    domicile: str
    sector: str
    industry: str = ""

    def __post_init__(self) -> None:
        if not self.company_id or ":" not in self.company_id:
            raise ValueError("company_id must be a stable namespaced id such as US:AAPL")
        if not self.name:
            raise ValueError("Company name is required")


@dataclass(frozen=True)
class Security:
    """Tradable security identity linked to an economic company when applicable."""

    security_id: str
    ticker: str
    exchange: str
    currency: str
    security_type: SecurityType
    company_id: str | None = None

    def __post_init__(self) -> None:
        if not self.security_id or ":" not in self.security_id:
            raise ValueError(
                "security_id must be a stable namespaced id such as NASDAQ:AAPL"
            )
        if not self.ticker or not self.exchange or not self.currency:
            raise ValueError("ticker, exchange and currency are required")
