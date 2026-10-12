from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from scripts.run_backtest_worker import process_run, validate_run_id


class FakeSupabaseREST:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.uploads: dict[str, bytes] = {}
        self.requests: list[tuple[str, str, Any]] = []

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        self.requests.append((method, path, kwargs.get("payload")))
        if path == "rest/v1/rpc/claim_mrq_backtest_run":
            return [
                {
                    "run_id": self.run_id,
                    "request_json": {
                        "strategy": {"name": "test", "weights": {"SPY": 1.0}},
                        "strategy_version": None,
                    },
                }
            ]
        return None

    def upload(self, object_path: str, content: bytes, content_type: str) -> None:
        self.uploads[object_path] = content


class BacktestWorkerTests(unittest.TestCase):
    def test_run_id_rejects_path_traversal(self) -> None:
        with self.assertRaisesRegex(ValueError, "24 lowercase hexadecimal"):
            validate_run_id("bt_../../report.json")


    def test_worker_uploads_artifacts_and_marks_run_succeeded(self) -> None:
        run_id = "bt_0123456789abcdef01234567"
        api = FakeSupabaseREST(run_id)

        def executor(
            _prepared: dict[str, Any],
            _project_root: str | Path,
            state_root: str | Path,
            current_run_id: str,
        ) -> dict[str, Any]:
            report = {
                "backtest_id": current_run_id,
                "strategy": {"name": "test", "weights": {"SPY": 1.0}},
                "metrics": {"cagr": 0.05},
                "period": {"actual_start": "2020-01-01", "actual_end": "2024-12-31"},
                "data": {"input_sha256": "a" * 64},
                "generated_at": "2025-01-01T00:00:00+00:00",
            }
            run_dir = Path(state_root) / "backtests" / current_run_id
            run_dir.mkdir(parents=True)
            (run_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
            (run_dir / "report.md").write_text("# Test report\n", encoding="utf-8")
            (run_dir / "price_snapshot.csv").write_text(
                "date,SPY\n2020-01-01,100\n", encoding="utf-8"
            )
            return report

        with tempfile.TemporaryDirectory() as temp_dir:
            completed = process_run(
                run_id,
                api=api,
                executor=executor,
                project_root=temp_dir,
                worker_id="test-run-1",
            )

        self.assertTrue(completed)
        self.assertEqual(
            set(api.uploads),
            {
                f"{run_id}/report.json",
                f"{run_id}/report.md",
                f"{run_id}/price_snapshot.csv",
            },
        )
        report_writes = [
            payload for method, path, payload in api.requests
            if method == "POST" and path.startswith("rest/v1/mrq_backtest_reports")
        ]
        self.assertEqual(report_writes[0]["metrics"], {"cagr": 0.05})
        status_writes = [
            payload for method, path, payload in api.requests
            if method == "PATCH" and path.startswith("rest/v1/mrq_backtest_runs?")
        ]
        self.assertEqual(status_writes[-1]["status"], "succeeded")


if __name__ == "__main__":
    unittest.main()
