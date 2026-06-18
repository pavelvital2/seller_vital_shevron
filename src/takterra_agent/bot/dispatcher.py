from __future__ import annotations

from pathlib import Path

from takterra_agent.bot.commands import TelegramCommandResult, handle_telegram_command
from takterra_agent.tasks.registry import TaskRegistry, default_task_registry


def build_registry() -> TaskRegistry:
    return default_task_registry()


def telegram_tasks() -> list[dict]:
    return build_registry().to_list(telegram_only=True)


def dispatch_message(message: str, *, data_dir: Path = Path("data")) -> TelegramCommandResult:
    return handle_telegram_command(message, data_dir=data_dir)
