from __future__ import annotations

from .models import BusinessType, ValuationMethod


def preferred_methods(business_type: BusinessType) -> tuple[ValuationMethod, ...]:
    """Return transparent default valuation methods by business model.

    This is a routing contract, not a claim that every method is implemented in v0.
    """

    mapping = {
        BusinessType.GENERAL: (
            ValuationMethod.DCF,
            ValuationMethod.REVERSE_DCF,
        ),
        BusinessType.HIGH_GROWTH: (
            ValuationMethod.REVERSE_DCF,
            ValuationMethod.DCF,
        ),
        BusinessType.BANK: (ValuationMethod.RESIDUAL_INCOME,),
        BusinessType.INSURANCE: (ValuationMethod.EMBEDDED_VALUE,),
        BusinessType.REIT: (ValuationMethod.AFFO_NAV,),
        BusinessType.COMMODITY: (ValuationMethod.NORMALIZED_CYCLE,),
    }
    return mapping[business_type]
