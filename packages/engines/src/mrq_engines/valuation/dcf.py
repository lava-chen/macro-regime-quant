from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DCFInputs:
    free_cash_flow: float
    growth_rates: tuple[float, ...]
    discount_rate: float
    terminal_growth_rate: float
    net_debt: float
    diluted_shares: float

    def __post_init__(self) -> None:
        if self.diluted_shares <= 0:
            raise ValueError("diluted_shares must be positive")
        if self.discount_rate <= self.terminal_growth_rate:
            raise ValueError("discount_rate must be greater than terminal_growth_rate")
        if self.discount_rate <= -1:
            raise ValueError("discount_rate must be greater than -100%")


def dcf_value(inputs: DCFInputs) -> float:
    """Return intrinsic equity value per diluted share using an FCFF-style DCF."""

    fcf = float(inputs.free_cash_flow)
    enterprise_value = 0.0

    for year, growth in enumerate(inputs.growth_rates, start=1):
        fcf *= 1.0 + growth
        enterprise_value += fcf / (1.0 + inputs.discount_rate) ** year

    terminal_fcf = fcf * (1.0 + inputs.terminal_growth_rate)
    terminal_value = terminal_fcf / (
        inputs.discount_rate - inputs.terminal_growth_rate
    )
    enterprise_value += terminal_value / (
        1.0 + inputs.discount_rate
    ) ** len(inputs.growth_rates)

    equity_value = enterprise_value - inputs.net_debt
    return equity_value / inputs.diluted_shares
