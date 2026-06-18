from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from takterra_agent.core.run_manifest import latest_run
from takterra_agent.tasks.approvals import run_approvals_status
from takterra_agent.tasks.registry import default_task_registry


SUPPORTED_READ_ONLY_COMMANDS = {
    "/help",
    "/status",
    "/today",
    "/reviews",
    "/approvals",
    "/catalog",
    "/runs",
}

TELEGRAM_TITLES = {
    "/approvals": "Согласования",
    "/catalog": "Каталог",
    "/help": "Помощь",
    "/reviews": "Отзывы и вопросы",
    "/runs": "Запуски",
    "/status": "Статус проекта",
    "/today": "Ежедневный отчет",
}


@dataclass(frozen=True)
class TelegramCommandResult:
    command: str
    ok: bool
    text: str
    mode: str = "read_only"
    artifacts: dict[str, str] = field(default_factory=dict)
    blocked_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def handle_telegram_command(
    message: str,
    *,
    data_dir: Path = Path("data"),
) -> TelegramCommandResult:
    command = _normalize_command(message)
    if command == "/help":
        return _help()
    if command == "/status":
        return _latest_run_command(
            command=command,
            title="Статус проекта",
            task="status-preflight",
            data_dir=data_dir,
            next_step="Если данные устарели, запустить read-only `status-preflight`.",
        )
    if command == "/today":
        return _latest_run_command(
            command=command,
            title="Ежедневный отчет",
            task="daily-morning-report",
            data_dir=data_dir,
            next_step="Если нужен свежий отчет, запустить read-only `daily-morning-report --seller-v3`.",
        )
    if command == "/reviews":
        return _latest_run_command(
            command=command,
            title="Отзывы и вопросы",
            task="reviews-questions",
            data_dir=data_dir,
            next_step="Если нужен свежий список, запустить dry-run `reviews-questions --marketplace all`.",
            mode_label="dry-run/read-only",
        )
    if command == "/catalog":
        return _latest_run_command(
            command=command,
            title="Каталог",
            task="catalog-fetch",
            data_dir=data_dir,
            next_step="Если нужен свежий каталог, запустить read-only `fetch-catalog`.",
        )
    if command == "/runs":
        return _runs(data_dir=data_dir)
    if command == "/approvals":
        return _approvals(data_dir=data_dir)
    return TelegramCommandResult(
        command=command,
        ok=False,
        blocked_reason="unsupported_command",
        text=(
            "Команда не поддерживается в read-only Telegram MVP.\n\n"
            "Доступные команды: "
            + ", ".join(sorted(SUPPORTED_READ_ONLY_COMMANDS))
        ),
    )


def _help() -> TelegramCommandResult:
    registry = default_task_registry()
    tasks = registry.list(telegram_only=True)
    lines = [
        "Telegram MVP",
        "",
        "Итог: доступны только read-only/maintenance экраны. Write-кнопок нет.",
        "",
        "Команды:",
    ]
    for task in tasks:
        label = task.telegram_button_label or f"/{task.command}"
        if label not in SUPPORTED_READ_ONLY_COMMANDS:
            continue
        mode = "read-only" if task.mode == "read_only" else task.mode
        lines.append(f"- `{label}` - {TELEGRAM_TITLES.get(label, task.title)}; режим `{mode}`, риск `{task.risk}`")
    lines.extend(
        [
            "",
            "Ограничения:",
            "- MVP показывает последние runtime-данные и статусы.",
            "- Изменения в Ozon/WB через Telegram не выполняются.",
        ]
    )
    return TelegramCommandResult(command="/help", ok=True, text="\n".join(lines))


