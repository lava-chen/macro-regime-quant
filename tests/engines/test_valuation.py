import pytest
from mrq_engines.valuation.dcf import DCFInputs, dcf_value
from mrq_engines.valuation.reverse_dcf import implied_constant_growth


def test_reverse_dcf_recovers_known_constant_growth():
    growth = 0.08
    inputs = DCFInputs(
        free_cash_flow=100.0,
        growth_rates=(growth,) * 5,
        discount_rate=0.10,
        terminal_growth_rate=0.03,
        net_debt=50.0,
        diluted_shares=10.0,
    )
    market_price = dcf_value(inputs)

    implied = implied_constant_growth(
        market_price,
        free_cash_flow=100.0,
        years=5,
        discount_rate=0.10,
        terminal_growth_rate=0.03,
        net_debt=50.0,
        diluted_shares=10.0,
    )
    assert implied == pytest.approx(growth, abs=1e-6)


def test_dcf_rejects_terminal_growth_at_or_above_discount_rate():
    with pytest.raises(ValueError, match="greater"):
        DCFInputs(
            free_cash_flow=100.0,
            growth_rates=(0.05,),
            discount_rate=0.03,
            terminal_growth_rate=0.03,
            net_debt=0.0,
            diluted_shares=10.0,
        )
