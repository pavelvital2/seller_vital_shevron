from __future__ import annotations

from pathlib import Path

from seller_agent.core.job_runner import JobRunner
from seller_agent.core.job_service import JobService
from seller_agent.core.job_store import JobStore
from seller_agent.core.job_worker import JobWorker
from seller_agent.core.workflow_runner import WorkflowRunner


def test_job_worker_runs_queued_job_and_calls_hook(tmp_path: Path) -> None:
    def handler(task, data_dir, credentials, inputs):  # type: ignore[no-untyped-def]
        return {"run_id": "worker_test", "overall_status": "ok", "artifacts": {}}

    store = JobStore(tmp_path / "runtime.db")
    service = JobService(
        store=store,
        workflow_runner=WorkflowRunner(
            data_dir=tmp_path / "data",
            lock_dir=tmp_path / "locks",
            handlers={"status-preflight": handler},
        ),
        data_dir=tmp_path / "data",
    )
    job = service.submit(task_id="status-preflight")
    notified: list[str] = []

    summary = JobWorker(JobRunner(service), after_run=lambda item: notified.append(item.job_id)).run_loop(
        max_iterations=1,
        poll_interval_seconds=0,
    )

    assert summary.ok is True
    assert summary.iterations == 1
    assert summary.ran_jobs == 1
    assert notified == [job.job_id]
    assert store.get_job(job.job_id).status == "success"  # type: ignore[union-attr]


def test_job_worker_stops_when_empty(tmp_path: Path) -> None:
    service = JobService(store=JobStore(tmp_path / "runtime.db"), data_dir=tmp_path / "data")

    summary = JobWorker(JobRunner(service)).run_loop(
        max_iterations=5,
        poll_interval_seconds=0,
        stop_when_empty=True,
    )

    assert summary.ok is True
    assert summary.iterations == 1
    assert summary.ran_jobs == 0
