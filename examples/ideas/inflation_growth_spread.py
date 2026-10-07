"""Idea: when inflation is accelerating and growth is not, favour bonds.

Hypothesis: a rising inflation-minus-growth differential is a tightening of
financial conditions. Real assets should lag, duration-heavy assets should hold
up better over the next few months.

The panel handed to `signal` is point-in-time — each row is what was knowable
at that month-end — so this cannot peek at the future by construction.
"""

import pandas as pd


def signal(panel: pd.DataFrame) -> pd.Series:
    # 12-month change in inflation vs 12-month change in growth.
    inflation = panel["us_core_cpi"].pct_change(12, fill_method=None)
    growth = panel["us_payrolls"].pct_change(12, fill_method=None)

    spread = inflation - growth
    # Rank into [0, 1]: high spread -> long the defensive asset.
    ranked = spread.rank(pct=True)
    return ranked
