import pytest

from macro_regime_quant.entities.models import Company, Security, SecurityType


def test_company_and_security_have_stable_namespaced_ids():
    company = Company(
        company_id="US:AAPL",
        name="Apple Inc.",
        domicile="US",
        sector="Technology",
    )
    security = Security(
        security_id="NASDAQ:AAPL",
        ticker="AAPL",
        exchange="NASDAQ",
        currency="USD",
        security_type=SecurityType.COMMON_STOCK,
        company_id=company.company_id,
    )

    assert security.company_id == "US:AAPL"


def test_company_rejects_unnamespaced_id():
    with pytest.raises(ValueError, match="namespaced"):
        Company(
            company_id="AAPL",
            name="Apple Inc.",
            domicile="US",
            sector="Technology",
        )
