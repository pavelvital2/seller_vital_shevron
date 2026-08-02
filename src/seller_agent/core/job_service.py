from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import uuid
from typing import Any

from seller_agent.core.job_models import (
    ApprovalManifestReconciliation,
    ApprovalRecord,
    ApprovalStatus,
    JobRecord,
)
from seller_agent.core.job_store import (
    DEFAULT_RUNTIME_DB,
    ApprovalVerifyIntegrityError,
    JobStore,
)
from seller_agent.core.workflow_runner import WorkflowRunner
from seller_agent.core.run_manifest import close_run_manifest
from seller_agent.tasks.registry import RegisteredTask, TaskRegistry, default_task_registry
from seller_agent.safety.approval_package import (
    build_approval_package,
    normalize_business_params,
)
from seller_agent.safety.guard import SafetyGuard
from seller_agent.safety.plan_approval import actionable_plan_source_ref
from seller_agent.tasks.inbox_workflow import (
    INBOX_RECEIPT_STATE_ERROR,
    InboxReceiptStateError,
    validate_inbox_receipt_state,
)


INBOX_RECEIPT_STATE_TASKS = frozenset(
    {"ozon-inbox", "wb-inbox", "ozon-inbox-apply", "wb-inbox-apply"}
)


@dataclass(frozen=True)
class JobServiceResult:
    job: JobRecord
    ok: bool
    status: str
    message: str = ""


