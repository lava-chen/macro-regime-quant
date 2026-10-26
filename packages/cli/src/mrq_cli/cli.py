from __future__ import annotations

import argparse
import json
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
from mrq_engines.pairs import PairSignalSpec, PairSpec, SpreadSpec
from mrq_engines.pipeline import (
    build_china_baseline,
    build_us_baseline,
    country_source_coverage,
    load_monthly_panel,
)
from mrq_research.backtest.long_short import LongShortConfig
from mrq_research.idea import IdeaError, evaluate_idea, load_signal_function
from mrq_research.pairs import analyse_pair, backtest_pair
from mrq_research.us_regime import analyze_us_regimes

from mrq_cli.backtest_workbench.http_api import serve_api
from mrq_cli.backtest_workbench.prices import load_market_prices


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

    analyse = sub.add_parser(
        "analyse-pair",
        help="Test whether two assets share a stationary long-run relation",
    )
    analyse.add_argument("pair", help="Pair as SYM_A:SYM_B, e.g. KO:PEP")
    analyse.add_argument("--start", default="2005-01-01")
    analyse.add_argument("--end", default=None)
    analyse.add_argument("--lookback", type=int, default=60)
    analyse.add_argument("--price-kind", choices=["log", "level"], default="log")
    analyse.add_argument("--beta-method", choices=["ols", "fixed"], default="ols")

    backtest = sub.add_parser(
        "backtest-pair",
        help="Backtest a z-score pair strategy with two-leg costs and borrow fees",
    )
    backtest.add_argument("pair", help="Pair as SYM_A:SYM_B, e.g. KO:PEP")
    backtest.add_argument("--start", default="2005-01-01")
    backtest.add_argument("--end", default=None)
    backtest.add_argument("--lookback", type=int, default=60)
    backtest.add_argument("--entry-z", type=float, default=2.0)
    backtest.add_argument("--exit-z", type=float, default=0.5)
    backtest.add_argument("--stop-z", type=float, default=None)
    backtest.add_argument("--max-holding-days", type=int, default=None)
    backtest.add_argument("--price-kind", choices=["log", "level"], default="log")
    backtest.add_argument("--beta-method", choices=["ols", "fixed"], default="ols")
    backtest.add_argument("--fixed-beta", type=float, default=1.0)
    backtest.add_argument("--hedge-mode", choices=["beta_neutral", "dollar_neutral"], default="beta_neutral")
    backtest.add_argument("--fee-bps", type=float, default=5.0, help="Commission on traded notional")
    backtest.add_argument("--slippage-bps", type=float, default=2.0)
    backtest.add_argument("--borrow-bps", type=float, default=50.0, help="Annual borrow fee on the short leg")
    backtest.add_argument("--risk-free-rate", type=float, default=0.0)
    backtest.add_argument("--output", default="reports/pairs")

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


def _parse_pair(text: str) -> tuple[str, str]:
    cleaned = text.replace(",", ":").strip().upper()
    parts = [part.strip() for part in cleaned.split(":") if part.strip()]
    if len(parts) != 2:
        raise SystemExit(f"error: a pair is written as SYM_A:SYM_B, got {text!r}")
    if parts[0] == parts[1]:
        raise SystemExit(f"error: a pair needs two different assets, got {text!r}")
    return parts[0], parts[1]


def _load_pair_prices(
    asset_a: str,
    asset_b: str,
    start: str | None,
    end: str | None,
) -> tuple[pd.DataFrame, list[dict[str, object]]]:
    try:
        prices, sources = load_market_prices([asset_a, asset_b], start=start, end=end)
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(f"error loading prices for {asset_a}/{asset_b}: {exc}") from exc
    if prices.empty:
        raise SystemExit(f"No price data for {asset_a}/{asset_b} in the requested range")
    return prices, [source.to_dict() for source in sources]


