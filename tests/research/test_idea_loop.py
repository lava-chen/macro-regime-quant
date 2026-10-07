"""Tests for the idea loop.

Two bugs are pinned here because both produced a report that looked entirely
plausible while being wrong:

1. Averaging every (asset, horizon) column into one target made the IC
   identical at every horizon — a result that reads like a finding and is
   arithmetic.
2. Passing a price *level* instead of a forward return correlated the signal
   against the level itself, yielding "returns" in the hundreds of percent.
"""

from __future__ import annotations

from pathlib import Path

import mrq_engines.pipeline as pipeline_mod
import numpy as np
import pandas as pd
import pytest
from mrq_engines.pipeline import load_monthly_panel
from mrq_research.factor_test import analyse_factor
from mrq_research.forward_returns import forward_returns
from mrq_research.idea import IdeaError, evaluate_idea, load_signal_function

IDX = pd.date_range("2010-01-31", periods=120, freq="ME")
PRICES = pd.DataFrame(
    {"a": pd.Series(range(100, 220), index=IDX, dtype=float),
     "b": pd.Series(range(50, 170), index=IDX, dtype=float)}
)


def _signal(panel: pd.DataFrame) -> pd.Series:
    return panel.iloc[:, 0].rank(pct=True)


class _Stub:
    def __init__(self, frame): self.frame = frame

    def fetch(self, spec, start=None, end=None):
        return self.frame.copy()


@pytest.fixture(autouse=True)
def _stub_panel(monkeypatch: pytest.MonkeyPatch):
    base = np.arange(120, dtype=float)
    monkeypatch.setattr(
        pipeline_mod,
        "default_provider_registry",
        lambda: {
            "stub": _Stub(pd.DataFrame({"observation_date": IDX, "value": base})),
            "price": _Stub(pd.DataFrame({"observation_date": IDX, "value": base + 1000.0})),
            "rate": _Stub(pd.DataFrame({"observation_date": IDX, "value": base / 100.0 - 1.0})),
        },
    )


def _catalog(tmp_path: Path) -> Path:
    path = tmp_path / "catalog.yaml"
    path.write_text(
        """
series:
  macro_x:
    provider: stub
    symbol: X
    kind: macro
    frequency: monthly
    release_lag_days: 10
  good_price:
    provider: price
    symbol: P
    kind: market
    frequency: monthly
    release_lag_days: 0
  a_rate:
    provider: rate
    symbol: R
    kind: rate
    frequency: monthly
    release_lag_days: 0
""",
        encoding="utf-8",
    )
    return path


# --------------------------------------------------------------------------- #
# Horizons must actually differ
# --------------------------------------------------------------------------- #


def test_ic_is_computed_per_horizon_not_averaged():
    """Regression: a single averaged target gave identical IC at every horizon."""

    signal = pd.Series(np.arange(120, dtype=float), index=IDX) 
    fwd = forward_returns(PRICES, horizons=(1, 6, 12))

    d = analyse_factor(signal, fwd, horizons=(1, 6, 12))
    values = [d.ic_by_horizon[h] for h in (1, 6, 12)]
    assert not (values[0] == values[1] == values[2]), (
        f"IC identical across horizons ({values[0]}): the target was not "
        "resolved per horizon"
    )


def test_sample_size_shrinks_as_the_horizon_grows():
    signal = pd.Series(np.arange(120, dtype=float), index=IDX) 
    fwd = forward_returns(PRICES, horizons=(1, 6, 12))
    d = analyse_factor(signal, fwd, horizons=(1, 6, 12))
    assert (
        d.n_obs_by_horizon[1] > d.n_obs_by_horizon[6] > d.n_obs_by_horizon[12]
    )


def test_a_mean_reverting_signal_on_a_rising_asset_is_detected():
    """A falling signal against a rising asset must show a positive IC.

    Watch the sign: a rising price implies a *smaller* percentage gain ahead, so
    a signal rising with the asset correlates negatively. Percent returns shrink
    as the base grows — assuming the opposite would let this test pass for the
    wrong reason.
    """

    signal = pd.Series(np.arange(120, dtype=float)[::-1], index=IDX, dtype=float)
    fwd = forward_returns(PRICES, horizons=(1,))
    d = analyse_factor(signal, fwd, horizons=(1,))
    assert d.ic_by_horizon[1] > 0.5


