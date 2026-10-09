from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import yaml
from mrq_data.china_watch import (
    append_live_records,
    run_china_data_watch,
    write_collection_audit_markdown,
)
from mrq_data.snapshots import validate_snapshot
from mrq_engines.china_macro_report import build_china_macro_report
from mrq_engines.pipeline import (
    build_china_baseline,
    build_us_baseline,
    country_source_coverage,
    load_monthly_panel,
)
from mrq_research.idea import IdeaError, evaluate_idea, load_signal_function
from mrq_research.us_regime import analyze_us_regimes

from mrq_cli.backtest_workbench.http_api import serve_api


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
    china.add_argument(
        "--allow-partial-sources",
        action="store_true",
        help="Build present China sources only and leave under-sourced factors missing",
    )

    collect = sub.add_parser(
        "refresh-china-macro",
        aliases=["collect-china-snapshots"],
        help="Incrementally collect NBS/PBOC data, audit changes, and write the PIT-safe report",
    )
    collect.add_argument("--start-year", type=int, default=None)
    collect.add_argument("--end-year", type=int, default=None)
    collect.add_argument("--output", default="data/raw/china")
    collect.add_argument("--report-dir", default="reports/china_macro")
    collect.add_argument("--workers", type=int, default=2)
    collect.add_argument("--full-rescan", action="store_true")
    collect.add_argument("--as-of", default=None, help="Historical cutoff date (YYYY-MM-DD)")
    collect.add_argument("--min-z-history", type=int, default=36)

    china_report = sub.add_parser(
        "report-china-macro",
        help="Rebuild the China macro report offline from the immutable snapshot ledger",
    )
    china_report.add_argument("--ledger", default="data/raw/china/china_macro_snapshots.csv")
    china_report.add_argument("--raw-dir", default="data/raw/china")
    china_report.add_argument("--output", default="reports/china_macro")
    china_report.add_argument("--as-of", default=None, help="Historical cutoff date (YYYY-MM-DD)")
    china_report.add_argument("--min-z-history", type=int, default=36)

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

    api = sub.add_parser(
        "serve-backtest-api",
        help="Serve the authenticated strategy and backtest API for a Custom GPT Action",
    )
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8000)

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


def main() -> None:
    args = build_parser().parse_args()

    if args.command == "serve-backtest-api":
        serve_api(host=args.host, port=args.port)
        return

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
        coverage = country_source_coverage("china")
        if coverage["missing_snapshots"] and not args.allow_partial_sources:
            raise FileNotFoundError(
                "Missing configured China snapshots: "
                f"{coverage['missing_snapshots']}. Collect sources or pass "
                "--allow-partial-sources for a documented partial run."
            )
        _write_baseline(
            *build_china_baseline(
                start=args.start,
                end=args.end,
                min_z_history=args.min_z_history,
                availability_policy=args.availability_policy,
                allow_partial_sources=args.allow_partial_sources,
            ),
            output=args.output,
            run_metadata={
                "country": "china",
                "availability_policy": args.availability_policy,
                "macro_vintage": "frozen official release snapshots",
                "partial_sources": args.allow_partial_sources,
                "source_coverage": coverage,
            },
        )
        return

    if args.command in {"refresh-china-macro", "collect-china-snapshots"}:
        audit = run_china_data_watch(
            raw_dir=args.output,
            report_dir=args.report_dir,
            start_year=args.start_year,
            end_year=args.end_year,
            max_workers=args.workers,
            full_rescan=args.full_rescan,
            as_of=args.as_of,
        )
        write_collection_audit_markdown(audit, Path(args.report_dir) / "collection_audit.md")
        report = build_china_macro_report(
            ledger_path=Path(args.output) / "china_macro_snapshots.csv",
            output_dir=args.report_dir,
            as_of=args.as_of,
            min_z_history=args.min_z_history,
            collection_audit=audit,
        )
        print(yaml.safe_dump({
            "collection_status": audit["status"],
            "ledger_rows": audit["ledger_rows"],
            "new_rows_appended": audit["new_rows_appended"],
            "macro_state": report["macro_state"],
            "report": str(Path(args.report_dir) / "latest.json"),
        }, sort_keys=False, allow_unicode=True))
        if audit["status"] != "success":
            raise SystemExit(1)
        return

    if args.command == "report-china-macro":
        ledger = Path(args.ledger)
        append_live_records(ledger, [], raw_dir=args.raw_dir)
        result = build_china_macro_report(
            ledger_path=ledger,
            output_dir=args.output,
            as_of=args.as_of,
            min_z_history=args.min_z_history,
        )
        print(yaml.safe_dump({
            "as_of": result["as_of"],
            "macro_state": result["macro_state"],
            "report": str(Path(args.output) / "latest.json"),
        }, sort_keys=False, allow_unicode=True))
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