def _print_pair_diagnostics(diagnostics) -> None:
    print(f"\nSample: {diagnostics.start.date()} .. {diagnostics.end.date()} "
          f"({diagnostics.observations} common sessions)")
    print(f"Hedge ratio (trailing, point-in-time): "
          f"mean {diagnostics.hedge_ratio_mean:.3f}, "
          f"sd {diagnostics.hedge_ratio_std:.3f}, "
          f"last {diagnostics.hedge_ratio_last:.3f}")
    print(f"Cointegration (Engle-Granger, ADF on residuals): "
          f"statistic {diagnostics.cointegration_statistic:.2f} "
          f"(5% critical {diagnostics.cointegration_critical_values['5%']:.2f}) "
          f"-> {diagnostics.cointegration_verdict}")
    print(f"Spread half-life: {diagnostics.spread_half_life:.1f} days")
    print(f"Daily return correlation: {diagnostics.return_correlation:.3f}")
    print(f"Hedge ratio non-positive on: {diagnostics.hedge_ratio_non_positive_share:.0%} of sessions")

    if not diagnostics.ratio_path.empty:
        path = diagnostics.ratio_path
        print("\nYear-end price ratio (a persistent walk here means a trend, not a spread):")
        print(
            "  "
            + "  ".join(
                f"{int(year)}:{ratio:.3f}"
                for year, ratio in zip(path["year"], path["price_ratio"], strict=True)
            )
        )

    print("\nForward behaviour of the spread, by how stretched it was:")
    table = diagnostics.reversion.copy()
    print(table.to_string(index=False, float_format=lambda value: f"{value:+.3f}"))
    for warning in diagnostics.warnings:
        print(f"\n! {warning}")


def _run_analyse_pair(args) -> None:
    asset_a, asset_b = _parse_pair(args.pair)
    prices, sources = _load_pair_prices(asset_a, asset_b, args.start, args.end)
    spec = PairSpec(
        asset_a=asset_a,
        asset_b=asset_b,
        spread=SpreadSpec(
            lookback=args.lookback,
            price_kind=args.price_kind,
            beta_method=args.beta_method,
        ),
    )
    diagnostics = analyse_pair(prices, spec)
    print(f"\nPair: {spec.name}")
    for source in sources:
        print(f"  {source['symbol']}: {source['price_basis']} "
              f"({source['provider']}, {source['first_date']}..{source['last_date']})")
    _print_pair_diagnostics(diagnostics)


def _markdown_table(frame: pd.DataFrame) -> str:
    """Render a frame as a markdown table.

    pandas' own ``to_markdown`` needs the optional ``tabulate`` package, and
    this workspace does not depend on it.
    """

    if frame.empty:
        return ""
    header = "| " + " | ".join(str(column) for column in frame.columns) + " |"
    divider = "| " + " | ".join("---" for _ in frame.columns) + " |"
    rows = [
        "| " + " | ".join(f"{value:.4g}" if isinstance(value, float) else str(value) for value in row) + " |"
        for row in frame.itertuples(index=False, name=None)
    ]
    return "\n".join([header, divider, *rows])