class JobService:
    """Submits and runs task jobs through the project runtime store."""

    def __init__(
        self,
        *,
        store: JobStore | None = None,
        registry: TaskRegistry | None = None,
        workflow_runner: WorkflowRunner | None = None,
        data_dir: Path = Path("data"),
        runtime_db: Path = DEFAULT_RUNTIME_DB,
        safety_guard: SafetyGuard | None = None,
    ) -> None:
        self.store = store or JobStore(runtime_db)
        self.data_dir = data_dir
        self.registry = registry or default_task_registry()
        self.workflow_runner = workflow_runner or WorkflowRunner(registry=self.registry, data_dir=data_dir)
        self.safety_guard = safety_guard or SafetyGuard()

    def submit(
        self,
        *,
        task_id: str,
        params: dict[str, Any] | None = None,
        actor: str = "system",
        source: str = "",
    ) -> JobRecord:
        task = self.registry.get(task_id)
        if not task.enabled:
            raise ValueError(f"Task `{task.name}` is disabled for JobService.")
        if task.mode == "verify":
            raise RuntimeError(
                "Verify jobs require an existing applied approval and "
                "submit_approval_verify(approval_id)."
            )
        if task.is_write:
            raise RuntimeError(
                "Write jobs require an existing approved approval package and "
                "submit_approval_apply(approval_id); confirmed_by_user cannot create or approve one."
            )
        return self._submit_task(
            task=task,
            params=params,
            actor=actor,
            source=source,
        )

    def _submit_task(
        self,
        *,
        task: RegisteredTask,
        params: dict[str, Any] | None,
        actor: str,
        source: str,
    ) -> JobRecord:
        job_params = dict(params or {})
        job = self.store.create_job(
            task_id=task.name,
            params=job_params,
            actor=actor,
            status="queued",
            source=source,
        )
        self.store.append_event(
            job_id=job.job_id,
            event_type="job_queued",
            message=f"Job queued for task `{task.name}`.",
            data={"task_id": task.name, "mode": task.mode, "risk": task.risk},
        )
        current = self.store.get_job(job.job_id)
        if current is None:
            raise RuntimeError(f"Job disappeared after submit: {job.job_id}")
        return current

    def run(
        self,
        job_id: str,
        *,
        claim_token: str = "",
        worker_id: str = "",
    ) -> JobServiceResult:
        job = self.store.get_job(job_id)
        if job is None:
            raise KeyError(f"Unknown job: {job_id}")
        if job.status not in {"created", "queued", "waiting_confirmation"}:
            return JobServiceResult(
                job=job,
                ok=False,
                status="blocked",
                message=f"Job `{job_id}` is `{job.status}` and cannot be started.",
            )

        task = self.registry.get(job.task_id)
        if bool(claim_token) != bool(worker_id):
            return JobServiceResult(
                job=job,
                ok=False,
                status="claim_invalid",
                message="Both claim_token and worker_id are required to start a claimed job.",
            )
        if claim_token and not self.store.job_claim_is_active(
            job_id=job_id,
            claim_token=claim_token,
            worker_id=worker_id,
        ):
            current = self.store.get_job(job_id) or job
            return JobServiceResult(
                job=current,
                ok=False,
                status="claim_invalid",
                message=f"Claim for queued job `{job_id}` is invalid, expired or owned by another worker.",
            )
        if not task.enabled:
            failed = self.store.update_job_status(
                job_id,
                "failed",
                error="task_disabled",
                message="Job blocked because its task was disabled before workflow start.",
            )
            if claim_token:
                self.store.release_job_claim(
                    job_id=job_id,
                    claim_token=claim_token,
                    worker_id=worker_id,
                )
            self.store.append_event(
                job_id=job_id,
                event_type="job_task_disabled",
                message="Job blocked because its registered task is disabled.",
                data={"task_id": task.name},
            )
            return JobServiceResult(
                job=failed,
                ok=False,
                status="blocked",
                message="task_disabled",
            )
        if task.name in INBOX_RECEIPT_STATE_TASKS:
            try:
                validate_inbox_receipt_state(self.data_dir)
            except InboxReceiptStateError:
                failed = self.store.update_job_status(
                    job_id,
                    "failed",
                    error=INBOX_RECEIPT_STATE_ERROR,
                    message="Job blocked by invalid durable inbox receipt state.",
                )
                if claim_token:
                    self.store.release_job_claim(
                        job_id=job_id,
                        claim_token=claim_token,
                        worker_id=worker_id,
                    )
                self.store.append_event(
                    job_id=job_id,
                    event_type="job_runtime_state_blocked",
                    message="Job blocked before workflow start by invalid durable runtime state.",
                    data={"task_id": task.name, "reason_code": INBOX_RECEIPT_STATE_ERROR},
                )
                return JobServiceResult(
                    job=failed,
                    ok=False,
                    status="runtime_state_invalid",
                    message=INBOX_RECEIPT_STATE_ERROR,
                )
        if job.status == "created":
            job = self.store.update_job_status(
                job_id,
                "queued",
                message="Job queued before claim acquisition.",
            )
        elif job.status == "waiting_confirmation":
            if task.is_write and not _confirmed(job.params):
                return JobServiceResult(
                    job=job,
                    ok=False,
                    status="waiting_confirmation",
                    message=job.error or "Job is waiting for explicit owner confirmation.",
                )
            job = self.store.update_job_status(
                job_id,
                "queued",
                message="Confirmed job returned to the queue before claim acquisition.",
            )

        if not claim_token:
            direct_worker_id = f"job-service:{uuid.uuid4().hex}"
            claim = self.store.claim_queued_job(
                job_id=job_id,
                worker_id=direct_worker_id,
                ttl_seconds=_lease_ttl_seconds(task.timeout_seconds),
            )
            if claim is None:
                current = self.store.get_job(job_id) or job
                return JobServiceResult(
                    job=current,
                    ok=False,
                    status="claim_unavailable",
                    message=f"Queued job `{job_id}` is already claimed or no longer available.",
                )
            claim_token = claim.claim_token
            worker_id = claim.worker_id

        if task.is_write and not _confirmed(job.params):
            waiting = self.store.update_job_status(
                job_id,
                "waiting_confirmation",
                error="confirmed_by_user=true is required for apply jobs.",
                message="Job is waiting for explicit owner confirmation.",
            )
            self.store.release_job_claim(
                job_id=job_id,
                claim_token=claim_token,
                worker_id=worker_id,
            )
            return JobServiceResult(job=waiting, ok=False, status="waiting_confirmation", message=waiting.error)
        if not task.is_read_only and not task.is_write and task.mode not in {"dry_run", "verify"}:
            failed = self.store.update_job_status(
                job_id,
                "failed",
                error=(
                    "JobService supports read-only, verify and explicitly confirmed apply tasks; "
                    f"`{task.name}` is `{task.mode}`."
                ),
                message="Job blocked because task mode is unsupported.",
            )
            return JobServiceResult(job=failed, ok=False, status="blocked", message=failed.error)

        if task.is_write:
            approval_id = _approval_id(job.params)
            approval = self.store.get_approval(approval_id) if approval_id else None
            decision = self.safety_guard.validate_apply(task=task, job=job, approval=approval)
            if not decision.allowed:
                safe_issues = _sanitized_safety_issues(decision.issues)
                blocked_status = "safety_blocked"
                event_type = "job_safety_guard_blocked"
                if "approval_record_checksum_mismatch" in safe_issues:
                    blocked_status = "approval_checksum_mismatch"
                    event_type = "job_approval_checksum_mismatch"
                elif "approval_status_invalid" in safe_issues:
                    blocked_status = "approval_not_available"
                    event_type = "job_approval_blocked"
                elif "approval_record_missing" in safe_issues:
                    blocked_status = "approval_missing"
                failed = self.store.update_job_status(
                    job_id,
                    "failed",
                    error=_sanitized_safety_error(safe_issues),
                    message="Job blocked by centralized SafetyGuard before resource acquisition.",
                )
                self.store.append_event(job_id=job_id, event_type=event_type, message="Centralized SafetyGuard blocked apply.", data={"issues": list(safe_issues)})
                return JobServiceResult(job=failed, ok=False, status=blocked_status, message=failed.error)

        leases_acquired = False
        reserved_approval_id = ""
        try:
            if task.lock_keys:
                ttl_seconds = _lease_ttl_seconds(task.timeout_seconds)
                leases = self.store.acquire_resource_leases(
                    resource_keys=task.lock_keys,
                    owner_id=job_id,
                    ttl_seconds=ttl_seconds,
                    data={"task_id": task.name, "ttl_seconds": ttl_seconds},
                )
                if leases is None:
                    self.store.append_event(
                        job_id=job_id,
                        event_type="job_resource_blocked",
                        message="Job is waiting for resource leases.",
                        data={"lock_keys": list(task.lock_keys)},
                    )
                    current = self.store.get_job(job_id) or job
                    self.store.release_job_claim(
                        job_id=job_id,
                        claim_token=claim_token,
                        worker_id=worker_id,
                    )
                    return JobServiceResult(
                        job=current,
                        ok=False,
                        status="resource_locked",
                        message=f"Required resource lease is busy for task `{task.name}`.",
                    )
                leases_acquired = bool(leases)
                if leases_acquired:
                    self.store.append_event(
                        job_id=job_id,
                        event_type="job_resource_leases_acquired",
                        message="Resource leases acquired.",
                        data={"lock_keys": [lease.resource_key for lease in leases]},
                    )

            if task.is_write:
                approval_id = _approval_id(job.params)
                if approval_id:
                    approval = self.store.get_approval(approval_id)
                    expected_checksum = _approval_checksum(job.params)
                    if approval is None:
                        failed = self.store.update_job_status(
                            job_id,
                            "failed",
                            error=f"Runtime approval not found: {approval_id}",
                            message="Job blocked before workflow start because runtime approval is missing.",
                        )
                        return JobServiceResult(job=failed, ok=False, status="approval_missing", message=failed.error)
                    if expected_checksum and approval.checksum != expected_checksum:
                        failed = self.store.update_job_status(
                            job_id,
                            "failed",
                            error="runtime approval checksum mismatch",
                            message="Job blocked before workflow start because runtime approval checksum mismatched.",
                        )
                        self.store.append_event(
                            job_id=job_id,
                            event_type="job_approval_checksum_mismatch",
                            message="Runtime approval checksum mismatch.",
                            data={"approval_id": approval_id},
                        )
                        return JobServiceResult(job=failed, ok=False, status="approval_checksum_mismatch", message=failed.error)
                    if not self.store.reserve_approval_for_apply(
                        approval_id=approval_id,
                        owner_job_id=job_id,
                        expected_checksum=expected_checksum,
                    ):
                        failed = self.store.update_job_status(
                            job_id,
                            "failed",
                            error=f"Runtime approval is not available for apply: {approval_id}",
                            message="Job blocked before workflow start because runtime approval could not be reserved.",
                        )
                        self.store.append_event(
                            job_id=job_id,
                            event_type="job_approval_blocked",
                            message="Runtime approval could not be reserved.",
                            data={"approval_id": approval_id, "approval_status": approval.status},
                        )
                        return JobServiceResult(job=failed, ok=False, status="approval_not_available", message=failed.error)
                    reserved_approval_id = approval_id
                    self.store.append_event(
                        job_id=job_id,
                        event_type="job_approval_reserved",
                        message="Runtime approval reserved for apply.",
                        data={"approval_id": approval_id},
                    )

            running = self.store.start_claimed_job(
                job_id=job_id,
                claim_token=claim_token,
                worker_id=worker_id,
                execution_ttl_seconds=_execution_ttl_seconds(task.timeout_seconds),
                message="Job runner started.",
            )
            if running is None:
                self.store.release_job_claim(
                    job_id=job_id,
                    claim_token=claim_token,
                    worker_id=worker_id,
                )
                current = self.store.get_job(job_id) or job
                return JobServiceResult(
                    job=current,
                    ok=False,
                    status="claim_invalid",
                    message=f"Claim for queued job `{job_id}` expired or changed before workflow start.",
                )
            if task.is_write:
                self.store.append_event(job_id=job_id, event_type="job_write_window_started", message="Apply workflow started after approval, checksum and lease gates.", data={"approval_id": reserved_approval_id, "write_started": True})
            allowed_modes = _allowed_modes_for_task(task)
            result = self.workflow_runner.run_task(task.name, inputs=job.params, allowed_modes=allowed_modes)
            result_data = result.to_dict()
            verify_approval_id = _approval_id(job.params) if task.mode == "verify" else ""
            verify_succeeded = bool(
                verify_approval_id
                and result.ok
                and result.status == "ok"
                and _result_verification_confirmed(result_data)
            )

            if result.ok:
                final_status = "success" if result.status == "ok" else "partial_success"
                approval_action = ""
                approval_id_for_finish = ""
                if reserved_approval_id:
                    approval_action = "apply_succeeded"
                    approval_id_for_finish = reserved_approval_id
                elif verify_approval_id:
                    approval_action = (
                        "verify_succeeded" if verify_succeeded else "verify_inconclusive"
                    )
                    approval_id_for_finish = verify_approval_id
                final = self.store.finish_claimed_job(
                    job_id=job_id,
                    claim_token=claim_token,
                    worker_id=worker_id,
                    status=final_status,
                    result=result_data,
                    message=f"Workflow finished with status `{result.status}`.",
                    approval_id=approval_id_for_finish,
                    approval_action=approval_action,
                )
                if final is None:
                    current = self.store.get_job(job_id) or running
                    return JobServiceResult(
                        job=current,
                        ok=False,
                        status="claim_lost",
                        message="Late workflow completion ignored because the running claim no longer owns the job.",
                    )
                if verify_succeeded:
                    reconciliation = self._reconcile_approval_manifest(verify_approval_id)
                    if reconciliation is not None and reconciliation.status != "closed":
                        return JobServiceResult(
                            job=self.store.get_job(final.job_id) or final,
                            ok=False,
                            status="manifest_reconciliation_pending",
                            message="RunManifest reconciliation is pending a safe retry.",
                        )
                elif task.mode == "dry_run":
                    self._register_plan_approval(job=job, task=task, result_data=result_data)
                return JobServiceResult(job=final, ok=True, status=result.status)

            approval_action = ""
            approval_id_for_finish = ""
            if reserved_approval_id:
                approval_action = "apply_unknown"
                approval_id_for_finish = reserved_approval_id
            elif verify_approval_id:
                approval_action = "verify_inconclusive"
                approval_id_for_finish = verify_approval_id
            final = self.store.finish_claimed_job(
                job_id=job_id,
                claim_token=claim_token,
                worker_id=worker_id,
                status="failed",
                result=result_data,
                error=result.error or result.blocked_reason or "workflow_failed",
                message=f"Workflow failed with status `{result.status}`.",
                approval_id=approval_id_for_finish,
                approval_action=approval_action,
            )
            if final is None:
                current = self.store.get_job(job_id) or running
                return JobServiceResult(
                    job=current,
                    ok=False,
                    status="claim_lost",
                    message="Late workflow failure ignored because the running claim no longer owns the job.",
                )
            return JobServiceResult(job=final, ok=False, status=result.status, message=final.error)
        finally:
            if leases_acquired:
                released_count = self.store.release_resource_leases(resource_keys=task.lock_keys, owner_id=job_id)
                self.store.append_event(
                    job_id=job_id,
                    event_type="job_resource_leases_released",
                    message="Resource leases released.",
                    data={"lock_keys": list(task.lock_keys), "released_count": released_count},
                )

    def cancel(self, job_id: str, *, reason: str = "cancelled") -> JobServiceResult:
        job = self.store.get_job(job_id)
        if job is None:
            raise KeyError(f"Unknown job: {job_id}")
        if job.status not in {"created", "queued", "waiting_confirmation"}:
            return JobServiceResult(
                job=job,
                ok=False,
                status="blocked",
                message=f"Job `{job_id}` is `{job.status}` and cannot be cancelled safely.",
            )
        cancelled = self.store.update_job_status(
            job_id,
            "cancelled",
            error=reason,
            message=f"Job cancelled: {reason}",
        )
        return JobServiceResult(job=cancelled, ok=True, status="cancelled", message=reason)

    def approve(self, approval_id: str) -> ApprovalRecord:
        approval = self.store.get_approval(approval_id)
        if approval is None:
            raise KeyError(f"Unknown approval: {approval_id}")
        self._validated_approval_task(approval)
        if not self.store.decide_approval(approval_id=approval_id, status="approved"):
            current = self.store.get_approval(approval_id)
            if current is None:  # pragma: no cover - guarded above and transactionally stable.
                raise KeyError(f"Unknown approval: {approval_id}")
            if current.status != "approved":
                raise RuntimeError(f"Approval `{approval_id}` cannot be approved from `{current.status}`.")
        return self.store.get_approval(approval_id)  # type: ignore[return-value]

    def reject(self, approval_id: str) -> ApprovalRecord:
        if not self.store.decide_approval(approval_id=approval_id, status="rejected"):
            current = self.store.get_approval(approval_id)
            if current is None:
                raise KeyError(f"Unknown approval: {approval_id}")
            if current.status != "rejected":
                raise RuntimeError(f"Approval `{approval_id}` cannot be rejected from `{current.status}`.")
        return self.store.get_approval(approval_id)  # type: ignore[return-value]

    def submit_approval_apply(
        self,
        approval_id: str,
        *,
        actor: str = "owner",
        expected_task_id: str | None = None,
    ) -> JobRecord:
        approval = self.store.get_approval(approval_id)
        if approval is None:
            raise KeyError(f"Unknown approval: {approval_id}")
        if approval.status != "approved":
            raise RuntimeError(f"Approval `{approval_id}` is `{approval.status}`, expected `approved`.")
        task = self._validated_approval_task(approval)
        if expected_task_id is not None and task.name != expected_task_id:
            raise RuntimeError("SafetyGuard blocked approval package: approval_task_mismatch.")
        params = normalize_business_params(approval.data["apply_params"])
        params.update(
            {
                "confirmed_by_user": True,
                "approval_id": approval.approval_id,
                "approval_checksum": approval.checksum,
            }
        )
        return self._submit_task(
            task=task,
            params=params,
            actor=actor,
            source="runtime_approval",
        )

    def _validated_approval_task(self, approval: ApprovalRecord) -> RegisteredTask:
        task_id = str(approval.data.get("task_id") or "")
        try:
            task = self.registry.get(task_id)
        except KeyError as exc:
            raise RuntimeError("SafetyGuard blocked approval package: approval_task_unknown.") from exc
        if not task.enabled or not task.is_write or task.mode != "apply":
            raise RuntimeError("SafetyGuard blocked approval package: approval_task_not_available.")
        decision = self.safety_guard.validate_approval_package(task=task, approval=approval)
        if not decision.allowed:
            raise RuntimeError(_sanitized_safety_error(decision.issues))
        return task

    def submit_approval_verify(self, approval_id: str, *, actor: str = "owner") -> JobRecord:
        job, _ = self._submit_approval_verify(
            approval_id,
            actor=actor,
            source="runtime_approval_verify",
        )
        return job

    def _submit_approval_verify(
        self,
        approval_id: str,
        *,
        actor: str,
        source: str,
    ) -> tuple[JobRecord, bool]:
        approval = self.store.get_approval(approval_id)
        if approval is None:
            raise KeyError(f"Unknown approval: {approval_id}")
        if approval.status not in {"applied", "applying_unknown"}:
            raise RuntimeError(f"Approval `{approval_id}` is `{approval.status}`, verify is not available.")
        request = self._validated_approval_verify_request(approval)
        try:
            return self.store.create_or_get_active_approval_verify_job(
                approval_id=approval_id,
                task_id=str(request["task_id"]),
                params=dict(request["params"]),
                actor=actor,
                source=source,
                expected_record_checksum=approval.checksum,
                expected_apply_task_id=str(request["apply_task_id"]),
                expected_source_plan_task=str(request["source_plan_task"]),
                expected_verify_task=str(request["task_id"]),
                expected_marketplaces=tuple(request["marketplaces"]),
            )
        except ApprovalVerifyIntegrityError as exc:
            raise RuntimeError(_sanitized_safety_error(exc.issues)) from exc

    def reconcile_approval_manifest(
        self,
        approval_id: str,
    ) -> ApprovalManifestReconciliation:
        reconciliation = self._reconcile_approval_manifest(approval_id)
        if reconciliation is None:
            raise KeyError("Approval has no RunManifest reconciliation record.")
        return reconciliation

    def _reconcile_approval_manifest(
        self,
        approval_id: str,
    ) -> ApprovalManifestReconciliation | None:
        reconciliation = self.store.get_approval_manifest_reconciliation(approval_id)
        if reconciliation is None or reconciliation.status == "closed":
            return reconciliation
        approval = self.store.get_approval(approval_id)
        if approval is None or approval.status != "closed":
            raise RuntimeError("Approval must close before RunManifest reconciliation.")
        try:
            closed = close_run_manifest(
                data_dir=self.data_dir,
                run_id=reconciliation.source_run_id,
                applied_by_run_id=reconciliation.applied_by_run_id,
            )
        except Exception:  # noqa: BLE001 - persist only a constant safe recovery code.
            return self.store.mark_approval_manifest_reconciliation(
                approval_id=approval_id,
                succeeded=False,
                error_code="manifest_close_failed",
            )
        return self.store.mark_approval_manifest_reconciliation(
            approval_id=approval_id,
            succeeded=bool(closed),
            error_code="" if closed else "manifest_not_found",
        )

    def reconcile_runtime_manifests(self, *, limit: int = 50) -> dict[str, Any]:
        records = self.store.list_pending_approval_manifest_reconciliations(limit=limit)
        rows: list[dict[str, Any]] = []
        for record in records:
            reconciled = self.reconcile_approval_manifest(record.approval_id)
            rows.append(
                {
                    "approval_id": record.approval_id,
                    "source_run_id": record.source_run_id,
                    "status": reconciled.status,
                    "attempt_count": reconciled.attempt_count,
                    "last_error_code": reconciled.last_error_code,
                }
            )
        return {
            "overall_status": (
                "ok" if all(row["status"] == "closed" for row in rows) else "warning"
            ),
            "checked_count": len(records),
            "rows": rows,
        }

    def _register_plan_approval(
        self,
        *,
        job: JobRecord,
        task: RegisteredTask,
        result_data: dict[str, Any],
    ) -> ApprovalRecord | None:
        apply_tasks = [
            candidate
            for candidate in self.registry.list()
            if candidate.enabled
            and candidate.is_write
            and candidate.mode == "apply"
            and candidate.source_plan_task == task.name
        ]
        if len(apply_tasks) != 1:
            return None
        apply_task = apply_tasks[0]
        source_key = apply_task.approval_source_field
        planner_result = result_data.get("summary")
        if not isinstance(planner_result, dict):
            return None
        source_ref = actionable_plan_source_ref(
            planner_result,
            expected_source_field=source_key,
        )
        if not source_ref:
            return None
        apply_params = {source_key: source_ref}
        approval_source = {"kind": source_key, "ref": _canonical_json(source_ref)}
        approval_id = f"runtime:{apply_task.name}:{_canonical_hash({'task_id': apply_task.name, 'source': approval_source})[:24]}"
        package = build_approval_package(
            approval_id=approval_id,
            task_id=apply_task.name,
            source_plan_task=task.name,
            verify_task=apply_task.verify_task,
            source_kind=approval_source["kind"],
            source_ref=approval_source["ref"],
            apply_params=apply_params,
            marketplaces=apply_task.marketplaces,
        )
        return self.store.ensure_approval(
            approval_id=approval_id,
            source_job_id=job.job_id,
            status="pending_review",
            checksum=package["approval_checksum"],
            data=package,
        )

    def recover_runtime_approvals(
        self,
        *,
        statuses: tuple[ApprovalStatus, ...] = ("applying", "applying_unknown", "applied"),
        limit: int = 50,
        run_verify: bool = False,
        actor: str = "runtime_recovery",
    ) -> dict[str, Any]:
        approvals = self.store.list_approvals(statuses=statuses, limit=limit)
        rows: list[dict[str, Any]] = []
        for approval in approvals:
            row: dict[str, Any] = {
                "approval_id": approval.approval_id,
                "status_before": approval.status,
                "source_job_id": approval.source_job_id,
                "owner_job_id": approval.owner_job_id,
            }
            if approval.status == "applying" and approval.owner_job_id:
                owner_job = self.store.get_job(approval.owner_job_id)
                if owner_job is not None and owner_job.status in {
                    "created",
                    "queued",
                    "running",
                    "waiting_confirmation",
                }:
                    rows.append(
                        {
                            **row,
                            "action": "manual_verify_required",
                            "blocked_reason": "apply_job_still_active",
                            "owner_job_status": owner_job.status,
                        }
                    )
                    continue
            if approval.status == "applying":
                rows.append(
                    {
                        **row,
                        "action": "manual_verify_required",
                        "blocked_reason": "apply_state_not_reconciled",
                    }
                )
                continue
            try:
                verify_job, verify_job_created = self._submit_approval_verify(
                    approval.approval_id,
                    actor=actor,
                    source="runtime_approval_recovery",
                )
            except (KeyError, RuntimeError) as exc:
                rows.append(
                    {
                        **row,
                        "action": "manual_verify_required",
                        "blocked_reason": _sanitized_verify_submission_error(exc),
                    }
                )
                continue
            row.update(
                {
                    "action": (
                        "verify_job_queued" if verify_job_created else "verify_job_active"
                    ),
                    "verify_job_id": verify_job.job_id,
                    "verify_job_created": verify_job_created,
                    "verify_task": verify_job.task_id,
                }
            )
            if run_verify and verify_job_created:
                verify_result = self.run(verify_job.job_id)
                current = self.store.get_approval(approval.approval_id)
                verify_confirmed = bool(
                    verify_result.ok
                    and current is not None
                    and current.status == "closed"
                )
                row.update(
                    {
                        "action": "verify_job_ran",
                        "verify_job_status": verify_result.job.status,
                        "verify_result_status": verify_result.status,
                        "verify_ok": verify_confirmed,
                    }
                )
                row["approval_status_after"] = current.status if current else approval.status
            rows.append(row)

        blocked = [row for row in rows if row.get("action") == "manual_verify_required"]
        failed_verify = [row for row in rows if row.get("action") == "verify_job_ran" and not row.get("verify_ok")]
        return {
            "overall_status": "warning" if blocked or failed_verify else "ok",
            "checked_count": len(approvals),
            "queued_verify_jobs": sum(
                1 for row in rows if row.get("verify_job_created") is True
            ),
            "manual_verify_required": len(blocked),
            "failed_verify_jobs": len(failed_verify),
            "run_verify": run_verify,
            "rows": rows,
        }

    def _build_recovery_verify_request(self, approval: ApprovalRecord) -> dict[str, Any]:
        try:
            return self._validated_approval_verify_request(approval)
        except (KeyError, RuntimeError) as exc:
            return {
                "ok": False,
                "blocked_reason": _sanitized_verify_submission_error(exc),
            }

    def _validated_approval_verify_request(
        self,
        approval: ApprovalRecord,
    ) -> dict[str, Any]:
        task_id = str(approval.data.get("task_id") or "")
        if not task_id:
            raise RuntimeError(
                _sanitized_safety_error(("approval_package_missing_task_id",))
            )
        try:
            apply_task = self.registry.get(task_id)
        except KeyError as exc:
            raise RuntimeError("approval_unknown_task_id") from exc
        if not apply_task.is_write or apply_task.mode != "apply":
            raise RuntimeError("approval_task_not_available")

        integrity = self.safety_guard.validate_approval_package_integrity(
            task=apply_task,
            approval=approval,
        )
        if not integrity.allowed:
            raise RuntimeError(_sanitized_safety_error(integrity.issues))

        verify_task_id = str(apply_task.verify_task or "")
        if not verify_task_id:
            raise RuntimeError("approval_missing_verify_task")
        try:
            verify_task = self.registry.get(verify_task_id)
        except KeyError as exc:
            raise RuntimeError("approval_unknown_verify_task") from exc
        if not verify_task.enabled:
            raise RuntimeError("verify_task_disabled")
        if verify_task.mode != "verify":
            raise RuntimeError("verify_task_mode_not_safe")
        if verify_task.is_write:
            raise RuntimeError("verify_task_is_write")

        params = _runtime_recovery_verify_params(approval)
        if not params:
            raise RuntimeError("approval_missing_recoverable_params")
        params["approval_id"] = approval.approval_id
        params["runtime_recovery_source_job_id"] = approval.source_job_id
        params["runtime_recovery_apply_task"] = apply_task.name
        return {
            "ok": True,
            "task_id": verify_task.name,
            "params": params,
            "apply_task_id": apply_task.name,
            "source_plan_task": apply_task.source_plan_task,
            "marketplaces": tuple(apply_task.marketplaces),
        }

    def get_status(self, job_id: str) -> JobRecord | None:
        return self.store.get_job(job_id)


