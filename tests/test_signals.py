import pytest

from macro_regime_quant.signals.composite import SignalComponent, combine_signals


def test_signal_composite_is_confidence_adjusted():
    score = combine_signals(
        (
            SignalComponent("valuation", score=2.0, weight=1.0, confidence=1.0),
            SignalComponent("flow", score=-1.0, weight=1.0, confidence=0.5),
        )
    )
    assert score == pytest.approx(1.0)
