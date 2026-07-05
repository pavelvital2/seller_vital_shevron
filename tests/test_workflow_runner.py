from __future__ import annotations

import csv
import fcntl
from pathlib import Path

import seller_agent.core.workflow_runner as workflow_runner
from seller_agent.core.workflow_runner import WorkflowRunner


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


def test_workflow_runner_runs_pricing_status_default_handler(tmp_path: Path) -> None:
    products_path = tmp_path / "products.csv"
    with products_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "internal_product_id",
                "internal_sku",
                "product_name",
                "mapping_status",
                "pack_qty",
                "cost_total",
                "active_ozon",
                "active_wb",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "internal_product_id": "chev_nr_svo_pict0001",
                "internal_sku": "chev_nr_svo_pict0001",
                "product_name": "Шеврон СВО",
                "mapping_status": "confirmed",
                "pack_qty": "1",
                "cost_total": "85",
                "active_ozon": "false",
                "active_wb": "false",
            }
        )

    runner = WorkflowRunner(data_dir=tmp_path / "data", lock_dir=tmp_path / "locks")
    result = runner.run_read_only(
        "pricing-status",
        inputs={"products_path": str(products_path), "run_id": "pricing_status_workflow_test"},
    )

    assert result.ok is True
    assert result.task == "pricing-status"
    assert result.summary["summary"]["rows"] == 1
    assert result.artifacts["status_csv"].endswith("pricing_status.csv")


def test_workflow_runner_runs_wb_inbox_apply_default_handler(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    calls: list[dict] = []

    def fake_apply(**kwargs):  # type: ignore[no-untyped-def]
        calls.append(kwargs)
        return {
            "run_id": "wb_inbox_test_apply",
            "overall_status": "ok",
            "source_run_id": kwargs["source_run_id"],
            "artifacts": {"report": str(tmp_path / "report.md")},
        }

    monkeypatch.setattr(workflow_runner, "run_wb_inbox_apply", fake_apply)

    runner = WorkflowRunner(
        data_dir=tmp_path / "data",
        lock_dir=tmp_path / "locks",
        credentials=object(),  # type: ignore[arg-type]
    )
    result = runner.run_task(
        "wb-inbox-apply",
        inputs={"source_run_id": "wb_inbox_test", "confirmed_by_user": True},
        allowed_modes={"apply"},
    )

    assert result.ok is True
    assert result.task == "wb-inbox-apply"
    assert result.artifacts["report"].endswith("report.md")
    assert calls[0]["source_run_id"] == "wb_inbox_test"
    assert calls[0]["confirmed_by_user"] is True
