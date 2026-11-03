from __future__ import annotations

from dataclasses import dataclass
from math import log, sqrt

import numpy as np
import pandas as pd

from .models import SpreadSpec

#: Approximate Dickey-Fuller critical values (MacKinnon response surface, no trend).
#: They are large-sample constants: a statistic below the 5% line rejects the
#: unit-root hypothesis at the 5% level for a constant-only specification.
ADF_CRITICAL_VALUES_CONSTANT: dict[str, float] = {
    "1%": -3.43,
    "5%": -2.86,
    "10%": -2.57,
}

#: The same for a specification without a constant, used for Engle-Granger
#: residuals, which already have zero mean by construction.
ADF_CRITICAL_VALUES_NO_CONSTANT: dict[str, float] = {
    "1%": -3.75,
    "5%": -3.03,
    "10%": -2.63,
}


@dataclass(frozen=True)
class AdfResult:
    """Augmented Dickey-Fuller test on a single series."""

    statistic: float
    lag: int
    observations: int
    critical_values: dict[str, float]
    rejects_unit_root_5pct: bool

    def verdict(self) -> str:
        if not np.isfinite(self.statistic):
            return "insufficient data"
        if self.rejects_unit_root_5pct:
            return "stationary (unit root rejected at 5%)"
        return "non-stationary (unit root not rejected at 5%)"


@dataclass(frozen=True)
class CointegrationResult:
    """Engle-Granger evidence for a long-run relationship between two series."""

    statistic: float
    critical_values: dict[str, float]
    rejects_unit_root_5pct: bool
    half_life: float
    hedge_ratio: float
    intercept: float
    observations: int
    return_correlation: float

    def verdict(self) -> str:
        if not np.isfinite(self.statistic):
            return "insufficient data"
        if self.rejects_unit_root_5pct:
            return "cointegrated at 5%"
        return "no cointegration evidence at 5%"


def align_pair_prices(prices: pd.DataFrame) -> pd.DataFrame:
    """Return the two-leg panel on dates where both legs have a quote.

    Missing observations are dropped, never filled: a filled price would
    silently move a multi-session return onto one day and change the very
    spread the strategy trades.
    """

    if prices.shape[1] != 2:
        raise ValueError(f"A pair panel must have exactly two columns, got {list(prices.columns)}")
    if prices.index.has_duplicates:
        raise ValueError("Pair price dates must be unique")
    aligned = prices.sort_index().astype(float)
    if np.isinf(aligned.to_numpy()).all() or np.isinf(aligned.to_numpy()).any():
        # A missing observation is a holiday artefact and is dropped below; an
        # infinite one is corrupt data and must never be traded through.
        raise ValueError("Pair prices contain infinite values")
    aligned = aligned.dropna(how="any")
    if aligned.empty:
        raise ValueError("The two assets have no common trading dates")
    if (aligned <= 0).any().any():
        raise ValueError("Pair prices must be positive; a log-price spread needs positive levels")
    return aligned


def rolling_hedge_ratio(
    y: pd.Series,
    x: pd.Series,
    *,
    lookback: int,
    min_periods: int | None = None,
) -> pd.DataFrame:
    """Estimate beta and alpha of ``y = alpha + beta * x`` on trailing windows.

    The window for date ``t`` ends at ``t - 1``: the ratio used to build the
    spread at ``t`` must be known before ``t``, otherwise the spread is built
    with information from its own future.
    """

    if len(y) != len(x) or not y.index.equals(x.index):
        raise ValueError("y and x must share the same index")
    if lookback < 2:
        raise ValueError("lookback must be at least 2 periods")
    minp = min_periods if min_periods is not None else lookback

    mean_x = x.rolling(lookback, min_periods=minp).mean().shift(1)
    mean_y = y.rolling(lookback, min_periods=minp).mean().shift(1)
    mean_xy = (x * y).rolling(lookback, min_periods=minp).mean().shift(1)
    mean_xx = (x * x).rolling(lookback, min_periods=minp).mean().shift(1)

    var_x = mean_xx - mean_x**2
    cov_xy = mean_xy - mean_x * mean_y
    beta = cov_xy / var_x.where(var_x > 0)
    alpha = mean_y - beta * mean_x
    return pd.DataFrame({"beta": beta, "alpha": alpha})


