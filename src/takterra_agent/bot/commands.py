from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from takterra_agent.config import AppCredentials
from takterra_agent.core.run_manifest import latest_run
from takterra_agent.core.workflow_runner import WorkflowRunner
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
    live_today: bool = False,
    live_status: bool = False,
    credentials: AppCredentials | None = None,
) -> TelegramCommandResult:
    command = _normalize_command(message)
    if command == "/help":
        return _help()
    if command == "/status":
        if live_status:
            return _fresh_status_preflight(data_dir=data_dir, credentials=credentials)
        return _latest_run_command(
            command=command,
            title="Статус проекта",
            task="status-preflight",
            data_dir=data_dir,
            next_step="Если данные устарели, запустить read-only `status-preflight`.",
        )
    if command == "/today":
        if live_today:
            return _fresh_daily_report(data_dir=data_dir, credentials=credentials)
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
            "- `/status` собирает свежий read-only preflight, если включен live status mode.",
            "- `/today` собирает свежий read-only отчет, если включен live mode.",
            "- Остальные команды показывают последние runtime-данные и статусы.",
            "- Изменения в Ozon/WB через Telegram не выполняются.",
        ]
    )
    return TelegramCommandResult(command="/help", ok=True, text="\n".join(lines))


def _fresh_status_preflight(
    *,
    data_dir: Path,
    credentials: AppCredentials | None,
) -> TelegramCommandResult:
    result = WorkflowRunner(data_dir=data_dir, credentials=credentials).run_read_only("status-preflight")
    if result.blocked_reason == "workflow_busy":
        return TelegramCommandResult(
            command="/status",
            ok=False,
            blocked_reason="status_preflight_busy",
            text=(
                "Статус проекта\n\n"
                "Итог: свежая проверка состояния уже выполняется другим процессом.\n\n"
                f"Причина: `{result.error}`\n\n"
                "Изменений в Ozon/WB не выполнял."
            ),
        )
    if not result.ok:
        return TelegramCommandResult(
            command="/status",
            ok=False,
            blocked_reason=result.blocked_reason or "status_preflight_failed",
            text=(
                "Статус проекта\n\n"
                "Итог: свежую read-only проверку состояния не удалось выполнить.\n\n"
                f"Причина: `{result.error or result.status}`\n\n"
                "Изменений в Ozon/WB не выполнял."
            ),
        )

    return TelegramCommandResult(
        command="/status",
        ok=True,
        text=_status_preflight_chat_text(result.summary),
        artifacts=result.artifacts,
    )


def _fresh_daily_report(
    *,
    data_dir: Path,
    credentials: AppCredentials | None,
) -> TelegramCommandResult:
    result = WorkflowRunner(data_dir=data_dir, credentials=credentials).run_read_only(
        "daily-morning-report",
        inputs={"seller_v3": True},
    )
    if result.blocked_reason == "workflow_busy":
        return TelegramCommandResult(
            command="/today",
            ok=False,
            blocked_reason="daily_report_busy",
            text=(
                "Ежедневный отчет\n\n"
                "Итог: свежий отчет уже собирается другим процессом.\n\n"
                f"Причина: `{result.error}`\n\n"
                "Изменений в Ozon/WB не выполнял."
            ),
        )
    if not result.ok:
        return TelegramCommandResult(
            command="/today",
            ok=False,
            blocked_reason=result.blocked_reason or "daily_report_failed",
            text=(
                "Ежедневный отчет\n\n"
                "Итог: свежий read-only отчет не удалось построить.\n\n"
                f"Причина: `{result.error or result.status}`\n\n"
                "Изменений в Ozon/WB не выполнял."
            ),
        )

    return TelegramCommandResult(
        command="/today",
        ok=True,
        text=_daily_report_chat_text(result.summary),
        artifacts=result.artifacts,
    )


