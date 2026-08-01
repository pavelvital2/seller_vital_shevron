from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
from threading import Barrier, Event, Lock

import pytest

from seller_agent.core.job_runner import JobRunner
from seller_agent.core.job_service import JobService
from seller_agent.core.job_store import JobStore
from seller_agent.core.workflow_runner import WorkflowRunner
from seller_agent.safety.approval_package import build_approval_package


def _expire_claim(db_path: Path, job_id: str) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE job_claims SET expires_at = '1970-01-01T00:00:00Z' WHERE job_id = ?",
            (job_id,),
        )


def test_two_independent_stores_create_at_most_one_valid_claim(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    JobStore(db_path).create_job(task_id="pricing-status", status="queued", job_id="job-claim-race")
    stores = (JobStore(db_path), JobStore(db_path))
    start = Barrier(2)

    def claim(index: int):  # type: ignore[no-untyped-def]
        start.wait(timeout=5)
        return stores[index].claim_next_queued_job(worker_id=f"worker-{index}", ttl_seconds=60)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(claim, range(2)))

    valid_claims = [claim for claim in claims if claim is not None]
    assert len(valid_claims) == 1
    persisted = JobStore(db_path).get_job_claim("job-claim-race")
    assert persisted is not None
    assert persisted.claim_token == valid_claims[0].claim_token
    assert persisted.worker_id == valid_claims[0].worker_id


def test_two_independent_workers_call_handler_exactly_once(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    JobStore(db_path).create_job(task_id="pricing-status", status="queued", job_id="job-once")
    start = Barrier(2)
    calls: list[str] = []
    calls_lock = Lock()

    def handler(task, data_dir, credentials, inputs):  # type: ignore[no-untyped-def]
        with calls_lock:
            calls.append(task.name)
        return {"run_id": "claimed-once", "overall_status": "ok", "artifacts": {}}

    runners: list[JobRunner] = []
    for index in range(2):
        service = JobService(
            store=JobStore(db_path),
            workflow_runner=WorkflowRunner(
                data_dir=tmp_path / f"data-{index}",
                lock_dir=tmp_path / f"locks-{index}",
                handlers={"pricing-status": handler},
            ),
            data_dir=tmp_path / f"data-{index}",
        )
        runners.append(JobRunner(service, worker_id=f"worker-{index}"))

    def run_claimed(runner: JobRunner):  # type: ignore[no-untyped-def]
        start.wait(timeout=5)
        return runner.run_next()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run_claimed, runners))

    assert calls == ["pricing-status"]
    assert sum(result.ran for result in results) == 1
    assert JobStore(db_path).get_job("job-once").status == "success"  # type: ignore[union-attr]


