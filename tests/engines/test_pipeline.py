import pandas as pd
import pytest
from mrq_engines.pipeline import build_country_factors


def test_country_factor_pipeline_uses_configured_signs():
    idx = pd.date_range("2020-01-31", periods=10, freq="ME")
    raw = pd.DataFrame(
        {
            "growth_up": range(1, 11),
            "bad_when_up": range(10, 0, -1),
            "inflation": range(1, 11),
        },
        index=idx,
        dtype=float,
    )
    cfg = {
        "growth": {
            "components": {
                "a": {"source": "growth_up", "transform": "level", "sign": 1, "weight": 1},
                "b": {"source": "bad_when_up", "transform": "level", "sign": -1, "weight": 1},
            },
            "min_components": 2,
        },
        "inflation": {
            "components": {
                "p": {"source": "inflation", "transform": "level", "sign": 1, "weight": 1}
            },
            "min_components": 1,
        },
    }

    factors = build_country_factors(raw, cfg, min_z_history=3)
    assert factors["growth"].dropna().iloc[-1] > 0
    assert factors["inflation"].dropna().iloc[-1] > 0


def test_partial_sources_preserve_configured_minimum_components():
    idx = pd.date_range("2000-01-31", periods=48, freq="ME")
    raw = pd.DataFrame({"growth_a": range(48)}, index=idx)
    cfg = {
        "growth": {
            "components": {
                "activity_a": {"source": "growth_a", "transform": "level"},
                "activity_b": {"source": "growth_b", "transform": "level"},
            },
            "min_components": 2,
        },
        "inflation": {
            "components": {"cpi": {"source": "missing_cpi", "transform": "level"}},
            "min_components": 1,
        },
    }

    partial = build_country_factors(raw, cfg, min_z_history=2, allow_missing_components=True)

    assert partial["growth"].isna().all()
    assert partial["inflation"].isna().all()
    with pytest.raises(KeyError, match="growth_b"):
        build_country_factors(raw, cfg, min_z_history=2)