def _daily_report_chat_text(result: dict[str, Any]) -> str:
    business = result.get("business") if isinstance(result.get("business"), dict) else {}
    periods = _dict_value(business, "periods")
    ozon = _dict_value(business, "ozon")
    wb = _dict_value(business, "wb")
    actions = result.get("actions_v3") if isinstance(result.get("actions_v3"), dict) else {}
    ozon_actions = _dict_value(actions, "ozon")
    wb_actions = _dict_value(actions, "wb")

    ozon_orders_day = _nested(ozon, "orders", "yesterday")
    wb_orders_day = _nested(wb, "orders", "yesterday")
    ozon_buyouts = _nested(ozon, "finance_buyouts")
    wb_sales_day = _nested(wb, "sales", "yesterday")
    ozon_expenses = _nested(ozon, "finance_expenses")
    wb_expenses = _nested(wb, "finance_expenses")
    ozon_stocks = _nested(ozon, "stocks")
    wb_stocks = _nested(wb, "stocks")
    ozon_comm = _nested(ozon, "communications")
    wb_comm = _nested(wb, "communications")

    lines = [
        "Ежедневный отчет",
        "",
        f"Итог: свежий read-only отчет построен, статус `{result.get('overall_status') or 'н/д'}`.",
        f"Период: `{periods.get('yesterday') or 'н/д'}`, 00:00-23:59 MSK.",
        f"Run ID: `{result.get('run_id') or 'н/д'}`",
        "",
        "Заказы / выкупы / расходы:",
        f"- Ozon: заказы `{_int(ozon_orders_day.get('ordered_units'))}` шт. / `{_money(ozon_orders_day.get('revenue'))}`; выкупы `{_int(ozon_buyouts.get('buyout_units'))}` шт. / `{_money(ozon_buyouts.get('buyout_amount'))}`; расходы `{_money(ozon_expenses.get('total_expenses'))}`.",
        f"- WB: заказы `{_int(wb_orders_day.get('active_orders'))}` шт. / `{_money(wb_orders_day.get('amount'))}`; выкупы `{_int(wb_sales_day.get('sales_rows'))}` шт. / `{_money(wb_sales_day.get('sales_amount'))}`; расходы `{_money(wb_expenses.get('total_expenses'))}`.",
        "",
        "Отзывы и вопросы:",
        f"- Ozon требуют внимания: отзывы `{_int(ozon_comm.get('unanswered_feedbacks'))}`, вопросы `{_int(ozon_comm.get('unanswered_questions'))}`.",
        f"- WB требуют внимания: отзывы `{_int(wb_comm.get('unanswered_feedbacks'))}`, вопросы `{_int(wb_comm.get('unanswered_questions'))}`.",
        "",
        "Остатки:",
        f"- Ozon: всего `{_int(ozon_stocks.get('present_total'))}` шт., нулевой остаток `{_int(ozon_stocks.get('out_of_stock_count'))}` товаров.",
        f"- WB: всего `{_int(wb_stocks.get('quantity_total'))}` шт., нулевой остаток `{_int(wb_stocks.get('zero_stock_count'))}` товаров.",
        "",
        "Акции:",
        f"- Ozon: активные `{_int(ozon_actions.get('active_actions'))}`, товаров участвует `{_int(ozon_actions.get('products_in_actions'))}`, не участвует `{_int(ozon_actions.get('products_not_in_actions'))}`.",
        f"- WB: активные `{_int(wb_actions.get('active_actions'))}`, товаров участвует `{_int(wb_actions.get('products_in_actions'))}`, не участвует `{_int(wb_actions.get('products_not_in_actions'))}`.",
        "",
        "Важно:",
    ]
    for item in result.get("executive_summary") or []:
        lines.append(f"- {item}")

    artifacts = _safe_artifacts(result)
    if artifacts:
        lines.extend(["", "Файлы:"])
        if artifacts.get("report"):
            lines.append(f"- отчет: `{artifacts['report']}`")
        if artifacts.get("summary"):
            lines.append(f"- summary: `{artifacts['summary']}`")
    lines.extend(["", "Изменений в Ozon/WB не выполнял."])
    return "\n".join(lines)


