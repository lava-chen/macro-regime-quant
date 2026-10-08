from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import yaml
from mrq_data.catalog import CatalogError, load_catalog
from mrq_data.snapshots import validate_snapshot
from mrq_engines.pipeline import (
    build_china_baseline,
    build_us_baseline,
    load_monthly_panel,
)
from mrq_research.idea import IdeaError, evaluate_idea, load_signal_function
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


    test = sub.add_parser(
        "test-idea",
        help="Evaluate a signal file against forward returns and report IC diagnostics",
    )
    test.add_argument("idea", help="Python file exposing signal(panel) -> pd.Series")
    test.add_argument(
        "--series",
        required=True,
        help="Comma-separated catalogue keys the signal is allowed to read",
    )
    test.add_argument(
        "--forward-assets",
        required=True,
        help="Comma-separated catalogue keys used as forward-return targets",
    )
    test.add_argument("--start", default="2000-01-01")
    test.add_argument("--end", default=None)
    test.add_argument("--horizons", default="1,3,6,12")
    test.add_argument("--quantiles", type=int, default=5)
    test.add_argument("--catalog", default="config/data_catalog.yaml")
    test.add_argument(
        "--availability-policy",
        default="all",
        help="all | official_release_only | include_schedule | include_estimates | include_unverified",
    )
    archive = sub.add_parser(
        "snapshot-vintages",
        help="Capture ALFRED vintages for named series so a backtest can be truly point-in-time",
    )
    archive.add_argument(
        "--series",
        required=True,
        help="Comma-separated catalogue keys to snapshot. Use the FRED-backed ones.",
    )
    archive.add_argument("--start", default="2000-01-01")
    archive.add_argument("--end", default=None)
    archive.add_argument(
        "--step-months",
        type=int,
        default=3,
        help="Months between captured vintages (3 = quarterly).",
    )
    archive.add_argument("--catalog", default="config/data_catalog.yaml")
    archive.add_argument("--root", default="data/vintages")

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


def _run_snapshot_vintages(args) -> None:
    """Grow the vintage archive so historical backtests stop reading the future."""

    from mrq_data.vintages import coverage, snapshot_series

    try:
        catalog = load_catalog(args.catalog)
    except CatalogError as exc:
        raise SystemExit(f"error: {exc}") from exc

    keys = [s.strip() for s in args.series.split(",") if s.strip()]
    missing = [k for k in keys if k not in catalog]
    if missing:
        raise SystemExit(
            f"error: unknown series {missing}. Known keys include {sorted(catalog)[:6]}"
        )

    root = Path(args.root)
    print(
        f"Snapshotting {len(keys)} series every {args.step_months} month(s) "
        f"from {args.start} to {args.end or 'today'}"
    )

    for key in keys:
        spec = catalog[key]
        if spec.provider != "alfred":
            # A symbol that is never revised gains nothing from an archive, and
            # fetching it anyway would cost minutes for an identical result.
            print(f"  {key}: skipped (provider={spec.provider}) — point it at 'alfred' first")
            continue
        print(f"  {key} ({spec.symbol})")
        result = snapshot_series(
            spec,
            start=args.start,
            end=args.end,
            step_months=args.step_months,
            root=root,
            progress=True,
        )
        cov = coverage(spec.symbol, root=root)
        print(
            f"    captured={result['captured']} skipped={result['skipped']}"
            f" | archive {cov.earliest.date() if cov.earliest else '-'} .."
            f" {cov.latest.date() if cov.latest else '-'}"
            f" ({len(cov.vintages)} vintages)"
        )

    print(
        "\nAn archive is only as honest as its oldest snapshot: a backtest earlier "
        "than the archive start has no point-in-time data and must not be run."
    )


def _run_test_idea(args) -> None:
    """Evaluate a user-supplied signal file and print its diagnostics."""

    series = [s.strip() for s in args.series.split(",") if s.strip()]
    target_keys = [s.strip() for s in args.forward_assets.split(",") if s.strip()]
    horizons = tuple(int(h) for h in args.horizons.split(",") if h.strip())

    try:
        signal_fn = load_signal_function(args.idea)
    except IdeaError as exc:
        raise SystemExit(f"error: {exc}") from exc

    # Target prices are pulled with the same PIT panel machinery, so the
    # forward-return window starts only where the asset was knowable.
    try:
        targets = load_monthly_panel(
            args.catalog, target_keys, start=args.start, end=args.end,
            availability_policy=args.availability_policy,
        )
    except (KeyError, ValueError) as exc:
        raise SystemExit(f"error loading forward assets: {exc}") from exc

    if targets.empty:
        raise SystemExit(
            f"No forward-asset data for {target_keys}. Those sources are unreachable "
            "or unpopulated — fix the data before judging the signal."
        )

    try:
        result = evaluate_idea(
            signal_fn,
            series=series,
            forward_prices=targets,
            start=args.start,
            end=args.end,
            catalog_path=args.catalog,
            horizons=horizons,
            availability_policy=args.availability_policy,
            quantiles=args.quantiles,
        )
    except IdeaError as exc:
        raise SystemExit(f"error: {exc}") from exc

    d = result.diagnostics
    print(f"\nIdea: {args.idea}")
    print(f"Sample: {result.panel.index.min().date()} .. {result.panel.index.max().date()}")
    print(f"\n{'':<10}{'IC':>9}{'RankIC':>9}{'IC/IR':>9}{'n':>8}")
    for h in horizons:
        ic = d.ic_by_horizon.get(h, float("nan"))
        ric = d.rank_ic_by_horizon.get(h, float("nan"))
        ir = d.ic_ir_by_horizon.get(h, float("nan"))
        n = d.n_obs_by_horizon.get(h, 0)
        print(f"  {h:>3}m    {ic:>9.3f}{ric:>9.3f}{ir:>9.2f}{n:>8}")

    print(f"\nVerdict: {d.verdict()}")

    if not d.quantile_returns.empty:
        print("\nForward return by signal bucket:")
        q = d.quantile_returns.copy()
        q["mean_return"] = q["mean_return"].map(lambda v: f"{v:+.4%}")
        print(q.to_string(index=False))
        spread = d.quantile_returns.attrs.get("long_short_spread", float("nan"))
        if not pd.isna(spread):
            print(f"  top-minus-bottom spread: {spread:+.4%}")

    for w in d.warnings:
        print(f"\n! {w}")


def main(argv: list[str] | None = None) -> None:
    """Entry point. ``argv`` defaults to sys.argv; passing it makes the CLI testable."""
    args = build_parser().parse_args(argv)

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

    if args.command == "snapshot-vintages":
        _run_snapshot_vintages(args)
        return

    if args.command == "test-idea":
        _run_test_idea(args)
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
