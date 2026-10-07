from __future__ import annotations

from .dcf import DCFInputs, dcf_value


def implied_constant_growth(
    market_price: float,
    *,
    free_cash_flow: float,
    years: int,
    discount_rate: float,
    terminal_growth_rate: float,
    net_debt: float,
    diluted_shares: float,
    lower_bound: float = -0.50,
    upper_bound: float = 1.00,
    tolerance: float = 1e-7,
    max_iterations: int = 200,
) -> float:
    """Infer the constant annual FCF growth rate implied by a market price."""

    if market_price < 0:
        raise ValueError("market_price cannot be negative")
    if years <= 0:
        raise ValueError("years must be positive")
    if lower_bound >= upper_bound:
        raise ValueError("lower_bound must be smaller than upper_bound")

    def price_for(growth: float) -> float:
        return dcf_value(
            DCFInputs(
                free_cash_flow=free_cash_flow,
                growth_rates=(growth,) * years,
                discount_rate=discount_rate,
                terminal_growth_rate=terminal_growth_rate,
                net_debt=net_debt,
                diluted_shares=diluted_shares,
            )
        )

    low_price = price_for(lower_bound)
    high_price = price_for(upper_bound)
    if not low_price <= market_price <= high_price:
        raise ValueError(
            "market_price is outside the valuation range implied by the growth bounds"
        )

    low = lower_bound
    high = upper_bound
    for _ in range(max_iterations):
        mid = (low + high) / 2.0
        mid_price = price_for(mid)
        if abs(mid_price - market_price) <= tolerance:
            return mid
        if mid_price < market_price:
            low = mid
        else:
            high = mid

    return (low + high) / 2.0
