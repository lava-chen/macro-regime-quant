"""Single-factor diagnostics: does this idea predict anything at all?

This is the gate a new idea passes before anyone spends time on position sizing
and costs. A signal with no information content is not rescued by a clever
execution rule, so "is there a relationship" is answered first and cheaply.

Every statistic here is computed on point-in-time data. The panel handed to
your function is already aligned to what was knowable at each month-end, and
forward returns are computed strictly *after* the signal period, so an
apparently strong result cannot come from the test peeking at the future.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

#: Forward horizons in months, matching the monthly model frequency.
DEFAULT_HORIZONS: tuple[int, ...] = (1, 3, 6, 12)


@dataclass(frozen=True)
class FactorDiagnostics:
    """Everything worth knowing about one signal, across horizons."""

    signal: pd.Series
    horizons: tuple[int, ...]
    ic_by_horizon: dict[int, float]
    rank_ic_by_horizon: dict[int, float]
    ic_ir_by_horizon: dict[int, float]
    n_obs_by_horizon: dict[int, int]
    quantile_returns: pd.DataFrame
    hit_rate_by_horizon: dict[int, float]
    warnings: list[str] = field(default_factory=list)

    @property
    def best_horizon(self) -> int | None:
        ranked = sorted(
            (h for h, v in self.ic_by_horizon.items() if pd.notna(v)),
            key=lambda h: abs(self.ic_by_horizon[h]),
            reverse=True,
        )
        return ranked[0] if ranked else None

    def verdict(self) -> str:
        """Plain-language read, with the caveats attached.

        Deliberately conservative: a single in-sample |IC| below 0.05 is called
        out as noise rather than dressed up as a finding.
        """

        if not self.ic_by_horizon:
            return "no overlapping data — the signal never lined up with a forward return"
        best = self.best_horizon
        if best is None:
            return "no usable observations at any horizon"
        ic = self.ic_by_horizon[best]
        ir = self.ic_ir_by_horizon.get(best, float("nan"))
        n = self.n_obs_by_horizon.get(best, 0)
        strength = (
            "negligible" if abs(ic) < 0.02
            else "weak" if abs(ic) < 0.05
            else "moderate" if abs(ic) < 0.10
            else "strong"
        )
        direction = "positive" if ic > 0 else "negative"
        return (
            f"best horizon {best}m: IC {ic:+.3f} ({strength}, {direction}), "
            f"IC/IR {ir:+.2f}, n={n}"
        )


def _safe_corr(x: pd.Series, y: pd.Series, *, rank: bool = False) -> tuple[float, int]:
    if rank:
        x = x.rank()
        y = y.rank()
    paired = pd.concat([x, y], axis=1).dropna()
    if len(paired) < 3 or paired.iloc[:, 0].std() == 0 or paired.iloc[:, 1].std() == 0:
        return float("nan"), len(paired)
    return float(paired.corr().iloc[0, 1]), len(paired)


def quantile_returns(
    signal: pd.Series,
    forward: pd.Series,
    quantiles: int = 5,
) -> pd.DataFrame:
    """Mean forward return per signal bucket, plus the long-short spread.

    Monotonicity across buckets matters more than any single level: a signal
    that ranks well but has no ordering between buckets is not usable.
    """

    frame = pd.concat([signal.rename("signal"), forward.rename("forward")], axis=1).dropna()
    if frame.empty or frame["signal"].nunique() < 2:
        return pd.DataFrame()

    try:
        frame["bucket"] = pd.qcut(frame["signal"], quantiles, labels=False, duplicates="drop")
    except ValueError:
        return pd.DataFrame()

    grouped = frame.groupby("bucket", observed=True)["forward"]
    out = grouped.agg(["count", "mean", "std"]).reset_index()
    out.columns = ["bucket", "count", "mean_return", "std_return"]

    if len(out) >= 2:
        top = out.loc[out["bucket"].idxmax(), "mean_return"]
        bottom = out.loc[out["bucket"].idxmin(), "mean_return"]
        out.attrs["long_short_spread"] = float(top - bottom)
    else:
        out.attrs["long_short_spread"] = float("nan")
    return out


def analyse_factor(
    signal: pd.Series,
    forward_returns: pd.DataFrame,
    horizons: tuple[int, ...] | list[int] = DEFAULT_HORIZONS,
    quantiles: int = 5,
) -> FactorDiagnostics:
    """Diagnose one signal against forward returns.

    ``forward_returns`` may be a DataFrame of asset returns, or a mapping of
    ``{asset: Series}``; when several assets are supplied their mean forward
    return is used as the target.
    """

    horizons = tuple(int(h) for h in horizons)
    if any(h <= 0 for h in horizons):
        raise ValueError("Forward horizons must be positive months")
    if forward_returns.empty:
        raise ValueError("forward_returns is empty — cannot test a signal against nothing")

    signal = signal.astype(float).sort_index()

    def target_for(horizon: int) -> pd.Series:
        """Cross-sectional mean of the assets available at this horizon.

        forward_returns() returns a MultiIndex of (asset, horizon). Averaging
        every column into one series would give the same answer at every
        horizon, so each horizon has to be selected before it is collapsed.
        """

        frame = forward_returns
        if not isinstance(frame, pd.DataFrame):
            frame = pd.concat(frame, axis=1)

        if isinstance(frame.columns, pd.MultiIndex):
            level = frame.columns.names.index("horizon_months") if "horizon_months" in (
                frame.columns.names or []
            ) else frame.columns.nlevels - 1
            picked = [c for c in frame.columns if int(c[level]) == horizon]
            if not picked:
                return pd.Series(dtype=float, index=frame.index)
            frame = frame[picked]
        return frame.mean(axis=1)

    ic: dict[int, float] = {}
    rank_ic: dict[int, float] = {}
    ir: dict[int, float] = {}
    n_obs: dict[int, int] = {}
    hit: dict[int, float] = {}

    for horizon in horizons:
        target = target_for(horizon).reindex(signal.index).sort_index()
        value, count = _safe_corr(signal, target)
        ic[horizon] = value
        n_obs[horizon] = count

        rank_value, _ = _safe_corr(signal, target, rank=True)
        rank_ic[horizon] = rank_value

        # IC information ratio: mean IC / std of IC. A single IC has no IR, so
        # this stays NaN rather than reporting a fabricated stability figure.
        rolling = (
            pd.concat([signal.rename("s"), target.rename("y")], axis=1)
            .dropna()
            .assign(ic=lambda d: d["s"].rolling(12).corr(d["y"]))
            .dropna(subset=["ic"])
        )
        if len(rolling) >= 24 and rolling["ic"].std() > 0:
            ir[horizon] = float(rolling["ic"].mean() / rolling["ic"].std() * np.sqrt(12))
        else:
            ir[horizon] = float("nan")

        paired = pd.concat([signal.rename("s"), target.rename("y")], axis=1).dropna()
        if len(paired) >= 2 and not pd.isna(value):
            hit[horizon] = float(((paired["s"] > paired["s"].median()) == (paired["y"] > 0)).mean())
        else:
            hit[horizon] = float("nan")

    warnings: list[str] = []
    primary = max(horizons, key=lambda h: n_obs.get(h, 0))
    if n_obs.get(primary, 0) < 60:
        warnings.append(
            f"only {n_obs.get(primary, 0)} overlapping observations at {primary}m — "
            "treat any IC as noise until the sample grows"
        )
    if all(pd.isna(v) for v in ic.values()):
        warnings.append("signal is constant or has no overlap — nothing to measure")
    usable = [h for h in horizons if n_obs.get(h, 0) >= 60 and ic.get(h) == ic.get(h)]
    if len(usable) > 1 and max(abs(ic[h]) for h in usable) / max(
        min(abs(ic[h]) for h in usable), 1e-9
    ) > 3:
        warnings.append(
            "IC varies by more than 3x across horizons — a single-horizon result is "
            "likely a coincidence, not a stable relationship"
        )

    return FactorDiagnostics(
        signal=signal,
        horizons=horizons,
        ic_by_horizon=ic,
        rank_ic_by_horizon=rank_ic,
        ic_ir_by_horizon=ir,
        n_obs_by_horizon=n_obs,
        quantile_returns=quantile_returns(signal, target, quantiles=quantiles),
        hit_rate_by_horizon=hit,
        warnings=warnings,
    )
