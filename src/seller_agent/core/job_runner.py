from __future__ import annotations

from dataclasses import dataclass

from seller_agent.core.job_models import JobRecord
from seller_agent.core.job_service import JobService, JobServiceResult


@dataclass(frozen=True)
class JobRunnerResult:
    job: JobRecord | None
    ran: bool
    ok: bool
    message: str = ""


class JobRunner:
    """Runs queued jobs from JobService one at a time."""

    def __init__(self, service: JobService) -> None:
        self.service = service

    def run(self, job_id: str) -> JobServiceResult:
        return self.service.run(job_id)

    def run_next(self) -> JobRunnerResult:
        job = self.service.store.next_queued_job()
        if job is None:
            return JobRunnerResult(job=None, ran=False, ok=True, message="No queued jobs.")
        result = self.service.run(job.job_id)
        return JobRunnerResult(job=result.job, ran=True, ok=result.ok, message=result.message)