def default_job_service(
    *,
    data_dir: Path = Path("data"),
    runtime_db: Path = DEFAULT_RUNTIME_DB,
) -> JobService:
    return JobService(data_dir=data_dir, runtime_db=runtime_db)


def _confirmed(params: dict[str, Any]) -> bool:
    value = params.get("confirmed_by_user", False)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _lease_ttl_seconds(timeout_seconds: int) -> int:
    if timeout_seconds > 0:
        return max(timeout_seconds + 300, 900)
    return 7200


def _execution_ttl_seconds(timeout_seconds: int) -> int:
    if timeout_seconds > 0:
        return max(timeout_seconds + 300, 900)
    return 7200


def _allowed_modes_for_task(task: RegisteredTask) -> set[str]:
    if task.is_read_only:
        return {"read_only"}
    if task.mode == "dry_run":
        return {"dry_run"}
    if task.mode == "verify":
        return {"verify"}
    return {"apply"}


def _approval_id(params: dict[str, Any]) -> str:
    return str(params.get("approval_id") or "").strip()


def _approval_checksum(params: dict[str, Any]) -> str:
    return str(params.get("approval_checksum") or "").strip()


_VERIFY_TRANSPORT_ONLY_PARAMS = frozenset(
    {
        "approval_id",
        "approval_checksum",
        "confirmed_by_user",
        "runtime_approval_id",
        "runtime_approval_checksum",
        "runtime_recovery_approval_id",
        "runtime_recovery_approval_checksum",
        "runtime_recovery_source_job_id",
        "runtime_recovery_apply_task",
        "run_id",
    }
)


