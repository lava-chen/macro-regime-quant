from __future__ import annotations

import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from mrq_cli.backtest_workbench.http_api import create_server


def _request(base: str, method: str, path: str, *, body: dict | None = None, token: str | None = None):
    encoded = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
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
        assert report["data"]["price_snapshot_sha256"]
        assert report["benchmarks"]["same_weights_buy_and_hold"]

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

        invalid = {"strategy": {**strategy, "weights": {"GLD": 0.7, "QQQ": 0.5}}}
        assert _request(base, "POST", "/backtests", body=invalid, token="smoke-secret")[0] == 422
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_http_auth_is_required_and_missing_server_token_fails_closed(tmp_path, monkeypatch):
    monkeypatch.delenv("MRQ_API_TOKEN", raising=False)
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
