from __future__ import annotations

import time

import pytest
from mrq_cli.backtest_workbench.runs import BacktestRunStore, BacktestRunWorker


def _wait_for(store: BacktestRunStore, run_id: str, expected: str) -> dict[str, object]:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        status = store.get(run_id)
        if status["status"] == expected:
            return status
        if status["status"] in {"failed", "succeeded"}:
            break
        time.sleep(0.01)
    return store.get(run_id)


def test_run_queue_is_durable_and_idempotent(tmp_path):
    store = BacktestRunStore(tmp_path)
    request = {"strategy": {"name": "cash", "weights": {"CASH": 1.0}}}

    submitted = store.submit(request, idempotency_key="chat-req-001")
    repeated = store.submit(request, idempotency_key="chat-req-001")

    assert repeated["run_id"] == submitted["run_id"]
    assert repeated["status"] == "queued"
    assert BacktestRunStore(tmp_path).get(submitted["run_id"]) == submitted
    with pytest.raises(ValueError, match="different request"):
        store.submit(
            {"strategy": {"name": "other", "weights": {"CASH": 1.0}}},
            idempotency_key="chat-req-001",
        )


def test_run_queue_recovers_interrupted_work_and_persists_report_status(tmp_path):
    store = BacktestRunStore(tmp_path)
    submitted = store.submit({"strategy": {"name": "snapshot", "weights": {"GLD": 1.0}}})
    claimed = store.claim_next()
    assert claimed is not None
    assert claimed[0] == submitted["run_id"]
    assert store.get(submitted["run_id"])["status"] == "running"

    worker = BacktestRunWorker(store, lambda run_id, request: {"backtest_id": run_id}).start()
    try:
        completed = _wait_for(store, submitted["run_id"], "succeeded")
    finally:
        worker.close()

    assert completed["status"] == "succeeded"
    assert completed["backtest_id"] == submitted["run_id"]
    assert completed["attempts"] == 2
    assert BacktestRunStore(tmp_path).get(submitted["run_id"]) == completed


def test_run_queue_records_worker_failures_without_stopping_consumer(tmp_path):
    store = BacktestRunStore(tmp_path)
    submitted = store.submit({"strategy": {"name": "unavailable", "weights": {"SPY": 1.0}}})

    def fail(_run_id, _request):
        raise RuntimeError("price provider unavailable")

    worker = BacktestRunWorker(store, fail).start()
    try:
        failed = _wait_for(store, submitted["run_id"], "failed")
    finally:
        worker.close()

    assert failed["status"] == "failed"
    assert failed["error"] == "RuntimeError: price provider unavailable"
    assert failed["backtest_id"] is None
