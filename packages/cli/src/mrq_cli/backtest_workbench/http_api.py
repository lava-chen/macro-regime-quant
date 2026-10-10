from __future__ import annotations

import hashlib
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from .models import StrategySpec
from .prices import available_market_symbols, market_data_status
from .runs import BacktestRunStore, BacktestRunWorker
from .service import execute_backtest, read_backtest_report
from .store import StrategyStore

MAX_BODY_BYTES = 1_048_576


class BacktestRequestHandler(BaseHTTPRequestHandler):
    server_version = "MacroRegimeQuantAPI/1.0"

    def do_GET(self) -> None:
        path = unquote(urlsplit(self.path).path)
        if path == "/health":
            self._send_json(200, {"status": "ok", "service": "macro-regime-quant-backtest"})
            return
        if not self._authorized():
            return
        try:
            if path == "/assets":
                self._send_json(200, {"assets": available_market_symbols()})
            elif path == "/data-status":
                self._send_json(200, market_data_status(os.environ.get("MRQ_PROJECT_ROOT")))
            elif path == "/strategies":
                self._send_json(200, {"strategies": [row.to_dict() for row in StrategyStore().list()]})
            elif path.startswith("/strategies/"):
                parts = path.strip("/").split("/")
                if len(parts) == 3 and parts[2] == "versions":
                    self._send_json(
                        200,
                        {"versions": [row.to_dict() for row in StrategyStore().list_versions(parts[1])]},
                    )
                elif len(parts) == 4 and parts[2] == "versions":
                    self._send_json(
                        200,
                        StrategyStore().get_version(parts[1], int(parts[3])).to_dict(),
                    )
                else:
                    self._send_error_json(404, "Unknown endpoint")
            elif path.startswith("/backtest-runs/"):
                run_id = path.removeprefix("/backtest-runs/")
                self._send_json(200, self.server.run_store.get(run_id))  # type: ignore[attr-defined]
            elif path.startswith("/backtests/"):
                run_id = path.removeprefix("/backtests/")
                self._send_json(200, read_backtest_report(run_id))
            else:
                self._send_error_json(404, "Unknown endpoint")
        except KeyError as exc:
            self._send_error_json(404, str(exc))
        except ValueError as exc:
            self._send_error_json(422, str(exc))
        except FileNotFoundError as exc:
            self._send_error_json(404 if path.startswith("/backtests/") else 500, str(exc))

    def do_POST(self) -> None:
        if not self._authorized():
            return
        try:
            payload = self._read_json()
            if self.path.split("?", 1)[0] == "/strategies":
                spec = StrategySpec.from_dict(payload)
                record = StrategyStore().save(spec)
                self._send_json(201, record.to_dict())
            elif urlsplit(self.path).path == "/backtests":
                self._run_backtest(payload)
            elif urlsplit(self.path).path == "/backtest-runs":
                self._submit_backtest(payload)
            else:
                self._send_error_json(404, "Unknown endpoint")
        except FileExistsError as exc:
            self._send_error_json(409, str(exc))
        except KeyError as exc:
            self._send_error_json(404, str(exc))
        except (TypeError, ValueError) as exc:
            self._send_error_json(422, str(exc))
        except RuntimeError as exc:
            self._send_error_json(502, str(exc))

    def do_PUT(self) -> None:
        if not self._authorized():
            return
        path = unquote(urlsplit(self.path).path)
        if not path.startswith("/strategies/"):
            self._send_error_json(404, "Unknown endpoint")
            return
        strategy_id = path.removeprefix("/strategies/")
        store = StrategyStore()
        try:
            store.get(strategy_id)
            spec = StrategySpec.from_dict(self._read_json())
            self._send_json(200, store.save(spec, strategy_id=strategy_id, replace=True).to_dict())
        except KeyError as exc:
            self._send_error_json(404, str(exc))
        except (TypeError, ValueError) as exc:
            self._send_error_json(422, str(exc))

    def _run_backtest(self, payload: dict[str, Any]) -> None:
        prepared = _prepare_backtest(payload)
        report = _execute_prepared_backtest(
            prepared,
            project_root=os.environ.get("MRQ_PROJECT_ROOT"),
            state_root=os.environ.get("MRQ_STATE_DIR"),
        )
        self._send_json(200, report)

    def _submit_backtest(self, payload: dict[str, Any]) -> None:
        prepared = _prepare_backtest(payload)
        key = self.headers.get("Idempotency-Key") or payload.get("idempotency_key")
        if key is not None and not isinstance(key, str):
            raise TypeError("Idempotency-Key must be a string")
        job = self.server.run_store.submit(prepared, idempotency_key=key)  # type: ignore[attr-defined]
        self.server.run_worker.wake()  # type: ignore[attr-defined]
        self._send_json(202, job)

    def _authorized(self) -> bool:
        expected = os.environ.get("MRQ_API_TOKEN", "")
        if not expected:
            self._send_error_json(503, "API is not configured: MRQ_API_TOKEN is missing")
            return False
        scheme, separator, supplied = self.headers.get("Authorization", "").partition(" ")
        if (
            not separator
            or scheme.lower() != "bearer"
            or not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))
        ):
            self._send_error_json(401, "Invalid bearer token")
            return False
        return True

    def _read_json(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("Content-Length must be an integer") from exc
        if length < 1:
            raise ValueError("Request body is required")
        if length > MAX_BODY_BYTES:
            raise ValueError("Request body exceeds 1 MiB")
        try:
            payload = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Request body must be valid UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            raise TypeError("Request body must be a JSON object")
        return payload

    def _send_json(self, status_code: int, value: object) -> None:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def _send_error_json(self, status_code: int, message: str) -> None:
        self._send_json(status_code, {"error": message})

    def log_message(self, format_string: str, *args: object) -> None:
        # Avoid logging request bodies, strategy inputs, or bearer headers.
        super().log_message(format_string, *args)


class BacktestHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, host: str, port: int) -> None:
        super().__init__((host, port), BacktestRequestHandler)
        state_root = Path(os.environ.get("MRQ_STATE_DIR", "~/.macro-regime-quant")).expanduser()
        self.run_store = BacktestRunStore(state_root)
        self.run_worker = BacktestRunWorker(self.run_store, _execute_queued_request).start()

    def server_close(self) -> None:
        self.run_worker.close()
        super().server_close()