def _latest_run_command(
    *,
    command: str,
    title: str,
    task: str,
    data_dir: Path,
    next_step: str,
    mode_label: str = "read-only",
) -> TelegramCommandResult:
    run = latest_run(data_dir=data_dir, task=task)
    if not run:
        return TelegramCommandResult(
            command=command,
            ok=False,
            blocked_reason="no_runtime_data",
            text=(
                f"{title}\n\n"
                f"Итог: я не могу это подтвердить - в `data/runs/index.jsonl` нет запуска `{task}`.\n\n"
                f"Режим: {mode_label}. Изменений в магазинах не выполнял.\n\n"
                f"Следующий шаг:\n{next_step}"
            ),
            artifacts={"runs_index": str(data_dir / "runs" / "index.jsonl")},
        )
    artifacts = _safe_artifacts(run)
    lines = [
        title,
        "",
        f"Итог: последний запуск `{task}` имеет статус `{run.get('status') or 'н/д'}`.",
        "",
        f"Режим: {mode_label}. Изменений в магазинах не выполнял.",
        f"Run ID: `{run.get('run_id') or 'н/д'}`",
        f"Начало: `{run.get('started_at') or 'н/д'}`",
        f"Lifecycle: `{run.get('lifecycle_status') or 'н/д'}`",
        f"Риск: `{run.get('risk') or 'н/д'}`",
        "",
        "Что важно:",
    ]
    if run.get("status") in {"blocked", "error"}:
        lines.append("- последний запуск требует внимания")
    else:
        lines.append("- критических признаков по manifest не найдено")
    if run.get("pending_id"):
        lines.append(f"- есть pending package: `{run['pending_id']}`")
    if run.get("approved_id"):
        lines.append(f"- есть approved package: `{run['approved_id']}`")
    lines.extend(["", "Следующий шаг:", next_step])
    if artifacts:
        lines.extend(["", "Файлы:"])
        for key, value in sorted(artifacts.items()):
            lines.append(f"- `{key}`: `{value}`")
    return TelegramCommandResult(command=command, ok=True, text="\n".join(lines), artifacts=artifacts)


def _approvals(*, data_dir: Path) -> TelegramCommandResult:
    status = run_approvals_status(data_dir=data_dir, limit=10)
    rows = status.get("rows") if isinstance(status.get("rows"), list) else []
    status_counts = status.get("status_counts") if isinstance(status.get("status_counts"), dict) else {}
    lines = [
        "Согласования",
        "",
        f"Итог: открытых строк `{status.get('rows_count', 0)}`, показано `{status.get('returned_rows_count', 0)}`.",
        "",
        "Режим: read-only. Изменений в магазинах не выполнял.",
        "",
        "Статусы:",
    ]
    if status_counts:
        for key, value in sorted(status_counts.items()):
            lines.append(f"- `{key}`: `{value}`")
    else:
        lines.append("- нет открытых согласований")
    lines.extend(["", "Последние строки:"])
    if not rows:
        lines.append("- нет строк")
    for row in rows[:5]:
        lines.append(
            f"- `{row.get('kind')}` `{row.get('id')}`: `{row.get('lifecycle_status')}`"
        )
    if len(rows) > 5:
        lines.append(f"- ... еще `{len(rows) - 5}`")
    lines.extend(
        [
            "",
            "Следующий шаг:",
            "Закрывать или применять согласования можно только отдельным CLI/approved-flow, не из этого MVP.",
        ]
    )
    return TelegramCommandResult(
        command="/approvals",
        ok=True,
        text="\n".join(lines),
        artifacts=status.get("artifacts") if isinstance(status.get("artifacts"), dict) else {},
    )


def _runs(*, data_dir: Path) -> TelegramCommandResult:
    registry = default_task_registry()
    run_tasks = [task for task in registry.list(telegram_only=True) if task.command not in {"tasks"}]
    lines = [
        "Запуски",
        "",
        "Итог: последние runtime-статусы по Telegram-задачам.",
        "",
        "Режим: read-only. Изменений в магазинах не выполнял.",
        "",
    ]
    for task in run_tasks:
        if task.mode == "maintenance" and task.command != "approvals":
            continue
        run = latest_run(data_dir=data_dir, task=task.name)
        if not run and task.command != task.name:
            run = latest_run(data_dir=data_dir, task=task.command)
        status = run.get("status") if run else "нет данных"
        run_id = run.get("run_id") if run else ""
        lines.append(f"- `{task.telegram_button_label or task.command}`: `{status}` {f'`{run_id}`' if run_id else ''}".rstrip())
    return TelegramCommandResult(
        command="/runs",
        ok=True,
        text="\n".join(lines),
        artifacts={"runs_index": str(data_dir / "runs" / "index.jsonl")},
    )


def _safe_artifacts(run: dict[str, Any]) -> dict[str, str]:
    artifacts = run.get("artifacts")
    if not isinstance(artifacts, dict):
        return {}
    safe: dict[str, str] = {}
    for key, value in artifacts.items():
        key_text = str(key)
        if any(secret in key_text.lower() for secret in ("token", "secret", "cookie", "storage", "auth")):
            continue
        safe[key_text] = str(value)
    return safe


def _normalize_command(message: str) -> str:
    command = str(message or "").strip().split(maxsplit=1)[0].lower()
    if "@" in command:
        command = command.split("@", 1)[0]
    return command or "/help"
