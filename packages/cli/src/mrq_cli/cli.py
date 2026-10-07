from __future__ import annotations

import argparse
from pathlib import Path

import yaml
from mrq_data.snapshots import validate_snapshot
from mrq_engines.pipeline import build_china_baseline, build_us_baseline
from mrq_research.us_regime import analyze_us_regimes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="macro-regime-quant")
    sub = parser.add_subparsers(dest="command", required=True)

    us = sub.add_parser("build-us-baseline", help="Build monthly US factors and regimes")
    us.add_argument("--start", default="2000-01-01")
    us.add_argument("--end", default=None)
    us.add_argument("--output", default="data/processed/us")
    us.add_argument("--min-z-history", type=int, default=36)

    china = sub.add_parser(
        "build-china-baseline",
        help="Build monthly China factors from frozen official snapshots",
    )
    china.add_argument("--start", default="2005-01-01")
    china.add_argument("--end", default=None)
    china.add_argument("--output", default="data/processed/china")
    china.add_argument("--min-z-history", type=int, default=36)
    china.add_argument(
        "--availability-policy",
        choices=[
            "official_release_only",
            "include_schedule",
            "include_estimates",
            "include_unverified",
        ],
        default="official_release_only",
        help="Default includes only row dates backed by an official release document",
    )

    snapshot = sub.add_parser(
        "validate-snapshot",
        help="Validate a frozen macro CSV and its .meta.yaml sidecar",
    )
    snapshot.add_argument("path")
    snapshot.add_argument(
        "--allow-missing-available-date",
        action="store_true",
        help="Allow snapshots whose rows explicitly mark availability_basis=unknown",
    )

    research = sub.add_parser(
        "analyze-us-regimes",
        help="Map US macro regimes to 3M/6M/12M forward cross-asset returns",
    )
    research.add_argument("--start", default="2000-01-01")
    research.add_argument("--end", default=None)
    research.add_argument("--output", default="reports/us_regimes")
    research.add_argument("--min-z-history", type=int, default=36)

    return parser


def _write_baseline(
    raw,
    factors,
    regimes,
    output: str,
    run_metadata: dict[str, object] | None = None,
) -> None:
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    raw.to_csv(out / "raw_monthly.csv")
    factors.to_csv(out / "factors.csv")
    regimes.to_frame().to_csv(out / "regimes.csv")
    if run_metadata is not None:
        (out / "run_metadata.yaml").write_text(
            yaml.safe_dump(run_metadata, sort_keys=False),
            encoding="utf-8",
        )
    print(f"Wrote baseline to {out}")


def main() -> None:
    args = build_parser().parse_args()

    if args.command == "build-us-baseline":
        _write_baseline(
            *build_us_baseline(
                start=args.start,
                end=args.end,
                min_z_history=args.min_z_history,
            ),
            output=args.output,
            run_metadata={
                "country": "united_states",
                "availability_policy": "all; FRED uses approximate release lags",
                "macro_vintage": "latest FRED vintage",
            },
        )
        return

    if args.command == "build-china-baseline":
        _write_baseline(
            *build_china_baseline(
                start=args.start,
                end=args.end,
                min_z_history=args.min_z_history,
                availability_policy=args.availability_policy,
            ),
            output=args.output,
            run_metadata={
                "country": "china",
                "availability_policy": args.availability_policy,
                "macro_vintage": "frozen official release snapshots",
            },
        )
        return

    if args.command == "validate-snapshot":
        result = validate_snapshot(
            args.path,
            require_available_date=not args.allow_missing_available_date,
        )
        print(
            "Snapshot OK:",
            f"rows={result.rows}",
            f"range={result.first_observation.date()}..{result.last_observation.date()}",
            f"available_date={result.has_available_date}",
            f"availability_basis={result.availability_basis_counts}",
        )
        return

    if args.command == "analyze-us-regimes":
        state, prices, fwd, summary = analyze_us_regimes(
            start=args.start,
            end=args.end,
            min_z_history=args.min_z_history,
        )
        out = Path(args.output)
        out.mkdir(parents=True, exist_ok=True)
        state.to_csv(out / "state_history.csv")
        prices.to_csv(out / "asset_prices.csv")
        fwd.to_csv(out / "forward_returns.csv")
        summary.to_csv(out / "regime_return_summary.csv", index=False)
        print(f"Wrote US regime research outputs to {out}")
        return


if __name__ == "__main__":
    main()