def test_quantile_returns_come_back_with_a_spread():
    signal = pd.Series(np.arange(120, dtype=float)[::-1], index=IDX, dtype=float) 
    fwd = forward_returns(PRICES, horizons=(3,))
    d = analyse_factor(signal, fwd, horizons=(3,), quantiles=5)
    assert len(d.quantile_returns) == 5
    assert d.quantile_returns.attrs["long_short_spread"] > 0


def test_constant_signal_is_reported_not_silently_zero():
    signal = pd.Series(1.0, index=IDX)
    fwd = forward_returns(PRICES, horizons=(3,))
    d = analyse_factor(signal, fwd, horizons=(3,))
    assert pd.isna(d.ic_by_horizon[3])
    assert any("constant" in w for w in d.warnings)


# --------------------------------------------------------------------------- #
# evaluate_idea guards
# --------------------------------------------------------------------------- #


def test_rate_series_are_rejected_as_forward_assets(tmp_path: Path):
    """A yield has no percentage return; p.shift(-h)/p diverges near zero."""

    with pytest.raises(IdeaError, match="not price series"):
        evaluate_idea(
            _signal,
            series=["macro_x"],
            forward_prices=load_monthly_panel(
                _catalog(tmp_path), ["a_rate"], start="2010-01-01", end="2020-01-01"
            ),
            start="2010-01-01",
            end="2020-01-01",
            catalog_path=_catalog(tmp_path),
        )


def test_evaluate_idea_produces_diagnostics(tmp_path: Path):
    catalog = _catalog(tmp_path)
    result = evaluate_idea(
        _signal,
        series=["macro_x"],
        forward_prices=load_monthly_panel(
            catalog, ["good_price"], start="2010-01-01", end="2020-01-01"
        ),
        start="2010-01-01",
        end="2020-01-01",
        catalog_path=catalog,
    )
    assert not pd.isna(result.diagnostics.ic_by_horizon[1])
    assert "IC" in result.diagnostics.verdict()


def test_unknown_series_raises(tmp_path: Path):
    catalog = _catalog(tmp_path)
    with pytest.raises(KeyError):
        evaluate_idea(
            _signal,
            series=["nope"],
            forward_prices=load_monthly_panel(
                catalog, ["good_price"], start="2010-01-01", end="2020-01-01"
            ),
            start="2010-01-01",
            end="2020-01-01",
            catalog_path=catalog,
        )


# --------------------------------------------------------------------------- #
# Signal loading
# --------------------------------------------------------------------------- #


def test_signal_file_is_loaded_by_any_accepted_name(tmp_path: Path):
    for name in ("signal", "idea", "build_signal"):
        path = tmp_path / f"{name}.py"
        path.write_text(
            "import pandas as pd\n"
            "def " + name + "(panel: pd.DataFrame) -> pd.Series:\n"
            "    return panel.iloc[:, 0]\n",
            encoding="utf-8",
        )
        assert callable(load_signal_function(path))


def test_signal_file_without_an_entry_point_is_rejected(tmp_path: Path):
    path = tmp_path / "empty.py"
    path.write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(IdeaError, match="defines no callable"):
        load_signal_function(path)


def test_missing_idea_file_is_reported(tmp_path: Path):
    with pytest.raises(IdeaError, match="No such idea file"):
        load_signal_function(tmp_path / "nope.py")


def test_import_error_in_idea_file_is_surfaced(tmp_path: Path):
    path = tmp_path / "broken.py"
    path.write_text("raise ValueError('boom')\n", encoding="utf-8")
    with pytest.raises(IdeaError, match="boom"):
        load_signal_function(path)


def test_returning_a_dataframe_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    catalog = _catalog(tmp_path)
    with pytest.raises(IdeaError, match="expected a pandas Series"):
        evaluate_idea(
            lambda panel: pd.DataFrame({"x": [1.0]}, index=panel.index),
            series=["macro_x"],
            forward_prices=load_monthly_panel(
                catalog, ["good_price"], start="2010-01-01", end="2020-01-01"
            ),
            start="2010-01-01",
            end="2020-01-01",
            catalog_path=catalog,
        )