def test_claim_rejects_wrong_owner_token_and_expired_token(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    store = JobStore(db_path)
    job = store.create_job(task_id="pricing-status", status="queued", job_id="job-token")
    claim = store.claim_next_queued_job(worker_id="worker-a", ttl_seconds=60)

    assert claim is not None
    assert len(claim.claim_token) >= 32
    assert store.start_claimed_job(
        job_id=job.job_id,
        claim_token="wrong-token",
        worker_id=claim.worker_id,
        execution_ttl_seconds=900,
    ) is None
    assert store.start_claimed_job(
        job_id=job.job_id,
        claim_token=claim.claim_token,
        worker_id="worker-b",
        execution_ttl_seconds=900,
    ) is None
    assert store.get_job(job.job_id).status == "queued"  # type: ignore[union-attr]

    _expire_claim(db_path, job.job_id)

    assert store.start_claimed_job(
        job_id=job.job_id,
        claim_token=claim.claim_token,
        worker_id=claim.worker_id,
        execution_ttl_seconds=900,
    ) is None
    assert store.get_job(job.job_id).status == "queued"  # type: ignore[union-attr]


def test_expired_claim_can_be_reclaimed_before_running(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    first_store = JobStore(db_path)
    second_store = JobStore(db_path)
    job = first_store.create_job(task_id="pricing-status", status="queued", job_id="job-reclaim")
    first = first_store.claim_next_queued_job(worker_id="worker-a", ttl_seconds=60)

    assert first is not None
    _expire_claim(db_path, job.job_id)
    second = second_store.claim_next_queued_job(worker_id="worker-b", ttl_seconds=60)

    assert second is not None
    assert second.job.job_id == job.job_id
    assert second.worker_id == "worker-b"
    assert second.claim_token != first.claim_token
    assert first_store.start_claimed_job(
        job_id=job.job_id,
        claim_token=first.claim_token,
        worker_id=first.worker_id,
        execution_ttl_seconds=900,
    ) is None
    running = second_store.start_claimed_job(
        job_id=job.job_id,
        claim_token=second.claim_token,
        worker_id=second.worker_id,
        execution_ttl_seconds=900,
    )
    assert running is not None
    assert running.status == "running"
    extended = second_store.get_job_claim(job.job_id)
    assert extended is not None
    assert extended.expires_at > second.expires_at
    assert second_store.recover_stale_running_jobs() == []
    assert second_store.get_job(job.job_id).status == "running"  # type: ignore[union-attr]


def test_running_write_is_not_replayed_after_worker_loss(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    first_store = JobStore(db_path)
    handler_started = Event()
    allow_late_return = Event()
    apply_calls: list[str] = []

    def delayed_apply_handler(task, data_dir, credentials, inputs):  # type: ignore[no-untyped-def]
        apply_calls.append(task.name)
        handler_started.set()
        if not allow_late_return.wait(timeout=10):
            raise RuntimeError("test did not release delayed apply handler")
        return {"run_id": "late-apply-result", "overall_status": "ok", "artifacts": {}}

    first_service = JobService(
        store=first_store,
        workflow_runner=WorkflowRunner(
            data_dir=tmp_path / "data-first",
            lock_dir=tmp_path / "locks-first",
            credentials=object(),  # type: ignore[arg-type]
            handlers={"ozon-elastic-apply": delayed_apply_handler},
        ),
        data_dir=tmp_path / "data-first",
    )
    task = first_service.registry.get("ozon-elastic-apply")
    package = build_approval_package(
        approval_id="approval-worker-loss",
        task_id=task.name,
        source_plan_task=task.source_plan_task,
        verify_task=task.verify_task,
        source_kind="plan_run_id",
        source_ref="offline-plan",
        apply_params={"plan_run_id": "offline-plan"},
        marketplaces=task.marketplaces,
    )
    first_store.create_approval(
        approval_id=package["approval_id"],
        source_job_id="plan-job",
        status="pending_review",
        checksum=package["approval_checksum"],
        data=package,
    )
    first_service.approve(package["approval_id"])
    job = first_service.submit_approval_apply(package["approval_id"])

    second_service = JobService(store=JobStore(db_path), data_dir=tmp_path / "data-second")
    with ThreadPoolExecutor(max_workers=1) as pool:
        old_worker_result = pool.submit(
            JobRunner(first_service, worker_id="worker-lost").run_next
        )
        try:
            assert handler_started.wait(timeout=5)
            running = first_store.get_job(job.job_id)
            running_claim = first_store.get_job_claim(job.job_id)
            assert apply_calls == ["ozon-elastic-apply"]
            assert running is not None
            assert running.status == "running"
            assert running_claim is not None
            _expire_claim(db_path, job.job_id)

            replay = JobRunner(second_service, worker_id="worker-replacement").run_next()
            assert replay.ran is False
            late_finish = first_store.finish_claimed_job(
                job_id=job.job_id,
                claim_token=running_claim.claim_token,
                worker_id=running_claim.worker_id,
                status="success",
                result={"overall_status": "ok"},
            )
            assert late_finish is None

            recovery = second_service.recover_runtime_approvals(run_verify=False)
            assert recovery["queued_verify_jobs"] == 1
            assert recovery["manual_verify_required"] == 0
            assert recovery["rows"][0]["action"] == "verify_job_queued"
            assert recovery["rows"][0]["verify_task"] == "ozon-elastic-verify"
        finally:
            allow_late_return.set()
        original_result = old_worker_result.result(timeout=5)

    assert original_result.ran is True
    assert original_result.ok is False
    assert original_result.job is not None
    assert original_result.job.status == "timeout"
    assert apply_calls == ["ozon-elastic-apply"]
    recovered_job = first_store.get_job(job.job_id)
    recovered_approval = first_store.get_approval(package["approval_id"])
    assert recovered_job is not None
    assert recovered_job.status == "timeout"
    assert recovered_job.error == "worker_lost_timeout"
    assert recovered_approval is not None
    assert recovered_approval.status == "applying_unknown"
    assert recovered_approval.owner_job_id == job.job_id
    assert first_store.get_job_claim(job.job_id) is None
    worker_lost_events = [
        event for event in first_store.list_events(job.job_id) if event.event_type == "job_worker_lost"
    ]
    assert len(worker_lost_events) == 1
    assert running_claim.claim_token not in worker_lost_events[0].message
    assert running_claim.worker_id not in worker_lost_events[0].message
    assert worker_lost_events[0].data == {
        "approvals_marked_unknown": 1,
        "reason": "worker_lost",
        "recovery_required": True,
        "status": "timeout",
    }
    event_types = [event.event_type for event in first_store.list_events(job.job_id)]
    assert "job_success" not in event_types
    assert "job_approval_closed" not in event_types
    assert [item.job_id for item in first_store.list_jobs(task_id="ozon-elastic-apply")] == [job.job_id]
    queued_jobs = first_store.list_jobs(status="queued")
    assert [item.task_id for item in queued_jobs] == ["ozon-elastic-verify"]


def test_job_runner_explicit_run_claims_created_job(tmp_path: Path) -> None:
    calls: list[str] = []
    claim_owners_during_handler: list[str] = []

    def handler(task, data_dir, credentials, inputs):  # type: ignore[no-untyped-def]
        calls.append(task.name)
        active_claim = store.get_job_claim(job.job_id)
        assert active_claim is not None
        claim_owners_during_handler.append(active_claim.worker_id)
        return {"run_id": "created-job", "overall_status": "ok", "artifacts": {}}

    store = JobStore(tmp_path / "runtime.db")
    job = store.create_job(task_id="pricing-status", status="created", job_id="job-created")
    service = JobService(
        store=store,
        workflow_runner=WorkflowRunner(
            data_dir=tmp_path / "data",
            lock_dir=tmp_path / "locks",
            handlers={"pricing-status": handler},
        ),
        data_dir=tmp_path / "data",
    )

    result = JobRunner(service, worker_id="worker-explicit").run(job.job_id)

    assert result.ok is True
    assert result.job.status == "success"
    assert calls == ["pricing-status"]
    assert claim_owners_during_handler == ["worker-explicit"]
    assert store.get_job_claim(job.job_id) is None
    assert [event.event_type for event in store.list_events(job.job_id)] == [
        "job_created",
        "job_queued",
        "job_running",
        "job_success",
    ]


def test_claiming_preserves_fifo_among_available_jobs(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    store = JobStore(db_path)
    for job_id in ("job-first", "job-second", "job-third"):
        store.create_job(task_id="pricing-status", status="queued", job_id=job_id)

    first = JobStore(db_path).claim_next_queued_job(worker_id="worker-a", ttl_seconds=60)
    second = JobStore(db_path).claim_next_queued_job(worker_id="worker-b", ttl_seconds=60)
    third = JobStore(db_path).claim_next_queued_job(worker_id="worker-c", ttl_seconds=60)

    assert [first.job.job_id, second.job.job_id, third.job.job_id] == [  # type: ignore[union-attr]
        "job-first",
        "job-second",
        "job-third",
    ]


def test_terminal_completion_releases_claim(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "runtime.db")
    job = store.create_job(task_id="pricing-status", status="queued", job_id="job-terminal")
    claim = store.claim_next_queued_job(worker_id="worker-a", ttl_seconds=60)

    assert claim is not None
    assert store.start_claimed_job(
        job_id=job.job_id,
        claim_token=claim.claim_token,
        worker_id=claim.worker_id,
        execution_ttl_seconds=900,
    ) is not None
    assert store.finish_claimed_job(
        job_id=job.job_id,
        claim_token="wrong-token",
        worker_id=claim.worker_id,
        status="success",
    ) is None
    assert store.finish_claimed_job(
        job_id=job.job_id,
        claim_token=claim.claim_token,
        worker_id="wrong-worker",
        status="success",
    ) is None
    with pytest.raises(ValueError, match="finish_claimed_job"):
        store.update_job_status(job.job_id, "success", result={"ok": True})
    finished = store.finish_claimed_job(
        job_id=job.job_id,
        claim_token=claim.claim_token,
        worker_id=claim.worker_id,
        status="success",
        result={"ok": True},
    )

    assert finished is not None
    assert finished.status == "success"
    assert store.get_job_claim(job.job_id) is None


def test_additive_job_claims_migration_preserves_existing_jobs(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE jobs (
              job_id TEXT PRIMARY KEY,
              task_id TEXT NOT NULL,
              status TEXT NOT NULL,
              actor TEXT NOT NULL DEFAULT '',
              params_json TEXT NOT NULL DEFAULT '{}',
              result_json TEXT NOT NULL DEFAULT '{}',
              error TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              started_at TEXT NOT NULL DEFAULT '',
              finished_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        connection.execute(
            """
            INSERT INTO jobs (job_id, task_id, status, created_at, updated_at)
            VALUES ('legacy-job', 'pricing-status', 'queued', '2026-08-01T00:00:00Z', '2026-08-01T00:00:00Z')
            """
        )

    store = JobStore(db_path)
    store.initialize()

    preserved = store.get_job("legacy-job")
    assert preserved is not None
    assert preserved.task_id == "pricing-status"
    assert preserved.status == "queued"
    with sqlite3.connect(db_path) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(job_claims)").fetchall()
        }
        row_count = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    assert columns == {"job_id", "claim_token", "worker_id", "claimed_at", "expires_at"}
    assert row_count == 1
