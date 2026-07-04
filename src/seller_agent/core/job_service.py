from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from seller_agent.core.job_models import JobRecord
from seller_agent.core.job_store import DEFAULT_RUNTIME_DB, JobStore
from seller_agent.core.workflow_runner import WorkflowRunner
from seller_agent.tasks.registry import TaskRegistry, default_task_registry


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
    ) -> None:
        self.store = store or JobStore(runtime_db)
        self.registry = registry or default_task_registry()
        self.workflow_runner = workflow_runner or WorkflowRunner(registry=self.registry, data_dir=data_dir)

    def submit(
        self,
        *,
        task_id: str,
        params: dict[str, Any] | None = None,
        actor: str = "system",
        source: str = "",
    ) -> JobRecord:
        task = self.registry.get(task_id)
        job = self.store.create_job(
            task_id=task.name,
            params=params or {},
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
        if not task.is_read_only and not task.is_write:
            failed = self.store.update_job_status(
                job_id,
                "failed",
                error=f"JobService supports read-only tasks and explicitly confirmed apply tasks; `{task.name}` is `{task.mode}`.",
                message="Job blocked because task mode is unsupported.",
            )
            return JobServiceResult(job=failed, ok=False, status="blocked", message=failed.error)

        self.store.update_job_status(job_id, "running", message="Job runner started.")
        allowed_modes = {"read_only"} if task.is_read_only else {"apply"}
        result = self.workflow_runner.run_task(task.name, inputs=job.params, allowed_modes=allowed_modes)
        result_data = result.to_dict()

        if result.ok:
            final_status = "success" if result.status == "ok" else "partial_success"
            final = self.store.update_job_status(
                job_id,
                final_status,
                result=result_data,
                message=f"Read-only workflow finished with status `{result.status}`.",
            )
            return JobServiceResult(job=final, ok=True, status=result.status)

        final = self.store.update_job_status(
            job_id,
            "failed",
            result=result_data,
            error=result.error or result.blocked_reason or "workflow_failed",
            message=f"Read-only workflow failed with status `{result.status}`.",
        )
        return JobServiceResult(job=final, ok=False, status=result.status, message=final.error)

    def cancel(self, job_id: str, *, reason: str = "cancelled") -> JobServiceResult:
        job = self.store.get_job(job_id)
        if job is None:
            raise KeyError(f"Unknown job: {job_id}")
        if job.status not in {"created", "queued"}:
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
