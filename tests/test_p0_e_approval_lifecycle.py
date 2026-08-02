from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import threading

import pytest

import seller_agent.core.job_service as job_service_module
from seller_agent.cli import main
from seller_agent.core.job_service import JobService
from seller_agent.core.job_store import JobStore
from seller_agent.core.workflow_runner import WorkflowRunner
from seller_agent.safety.approval_package import build_approval_package
from seller_agent.safety.approvals import canonical_checksum
from seller_agent.tasks.registry import TaskRegistry, default_task_registry


def _create_approval(
    store: JobStore,
    *,
    approval_id: str,
    status: str = "pending_review",
    source_run_id: str = "plan-source",
    apply_params: dict[str, object] | None = None,
):
    task = default_task_registry().get("ozon-elastic-apply")
    package = build_approval_package(
        approval_id=approval_id,
        task_id=task.name,
        source_plan_task=task.source_plan_task,
        verify_task=task.verify_task,
        source_kind="plan_run_id",
        source_ref=json.dumps(source_run_id),
        apply_params=apply_params or {"plan_run_id": source_run_id},
        marketplaces=task.marketplaces,
    )
    return store.create_approval(
        approval_id=approval_id,
        source_job_id=f"source:{approval_id}",
        status=status,  # type: ignore[arg-type]
        checksum=package["approval_checksum"],
        data=package,
    )


def _service(
    tmp_path: Path,
    store: JobStore,
    *,
    apply_handler=None,  # type: ignore[no-untyped-def]
    verify_handler=None,  # type: ignore[no-untyped-def]
    registry: TaskRegistry | None = None,
) -> JobService:
    task_registry = registry or default_task_registry()
    handlers = {}
    if apply_handler is not None:
        handlers["ozon-elastic-apply"] = apply_handler
    if verify_handler is not None:
        handlers["ozon-elastic-verify"] = verify_handler
    return JobService(
        store=store,
        workflow_runner=WorkflowRunner(
            registry=task_registry,
            data_dir=tmp_path / "data",
            lock_dir=tmp_path / "locks",
            handlers=handlers,
        ),
        registry=task_registry,
        data_dir=tmp_path / "data",
    )


