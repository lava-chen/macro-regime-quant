"""SQLite-backed queue for asynchronous, reproducible backtest requests.

The queue and its reports must live on persistent storage. It intentionally
runs one worker in the API process for the single-instance personal prototype;
multi-instance deployments need a shared queue/worker service.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_RUN_ID = re.compile(r"^bt_[a-f0-9]{24}$")
_MAX_ERROR_CHARS = 500


def _now() -> str:
    return datetime.now(UTC).isoformat()


class BacktestRunStore:
    """Durable status and request store for single-process backtest jobs."""

    def __init__(self, state_root: str | Path) -> None:
        self.root = Path(state_root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "backtest_runs.sqlite3"
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS backtest_runs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
                    request_json TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL,
                    idempotency_key TEXT UNIQUE,
                    submitted_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    backtest_id TEXT,
                    error TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_backtest_runs_status_time
                    ON backtest_runs(status, submitted_at);
                """
            )

    def submit(
        self, request: dict[str, Any], *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        encoded = json.dumps(request, sort_keys=True, separators=(",", ":"), allow_nan=False)
        request_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        key = _validate_idempotency_key(idempotency_key)
        run_id = f"bt_{uuid.uuid4().hex[:24]}"
        submitted_at = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if key is not None:
                existing = connection.execute(
                    "SELECT run_id, request_sha256 FROM backtest_runs WHERE idempotency_key = ?",
                    (key,),
                ).fetchone()
                if existing is not None:
                    if existing["request_sha256"] != request_hash:
                        raise ValueError("Idempotency-Key was already used for a different request")
                    row = connection.execute(
                        "SELECT * FROM backtest_runs WHERE run_id = ?", (existing["run_id"],)
                    ).fetchone()
                    connection.commit()
                    return _public_row(row)
            connection.execute(
                """INSERT INTO backtest_runs
                (run_id, status, request_json, request_sha256, idempotency_key, submitted_at)
                VALUES (?, 'queued', ?, ?, ?, ?)""",
                (run_id, encoded, request_hash, key, submitted_at),
            )
            row = connection.execute(
                "SELECT * FROM backtest_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            connection.commit()
        return _public_row(row)

    def get(self, run_id: str) -> dict[str, Any]:
        normalized = _validate_run_id(run_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM backtest_runs WHERE run_id = ?", (normalized,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Backtest run {normalized!r} was not found")
        return _public_row(row)

    def claim_next(self) -> tuple[str, dict[str, Any]] | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT run_id, request_json FROM backtest_runs WHERE status = 'queued' "
                "ORDER BY submitted_at, run_id LIMIT 1"
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            started_at = _now()
            connection.execute(
                "UPDATE backtest_runs SET status = 'running', started_at = ?, attempts = attempts + 1 "
                "WHERE run_id = ? AND status = 'queued'",
                (started_at, row["run_id"]),
            )
            connection.commit()
        return str(row["run_id"]), json.loads(row["request_json"])

    def mark_succeeded(self, run_id: str, backtest_id: str) -> None:
        normalized = _validate_run_id(run_id)
        if _validate_run_id(backtest_id) != normalized:
            raise ValueError("Asynchronous run and saved report IDs must match")
        with self._connect() as connection:
            result = connection.execute(
                "UPDATE backtest_runs SET status = 'succeeded', backtest_id = ?, finished_at = ?, error = NULL "
                "WHERE run_id = ? AND status = 'running'",
                (backtest_id, _now(), normalized),
            )
        if result.rowcount != 1:
            raise KeyError(f"Running backtest {normalized!r} was not found")

    def mark_failed(self, run_id: str, error: str) -> None:
        normalized = _validate_run_id(run_id)
        safe_error = str(error).replace("\x00", "")[:_MAX_ERROR_CHARS]
        with self._connect() as connection:
            result = connection.execute(
                "UPDATE backtest_runs SET status = 'failed', finished_at = ?, error = ? "
                "WHERE run_id = ? AND status = 'running'",
                (_now(), safe_error, normalized),
            )
        if result.rowcount != 1:
            raise KeyError(f"Running backtest {normalized!r} was not found")

    def recover_running(self) -> None:
        """Requeue work left running by a terminated single API process.

        The job ID is reused, and the executor returns its already-persisted
        report if a process exited after writing the report but before marking
        the queue row complete.
        """

        with self._connect() as connection:
            connection.execute(
                "UPDATE backtest_runs SET status = 'queued', started_at = NULL "
                "WHERE status = 'running'"
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


class BacktestRunWorker:
    """One durable-queue consumer, intended for a single API process."""

    def __init__(
        self,
        store: BacktestRunStore,
        execute: Callable[[str, dict[str, Any]], dict[str, Any]],
        *,
        idle_seconds: float = 0.25,
    ) -> None:
        self.store = store
        self.execute = execute
        self.idle_seconds = idle_seconds
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> BacktestRunWorker:
        if self._thread is not None:
            return self
        self.store.recover_running()
        self._thread = threading.Thread(target=self._run, name="mrq-backtest-worker", daemon=True)
        self._thread.start()
        return self

    def wake(self) -> None:
        self._wake.set()

    def close(self, timeout: float = 30.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            claimed = self.store.claim_next()
            if claimed is None:
                self._wake.wait(self.idle_seconds)
                self._wake.clear()
                continue
            run_id, request = claimed
            try:
                report = self.execute(run_id, request)
                self.store.mark_succeeded(run_id, str(report["backtest_id"]))
            except Exception as exc:  # noqa: BLE001 - keep worker alive and expose failed status
                self.store.mark_failed(run_id, f"{type(exc).__name__}: {exc}")


def _public_row(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        raise RuntimeError("Backtest queue row disappeared")
    return {
        "run_id": row["run_id"],
        "status": row["status"],
        "submitted_at": row["submitted_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "backtest_id": row["backtest_id"],
        "error": row["error"],
        "attempts": int(row["attempts"]),
    }


def _validate_run_id(value: str) -> str:
    normalized = str(value).strip()
    if not _RUN_ID.fullmatch(normalized):
        raise ValueError("run_id must be a backtest ID returned by this API")
    return normalized


def _validate_idempotency_key(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized or len(normalized) > 128 or any(ord(char) < 32 for char in normalized):
        raise ValueError("Idempotency-Key must contain 1 to 128 printable characters")
    return normalized