def _normalize_runtime_verify_params(params: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in sorted(params.items())
        if key not in _VERIFY_TRANSPORT_ONLY_PARAMS
    }


def _runtime_recovery_verify_params(approval: ApprovalRecord) -> dict[str, Any]:
    raw = approval.data.get("verify_params")
    if not isinstance(raw, dict) or not raw:
        raw = approval.data.get("apply_params")
    if not isinstance(raw, dict):
        return {}
    return _normalize_runtime_verify_params(raw)


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


_SAFETY_GUARD_ISSUE_CODES = frozenset(
    {
        "approval_checksum_missing",
        "approval_id_missing",
        "approval_marketplaces_mismatch",
        "approval_package_apply_params_invalid",
        "approval_package_changed_after_verify_authorization",
        "approval_package_checksum_invalid",
        "approval_package_created_at_future",
        "approval_package_created_at_invalid",
        "approval_package_id_mismatch",
        "approval_package_marketplaces_invalid",
        "approval_package_missing_approval_checksum",
        "approval_package_missing_approval_id",
        "approval_package_missing_created_at",
        "approval_package_missing_source_kind",
        "approval_package_missing_source_plan_task",
        "approval_package_missing_source_ref",
        "approval_package_missing_task_id",
        "approval_package_missing_verify_task",
        "approval_package_schema_invalid",
        "approval_package_stale",
        "approval_package_transport_params_invalid",
        "approval_params_mismatch",
        "approval_record_checksum_mismatch",
        "approval_record_missing",
        "approval_source_plan_task_mismatch",
        "approval_status_invalid",
        "approval_task_mismatch",
        "approval_verify_task_mismatch",
        "mapping_evidence_missing",
        "owner_confirmation_missing",
        "resource_locks_missing",
        "safety_policy_violation",
        "source_plan_task_missing",
        "task_is_not_apply",
        "verify_task_missing",
    }
)