def _replace_approval_package(
    db_path: Path,
    approval_id: str,
    package: dict[str, object],
    *,
    record_checksum: str | None = None,
) -> None:
    with sqlite3.connect(db_path) as connection:
        if record_checksum is None:
            connection.execute(
                "UPDATE approvals SET data_json = ? WHERE approval_id = ?",
                (
                    json.dumps(
                        package,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    approval_id,
                ),
            )
        else:
            connection.execute(
                "UPDATE approvals SET data_json = ?, checksum = ? WHERE approval_id = ?",
                (
                    json.dumps(
                        package,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    record_checksum,
                    approval_id,
                ),
            )


def _signed_checksum(package: dict[str, object]) -> str:
    return "sha256:" + canonical_checksum(
        {key: value for key, value in package.items() if key != "approval_checksum"}
    )


def _verify_policy_registry(
    *,
    apply_enabled: bool = True,
    verify_enabled: bool = True,
    verify_mode: str = "verify",
) -> TaskRegistry:
    defaults = default_task_registry()
    registry = TaskRegistry()
    registry.register(
        replace(defaults.get("ozon-elastic-apply"), enabled=apply_enabled)
    )
    registry.register(
        replace(
            defaults.get("ozon-elastic-verify"),
            enabled=verify_enabled,
            mode=verify_mode,  # type: ignore[arg-type]
        )
    )
    return registry


def test_explicit_apply_then_evidenced_verify_closes_only_matching_approval_and_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apply_calls: list[str] = []
    verify_calls: list[str] = []
    manifest_calls: list[tuple[str, str, str]] = []
    store = JobStore(tmp_path / "runtime.db")
    approval = _create_approval(store, approval_id="approval-normal")
    unrelated = _create_approval(
        store,
        approval_id="approval-unrelated",
        status="applied",
        source_run_id="unrelated-plan",
    )

    def apply_handler(task, data_dir, credentials, inputs):  # type: ignore[no-untyped-def]
        apply_calls.append(inputs["approval_id"])
        return {
            "run_id": "apply-run",
            "overall_status": "ok",
            # Apply evidence cannot substitute for the separate verify action.
            "verification_confirmed": True,
            "artifacts": {},
        }

    def verify_handler(task, data_dir, credentials, inputs):  # type: ignore[no-untyped-def]
        verify_calls.append(inputs["approval_id"])
        assert "runtime_recovery_approval_id" not in inputs
        return {
            "run_id": "verify-run",
            "overall_status": "ok",
            "verification_confirmed": True,
            "artifacts": {},
        }

    def close_manifest(*, data_dir, run_id, applied_by_run_id):  # type: ignore[no-untyped-def]
        current = store.get_approval(approval.approval_id)
        manifest_calls.append((run_id, applied_by_run_id, current.status if current else ""))
        return True

    monkeypatch.setattr(job_service_module, "close_run_manifest", close_manifest)
    service = _service(
        tmp_path,
        store,
        apply_handler=apply_handler,
        verify_handler=verify_handler,
    )

    approved = service.approve(approval.approval_id)
    apply_job = service.submit_approval_apply(approval.approval_id)
    apply_result = service.run(apply_job.job_id)

    assert approved.status == "approved"
    assert apply_result.ok is True
    assert store.get_approval(approval.approval_id).status == "applied"  # type: ignore[union-attr]
    assert store.get_approval(unrelated.approval_id).status == "applied"  # type: ignore[union-attr]
    assert manifest_calls == []

    verify_job = service.submit_approval_verify(approval.approval_id)
    assert verify_job.params["approval_id"] == approval.approval_id
    verify_result = service.run(verify_job.job_id)

    assert verify_result.ok is True
    assert apply_calls == [approval.approval_id]
    assert verify_calls == [approval.approval_id]
    assert store.get_approval(approval.approval_id).status == "closed"  # type: ignore[union-attr]
    assert store.get_approval(unrelated.approval_id).status == "applied"  # type: ignore[union-attr]
    assert manifest_calls == [("plan-source", "apply-run", "closed")]
    assert [event.event_type for event in store.list_approval_events(approval.approval_id)] == [
        "approval_pending_review_created",
        "approval_approved",
        "approval_apply_started",
        "approval_applied",
        "approval_verify_queued",
        "approval_verify_started",
        "approval_verified",
        "approval_closed",
        "approval_manifest_close_pending",
        "approval_manifest_closed",
    ]
    reconciliation = store.get_approval_manifest_reconciliation(approval.approval_id)
    assert reconciliation is not None
    assert reconciliation.status == "closed"
    assert reconciliation.source_run_id == "plan-source"
    assert reconciliation.attempt_count == 1


@pytest.mark.parametrize(
    ("summary", "expected_job_status"),
    [
        ({"overall_status": "warning", "verification_confirmed": True}, "partial_success"),
        ({"overall_status": "partial", "verification_confirmed": True}, "failed"),
        ({"overall_status": "unknown", "verification_confirmed": True}, "failed"),
        ({"overall_status": "error", "verification_confirmed": True}, "failed"),
        ({"overall_status": "ok"}, "success"),
    ],
)
def test_non_successful_or_missing_verify_evidence_keeps_approval_open_for_reverify(
    tmp_path: Path,
    summary: dict[str, object],
    expected_job_status: str,
) -> None:
    store = JobStore(tmp_path / "runtime.db")
    approval = _create_approval(store, approval_id="approval-open", status="applied")

    def verify_handler(task, data_dir, credentials, inputs):  # type: ignore[no-untyped-def]
        return {**summary, "run_id": "verify-open", "artifacts": {}}

    service = _service(tmp_path, store, verify_handler=verify_handler)
    first = service.submit_approval_verify(approval.approval_id)
    result = service.run(first.job_id)

    assert result.job.status == expected_job_status
    assert store.get_approval(approval.approval_id).status == "applied"  # type: ignore[union-attr]
    second = service.submit_approval_verify(approval.approval_id)
    assert second.job_id != first.job_id
    events = store.list_approval_events(approval.approval_id)
    assert [event.event_type for event in events].count("approval_verify_inconclusive") == 1


def test_applying_unknown_recovery_uses_canonical_approval_id_and_explicit_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = JobStore(tmp_path / "runtime.db")
    approval = _create_approval(
        store,
        approval_id="approval-recovery",
        status="applying_unknown",
    )
    handler_calls: list[dict[str, object]] = []

    def verify_handler(task, data_dir, credentials, inputs):  # type: ignore[no-untyped-def]
        handler_calls.append(dict(inputs))
        return {
            "run_id": "verify-recovery",
            "overall_status": "ok",
            "verified": True,
            "artifacts": {},
        }

    monkeypatch.setattr(job_service_module, "close_run_manifest", lambda **kwargs: True)
    service = _service(tmp_path, store, verify_handler=verify_handler)
    recovery = service.recover_runtime_approvals(run_verify=True)

    assert recovery["overall_status"] == "ok"
    assert recovery["queued_verify_jobs"] == 1
    assert recovery["rows"][0]["approval_status_after"] == "closed"
    assert handler_calls[0]["approval_id"] == approval.approval_id
    assert "runtime_recovery_approval_id" not in handler_calls[0]
    assert store.get_approval(approval.approval_id).status == "closed"  # type: ignore[union-attr]


def test_signed_verify_params_are_normalized_to_one_canonical_approval_id_for_normal_and_recovery(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "runtime.db"
    store = JobStore(db_path)
    approvals = [
        _create_approval(
            store,
            approval_id="approval-canonical-normal",
            status="applied",
        ),
        _create_approval(
            store,
            approval_id="approval-canonical-recovery",
            status="applying_unknown",
        ),
    ]
    for approval in approvals:
        package = dict(approval.data)
        package["verify_params"] = {
            "business_probe": "kept-for-safe-verify",
            "approval_id": "conflicting-current-approval",
            "runtime_recovery_approval_id": "conflicting-recovery-approval",
            "runtime_approval_id": "conflicting-runtime-approval",
            "approval_checksum": "conflicting-current-checksum",
            "runtime_recovery_approval_checksum": "conflicting-recovery-checksum",
            "runtime_approval_checksum": "conflicting-runtime-checksum",
            "confirmed_by_user": True,
        }
        package["approval_checksum"] = _signed_checksum(package)
        _replace_approval_package(
            db_path,
            approval.approval_id,
            package,
            record_checksum=str(package["approval_checksum"]),
        )

    handler_calls: list[dict[str, object]] = []

    def verify_handler(task, data_dir, credentials, inputs):  # type: ignore[no-untyped-def]
        handler_calls.append(dict(inputs))
        return {
            "run_id": "verify-canonical-only",
            "overall_status": "warning",
            "artifacts": {},
        }

    service = _service(tmp_path, store, verify_handler=verify_handler)
    normal_job = service.submit_approval_verify(approvals[0].approval_id)
    service.run(normal_job.job_id)
    recovery = service.recover_runtime_approvals(
        statuses=("applying_unknown",),
        run_verify=True,
    )

    assert recovery["queued_verify_jobs"] == 1
    assert len(handler_calls) == 2
    forbidden_transport = {
        "runtime_recovery_approval_id",
        "runtime_approval_id",
        "approval_checksum",
        "runtime_recovery_approval_checksum",
        "runtime_approval_checksum",
        "confirmed_by_user",
    }
    for inputs, approval in zip(handler_calls, approvals, strict=True):
        assert inputs["approval_id"] == approval.approval_id
        assert inputs["business_probe"] == "kept-for-safe-verify"
        assert forbidden_transport.isdisjoint(inputs)
        assert [key for key in inputs if "approval_id" in key] == ["approval_id"]


def test_concurrent_duplicate_verify_keeps_one_active_job(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    first_store = JobStore(db_path)
    approval = _create_approval(first_store, approval_id="approval-dedup", status="applied")
    first_service = _service(tmp_path / "first", first_store)
    second_service = _service(tmp_path / "second", JobStore(db_path))
    barrier = threading.Barrier(2)

    def submit(service: JobService):
        barrier.wait(timeout=5)
        return service.submit_approval_verify(approval.approval_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(submit, first_service)
        second_future = pool.submit(submit, second_service)
        first = first_future.result(timeout=10)
        second = second_future.result(timeout=10)

    repeated = first_service.submit_approval_verify(approval.approval_id)
    assert first.job_id == second.job_id == repeated.job_id
    assert first.params["approval_id"] == approval.approval_id
    assert [job.job_id for job in first_store.list_jobs(task_id="ozon-elastic-verify")] == [
        first.job_id
    ]
    assert [
        event.event_type for event in first_store.list_approval_events(approval.approval_id)
    ].count("approval_verify_queued") == 1


def test_disabled_apply_task_does_not_block_normal_or_recovery_safe_verify(
    tmp_path: Path,
) -> None:
    store = JobStore(tmp_path / "runtime.db")
    normal = _create_approval(
        store,
        approval_id="approval-disabled-apply-normal",
        status="applied",
    )
    recovery = _create_approval(
        store,
        approval_id="approval-disabled-apply-recovery",
        status="applying_unknown",
    )
    service = _service(
        tmp_path,
        store,
        registry=_verify_policy_registry(apply_enabled=False),
    )

    normal_job = service.submit_approval_verify(normal.approval_id)
    recovery_result = service.recover_runtime_approvals(
        statuses=("applying_unknown",),
        run_verify=False,
    )

    assert normal_job.task_id == "ozon-elastic-verify"
    assert recovery_result["queued_verify_jobs"] == 1
    assert recovery_result["manual_verify_required"] == 0
    assert recovery_result["rows"][0]["approval_id"] == recovery.approval_id
    assert recovery_result["rows"][0]["verify_task"] == "ozon-elastic-verify"
    assert "verify_params" not in recovery_result["rows"][0]


@pytest.mark.parametrize(
    ("verify_enabled", "verify_mode", "expected_reason"),
    [
        (False, "verify", "verify_task_disabled"),
        (True, "read_only", "verify_task_mode_not_safe"),
        (True, "dry_run", "verify_task_mode_not_safe"),
    ],
)
def test_verify_task_must_be_enabled_and_strictly_verify_for_normal_and_recovery(
    tmp_path: Path,
    verify_enabled: bool,
    verify_mode: str,
    expected_reason: str,
) -> None:
    store = JobStore(tmp_path / "runtime.db")
    normal = _create_approval(
        store,
        approval_id=f"approval-invalid-verify-normal-{verify_enabled}-{verify_mode}",
        status="applied",
    )
    recovery = _create_approval(
        store,
        approval_id=f"approval-invalid-verify-recovery-{verify_enabled}-{verify_mode}",
        status="applying_unknown",
    )
    service = _service(
        tmp_path,
        store,
        registry=_verify_policy_registry(
            verify_enabled=verify_enabled,
            verify_mode=verify_mode,
        ),
    )

    with pytest.raises(RuntimeError, match=expected_reason):
        service.submit_approval_verify(normal.approval_id)
    recovery_result = service.recover_runtime_approvals(
        statuses=("applying_unknown",),
        run_verify=False,
    )

    assert recovery_result["queued_verify_jobs"] == 0
    assert recovery_result["manual_verify_required"] == 1
    assert recovery_result["rows"][0]["approval_id"] == recovery.approval_id
    assert recovery_result["rows"][0]["blocked_reason"] == expected_reason
    assert store.list_jobs(limit=10) == []


def test_active_duplicate_verify_rejects_current_package_snapshot_mismatch(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "runtime.db"
    store = JobStore(db_path)
    approval = _create_approval(
        store,
        approval_id="approval-active-snapshot-mismatch",
        status="applied",
    )
    service = _service(tmp_path, store)
    active = service.submit_approval_verify(approval.approval_id)
    package = dict(approval.data)
    package["actions"] = [{"note": "secret-active-snapshot-value"}]
    package["approval_checksum"] = _signed_checksum(package)
    _replace_approval_package(
        db_path,
        approval.approval_id,
        package,
        record_checksum=str(package["approval_checksum"]),
    )

    with pytest.raises(RuntimeError) as error:
        service.submit_approval_verify(approval.approval_id)

    assert "approval_package_changed_after_verify_authorization" in str(error.value)
    assert "secret-active-snapshot-value" not in str(error.value)
    assert store.get_approval(approval.approval_id).status == "applied"  # type: ignore[union-attr]
    assert [job.job_id for job in store.list_jobs(task_id="ozon-elastic-verify")] == [
        active.job_id
    ]


def test_generic_verify_submit_without_canonical_approval_is_blocked(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "runtime.db")
    service = _service(tmp_path, store)

    with pytest.raises(RuntimeError, match="approval.*submit_approval_verify"):
        service.submit(
            task_id="ozon-elastic-verify",
            params={"plan_run_id": "plan-without-approval"},
        )

    assert store.list_jobs(limit=10) == []


@pytest.mark.parametrize(
    ("mutation", "expected_issue", "forbidden_text"),
    [
        ("verify_task", "approval_package_checksum_invalid", "wb-actions-discount-verify"),
        ("package_data", "approval_package_checksum_invalid", "secret-business-value"),
        ("record_checksum", "approval_record_checksum_mismatch", "secret-record-checksum"),
    ],
)
def test_verify_rejects_tampered_package_or_record_with_sanitized_reason(
    tmp_path: Path,
    mutation: str,
    expected_issue: str,
    forbidden_text: str,
) -> None:
    db_path = tmp_path / "runtime.db"
    store = JobStore(db_path)
    approval = _create_approval(store, approval_id=f"approval-tampered-{mutation}", status="applied")
    package = dict(approval.data)
    record_checksum: str | None = None
    if mutation == "verify_task":
        package["verify_task"] = "wb-actions-discount-verify"
    elif mutation == "package_data":
        package["apply_params"] = {
            "plan_run_id": "plan-source",
            "unexpected": "secret-business-value",
        }
    else:
        record_checksum = "secret-record-checksum"
    _replace_approval_package(
        db_path,
        approval.approval_id,
        package,
        record_checksum=record_checksum,
    )
    service = _service(tmp_path, store)

    with pytest.raises(RuntimeError) as error:
        service.submit_approval_verify(approval.approval_id)

    assert expected_issue in str(error.value)
    assert forbidden_text not in str(error.value)
    assert store.get_approval(approval.approval_id).status == "applied"  # type: ignore[union-attr]
    assert store.list_jobs(task_id="ozon-elastic-verify") == []
    assert store.list_jobs(task_id="wb-actions-discount-verify") == []


def test_recovery_rejects_tampered_package_without_raising_or_leaking_data(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "runtime.db"
    store = JobStore(db_path)
    approval = _create_approval(
        store,
        approval_id="approval-recovery-tampered",
        status="applying_unknown",
    )
    package = dict(approval.data)
    package["verify_task"] = "secret-recovery-verify-task"
    _replace_approval_package(db_path, approval.approval_id, package)
    service = _service(tmp_path, store)

    recovery = service.recover_runtime_approvals(run_verify=True)

    assert recovery["overall_status"] == "warning"
    assert recovery["queued_verify_jobs"] == 0
    assert recovery["manual_verify_required"] == 1
    assert recovery["rows"][0]["action"] == "manual_verify_required"
    assert "approval_package_checksum_invalid" in recovery["rows"][0]["blocked_reason"]
    assert "secret-recovery-verify-task" not in json.dumps(recovery, ensure_ascii=False)
    assert store.get_approval(approval.approval_id).status == "applying_unknown"  # type: ignore[union-attr]
    assert store.list_jobs(limit=10) == []


def test_transactional_verify_link_rechecks_package_after_service_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "runtime.db"
    store = JobStore(db_path)
    approval = _create_approval(store, approval_id="approval-verify-toctou", status="applied")
    original_create = store.create_or_get_active_approval_verify_job

    def mutate_then_create(**kwargs):  # type: ignore[no-untyped-def]
        current = store.get_approval(approval.approval_id)
        assert current is not None
        package = dict(current.data)
        package["verify_task"] = "wb-actions-discount-verify"
        _replace_approval_package(db_path, approval.approval_id, package)
        return original_create(**kwargs)

    monkeypatch.setattr(store, "create_or_get_active_approval_verify_job", mutate_then_create)
    service = _service(tmp_path, store)

    with pytest.raises(RuntimeError, match="approval_package_checksum_invalid"):
        service.submit_approval_verify(approval.approval_id)

    assert store.get_approval(approval.approval_id).status == "applied"  # type: ignore[union-attr]
    assert store.list_jobs(limit=10) == []


def test_honestly_stale_signed_package_remains_eligible_for_safe_verify(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "runtime.db"
    store = JobStore(db_path)
    approval = _create_approval(store, approval_id="approval-stale-reconcile", status="applied")
    package = dict(approval.data)
    package["created_at"] = "2020-01-01T00:00:00+00:00"
    package["approval_checksum"] = _signed_checksum(package)
    _replace_approval_package(
        db_path,
        approval.approval_id,
        package,
        record_checksum=str(package["approval_checksum"]),
    )
    service = _service(tmp_path, store)

    verify_job = service.submit_approval_verify(approval.approval_id)

    assert verify_job.task_id == "ozon-elastic-verify"
    assert verify_job.params["approval_id"] == approval.approval_id


@pytest.mark.parametrize("first_failure", ["false", "exception"])
def test_manifest_close_failure_is_durable_and_retry_does_not_repeat_marketplace_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    first_failure: str,
) -> None:
    store = JobStore(tmp_path / "runtime.db")
    approval = _create_approval(store, approval_id=f"approval-manifest-{first_failure}")
    apply_calls: list[str] = []
    verify_calls: list[str] = []
    manifest_calls: list[tuple[str, str, str]] = []

    def apply_handler(task, data_dir, credentials, inputs):  # type: ignore[no-untyped-def]
        apply_calls.append(inputs["approval_id"])
        return {"run_id": "apply-for-manifest", "overall_status": "ok", "artifacts": {}}

    def verify_handler(task, data_dir, credentials, inputs):  # type: ignore[no-untyped-def]
        verify_calls.append(inputs["approval_id"])
        return {
            "run_id": "verify-for-manifest",
            "overall_status": "ok",
            "verification_confirmed": True,
            "artifacts": {},
        }

    def close_manifest(*, data_dir, run_id, applied_by_run_id):  # type: ignore[no-untyped-def]
        current = store.get_approval(approval.approval_id)
        manifest_calls.append((run_id, applied_by_run_id, current.status if current else ""))
        if len(manifest_calls) == 1:
            if first_failure == "exception":
                raise OSError("secret manifest filesystem detail")
            return False
        return True

    monkeypatch.setattr(job_service_module, "close_run_manifest", close_manifest)
    service = _service(
        tmp_path,
        store,
        apply_handler=apply_handler,
        verify_handler=verify_handler,
    )
    service.approve(approval.approval_id)
    apply_job = service.submit_approval_apply(approval.approval_id)
    assert service.run(apply_job.job_id).ok is True
    verify_job = service.submit_approval_verify(approval.approval_id)

    verify_result = service.run(verify_job.job_id)

    assert verify_result.ok is False
    assert verify_result.status == "manifest_reconciliation_pending"
    assert verify_result.job.status == "partial_success"
    assert verify_result.job.error == "manifest_reconciliation_pending"
    assert "secret" not in verify_result.message
    assert store.get_approval(approval.approval_id).status == "closed"  # type: ignore[union-attr]
    pending = store.get_approval_manifest_reconciliation(approval.approval_id)
    assert pending is not None
    assert pending.status == "failed"
    assert pending.last_error_code in {"manifest_not_found", "manifest_close_failed"}
    assert pending.attempt_count == 1
    with pytest.raises(RuntimeError, match="verify is not available"):
        service.submit_approval_verify(approval.approval_id)

    retry_service = _service(tmp_path, JobStore(store.db_path))
    retried = retry_service.reconcile_approval_manifest(approval.approval_id)
    repeated = retry_service.reconcile_approval_manifest(approval.approval_id)

    assert retried.status == "closed"
    assert retried.attempt_count == 2
    assert repeated == retried
    reconciled_job = store.get_job(verify_job.job_id)
    assert reconciled_job is not None
    assert reconciled_job.status == "success"
    assert reconciled_job.error == ""
    assert manifest_calls == [
        ("plan-source", "apply-for-manifest", "closed"),
        ("plan-source", "apply-for-manifest", "closed"),
    ]
    assert apply_calls == [approval.approval_id]
    assert verify_calls == [approval.approval_id]
    assert [event.event_type for event in store.list_approval_events(approval.approval_id)][
        -3:
    ] == [
        "approval_manifest_close_pending",
        "approval_manifest_close_failed",
        "approval_manifest_closed",
    ]


@pytest.mark.parametrize("initial_manifest_status", ["pending", "failed"])
def test_cli_recover_after_restart_covers_applied_and_manifest_reconciliation_without_param_leak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,  # type: ignore[no-untyped-def]
    initial_manifest_status: str,
) -> None:
    runtime_db = tmp_path / "runtime.db"
    data_dir = tmp_path / "data"
    store = JobStore(runtime_db)
    manifest_approval = _create_approval(
        store,
        approval_id=f"approval-cli-manifest-{initial_manifest_status}",
    )
    apply_calls: list[str] = []
    verify_calls: list[str] = []
    manifest_calls: list[tuple[str, str, str]] = []

    def apply_handler(task, data_dir, credentials, inputs):  # type: ignore[no-untyped-def]
        apply_calls.append(inputs["approval_id"])
        return {
            "run_id": "apply-cli-manifest",
            "overall_status": "ok",
            "artifacts": {},
        }

    def verify_handler(task, data_dir, credentials, inputs):  # type: ignore[no-untyped-def]
        verify_calls.append(inputs["approval_id"])
        return {
            "run_id": "verify-cli-manifest",
            "overall_status": "ok",
            "verification_confirmed": True,
            "artifacts": {},
        }

    def close_manifest(*, data_dir, run_id, applied_by_run_id):  # type: ignore[no-untyped-def]
        current = store.get_approval(manifest_approval.approval_id)
        manifest_calls.append((run_id, applied_by_run_id, current.status if current else ""))
        if initial_manifest_status == "failed" and len(manifest_calls) == 1:
            return False
        return True

    monkeypatch.setattr(job_service_module, "close_run_manifest", close_manifest)
    service = _service(
        tmp_path,
        store,
        apply_handler=apply_handler,
        verify_handler=verify_handler,
    )
    service.approve(manifest_approval.approval_id)
    apply_job = service.submit_approval_apply(manifest_approval.approval_id)
    assert service.run(apply_job.job_id).ok is True
    verify_job = service.submit_approval_verify(manifest_approval.approval_id)
    if initial_manifest_status == "pending":
        monkeypatch.setattr(
            service,
            "_reconcile_approval_manifest",
            lambda approval_id: store.get_approval_manifest_reconciliation(approval_id),
        )
    verify_result = service.run(verify_job.job_id)
    assert verify_result.status == "manifest_reconciliation_pending"
    before_restart = store.get_approval_manifest_reconciliation(
        manifest_approval.approval_id
    )
    assert before_restart is not None
    assert before_restart.status == initial_manifest_status

    sensitive_value = "secret-business-param-must-not-be-rendered"
    applied = _create_approval(
        store,
        approval_id=f"approval-cli-applied-{initial_manifest_status}",
        status="applied",
        apply_params={
            "plan_run_id": "plan-sensitive",
            "business_note": sensitive_value,
        },
    )

    args = [
        "jobs",
        "recover-approvals",
        "--runtime-db",
        str(runtime_db),
        "--data-dir",
        str(data_dir),
    ]
    assert main(args) == 0
    first_raw = capsys.readouterr().out
    first = json.loads(first_raw)

    assert sensitive_value not in first_raw
    assert first["overall_status"] == "ok"
    assert first["queued_verify_jobs"] == 1
    assert first["rows"][0]["approval_id"] == applied.approval_id
    assert first["rows"][0]["action"] == "verify_job_queued"
    assert "verify_params" not in first["rows"][0]
    assert first["manifest_reconciliation"]["checked_count"] == 1
    assert first["manifest_reconciliation"]["rows"][0]["approval_id"] == (
        manifest_approval.approval_id
    )
    assert first["manifest_reconciliation"]["rows"][0]["status"] == "closed"
    queued = store.list_jobs(task_id="ozon-elastic-verify", status="queued")
    applied_verify = next(job for job in queued if job.params["approval_id"] == applied.approval_id)
    assert applied_verify.params["business_note"] == sensitive_value
    assert apply_calls == [manifest_approval.approval_id]
    assert verify_calls == [manifest_approval.approval_id]

    manifest_call_count = 1 if initial_manifest_status == "pending" else 2
    assert len(manifest_calls) == manifest_call_count
    assert main(args) == 0
    second_raw = capsys.readouterr().out
    second = json.loads(second_raw)

    assert sensitive_value not in second_raw
    assert second["rows"][0]["action"] == "verify_job_active"
    assert "verify_params" not in second["rows"][0]
    assert second["manifest_reconciliation"]["checked_count"] == 0
    assert len(manifest_calls) == manifest_call_count
    assert apply_calls == [manifest_approval.approval_id]
    assert verify_calls == [manifest_approval.approval_id]


@pytest.mark.parametrize(
    "status",
    ["applying", "applying_unknown", "applied", "verified", "closed"],
)
def test_repeat_apply_is_blocked_for_every_post_approval_status(
    tmp_path: Path,
    status: str,
) -> None:
    store = JobStore(tmp_path / "runtime.db")
    approval = _create_approval(
        store,
        approval_id=f"approval-repeat-{status}",
        status=status,
    )
    before = store.get_approval(approval.approval_id)
    service = _service(tmp_path, store)

    with pytest.raises(RuntimeError, match="expected `approved`"):
        service.submit_approval_apply(approval.approval_id)

    assert store.list_jobs(limit=10) == []
    assert store.get_approval(approval.approval_id) == before


def test_pending_review_can_be_rejected_with_distinct_audit_event(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "runtime.db")
    approval = _create_approval(store, approval_id="approval-rejected")
    service = _service(tmp_path, store)

    rejected = service.reject(approval.approval_id)

    assert rejected.status == "rejected"
    assert [event.event_type for event in store.list_approval_events(approval.approval_id)] == [
        "approval_pending_review_created",
        "approval_rejected",
    ]


def test_approved_can_be_revoked_before_apply_with_exact_audit_event(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "runtime.db")
    approval = _create_approval(
        store,
        approval_id="approval-approved-revoked",
        status="approved",
    )
    service = _service(tmp_path, store)

    rejected = service.reject(approval.approval_id)

    assert rejected.status == "rejected"
    event = store.list_approval_events(approval.approval_id)[-1]
    assert event.event_type == "approval_rejected"
    assert event.status_before == "approved"
    assert event.status_after == "rejected"
    assert store.list_jobs(limit=10) == []


def test_lifecycle_schema_upgrade_preserves_existing_approval_row(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    store = JobStore(db_path)
    approval = _create_approval(store, approval_id="approval-before-lifecycle-schema")
    before = store.get_approval(approval.approval_id)
    with sqlite3.connect(db_path) as connection:
        connection.execute("DROP TABLE approval_manifest_reconciliations")
        connection.execute("DROP TABLE approval_verify_jobs")
        connection.execute("DROP TABLE approval_events")

    upgraded = JobStore(db_path)
    upgraded.initialize()

    assert upgraded.get_approval(approval.approval_id) == before
    with sqlite3.connect(db_path) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name IN (
                  'approval_events', 'approval_verify_jobs',
                  'approval_manifest_reconciliations'
                )
                """
            )
        }
    assert tables == {
        "approval_events",
        "approval_verify_jobs",
        "approval_manifest_reconciliations",
    }
