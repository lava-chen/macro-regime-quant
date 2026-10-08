"""Free cash flow to the firm, built from filed statements.

The tempting shortcut is ``operating_cash_flow - capex``. It is wrong twice
over, and both errors are invisible in the output:

1. **Operating cash flow contains interest income.** A company holding a large
   cash pile books interest inside operating activities. That cash is then
   subtracted a second time when net debt goes negative. The same money is
   counted once as operations and once as a debt offset.
2. **Operating cash flow is after tax on the whole enterprise, including
   interest.** FCFF must tax only the operating result, because the interest
   tax shield belongs to the capital structure, not the business.

So this builds the figure from its parts instead:

    FCFF = EBIT x (1 - effective tax rate) + D&A - capex - working capital build

Every input arrives through the point-in-time provider, so each carries the
date it was filed and nothing here can reach past the vantage point.

Tax is taken as the *effective* rate the company actually paid rather than a
statutory assumption: the effective rate encodes the loss carryforwards,
credits and jurisdictional mix that a headline rate would miss.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class FcffComponents:
    """The pieces a free-cash-flow figure is made of, kept visible.

    A single number cannot be audited. Every one of these can be checked
    against a filing, and a reader can see which term moved the answer.
    """

    operating_income: float
    pretax_income: float
    income_tax: float
    depreciation_amortization: float
    capex: float
    working_capital_change: float
    period_end: pd.Timestamp
    available_date: pd.Timestamp
    period_is_annual: bool

    @property
    def effective_tax_rate(self) -> float:
        if self.pretax_income == 0:
            raise ValueError(
                "pretax_income is zero, so the effective tax rate is undefined. "
                "A company with no pretax income cannot be valued off this year's "
                "cash flow — normalise across a cycle first."
            )
        return self.income_tax / self.pretax_income

    @property
    def nopat(self) -> float:
        """Net operating profit after tax — what operations earn for capital."""

        return self.operating_income * (1.0 - self.effective_tax_rate)

    @property
    def fcff(self) -> float:
        return (
            self.nopat
            + self.depreciation_amortization
            - self.capex
            - self.working_capital_change
        )

    def explain(self) -> str:
        return (
            f"EBIT {self.operating_income:,.0f} x (1 - tax {self.effective_tax_rate:.1%}) "
            f"= NOPAT {self.nopat:,.0f}  + D&A {self.depreciation_amortization:,.0f}  "
            f"- capex {self.capex:,.0f}  - WC build {self.working_capital_change:,.0f}  "
            f"= FCFF {self.fcff:,.0f}"
        )


def annual_value(frame: pd.DataFrame, metric: str) -> tuple[float, pd.Timestamp, pd.Timestamp] | None:
    """Pull one full-year figure out of a point-in-time fundamentals frame.

    Quarterly (10-Q) figures are year-to-date, so a Q3 10-Q is nine months of
    activity, not three. Using it as a year overstates nothing but annualises
    wrongly and makes a trend meaningless. ``fp`` is not in the frame, so the
    annual pick is the value whose period is followed by a later annual filing;
    callers pass a frame already restricted to one filing where that matters.
    """

    rows = frame[(frame["metric"] == metric) & (frame["period_end"] == frame["period_end"].max())]
    if rows.empty:
        return None
    row = rows.iloc[0]
    return float(row["value"]), pd.Timestamp(row["period_end"]), pd.Timestamp(row["available_date"])


def build_fcff(
    frame: pd.DataFrame,
    *,
    working_capital_change: float = 0.0,
    period_is_annual: bool = True,
) -> FcffComponents:
    """Assemble FCFF from a point-in-time fundamentals frame.

    ``frame`` must come from a provider filtered to a vantage point, and should
    contain a single fiscal period. ``working_capital_change`` defaults to zero
    only because the tax ontology marks it optional; pass the real figure when
    the filer reports receivables and inventory, and read the returned
    components before trusting the total.
    """

    def value(metric: str) -> float:
        pick = frame[frame["metric"] == metric]
        if pick.empty:
            raise KeyError(
                f"frame has no '{metric}'. Available: {sorted(set(frame['metric']))}"
            )
        return float(pick.sort_values("period_end").iloc[-1]["value"])

    if frame["period_end"].nunique() > 1:
        raise ValueError(
            "build_fcff expects a single fiscal period, got "
            f"{sorted(set(frame['period_end'].astype(str)))}. Mixing YTD and annual "
            "figures silently inflates the result."
        )

    period_end = pd.Timestamp(frame["period_end"].iloc[0])
    available = pd.Timestamp(frame["available_date"].iloc[0])
    if available < period_end:
        raise ValueError(
            f"available_date {available.date()} precedes period_end {period_end.date()}, "
            "which means the figure was not knowable when the period closed"
        )

    return FcffComponents(
        operating_income=value("operating_income"),
        pretax_income=value("pretax_income"),
        income_tax=value("income_tax"),
        depreciation_amortization=value("depreciation_amortization"),
        capex=value("capex"),
        working_capital_change=working_capital_change,
        period_end=period_end,
        available_date=available,
        period_is_annual=period_is_annual,
    )
