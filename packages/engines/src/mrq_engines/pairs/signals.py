from __future__ import annotations

import numpy as np
import pandas as pd

from .models import PairSignalSpec

#: Position sign convention: +1 is long the spread (long asset A, short asset B),
#: -1 is short the spread. 0 means flat.
POSITION_LONG = 1
POSITION_FLAT = 0
POSITION_SHORT = -1


def zscore_state_machine(z: pd.Series, spec: PairSignalSpec) -> pd.DataFrame:
    """Turn a z-score series into a position series with entry and exit rules.

    The machine is deliberately explicit rather than vectorised: the exit of a
    position depends on how the position was entered, and a vectorised
    formulation would hide that dependency. A position is opened when the
    spread is stretched, and closed when it has come back far enough, when it
    has stretched further against us, or when it has been held too long.

    After a stop or a timeout the machine stays disarmed until the spread has
    come back inside the exit band. Re-entering at the same stretched level
    that just stopped us out would buy the same loss again.
    """

    if not isinstance(z.index, pd.DatetimeIndex):
        raise TypeError("z-score must be indexed by date")
    if z.index.has_duplicates:
        raise ValueError("z-score dates must be unique")

    position = np.zeros(len(z), dtype=float)
    entry = np.zeros(len(z), dtype=bool)
    exit_ = np.zeros(len(z), dtype=bool)
    holding = np.zeros(len(z), dtype=int)

    current = POSITION_FLAT
    days_held = 0
    armed = True
    for offset, value in enumerate(z.to_numpy(dtype=float)):
        if not np.isfinite(value):
            # No z-score means no information: hold whatever is already open.
            position[offset] = current
            holding[offset] = days_held
            continue

        if not armed and abs(value) <= spec.exit_z:
            armed = True

        if current == POSITION_FLAT:
            if armed and value >= spec.entry_z:
                current = POSITION_SHORT
                days_held = 0
                entry[offset] = True
            elif armed and value <= -spec.entry_z:
                current = POSITION_LONG
                days_held = 0
                entry[offset] = True
        else:
            days_held += 1
            stretched_against_us = abs(value) >= spec.stop_z if spec.stop_z is not None else False
            timed_out = (
                spec.max_holding_days is not None and days_held >= spec.max_holding_days
            )
            reverted = abs(value) <= spec.exit_z
            if reverted or stretched_against_us or timed_out:
                current = POSITION_FLAT
                days_held = 0
                exit_[offset] = True
                if not reverted:
                    armed = False

        position[offset] = current
        holding[offset] = days_held

    return pd.DataFrame(
        {
            "position": position,
            "entry": entry,
            "exit": exit_,
            "holding_days": holding,
        },
        index=z.index,
    )