def _pair_markdown(backtest, sources: list[dict[str, object]]) -> str:
    diagnostics = backtest.diagnostics
    metrics = backtest.result.metrics
    spec = backtest.spec
    lines = [
        f"# 配对交易回测：{spec.name}",
        "",
        "## 结论",
        "",
        (
            f"- 协整检验：{diagnostics.cointegration_verdict}"
            f"（统计量 {diagnostics.cointegration_statistic:.2f}，"
            f"5% 临界值 {diagnostics.cointegration_critical_values['5%']:.2f}）"
        ),
        f"- 价差半衰期：{diagnostics.spread_half_life:.1f} 天",
        (
            f"- 样本：{diagnostics.start.date()} .. {diagnostics.end.date()}"
            f"（{diagnostics.observations} 个共同交易日）"
        ),
        (
            f"- 年化收益 {metrics['cagr']:+.2%}，波动 {metrics['vol']:.2%}，"
            f"Sharpe {metrics['sharpe']:.2f}，最大回撤 {metrics['max_drawdown']:.2%}"
        ),
        (
            f"- 总换手 {metrics['total_turnover']:.2f}，"
            f"交易成本 {metrics['total_trading_cost']:.2%}，"
            f"借券成本 {metrics['total_borrow_cost']:.2%}"
        ),
        "",
        "## 参数",
        "",
        f"- 回看窗口：{spec.spread.lookback} 个交易日",
        f"- 价格形式：{spec.spread.price_kind}；对冲比率方法：{spec.spread.beta_method}",
        f"- 对冲方式：{spec.hedge_mode}",
        f"- 进出场阈值：Z ≥ {spec.signal.entry_z} 开仓，|Z| ≤ {spec.signal.exit_z} 平仓",
        "",
        "## 数据来源",
        "",
    ]
    for source in sources:
        lines.append(
            f"- {source['symbol']}：{source['price_basis']}，"
            f"provider={source['provider']}，抓取时间 {source['retrieved_at']}"
        )
    lines += [
        "",
        "## 均值回复",
        "",
        _markdown_table(diagnostics.reversion),
        "",
        "## 年末价格比",
        "",
        _markdown_table(diagnostics.ratio_path),
        "",
        "## 风险提示",
        "",
        "- Yahoo 自动复权价没有 point-in-time 版本，历史公司行为会回溯改写整段价格。",
        "- 上述参数未做样本外验证；用同一段数据选参数再报结果属于过拟合。",
        "- 借券费率是假设值，真实费率随标的和时间变化，且可能无法借到券。",
    ]
    for warning in diagnostics.warnings:
        lines.append(f"- {warning}")
    return "\n".join(lines) + "\n"


def _run_backtest_pair(args) -> None:
    asset_a, asset_b = _parse_pair(args.pair)
    prices, sources = _load_pair_prices(asset_a, asset_b, args.start, args.end)
    spec = PairSpec(
        asset_a=asset_a,
        asset_b=asset_b,
        spread=SpreadSpec(
            lookback=args.lookback,
            price_kind=args.price_kind,
            beta_method=args.beta_method,
            fixed_beta=args.fixed_beta,
        ),
        signal=PairSignalSpec(
            entry_z=args.entry_z,
            exit_z=args.exit_z,
            stop_z=args.stop_z,
            max_holding_days=args.max_holding_days,
        ),
        hedge_mode=args.hedge_mode,
    )
    config = LongShortConfig(
        transaction_cost_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
        borrow_annual_bps=args.borrow_bps,
        risk_free_rate=args.risk_free_rate,
    )
    backtest = backtest_pair(prices, spec, config)
    metrics = backtest.result.metrics

    print(f"\nPair: {spec.name}")
    _print_pair_diagnostics(backtest.diagnostics)

    print("\nBacktest:")
    print(f"  CAGR              {metrics['cagr']:+.2%}")
    print(f"  Volatility        {metrics['vol']:.2%}")
    print(f"  Sharpe            {metrics['sharpe']:.2f}")
    print(f"  Max drawdown      {metrics['max_drawdown']:.2%}")
    print(f"  Calmar            {metrics['calmar']:.2f}")
    print(f"  Total turnover    {metrics['total_turnover']:.2f}")
    print(f"  Trading costs     {metrics['total_trading_cost']:.2%}")
    print(f"  Borrow costs      {metrics['total_borrow_cost']:.2%}")
    print(f"  Avg gross exposure {metrics['average_gross_exposure']:.2%}")
    print(f"  Avg net exposure   {metrics['average_net_exposure']:.2%}")
    print(f"  Round trips       {int(backtest.frame['exit'].sum())}")

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    stem = spec.name.replace("/", "_")
    backtest.frame.to_csv(out / f"{stem}_timeseries.csv")
    payload = backtest.to_dict()
    payload["sources"] = sources
    (out / f"{stem}_report.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    (out / f"{stem}_report.md").write_text(
        _pair_markdown(backtest, sources), encoding="utf-8"
    )
    print(f"\nWrote {out}/{stem}_report.md and {stem}_timeseries.csv")


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

    if args.command == "analyse-pair":
        _run_analyse_pair(args)
        return

    if args.command == "backtest-pair":
        _run_backtest_pair(args)
        return


if __name__ == "__main__":
    main()
