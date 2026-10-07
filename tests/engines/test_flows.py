import pytest
from mrq_engines.flow.stock_flow import (
    estimate_fund_flow_from_aum,
    reconcile_stock_change,
)


def test_stock_flow_identity_removes_valuation_effect():
    result = reconcile_stock_change(
        previous_stock=100.0,
        current_stock=110.0,
        valuation_effect=8.0,
    )
    assert result.inferred_net_flow == pytest.approx(2.0)
    assert result.residual == pytest.approx(0.0)


def test_aum_flow_estimate_removes_asset_return():
    flow = estimate_fund_flow_from_aum(
        previous_aum=100.0,
        current_aum=110.0,
        asset_return=0.08,
    )
    assert flow == pytest.approx(2.0)
