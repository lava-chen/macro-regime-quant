"""Which XBRL tags mean what, per business model.

EDGAR publishes 500-odd us-gaap tags for a large filer, and the tag names are
not a glossary. Apple's 2017-09-30 balance sheet reports
``CashAndCashEquivalentsAtCarryingValue`` at 20.29 B and
``AvailableForSaleSecurities`` at 268.89 B; reading only the first turns a
company holding 192 B net cash into one carrying 77 B of net debt, and the
error propagates straight into a valuation.

So the mapping from economic concept to tag is not a lookup — it is a claim
about how a particular kind of business accounts for itself. It lives here,
next to the routing decision, where it can be reviewed, rather than buried in
whichever function happened to need the number first.

``UNSUPPORTED`` is a first-class answer. A bank or an insurer does not have
FCFF in any meaningful sense — interest is an operating cost, deposits are
inventory, and a free-cash-flow-to-firm model will produce a confident wrong
number. Refusing is the correct output.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import BusinessType, ValuationMethod

#: Sentinel meaning "this tag has no defensible mapping for this business model".
UNSUPPORTED = None


@dataclass(frozen=True)
class TagSet:
    """The tags that carry the economic quantities one model needs."""

    operating_income: str | None
    pretax_income: str | None
    income_tax: str | None
    depreciation_amortization: str | None
    capex: str | None
    accounts_receivable_change: str | None
    inventory_change: str | None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def supports_fcff(self) -> bool:
        """FCFF needs EBIT, a tax figure and cash investment to be defensible."""

        return all(
            v is not UNSUPPORTED
            for v in (
                self.operating_income,
                self.pretax_income,
                self.income_tax,
                self.depreciation_amortization,
                self.capex,
            )
        )

    def missing_for_fcff(self) -> list[str]:
        required = {
            "operating_income": self.operating_income,
            "pretax_income": self.pretax_income,
            "income_tax": self.income_tax,
            "depreciation_amortization": self.depreciation_amortization,
            "capex": self.capex,
        }
        return [name for name, tag in required.items() if tag is UNSUPPORTED]

    def as_mapping(self) -> dict[str, str]:
        """``{us-gaap tag: concept}``, ready to hand to a provider.

        The provider's own convention is tag-first — ``metrics={"Revenues":
        "revenue"}`` — because the tag is what the filer actually reports and
        the concept is this project's label. Returning it any other way round
        silently yields an empty frame, since no us-gaap tag is named
        ``operating_income``.
        """

        pairs = {
            "operating_income": self.operating_income,
            "pretax_income": self.pretax_income,
            "income_tax": self.income_tax,
            "depreciation_amortization": self.depreciation_amortization,
            "capex": self.capex,
            "accounts_receivable_change": self.accounts_receivable_change,
            "inventory_change": self.inventory_change,
        }
        return {tag: concept for concept, tag in pairs.items() if tag is not UNSUPPORTED}


_INDUSTRIAL = TagSet(
    operating_income="OperatingIncomeLoss",
    pretax_income=(
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest"
    ),
    income_tax="IncomeTaxExpenseBenefit",
    # The flow statement's combined line covers both, and D&A reported inside
    # investing activities is easier to trust than a split across two tags.
    depreciation_amortization="DepreciationAmortizationAndAccretionNet",
    capex="PaymentsToAcquirePropertyPlantAndEquipment",
    accounts_receivable_change="IncreaseDecreaseInAccountsReceivable",
    inventory_change="IncreaseDecreaseInInventories",
    notes=(
        "Working-capital change is a partial proxy: it covers receivables and",
        "inventory only. Other operating assets and liabilities are excluded, so",
        "FCFF is an approximation rather than a reconciliation.",
    ),
)

# Banks and insurers are routed elsewhere on purpose. Interest and deposits sit
# inside operating items, so an FCFF model does not merely lose precision — it
# is being asked the wrong question.
_FINANCIAL = TagSet(
    operating_income=UNSUPPORTED,
    pretax_income="IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
    income_tax="IncomeTaxExpenseBenefit",
    depreciation_amortization=UNSUPPORTED,
    capex=UNSUPPORTED,
    accounts_receivable_change=UNSUPPORTED,
    inventory_change=UNSUPPORTED,
    notes=(
        "Financial statements for banks and insurers do not decompose into FCFF.",
        "Use residual income, embedded value, or a capital-based model instead.",
    ),
)

_REIT = TagSet(
    operating_income="OperatingIncomeLoss",
    pretax_income=(
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest"
    ),
    income_tax="IncomeTaxExpenseBenefit",
    # Real-estate companies book large non-cash real-estate depreciation that
    # returns nothing to shareholders, so D&A is not added back here.
    depreciation_amortization=UNSUPPORTED,
    capex="PaymentsToAcquirePropertyPlantAndEquipment",
    accounts_receivable_change="IncreaseDecreaseInAccountsReceivable",
    inventory_change=UNSUPPORTED,
    notes=(
        "REIT FCFF is not a supported basis. Depreciation on real estate is a real",
        "economic cost that never reverses. Route to AFFO/NAV.",
    ),
)

_COMMODITY = TagSet(
    operating_income="OperatingIncomeLoss",
    pretax_income=(
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest"
    ),
    income_tax="IncomeTaxExpenseBenefit",
    depreciation_amortization="DepreciationAmortizationAndAccretionNet",
    capex="PaymentsToAcquirePropertyPlantAndEquipment",
    accounts_receivable_change="IncreaseDecreaseInAccountsReceivable",
    inventory_change="IncreaseDecreaseInInventories",
    notes=(
        "Cyclical peak earnings are not a valuation base. Normalise the cycle",
        "before discounting; a point FCFF at the top of the cycle is the classic",
        "way to conclude a cheap stock is expensive.",
    ),
)

TAGS_BY_BUSINESS: dict[BusinessType, TagSet] = {
    BusinessType.GENERAL: _INDUSTRIAL,
    BusinessType.HIGH_GROWTH: _INDUSTRIAL,
    BusinessType.BANK: _FINANCIAL,
    BusinessType.INSURANCE: _FINANCIAL,
    BusinessType.REIT: _REIT,
    BusinessType.COMMODITY: _COMMODITY,
}

#: Methods that consume a free-cash-flow figure. Anything outside this set is
#: built from different primitives and must not be fed FCFF.
FCFF_METHODS = frozenset({ValuationMethod.DCF, ValuationMethod.REVERSE_DCF})


def tags_for(business_type: BusinessType) -> TagSet:
    return TAGS_BY_BUSINESS.get(business_type, _INDUSTRIAL)


def supports_fcff(business_type: BusinessType, method: ValuationMethod) -> bool:
    """Whether this business model can support this method's cash-flow basis."""

    if method not in FCFF_METHODS:
        return True
    return tags_for(business_type).supports_fcff


def balance_sheet_tags() -> dict[str, str]:
    """Tags for the net-debt side, with the securities traps called out.

    A cash-rich company usually holds most of its liquid assets outside
    ``CashAndCashEquivalentsAtCarryingValue``. Reading only the cash line
    overstates debt substantially, so the caller is expected to check which of
    these the filer actually reports.
    """

    return {
        "cash_and_equivalents": "CashAndCashEquivalentsAtCarryingValue",
        "short_term_investments": "ShortTermInvestments",
        "marketable_securities_current": "MarketableSecuritiesCurrent",
        "available_for_sale_securities": "AvailableForSaleSecurities",
        "long_term_investments": "LongTermInvestments",
        "long_term_debt": "LongTermDebtNoncurrent",
        "debt_current": "LongTermDebtCurrent",
    }
