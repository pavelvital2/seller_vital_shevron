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


def test_job_service_waits_confirmation_for_apply_task(tmp_path: Path) -> None:
    service = JobService(store=JobStore(tmp_path / "runtime.db"), data_dir=tmp_path / "data")

    job = service.submit(task_id="ozon-elastic-apply", params={"plan_run_id": "x"})
    result = service.run(job.job_id)

    assert result.ok is False
    assert result.status == "waiting_confirmation"
    assert result.job.status == "waiting_confirmation"
    assert "confirmed_by_user" in result.job.error


def test_job_service_runs_confirmed_apply_workflow(tmp_path: Path) -> None:
    def handler(task, data_dir, credentials, inputs):  # type: ignore[no-untyped-def]
        assert task.name == "approved-cards-batch-apply"
        assert inputs["confirmed_by_user"] is True
        assert inputs["approval_id"].startswith("runtime:approved-cards-batch-apply:")
        assert inputs["approval_checksum"].startswith("sha256:")
        return {"run_id": "apply_job_test", "overall_status": "ok", "artifacts": {"report": str(data_dir / "runs/report.md")}}

    store = JobStore(tmp_path / "runtime.db")
    service = JobService(
        store=store,
        workflow_runner=WorkflowRunner(
            data_dir=tmp_path / "data",
            lock_dir=tmp_path / "locks",
            credentials=object(),  # type: ignore[arg-type]
            handlers={"approved-cards-batch-apply": handler},
        ),
        data_dir=tmp_path / "data",
    )
    job = service.submit(
        task_id="apply-approved-cards",
        params={"confirmed_by_user": True, "internal_skus": ["sku-1"]},
    )

    result = service.run(job.job_id)

    assert result.ok is True
    assert result.job.status == "success"
    assert result.job.result["summary"]["run_id"] == "apply_job_test"
    assert store.acquire_resource_lease(resource_key="marketplace:ozon", owner_id="job_after", ttl_seconds=60) is not None
    approval = store.get_approval(job.params["approval_id"])
    assert approval is not None
    assert approval.status == "applied"
    assert approval.checksum == job.params["approval_checksum"]


def test_job_service_auto_attaches_runtime_approval_to_confirmed_apply_submit(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "runtime.db")
    service = JobService(store=store, data_dir=tmp_path / "data")

    job = service.submit(
        task_id="ozon-elastic-apply",
        params={"confirmed_by_user": True, "plan_run_id": "elastic_plan_1"},
        actor="telegram_owner",
        source="telegram_callback",
    )
    approval = store.get_approval(job.params["approval_id"])

    assert job.params["approval_id"].startswith("runtime:ozon-elastic-apply:")
    assert job.params["approval_checksum"].startswith("sha256:")
    assert approval is not None
    assert approval.status == "approved"
    assert approval.source_job_id == job.job_id
    assert approval.checksum == job.params["approval_checksum"]
    assert approval.data["source_kind"] == "plan_run_id"
    assert [event.event_type for event in store.list_events(job.job_id)] == [
        "job_created",
        "job_runtime_approval_attached",
        "job_queued",
    ]


def test_job_service_reserves_runtime_approval_for_confirmed_apply(tmp_path: Path) -> None:
    def handler(task, data_dir, credentials, inputs):  # type: ignore[no-untyped-def]
        return {"run_id": "apply_job_test", "overall_status": "ok", "artifacts": {}}

    store = JobStore(tmp_path / "runtime.db")
    store.create_approval(
        approval_id="approval_1",
        source_job_id="source_job",
        status="approved",
        checksum="sha256:approval",
    )
    service = JobService(
        store=store,
        workflow_runner=WorkflowRunner(
            data_dir=tmp_path / "data",
            lock_dir=tmp_path / "locks",
            credentials=object(),  # type: ignore[arg-type]
            handlers={"approved-cards-batch-apply": handler},
        ),
        data_dir=tmp_path / "data",
    )
    job = service.submit(
        task_id="apply-approved-cards",
        params={
            "confirmed_by_user": True,
            "internal_skus": ["sku-1"],
            "approval_id": "approval_1",
            "approval_checksum": "sha256:approval",
        },
    )

    result = service.run(job.job_id)
    approval = store.get_approval("approval_1")

    assert result.ok is True
    assert approval is not None
    assert approval.status == "applied"
    assert approval.owner_job_id == job.job_id
    assert "job_approval_reserved" in [event.event_type for event in store.list_events(job.job_id)]


