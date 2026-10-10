import json

import pandas as pd
from mrq_cli.backtest_workbench import service
from mrq_cli.backtest_workbench.models import StrategySpec
from mrq_cli.backtest_workbench.prices import PriceSource


def test_service_uses_build_sha_for_report_provenance(monkeypatch):
    monkeypatch.setenv("MRQ_CODE_VERSION", "build-commit-abc123")

    assert service._code_version() == "build-commit-abc123"


def test_service_persists_input_snapshot_report_and_markdown(tmp_path, monkeypatch):
    dates = pd.date_range("2020-01-01", periods=5, freq="B")
    prices = pd.DataFrame(
        {"GLD": [100, 101, 102, 103, 104], "QQQ": [100, 100, 101, 102, 105]},
        index=dates,
        dtype=float,
    )
    source = PriceSource(
        symbol="GLD",
        provider="local_csv",
        price_basis="adjusted_close",
        source_path="data/raw/market/GLD.csv",
        source_url=None,
        first_date="2020-01-01",
        last_date="2020-01-07",
        observations=5,
        retrieved_at="2026-10-09T00:00:00+00:00",
        content_sha256="a" * 64,
    )
    qqq_source = PriceSource(**{**source.to_dict(), "symbol": "QQQ", "content_sha256": "b" * 64})
    monkeypatch.setattr(service, "load_market_prices", lambda *args, **kwargs: (prices, [source, qqq_source]))
    strategy = StrategySpec(
        name="Test 50/50",
        weights={"GLD": 0.5, "QQQ": 0.5},
        start_date="2020-01-01",
        end_date="2020-01-07",
        initial_capital=1000,
        transaction_cost_bps=0,
    )

    report = service.execute_backtest(strategy, state_root=tmp_path)

    run_dir = tmp_path / "backtests" / report["backtest_id"]
    assert report["metrics"]["ending_value"] > 1000
    assert report["data"]["price_snapshot_sha256"]
    assert (run_dir / "price_snapshot.csv").is_file()
    assert (run_dir / "report.md").is_file()
    assert json.loads((run_dir / "report.json").read_text("utf-8"))["backtest_id"] == report["backtest_id"]
    assert service.read_backtest_report(report["backtest_id"], state_root=tmp_path) == report


def test_service_report_is_json_serializable_with_flat_or_nan_metrics(tmp_path, monkeypatch):
    dates = pd.date_range("2024-01-01", periods=4, freq="B")
    prices = pd.DataFrame({"GLD": [100, 100, 100, 100]}, index=dates, dtype=float)
    source = PriceSource(
        symbol="GLD",
        provider="local_csv",
        price_basis="close",
        source_path="data/raw/market/GLD.csv",
        source_url=None,
        first_date=dates[0].date().isoformat(),
        last_date=dates[-1].date().isoformat(),
        observations=4,
        retrieved_at="2026-10-09T00:00:00+00:00",
        content_sha256="c" * 64,
    )
    monkeypatch.setattr(service, "load_market_prices", lambda *args, **kwargs: (prices, [source]))
    report = service.execute_backtest(
        StrategySpec(name="One asset", weights={"GLD": 1.0}, transaction_cost_bps=0),
        state_root=tmp_path,
    )
    json.dumps(report, allow_nan=False)
    assert report["benchmarks"]["GLD_buy_and_hold"]["sharpe"] is None
