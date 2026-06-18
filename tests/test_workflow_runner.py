from __future__ import annotations

import fcntl
from pathlib import Path

from takterra_agent.core.workflow_runner import WorkflowRunner


def test_workflow_runner_runs_read_only_handler(tmp_path: Path) -> None:
    calls: list[dict] = []

    def handler(task, data_dir, credentials, inputs):  # type: ignore[no-untyped-def]
        calls.append(
            {
                "task": task.name,
                "data_dir": data_dir,
                "credentials": credentials,
                "inputs": inputs,
            }
        )
        return {
            "run_id": "daily_test",
            "overall_status": "warning",
            "artifacts": {"report": str(data_dir / "runs" / "report.md")},
        }

    runner = WorkflowRunner(
        data_dir=tmp_path,
        lock_dir=tmp_path / "locks",
        credentials=object(),  # type: ignore[arg-type]
        handlers={"daily-morning-report": handler},
    )

    result = runner.run_read_only("daily-morning-report", inputs={"seller_v3": True})

    assert result.ok is True
    assert result.status == "warning"
    assert result.task == "daily-morning-report"
    assert result.artifacts["report"].endswith("report.md")
    assert calls[0]["task"] == "daily-morning-report"
    assert calls[0]["data_dir"] == tmp_path
    assert calls[0]["inputs"] == {"seller_v3": True}


def test_workflow_runner_blocks_non_read_only_task(tmp_path: Path) -> None:
    runner = WorkflowRunner(data_dir=tmp_path, lock_dir=tmp_path / "locks", handlers={})

    result = runner.run_read_only("ozon-elastic-apply")

    assert result.ok is False
    assert result.status == "blocked"
    assert result.blocked_reason == "not_read_only"
    assert result.mode == "apply"


def test_workflow_runner_blocks_read_only_task_without_handler(tmp_path: Path) -> None:
    runner = WorkflowRunner(data_dir=tmp_path, lock_dir=tmp_path / "locks", handlers={})

    result = runner.run_read_only("catalog-fetch")

    assert result.ok is False
    assert result.status == "blocked"
    assert result.blocked_reason == "unsupported_workflow"


def test_workflow_runner_reports_busy_lock(tmp_path: Path) -> None:
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    lock_path = lock_dir / "daily-morning-report.lock"
    with lock_path.open("a+", encoding="utf-8") as locked:
        fcntl.flock(locked.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        runner = WorkflowRunner(
            data_dir=tmp_path,
            lock_dir=lock_dir,
            credentials=object(),  # type: ignore[arg-type]
            handlers={"daily-morning-report": lambda *args, **kwargs: {"overall_status": "ok"}},
        )
        result = runner.run_read_only("daily-morning-report")

    assert result.ok is False
    assert result.status == "blocked"
    assert result.blocked_reason == "workflow_busy"


def test_workflow_runner_sanitizes_handler_errors(tmp_path: Path) -> None:
    def handler(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("token and client_secret leaked in raw exception")

    runner = WorkflowRunner(
        data_dir=tmp_path,
        lock_dir=tmp_path / "locks",
        credentials=object(),  # type: ignore[arg-type]
        handlers={"daily-morning-report": handler},
    )

    result = runner.run_read_only("daily-morning-report")

    assert result.ok is False
    assert result.status == "error"
    assert result.blocked_reason == "workflow_failed"
    assert "token" not in result.error
    assert "client_secret" not in result.error
    assert "<redacted>" in result.error