def test_job_service_blocks_apply_on_runtime_approval_checksum_mismatch(tmp_path: Path) -> None:
    def fail_handler(task, data_dir, credentials, inputs):  # type: ignore[no-untyped-def]
        raise AssertionError("workflow must not start with checksum mismatch")

    store = JobStore(tmp_path / "runtime.db")
    store.create_approval(
        approval_id="approval_1",
        source_job_id="source_job",
        status="approved",
        checksum="sha256:approval",
    )
    service = JobService(
        store=store,
        workflow_runner=WorkflowRunner(
            data_dir=tmp_path / "data",
            lock_dir=tmp_path / "locks",
            credentials=object(),  # type: ignore[arg-type]
            handlers={"approved-cards-batch-apply": fail_handler},
        ),
        data_dir=tmp_path / "data",
    )
    job = service.submit(
        task_id="apply-approved-cards",
        params={
            "confirmed_by_user": True,
            "internal_skus": ["sku-1"],
            "approval_id": "approval_1",
            "approval_checksum": "sha256:wrong",
        },
    )

    result = service.run(job.job_id)
    approval = store.get_approval("approval_1")

    assert result.ok is False
    assert result.status == "approval_checksum_mismatch"
    assert result.job.status == "failed"
    assert approval is not None
    assert approval.status == "approved"
    assert "job_approval_checksum_mismatch" in [event.event_type for event in store.list_events(job.job_id)]


def test_job_service_blocks_confirmed_apply_when_resource_lease_is_busy(tmp_path: Path) -> None:
    def fail_handler(task, data_dir, credentials, inputs):  # type: ignore[no-untyped-def]
        raise AssertionError("workflow must not start while resource lease is busy")

    store = JobStore(tmp_path / "runtime.db")
    assert store.acquire_resource_lease(resource_key="marketplace:ozon", owner_id="other_job", ttl_seconds=60) is not None
    service = JobService(
        store=store,
        workflow_runner=WorkflowRunner(
            data_dir=tmp_path / "data",
            lock_dir=tmp_path / "locks",
            credentials=object(),  # type: ignore[arg-type]
            handlers={"ozon-elastic-apply": fail_handler},
        ),
        data_dir=tmp_path / "data",
    )
    job = service.submit(task_id="ozon-elastic-apply", params={"confirmed_by_user": True, "plan_run_id": "plan_1"})

    result = service.run(job.job_id)

    assert result.ok is False
    assert result.status == "resource_locked"
    assert result.job.status == "queued"
    assert [event.event_type for event in store.list_events(job.job_id)] == [
        "job_created",
        "job_runtime_approval_attached",
        "job_queued",
        "job_resource_blocked",
    ]


def test_job_service_blocks_repeated_auto_runtime_approval_apply(tmp_path: Path) -> None:
    def handler(task, data_dir, credentials, inputs):  # type: ignore[no-untyped-def]
        return {"run_id": "apply_job_test", "overall_status": "ok", "artifacts": {}}

    store = JobStore(tmp_path / "runtime.db")
    service = JobService(
        store=store,
        workflow_runner=WorkflowRunner(
            data_dir=tmp_path / "data",
            lock_dir=tmp_path / "locks",
            credentials=object(),  # type: ignore[arg-type]
            handlers={"ozon-elastic-apply": handler},
        ),
        data_dir=tmp_path / "data",
    )
    first = service.submit(task_id="ozon-elastic-apply", params={"confirmed_by_user": True, "plan_run_id": "plan_1"})
    first_result = service.run(first.job_id)
    second = service.submit(task_id="ozon-elastic-apply", params={"confirmed_by_user": True, "plan_run_id": "plan_1"})
    second_result = service.run(second.job_id)

    assert first_result.ok is True
    assert second.params["approval_id"] == first.params["approval_id"]
    assert second_result.ok is False
    assert second_result.status == "approval_not_available"
    approval = store.get_approval(first.params["approval_id"])
    assert approval is not None
    assert approval.status == "applied"
    assert approval.owner_job_id == first.job_id


def test_job_service_recovery_runs_safe_verify_job_for_unknown_apply(tmp_path: Path) -> None:
    def apply_handler(task, data_dir, credentials, inputs):  # type: ignore[no-untyped-def]
        return {"run_id": "apply_job_test", "overall_status": "error", "artifacts": {}}

    def verify_handler(task, data_dir, credentials, inputs):  # type: ignore[no-untyped-def]
        assert task.name == "card-content-update-verify"
        assert inputs["internal_skus"] == ["sku-1"]
        assert "confirmed_by_user" not in inputs
        assert inputs["runtime_recovery_apply_task"] == "approved-cards-batch-apply"
        return {"run_id": "verify_job_test", "overall_status": "ok", "artifacts": {}}

    store = JobStore(tmp_path / "runtime.db")
    service = JobService(
        store=store,
        workflow_runner=WorkflowRunner(
            data_dir=tmp_path / "data",
            lock_dir=tmp_path / "locks",
            credentials=object(),  # type: ignore[arg-type]
            handlers={
                "approved-cards-batch-apply": apply_handler,
                "card-content-update-verify": verify_handler,
            },
        ),
        data_dir=tmp_path / "data",
    )
    job = service.submit(
        task_id="apply-approved-cards",
        params={"confirmed_by_user": True, "internal_skus": ["sku-1"]},
    )

    apply_result = service.run(job.job_id)
    recovery = service.recover_runtime_approvals(run_verify=True)
    approval = store.get_approval(job.params["approval_id"])

    assert apply_result.ok is False
    assert approval is not None
    assert approval.status == "verified"
    assert recovery["overall_status"] == "ok"
    assert recovery["queued_verify_jobs"] == 1
    assert recovery["rows"][0]["action"] == "verify_job_ran"
    assert recovery["rows"][0]["approval_status_after"] == "verified"


