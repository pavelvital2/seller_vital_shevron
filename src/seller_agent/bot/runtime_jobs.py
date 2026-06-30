from __future__ import annotations

from pathlib import Path
from typing import Any

from seller_agent.bot.commands import TelegramCommandResult
from seller_agent.core.job_service import JobService
from seller_agent.core.job_store import DEFAULT_RUNTIME_DB, JobStore


def dispatch_runtime_job_message(
    message: str,
    *,
    update_id: int,
    chat_id: int,
    data_dir: Path = Path("data"),
    runtime_db: Path = DEFAULT_RUNTIME_DB,
    live_today: bool = False,
    live_status: bool = False,
) -> TelegramCommandResult | None:
    runtime_request = _runtime_request_for_message(
        message,
        live_today=live_today,
        live_status=live_status,
    )
    if runtime_request is None:
        return None

    command, task_id, params = runtime_request
    store = JobStore(runtime_db)
    registered = store.register_telegram_update(
        update_id=update_id,
        chat_id=str(chat_id),
        command=command,
        payload={"message": message, "params": params},
        processing_status="received",
    )
    if not registered:
        existing = store.get_telegram_update(update_id)
        job_id = existing.job_id if existing else ""
        return TelegramCommandResult(
            command=command,
            ok=True,
            text=(
                _runtime_title(command)
                + "\n\n"
                + "Итог: повторный Telegram update не поставлен в очередь второй раз.\n\n"
                + f"Job ID: `{job_id or 'не найден'}`\n"
                + f"Runtime DB: `{runtime_db}`"
            ),
            artifacts={"runtime_db": str(runtime_db)},
        )

    service = JobService(store=store, data_dir=data_dir, runtime_db=runtime_db)
    job = service.submit(
        task_id=task_id,
        params=params,
        actor=f"telegram:{chat_id}",
        source="telegram",
    )
    store.update_telegram_update_status(update_id=update_id, processing_status="queued", job_id=job.job_id)
    return TelegramCommandResult(
        command=command,
        ok=True,
        text=(
            _runtime_title(command)
            + "\n\n"
            + "Итог: задача поставлена в runtime-очередь.\n\n"
            + f"Job ID: `{job.job_id}`\n"
            + f"Task: `{task_id}`\n"
            + f"Статус: `{job.status}`\n\n"
            + "Следующий слой: worker/timer выполнит очередь и отправит итоговый отчет. "
            + "Для ручной проверки можно выполнить `seller_agent.cli jobs run-next`."
        ),
        artifacts={"runtime_db": str(runtime_db)},
    )


def _runtime_request_for_message(
    message: str,
    *,
    live_today: bool,
    live_status: bool,
) -> tuple[str, str, dict[str, Any]] | None:
    command = str(message or "").strip().split(maxsplit=1)[0].lower()
    if command == "/status" and live_status:
        return ("/status", "status-preflight", {"include_lk": True})
    if command == "/today" and live_today:
        return (
            "/today",
            "daily-morning-report",
            {"refresh_preflight": True, "seller_v2": False, "seller_v3": True},
        )
    return None


def _runtime_title(command: str) -> str:
    if command == "/today":
        return "Ежедневный отчет"
    if command == "/status":
        return "Статус проекта"
    return "Runtime job"
