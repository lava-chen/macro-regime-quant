from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FlowReconciliation:
    previous_stock: float
    current_stock: float
    valuation_effect: float
    other_adjustments: float
    inferred_net_flow: float
    residual: float


def reconcile_stock_change(
    previous_stock: float,
    current_stock: float,
    *,
    valuation_effect: float = 0.0,
    other_adjustments: float = 0.0,
) -> FlowReconciliation:
    """Apply the stock-flow identity.

    current_stock = previous_stock + net_flow + valuation_effect + other_adjustments

    With observed stocks and adjustment terms, net_flow is the residual implied flow.
    """

    inferred = (
        current_stock
        - previous_stock
        - valuation_effect
        - other_adjustments
    )
    reconstructed = (
        previous_stock
        + inferred
        + valuation_effect
        + other_adjustments
    )
    residual = current_stock - reconstructed
    return FlowReconciliation(
        previous_stock=previous_stock,
        current_stock=current_stock,
        valuation_effect=valuation_effect,
        other_adjustments=other_adjustments,
        inferred_net_flow=inferred,
        residual=residual,
    )


def estimate_fund_flow_from_aum(
    previous_aum: float,
    current_aum: float,
    asset_return: float,
) -> float:
    """Estimate external net flow after removing the market-value effect."""

    if previous_aum < 0 or current_aum < 0:
        raise ValueError("AUM cannot be negative")
    valuation_adjusted_previous = previous_aum * (1.0 + asset_return)
    return current_aum - valuation_adjusted_previous
