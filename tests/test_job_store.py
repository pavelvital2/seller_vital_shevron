from __future__ import annotations

import sqlite3
from pathlib import Path

from seller_agent.core.job_store import JobStore


def test_job_store_creates_job_and_events(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "runtime.db")

    job = store.create_job(
        task_id="status-preflight",
        params={"include_lk": False},
        actor="telegram:123",
        job_id="job_test_1",
    )
    store.append_event(job_id=job.job_id, event_type="custom", message="step", data={"ok": True})
    updated = store.update_job_status(job.job_id, "success", result={"run_id": "status_test"})

    assert job.status == "created"
    assert updated.status == "success"
    assert updated.result == {"run_id": "status_test"}
    assert updated.finished_at

    events = store.list_events(job.job_id)
    assert [event.event_type for event in events] == ["job_created", "custom", "job_success"]
    assert events[1].data == {"ok": True}

    listed = store.list_jobs()
    assert [item.job_id for item in listed] == ["job_test_1"]


def test_job_store_deduplicates_telegram_updates(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "runtime.db")

    assert store.register_telegram_update(
        update_id=100,
        chat_id="42",
        command="/today",
        payload={"message": {"text": "/today"}},
    )
    assert not store.register_telegram_update(update_id=100, chat_id="42", command="/today")

    assert store.update_telegram_update_status(update_id=100, processing_status="queued", job_id="job_today")
    update = store.get_telegram_update(100)

    assert update is not None
    assert update.chat_id == "42"
    assert update.command == "/today"
    assert update.job_id == "job_today"
    assert update.processing_status == "queued"


def test_job_store_resource_lease_blocks_until_release_or_expiry(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "runtime.db")

    first = store.acquire_resource_lease(resource_key="wb_cards_write", owner_id="job_1", ttl_seconds=60)
    blocked = store.acquire_resource_lease(resource_key="wb_cards_write", owner_id="job_2", ttl_seconds=60)

    assert first is not None
    assert blocked is None
    assert store.release_resource_lease(resource_key="wb_cards_write", owner_id="job_2") is False
    assert store.release_resource_lease(resource_key="wb_cards_write", owner_id="job_1") is True

    second = store.acquire_resource_lease(resource_key="wb_cards_write", owner_id="job_2", ttl_seconds=60)
    assert second is not None
    assert second.owner_id == "job_2"


def test_job_store_approval_reserve_is_atomic(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "runtime.db")
    store.create_approval(
        approval_id="approved_reviews_1",
        source_job_id="job_source",
        status="approved",
        checksum="sha256:test",
    )

    assert store.reserve_approval_for_apply(approval_id="approved_reviews_1", owner_job_id="job_apply_1")
    assert not store.reserve_approval_for_apply(approval_id="approved_reviews_1", owner_job_id="job_apply_2")

    approval = store.get_approval("approved_reviews_1")
    assert approval is not None
    assert approval.status == "applying"
    assert approval.owner_job_id == "job_apply_1"


def test_job_store_initializes_schema_once(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    store = JobStore(db_path)

    store.initialize()
    store.initialize()

    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }

    assert {
        "jobs",
        "job_events",
        "task_requests",
        "approvals",
        "resource_leases",
        "telegram_updates",
    }.issubset(tables)
