"""Research loop: write a signal, get an honest read on it.

The point of this module is to make "I had an idea" a two-minute loop rather
than an afternoon of plumbing. You write one function:

    def signal(panel: pd.DataFrame) -> pd.Series: ...

and get back information coefficient, rank IC, quantile monotonicity and
caveats. Nothing here sizes positions or models costs — those come after the
signal has earned the right to be sized.

The panel passed in is point-in-time: each row holds only what was knowable at
that month-end. Forward returns are computed strictly afterwards. A strong IC
here is therefore evidence about the signal, not an artefact of alignment.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pandas as pd
from mrq_engines.pipeline import load_monthly_panel

from .factor_test import DEFAULT_HORIZONS, FactorDiagnostics, analyse_factor
from .forward_returns import forward_returns

#: Signature a user module may expose. Anything else is rejected loudly rather
#: than failing later with a confusing pandas error.
SignalFunction = Callable[[pd.DataFrame], pd.Series]

ENTRY_NAMES = ("signal", "idea", "build_signal", "main")


class IdeaError(ValueError):
    """The supplied module is not a usable signal definition."""


@dataclass(frozen=True)
class IdeaResult:
    signal: pd.Series
    diagnostics: FactorDiagnostics
    panel: pd.DataFrame


def load_signal_function(path: str | Path) -> SignalFunction:
    """Import ``path`` and pull the signal callable out of it.

    The file is executed as a standalone module, not imported into this
    package's namespace, so a research script can keep its own scratch code
    without colliding with anything here.
    """

    module_path = Path(path).resolve()
    if not module_path.exists():
        raise IdeaError(f"No such idea file: {module_path}")

    spec = importlib.util.spec_from_file_location(module_path.stem, module_path)
    if spec is None or spec.loader is None:
        raise IdeaError(f"Could not load {module_path} as a Python module")
    module: ModuleType = importlib.util.module_from_spec(spec)
    sys.modules[module_path.stem] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise IdeaError(f"{module_path.name} raised on import: {exc}") from exc

    for name in ENTRY_NAMES:
        candidate = getattr(module, name, None)
        if callable(candidate):
            return candidate  # type: ignore[return-value]

    raise IdeaError(
        f"{module_path.name} defines no callable. Name your function one of: "
        f"{', '.join(ENTRY_NAMES)}"
    )


def _validate_signal(signal: pd.Series, panel: pd.DataFrame, origin: str) -> pd.Series:
    if not isinstance(signal, pd.Series):
        raise IdeaError(
            f"{origin} returned {type(signal).__name__}, expected a pandas Series"
        )
    if signal.empty:
        raise IdeaError(f"{origin} returned an empty signal")
    duplicated = signal.index.duplicated().any()
    if duplicated:
        raise IdeaError(f"{origin} returned duplicate index entries")
    # Reindexing onto the panel is what makes a same-bar signal impossible: a
    # value keyed to any other date simply does not exist here.
    return signal.reindex(panel.index)


def evaluate_idea(
    signal_fn: SignalFunction,
    *,
    series: list[str],
    forward_prices: pd.DataFrame,
    start: str,
    end: str,
    catalog_path: str | Path = "config/data_catalog.yaml",
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    availability_policy: str = "all",
    quantiles: int = 5,
) -> IdeaResult:
    """Run one idea end to end and return its diagnostics.

    ``series`` is what the signal may look at — keep it to the variables the
    idea actually needs, so a result cannot silently depend on a column you did
    not intend to use.

    ``forward_prices`` is a panel of asset *levels*. Forward returns are derived
    here rather than passed in, because handing a price level to the correlation
    against a signal measures co-trending, not predictability: the resulting IC
    would be identical at every horizon and could not be read as a return at
    all.
    """

    panel = load_monthly_panel(
        catalog_path,
        series,
        start=start,
        end=end,
        availability_policy=availability_policy,
    )
    if panel.empty:
        raise IdeaError(
            f"The panel is empty for {series} between {start} and {end}. "
            "Check that the series exist in the catalogue and that their sources are reachable."
        )

    raw = signal_fn(panel)
    name = getattr(signal_fn, "__name__", "signal")
    signal = _validate_signal(raw, panel, name)

    if forward_prices.empty:
        raise IdeaError("forward_prices is empty — nothing to test the signal against")

    # A rate or yield cannot be turned into a return: p.shift(-h)/p - 1 diverges
    # as p approaches zero, which shows up as +inf and a meaningless IC. Catching
    # it here turns a silently absurd number into an actionable message.
    non_positive = [c for c in forward_prices.columns if (forward_prices[c] <= 0).any()]
    if non_positive:
        raise IdeaError(
            f"forward assets {non_positive} are not price series — they contain "
            "values at or below zero. Rates, yields and level indices have no "
            "meaningful percentage return. Use a tradable price (equity index, "
            "commodity, FX) as the target, or test the idea against a spread."
        )

    fwd = forward_returns(forward_prices, horizons=horizons)
    diagnostics = analyse_factor(signal, fwd, horizons=horizons, quantiles=quantiles)
    return IdeaResult(signal=signal, diagnostics=diagnostics, panel=panel)
