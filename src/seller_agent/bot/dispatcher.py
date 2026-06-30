from __future__ import annotations

from pathlib import Path

from seller_agent.bot.commands import TelegramCommandResult, handle_telegram_callback, handle_telegram_command
from seller_agent.core.job_store import DEFAULT_RUNTIME_DB
from seller_agent.tasks.registry import TaskRegistry, default_task_registry


def build_registry() -> TaskRegistry:
    return default_task_registry()


def telegram_tasks() -> list[dict]:
    return build_registry().to_list(telegram_only=True)


def dispatch_message(
    message: str,
    *,
    data_dir: Path = Path("data"),
    live_today: bool = False,
    live_status: bool = False,
    runtime_db: Path = DEFAULT_RUNTIME_DB,
) -> TelegramCommandResult:
    return handle_telegram_command(
        message,
        data_dir=data_dir,
        live_today=live_today,
        live_status=live_status,
        runtime_db=runtime_db,
    )


def dispatch_callback(
    callback_data: str,
    *,
    data_dir: Path = Path("data"),
) -> TelegramCommandResult:
    return handle_telegram_callback(callback_data, data_dir=data_dir)
