from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
from pathlib import Path
from typing import Any

from seller_agent.core.job_service import JobService
from seller_agent.core.job_store import DEFAULT_RUNTIME_DB, JobStore


DEFAULT_OWNER_BOT_CHAT_ID = 867144591


@dataclass(frozen=True)
class ScheduledJobResult:
    ok: bool
    task_id: str
    schedule_key: str
    job_id: str = ""
    duplicate: bool = False
    error: str = ""


def enqueue_scheduled_job(
    *,
    task_id: str,
    params: dict[str, Any] | None = None,
    schedule_key: str | None = None,
    title: str = "",
    chat_id: int = DEFAULT_OWNER_BOT_CHAT_ID,
    thread_id: int | None = None,
    data_dir: Path = Path("data"),
    runtime_db: Path = DEFAULT_RUNTIME_DB,
) -> ScheduledJobResult:
    key = schedule_key or f"{task_id}:{date.today().isoformat()}"
    update_id = _synthetic_update_id(key)
    store = JobStore(runtime_db)
    payload = {
        "kind": "scheduled_job",
        "schedule_key": key,
        "params": dict(params or {}),
        "task_id": task_id,
        "title": title or task_id,
        "thread_id": thread_id,
    }
    registered = store.register_telegram_update(
        update_id=update_id,
        chat_id=str(chat_id),
        command=f"scheduled:{task_id}",
        payload=payload,
        processing_status="received",
    )
    if not registered:
        existing = store.get_telegram_update(update_id)
        return ScheduledJobResult(
            ok=True,
            task_id=task_id,
            schedule_key=key,
            job_id=existing.job_id if existing else "",
            duplicate=True,
        )
    try:
        service = JobService(store=store, data_dir=data_dir, runtime_db=runtime_db)
        job = service.submit(
            task_id=task_id,
            params=dict(params or {}),
            actor="systemd-timer",
            source=f"scheduled:{key}",
        )
    except Exception as exc:  # noqa: BLE001 - safe status for timer output.
        store.update_telegram_update_status(update_id=update_id, processing_status="enqueue_failed")
        return ScheduledJobResult(
            ok=False,
            task_id=task_id,
            schedule_key=key,
            error=str(exc).replace("\n", " ")[:300],
        )
    store.update_telegram_update_status(update_id=update_id, processing_status="queued", job_id=job.job_id)
    return ScheduledJobResult(ok=True, task_id=task_id, schedule_key=key, job_id=job.job_id)


def _synthetic_update_id(schedule_key: str) -> int:
    digest = hashlib.sha256(schedule_key.encode("utf-8")).digest()
    return -int.from_bytes(digest[:7], "big")
