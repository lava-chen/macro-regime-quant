"""Snapshot adjusted daily prices into the local market store.

The backtest workbench reads a symbol from ``data/raw/market/<SYMBOL>.csv``
when that file exists and only falls back to a live provider call otherwise.
Snapshotting once means a backtest is reproducible, runs offline, and does
not depend on a provider that rate-limits shared addresses.

The written file is what the loader expects: a date column, an
``adjusted_close`` column, and a sidecar ``.meta.json`` recording where the
data came from and when it was fetched.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from mrq_core.types import SeriesSpec
from mrq_data.providers.yahoo import YahooProvider


def snapshot_symbol(
    symbol: str,
    *,
    start: str | None,
    end: str | None,
    output_dir: Path,
) -> Path:
    spec = SeriesSpec(
        key=symbol.upper(),
        provider="yahoo",
        symbol=symbol.upper(),
        kind="market",
        frequency="daily",
    )
    frame = YahooProvider().fetch(spec, start=start, end=end)
    if frame.empty:
        raise RuntimeError(f"{symbol}: no observations returned")

    frame = frame.sort_values("observation_date")
    frame = frame.rename(columns={"observation_date": "date", "value": "adjusted_close"})
    frame["date"] = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d")

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{symbol.upper()}.csv"
    frame[["date", "adjusted_close"]].to_csv(path, index=False)

    metadata = {
        "source_url": f"https://finance.yahoo.com/quote/{symbol.upper()}/history/",
        "retrieved_at": datetime.now(UTC).isoformat(),
        "origin_provider": "yahoo_finance",
        "price_basis": "Yahoo auto_adjust=True adjusted close",
        "first_date": str(frame["date"].min()),
        "last_date": str(frame["date"].max()),
        "observations": len(frame),
    }
    path.with_suffix(path.suffix + ".meta.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbols", nargs="+", help="Ticker symbols, e.g. KO PEP")
    parser.add_argument("--start", default="2005-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--output", default="data/raw/market")
    args = parser.parse_args()

    output_dir = Path(args.output)
    failures = 0
    for symbol in args.symbols:
        try:
            path = snapshot_symbol(
                symbol, start=args.start, end=args.end, output_dir=output_dir
            )
        except (RuntimeError, ValueError, OSError) as exc:  # one bad symbol must not stop the rest
            failures += 1
            print(f"{symbol}: FAILED ({type(exc).__name__}: {exc})")
            continue
        print(f"{symbol}: wrote {path}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
