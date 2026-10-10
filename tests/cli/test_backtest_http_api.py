from __future__ import annotations

import json
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from mrq_cli.backtest_workbench.http_api import create_server


def _request(
    base: str,
    method: str,
    path: str,
    *,
    body: dict | None = None,
    token: str | None = None,
    extra_headers: dict[str, str] | None = None,
):
    encoded = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    headers.update(extra_headers or {})
    request = Request(f"{base}{path}", data=encoded, headers=headers, method=method)
    try:
        response = urlopen(request, timeout=5)
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))
    with response:
        return response.status, json.loads(response.read().decode("utf-8"))


def test_http_end_to_end_strategy_save_run_and_report_retrieval(tmp_path, monkeypatch):
    monkeypatch.setenv("MRQ_API_TOKEN", "smoke-secret")
    monkeypatch.setenv("MRQ_PROJECT_ROOT", str(tmp_path))
    state = tmp_path / "private-state"
    monkeypatch.setenv("MRQ_STATE_DIR", str(state))
    config = tmp_path / "config"
    config.mkdir()
    (config / "data_catalog.yaml").write_text(
        """series:
  gold:
    provider: yahoo
    symbol: GLD
    kind: commodity
    frequency: daily
  nasdaq:
    provider: yahoo
    symbol: QQQ
    kind: market
    frequency: daily
""",
        encoding="utf-8",
    )
    market = tmp_path / "data" / "raw" / "market"
    market.mkdir(parents=True)
    dates = ["2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07", "2025-01-08"]
    (market / "GLD.csv").write_text(
        "date,adjusted_close\n" + "".join(f"{day},{value}\n" for day, value in zip(dates, [100, 102, 104, 101, 106])),
        encoding="utf-8",
    )
    (market / "QQQ.csv").write_text(
        "date,adjusted_close\n" + "".join(f"{day},{value}\n" for day, value in zip(dates, [100, 101, 105, 110, 108])),
        encoding="utf-8",
    )

    try:
        server = create_server("127.0.0.1", 0)
    except PermissionError:
        pytest.skip("Execution sandbox denies local socket binding; CI runs this HTTP smoke test.")
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        assert _request(base, "GET", "/health")[0] == 200
        assert _request(base, "GET", "/strategies")[0] == 401
        assert _request(base, "GET", "/assets", token="smoke-secret")[1]["assets"]

        strategy = {
            "name": "Gold Nasdaq 50 50",
            "weights": {"GLD": 0.5, "QQQ": 0.5},
            "rebalance_frequency": "monthly",
            "start_date": "2025-01-02",
            "end_date": "2025-01-08",
            "initial_capital": 1000,
            "transaction_cost_bps": 0,
            "cash_flow": {
                "weekly_contribution_amount": 20,
                "contribution_day": "FRI",
                "take_profit_tiers": [{"return_threshold": 0.2, "sell_fraction": 0.1}],
                "drawdown_rules": [{"trigger_drawdown": 0.15, "max_invested_weight": 0.5}],
            },
        }
        created_status, saved = _request(
            base, "POST", "/strategies", body=strategy, token="smoke-secret"
        )
        assert created_status == 201
        assert saved["strategy_id"] == "gold-nasdaq-50-50"
        assert _request(base, "POST", "/strategies", body=strategy, token="smoke-secret")[0] == 409

        status, report = _request(
            base,
            "POST",
            "/backtests",
            body={"strategy_id": saved["strategy_id"]},
            token="smoke-secret",
        )
        assert status == 200
        assert report["metrics"]["ending_value"] > 1000
        assert report["metrics"]["total_contributions"] == 1040
        assert report["cash_flow"]["contribution_count"] == 2
        assert report["benchmarks"]["weekly_dca_without_controls"]["total_contributions"] == 1040
        assert report["data"]["price_snapshot_sha256"]
        assert report["benchmarks"]["same_weights_buy_and_hold"]
        assert report["strategy_version"]["strategy_id"] == saved["strategy_id"]
        assert report["strategy_version"]["version"] == 1
        assert len(report["strategy_version"]["spec_sha256"]) == 64

        data_status_code, data_status = _request(
            base, "GET", "/data-status", token="smoke-secret"
        )
        assert data_status_code == 200
        data_assets = {asset["symbol"]: asset for asset in data_status["assets"]}
        assert data_assets["GLD"]["availability"] == "snapshot_available"
        assert data_assets["GLD"]["quality"] == "schema_validated_no_fills"
        assert data_assets["QQQ"]["coverage_start"] == "2025-01-02"

        run_payload = {"strategy_id": saved["strategy_id"], "version": 1}
        queued_status, queued = _request(
            base,
            "POST",
            "/backtest-runs",
            body=run_payload,
            token="smoke-secret",
            extra_headers={"Idempotency-Key": "gold-nasdaq-async-once"},
        )
        assert queued_status == 202
        assert queued["status"] in {"queued", "running", "succeeded"}
        duplicate_status, duplicate = _request(
            base,
            "POST",
            "/backtest-runs",
            body=run_payload,
            token="smoke-secret",
            extra_headers={"Idempotency-Key": "gold-nasdaq-async-once"},
        )
        assert duplicate_status == 202
        assert duplicate["run_id"] == queued["run_id"]

        deadline = time.monotonic() + 5
        async_result = queued
        while time.monotonic() < deadline:
            _, async_result = _request(
                base,
                "GET",
                f"/backtest-runs/{queued['run_id']}",
                token="smoke-secret",
            )
            if async_result["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.01)
        assert async_result["status"] == "succeeded"
        assert async_result["backtest_id"] == queued["run_id"]
        async_report_status, async_report = _request(
            base,
            "GET",
            f"/backtests/{queued['run_id']}",
            token="smoke-secret",
        )
        assert async_report_status == 200
        assert async_report["backtest_id"] == queued["run_id"]
        assert async_report["strategy_version"]["version"] == 1

        restored_status, restored = _request(
            base,
            "GET",
            f"/backtests/{report['backtest_id']}",
            token="smoke-secret",
        )
        assert restored_status == 200
        assert restored["backtest_id"] == report["backtest_id"]
        run_dir = state / "backtests" / report["backtest_id"]
        assert (run_dir / "price_snapshot.csv").is_file()
        assert (run_dir / "report.md").is_file()
        assert (run_dir / "report.json").is_file()

        revised_strategy = {
            **strategy,
            "weights": {"GLD": 0.6, "QQQ": 0.4},
        }
        updated_status, updated = _request(
            base,
            "PUT",
            f"/strategies/{saved['strategy_id']}",
            body=revised_strategy,
            token="smoke-secret",
        )
        assert updated_status == 200
        assert updated["version"] == 2
        versions_status, versions = _request(
            base,
            "GET",
            f"/strategies/{saved['strategy_id']}/versions",
            token="smoke-secret",
        )
        assert versions_status == 200
        assert [row["version"] for row in versions["versions"]] == [1, 2]
        old_version_status, old_version = _request(
            base,
            "GET",
            f"/strategies/{saved['strategy_id']}/versions/1",
            token="smoke-secret",
        )
        assert old_version_status == 200
        assert old_version["strategy"]["weights"] == {"GLD": 0.5, "QQQ": 0.5}
        old_run_status, old_run = _request(
            base,
            "POST",
            "/backtests",
            body={"strategy_id": saved["strategy_id"], "version": 1},
            token="smoke-secret",
        )
        assert old_run_status == 200
        assert old_run["strategy_version"]["version"] == 1
        assert old_run["strategy"]["weights"] == {"GLD": 0.5, "QQQ": 0.5}

        invalid = {"strategy": {**strategy, "weights": {"GLD": 0.7, "QQQ": 0.5}}}
        assert _request(base, "POST", "/backtests", body=invalid, token="smoke-secret")[0] == 422
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_http_auth_is_required_and_missing_server_token_fails_closed(tmp_path, monkeypatch):
    monkeypatch.delenv("MRQ_API_TOKEN", raising=False)
    monkeypatch.setenv("MRQ_STATE_DIR", str(tmp_path / "private-state"))
    try:
        server = create_server("127.0.0.1", 0)
    except PermissionError:
        pytest.skip("Execution sandbox denies local socket binding; CI runs this HTTP smoke test.")
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        assert _request(base, "GET", "/health")[0] == 200
        assert _request(base, "GET", "/assets")[0] == 503
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