def build_spread(prices: pd.DataFrame, spec: SpreadSpec) -> pd.DataFrame:
    """Build the pair's spread, hedge ratio and intercept on a shared index.

    ``price_kind='log'`` gives ``log(P_a) - beta * log(P_b) - alpha``; the
    level variant keeps the raw price difference. The log form is the one
    that matches a stationary relation for price series, and it keeps the
    hedge ratio interpretable as a return elasticity.
    """

    aligned = align_pair_prices(prices)
    y = aligned.iloc[:, 0]
    x = aligned.iloc[:, 1]
    if spec.price_kind == "log":
        y = np.log(y)
        x = np.log(x)

    if spec.beta_method == "fixed":
        beta = pd.Series(spec.fixed_beta, index=aligned.index, dtype=float)
        alpha = pd.Series(0.0, index=aligned.index, dtype=float)
        if spec.detrend:
            # The intercept of a fixed-beta spread is still an estimate, so it
            # uses the same trailing window and the same shift.
            raw = y - beta * x
            alpha = raw.rolling(spec.lookback, min_periods=spec.effective_min_periods).mean().shift(1)
    else:
        estimates = rolling_hedge_ratio(
            y,
            x,
            lookback=spec.lookback,
            min_periods=spec.effective_min_periods,
        )
        beta = estimates["beta"]
        alpha = estimates["alpha"] if spec.detrend else pd.Series(0.0, index=aligned.index, dtype=float)

    spread = y - beta * x - alpha
    return pd.DataFrame({"beta": beta, "alpha": alpha, "spread": spread})


def rolling_zscore(
    series: pd.Series,
    window: int,
    min_periods: int | None = None,
) -> pd.Series:
    """Trailing z-score of a series.

    The window ends on the observation date, so the value at ``t`` uses only
    history up to ``t``. Fills are not allowed: a zero-variance window would
    produce an infinite z-score that no threshold can contain.
    """

    if window < 2:
        raise ValueError("window must be at least 2 periods")
    minp = min_periods if min_periods is not None else window
    mean = series.rolling(window, min_periods=minp).mean()
    std = series.rolling(window, min_periods=minp).std(ddof=1)
    z = (series - mean) / std.where(std > 0)
    return z.replace([np.inf, -np.inf], np.nan)


def _ols_t_statistic(design: np.ndarray, target: np.ndarray, coefficient: int) -> tuple[float, int]:
    """OLS coefficient and its t-statistic for one column of ``design``."""

    observations, parameters = design.shape
    if observations <= parameters:
        return float("nan"), 0
    xtx_inv = np.linalg.pinv(design.T @ design)
    coefficients = xtx_inv @ design.T @ target
    residuals = target - design @ coefficients
    sigma2 = float(residuals @ residuals) / (observations - parameters)
    variance = sigma2 * xtx_inv[coefficient, coefficient]
    if not np.isfinite(variance) or variance <= 0:
        return float("nan"), observations
    return float(coefficients[coefficient] / sqrt(variance)), observations


