from __future__ import annotations

from pathlib import Path

from seller_agent.core.job_store import JobStore
from seller_agent.core.scheduled_jobs import DEFAULT_OWNER_BOT_CHAT_ID, enqueue_scheduled_job


def test_scheduled_job_is_deduplicated_by_schedule_key(tmp_path: Path) -> None:
    runtime_db = tmp_path / "runtime.db"
    first = enqueue_scheduled_job(
        task_id="liquidation-daily-control",
        params={"date_to": "2026-07-30"},
        schedule_key="liquidation-daily-control:2026-07-30",
        chat_id=-1001,
        thread_id=42,
        data_dir=tmp_path / "data",
        runtime_db=runtime_db,
    )
    second = enqueue_scheduled_job(
        task_id="liquidation-daily-control",
        params={"date_to": "2026-07-30"},
        schedule_key="liquidation-daily-control:2026-07-30",
        chat_id=-1001,
        thread_id=42,
        data_dir=tmp_path / "data",
        runtime_db=runtime_db,
    )

    assert first.ok is True
    assert first.duplicate is False
    assert first.job_id
    assert second.ok is True
    assert second.duplicate is True
    assert second.job_id == first.job_id


def test_scheduled_job_defaults_to_owner_manager_bot_without_topic(tmp_path: Path) -> None:
    runtime_db = tmp_path / "runtime.db"

    result = enqueue_scheduled_job(
        task_id="pricing-status",
        schedule_key="pricing-status:owner-bot-route",
        runtime_db=runtime_db,
        data_dir=tmp_path / "data",
    )

    updates = JobStore(runtime_db).list_telegram_updates(limit=10)
    assert result.ok is True
    assert updates[0].chat_id == str(DEFAULT_OWNER_BOT_CHAT_ID)
    assert updates[0].payload["thread_id"] is None