def _status_preflight_chat_text(result: dict[str, Any]) -> str:
    checks = result.get("checks") if isinstance(result.get("checks"), dict) else {}
    status_counts = _check_status_counts(checks)
    lines = [
        "Статус проекта",
        "",
        f"Итог: свежая read-only проверка выполнена, статус `{result.get('overall_status') or 'н/д'}`.",
        f"Run ID: `{result.get('run_id') or 'н/д'}`",
        "",
        "Ключевые проверки:",
    ]
    for key in (
        "ozon_api",
        "ozon_performance_api",
        "wb_api",
        "master_catalog",
        "ozon_cdp",
        "ozon_session_keeper",
        "wb_session",
    ):
        if key in checks:
            lines.append(f"- {_check_label(key)}: `{_check_status(checks.get(key))}`")

    lines.extend(
        [
            "",
            "Сводка:",
            f"- ok: `{status_counts.get('ok', 0)}`",
            f"- warning/skipped: `{status_counts.get('warning', 0) + status_counts.get('skipped', 0)}`",
            f"- error: `{status_counts.get('error', 0)}`",
        ]
    )

    issues = _check_issues(checks)
    if issues:
        lines.extend(["", "Требует внимания:"])
        lines.extend(f"- {issue}" for issue in issues[:6])
        if len(issues) > 6:
            lines.append(f"- ... еще `{len(issues) - 6}`")
    else:
        lines.extend(["", "Требует внимания:", "- критических замечаний по проверкам нет"])

    artifacts = _safe_artifacts(result)
    if artifacts:
        lines.extend(["", "Файлы:"])
        if artifacts.get("report"):
            lines.append(f"- отчет: `{artifacts['report']}`")
        if artifacts.get("summary"):
            lines.append(f"- summary: `{artifacts['summary']}`")
    lines.extend(["", "Изменений в Ozon/WB не выполнял."])
    return "\n".join(lines)


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


def _dict_value(source: dict[str, Any], key: str) -> dict[str, Any]:
    value = source.get(key)
    return value if isinstance(value, dict) else {}


def _nested(source: dict[str, Any], *keys: str) -> dict[str, Any]:
    current: Any = source
    for key in keys:
        if not isinstance(current, dict):
            return {}
        current = current.get(key)
    return current if isinstance(current, dict) else {}


def _int(value: Any) -> str:
    if value in (None, ""):
        return "н/д"
    try:
        return f"{int(float(value)):,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(value)


def _money(value: Any) -> str:
    if value in (None, ""):
        return "н/д"
    try:
        return f"{float(value):,.0f}".replace(",", " ") + " ₽"
    except (TypeError, ValueError):
        return str(value)


def _check_label(key: str) -> str:
    labels = {
        "master_catalog": "Master catalog",
        "ozon_api": "Ozon Seller API",
        "ozon_cdp": "Ozon LK/CDP",
        "ozon_performance_api": "Ozon Performance API",
        "ozon_session_keeper": "Ozon session keeper",
        "wb_api": "WB API",
        "wb_session": "WB LK session",
    }
    return labels.get(key, key.replace("_", " "))


def _check_status(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("status") or "н/д")
    return "н/д"


def _check_status_counts(checks: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in checks.values():
        status = _check_status(value)
        counts[status] = counts.get(status, 0) + 1
    return counts


def _check_issues(checks: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for key, value in sorted(checks.items()):
        if not isinstance(value, dict):
            continue
        status = str(value.get("status") or "")
        if status not in {"error", "warning"}:
            continue
        reason = value.get("error") or value.get("message") or status
        issues.append(f"{_check_label(key)}: `{reason}`")
    return issues


def _normalize_command(message: str) -> str:
    command = str(message or "").strip().split(maxsplit=1)[0].lower()
    if "@" in command:
        command = command.split("@", 1)[0]
    return command or "/help"
