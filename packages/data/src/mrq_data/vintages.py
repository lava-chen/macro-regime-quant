"""Vintage archive: keep what each series looked like at a point in time.

A single ALFRED pull with today's vintage does not make a backtest
point-in-time. It relabels current knowledge as if it had always been
available, which is the same look-ahead as fetching the revised FRED series —
just wearing a better label. The archive is what makes the label true: for a
backtest month t, the correct input is the newest vintage dated at or before t.

So the workflow is to grow an archive. Each run captures one more slice of
history, the archive accumulates, and backtests drawn from it become honest to
the extent the archive goes back.

Layout::

    data/vintages/<symbol>/<YYYY-MM-DD>.csv

one file per series per vintage, holding observation_date, value and the
vintage that produced it. Missing vintages are not an error: the reader falls
back to the newest snapshot at or before the requested date, which is exactly
what a researcher would have had.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from mrq_core.types import SeriesSpec

from .providers.alfred import AlfredProvider

#: Where the archive lives, relative to the repository root.
VINTAGE_ROOT = Path("data/vintages")

#: Filename stem for the metadata sidecar written next to each snapshot.
MANIFEST_SUFFIX = "_manifest.csv"


@dataclass(frozen=True)
class VintageCoverage:
    """How far back a series' archive actually reaches."""

    symbol: str
    vintages: tuple[pd.Timestamp, ...]

    @property
    def earliest(self) -> pd.Timestamp | None:
        return min(self.vintages) if self.vintages else None

    @property
    def latest(self) -> pd.Timestamp | None:
        return max(self.vintages) if self.vintages else None

    def as_of(self, when: str | pd.Timestamp) -> pd.Timestamp | None:
        """Newest vintage at or before ``when`` — what was on disk that day."""

        cutoff = pd.Timestamp(when)
        usable = [v for v in self.vintages if v <= cutoff]
        return max(usable) if usable else None


def _safe_name(symbol: str) -> str:
    """Filesystem-safe stem for a FRED series id."""

    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in symbol)


def snapshot_series(
    spec: SeriesSpec,
    *,
    start: str,
    end: str | None,
    step_months: int = 3,
    root: Path = VINTAGE_ROOT,
    provider: AlfredProvider | None = None,
    progress: bool = False,
) -> dict[str, int]:
    """Capture one vintage of ``spec`` every ``step_months`` across a date range.

    The first requested date is always captured, then dates advance by
    ``step_months`` until ``end``. Existing snapshots are left alone, so this is
    safe to re-run and only fetches what is genuinely missing — which matters
    because each vintage is a network round trip.
    """

    if step_months < 1:
        raise ValueError("step_months must be at least 1")

    fetch = provider or AlfredProvider()
    series_dir = Path(root) / _safe_name(spec.symbol)
    series_dir.mkdir(parents=True, exist_ok=True)

    first = pd.Timestamp(start)
    last = pd.Timestamp(end) if end else pd.Timestamp.now().normalize()
    if first > last:
        raise ValueError(f"start {first.date()} is after end {last.date()}")

    captured = skipped = 0
    cursor = first
    while cursor <= last:
        target = series_dir / f"{cursor.date().isoformat()}.csv"
        if target.exists():
            skipped += 1
        else:
            frame = fetch.fetch(spec, start=first, end=last, vintage_date=cursor)
            payload = frame.loc[:, ["observation_date", "value", "vintage_date"]].copy()
            payload["value"] = pd.to_numeric(payload["value"], errors="coerce")
            payload = payload.dropna(subset=["value"])
            payload.to_csv(target, index=False)
            captured += 1
            if progress:
                print(f"    {spec.symbol} @ {cursor.date()}  {len(payload)} rows")
        cursor = cursor + pd.DateOffset(months=step_months)

    return {"captured": captured, "skipped": skipped}


def load_vintage(
    symbol: str,
    as_of: str | pd.Timestamp,
    *,
    root: Path = VINTAGE_ROOT,
) -> pd.DataFrame | None:
    """Read the newest snapshot of ``symbol`` that existed at ``as_of``.

    Returns None when the archive does not reach back that far, so a caller can
    decide whether to refuse the backtest or start later. Silently substituting
    today's data here would reintroduce exactly the look-ahead the archive
    exists to prevent.
    """

    series_dir = Path(root) / _safe_name(symbol)
    if not series_dir.is_dir():
        return None

    cutoff = pd.Timestamp(as_of).date().isoformat()
    candidates = sorted(
        p for p in series_dir.glob("*.csv") if not p.name.endswith(MANIFEST_SUFFIX)
    )
    usable = [p for p in candidates if p.stem <= cutoff]
    if not usable:
        return None

    frame = pd.read_csv(usable[-1])
    if "value" not in frame.columns:
        raise ValueError(f"{usable[-1]} has no 'value' column")
    frame["observation_date"] = pd.to_datetime(frame["observation_date"])
    if "vintage_date" in frame.columns:
        frame["vintage_date"] = pd.to_datetime(frame["vintage_date"])
    return frame


def coverage(symbol: str, *, root: Path = VINTAGE_ROOT) -> VintageCoverage:
    series_dir = Path(root) / _safe_name(symbol)
    vintages: list[pd.Timestamp] = []
    if series_dir.is_dir():
        vintages = [
            pd.Timestamp(p.stem)
            for p in series_dir.glob("*.csv")
            if not p.name.endswith(MANIFEST_SUFFIX)
        ]
    return VintageCoverage(symbol=symbol, vintages=tuple(sorted(vintages)))
