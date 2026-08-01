from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from seller_agent.core.job_models import ApprovalRecord, ApprovalStatus, JobRecord
from seller_agent.core.job_store import DEFAULT_RUNTIME_DB, JobStore
from seller_agent.core.workflow_runner import WorkflowRunner
from seller_agent.core.run_manifest import close_run_manifest
from seller_agent.tasks.registry import RegisteredTask, TaskRegistry, default_task_registry
from seller_agent.safety.approval_package import build_approval_package
from seller_agent.safety.guard import SafetyGuard


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
        job_params = dict(params or {})
        runtime_approval = _runtime_approval_for_submit(task, job_params) if task.is_write else None
        job = self.store.create_job(
            task_id=task.name,
            params=job_params,
            actor=actor,
            status="queued",
            source=source,
        )
        if runtime_approval is not None:
            approval = self.store.ensure_approval(
                approval_id=runtime_approval["approval_id"],
                source_job_id=job.job_id,
                status="approved",
                checksum=runtime_approval["approval_checksum"],
                data=runtime_approval,
            )
            if approval.status == "pending_review" and approval.checksum == runtime_approval["approval_checksum"]:
                self.store.decide_approval(approval_id=approval.approval_id, status="approved")
                approval = self.store.get_approval(approval.approval_id) or approval
            self.store.append_event(
                job_id=job.job_id,
                event_type="job_runtime_approval_attached",
                message="Runtime approval attached to confirmed write job.",
                data={
                    "approval_id": approval.approval_id,
                    "approval_status": approval.status,
                    "source_kind": runtime_approval["source_kind"],
                },
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

    def run(self, job_id: str) -> JobServiceResult:
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
        if task.is_write and not _confirmed(job.params):
            waiting = self.store.update_job_status(
                job_id,
                "waiting_confirmation",
                error="confirmed_by_user=true is required for apply jobs.",
                message="Job is waiting for explicit owner confirmation.",
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
            approval_id = _runtime_approval_id(job.params)
            approval = self.store.get_approval(approval_id) if approval_id else None
            decision = self.safety_guard.validate_apply(task=task, job=job, approval=approval)
            if not decision.allowed:
                blocked_status = "safety_blocked"
                event_type = "job_safety_guard_blocked"
                if "approval_record_checksum_mismatch" in decision.issues:
                    blocked_status = "approval_checksum_mismatch"
                    event_type = "job_approval_checksum_mismatch"
                elif any(issue.startswith("approval_status_invalid:") for issue in decision.issues):
                    blocked_status = "approval_not_available"
                    event_type = "job_approval_blocked"
                elif "approval_record_missing" in decision.issues:
                    blocked_status = "approval_missing"
                failed = self.store.update_job_status(
                    job_id,
                    "failed",
                    error="SafetyGuard blocked apply: " + ", ".join(decision.issues),
                    message="Job blocked by centralized SafetyGuard before resource acquisition.",
                )
                self.store.append_event(job_id=job_id, event_type=event_type, message="Centralized SafetyGuard blocked apply.", data={"issues": list(decision.issues)})
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
                approval_id = _runtime_approval_id(job.params)
                if approval_id:
                    approval = self.store.get_approval(approval_id)
                    expected_checksum = _runtime_approval_checksum(job.params)
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

            self.store.update_job_status(job_id, "running", message="Job runner started.")
            if task.is_write:
                self.store.append_event(job_id=job_id, event_type="job_write_window_started", message="Apply workflow started after approval, checksum and lease gates.", data={"approval_id": reserved_approval_id, "write_started": True})
            allowed_modes = _allowed_modes_for_task(task)
            result = self.workflow_runner.run_task(task.name, inputs=job.params, allowed_modes=allowed_modes)
            result_data = result.to_dict()

            if result.ok:
                final_status = "success" if result.status == "ok" else "partial_success"
                if reserved_approval_id:
                    self.store.update_approval_status(approval_id=reserved_approval_id, status="applied")
                    if _result_verification_confirmed(result_data):
                        self.store.update_approval_status(approval_id=reserved_approval_id, status="verified")
                        self.store.update_approval_status(approval_id=reserved_approval_id, status="closed")
                        self.store.append_event(job_id=job_id, event_type="job_approval_closed", message="Apply result contained explicit successful verification; approval lifecycle closed.", data={"approval_id": reserved_approval_id})
                        approval = self.store.get_approval(reserved_approval_id)
                        source_run_id = _approval_source_run_id(approval) if approval else ""
                        apply_summary = result_data.get("summary") if isinstance(result_data.get("summary"), dict) else {}
                        if source_run_id:
                            close_run_manifest(
                                data_dir=self.data_dir,
                                run_id=source_run_id,
                                applied_by_run_id=str(apply_summary.get("run_id") or job_id),
                            )
                elif task.mode == "verify":
                    recovery_approval_id = _runtime_approval_id(job.params)
                    if recovery_approval_id and _result_verification_confirmed(result_data):
                        self.store.update_approval_status(approval_id=recovery_approval_id, status="verified")
                        self.store.update_approval_status(approval_id=recovery_approval_id, status="closed")
                elif task.mode == "dry_run":
                    self._register_plan_approval(job=job, task=task, result_data=result_data)
                final = self.store.update_job_status(
                    job_id,
                    final_status,
                    result=result_data,
                    message=f"Workflow finished with status `{result.status}`.",
                )
                return JobServiceResult(job=final, ok=True, status=result.status)

            if reserved_approval_id:
                self.store.update_approval_status(approval_id=reserved_approval_id, status="applying_unknown")
            final = self.store.update_job_status(
                job_id,
                "failed",
                result=result_data,
                error=result.error or result.blocked_reason or "workflow_failed",
                message=f"Workflow failed with status `{result.status}`.",
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
        if not self.store.decide_approval(approval_id=approval_id, status="approved"):
            current = self.store.get_approval(approval_id)
            if current is None:
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

    def submit_approval_apply(self, approval_id: str, *, actor: str = "owner") -> JobRecord:
        approval = self.store.get_approval(approval_id)
        if approval is None:
            raise KeyError(f"Unknown approval: {approval_id}")
        if approval.status != "approved":
            raise RuntimeError(f"Approval `{approval_id}` is `{approval.status}`, expected `approved`.")
        params = dict(approval.data.get("apply_params") or {})
        params.update(
            {
                "confirmed_by_user": True,
                "approval_id": approval.approval_id,
                "approval_checksum": approval.checksum,
            }
        )
        return self.submit(task_id=str(approval.data.get("task_id") or ""), params=params, actor=actor, source="runtime_approval")

    def submit_approval_verify(self, approval_id: str, *, actor: str = "owner") -> JobRecord:
        approval = self.store.get_approval(approval_id)
        if approval is None:
            raise KeyError(f"Unknown approval: {approval_id}")
        if approval.status not in {"applied", "applying_unknown"}:
            raise RuntimeError(f"Approval `{approval_id}` is `{approval.status}`, verify is not available.")
        request = self._build_recovery_verify_request(approval)
        if not request["ok"]:
            raise RuntimeError(str(request["blocked_reason"]))
        return self.submit(task_id=request["task_id"], params=request["params"], actor=actor, source="runtime_approval_verify")

    def _register_plan_approval(
        self,
        *,
        job: JobRecord,
        task: RegisteredTask,
        result_data: dict[str, Any],
    ) -> ApprovalRecord | None:
        apply_tasks = [candidate for candidate in self.registry.list() if candidate.is_write and candidate.source_plan_task == task.name]
        if len(apply_tasks) != 1:
            return None
        summary = result_data.get("summary") if isinstance(result_data.get("summary"), dict) else {}
        source_ref = str(summary.get("run_id") or result_data.get("run_id") or "").strip()
        if not source_ref:
            return None
        apply_task = apply_tasks[0]
        source_key = "source_run_id" if task.name.endswith("inbox") else "plan_run_id"
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
        statuses: tuple[ApprovalStatus, ...] = ("applying", "applying_unknown"),
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
            request = self._build_recovery_verify_request(approval)
            if not request["ok"]:
                rows.append({**row, "action": "manual_verify_required", "blocked_reason": request["blocked_reason"]})
                continue

            verify_job = self.submit(
                task_id=request["task_id"],
                params=request["params"],
                actor=actor,
                source="runtime_approval_recovery",
            )
            row.update(
                {
                    "action": "verify_job_queued",
                    "verify_job_id": verify_job.job_id,
                    "verify_task": verify_job.task_id,
                    "verify_params": verify_job.params,
                }
            )
            if run_verify:
                verify_result = self.run(verify_job.job_id)
                verify_confirmed = verify_result.ok and verify_result.status == "ok"
                row.update(
                    {
                        "action": "verify_job_ran",
                        "verify_job_status": verify_result.job.status,
                        "verify_result_status": verify_result.status,
                        "verify_ok": verify_confirmed,
                    }
                )
                if verify_confirmed:
                    self.store.update_approval_status(approval_id=approval.approval_id, status="verified")
                    self.store.update_approval_status(approval_id=approval.approval_id, status="closed")
                    row["approval_status_after"] = "closed"
                else:
                    current = self.store.get_approval(approval.approval_id)
                    row["approval_status_after"] = current.status if current else approval.status
            rows.append(row)

        blocked = [row for row in rows if row.get("action") == "manual_verify_required"]
        failed_verify = [row for row in rows if row.get("action") == "verify_job_ran" and not row.get("verify_ok")]
        return {
            "overall_status": "warning" if blocked or failed_verify else "ok",
            "checked_count": len(approvals),
            "queued_verify_jobs": sum(1 for row in rows if row.get("verify_job_id")),
            "manual_verify_required": len(blocked),
            "failed_verify_jobs": len(failed_verify),
            "run_verify": run_verify,
            "rows": rows,
        }

    def _build_recovery_verify_request(self, approval: ApprovalRecord) -> dict[str, Any]:
        task_id = str(approval.data.get("task_id") or "")
        if not task_id:
            return {"ok": False, "blocked_reason": "approval_missing_task_id"}
        try:
            apply_task = self.registry.get(task_id)
        except KeyError:
            return {"ok": False, "blocked_reason": "approval_unknown_task_id"}
        if not apply_task.enabled:
            return {"ok": False, "blocked_reason": "apply_task_disabled"}

        verify_task_id = str(approval.data.get("verify_task") or apply_task.verify_task or "")
        if not verify_task_id:
            return {"ok": False, "blocked_reason": "approval_missing_verify_task"}
        try:
            verify_task = self.registry.get(verify_task_id)
        except KeyError:
            return {"ok": False, "blocked_reason": "approval_unknown_verify_task"}
        if verify_task.is_write:
            return {"ok": False, "blocked_reason": "verify_task_is_write"}
        if verify_task.mode not in {"verify", "read_only", "dry_run"}:
            return {"ok": False, "blocked_reason": f"verify_task_mode_not_safe:{verify_task.mode}"}

        params = _runtime_recovery_verify_params(approval)
        if not params:
            return {"ok": False, "blocked_reason": "approval_missing_recoverable_params"}
        params["runtime_recovery_approval_id"] = approval.approval_id
        params["runtime_recovery_source_job_id"] = approval.source_job_id
        params["runtime_recovery_apply_task"] = apply_task.name
        return {"ok": True, "task_id": verify_task.name, "params": params}

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


def _allowed_modes_for_task(task: RegisteredTask) -> set[str]:
    if task.is_read_only:
        return {"read_only"}
    if task.mode == "dry_run":
        return {"dry_run"}
    if task.mode == "verify":
        return {"verify"}
    return {"apply"}


def _runtime_approval_id(params: dict[str, Any]) -> str:
    return str(params.get("approval_id") or params.get("runtime_approval_id") or "").strip()


def _runtime_approval_checksum(params: dict[str, Any]) -> str:
    return str(params.get("approval_checksum") or params.get("runtime_approval_checksum") or "").strip()


def _runtime_approval_for_submit(task: RegisteredTask, params: dict[str, Any]) -> dict[str, Any] | None:
    if not _confirmed(params) or _runtime_approval_id(params):
        return None
    approval_source = _runtime_approval_source(params)
    approval_payload = _approval_payload(params)
    approval_id = f"runtime:{task.name}:{_canonical_hash({'task_id': task.name, 'source': approval_source})[:24]}"
    package = build_approval_package(
        approval_id=approval_id,
        task_id=task.name,
        source_plan_task=task.source_plan_task,
        verify_task=task.verify_task,
        source_kind=approval_source["kind"],
        source_ref=approval_source["ref"],
        apply_params=approval_payload,
        marketplaces=task.marketplaces,
    )
    approval_checksum = package["approval_checksum"]
    params["approval_id"] = approval_id
    params["approval_checksum"] = approval_checksum
    return package


def _runtime_approval_source(params: dict[str, Any]) -> dict[str, str]:
    for key in (
        "approved_path",
        "plan_run_id",
        "source_run_id",
        "pending_id",
        "approved_id",
        "base_plan_run_id",
        "internal_skus",
    ):
        value = params.get(key)
        if _has_runtime_source_value(value):
            return {"kind": key, "ref": _canonical_json(value)}
    return {"kind": "params", "ref": _canonical_hash(_approval_payload(params))}


def _has_runtime_source_value(value: Any) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, (list, tuple, set, dict)) and not value:
        return False
    return True


def _approval_payload(params: dict[str, Any]) -> dict[str, Any]:
    ignored = {
        "approval_id",
        "runtime_approval_id",
        "approval_checksum",
        "runtime_approval_checksum",
        "confirmed_by_user",
    }
    return {key: value for key, value in sorted(params.items()) if key not in ignored}


def _runtime_recovery_verify_params(approval: ApprovalRecord) -> dict[str, Any]:
    raw = approval.data.get("verify_params")
    if isinstance(raw, dict) and raw:
        return dict(raw)
    apply_params = approval.data.get("apply_params")
    if not isinstance(apply_params, dict):
        return {}
    ignored = {
        "approval_id",
        "runtime_approval_id",
        "approval_checksum",
        "runtime_approval_checksum",
        "confirmed_by_user",
        "run_id",
    }
    return {key: value for key, value in sorted(apply_params.items()) if key not in ignored}


def _approval_source_run_id(approval: ApprovalRecord) -> str:
    if str(approval.data.get("source_kind") or "") not in {"plan_run_id", "source_run_id"}:
        return ""
    value = approval.data.get("source_ref")
    try:
        decoded = json.loads(str(value))
    except (json.JSONDecodeError, TypeError):
        decoded = value
    return str(decoded or "").strip()


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


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