def _prepare_backtest(payload: dict[str, Any]) -> dict[str, Any]:
    strategy_id = payload.get("strategy_id")
    inline_strategy = payload.get("strategy")
    if bool(strategy_id) == bool(inline_strategy):
        raise ValueError("Provide exactly one of strategy_id or strategy")
    strategy_version = None
    if strategy_id:
        store = StrategyStore()
        requested_version = payload.get("version")
        record = (
            store.get_version(str(strategy_id), int(requested_version))
            if requested_version is not None
            else store.get(str(strategy_id))
        )
        strategy = record.spec
        canonical = json.dumps(record.spec.to_dict(), sort_keys=True, separators=(",", ":"))
        strategy_version = {
            "strategy_id": record.strategy_id,
            "version": record.version,
            "spec_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        }
    elif isinstance(inline_strategy, dict):
        strategy = StrategySpec.from_dict(inline_strategy)
    else:
        raise ValueError("strategy must be a JSON object")
    return {"strategy": strategy.to_dict(), "strategy_version": strategy_version}


def _execute_prepared_backtest(
    prepared: dict[str, Any],
    *,
    project_root: str | None,
    state_root: str | None,
    run_id: str | None = None,
) -> dict[str, Any]:
    strategy = StrategySpec.from_dict(dict(prepared["strategy"]))
    return execute_backtest(
        strategy,
        project_root=project_root,
        state_root=state_root,
        strategy_version=prepared.get("strategy_version"),
        run_id=run_id,
    )


def _execute_queued_request(run_id: str, prepared: dict[str, Any]) -> dict[str, Any]:
    return _execute_prepared_backtest(
        prepared,
        project_root=os.environ.get("MRQ_PROJECT_ROOT"),
        state_root=os.environ.get("MRQ_STATE_DIR"),
        run_id=run_id,
    )


def create_server(host: str = "127.0.0.1", port: int = 8000) -> BacktestHTTPServer:
    return BacktestHTTPServer(host, port)


def serve_api(host: str = "127.0.0.1", port: int = 8000) -> None:
    if not os.environ.get("MRQ_API_TOKEN"):
        raise SystemExit("Set MRQ_API_TOKEN before starting the backtest API")
    server = create_server(host, port)
    print(f"Backtest API listening on http://{host}:{server.server_port}")
    print("Health: /health · Async runs: /backtest-runs · OpenAPI: chatgpt/openapi.yaml")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping backtest API")
    finally:
        server.server_close()
