from __future__ import annotations

from enum import StrEnum

import pandas as pd


class MacroRegime(StrEnum):
    GOLDILOCKS = "goldilocks"
    REFLATION = "reflation"
    STAGFLATION = "stagflation"
    RECESSION = "recession"


def classify_regime(growth: pd.Series, inflation: pd.Series) -> pd.Series:
    """Classify the simple 2D growth/inflation macro state."""

    frame = pd.concat({"growth": growth, "inflation": inflation}, axis=1)
    out = pd.Series(pd.NA, index=frame.index, dtype="string", name="regime")
    valid = frame.notna().all(axis=1)

    g_up = frame["growth"] > 0
    i_up = frame["inflation"] > 0

    out.loc[valid & g_up & ~i_up] = MacroRegime.GOLDILOCKS.value
    out.loc[valid & g_up & i_up] = MacroRegime.REFLATION.value
    out.loc[valid & ~g_up & i_up] = MacroRegime.STAGFLATION.value
    out.loc[valid & ~g_up & ~i_up] = MacroRegime.RECESSION.value
    return out
