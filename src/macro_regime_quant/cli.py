from __future__ import annotations

import argparse
from pathlib import Path

from .pipeline import build_us_baseline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="macro-regime-quant")
    sub = parser.add_subparsers(dest="command", required=True)

    us = sub.add_parser("build-us-baseline", help="Build monthly US factors and regimes")
    us.add_argument("--start", default="2000-01-01")
    us.add_argument("--end", default=None)
    us.add_argument("--output", default="data/processed/us")
    us.add_argument("--min-z-history", type=int, default=36)

    return parser


def main() -> None:
    args = build_parser().parse_args()

    if args.command == "build-us-baseline":
        raw, factors, regimes = build_us_baseline(
            start=args.start,
            end=args.end,
            min_z_history=args.min_z_history,
        )
        out = Path(args.output)
        out.mkdir(parents=True, exist_ok=True)
        raw.to_csv(out / "raw_monthly.csv")
        factors.to_csv(out / "factors.csv")
        regimes.to_frame().to_csv(out / "regimes.csv")
        print(f"Wrote US baseline to {out}")


if __name__ == "__main__":
    main()
