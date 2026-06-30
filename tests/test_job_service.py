from __future__ import annotations

import csv
import json
from pathlib import Path

from seller_agent.cli import main
from seller_agent.core.job_runner import JobRunner
from seller_agent.core.job_service import JobService
from seller_agent.core.job_store import JobStore
from seller_agent.core.workflow_runner import WorkflowRunner


def test_job_service_submits_and_runs_read_only_workflow(tmp_path: Path) -> None:
    def handler(task, data_dir, credentials, inputs):  # type: ignore[no-untyped-def]
        return {
            "run_id": inputs["run_id"],
            "overall_status": "ok",
            "artifacts": {"report": str(data_dir / "runs" / "report.md")},
        }

    store = JobStore(tmp_path / "runtime.db")
    workflow_runner = WorkflowRunner(
        data_dir=tmp_path / "data",
        lock_dir=tmp_path / "locks",
        handlers={"pricing-status": handler},
    )
    service = JobService(store=store, workflow_runner=workflow_runner, data_dir=tmp_path / "data")

    job = service.submit(
        task_id="pricing-status",
        params={"run_id": "pricing_job_test"},
        actor="telegram:42",
        source="telegram",
    )
    result = service.run(job.job_id)

    assert job.status == "queued"
    assert result.ok is True
    assert result.job.status == "success"
    assert result.job.result["summary"]["run_id"] == "pricing_job_test"

    events = store.list_events(job.job_id)
    assert [event.event_type for event in events] == [
        "job_created",
        "job_queued",
        "job_running",
        "job_success",
    ]


def test_job_service_blocks_apply_task_in_v1(tmp_path: Path) -> None:
    service = JobService(store=JobStore(tmp_path / "runtime.db"), data_dir=tmp_path / "data")

    job = service.submit(task_id="ozon-elastic-apply", params={"plan_run_id": "x"})
    result = service.run(job.job_id)

    assert result.ok is False
    assert result.status == "blocked"
    assert result.job.status == "failed"
    assert "read-only" in result.job.error


def test_job_service_can_cancel_queued_job(tmp_path: Path) -> None:
    service = JobService(store=JobStore(tmp_path / "runtime.db"), data_dir=tmp_path / "data")
    job = service.submit(task_id="pricing-status")

    result = service.cancel(job.job_id, reason="owner_sleep")

    assert result.ok is True
    assert result.job.status == "cancelled"
    assert result.job.error == "owner_sleep"


def test_job_runner_runs_next_queued_job(tmp_path: Path) -> None:
    def handler(task, data_dir, credentials, inputs):  # type: ignore[no-untyped-def]
        return {"run_id": "status_job_test", "overall_status": "warning", "artifacts": {}}

    store = JobStore(tmp_path / "runtime.db")
    service = JobService(
        store=store,
        workflow_runner=WorkflowRunner(
            data_dir=tmp_path / "data",
            lock_dir=tmp_path / "locks",
            credentials=object(),  # type: ignore[arg-type]
            handlers={"status-preflight": handler},
        ),
        data_dir=tmp_path / "data",
    )
    runner = JobRunner(service)
    job = service.submit(task_id="status-preflight")

    result = runner.run_next()
    empty = runner.run_next()

    assert result.ran is True
    assert result.job is not None
    assert result.job.job_id == job.job_id
    assert result.job.status == "partial_success"
    assert empty.ran is False
    assert empty.ok is True


def test_cli_jobs_submit_show_cancel_and_run(
    tmp_path: Path,
    capsys,  # type: ignore[no-untyped-def]
) -> None:
    runtime_db = tmp_path / "runtime.db"

    assert main(["jobs", "list", "--runtime-db", str(runtime_db)]) == 0
    empty_list_output = json.loads(capsys.readouterr().out)
    assert empty_list_output["rows"] == []

    assert main(["jobs", "submit", "--runtime-db", str(runtime_db), "--task", "pricing-status"]) == 0
    submit_output = json.loads(capsys.readouterr().out)
    job_id = submit_output["job"]["job_id"]
    assert submit_output["job"]["status"] == "queued"

    assert main(["jobs", "list", "--runtime-db", str(runtime_db)]) == 0
    list_output = json.loads(capsys.readouterr().out)
    assert [row["job_id"] for row in list_output["rows"]] == [job_id]

    assert main(["jobs", "show", "--runtime-db", str(runtime_db), "--job-id", job_id]) == 0
    show_output = json.loads(capsys.readouterr().out)
    assert show_output["job"]["job_id"] == job_id
    assert [event["event_type"] for event in show_output["events"]] == ["job_created", "job_queued"]

    assert main(["jobs", "cancel", "--runtime-db", str(runtime_db), "--job-id", job_id, "--reason", "test"]) == 0
    cancel_output = json.loads(capsys.readouterr().out)
    assert cancel_output["job"]["status"] == "cancelled"

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
                "internal_product_id": "chev_nr_test_pict0001",
                "internal_sku": "chev_nr_test_pict0001",
                "product_name": "Шеврон тест",
                "mapping_status": "confirmed",
                "pack_qty": "1",
                "cost_total": "85",
                "active_ozon": "false",
                "active_wb": "false",
            }
        )

    params = json.dumps({"products_path": str(products_path), "run_id": "pricing_job_cli_test"}, ensure_ascii=False)
    assert (
        main(
            [
                "jobs",
                "submit",
                "--runtime-db",
                str(runtime_db),
                "--data-dir",
                str(tmp_path / "data"),
                "--task",
                "pricing-status",
                "--params-json",
                params,
            ]
        )
        == 0
    )
    run_job_id = json.loads(capsys.readouterr().out)["job"]["job_id"]
    assert (
        main(
            [
                "jobs",
                "run",
                "--runtime-db",
                str(runtime_db),
                "--data-dir",
                str(tmp_path / "data"),
                "--job-id",
                run_job_id,
            ]
        )
        == 0
    )
    run_output = json.loads(capsys.readouterr().out)
    assert run_output["ok"] is True
    assert run_output["job"]["status"] == "success"
