"""Tests for FCFF construction and the per-business-model tag mapping.

These guard the specific shortcuts that produce confident wrong valuations:
treating a cash pile's interest income as operations, and reading a balance
sheet's cash line while ignoring the securities sitting next to it.
"""

from __future__ import annotations

import pandas as pd
import pytest
from mrq_engines.valuation.fcff import build_fcff
from mrq_engines.valuation.models import BusinessType, ValuationMethod
from mrq_engines.valuation.taxonomy import (
    UNSUPPORTED,
    balance_sheet_tags,
    supports_fcff,
    tags_for,
)

# Apple's FY2017 as filed 2017-11-03 and visible to a 2018-01-01 backtest.
APPLE = {
    "metric": ["operating_income", "pretax_income", "income_tax",
               "depreciation_amortization", "capex"],
    "value": [61.34e9, 64.09e9, 15.74e9, 10.16e9, 12.45e9],
}


def frame(**overrides) -> pd.DataFrame:
    """A single-fiscal-period frame; kwargs replace a value by its concept name."""

    values = dict(zip(APPLE["metric"], APPLE["value"], strict=True))
    values.update(overrides)
    df = pd.DataFrame({"metric": list(values), "value": list(values.values())})
    df["period_end"] = pd.Timestamp("2017-09-30")
    df["available_date"] = pd.Timestamp("2017-11-03")
    return df


# --- the arithmetic -------------------------------------------------------


def test_fcff_is_built_from_nopat_not_operating_cash_flow():
    f = build_fcff(frame())
    assert f.effective_tax_rate == pytest.approx(15.74 / 64.09)
    assert f.nopat == pytest.approx(61.34e9 * (1 - 15.74 / 64.09))
    assert f.fcff == pytest.approx(f.nopat + 10.16e9 - 12.45e9)


def test_interest_income_cannot_leak_into_the_cash_flow():
    """Operating cash flow would carry interest on the cash pile; FCFF must not."""

    f = build_fcff(frame())
    naive = 63.60e9 - 12.45e9  # OCF - capex, the tempting shortcut
    assert f.fcff < naive
    assert f.fcff < naive
    # FY2017 Apple: EBIT 61.34 x (1 - 24.6%) = 46.26, +10.16 D&A, -12.45 capex
    assert f.fcff == pytest.approx(43.97e9, rel=1e-3)


def test_working_capital_build_reduces_cash_flow():
    without = build_fcff(frame()).fcff
    with_wc = build_fcff(frame(), working_capital_change=4.81e9).fcff
    assert with_wc == pytest.approx(without - 4.81e9)


def test_effective_rate_beats_a_statutory_assumption():
    """Apple paid 24.6%, not the textbook 21%."""

    assert build_fcff(frame()).effective_tax_rate == pytest.approx(0.2456, rel=1e-3)


def test_zero_pretax_income_is_refused():
    with pytest.raises(ValueError, match="normalise across a cycle"):
        _ = build_fcff(frame(pretax_income=0.0)).effective_tax_rate


# --- point-in-time integrity ---------------------------------------------


def test_available_date_must_not_precede_period_end():
    df = frame()
    df["available_date"] = pd.Timestamp("2017-01-01")
    with pytest.raises(ValueError, match="not knowable"):
        build_fcff(df)


def test_mixed_periods_are_refused():
    df = frame()
    df["period_end"] = [pd.Timestamp("2017-09-30")] * 4 + [pd.Timestamp("2016-09-30")]
    with pytest.raises(ValueError, match="Mixing YTD and annual"):
        build_fcff(df)


def test_missing_metric_names_what_is_available():
    df = frame()
    df = df.drop(df[df.metric == "capex"].index)
    with pytest.raises(KeyError, match="capex"):
        build_fcff(df)


def test_components_expose_every_term():
    text = build_fcff(frame()).explain()
    for term in ("EBIT", "NOPAT", "D&A", "capex", "FCFF"):
        assert term in text


# --- the mapping is a claim, not a lookup -------------------------------


def test_financials_cannot_support_an_fcff_model():
    for business in (BusinessType.BANK, BusinessType.INSURANCE):
        assert not supports_fcff(business, ValuationMethod.DCF)
        assert "FCFF" in " ".join(tags_for(business).notes)


def test_reits_refuse_fcff_because_depreciation_is_real():
    assert not supports_fcff(BusinessType.REIT, ValuationMethod.DCF)
    assert tags_for(BusinessType.REIT).depreciation_amortization is UNSUPPORTED


def test_operating_businesses_support_fcff():
    for business in (BusinessType.GENERAL, BusinessType.HIGH_GROWTH, BusinessType.COMMODITY):
        assert supports_fcff(business, ValuationMethod.DCF)


def test_non_fcff_methods_are_never_gated():
    """A bank is perfectly validable — just not by this cash-flow basis."""

    assert supports_fcff(BusinessType.BANK, ValuationMethod.RESIDUAL_INCOME)


def test_missing_for_fcff_reports_by_concept():
    assert "depreciation_amortization" in tags_for(BusinessType.REIT).missing_for_fcff()


def test_mapping_is_tag_first_so_a_provider_can_take_it():
    """A concept-keyed dict matches no us-gaap tag and returns nothing."""

    mapping = tags_for(BusinessType.GENERAL).as_mapping()
    assert "OperatingIncomeLoss" in mapping
    assert mapping["OperatingIncomeLoss"] == "operating_income"


def test_unsupported_tags_stay_out_of_the_mapping():
    mapping = tags_for(BusinessType.BANK).as_mapping()
    assert mapping == {
        tags_for(BusinessType.BANK).pretax_income: "pretax_income",
        tags_for(BusinessType.BANK).income_tax: "income_tax",
    }


def test_balance_sheet_covers_securities_not_just_cash():
    """The 20 B vs 269 B trap is only avoidable if the tags are offered."""

    tags = balance_sheet_tags()
    assert "available_for_sale_securities" in tags
    assert "marketable_securities_current" in tags
    assert tags["cash_and_equivalents"] != tags["available_for_sale_securities"]


def test_commodity_tag_set_warns_about_cyclicality():
    assert "cycle" in " ".join(tags_for(BusinessType.COMMODITY).notes)
