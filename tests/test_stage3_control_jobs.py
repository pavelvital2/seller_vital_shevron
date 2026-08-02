from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from seller_agent.core.job_service import JobService
from seller_agent.core.job_store import ControlRequestConflictError, JobStore
from seller_agent.tasks.registry import RegisteredTask, TaskRegistry


def _registry() -> TaskRegistry:
    registry = TaskRegistry()
    registry.register(
        RegisteredTask(
            name="store-analytics-overview",
            command="store-analytics-overview",
            title="Overview",
            description="Owner analytics.",
            mode="read_only",
            risk="low",
            marketplaces=("ozon", "wb"),
            parameter_schema={
                "marketplace": {"type": "string", "enum": ["ozon", "wb"], "required": True},
                "period_days": {"type": "integer", "enum": [7, 30, 90], "required": True},
                "region_id": {
                    "type": "string",
                    "enum": ["moscow", "rostov-on-don", "novosibirsk", "kazan"],
                    "required": True,
                },
            },
            result_schema={
                "overall_status": {"type": "string", "required": True},
                "run_id": {"type": "string", "required": True},
            },
        )
    )
    registry.register(
        RegisteredTask(
            name="unsafe-write",
            command="unsafe-write",
            title="Write",
            description="Must not be submitted by control API.",
            mode="apply",
            risk="high",
            requires_confirmation=True,
        )
    )
    return registry


def test_control_submit_is_atomic_idempotent_and_owner_scoped(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "runtime.db")
    service = JobService(store=store, registry=_registry(), data_dir=tmp_path / "data")
    params = {"marketplace": "ozon", "period_days": 30, "region_id": "moscow"}

    first = service.submit_control_read_only(
        task_id="store-analytics-overview",
        params=params,
        owner_id="42",
        chat_id="42",
        idempotency_key="idem-key-1",
        allowed_task_ids={"store-analytics-overview"},
    )
    restarted_service = JobService(
        store=JobStore(tmp_path / "runtime.db"),
        registry=_registry(),
        data_dir=tmp_path / "data",
    )
    duplicate = restarted_service.submit_control_read_only(
        task_id="store-analytics-overview",
        params=params,
        owner_id="42",
        chat_id="42",
        idempotency_key="idem-key-1",
        allowed_task_ids={"store-analytics-overview"},
    )

    assert first.created is True
    assert duplicate.created is False
    assert duplicate.public_job_id == first.public_job_id
    assert duplicate.job.job_id == first.job.job_id
    assert store.get_control_job_for_owner(owner_id="42", public_job_id=first.public_job_id)
    assert store.get_control_job_for_owner(owner_id="7", public_job_id=first.public_job_id) is None

    other_owner = service.submit_control_read_only(
        task_id="store-analytics-overview",
        params=params,
        owner_id="7",
        chat_id="7",
        idempotency_key="idem-key-1",
        allowed_task_ids={"store-analytics-overview"},
    )
    assert other_owner.job.job_id != first.job.job_id
    with sqlite3.connect(tmp_path / "runtime.db") as connection:
        persisted = "\n".join(
            str(value)
            for row in connection.execute("SELECT * FROM control_request_receipts")
            for value in row
        )
    assert "idem-key-1" not in persisted


def test_concurrent_duplicate_control_submit_creates_one_job(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"

    def submit() -> tuple[str, str]:
        service = JobService(
            store=JobStore(db_path),
            registry=_registry(),
            data_dir=tmp_path / "data",
        )
        result = service.submit_control_read_only(
            task_id="store-analytics-overview",
            params={"marketplace": "wb", "period_days": 30, "region_id": "kazan"},
            owner_id="42",
            chat_id="42",
            idempotency_key="concurrent-idempotency-key",
            allowed_task_ids={"store-analytics-overview"},
        )
        return result.job.job_id, result.public_job_id

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(lambda _: submit(), range(5)))

    assert len(set(results)) == 1
    assert len(JobStore(db_path).list_jobs(limit=20)) == 1


def test_control_submit_conflicting_payload_and_unsafe_tasks_fail_closed(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "runtime.db")
    service = JobService(store=store, registry=_registry(), data_dir=tmp_path / "data")
    common = {
        "owner_id": "42",
        "chat_id": "42",
        "idempotency_key": "idem-key-2",
        "allowed_task_ids": {"store-analytics-overview"},
    }
    service.submit_control_read_only(
        task_id="store-analytics-overview",
        params={"marketplace": "wb", "period_days": 7, "region_id": "moscow"},
        **common,
    )
    with pytest.raises(ControlRequestConflictError):
        service.submit_control_read_only(
            task_id="store-analytics-overview",
            params={"marketplace": "wb", "period_days": 90, "region_id": "moscow"},
            **common,
        )
    with pytest.raises(ValueError, match="task_not_allowed"):
        service.submit_control_read_only(
            task_id="unsafe-write",
            params={},
            owner_id="42",
            chat_id="42",
            idempotency_key="idem-write",
            allowed_task_ids={"store-analytics-overview"},
        )
    with pytest.raises(ValueError, match="task_not_registered"):
        service.submit_control_read_only(
            task_id="arbitrary-task",
            params={},
            owner_id="42",
            chat_id="42",
            idempotency_key="idem-unknown",
            allowed_task_ids={"store-analytics-overview"},
        )
    with pytest.raises(ValueError, match="params_invalid"):
        service.submit_control_read_only(
            task_id="store-analytics-overview",
            params={"marketplace": "ozon", "period_days": 5, "secret": "no"},
            owner_id="42",
            chat_id="42",
            idempotency_key="idem-invalid",
            allowed_task_ids={"store-analytics-overview"},
        )
    assert len(store.list_jobs(limit=20)) == 1


def test_control_schema_migration_preserves_existing_jobs(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE jobs (
              job_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, status TEXT NOT NULL,
              actor TEXT NOT NULL DEFAULT '', params_json TEXT NOT NULL DEFAULT '{}',
              result_json TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              started_at TEXT NOT NULL DEFAULT '', finished_at TEXT NOT NULL DEFAULT ''
            );
            INSERT INTO jobs VALUES (
              'legacy-job', 'status-preflight', 'success', 'legacy', '{}', '{}', '',
              '2026-08-01T00:00:00Z', '2026-08-01T00:00:00Z', '', '2026-08-01T00:01:00Z'
            );
            """
        )

    store = JobStore(db_path)
    store.initialize()

    assert store.get_job("legacy-job") is not None
    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        migrations = {
            row[0]
            for row in connection.execute("SELECT migration_name FROM schema_migrations")
        }
    assert {
        "control_auth_replays",
        "control_request_receipts",
        "job_notification_routes",
    } <= tables
    assert "control_plane_schema_v1" in migrations
