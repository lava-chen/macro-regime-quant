from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import pandas as pd

from .models import StrategySpec
from .prices import load_market_prices
from .report import build_backtest_report, build_markdown_report
from .runner import PortfolioRun, run_portfolio_backtest


def execute_backtest(
    strategy: StrategySpec,
    *,
    project_root: str | Path | None = None,
    state_root: str | Path | None = None,
) -> dict[str, Any]:
    symbols = [symbol for symbol in strategy.weights if symbol != "CASH"]
    prices, source_records = load_market_prices(
        symbols,
        start=strategy.start_date,
        end=strategy.end_date,
        project_root=project_root,
    )
    aligned = prices.loc[:, symbols].dropna(how="any")
    if len(aligned) < 2:
        raise ValueError("The selected assets do not have two common complete price dates")

    run_id = f"bt_{uuid.uuid4().hex[:12]}"
    portfolio_run = run_portfolio_backtest(prices, strategy)
    benchmarks = _benchmark_results(portfolio_run, strategy)
    price_bytes = portfolio_run.prices.to_csv(index_label="date", float_format="%.12g").encode("utf-8")
    input_sha256 = hashlib.sha256(price_bytes).hexdigest()
    generated_at = datetime.now(timezone.utc).isoformat()
    report = build_backtest_report(
        run_id,
        strategy,
        portfolio_run,
        price_sources=[source.to_dict() for source in source_records],
        benchmarks=benchmarks,
        input_sha256=input_sha256,
        generated_at=generated_at,
        code_version=_code_version(),
    )
    report["data"]["aligned_start"] = portfolio_run.prices.index.min().date().isoformat()
    report["data"]["aligned_end"] = portfolio_run.prices.index.max().date().isoformat()
    report["data"]["snapshot_file"] = "price_snapshot.csv"
    _persist_run(report, price_bytes, build_markdown_report(report), state_root=state_root)
    return report


def read_backtest_report(run_id: str, *, state_root: str | Path | None = None) -> dict[str, Any]:
    if not run_id.startswith("bt_") or not run_id[3:].isalnum() or len(run_id) > 32:
        raise ValueError("Invalid backtest run ID")
    return json.loads((_state_root(state_root) / "backtests" / run_id / "report.json").read_text("utf-8"))


def _benchmark_results(
    portfolio_run: PortfolioRun,
    strategy: StrategySpec,
) -> dict[str, dict[str, float | None]]:
    from mrq_research.backtest.engine import BacktestConfig, run_backtest

    prices = portfolio_run.prices
    benchmark_map: dict[str, dict[str, float | None]] = {}
    weight_columns = list(strategy.weights)
    hold_target = pd.DataFrame([strategy.weights], index=[prices.index[0]])
    hold = run_backtest(
        prices,
        hold_target,
        BacktestConfig(
            transaction_cost_bps=strategy.transaction_cost_bps,
            execution_lag_periods=1,
            periods_per_year=252,
            allow_cash=True,
        ),
    )
    benchmark_map["same_weights_buy_and_hold"] = _benchmark_metrics(hold)

    for symbol in weight_columns:
        if symbol == "CASH":
            continue
        target = pd.DataFrame(0.0, index=[prices.index[0]], columns=prices.columns)
        target.loc[prices.index[0], symbol] = 1.0
        result = run_backtest(
            prices,
            target,
            BacktestConfig(
                transaction_cost_bps=strategy.transaction_cost_bps,
                execution_lag_periods=1,
                periods_per_year=252,
                allow_cash=False,
            ),
        )
        benchmark_map[f"{symbol}_buy_and_hold"] = _benchmark_metrics(result)
    return benchmark_map


def _benchmark_metrics(result: Any) -> dict[str, float | None]:
    metrics: dict[str, float | None] = {}
    for name, value in result.metrics.items():
        number = float(value)
        metrics[name] = number if pd.notna(number) else None
    metrics["total_return"] = float((1.0 + result.returns).prod() - 1.0)
    return metrics


def _persist_run(
    report: dict[str, Any],
    prices_csv: bytes,
    markdown: str,
    *,
    state_root: str | Path | None,
) -> None:
    destination = _state_root(state_root) / "backtests" / str(report["backtest_id"])
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "price_snapshot.csv").write_bytes(prices_csv)
    (destination / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    (destination / "report.md").write_text(markdown, encoding="utf-8")


def _state_root(value: str | Path | None) -> Path:
    if value is not None:
        return Path(value).expanduser()
    return Path(os.environ.get("MRQ_STATE_DIR", "~/.macro-regime-quant")).expanduser()


def _code_version() -> str:
    try:
        return version("macro-regime-quant")
    except PackageNotFoundError:
        return "source-checkout"
