from __future__ import annotations

from pathlib import Path

from seller_agent.core.job_runner import JobRunner
from seller_agent.core.resource_keys import WB_LK_PROFILE_KEY
from seller_agent.core.job_service import JobService
from seller_agent.core.job_store import JobStore
from seller_agent.core.job_worker import JobWorker
from seller_agent.core.workflow_runner import WorkflowRunner
from seller_agent.tasks.registry import RegisteredTask, TaskRegistry


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


def test_job_worker_defers_lease_busy_job_without_reselection_or_notification(
    tmp_path: Path,
) -> None:
    handler_calls: list[str] = []

    def handler(task, data_dir, credentials, inputs):  # type: ignore[no-untyped-def]
        handler_calls.append(task.name)
        return {"run_id": "lease_retry", "overall_status": "ok", "artifacts": {}}

    registry = TaskRegistry()
    registry.register(
        RegisteredTask(
            name="leased-read",
            command="leased-read",
            title="Leased read",
            description="test",
            mode="read_only",
            risk="low",
            lock_keys=(WB_LK_PROFILE_KEY,),
        )
    )
    store = JobStore(tmp_path / "runtime.db")
    assert store.acquire_resource_lease(
        resource_key=WB_LK_PROFILE_KEY,
        owner_id="session-refresh",
        ttl_seconds=60,
    ) is not None
    service = JobService(
        store=store,
        registry=registry,
        workflow_runner=WorkflowRunner(
            registry=registry,
            data_dir=tmp_path / "data",
            lock_dir=tmp_path / "locks",
            handlers={"leased-read": handler},
        ),
        data_dir=tmp_path / "data",
    )
    job = service.submit(task_id="leased-read")
    notified: list[str] = []
    worker = JobWorker(
        JobRunner(service),
        after_run=lambda item: notified.append(item.job_id),
    )

    summary = worker.run_loop(
        max_iterations=13,
        poll_interval_seconds=0,
        stop_when_empty=True,
    )

    assert summary.ok is True
    assert summary.iterations == 1
    assert summary.ran_jobs == 1
    assert summary.failed_jobs == 0
    assert summary.deferred_jobs == 1
    assert notified == []
    assert handler_calls == []
    assert store.get_job(job.job_id).status == "queued"  # type: ignore[union-attr]
    assert store.get_job_claim(job.job_id) is None
    assert [event.event_type for event in store.list_events(job.job_id)].count(
        "job_resource_blocked"
    ) == 1

    assert store.release_resource_lease(
        resource_key=WB_LK_PROFILE_KEY,
        owner_id="session-refresh",
    ) is True
    retry = worker.run_once()

    assert retry.ok is True
    assert retry.deferred is False
    assert retry.job is not None
    assert retry.job.status == "success"
    assert handler_calls == ["leased-read"]
    assert notified == [job.job_id]


def test_job_service_runs_dry_run_tasks_through_worker(tmp_path: Path) -> None:
    def handler(task, data_dir, credentials, inputs):  # type: ignore[no-untyped-def]
        return {"run_id": "dry_run_test", "overall_status": "ok", "artifacts": {}}

    store = JobStore(tmp_path / "runtime.db")
    service = JobService(
        store=store,
        workflow_runner=WorkflowRunner(
            data_dir=tmp_path / "data",
            lock_dir=tmp_path / "locks",
            handlers={"ozon-elastic-plan": handler},
        ),
        data_dir=tmp_path / "data",
    )
    job = service.submit(task_id="ozon-elastic-plan")

    result = JobWorker(JobRunner(service)).run_once()

    assert result.ok is True
    assert result.job is not None
    assert result.job.job_id == job.job_id
    assert result.job.status == "success"


def test_job_worker_processes_oldest_queued_job_first(tmp_path: Path) -> None:
    def handler(task, data_dir, credentials, inputs):  # type: ignore[no-untyped-def]
        return {"run_id": inputs["run_id"], "overall_status": "ok", "artifacts": {}}

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
    first = service.submit(task_id="status-preflight", params={"run_id": "first"})
    service.submit(task_id="status-preflight", params={"run_id": "second"})

    result = JobWorker(JobRunner(service)).run_once()

    assert result.job is not None
    assert result.job.job_id == first.job_id
