from __future__ import annotations

from dataclasses import dataclass
import uuid

from seller_agent.core.job_models import JobRecord
from seller_agent.core.job_service import JobService, JobServiceResult


@dataclass(frozen=True)
class JobRunnerResult:
    job: JobRecord | None
    ran: bool
    ok: bool
    deferred: bool = False
    message: str = ""


class JobRunner:
    """Runs queued jobs from JobService one at a time."""

    def __init__(
        self,
        service: JobService,
        *,
        worker_id: str | None = None,
        claim_ttl_seconds: int = 300,
    ) -> None:
        if claim_ttl_seconds <= 0:
            raise ValueError("claim_ttl_seconds must be positive")
        self.service = service
        self.worker_id = worker_id or f"job-runner:{uuid.uuid4().hex}"
        self.claim_ttl_seconds = claim_ttl_seconds

    def run(self, job_id: str) -> JobServiceResult:
        job = self.service.store.get_job(job_id)
        if job is None:
            raise KeyError(f"Unknown job: {job_id}")
        if job.status in {"created", "waiting_confirmation"}:
            job = self.service.store.update_job_status(
                job_id,
                "queued",
                message="Explicit runner queued job before claim acquisition.",
            )
        claim = self.service.store.claim_queued_job(
            job_id=job_id,
            worker_id=self.worker_id,
            ttl_seconds=self.claim_ttl_seconds,
        )
        if claim is None:
            job = self.service.store.get_job(job_id) or job
            return JobServiceResult(
                job=job,
                ok=False,
                status="claim_unavailable",
                message=f"Queued job `{job_id}` is already claimed or no longer available.",
            )
        return self.service.run(
            claim.job.job_id,
            claim_token=claim.claim_token,
            worker_id=claim.worker_id,
        )

    def run_next(self) -> JobRunnerResult:
        self.service.store.recover_stale_running_jobs()
        claim = self.service.store.claim_next_queued_job(
            worker_id=self.worker_id,
            ttl_seconds=self.claim_ttl_seconds,
        )
        if claim is None:
            return JobRunnerResult(job=None, ran=False, ok=True, message="No queued jobs.")
        result = self.service.run(
            claim.job.job_id,
            claim_token=claim.claim_token,
            worker_id=claim.worker_id,
        )
        deferred = result.status == "resource_locked" and result.job.status == "queued"
        return JobRunnerResult(
            job=result.job,
            ran=True,
            ok=result.ok or deferred,
            deferred=deferred,
            message=result.message,
        )