def adf_test(
    series: pd.Series,
    *,
    max_lag: int = 1,
    constant: bool = True,
) -> AdfResult:
    """Augmented Dickey-Fuller test for a unit root.

    The specification is ``dS_t = alpha + gamma * S_{t-1} + sum(c_i * dS_{t-i}) + e_t``.
    A significantly negative ``gamma`` means the series has no unit root.

    The test is deliberately simple: a fixed lag order, ordinary least squares
    standard errors and large-sample critical values. It is evidence for or
    against a stationary relation, not a licence to skip the out-of-sample
    check that follows it.
    """

    if max_lag < 0:
        raise ValueError("max_lag must be non-negative")
    values = series.dropna().astype(float)
    if len(values) < 10 + max_lag:
        return AdfResult(
            statistic=float("nan"),
            lag=max_lag,
            observations=len(values),
            critical_values=ADF_CRITICAL_VALUES_CONSTANT if constant else ADF_CRITICAL_VALUES_NO_CONSTANT,
            rejects_unit_root_5pct=False,
        )

    levels = values.to_numpy(dtype=float)
    differences = np.diff(levels)
    lagged_level = levels[:-1]
    rows = []
    for position in range(len(differences)):
        row = [lagged_level[position]]
        for lag in range(1, max_lag + 1):
            if position - lag < 0:
                row.append(np.nan)
            else:
                row.append(differences[position - lag])
        rows.append(row)
    design = np.asarray(rows, dtype=float)
    if constant:
        design = np.column_stack([np.ones(len(design)), design])
    target = differences

    keep = np.isfinite(design).all(axis=1)
    design = design[keep]
    target = target[keep]
    if len(target) <= design.shape[1]:
        return AdfResult(
            statistic=float("nan"),
            lag=max_lag,
            observations=len(target),
            critical_values=ADF_CRITICAL_VALUES_CONSTANT if constant else ADF_CRITICAL_VALUES_NO_CONSTANT,
            rejects_unit_root_5pct=False,
        )

    coefficient_index = 1 if constant else 0
    statistic, observations = _ols_t_statistic(design, target, coefficient_index)
    critical = ADF_CRITICAL_VALUES_CONSTANT if constant else ADF_CRITICAL_VALUES_NO_CONSTANT
    return AdfResult(
        statistic=statistic,
        lag=max_lag,
        observations=observations,
        critical_values=critical,
        rejects_unit_root_5pct=bool(np.isfinite(statistic) and statistic < critical["5%"]),
    )


def half_life(spread: pd.Series) -> float:
    """Half-life of a spread, in periods, from the standard pairs-trading fit.

    The fit is ``dS_t = a + b * S_{t-1} + e_t``; ``b`` is the speed at which a
    deviation is pulled back, and the half-life is ``-ln(2) / b``. A positive
    or undefined ``b`` means the spread drifts instead of reverting, and the
    caller must not treat a z-score as a reversion signal.
    """

    values = spread.dropna().astype(float)
    if len(values) < 30:
        return float("nan")
    lagged = values.shift(1)
    frame = pd.concat([values.diff(), lagged], axis=1).dropna()
    if len(frame) < 20:
        return float("nan")
    design = np.column_stack([np.ones(len(frame)), frame.iloc[:, 1].to_numpy(dtype=float)])
    target = frame.iloc[:, 0].to_numpy(dtype=float)
    coefficients, *_ = np.linalg.lstsq(design, target, rcond=None)
    phi = float(coefficients[1])
    if not np.isfinite(phi) or phi >= 0:
        return float("nan")
    return float(-log(2) / phi)


def cointegration_test(y: pd.Series, x: pd.Series) -> CointegrationResult:
    """Engle-Granger test for a cointegrating relation between ``y`` and ``x``.

    The residuals of ``y = alpha + beta * x`` must be stationary for the two
    series to be cointegrated. The residual test runs without a constant,
    because the residuals already have zero mean.
    """

    frame = pd.concat([y.rename("y"), x.rename("x")], axis=1).dropna()
    if len(frame) < 30:
        return CointegrationResult(
            statistic=float("nan"),
            critical_values=ADF_CRITICAL_VALUES_NO_CONSTANT,
            rejects_unit_root_5pct=False,
            half_life=float("nan"),
            hedge_ratio=float("nan"),
            intercept=float("nan"),
            observations=len(frame),
            return_correlation=float("nan"),
        )

    design = np.column_stack([np.ones(len(frame)), frame["x"].to_numpy(dtype=float)])
    target = frame["y"].to_numpy(dtype=float)
    coefficients, *_ = np.linalg.lstsq(design, target, rcond=None)
    intercept, beta = float(coefficients[0]), float(coefficients[1])
    residuals = pd.Series(target - design @ coefficients, index=frame.index)

    adf = adf_test(residuals, max_lag=1, constant=False)
    returns = frame.pct_change(fill_method=None).dropna()
    correlation = float(returns["y"].corr(returns["x"])) if len(returns) > 2 else float("nan")

    return CointegrationResult(
        statistic=adf.statistic,
        critical_values=adf.critical_values,
        rejects_unit_root_5pct=adf.rejects_unit_root_5pct,
        half_life=half_life(residuals),
        hedge_ratio=beta,
        intercept=intercept,
        observations=len(frame),
        return_correlation=correlation,
    )
