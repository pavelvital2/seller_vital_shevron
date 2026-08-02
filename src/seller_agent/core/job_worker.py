from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Callable

from seller_agent.core.job_models import JobRecord
from seller_agent.core.job_runner import JobRunner
from seller_agent.core.job_store import TERMINAL_JOB_STATUSES


AfterRunHook = Callable[[JobRecord], None]


@dataclass(frozen=True)
class JobWorkerIteration:
    ran: bool
    ok: bool
    deferred: bool = False
    job: JobRecord | None = None
    message: str = ""


@dataclass(frozen=True)
class JobWorkerSummary:
    ok: bool
    iterations: int
    ran_jobs: int
    failed_jobs: int
    deferred_jobs: int
    messages: list[str] = field(default_factory=list)


class JobWorker:
    """Small controllable worker loop for queued runtime jobs."""

    def __init__(self, runner: JobRunner, *, after_run: AfterRunHook | None = None) -> None:
        self.runner = runner
        self.after_run = after_run

    def run_once(self) -> JobWorkerIteration:
        result = self.runner.run_next()
        if (
            result.job is not None
            and result.job.status in TERMINAL_JOB_STATUSES
            and self.after_run is not None
        ):
            self.after_run(result.job)
        return JobWorkerIteration(
            ran=result.ran,
            ok=result.ok,
            deferred=result.deferred,
            job=result.job,
            message=result.message,
        )

    def run_loop(
        self,
        *,
        max_iterations: int = 1,
        poll_interval_seconds: float = 1.0,
        stop_when_empty: bool = False,
    ) -> JobWorkerSummary:
        iterations = 0
        ran_jobs = 0
        failed_jobs = 0
        deferred_jobs = 0
        messages: list[str] = []
        while iterations < max_iterations:
            iterations += 1
            item = self.run_once()
            if item.ran:
                ran_jobs += 1
                if item.deferred:
                    deferred_jobs += 1
                elif not item.ok:
                    failed_jobs += 1
            if item.message:
                messages.append(item.message)
            if item.deferred:
                break
            if stop_when_empty and not item.ran:
                break
            if iterations < max_iterations:
                time.sleep(poll_interval_seconds)
        return JobWorkerSummary(
            ok=failed_jobs == 0,
            iterations=iterations,
            ran_jobs=ran_jobs,
            failed_jobs=failed_jobs,
            deferred_jobs=deferred_jobs,
            messages=messages,
        )
