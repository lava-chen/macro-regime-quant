"""One-shot GitHub Actions worker for a Supabase-queued backtest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

RUN_ID_PATTERN = re.compile(r"^bt_[a-f0-9]{24}$")
ARTIFACT_NAMES = ("report.json", "report.md", "price_snapshot.csv")
MAX_ERROR_CHARS = 500
WorkerExecutor = Callable[[dict[str, Any], str | Path, str | Path, str], dict[str, Any]]


class SupabaseREST:
    """Small server-side REST client; credentials are read only from the runner env."""

    def __init__(self, url: str, secret_key: str, timeout: int = 30) -> None:
        if not url.startswith("https://"):
            raise ValueError("SUPABASE_URL must use HTTPS")
        if not secret_key:
            raise ValueError("MRQ_SUPABASE_SECRET_KEY is required")
        self.base_url = url.rstrip("/")
        self.secret_key = secret_key
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: Any = None,
        raw_body: bytes | None = None,
        content_type: str = "application/json",
        prefer: str | None = None,
    ) -> Any:
        body = raw_body
        headers = {
            "Accept": "application/json",
            "apikey": self.secret_key,
            "Authorization": f"Bearer {self.secret_key}",
        }
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if body is not None:
            headers["Content-Type"] = content_type
        if prefer:
            headers["Prefer"] = prefer
        request = Request(
            f"{self.base_url}/{path.lstrip('/')}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                result = response.read()
        except HTTPError as exc:
            detail = exc.read(300).decode("utf-8", errors="replace").replace("\n", " ")
            raise RuntimeError(f"Supabase returned HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"Supabase request failed: {exc.reason}") from exc
        if not result:
            return None
        if content_type.startswith("application/json") or "json" in content_type:
            return json.loads(result.decode("utf-8"))
        return result

    def upload(self, object_path: str, content: bytes, content_type: str) -> None:
        encoded_path = quote(object_path, safe="/")
        headers = {
            "Accept": "application/json",
            "Content-Type": content_type,
            "apikey": self.secret_key,
            "Authorization": f"Bearer {self.secret_key}",
            "x-upsert": "true",
        }
        request = Request(
            f"{self.base_url}/storage/v1/object/mrq-backtest-artifacts/{encoded_path}",
            data=content,
            headers=headers,
            method="PUT",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                response.read()
        except (HTTPError, URLError) as exc:
            if isinstance(exc, HTTPError):
                detail = exc.read(300).decode("utf-8", errors="replace").replace("\n", " ")
                raise OSError(f"Storage upload failed ({exc.code}): {detail}") from exc
            raise OSError(f"Storage upload failed: {exc.reason}") from exc


def validate_run_id(run_id: str) -> str:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("run_id must match bt_ followed by 24 lowercase hexadecimal characters")
    return run_id


def execution_id() -> str:
    run = os.environ.get("GITHUB_RUN_ID", "local")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    return f"{run}-{attempt}"[:128]


def _execute_prepared(
    prepared: dict[str, Any],
    project_root: str | Path,
    state_root: str | Path,
    run_id: str,
) -> dict[str, Any]:
    from mrq_cli.backtest_workbench.models import StrategySpec
    from mrq_cli.backtest_workbench.service import execute_backtest

    strategy = StrategySpec.from_dict(dict(prepared["strategy"]))
    return execute_backtest(
        strategy,
        project_root=project_root,
        state_root=state_root,
        strategy_version=prepared.get("strategy_version"),
        run_id=run_id,
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _safe_error(error: BaseException) -> str:
    message = " ".join(str(error).split())
    return f"{type(error).__name__}: {message}"[:MAX_ERROR_CHARS]


def process_run(
    run_id: str,
    *,
    api: SupabaseREST,
    executor: WorkerExecutor = _execute_prepared,
    project_root: str | Path,
    worker_id: str | None = None,
) -> bool:
    """Claim once, execute in temporary storage, then persist the report and metrics."""

    validate_run_id(run_id)
    worker_id = worker_id or execution_id()
    claimed = api.request(
        "POST",
        "rest/v1/rpc/claim_mrq_backtest_run",
        payload={"p_run_id": run_id, "p_execution_id": worker_id},
    )
    row = claimed[0] if isinstance(claimed, list) and claimed else None
    if not isinstance(row, dict):
        print(f"run {run_id}: no claim available; it may already be running or terminal")
        return False

    run_filter = (
        f"run_id=eq.{run_id}&"
        f"worker_execution_id=eq.{quote(worker_id, safe='')}"
    )
    run_path = f"rest/v1/mrq_backtest_runs?{run_filter}"
    try:
        prepared = row.get("request_json")
        if not isinstance(prepared, dict) or not isinstance(prepared.get("strategy"), dict):
            raise TypeError("Stored backtest request must contain a strategy object")

        with tempfile.TemporaryDirectory(prefix=f"mrq-{run_id}-") as work_dir:
            state_root = Path(work_dir) / "state"
            executor(prepared, project_root, state_root, run_id)
            run_dir = state_root / "backtests" / run_id
            artifact_bytes = {name: (run_dir / name).read_bytes() for name in ARTIFACT_NAMES}
            report_value = json.loads(artifact_bytes["report.json"].decode("utf-8"))
            prefix = run_id

            mime_types = {
                "report.json": "application/json",
                "report.md": "text/markdown",
                "price_snapshot.csv": "text/csv",
            }
            for name, content in artifact_bytes.items():
                api.upload(f"{prefix}/{name}", content, mime_types[name])

            report_row = {
                "run_id": run_id,
                "strategy_json": report_value["strategy"],
                "metrics": report_value["metrics"],
                "period": report_value["period"],
                "data": report_value["data"],
                "report_sha256": hashlib.sha256(artifact_bytes["report.json"]).hexdigest(),
                "input_sha256": report_value.get("data", {}).get("input_sha256"),
                "artifacts_prefix": prefix,
                "generated_at": report_value.get("generated_at"),
            }
            api.request(
                "POST",
                "rest/v1/mrq_backtest_reports?on_conflict=run_id",
                payload=report_row,
                prefer="resolution=merge-duplicates,return=minimal",
            )
            api.request(
                "PATCH",
                run_path,
                payload={
                    "status": "succeeded",
                    "backtest_id": run_id,
                    "artifacts_prefix": prefix,
                    "finished_at": _now(),
                    "lease_expires_at": None,
                    "error": None,
                    "updated_at": _now(),
                },
                prefer="return=minimal",
            )
        print(f"run {run_id}: succeeded; report and price snapshot stored privately")
        return True
    # The worker boundary must persist unexpected engine/data failures as a terminal run state.
    except Exception as error:
        api.request(
            "PATCH",
            run_path,
            payload={
                "status": "failed",
                "finished_at": _now(),
                "lease_expires_at": None,
                "error": _safe_error(error),
                "updated_at": _now(),
            },
            prefer="return=minimal",
        )
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    try:
        run_id = validate_run_id(args.run_id)
        api = SupabaseREST(
            os.environ.get("SUPABASE_URL", ""),
            os.environ.get("SUPABASE_SECRET_KEY", ""),
        )
        process_run(run_id, api=api, project_root=Path.cwd())
        return 0
    # Surface any failed invocation as a non-zero Actions job.
    except Exception as error:  # noqa: BLE001
        print(f"backtest worker failed: {_safe_error(error)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