def test_job_service_recovery_does_not_close_approval_on_verify_warning(tmp_path: Path) -> None:
    def verify_handler(task, data_dir, credentials, inputs):  # type: ignore[no-untyped-def]
        return {"run_id": "verify_warning", "overall_status": "warning", "artifacts": {}}

    store = JobStore(tmp_path / "runtime.db")
    store.create_approval(
        approval_id="approval_warning",
        source_job_id="job_source",
        status="applying_unknown",
        data={
            "task_id": "approved-cards-batch-apply",
            "apply_params": {"internal_skus": ["sku-1"], "confirmed_by_user": True},
        },
    )
    service = JobService(
        store=store,
        workflow_runner=WorkflowRunner(
            data_dir=tmp_path / "data",
            lock_dir=tmp_path / "locks",
            credentials=object(),  # type: ignore[arg-type]
            handlers={"card-content-update-verify": verify_handler},
        ),
        data_dir=tmp_path / "data",
    )

    recovery = service.recover_runtime_approvals(run_verify=True)
    approval = store.get_approval("approval_warning")

    assert recovery["overall_status"] == "warning"
    assert recovery["failed_verify_jobs"] == 1
    assert recovery["rows"][0]["verify_result_status"] == "warning"
    assert recovery["rows"][0]["verify_ok"] is False
    assert approval is not None
    assert approval.status == "applying_unknown"


def test_job_service_recovery_does_not_verify_while_apply_job_is_active(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "runtime.db")
    owner_job = store.create_job(
        task_id="approved-cards-batch-apply",
        params={"confirmed_by_user": True, "internal_skus": ["sku-1"]},
        status="running",
    )
    store.create_approval(
        approval_id="approval_active_apply",
        source_job_id=owner_job.job_id,
        status="approved",
        data={
            "task_id": "approved-cards-batch-apply",
            "apply_params": {"internal_skus": ["sku-1"], "confirmed_by_user": True},
        },
    )
    store.reserve_approval_for_apply(approval_id="approval_active_apply", owner_job_id=owner_job.job_id)
    service = JobService(store=store, data_dir=tmp_path / "data")

    recovery = service.recover_runtime_approvals(run_verify=True)

    assert recovery["overall_status"] == "warning"
    assert recovery["queued_verify_jobs"] == 0
    assert recovery["manual_verify_required"] == 1
    assert recovery["rows"][0]["blocked_reason"] == "apply_job_still_active"
    assert recovery["rows"][0]["owner_job_status"] == "running"
    assert store.get_approval("approval_active_apply").status == "applying"  # type: ignore[union-attr]


def test_job_service_recovery_requires_manual_verify_for_disabled_legacy_apply(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "runtime.db")
    store.create_approval(
        approval_id="approval_1",
        source_job_id="job_source",
        status="applying_unknown",
        data={
            "task_id": "actions-apply",
            "apply_params": {"pending_id": "legacy_actions_1", "confirmed_by_user": True},
        },
    )
    service = JobService(store=store, data_dir=tmp_path / "data")

    recovery = service.recover_runtime_approvals(run_verify=True)

    assert recovery["overall_status"] == "warning"
    assert recovery["queued_verify_jobs"] == 0
    assert recovery["manual_verify_required"] == 1
    assert recovery["rows"][0]["action"] == "manual_verify_required"
    assert recovery["rows"][0]["blocked_reason"] == "apply_task_disabled"
    assert store.get_approval("approval_1").status == "applying_unknown"  # type: ignore[union-attr]


def test_job_service_rejects_disabled_legacy_task(tmp_path: Path) -> None:
    service = JobService(store=JobStore(tmp_path / "runtime.db"), data_dir=tmp_path / "data")

    try:
        service.submit(task_id="actions-apply", params={"confirmed_by_user": True})
    except ValueError as exc:
        assert "disabled" in str(exc)
    else:
        raise AssertionError("disabled legacy task must not be submitted")


def test_job_service_can_cancel_queued_job(tmp_path: Path) -> None:
    service = JobService(store=JobStore(tmp_path / "runtime.db"), data_dir=tmp_path / "data")
    job = service.submit(task_id="pricing-status")

    result = service.cancel(job.job_id, reason="owner_sleep")

    assert result.ok is True
    assert result.job.status == "cancelled"
    assert result.job.error == "owner_sleep"


def test_job_service_can_cancel_job_waiting_for_confirmation(tmp_path: Path) -> None:
    service = JobService(store=JobStore(tmp_path / "runtime.db"), data_dir=tmp_path / "data")
    job = service.submit(task_id="ozon-elastic-apply", params={"plan_run_id": "plan-1"})
    waiting = service.run(job.job_id)

    result = service.cancel(job.job_id, reason="owner_declined")

    assert waiting.job.status == "waiting_confirmation"
    assert result.ok is True
    assert result.job.status == "cancelled"
    assert result.job.error == "owner_declined"


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
