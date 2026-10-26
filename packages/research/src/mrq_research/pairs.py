"""Pair-trading research: is the spread tradeable, and does it make money?

Two levels of evidence live here, and they answer different questions.

``analyse_pair`` asks whether the two assets share a stationary long-run
relation at all. A pair that is not cointegrated has no spread to trade, and
a backtest on it is a coincidence dressed up as a strategy.

``backtest_pair`` asks the harder question: after the cost of trading two
legs, with borrow fees and a one-day execution lag, does the mean reversion
survive? The answer is only meaningful out of sample, so the diagnostics are
reported next to the backtest rather than used to pick its parameters.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from mrq_engines.pairs import (
    PairSpec,
    align_pair_prices,
    build_spread,
    cointegration_test,
    rolling_zscore,
    zscore_state_machine,
)

from .backtest.long_short import LongShortConfig, LongShortResult, run_long_short_backtest
from .backtest.metrics import summary


@dataclass(frozen=True)
class PairDiagnostics:
    """Evidence about the pair's long-run relation, before any trading."""

    observations: int
    start: pd.Timestamp
    end: pd.Timestamp
    hedge_ratio_mean: float
    hedge_ratio_std: float
    hedge_ratio_last: float
    hedge_ratio_non_positive_share: float
    cointegration_statistic: float
    cointegration_critical_values: dict[str, float]
    cointegration_verdict: str
    spread_half_life: float
    return_correlation: float
    reversion: pd.DataFrame
    ratio_path: pd.DataFrame
    warnings: tuple[str, ...]

    @property
    def cointegrated(self) -> bool:
        return self.cointegration_verdict.startswith("cointegrated")

    def to_dict(self) -> dict[str, object]:
        return {
            "observations": self.observations,
            "start": self.start.date().isoformat(),
            "end": self.end.date().isoformat(),
            "hedge_ratio_mean": self.hedge_ratio_mean,
            "hedge_ratio_std": self.hedge_ratio_std,
            "hedge_ratio_last": self.hedge_ratio_last,
            "hedge_ratio_non_positive_share": self.hedge_ratio_non_positive_share,
            "cointegration_statistic": self.cointegration_statistic,
            "cointegration_verdict": self.cointegration_verdict,
            "spread_half_life": self.spread_half_life,
            "return_correlation": self.return_correlation,
            "reversion": self.reversion.to_dict(orient="records"),
            "ratio_path": self.ratio_path.to_dict(orient="records"),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class PairBacktest:
    """The full result of trading one pair."""

    spec: PairSpec
    diagnostics: PairDiagnostics
    result: LongShortResult
    instructions: pd.DataFrame
    frame: pd.DataFrame

    def to_dict(self) -> dict[str, object]:
        return {
            "pair": self.spec.name,
            "asset_a": self.spec.asset_a,
            "asset_b": self.spec.asset_b,
            "spec": {
                "lookback": self.spec.spread.lookback,
                "price_kind": self.spec.spread.price_kind,
                "beta_method": self.spec.spread.beta_method,
                "hedge_mode": self.spec.hedge_mode,
                "entry_z": self.spec.signal.entry_z,
                "exit_z": self.spec.signal.exit_z,
                "stop_z": self.spec.signal.stop_z,
                "max_holding_days": self.spec.signal.max_holding_days,
            },
            "diagnostics": self.diagnostics.to_dict(),
            "metrics": self.result.metrics,
        }


def _price_frame(prices: pd.DataFrame, spec: PairSpec) -> pd.DataFrame:
    aligned = align_pair_prices(prices)
    return aligned.rename(columns={aligned.columns[0]: spec.asset_a, aligned.columns[1]: spec.asset_b})


def analyse_pair(prices: pd.DataFrame, spec: PairSpec) -> PairDiagnostics:
    """Assess whether the pair has a stationary long-run relationship."""

    aligned = _price_frame(prices, spec)
    warnings: list[str] = []

    y = aligned[spec.asset_a]
    x = aligned[spec.asset_b]
    if spec.spread.price_kind == "log":
        y = np.log(y)
        x = np.log(x)

    cointegration = cointegration_test(y, x)
    spread_frame = build_spread(aligned[[spec.asset_a, spec.asset_b]], spec.spread)
    spread = spread_frame["spread"]
    z = rolling_zscore(spread, spec.spread.lookback, spec.spread.effective_min_periods)
    reversion = _reversion_table(z, spread, horizons=(5, 10, 20))
    ratio_path = _ratio_path(aligned, spec)

    beta = spread_frame["beta"].dropna()
    if beta.empty:
        raise ValueError("Not enough history to estimate a hedge ratio for this pair")

    non_positive_share = float((beta <= 0).mean())
    if not cointegration.rejects_unit_root_5pct:
        warnings.append(
            "No cointegration evidence at 5%: the spread may not be mean-reverting, "
            "so any backtest result should be treated as noise."
        )
    if not np.isfinite(cointegration.half_life) or cointegration.half_life <= 0:
        warnings.append(
            "Spread half-life is not finite: the spread drifts instead of reverting."
        )
    if abs(cointegration.return_correlation) < 0.5:
        warnings.append(
            f"Daily return correlation is only {cointegration.return_correlation:.2f}; "
            "the legs do not move together closely enough for a reliable hedge."
        )
    if non_positive_share > 0.1:
        warnings.append(
            f"The trailing hedge ratio is non-positive on {non_positive_share:.0%} of the "
            "sample; the window is too short for a stable estimate. Lengthen the "
            "lookback before trusting the sizing."
        )
    if len(beta) > 1 and float(beta.std(ddof=1)) > 0.3:
        warnings.append(
            f"The hedge ratio swings by {float(beta.std(ddof=1)):.2f} (standard deviation); "
            "the position is being resized by a moving target."
        )

    return PairDiagnostics(
        observations=len(aligned),
        start=aligned.index.min(),
        end=aligned.index.max(),
        hedge_ratio_mean=float(beta.mean()),
        hedge_ratio_std=float(beta.std(ddof=1)) if len(beta) > 1 else float("nan"),
        hedge_ratio_last=float(beta.iloc[-1]),
        hedge_ratio_non_positive_share=non_positive_share,
        cointegration_statistic=cointegration.statistic,
        cointegration_critical_values=cointegration.critical_values,
        cointegration_verdict=cointegration.verdict(),
        spread_half_life=float(cointegration.half_life),
        return_correlation=cointegration.return_correlation,
        reversion=reversion,
        ratio_path=ratio_path,
        warnings=tuple(warnings),
    )


def _ratio_path(aligned: pd.DataFrame, spec: PairSpec) -> pd.DataFrame:
    """Year-end price ratio, the most direct look at whether the pair drifts.

    A ratio that walks steadily in one direction is a trend, not a spread, and
    no z-score on top of it is a mean-reverting trade.
    """

    ratio = aligned[spec.asset_a] / aligned[spec.asset_b]
    yearly = ratio.resample("YE").last().dropna()
    return pd.DataFrame({"year": yearly.index.year, "price_ratio": yearly.to_numpy()})


def _reversion_table(
    z: pd.Series,
    spread: pd.Series,
    horizons: tuple[int, ...],
) -> pd.DataFrame:
    """Forward behaviour of the spread, by how stretched it was today.

    The spread moves is reported in units of its own standard deviation,
    because a change in the z-score partly comes from the rolling mean and
    standard deviation moving as well — that part is a property of the
    normalisation, not of the pair.
    """

    buckets = [
        ("z <= -2", z <= -2.0),
        ("-2 < z <= -1", (z > -2.0) & (z <= -1.0)),
        ("-1 < z < 1", (z > -1.0) & (z < 1.0)),
        ("1 <= z < 2", (z >= 1.0) & (z < 2.0)),
        ("z >= 2", z >= 2.0),
    ]
    scale = float(spread.std(ddof=1)) or float("nan")
    rows: list[dict[str, object]] = []
    for label, mask in buckets:
        selected = z[mask].dropna()
        row: dict[str, object] = {"bucket": label, "count": int(selected.size)}
        for horizon in horizons:
            forward_spread = spread.shift(-horizon) - spread
            sample = pd.concat(
                [selected.rename("z"), forward_spread.loc[selected.index].rename("forward")],
                axis=1,
            ).dropna()
            forward_z = z.shift(-horizon)
            z_sample = pd.concat([selected.rename("z"), forward_z.loc[selected.index].rename("fz")], axis=1).dropna()
            row[f"mean_spread_change_{horizon}d"] = (
                float(sample["forward"].mean() / scale) if len(sample) else float("nan")
            )
            row[f"closer_to_mean_{horizon}d"] = (
                float((z_sample["fz"].abs() < z_sample["z"].abs()).mean()) if len(z_sample) else float("nan")
            )
        rows.append(row)
    return pd.DataFrame(rows)


def build_pair_instructions(
    prices: pd.DataFrame,
    spec: PairSpec,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Turn the pair's z-score into sparse two-leg target-weight instructions.

    An instruction is issued only when the position changes: on entry the two
    legs are sized from the hedge ratio known on that date, on exit both legs
    go flat. Between instructions the legs drift with prices, which is what
    actually happens to a pair position that is not re-hedged every day.
    """

    aligned = _price_frame(prices, spec)
    spread_frame = build_spread(aligned[[spec.asset_a, spec.asset_b]], spec.spread)
    z = rolling_zscore(spread_frame["spread"], spec.spread.lookback, spec.spread.effective_min_periods)
    states = zscore_state_machine(z, spec.signal)

    rows: dict[pd.Timestamp, dict[str, float]] = {}
    warnings: list[str] = []
    for date in aligned.index:
        if not states.at[date, "entry"]:
            continue
        position = float(states.at[date, "position"])
        beta = float(spread_frame.at[date, "beta"])
        if not np.isfinite(beta):
            warnings.append(f"{date.date()}: hedge ratio unavailable, entry skipped")
            continue
        if spec.hedge_mode == "dollar_neutral" or beta <= 0:
            if spec.hedge_mode != "dollar_neutral" and beta <= 0:
                warnings.append(
                    f"{date.date()}: hedge ratio {beta:.3f} is not positive, "
                    "the pair was traded dollar-neutral instead"
                )
            weight_a = position / 2.0
            weight_b = -position / 2.0
        else:
            weight_a = position / (1.0 + beta)
            weight_b = -position * beta / (1.0 + beta)
        rows[date] = {spec.asset_a: weight_a, spec.asset_b: weight_b}

    for date in aligned.index:
        if states.at[date, "exit"]:
            rows[date] = {spec.asset_a: 0.0, spec.asset_b: 0.0}

    instructions = pd.DataFrame.from_dict(rows, orient="index")
    instructions = instructions.reindex(columns=[spec.asset_a, spec.asset_b]).sort_index()
    instructions.index.name = aligned.index.name

    frame = pd.concat(
        [
            aligned,
            spread_frame[["beta", "spread"]],
            z.rename("zscore"),
            states[["position", "entry", "exit", "holding_days"]],
        ],
        axis=1,
    )
    return instructions, frame


def combine_pair_returns(
    returns: dict[str, pd.Series],
    periods_per_year: int = 252,
    positions: dict[str, pd.Series] | None = None,
) -> tuple[pd.Series, dict[str, float]]:
    """Combine several pairs into one equally weighted book.

    Each pair is dollar neutral on its own, so an equal weight across pairs is
    an equal weight across capital rather than across risk. Days on which a
    pair has no position contribute nothing rather than shrinking the whole
    book.

    ``positions`` is the per-pair position series. When it is given, the
    report says how many pairs were actually holding something — counting
    pairs that merely have a return value would report every pair as active
    on every day, because a flat pair's return is a real 0.0 and not a gap.
    """

    if not returns:
        raise ValueError("At least one pair return series is required")
    frame = pd.DataFrame(returns).sort_index()
    portfolio = frame.mean(axis=1)
    metrics = summary(portfolio, periods_per_year)

    if positions:
        position_frame = pd.DataFrame(positions).reindex(frame.index).fillna(0.0)
        held = (position_frame.abs() > 0).sum(axis=1)
        metrics["average_holding_pairs"] = float(held.mean())
        metrics["minimum_holding_pairs"] = float(held.min())
        metrics["maximum_holding_pairs"] = float(held.max())
    else:
        metrics["average_holding_pairs"] = float("nan")
        metrics["minimum_holding_pairs"] = float("nan")
        metrics["maximum_holding_pairs"] = float("nan")
    metrics["pairs_in_book"] = float(frame.shape[1])
    return portfolio.rename("portfolio_return"), metrics


def backtest_pair(
    prices: pd.DataFrame,
    spec: PairSpec,
    config: LongShortConfig | None = None,
) -> PairBacktest:
    """Run the pair's z-score strategy over an aligned price panel."""

    config = config or LongShortConfig()
    instructions, frame = build_pair_instructions(prices, spec)
    aligned = _price_frame(prices, spec)

    if instructions.empty:
        result = run_long_short_backtest(
            aligned[[spec.asset_a, spec.asset_b]],
            pd.DataFrame(
                {spec.asset_a: [0.0], spec.asset_b: [0.0]},
                index=[aligned.index[0]],
            ),
            config,
        )
    else:
        result = run_long_short_backtest(
            aligned[[spec.asset_a, spec.asset_b]], instructions, config
        )

    diagnostics = analyse_pair(prices, spec)
    frame = frame.copy()
    frame["equity"] = (1.0 + result.returns).cumprod()
    frame["strategy_return"] = result.returns
    frame["turnover"] = result.turnover

    return PairBacktest(
        spec=spec,
        diagnostics=diagnostics,
        result=result,
        instructions=instructions,
        frame=frame,
    )