def _sanitized_safety_issues(issues: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    safe: list[str] = []
    for raw_issue in issues:
        issue = str(raw_issue).strip()
        safe_issue = issue if issue in _SAFETY_GUARD_ISSUE_CODES else "safety_policy_violation"
        if safe_issue not in safe:
            safe.append(safe_issue)
    return tuple(safe or ("safety_policy_violation",))


def _sanitized_safety_error(issues: tuple[str, ...] | list[str]) -> str:
    return "SafetyGuard blocked apply: " + ", ".join(_sanitized_safety_issues(issues))


_VERIFY_SUBMISSION_ERROR_CODES = frozenset(
    {
        "approval_missing_recoverable_params",
        "approval_missing_verify_task",
        "approval_task_not_available",
        "approval_unknown_task_id",
        "approval_unknown_verify_task",
        "verify_task_is_write",
        "verify_task_disabled",
        "verify_task_mode_not_safe",
    }
)


def _sanitized_verify_submission_error(error: Exception) -> str:
    message = str(error).strip()
    if message.startswith("SafetyGuard blocked apply: "):
        return message
    if message in _VERIFY_SUBMISSION_ERROR_CODES:
        return message
    return _sanitized_safety_error(("safety_policy_violation",))


def _result_verification_confirmed(result: dict[str, Any]) -> bool:
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    if str(summary.get("lifecycle_status") or "") in {"verified", "closed"}:
        return True
    if summary.get("verified") is True or summary.get("verification_confirmed") is True:
        return True
    for key in ("verify", "verification"):
        value = summary.get(key)
        if isinstance(value, dict) and str(value.get("overall_status") or value.get("status") or "") == "ok":
            return True
    return False
