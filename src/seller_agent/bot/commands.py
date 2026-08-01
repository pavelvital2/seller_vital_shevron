from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
from typing import Any

from seller_agent.config import AppCredentials, load_credentials
from seller_agent.core.job_service import JobService
from seller_agent.core.job_store import DEFAULT_RUNTIME_DB, JobStore, approval_callback_token
from seller_agent.core.run_manifest import latest_run
from seller_agent.core.workflow_runner import WorkflowRunner
from seller_agent.tasks.approvals import run_approvals_status
from seller_agent.tasks.ozon_actions_optimizer_plan import run_ozon_actions_optimizer_plan
from seller_agent.tasks.ozon_elastic_plan import run_ozon_elastic_plan
from seller_agent.tasks.inbox_workflow import (
    run_ozon_inbox_triage,
    run_wb_inbox_triage,
)
from seller_agent.tasks.registry import default_task_registry
from seller_agent.tasks.ozon_production_work_plan import record_ozon_work_plan_decision
from seller_agent.tasks.wb_actions_discount_plan import (
    run_wb_actions_discount_plan,
    wb_actions_report_stats,
)
from seller_agent.tasks.wb_production_work_plan import record_wb_work_plan_decision


SUPPORTED_COMMANDS = {
    "/elastic",
    "/help",
    "/jobs",
    "/menu",
    "/status",
    "/start",
    "/today",
    "/reviews",
    "/ozon-inbox",
    "/ozon-actions",
    "/ozon-stock-supplies",
    "/ozon-work-plan",
    "/ozon-pricing-margin",
    "/wb-pricing-margin",
    "/ozon",
    "/period-report",
    "/period-report-ozon",
    "/period-report-wb",
    "/wb",
    "/wb-analytics",
    "/wb-stock-supplies",
    "/wb-work-plan",
    "/wb-inbox",
    "/approvals",
    "/catalog",
    "/runs",
    "/wb-actions",
    "/wb-actions-manual",
    "/wb-actions-min-price",
}

TELEGRAM_TITLES = {
    "/approvals": "Согласования",
    "/catalog": "Каталог",
    "/elastic": "Ozon Elastic",
    "/help": "Помощь",
    "/jobs": "Runtime jobs",
    "/menu": "Главное меню",
    "/reviews": "Отзывы и вопросы",
    "/ozon": "Ozon",
    "/ozon-inbox": "Ozon входящие",
    "/ozon-actions": "Ozon все акции",
    "/ozon-stock-supplies": "Остатки и поставки Ozon",
    "/ozon-work-plan": "В работу Ozon",
    "/ozon-pricing-margin": "Цены и маржа Ozon",
    "/wb-pricing-margin": "Цены и маржа WB",
    "/period-report": "Отчёт за период",
    "/period-report-ozon": "Ozon: отчёт за период",
    "/period-report-wb": "Wildberries: отчёт за период",
    "/start": "Главное меню",
    "/wb": "Wildberries",
    "/wb-analytics": "WB аналитика",
    "/wb-stock-supplies": "Остатки и поставки",
    "/wb-work-plan": "В работу",
    "/wb-inbox": "WB входящие",
    "/runs": "Запуски",
    "/status": "Статус проекта",
    "/today": "Ежедневный отчет",
    "/wb-actions": "WB акции 70-55-55",
    "/wb-actions-manual": "Ручная акция",
    "/wb-actions-min-price": "Акции от минимальной цены",
}


@dataclass(frozen=True)
class TelegramCommandResult:
    command: str
    ok: bool
    text: str
    mode: str = "read_only"
    artifacts: dict[str, str] = field(default_factory=dict)
    reply_markup: dict[str, Any] = field(default_factory=dict)
    blocked_reason: str = ""
    conversation_state: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def handle_telegram_command(
    message: str,
    *,
    data_dir: Path = Path("data"),
    live_today: bool = False,
    live_status: bool = False,
    runtime_db: Path = DEFAULT_RUNTIME_DB,
    credentials: AppCredentials | None = None,
    conversation_state: dict[str, Any] | None = None,
) -> TelegramCommandResult:
    command, argument = _parse_command(message)
    command = _normalize_button_command(command)
    active_conversation = conversation_state if isinstance(conversation_state, dict) else {}
    if (
        active_conversation.get("stage") in {"ozon_work_capacity_input", "ozon_work_days_input"}
        and not _is_explicit_command_or_button(message)
    ):
        return _ozon_work_plan_value(message, state=active_conversation)
    if (
        active_conversation.get("stage") == "ozon_work_clusters_input"
        and not _is_explicit_command_or_button(message)
    ):
        return _ozon_work_plan_clusters(message, state=active_conversation)
    if (
        active_conversation.get("stage") == "ozon_pricing_cost_input"
        and not _is_explicit_command_or_button(message)
    ):
        return _ozon_pricing_cost_input(message, state=active_conversation)
    if (
        active_conversation.get("stage") == "ozon_pricing_margin_input"
        and not _is_explicit_command_or_button(message)
    ):
        return _ozon_pricing_margin_runtime_required(message, state=active_conversation)
    if (
        active_conversation.get("stage") == "wb_pricing_cost_input"
        and not _is_explicit_command_or_button(message)
    ):
        return _wb_pricing_cost_input(message, state=active_conversation)
    if (
        active_conversation.get("stage") == "wb_pricing_margin_input"
        and not _is_explicit_command_or_button(message)
    ):
        return _wb_pricing_margin_runtime_required(message, state=active_conversation)
    if (
        active_conversation.get("stage") == "wb_manual_scheme_input"
        and not _is_explicit_command_or_button(message)
    ):
        return _wb_manual_actions_parameters(message)
    if (
        active_conversation.get("stage") == "wb_min_price_discount_input"
        and not _is_explicit_command_or_button(message)
    ):
        return _wb_min_price_actions_parameters(message)
    if (
        active_conversation.get("stage") in {"wb_work_capacity_input", "wb_work_days_input"}
        and not _is_explicit_command_or_button(message)
    ):
        return _wb_work_plan_value(message, state=active_conversation)
    if (
        active_conversation.get("stage") == "wb_work_clusters_input"
        and not _is_explicit_command_or_button(message)
    ):
        return _wb_work_plan_clusters(message, state=active_conversation)
    if (
        active_conversation.get("stage") in {"period_report_custom_from", "period_report_custom_to"}
        and not _is_explicit_command_or_button(message)
    ):
        return _period_report_custom_input(message, state=active_conversation)
    if command in {"/start", "/menu"}:
        return _main_menu()
    if command == "/help":
        return _help()
    if command == "/ozon":
        return _ozon_menu()
    if command in {"/wb", "/wildberries", "/вайлдберриз"}:
        return _wb_menu()
    if command in {"/elastic", "/ozon-elastic", "/ozon_elastic"}:
        return _ozon_elastic_plan(data_dir=data_dir, credentials=credentials)
    if command in {"/ozon-actions", "/ozon_actions", "/ozon-all-actions"}:
        return _ozon_actions_plan(data_dir=data_dir, credentials=credentials)
    if command in {"/ozon-stock-supplies", "/ozon_stock_supplies"}:
        return _ozon_stock_supplies(data_dir=data_dir, credentials=credentials)
    if command in {"/ozon-work-plan", "/ozon_work_plan"}:
        return _ozon_work_plan_start()
    if command in {"/ozon-pricing-margin", "/ozon_pricing_margin"}:
        return _ozon_pricing_margin_start()
    if command in {"/wb-pricing-margin", "/wb_pricing_margin"}:
        return _wb_pricing_margin_start()
    if command in {"/wb-actions", "/wb_actions", "/wb-actions-70-55-55"}:
        return _wb_actions_plan(data_dir=data_dir, credentials=credentials)
    if command in {"/wb-actions-manual", "/wb_actions_manual"}:
        return _wb_manual_actions_start()
    if command in {"/wb-actions-min-price", "/wb_actions_min_price"}:
        return _wb_min_price_actions_start()
    if command in {"/period-report-ozon", "/period_report_ozon"}:
        return _period_report_start("ozon")
    if command in {"/period-report-wb", "/period_report_wb"}:
        return _period_report_start("wb")
    if command in {"/period-report", "/period_report"}:
        return _period_report_marketplace_start()
    if command in {"/wb-analytics", "/wb_analytics"}:
        return _wb_analytics(data_dir=data_dir, credentials=credentials)
    if command in {"/wb-stock-supplies", "/wb_stock_supplies"}:
        return _wb_stock_supplies(data_dir=data_dir, credentials=credentials)
    if command in {"/wb-work-plan", "/wb_work_plan"}:
        return _wb_work_plan_start()
    if command in {"/ozon-inbox", "/ozon_inbox"}:
        return _ozon_inbox_plan(data_dir=data_dir, credentials=credentials)
    if command in {"/wb-inbox", "/wb_inbox"}:
        return _wb_inbox_plan(data_dir=data_dir, credentials=credentials)
    if command == "/jobs":
        return _jobs(runtime_db=runtime_db)
    if command == "/job" and argument:
        return _job_show(argument, runtime_db=runtime_db)
    if command.startswith("/job_"):
        return _job_show(command.removeprefix("/job_"), runtime_db=runtime_db)
    if command == "/cancel" and argument:
        return _job_cancel(argument, runtime_db=runtime_db)
    if command.startswith("/cancel_"):
        return _job_cancel(command.removeprefix("/cancel_"), runtime_db=runtime_db)
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
        if argument:
            return _catalog_search(argument, data_dir=data_dir)
        return _latest_run_command(
            command=command,
            title="Каталог",
            task="catalog-build-unified",
            data_dir=data_dir,
            next_step="Если нужен свежий единый каталог, запустить read-only `build-unified-catalog`.",
        )
    if command == "/runs":
        return _runs(data_dir=data_dir)
    if command == "/approvals":
        return _approvals(data_dir=data_dir, runtime_db=runtime_db)
    return TelegramCommandResult(
        command=command,
        ok=False,
        blocked_reason="unsupported_command",
        text=(
            "Команда не поддерживается в read-only Telegram MVP.\n\n"
            "Доступные команды: "
            + ", ".join(sorted(SUPPORTED_COMMANDS))
        ),
    )


def handle_telegram_callback(
    callback_data: str,
    *,
    data_dir: Path = Path("data"),
    credentials: AppCredentials | None = None,
) -> TelegramCommandResult:
    data = str(callback_data or "").strip()
    if data == "mpr_market:o":
        return _period_report_start("ozon")
    if data == "mpr_market:w":
        return _period_report_start("wb")
    if data.startswith("mpr_type:"):
        return _period_report_type_callback(data)
    if data.startswith("mpr_period:"):
        return _period_report_period_callback(data)
    if data.startswith("mpr_run:"):
        return _period_report_run_callback(data, data_dir=data_dir, credentials=credentials)
    if data == "mpr_cancel":
        return _period_report_cancel()
    if data.startswith("opm_period:"):
        return _ozon_pricing_margin_period_callback(data)
    if data == "opm_cancel":
        return _ozon_pricing_margin_cancel()
    if data.startswith("wpm_period:"):
        return _wb_pricing_margin_period_callback(data)
    if data == "wpm_cancel":
        return _wb_pricing_margin_cancel()
    if data.startswith("ozwp_mode:"):
        return _ozon_work_plan_mode_callback(data)
    if data.startswith("ozwp_run:"):
        return _ozon_work_plan_run_callback(data, data_dir=data_dir, credentials=credentials)
    if data.startswith("ozwp_approve:"):
        return _ozon_work_plan_decision_callback(data, data_dir=data_dir, approved=True)
    if data.startswith("ozwp_reject:"):
        return _ozon_work_plan_decision_callback(data, data_dir=data_dir, approved=False)
    if data == "ozwp_cancel":
        return _ozon_work_plan_cancel()
    if data.startswith("wbwp_mode:"):
        return _wb_work_plan_mode_callback(data)
    if data.startswith("wbwp_run:"):
        return _wb_work_plan_run_callback(data, data_dir=data_dir, credentials=credentials)
    if data.startswith("wbwp_approve:"):
        return _wb_work_plan_decision_callback(data, data_dir=data_dir, approved=True)
    if data.startswith("wbwp_reject:"):
        return _wb_work_plan_decision_callback(data, data_dir=data_dir, approved=False)
    if data == "wbwp_cancel":
        return _wb_work_plan_cancel()
    if data.startswith(("oe_apply:", "oza_apply:", "wba_apply:")):
        return _legacy_write_callback_blocked()
    if data.startswith("wbam_confirm:"):
        scheme_text = data.removeprefix("wbam_confirm:").strip()
        return _wb_manual_actions_plan(
            scheme_text=scheme_text,
            data_dir=data_dir,
            credentials=credentials,
        )
    if data == "wbam_cancel":
        return _wb_manual_actions_cancel(stage="parameters")
    if data.startswith("wbam_reject:"):
        plan_run_id = data.removeprefix("wbam_reject:").strip()
        return _wb_manual_actions_reject(plan_run_id)
    if data.startswith("wbmp_review:"):
        return _wb_min_price_actions_review(data.removeprefix("wbmp_review:").strip())
    if data.startswith("wbmp_confirm:"):
        return _wb_min_price_actions_plan(
            outside_discount=data.removeprefix("wbmp_confirm:").strip(),
            data_dir=data_dir,
            credentials=credentials,
        )
    if data.startswith("wbmp_apply:"):
        return _legacy_write_callback_blocked()
    if data == "wbmp_change":
        return _wb_min_price_actions_start()
    if data == "wbmp_cancel":
        return _wb_min_price_actions_cancel()
    if data.startswith("wbmp_reject:"):
        return _wb_min_price_actions_reject(data.removeprefix("wbmp_reject:").strip())
    if data.startswith(("ozin_apply:", "wbin_apply:")):
        return _legacy_write_callback_blocked()
    return TelegramCommandResult(
        command="callback",
        ok=False,
        blocked_reason="unsupported_callback",
        text=(
            "Действие не поддерживается.\n\n"
            f"Callback: `{_truncate(data, 80)}`\n\n"
            "Изменений в Ozon/WB не выполнял."
        ),
    )


def _legacy_write_callback_blocked() -> TelegramCommandResult:
    return TelegramCommandResult(
        command="callback",
        ok=False,
        mode="apply",
        blocked_reason="runtime_jobs_required",
        text=(
            "Apply заблокирован\n\n"
            "Write callback разрешён только через runtime Job Worker и существующий approval ID. "
            "Legacy plan/source run ID не является подтверждением владельца.\n\n"
            "Изменений в Ozon/WB не выполнялось."
        ),
    )


MAIN_MENU_KEYBOARD: dict[str, Any] = {
    "keyboard": [
        [{"text": "Статус"}, {"text": "Помощь"}],
        [{"text": "Общий вчерашний отчет"}],
        [{"text": "Озон"}, {"text": "Вайлдберриз"}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
}

OZON_MENU_KEYBOARD: dict[str, Any] = {
    "keyboard": [
        [{"text": "Ozon акции"}, {"text": "Ozon эластик"}],
        [{"text": "Отчёт за период Ozon"}],
        [{"text": "Остатки и поставки Ozon"}],
        [{"text": "В работу Ozon"}],
        [{"text": "Цены и маржа Ozon"}],
        [{"text": "Ozon входящие"}],
        [{"text": "Назад"}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
}

WB_MENU_KEYBOARD: dict[str, Any] = {
    "keyboard": [
        [{"text": "WB акции"}, {"text": "Ручная акция"}],
        [{"text": "Акции от минимальной цены"}],
        [{"text": "WB аналитика"}],
        [{"text": "Остатки и поставки"}],
        [{"text": "В работу"}],
        [{"text": "Цены и маржа WB"}],
        [{"text": "Отчёт за период WB"}],
        [{"text": "WB входящие"}],
        [{"text": "Назад"}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
}


def _main_menu() -> TelegramCommandResult:
    return TelegramCommandResult(
        command="/menu",
        ok=True,
        text=(
            "Главное меню\n\n"
            "Итог: выбери нужный раздел кнопкой ниже.\n\n"
            "Первый экран:\n"
            "- Статус\n"
            "- Помощь\n"
            "- Общий вчерашний отчет\n"
            "- Озон\n"
            "- Вайлдберриз"
        ),
        reply_markup=MAIN_MENU_KEYBOARD,
    )


def _ozon_menu() -> TelegramCommandResult:
    return TelegramCommandResult(
        command="/ozon",
        ok=True,
        text=(
            "Ozon\n\n"
            "Итог: выбери операцию Ozon.\n\n"
            "- Ozon акции - сравнение всех акций Ozon.\n"
            "- Ozon эластик - только эластичный бустинг.\n"
            "- Отчёт за период - краткий, финансовый или полный отчёт Ozon.\n"
            "- Остатки и поставки - свежий FBO-остаток по складам и активные поставки Ozon.\n"
            "- В работу - производственный план Ozon по потребности выбранного числа кластеров.\n"
            "- Цены и маржа - read-only расчёт расходов и ценовой сетки Ozon.\n"
            "- Ozon входящие - отзывы, вопросы, чаты и уведомления."
        ),
        reply_markup=OZON_MENU_KEYBOARD,
    )


def _wb_menu() -> TelegramCommandResult:
    return TelegramCommandResult(
        command="/wb",
        ok=True,
        text=(
            "Wildberries\n\n"
            "Итог: выбери операцию Wildberries.\n\n"
            "- WB акции - акции и скидки по схеме 70-55-55.\n"
            "- Ручная акция - расчёт и применение по введённой схеме.\n"
            "- Отчёт за период - краткий, финансовый или полный отчёт WB.\n"
            "- WB аналитика - видимость, позиции и динамика из Parser Data API.\n"
            "- Остатки и поставки - свежие остатки по складам, все активные поставки и аномалии WB.\n"
            "- В работу - производственный план WB по доступному объёму или периоду покрытия.\n"
            "- Цены и маржа - dry-run расчёт расходов и ценовой сетки WB.\n"
            "- WB входящие - отзывы, вопросы и уведомления."
        ),
        reply_markup=WB_MENU_KEYBOARD,
    )


def _help() -> TelegramCommandResult:
    registry = default_task_registry()
    tasks = registry.list(telegram_only=True)
    lines = [
        "Telegram bot",
        "",
        "Итог: доступны read-only экраны и безопасные кнопки Ozon Elastic / WB акции через dry-run и подтверждение.",
        "",
        "Команды:",
    ]
    for task in tasks:
        label = task.telegram_button_label or f"/{task.command}"
        if label not in SUPPORTED_COMMANDS:
            continue
        mode = "read-only" if task.mode == "read_only" else task.mode
        lines.append(f"- `{label}` - {TELEGRAM_TITLES.get(label, task.title)}; режим `{mode}`, риск `{task.risk}`")
    lines.extend(
        [
            "",
            "Ограничения:",
            "- `/status` собирает свежий read-only preflight, если включен live status mode.",
            "- `/today` собирает свежий read-only отчет, если включен live mode.",
            "- `/catalog <запрос>` ищет товар в unified catalog по internal_sku, Ozon/WB ID, barcode или названию.",
            "- `/elastic` строит свежий dry-run Ozon Elastic и показывает кнопку применения.",
            "- Кнопка применения Ozon Elastic запускает apply только по конкретному показанному `plan_run_id`.",
            "- `/ozon-actions` строит свежий dry-run Ozon всех акций и показывает отдельную кнопку применения.",
            "- Кнопка применения Ozon всех акций запускает отдельный apply-контур только по конкретному `plan_run_id`.",
            "- `/wb-actions` строит свежий dry-run WB акций по схеме 70-55-55 и показывает кнопку применения.",
            "- `/wb-actions-manual` запрашивает ручную схему, подтверждает параметры и только затем строит fresh dry-run.",
            "- `/wb-actions-min-price` выбирает для каждого товара лучшую акцию не ниже минимальной цены.",
            "- Кнопка применения WB акций запускает apply только по конкретному показанному `plan_run_id`.",
            "- `/wb-analytics` строит свежую read-only аналитику WB по Parser Data API warehouse.",
            "- `/wb-stock-supplies` строит свежий read-only отчет по остаткам складов и всем активным FBW-поставкам WB.",
            "- `/ozon-stock-supplies` строит свежий read-only отчет по FBO-остаткам и активным поставкам Ozon.",
            "- `/wb-work-plan` формирует Excel в работу по физической мощности или периоду покрытия; поставки WB не создаёт.",
            "- `/ozon-inbox` собирает свежие Ozon отзывы/вопросы/чаты/уведомления и показывает кнопку применения согласованного пакета.",
            "- `/wb-inbox` собирает свежие WB отзывы/вопросы и read-only новости/уведомления WB из ЛК `news-v2`.",
            "- Остальные команды показывают последние runtime-данные и статусы.",
            "- Другие изменения в Ozon/WB через Telegram не выполняются.",
        ]
    )
    return TelegramCommandResult(
        command="/help",
        ok=True,
        text="\n".join(lines),
        reply_markup=MAIN_MENU_KEYBOARD,
    )


def _wb_analytics(
    *,
    data_dir: Path,
    credentials: AppCredentials | None,
) -> TelegramCommandResult:
    result = WorkflowRunner(data_dir=data_dir, credentials=credentials).run_read_only(
        "wb-parser-warehouse-analytics",
        inputs={"supplier_id": "4516781", "limit": 500, "report_limit": 50},
    )
    if result.blocked_reason == "workflow_busy":
        return TelegramCommandResult(
            command="/wb-analytics",
            ok=False,
            blocked_reason="wb_analytics_busy",
            text=(
                "WB аналитика\n\n"
                "Итог: свежая аналитика WB уже собирается другим процессом.\n\n"
                f"Причина: `{result.error}`\n\n"
                "Изменений в WB не выполнял."
            ),
        )
    if not result.ok:
        return TelegramCommandResult(
            command="/wb-analytics",
            ok=False,
            blocked_reason=result.blocked_reason or "wb_analytics_failed",
            text=(
                "WB аналитика\n\n"
                "Итог: свежую read-only аналитику WB не удалось построить.\n\n"
                f"Причина: `{result.error or result.status}`\n\n"
                "Изменений в WB не выполнял."
            ),
        )
    return TelegramCommandResult(
        command="/wb-analytics",
        ok=True,
        text=_wb_analytics_chat_text(result.summary),
        artifacts=result.artifacts,
    )


def _wb_stock_supplies(
    *,
    data_dir: Path,
    credentials: AppCredentials | None,
) -> TelegramCommandResult:
    result = WorkflowRunner(data_dir=data_dir, credentials=credentials).run_read_only(
        "wb-stock-supply-monitor"
    )
    if result.blocked_reason == "workflow_busy":
        return TelegramCommandResult(
            command="/wb-stock-supplies",
            ok=False,
            blocked_reason="wb_stock_supply_busy",
            text=(
                "Остатки и поставки WB\n\n"
                "Итог: свежий отчет уже собирается другим процессом.\n\n"
                f"Причина: `{result.error}`\n\n"
                "Изменений в WB не выполнял."
            ),
        )
    if not result.ok:
        return TelegramCommandResult(
            command="/wb-stock-supplies",
            ok=False,
            blocked_reason=result.blocked_reason or "wb_stock_supply_failed",
            text=(
                "Остатки и поставки WB\n\n"
                "Итог: свежий read-only отчет не удалось построить.\n\n"
                f"Причина: `{result.error or result.status}`\n\n"
                "Изменений в WB не выполнял."
            ),
        )
    return TelegramCommandResult(
        command="/wb-stock-supplies",
        ok=True,
        text=_wb_stock_supplies_chat_text(result.summary),
        artifacts=result.artifacts,
    )


def _ozon_stock_supplies(
    *,
    data_dir: Path,
    credentials: AppCredentials | None,
) -> TelegramCommandResult:
    result = WorkflowRunner(data_dir=data_dir, credentials=credentials).run_read_only(
        "ozon-stock-supply-monitor"
    )
    if result.blocked_reason == "workflow_busy":
        return TelegramCommandResult(
            command="/ozon-stock-supplies",
            ok=False,
            blocked_reason="ozon_stock_supply_busy",
            text=(
                "Остатки и поставки Ozon\n\n"
                "Свежий отчет уже собирается другим процессом.\n\n"
                f"Причина: `{result.error}`\n\n"
                "Изменений в Ozon не выполнял."
            ),
        )
    if not result.ok:
        return TelegramCommandResult(
            command="/ozon-stock-supplies",
            ok=False,
            blocked_reason=result.blocked_reason or "ozon_stock_supply_failed",
            text=(
                "Остатки и поставки Ozon\n\n"
                "Свежий read-only отчет не сформирован.\n\n"
                f"Причина: `{result.error or result.status}`\n\n"
                "Изменений в Ozon не выполнял."
            ),
        )
    return TelegramCommandResult(
        command="/ozon-stock-supplies",
        ok=True,
        text=_ozon_stock_supplies_chat_text(result.summary),
        artifacts=result.artifacts,
    )


def _ozon_work_plan_start() -> TelegramCommandResult:
    return TelegramCommandResult(
        command="/ozon-work-plan",
        ok=True,
        mode="input",
        text=(
            "Ozon: в работу\n\n"
            "Выберите способ расчёта:\n\n"
            "- по производственной возможности — ввести доступное количество физических изделий;\n"
            "- по периоду покрытия — ввести количество дней запаса.\n\n"
            "После этого бот запросит количество кластеров назначения. Поставка в Ozon не создаётся."
        ),
        reply_markup={
            "inline_keyboard": [
                [{"text": "По объёму производства", "callback_data": "ozwp_mode:c"}],
                [{"text": "По дням покрытия", "callback_data": "ozwp_mode:d"}],
                [{"text": "Отменить", "callback_data": "ozwp_cancel"}],
            ]
        },
    )


def _ozon_pricing_margin_start() -> TelegramCommandResult:
    return TelegramCommandResult(
        command="/ozon-pricing-margin",
        ok=True,
        mode="input",
        text=(
            "Ozon: цены и маржа\n\n"
            "Выберите период, по которому бот рассчитает фактические расходы Ozon FBO. "
            "Используются только завершённые дни до вчерашнего включительно.\n\n"
            "Расчёт работает в режиме dry-run: цены в Ozon не изменяются."
        ),
        reply_markup={
            "inline_keyboard": [
                [
                    {"text": "15 дней", "callback_data": "opm_period:15"},
                    {"text": "30 дней", "callback_data": "opm_period:30"},
                ],
                [{"text": "Отменить", "callback_data": "opm_cancel"}],
            ]
        },
    )


def _ozon_pricing_margin_period_callback(data: str) -> TelegramCommandResult:
    raw_days = data.removeprefix("opm_period:").strip()
    period_days = int(raw_days) if raw_days.isdigit() else 0
    if period_days not in {15, 30}:
        return _ozon_pricing_margin_invalid("Период повреждён. Запустите «Цены и маржа Ozon» заново.")
    return TelegramCommandResult(
        command="/ozon-pricing-margin",
        ok=True,
        mode="input",
        text=(
            f"Ozon: цены и маржа\n\nПериод расходов: `{period_days} дней`.\n\n"
            "Введите себестоимость одного физического изделия в рублях.\n\n"
            "Пример: `85`. Для комплекта бот умножит эту сумму на `pack_qty`."
        ),
        reply_markup={"inline_keyboard": [[{"text": "Отменить", "callback_data": "opm_cancel"}]]},
        conversation_state={"stage": "ozon_pricing_cost_input", "period_days": period_days},
    )


def _ozon_pricing_cost_input(message: str, *, state: dict[str, Any]) -> TelegramCommandResult:
    period_days = _int_value(state.get("period_days"))
    cost = _owner_decimal(message)
    if period_days not in {15, 30}:
        return _ozon_pricing_margin_invalid("Период расчёта потерян. Запустите расчёт заново.")
    if cost is None or cost <= 0 or cost > Decimal("100000"):
        return TelegramCommandResult(
            command="/ozon-pricing-margin",
            ok=False,
            mode="input",
            blocked_reason="invalid_ozon_unit_cost",
            text=(
                "Ozon: цены и маржа\n\n"
                "Введите положительную себестоимость одного физического изделия в рублях. "
                "Допустимы целые и дробные значения, например `85` или `85,50`."
            ),
            reply_markup={"inline_keyboard": [[{"text": "Отменить", "callback_data": "opm_cancel"}]]},
            conversation_state=state,
        )
    cost_text = _decimal_text(cost)
    return TelegramCommandResult(
        command="/ozon-pricing-margin",
        ok=True,
        mode="input",
        text=(
            f"Ozon: цены и маржа\n\nСебестоимость изделия: `{cost_text} руб.`\n\n"
            "Введите желаемую маржу с одного физического изделия в рублях.\n\n"
            "Пример: `60`. После ввода Job Worker соберёт расходы Ozon и сформирует расчёт."
        ),
        reply_markup={"inline_keyboard": [[{"text": "Отменить", "callback_data": "opm_cancel"}]]},
        conversation_state={
            "stage": "ozon_pricing_margin_input",
            "period_days": period_days,
            "unit_cost": cost_text,
        },
    )


def _ozon_pricing_margin_runtime_required(message: str, *, state: dict[str, Any]) -> TelegramCommandResult:
    margin = _owner_decimal(message)
    if margin is None or margin < 0 or margin > Decimal("100000"):
        return TelegramCommandResult(
            command="/ozon-pricing-margin",
            ok=False,
            mode="input",
            blocked_reason="invalid_ozon_target_margin",
            text=(
                "Ozon: цены и маржа\n\n"
                "Введите неотрицательную маржу на одно физическое изделие в рублях, например `60`."
            ),
            reply_markup={"inline_keyboard": [[{"text": "Отменить", "callback_data": "opm_cancel"}]]},
            conversation_state=state,
        )
    return TelegramCommandResult(
        command="/ozon-pricing-margin",
        ok=False,
        mode="input",
        blocked_reason="runtime_jobs_required",
        text=(
            "Расчёт должен быть поставлен в Job Worker. В preview-режиме очередь отключена; "
            "в рабочем Telegram-боте это сообщение автоматически создаст read-only задачу."
        ),
        conversation_state=state,
    )


def _ozon_pricing_margin_cancel() -> TelegramCommandResult:
    return TelegramCommandResult(
        command="/ozon-pricing-margin",
        ok=True,
        text="Ozon: расчёт цен и маржи отменён. Изменений в кабинете не было.",
        reply_markup=OZON_MENU_KEYBOARD,
    )


def _ozon_pricing_margin_invalid(message: str) -> TelegramCommandResult:
    return TelegramCommandResult(
        command="/ozon-pricing-margin",
        ok=False,
        mode="input",
        blocked_reason="invalid_ozon_pricing_margin_parameters",
        text=f"Ozon: цены и маржа\n\n{message}\n\nИзменений в Ozon не было.",
        reply_markup=OZON_MENU_KEYBOARD,
    )


def _wb_pricing_margin_start() -> TelegramCommandResult:
    return TelegramCommandResult(
        command="/wb-pricing-margin",
        ok=True,
        mode="input",
        text=(
            "WB: цены и маржа\n\n"
            "Выберите период фактических расходов WB. Используются только завершённые дни "
            "до вчерашнего включительно. Расчёт работает в режиме dry-run: цены WB не изменяются."
        ),
        reply_markup={
            "inline_keyboard": [
                [
                    {"text": "15 дней", "callback_data": "wpm_period:15"},
                    {"text": "30 дней", "callback_data": "wpm_period:30"},
                ],
                [{"text": "Отменить", "callback_data": "wpm_cancel"}],
            ]
        },
    )


def _wb_pricing_margin_period_callback(data: str) -> TelegramCommandResult:
    raw_days = data.removeprefix("wpm_period:").strip()
    period_days = int(raw_days) if raw_days.isdigit() else 0
    if period_days not in {15, 30}:
        return _wb_pricing_margin_invalid("Период повреждён. Запустите «Цены и маржа WB» заново.")
    return TelegramCommandResult(
        command="/wb-pricing-margin",
        ok=True,
        mode="input",
        text=(
            f"WB: цены и маржа\n\nПериод расходов: `{period_days} дней`.\n\n"
            "Введите себестоимость одного физического изделия в рублях.\n\n"
            "Пример: `85`. Для комплекта бот умножит сумму на `pack_qty`."
        ),
        reply_markup={"inline_keyboard": [[{"text": "Отменить", "callback_data": "wpm_cancel"}]]},
        conversation_state={"stage": "wb_pricing_cost_input", "period_days": period_days},
    )


def _wb_pricing_cost_input(message: str, *, state: dict[str, Any]) -> TelegramCommandResult:
    period_days = _int_value(state.get("period_days"))
    cost = _owner_decimal(message)
    if period_days not in {15, 30}:
        return _wb_pricing_margin_invalid("Период расчёта потерян. Запустите расчёт заново.")
    if cost is None or cost <= 0 or cost > Decimal("100000"):
        return TelegramCommandResult(
            command="/wb-pricing-margin",
            ok=False,
            mode="input",
            blocked_reason="invalid_wb_unit_cost",
            text=(
                "WB: цены и маржа\n\nВведите положительную себестоимость одного физического "
                "изделия в рублях, например `85` или `85,50`."
            ),
            reply_markup={"inline_keyboard": [[{"text": "Отменить", "callback_data": "wpm_cancel"}]]},
            conversation_state=state,
        )
    cost_text = _decimal_text(cost)
    return TelegramCommandResult(
        command="/wb-pricing-margin",
        ok=True,
        mode="input",
        text=(
            f"WB: цены и маржа\n\nСебестоимость изделия: `{cost_text} руб.`\n\n"
            "Введите желаемую маржу с одного физического изделия в рублях.\n\n"
            "После ввода Job Worker соберёт расходы WB и сформирует dry-run расчёт."
        ),
        reply_markup={"inline_keyboard": [[{"text": "Отменить", "callback_data": "wpm_cancel"}]]},
        conversation_state={
            "stage": "wb_pricing_margin_input",
            "period_days": period_days,
            "unit_cost": cost_text,
        },
    )


def _wb_pricing_margin_runtime_required(message: str, *, state: dict[str, Any]) -> TelegramCommandResult:
    margin = _owner_decimal(message)
    if margin is None or margin < 0 or margin > Decimal("100000"):
        return TelegramCommandResult(
            command="/wb-pricing-margin",
            ok=False,
            mode="input",
            blocked_reason="invalid_wb_target_margin",
            text=(
                "WB: цены и маржа\n\nВведите неотрицательную маржу на одно физическое "
                "изделие в рублях, например `60`."
            ),
            reply_markup={"inline_keyboard": [[{"text": "Отменить", "callback_data": "wpm_cancel"}]]},
            conversation_state=state,
        )
    return TelegramCommandResult(
        command="/wb-pricing-margin",
        ok=False,
        mode="input",
        blocked_reason="runtime_jobs_required",
        text="Расчёт должен быть поставлен в Job Worker.",
        conversation_state=state,
    )


def _wb_pricing_margin_cancel() -> TelegramCommandResult:
    return TelegramCommandResult(
        command="/wb-pricing-margin",
        ok=True,
        text="WB: расчёт цен и маржи отменён. Изменений в кабинете не было.",
        reply_markup=WB_MENU_KEYBOARD,
    )


def _wb_pricing_margin_invalid(message: str) -> TelegramCommandResult:
    return TelegramCommandResult(
        command="/wb-pricing-margin",
        ok=False,
        mode="input",
        blocked_reason="invalid_wb_pricing_margin_parameters",
        text=f"WB: цены и маржа\n\n{message}\n\nИзменений в WB не было.",
        reply_markup=WB_MENU_KEYBOARD,
    )


def _ozon_work_plan_mode_callback(data: str) -> TelegramCommandResult:
    code = data.removeprefix("ozwp_mode:").strip()
    if code not in {"c", "d"}:
        return _ozon_work_plan_invalid("Режим расчёта повреждён. Запустите «В работу Ozon» заново.")
    capacity_mode = code == "c"
    return TelegramCommandResult(
        command="/ozon-work-plan",
        ok=True,
        mode="input",
        text=(
            "Ozon: в работу\n\n"
            + (
                "Введите доступное количество физических изделий целым числом.\n\n"
                "Пример: `1400`. Итоговый объём после округления не превысит это значение."
                if capacity_mode
                else "Введите необходимый период покрытия в днях целым числом.\n\n"
                "Пример: `30`. Потребность будет рассчитана отдельно внутри каждого кластера."
            )
        ),
        reply_markup={"inline_keyboard": [[{"text": "Отменить", "callback_data": "ozwp_cancel"}]]},
        conversation_state={"stage": "ozon_work_capacity_input" if capacity_mode else "ozon_work_days_input"},
    )


def _ozon_work_plan_value(message: str, *, state: dict[str, Any]) -> TelegramCommandResult:
    text = str(message or "").strip()
    stage = str(state.get("stage") or "")
    code = "c" if stage == "ozon_work_capacity_input" else "d" if stage == "ozon_work_days_input" else ""
    maximum = 100_000 if code == "c" else 365
    value = int(text) if text.isdigit() else 0
    if not code or value <= 0 or value > maximum:
        unit = "физических изделий" if code == "c" else "дней"
        return TelegramCommandResult(
            command="/ozon-work-plan",
            ok=False,
            mode="input",
            blocked_reason="invalid_ozon_work_plan_value",
            text=(
                "Ozon: в работу\n\n"
                f"Введите одно целое положительное число: количество {unit}. Максимум: `{maximum}`.\n\n"
                "Расчёт не запускался, изменений в Ozon не было."
            ),
            reply_markup={"inline_keyboard": [[{"text": "Отменить", "callback_data": "ozwp_cancel"}]]},
            conversation_state=state,
        )
    return TelegramCommandResult(
        command="/ozon-work-plan",
        ok=True,
        mode="input",
        text=(
            "Ozon: в работу\n\n"
            "Введите количество кластеров назначения целым числом от `1` до `20`.\n\n"
            "Бот рассчитает чистую потребность каждого кластера и выберет указанное количество направлений "
            "по убыванию дефицита."
        ),
        reply_markup={"inline_keyboard": [[{"text": "Отменить", "callback_data": "ozwp_cancel"}]]},
        conversation_state={"stage": "ozon_work_clusters_input", "mode_code": code, "value": value},
    )


def _ozon_work_plan_clusters(message: str, *, state: dict[str, Any]) -> TelegramCommandResult:
    text = str(message or "").strip()
    cluster_count = int(text) if text.isdigit() else 0
    code = str(state.get("mode_code") or "")
    value = _int_value(state.get("value"))
    if code not in {"c", "d"} or value <= 0:
        return _ozon_work_plan_invalid("Параметры расчёта потеряны. Запустите «В работу Ozon» заново.")
    if cluster_count <= 0 or cluster_count > 20:
        return TelegramCommandResult(
            command="/ozon-work-plan",
            ok=False,
            mode="input",
            blocked_reason="invalid_ozon_work_plan_cluster_count",
            text=(
                "Ozon: в работу\n\n"
                "Введите количество кластеров целым числом от `1` до `20`.\n\n"
                "Расчёт не запускался, изменений в Ozon не было."
            ),
            reply_markup={"inline_keyboard": [[{"text": "Отменить", "callback_data": "ozwp_cancel"}]]},
            conversation_state=state,
        )
    mode_label = "по производственной возможности" if code == "c" else "по периоду покрытия"
    value_label = f"{value} физических изделий" if code == "c" else f"{value} дней"
    detail = (
        "Цель покрытия: 30 дней; общий физический объём после округления не будет превышен."
        if code == "c"
        else "Количество будет рассчитано автоматически и округлено по кратности типа изделия."
    )
    return TelegramCommandResult(
        command="/ozon-work-plan",
        ok=True,
        mode="review",
        text=(
            "Ozon: в работу\n\n"
            "Проверьте параметры:\n\n"
            f"- режим: `{mode_label}`;\n"
            f"- значение: `{value_label}`;\n"
            f"- кластеров назначения: `{cluster_count}`;\n"
            "- спрос: последние `90` полных дней с повышенным весом последних `30` дней;\n"
            "- потребность: отдельно для каждой пары товар × кластер;\n"
            "- вычитаются только свободный остаток и confirmed inbound этого же кластера;\n"
            f"- {detail}\n\n"
            "Подтверждение сформирует только Excel и read-only отчёт. Поставка в Ozon не создаётся."
        ),
        reply_markup={
            "inline_keyboard": [
                [{"text": "Сформировать файл", "callback_data": f"ozwp_run:{code}:{value}:{cluster_count}"}],
                [{"text": "Отменить", "callback_data": "ozwp_cancel"}],
            ]
        },
    )


def _ozon_work_plan_run_callback(
    data: str,
    *,
    data_dir: Path,
    credentials: AppCredentials | None,
) -> TelegramCommandResult:
    parts = data.split(":")
    if len(parts) != 4 or parts[1] not in {"c", "d"} or not parts[2].isdigit() or not parts[3].isdigit():
        return _ozon_work_plan_invalid("Параметры подтверждения повреждены. Запустите «В работу Ozon» заново.")
    code = parts[1]
    value = int(parts[2])
    cluster_count = int(parts[3])
    maximum = 100_000 if code == "c" else 365
    if value <= 0 or value > maximum or cluster_count <= 0 or cluster_count > 20:
        return _ozon_work_plan_invalid("Введённые значения вышли за допустимые границы.")
    calculation_mode = "capacity" if code == "c" else "coverage_days"
    result = WorkflowRunner(data_dir=data_dir, credentials=credentials).run_read_only(
        "ozon-production-work-plan",
        inputs={"mode": calculation_mode, "value": value, "cluster_count": cluster_count},
    )
    if not result.ok:
        return TelegramCommandResult(
            command="/ozon-work-plan",
            ok=False,
            blocked_reason=result.blocked_reason or "ozon_work_plan_failed",
            text=(
                "Ozon: в работу\n\n"
                "Файл не сформирован.\n\n"
                f"Причина: `{result.error or result.status}`\n\n"
                "Производство и поставка Ozon не запускались."
            ),
        )
    summary = result.summary
    metrics = summary.get("metrics") if isinstance(summary.get("metrics"), dict) else {}
    cluster_totals = summary.get("cluster_totals") if isinstance(summary.get("cluster_totals"), list) else []
    warnings = summary.get("warnings") if isinstance(summary.get("warnings"), list) else []
    run_id = str(summary.get("run_id") or "")
    lines = [
        "Ozon: в работу",
        "",
        "Excel сформирован. Производство и поставка Ozon не запускались.",
        "",
        f"- товарных единиц Ozon: `{_int(metrics.get('marketplace_units'))}`;",
        f"- физических изделий: `{_int(metrics.get('physical_pieces'))}`;",
        f"- артикулов: `{_int(metrics.get('articles'))}`;",
        f"- выбрано кластеров: `{_int(metrics.get('selected_clusters'))}`;",
        f"- строк контроля: `{_int(metrics.get('control_rows'))}`.",
    ]
    if calculation_mode == "capacity":
        lines.append(f"- не распределено мощности: `{_int(metrics.get('unused_capacity_physical'))}` физических изделий.")
    if cluster_totals:
        lines.extend(["", "По кластерам:"])
        for row in cluster_totals:
            if not isinstance(row, dict):
                continue
            lines.append(
                f"- #{_int(row.get('priority'))} {row.get('cluster')}: "
                f"`{_int(row.get('marketplace_units'))}` ед. Ozon / "
                f"`{_int(row.get('physical_pieces'))}` физических изделий."
            )
    if warnings:
        lines.extend(["", "Ограничения:"])
        lines.extend(f"- {warning}" for warning in warnings[:5])
    lines.extend(["", f"Run ID: `{run_id}`", "", "Проверьте приложенный Excel, затем утвердите или отклоните план."])
    return TelegramCommandResult(
        command="/ozon-work-plan",
        ok=True,
        mode="review",
        text="\n".join(lines),
        artifacts=result.artifacts,
        reply_markup={
            "inline_keyboard": [
                [{"text": "Утвердить в работу", "callback_data": f"ozwp_approve:{run_id}"}],
                [{"text": "Отклонить", "callback_data": f"ozwp_reject:{run_id}"}],
            ]
        },
    )


def _ozon_work_plan_decision_callback(data: str, *, data_dir: Path, approved: bool) -> TelegramCommandResult:
    prefix = "ozwp_approve:" if approved else "ozwp_reject:"
    plan_run_id = data.removeprefix(prefix).strip()
    try:
        decision = record_ozon_work_plan_decision(
            data_dir=data_dir,
            plan_run_id=plan_run_id,
            approved=approved,
        )
    except Exception as exc:  # noqa: BLE001 - Telegram receives a sanitized local decision error.
        return TelegramCommandResult(
            command="/ozon-work-plan",
            ok=False,
            blocked_reason="ozon_work_plan_decision_failed",
            text=(
                "Ozon: в работу\n\n"
                "Решение не сохранено.\n\n"
                f"Причина: `{_safe_error(exc)}`\n\n"
                "Производство и поставка Ozon не запускались."
            ),
        )
    status_text = "утверждён в работу" if approved else "отклонён"
    return TelegramCommandResult(
        command="/ozon-work-plan",
        ok=True,
        mode="approved" if approved else "cancelled",
        text=(
            "Ozon: в работу\n\n"
            f"План `{decision['plan_run_id']}` {status_text}.\n\n"
            "Решение сохранено локально. Поставка в Ozon автоматически не создавалась."
        ),
        reply_markup=OZON_MENU_KEYBOARD,
    )


def _ozon_work_plan_cancel() -> TelegramCommandResult:
    return TelegramCommandResult(
        command="/ozon-work-plan",
        ok=True,
        mode="cancelled",
        text="Ozon: в работу\n\nРасчёт отменён. Данные не запрашивались, производство и поставка Ozon не запускались.",
        reply_markup=OZON_MENU_KEYBOARD,
    )


def _ozon_work_plan_invalid(reason: str) -> TelegramCommandResult:
    return TelegramCommandResult(
        command="/ozon-work-plan",
        ok=False,
        blocked_reason="invalid_ozon_work_plan_parameters",
        text=f"Ozon: в работу\n\n{reason}\n\nПроизводство и поставка Ozon не запускались.",
    )


def _wb_work_plan_start() -> TelegramCommandResult:
    return TelegramCommandResult(
        command="/wb-work-plan",
        ok=True,
        mode="input",
        text=(
            "WB: в работу\n\n"
            "Выберите способ расчёта:\n\n"
            "- по производственной возможности — ввести доступное количество физических изделий;\n"
            "- по периоду покрытия — ввести количество дней запаса.\n\n"
            "После этого бот запросит количество кластеров назначения от 1 до 6. "
            "Распределение выполняется автоматически по локальной потребности. Поставка в WB не создаётся."
        ),
        reply_markup={
            "inline_keyboard": [
                [{"text": "По объёму производства", "callback_data": "wbwp_mode:c"}],
                [{"text": "По дням покрытия", "callback_data": "wbwp_mode:d"}],
                [{"text": "Отменить", "callback_data": "wbwp_cancel"}],
            ]
        },
    )


def _wb_work_plan_mode_callback(data: str) -> TelegramCommandResult:
    code = data.removeprefix("wbwp_mode:").strip()
    if code not in {"c", "d"}:
        return _wb_work_plan_invalid("Режим расчёта повреждён. Запустите «В работу» заново.")
    capacity_mode = code == "c"
    return TelegramCommandResult(
        command="/wb-work-plan",
        ok=True,
        mode="input",
        text=(
            "WB: в работу\n\n"
            + (
                "Введите доступное количество физических изделий целым числом.\n\n"
                "Пример: `1000`. Бот распределит объём по артикулам и регионам, не превышая его."
                if capacity_mode
                else "Введите необходимый период покрытия в днях целым числом.\n\n"
                "Пример: `30`. Бот рассчитает потребность по среднесуточным продажам."
            )
        ),
        reply_markup={"inline_keyboard": [[{"text": "Отменить", "callback_data": "wbwp_cancel"}]]},
        conversation_state={"stage": "wb_work_capacity_input" if capacity_mode else "wb_work_days_input"},
    )


def _wb_work_plan_value(message: str, *, state: dict[str, Any]) -> TelegramCommandResult:
    text = str(message or "").strip()
    stage = str(state.get("stage") or "")
    code = "c" if stage == "wb_work_capacity_input" else "d" if stage == "wb_work_days_input" else ""
    maximum = 100_000 if code == "c" else 365
    value = int(text) if text.isdigit() else 0
    if not code or value <= 0 or value > maximum:
        unit = "физических изделий" if code == "c" else "дней"
        return TelegramCommandResult(
            command="/wb-work-plan",
            ok=False,
            mode="input",
            blocked_reason="invalid_wb_work_plan_value",
            text=(
                "WB: в работу\n\n"
                f"Введите одно целое положительное число: количество {unit}. Максимум: `{maximum}`.\n\n"
                "Расчёт не запускался, изменений в WB не было."
            ),
            reply_markup={"inline_keyboard": [[{"text": "Отменить", "callback_data": "wbwp_cancel"}]]},
            conversation_state=state,
        )
    return TelegramCommandResult(
        command="/wb-work-plan",
        ok=True,
        mode="input",
        text=(
            "WB: в работу\n\n"
            "Введите количество кластеров назначения целым числом от `1` до `6`.\n\n"
            "Бот выберет кластеры по убыванию подтверждённой локальной потребности."
        ),
        reply_markup={"inline_keyboard": [[{"text": "Отменить", "callback_data": "wbwp_cancel"}]]},
        conversation_state={"stage": "wb_work_clusters_input", "mode_code": code, "value": value},
    )


def _wb_work_plan_clusters(message: str, *, state: dict[str, Any]) -> TelegramCommandResult:
    text = str(message or "").strip()
    code = str(state.get("mode_code") or "")
    value = _int_value(state.get("value"))
    cluster_count = int(text) if text.isdigit() else 0
    if code not in {"c", "d"} or value <= 0 or not 1 <= cluster_count <= 6:
        return TelegramCommandResult(
            command="/wb-work-plan",
            ok=False,
            mode="input",
            blocked_reason="invalid_wb_work_plan_cluster_count",
            text=(
                "WB: в работу\n\n"
                "Введите целое число кластеров назначения от `1` до `6`.\n\n"
                "Расчёт не запускался, изменений в WB не было."
            ),
            reply_markup={"inline_keyboard": [[{"text": "Отменить", "callback_data": "wbwp_cancel"}]]},
            conversation_state=state,
        )
    mode_label = "по производственной возможности" if code == "c" else "по периоду покрытия"
    value_label = f"{value} физических изделий" if code == "c" else f"{value} дней"
    detail = (
        "Цель покрытия: 30 дней; общий физический объём после округления не будет превышен."
        if code == "c"
        else "Количество будет рассчитано автоматически и округлено по кратности типа изделия."
    )
    return TelegramCommandResult(
        command="/wb-work-plan",
        ok=True,
        mode="review",
        text=(
            "WB: в работу\n\n"
            "Проверьте параметры:\n\n"
            f"- режим: `{mode_label}`;\n"
            f"- значение: `{value_label}`;\n"
            f"- кластеров назначения: `{cluster_count}`;\n"
            "- спрос: последние `90` полных дней с повышенным весом последних `30` дней;\n"
            "- приоритет: по суммарной положительной потребности каждого кластера;\n"
            "- расчёт каждой позиции: спрос в кластере минус остаток этой позиции в том же кластере "
            "минус подтверждённые поставки этой позиции в тот же кластер;\n"
            f"- {detail}\n\n"
            "Подтверждение сформирует только Excel и read-only отчёт. Поставка в WB не создаётся."
        ),
        reply_markup={
            "inline_keyboard": [
                [
                    {
                        "text": "Сформировать файл",
                        "callback_data": f"wbwp_run:{code}:{value}:{cluster_count}",
                    }
                ],
                [{"text": "Отменить", "callback_data": "wbwp_cancel"}],
            ]
        },
    )


def _wb_work_plan_run_callback(
    data: str,
    *,
    data_dir: Path,
    credentials: AppCredentials | None,
) -> TelegramCommandResult:
    parts = data.split(":")
    if (
        len(parts) != 4
        or parts[1] not in {"c", "d"}
        or not parts[2].isdigit()
        or not parts[3].isdigit()
    ):
        return _wb_work_plan_invalid("Параметры подтверждения повреждены. Запустите «В работу» заново.")
    code = parts[1]
    value = int(parts[2])
    cluster_count = int(parts[3])
    maximum = 100_000 if code == "c" else 365
    if value <= 0 or value > maximum or not 1 <= cluster_count <= 6:
        return _wb_work_plan_invalid("Введённое значение вышло за допустимые границы.")
    calculation_mode = "capacity" if code == "c" else "coverage_days"
    result = WorkflowRunner(data_dir=data_dir, credentials=credentials).run_read_only(
        "wb-production-work-plan",
        inputs={"mode": calculation_mode, "value": value, "cluster_count": cluster_count},
    )
    if not result.ok:
        return TelegramCommandResult(
            command="/wb-work-plan",
            ok=False,
            blocked_reason=result.blocked_reason or "wb_work_plan_failed",
            text=(
                "WB: в работу\n\n"
                "Файл не сформирован.\n\n"
                f"Причина: `{result.error or result.status}`\n\n"
                "Производство и поставка WB не запускались."
            ),
        )
    summary = result.summary
    metrics = summary.get("metrics") if isinstance(summary.get("metrics"), dict) else {}
    region_totals = summary.get("region_totals") if isinstance(summary.get("region_totals"), list) else []
    warnings = summary.get("warnings") if isinstance(summary.get("warnings"), list) else []
    run_id = str(summary.get("run_id") or "")
    lines = [
        "WB: в работу",
        "",
        "Excel сформирован. Производство и поставка WB не запускались.",
        "",
        f"- товарных единиц WB: `{_int(metrics.get('marketplace_units'))}`;",
        f"- физических изделий: `{_int(metrics.get('physical_pieces'))}`;",
        f"- артикулов: `{_int(metrics.get('articles'))}`;",
        f"- выбранных кластеров назначения: `{_int(metrics.get('selected_clusters'))}`;",
        f"- строк контроля: `{_int(metrics.get('control_rows'))}`.",
    ]
    if calculation_mode == "capacity":
        lines.append(f"- не распределено мощности: `{_int(metrics.get('unused_capacity_physical'))}` физических изделий.")
    if region_totals:
        lines.extend(["", "По регионам:"])
        for row in region_totals:
            if not isinstance(row, dict):
                continue
            lines.append(
                f"- #{_int(row.get('priority'))} {row.get('region')}: "
                f"`{_int(row.get('marketplace_units'))}` ед. WB / "
                f"`{_int(row.get('physical_pieces'))}` физических изделий."
            )
    if warnings:
        lines.extend(["", "Ограничения:"])
        lines.extend(f"- {warning}" for warning in warnings[:5])
    lines.extend(["", f"Run ID: `{run_id}`", "", "Проверьте приложенный Excel, затем утвердите или отклоните план."])
    return TelegramCommandResult(
        command="/wb-work-plan",
        ok=True,
        mode="review",
        text="\n".join(lines),
        artifacts=result.artifacts,
        reply_markup={
            "inline_keyboard": [
                [{"text": "Утвердить в работу", "callback_data": f"wbwp_approve:{run_id}"}],
                [{"text": "Отклонить", "callback_data": f"wbwp_reject:{run_id}"}],
            ]
        },
    )


def _wb_work_plan_decision_callback(data: str, *, data_dir: Path, approved: bool) -> TelegramCommandResult:
    prefix = "wbwp_approve:" if approved else "wbwp_reject:"
    plan_run_id = data.removeprefix(prefix).strip()
    try:
        decision = record_wb_work_plan_decision(
            data_dir=data_dir,
            plan_run_id=plan_run_id,
            approved=approved,
        )
    except Exception as exc:  # noqa: BLE001 - Telegram receives a sanitized local decision error.
        return TelegramCommandResult(
            command="/wb-work-plan",
            ok=False,
            blocked_reason="wb_work_plan_decision_failed",
            text=(
                "WB: в работу\n\n"
                "Решение не сохранено.\n\n"
                f"Причина: `{_safe_error(exc)}`\n\n"
                "Производство и поставка WB не запускались."
            ),
        )
    status_text = "утверждён в работу" if approved else "отклонён"
    return TelegramCommandResult(
        command="/wb-work-plan",
        ok=True,
        mode="approved" if approved else "cancelled",
        text=(
            "WB: в работу\n\n"
            f"План `{decision['plan_run_id']}` {status_text}.\n\n"
            "Решение сохранено локально. Поставка в WB автоматически не создавалась."
        ),
        reply_markup=WB_MENU_KEYBOARD,
    )


def _wb_work_plan_cancel() -> TelegramCommandResult:
    return TelegramCommandResult(
        command="/wb-work-plan",
        ok=True,
        mode="cancelled",
        text="WB: в работу\n\nРасчёт отменён. Данные не запрашивались, производство и поставка WB не запускались.",
        reply_markup=WB_MENU_KEYBOARD,
    )


def _wb_work_plan_invalid(reason: str) -> TelegramCommandResult:
    return TelegramCommandResult(
        command="/wb-work-plan",
        ok=False,
        blocked_reason="invalid_wb_work_plan_parameters",
        text=f"WB: в работу\n\n{reason}\n\nПроизводство и поставка WB не запускались.",
    )


def _ozon_inbox_plan(
    *,
    data_dir: Path,
    credentials: AppCredentials | None,
) -> TelegramCommandResult:
    try:
        result = run_ozon_inbox_triage(
            credentials=credentials or load_credentials(),
            data_dir=data_dir,
        )
    except Exception as exc:  # noqa: BLE001 - Telegram must return a safe failure.
        return TelegramCommandResult(
            command="/ozon-inbox",
            ok=False,
            mode="dry_run",
            blocked_reason="ozon_inbox_failed",
            text=(
                "Ozon входящие\n\n"
                "Итог: свежий пакет отзывов, вопросов и уведомлений Ozon не удалось собрать.\n\n"
                f"Причина: `{_safe_error(exc)}`\n\n"
                "Изменений в Ozon не выполнял."
            ),
        )

    reviews = result.get("reviews") if isinstance(result.get("reviews"), dict) else {}
    messenger = result.get("messenger") if isinstance(result.get("messenger"), dict) else {}
    review_counts = _review_action_counts_from_summary(reviews)
    messenger_counts = _messenger_counts_from_pending(data_dir=data_dir, run_id=str(result.get("run_id") or ""))
    actions_count = int(result.get("actions_count") or 0)
    run_id = str(result.get("run_id") or "")
    lines = [
        "Ozon входящие",
        "",
        f"Итог: свежий dry-run построен, статус `{result.get('overall_status') or 'н/д'}`. Ответы не отправлены, уведомления не отмечены.",
        f"Run ID: `{run_id or 'н/д'}`",
        "",
        "Сводка:",
        f"- отзывы/вопросы к действию: `{_int(reviews.get('actions_count'))}`",
        f"- вопросы покупателей: `{_int((review_counts.get('question_answer') or 0) + (review_counts.get('manual_question_review') or 0))}`",
        f"- автоответы на вопросы: `{_int(review_counts.get('question_answer'))}`",
        f"- вопросы на ручную проверку: `{_int(review_counts.get('manual_question_review'))}`",
        f"- оценок по конкретным товарам: `{_int(result.get('product_rating_rows_count'))}`",
        f"- низких оценок 1-3 по товарам: `{_int(result.get('low_rating_product_rows_count'))}`",
        f"- Messenger/уведомления к действию: `{sum(messenger_counts.values())}`",
        f"- ответы покупателям в чатах: `{_int(messenger_counts.get('send_chat_message', 0))}`",
        f"- уведомления отметить прочитанными: `{_int(messenger_counts.get('mark_chat_read', 0))}`",
        f"- ручная проверка чатов: `{_int(messenger_counts.get('manual_chat_review', 0))}`",
        f"- total_unread_count API: `{_int(messenger.get('total_unread_count'))}`",
        "",
        "Что дальше:",
    ]
    if actions_count:
        lines.extend(
            [
                "В синхронном dry-run write-кнопка не показывается.",
                "Apply доступен только из runtime Job Worker после создания и согласования runtime approval.",
            ]
        )
    else:
        lines.append("Действий к применению нет, кнопку apply не показываю.")
    artifacts = _safe_artifacts(result)
    if artifacts:
        lines.extend(["", "Файлы:"])
        if artifacts.get("report"):
            lines.append(f"- отчет: `{artifacts['report']}`")
    return TelegramCommandResult(
        command="/ozon-inbox",
        ok=True,
        mode="dry_run",
        text="\n".join(lines),
        artifacts=artifacts,
        reply_markup={},
    )


def _wb_inbox_plan(
    *,
    data_dir: Path,
    credentials: AppCredentials | None,
) -> TelegramCommandResult:
    try:
        result = run_wb_inbox_triage(
            credentials=credentials or load_credentials(),
            data_dir=data_dir,
        )
    except Exception as exc:  # noqa: BLE001 - Telegram must return a safe failure.
        return TelegramCommandResult(
            command="/wb-inbox",
            ok=False,
            mode="dry_run",
            blocked_reason="wb_inbox_failed",
            text=(
                "WB входящие\n\n"
                "Итог: свежий пакет отзывов и вопросов WB не удалось собрать.\n\n"
                f"Причина: `{_safe_error(exc)}`\n\n"
                "Изменений в WB не выполнял."
            ),
        )

    reviews = result.get("reviews") if isinstance(result.get("reviews"), dict) else {}
    notifications = result.get("wb_notifications") if isinstance(result.get("wb_notifications"), dict) else {}
    notification_items = notifications.get("items") if isinstance(notifications.get("items"), list) else []
    important_items = notifications.get("important_items") if isinstance(notifications.get("important_items"), list) else []
    run_id = str(result.get("run_id") or "")
    actions_count = int(result.get("actions_count") or 0)
    lines = [
        "WB входящие",
        "",
        f"Итог: свежий dry-run построен, статус `{result.get('overall_status') or 'н/д'}`. Ответы не отправлены.",
        f"Run ID: `{run_id or 'н/д'}`",
        "",
        "Сводка:",
        f"- WB отзывы/вопросы к действию: `{_int(reviews.get('actions_count'))}`",
        f"- оценок по конкретным товарам: `{_int(result.get('product_rating_rows_count'))}`",
        f"- низких оценок 1-3 по товарам: `{_int(result.get('low_rating_product_rows_count'))}`",
        f"- WB уведомления: `{notifications.get('status') or 'н/д'}`",
        f"- WB новости/уведомления прочитано: `{len(notification_items)}`",
        f"- важных WB новостей/уведомлений: `{len(important_items)}`",
        "",
        "Важно:",
        "- WB вопросы входят в этот пакет через официальный Feedbacks API.",
        "- WB новости/уведомления читаются из ЛК `news-v2` в read-only режиме; mark-read для WB уведомлений бот пока не делает.",
        "",
        "Что дальше:",
    ]
    if actions_count:
        lines.extend(
            [
                "В синхронном dry-run write-кнопка не показывается.",
                "Apply доступен только из runtime Job Worker после создания и согласования runtime approval.",
            ]
        )
    else:
        lines.append("Действий к применению нет, кнопку apply не показываю.")
    artifacts = _safe_artifacts(result)
    if artifacts:
        lines.extend(["", "Файлы:"])
        if artifacts.get("report"):
            lines.append(f"- отчет: `{artifacts['report']}`")
    return TelegramCommandResult(
        command="/wb-inbox",
        ok=True,
        mode="dry_run",
        text="\n".join(lines),
        artifacts=artifacts,
        reply_markup={},
    )


def _ozon_inbox_apply(
    *,
    source_run_id: str,
    data_dir: Path,
    credentials: AppCredentials | None,
) -> TelegramCommandResult:
    if not _valid_inbox_run_id(source_run_id, prefix="ozon_inbox_"):
        return TelegramCommandResult(
            command="/ozon_inbox_apply",
            ok=False,
            mode="apply",
            blocked_reason="invalid_source_run_id",
            text=(
                "Ozon входящие apply\n\n"
                "Итог: apply заблокирован - некорректный `run_id`.\n\n"
                f"Получено: `{_truncate(source_run_id, 100)}`\n\n"
                "Изменений в Ozon не выполнял."
            ),
        )
    try:
        result = _run_inbox_apply_job(
            task_id="ozon-inbox-apply",
            source_run_id=source_run_id,
            data_dir=data_dir,
            credentials=credentials,
        )
    except Exception as exc:  # noqa: BLE001
        return TelegramCommandResult(
            command="/ozon_inbox_apply",
            ok=False,
            mode="apply",
            blocked_reason="ozon_inbox_apply_failed",
            text=(
                "Ozon входящие apply\n\n"
                "Итог: apply не выполнен или остановлен safety-контуром.\n\n"
                f"Run ID: `{source_run_id}`\n"
                f"Причина: `{_safe_error(exc)}`\n\n"
                "Нужен новый dry-run и новое подтверждение, если пакет устарел."
            ),
        )
    return _inbox_apply_result_text(command="/ozon_inbox_apply", title="Ozon входящие apply", result=result)


def _wb_inbox_apply(
    *,
    source_run_id: str,
    data_dir: Path,
    credentials: AppCredentials | None,
) -> TelegramCommandResult:
    if not _valid_inbox_run_id(source_run_id, prefix="wb_inbox_"):
        return TelegramCommandResult(
            command="/wb_inbox_apply",
            ok=False,
            mode="apply",
            blocked_reason="invalid_source_run_id",
            text=(
                "WB входящие apply\n\n"
                "Итог: apply заблокирован - некорректный `run_id`.\n\n"
                f"Получено: `{_truncate(source_run_id, 100)}`\n\n"
                "Изменений в WB не выполнял."
            ),
        )
    try:
        result = _run_inbox_apply_job(
            task_id="wb-inbox-apply",
            source_run_id=source_run_id,
            data_dir=data_dir,
            credentials=credentials,
        )
    except Exception as exc:  # noqa: BLE001
        return TelegramCommandResult(
            command="/wb_inbox_apply",
            ok=False,
            mode="apply",
            blocked_reason="wb_inbox_apply_failed",
            text=(
                "WB входящие apply\n\n"
                "Итог: apply не выполнен или остановлен safety-контуром.\n\n"
                f"Run ID: `{source_run_id}`\n"
                f"Причина: `{_safe_error(exc)}`\n\n"
                "Нужен новый dry-run и новое подтверждение, если пакет устарел."
            ),
        )
    return _inbox_apply_result_text(command="/wb_inbox_apply", title="WB входящие apply", result=result)


def _wb_actions_plan(
    *,
    data_dir: Path,
    credentials: AppCredentials | None,
) -> TelegramCommandResult:
    return _wb_actions_plan_for_scheme(
        scheme_text="70-55-55",
        data_dir=data_dir,
        credentials=credentials,
        manual=False,
    )


def _period_report_start(marketplace: str) -> TelegramCommandResult:
    market_code = "o" if marketplace == "ozon" else "w"
    title = "Ozon" if marketplace == "ozon" else "Wildberries"
    return TelegramCommandResult(
        command=f"/period-report-{marketplace}",
        ok=True,
        mode="input",
        text=(
            f"{title}: отчёт за период\n\n"
            "Выберите вид отчёта:\n\n"
            "- Краткий - основные показатели, выкупы в товарах и физических изделиях.\n"
            "- Финансовый - выплаты и расходы по статьям.\n"
            "- Полный - сводка, финансы, товары и динамика по дням.\n\n"
            "Данные только читаются. Изменений в кабинете не будет."
        ),
        reply_markup={
            "inline_keyboard": [
                [
                    {"text": "Краткий", "callback_data": f"mpr_type:{market_code}:s"},
                    {"text": "Финансовый", "callback_data": f"mpr_type:{market_code}:f"},
                ],
                [{"text": "Полный отчёт", "callback_data": f"mpr_type:{market_code}:a"}],
                [{"text": "Отменить", "callback_data": "mpr_cancel"}],
            ]
        },
    )


def _period_report_marketplace_start() -> TelegramCommandResult:
    return TelegramCommandResult(
        command="/period-report",
        ok=True,
        mode="input",
        text="Отчёт за период\n\nВыберите маркетплейс:",
        reply_markup={
            "inline_keyboard": [
                [
                    {"text": "Ozon", "callback_data": "mpr_market:o"},
                    {"text": "Wildberries", "callback_data": "mpr_market:w"},
                ],
                [{"text": "Отменить", "callback_data": "mpr_cancel"}],
            ]
        },
    )


def _period_report_type_callback(data: str) -> TelegramCommandResult:
    parts = data.split(":")
    if len(parts) != 3 or parts[1] not in {"o", "w"} or parts[2] not in {"s", "f", "a"}:
        return _period_report_invalid("Некорректно выбран вид отчёта.")
    market_code, report_code = parts[1], parts[2]
    title = "Ozon" if market_code == "o" else "Wildberries"
    report_label = _period_report_label(report_code)
    return TelegramCommandResult(
        command="/period-report",
        ok=True,
        mode="input",
        text=f"{title}: {report_label.lower()} отчёт\n\nВыберите период:",
        reply_markup={
            "inline_keyboard": [
                [
                    {"text": "Вчера", "callback_data": f"mpr_period:{market_code}:{report_code}:y"},
                    {"text": "7 дней", "callback_data": f"mpr_period:{market_code}:{report_code}:7"},
                    {"text": "30 дней", "callback_data": f"mpr_period:{market_code}:{report_code}:30"},
                ],
                [
                    {"text": "Текущий месяц", "callback_data": f"mpr_period:{market_code}:{report_code}:m"},
                    {"text": "Прошлый месяц", "callback_data": f"mpr_period:{market_code}:{report_code}:p"},
                ],
                [{"text": "Свой период", "callback_data": f"mpr_period:{market_code}:{report_code}:c"}],
                [{"text": "Отменить", "callback_data": "mpr_cancel"}],
            ]
        },
    )


def _period_report_period_callback(data: str) -> TelegramCommandResult:
    parts = data.split(":")
    if len(parts) != 4 or parts[1] not in {"o", "w"} or parts[2] not in {"s", "f", "a"}:
        return _period_report_invalid("Некорректно выбран период.")
    market_code, report_code, period_code = parts[1], parts[2], parts[3]
    if period_code == "c":
        return TelegramCommandResult(
            command="/period-report",
            ok=True,
            mode="input",
            text=(
                "Свой период\n\n"
                "Введите дату начала в формате `ДД.ММ.ГГГГ` или `ГГГГ-ММ-ДД`."
            ),
            reply_markup={"inline_keyboard": [[{"text": "Отменить", "callback_data": "mpr_cancel"}]]},
            conversation_state={
                "stage": "period_report_custom_from",
                "market_code": market_code,
                "report_code": report_code,
            },
        )
    period = _period_report_dates(period_code)
    if period is None:
        return _period_report_invalid("Некорректно выбран период.")
    return _period_report_confirmation(
        market_code=market_code,
        report_code=report_code,
        date_from=period[0],
        date_to=period[1],
    )


def _period_report_custom_input(message: str, *, state: dict[str, Any]) -> TelegramCommandResult:
    value = _parse_owner_date(message)
    market_code = str(state.get("market_code") or "")
    report_code = str(state.get("report_code") or "")
    if market_code not in {"o", "w"} or report_code not in {"s", "f", "a"}:
        return _period_report_invalid("Состояние выбора периода потеряно. Запустите отчёт заново.")
    if value is None:
        return TelegramCommandResult(
            command="/period-report",
            ok=False,
            mode="input",
            blocked_reason="invalid_period_report_date",
            text="Дата не распознана. Введите её как `ДД.ММ.ГГГГ` или `ГГГГ-ММ-ДД`.",
            reply_markup={"inline_keyboard": [[{"text": "Отменить", "callback_data": "mpr_cancel"}]]},
            conversation_state=state,
        )
    if value > date.today():
        return TelegramCommandResult(
            command="/period-report",
            ok=False,
            mode="input",
            blocked_reason="future_period_report_date",
            text="Будущую дату выбрать нельзя. Введите дату не позднее сегодняшней.",
            reply_markup={"inline_keyboard": [[{"text": "Отменить", "callback_data": "mpr_cancel"}]]},
            conversation_state=state,
        )
    if state.get("stage") == "period_report_custom_from":
        return TelegramCommandResult(
            command="/period-report",
            ok=True,
            mode="input",
            text=(
                f"Дата начала: `{value.strftime('%d.%m.%Y')}`.\n\n"
                "Введите дату окончания в формате `ДД.ММ.ГГГГ` или `ГГГГ-ММ-ДД`."
            ),
            reply_markup={"inline_keyboard": [[{"text": "Отменить", "callback_data": "mpr_cancel"}]]},
            conversation_state={
                "stage": "period_report_custom_to",
                "market_code": market_code,
                "report_code": report_code,
                "date_from": value.isoformat(),
            },
        )
    date_from = _parse_owner_date(str(state.get("date_from") or ""))
    if date_from is None or value < date_from:
        return TelegramCommandResult(
            command="/period-report",
            ok=False,
            mode="input",
            blocked_reason="invalid_period_report_range",
            text="Дата окончания не может быть раньше даты начала. Введите дату окончания ещё раз.",
            reply_markup={"inline_keyboard": [[{"text": "Отменить", "callback_data": "mpr_cancel"}]]},
            conversation_state=state,
        )
    return _period_report_confirmation(
        market_code=market_code,
        report_code=report_code,
        date_from=date_from,
        date_to=value,
    )


def _period_report_confirmation(
    *,
    market_code: str,
    report_code: str,
    date_from: date,
    date_to: date,
) -> TelegramCommandResult:
    marketplace = "Ozon" if market_code == "o" else "Wildberries"
    return TelegramCommandResult(
        command="/period-report",
        ok=True,
        mode="review",
        text=(
            "Проверьте параметры отчёта:\n\n"
            f"- маркетплейс: `{marketplace}`;\n"
            f"- вид: `{_period_report_label(report_code)}`;\n"
            f"- период: `{date_from.strftime('%d.%m.%Y')} - {date_to.strftime('%d.%m.%Y')}`.\n\n"
            "После подтверждения бот прочитает данные маркетплейса и сформирует отчёт. "
            "Изменений в кабинете не будет."
        ),
        reply_markup={
            "inline_keyboard": [
                [{
                    "text": "Сформировать отчёт",
                    "callback_data": f"mpr_run:{market_code}:{report_code}:{date_from.isoformat()}:{date_to.isoformat()}",
                }],
                [{"text": "Отменить", "callback_data": "mpr_cancel"}],
            ]
        },
    )


def _period_report_run_callback(
    data: str,
    *,
    data_dir: Path,
    credentials: AppCredentials | None,
) -> TelegramCommandResult:
    parts = data.split(":")
    if len(parts) != 5 or parts[1] not in {"o", "w"} or parts[2] not in {"s", "f", "a"}:
        return _period_report_invalid("Параметры подтверждения повреждены. Запустите отчёт заново.")
    market_code, report_code = parts[1], parts[2]
    date_from = _parse_owner_date(parts[3])
    date_to = _parse_owner_date(parts[4])
    if date_from is None or date_to is None or date_from > date_to or date_to > date.today():
        return _period_report_invalid("Период подтверждения некорректен. Запустите отчёт заново.")
    marketplace = "ozon" if market_code == "o" else "wb"
    report_type = {"s": "short", "f": "financial", "a": "full"}[report_code]
    result = WorkflowRunner(data_dir=data_dir, credentials=credentials).run_read_only(
        "marketplace-period-report",
        inputs={
            "marketplace": marketplace,
            "report_type": report_type,
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
        },
    )
    title = "Ozon" if marketplace == "ozon" else "Wildberries"
    if not result.ok:
        return TelegramCommandResult(
            command="/period-report",
            ok=False,
            blocked_reason=result.blocked_reason or "period_report_failed",
            text=(
                f"{title}: отчёт за период\n\n"
                "Отчёт не сформирован.\n\n"
                f"Причина: `{result.error or result.status}`\n\n"
                "Изменений в кабинете не выполнялось."
            ),
        )
    metrics = result.summary.get("metrics") if isinstance(result.summary.get("metrics"), dict) else {}
    warnings = result.summary.get("warnings") if isinstance(result.summary.get("warnings"), list) else []
    lines = [
        f"{title}: отчёт за период",
        "",
        f"Период: `{date_from.strftime('%d.%m.%Y')} - {date_to.strftime('%d.%m.%Y')}`.",
        f"Вид: `{_period_report_label(report_code)}`.",
        "",
        "Основные показатели:",
        f"- заказы: `{_int(metrics.get('orders'))}` товаров на `{_money(metrics.get('order_amount'))}`;",
        f"- выкупы: `{_int(metrics.get('buyout_units'))}` товаров / `{_int(metrics.get('physical_pieces'))}` физических изделий;",
        f"- возвраты: `{_int(metrics.get('returns'))}`, отмены: `{_int(metrics.get('cancellations'))}`;",
        f"- продажи до расходов: `{_money(metrics.get('gross'))}`;",
        f"- расходы: `{_money(metrics.get('expenses'))}`;",
        f"- к выплате после расходов: `{_money(metrics.get('net'))}`;",
        f"- на одно физическое изделие: `{_money(metrics.get('net_per_piece'))}`.",
    ]
    if warnings:
        lines.extend(["", "Ограничения:"])
        lines.extend(f"- {warning}" for warning in warnings[:5])
    lines.extend(["", f"Run ID: `{result.summary.get('run_id') or 'н/д'}`", "", "Полный файл отчёта приложен. Изменений в кабинете не выполнялось."])
    return TelegramCommandResult(
        command="/period-report",
        ok=True,
        text="\n".join(lines),
        artifacts=result.artifacts,
    )


def _period_report_dates(code: str) -> tuple[date, date] | None:
    today = date.today()
    if code == "y":
        yesterday = today - timedelta(days=1)
        return yesterday, yesterday
    if code == "7":
        return today - timedelta(days=6), today
    if code == "30":
        return today - timedelta(days=29), today
    if code == "m":
        return today.replace(day=1), today
    if code == "p":
        previous_end = today.replace(day=1) - timedelta(days=1)
        return previous_end.replace(day=1), previous_end
    return None


def _parse_owner_date(value: str) -> date | None:
    text = str(value or "").strip()
    for pattern in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def _period_report_label(code: str) -> str:
    return {"s": "Краткий", "f": "Финансовый", "a": "Полный отчёт"}.get(code, "Отчёт")


def _period_report_cancel() -> TelegramCommandResult:
    return TelegramCommandResult(
        command="/period-report",
        ok=True,
        text="Отчёт за период отменён. Данные не запрашивались, изменений в кабинетах не было.",
    )


def _period_report_invalid(reason: str) -> TelegramCommandResult:
    return TelegramCommandResult(
        command="/period-report",
        ok=False,
        blocked_reason="invalid_period_report_parameters",
        text=f"Отчёт за период\n\n{reason}\n\nИзменений в кабинетах не выполнялось.",
    )


def _wb_min_price_actions_start() -> TelegramCommandResult:
    return TelegramCommandResult(
        command="/wb-actions-min-price",
        ok=True,
        mode="input",
        text=(
            "Акции от минимальной цены\n\n"
            "Введите целую скидку от 0 до 99% для товаров, которым не подходит ни одна "
            "активная акция.\n\n"
            "Если скидку не задавать, используется значение по умолчанию `50%`.\n\n"
            "После подтверждения бот построит свежий dry-run. На этом этапе скидки в WB "
            "не изменяются."
        ),
        reply_markup={
            "inline_keyboard": [
                [{"text": "Использовать 50%", "callback_data": "wbmp_review:50"}],
                [{"text": "Отменить", "callback_data": "wbmp_cancel"}],
            ]
        },
        conversation_state={"stage": "wb_min_price_discount_input"},
    )


def _wb_min_price_actions_parameters(message: str) -> TelegramCommandResult:
    value = str(message or "").strip().lower()
    if value in {"", "-", "по умолчанию", "default", "пропустить"}:
        return _wb_min_price_actions_review("50")
    return _wb_min_price_actions_review(value)


def _wb_min_price_actions_review(value: str) -> TelegramCommandResult:
    discount = _parse_wb_outside_discount(value)
    if discount is None:
        return TelegramCommandResult(
            command="/wb-actions-min-price",
            ok=False,
            mode="input",
            blocked_reason="invalid_wb_outside_discount",
            text=(
                "Акции от минимальной цены\n\n"
                "Скидка не распознана. Введите одно целое число от 0 до 99.\n\n"
                "Если хотите использовать значение по умолчанию, нажмите "
                "«Использовать 50%»."
            ),
            reply_markup={
                "inline_keyboard": [
                    [{"text": "Использовать 50%", "callback_data": "wbmp_review:50"}],
                    [{"text": "Отменить", "callback_data": "wbmp_cancel"}],
                ]
            },
            conversation_state={"stage": "wb_min_price_discount_input"},
        )
    return TelegramCommandResult(
        command="/wb-actions-min-price",
        ok=True,
        mode="review",
        text=(
            "Акции от минимальной цены\n\n"
            "Проверьте параметры:\n\n"
            "- акция выбирается только если фактическая цена после скидки WB не ниже "
            "минимальной цены товара;\n"
            "- если подходят несколько акций, выбирается акция с самой высокой ценой;\n"
            f"- скидка для товаров вне подходящих акций: `{discount}%`.\n\n"
            "Подтверждение запустит только свежий расчёт. Изменений в WB пока не будет."
        ),
        reply_markup={
            "inline_keyboard": [
                [
                    {
                        "text": "Подтвердить параметры",
                        "callback_data": f"wbmp_confirm:{discount}",
                    }
                ],
                [{"text": "Изменить скидку", "callback_data": "wbmp_change"}],
                [{"text": "Отменить", "callback_data": "wbmp_cancel"}],
            ]
        },
    )


def _wb_min_price_actions_plan(
    *,
    outside_discount: str,
    data_dir: Path,
    credentials: AppCredentials | None,
) -> TelegramCommandResult:
    discount = _parse_wb_outside_discount(outside_discount)
    if discount is None:
        return TelegramCommandResult(
            command="/wb-actions-min-price",
            ok=False,
            mode="dry_run",
            blocked_reason="invalid_wb_outside_discount",
            text=(
                "Акции от минимальной цены\n\n"
                "Расчёт заблокирован: подтверждённая скидка повреждена.\n\n"
                "Изменений в WB не выполнялось."
            ),
        )
    result = WorkflowRunner(data_dir=data_dir, credentials=credentials).run_task(
        "wb-best-price-action-plan",
        inputs={"outside_discount": discount},
        allowed_modes={"dry_run"},
    )
    if not result.ok:
        return TelegramCommandResult(
            command="/wb-actions-min-price",
            ok=False,
            mode="dry_run",
            blocked_reason=result.blocked_reason or "wb_best_price_plan_failed",
            text=(
                "Акции от минимальной цены\n\n"
                "Свежий dry-run не построен.\n\n"
                f"Причина: `{_safe_error(RuntimeError(result.error or result.status))}`\n\n"
                "Изменений в WB не выполнялось."
            ),
        )
    return _wb_min_price_actions_plan_result(result.summary, artifacts=result.artifacts)


def _wb_min_price_actions_plan_result(
    result: dict[str, Any],
    *,
    artifacts: dict[str, str],
) -> TelegramCommandResult:
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    run_id = str(result.get("run_id") or "")
    discount = _int(result.get("outside_action_discount"))
    safe_to_apply = bool(result.get("safe_to_apply"))
    lines = [
        "Акции от минимальной цены",
        "",
        f"Итог: fresh dry-run построен со статусом `{result.get('overall_status') or 'н/д'}`.",
        f"Run ID: `{run_id or 'н/д'}`",
        f"Скидка вне подходящих акций: `{discount}%`.",
        "",
        "Сейчас:",
        f"- товаров в расчёте: `{_int(summary.get('scope_total'))}`;",
        f"- участвуют в активных акциях: `{_int(summary.get('currently_participating'))}`;",
        f"- предложены хотя бы одной акцией: `{_int(summary.get('offered_any_action'))}`.",
        "",
        "После применения:",
        f"- будут участвовать в лучшей допустимой акции: `{_int(summary.get('eligible_any_action'))}`;",
        f"- останутся вне акций: `{_int(summary.get('outside_action'))}`;",
        f"- скидка изменится: `{_int(summary.get('to_change_discount'))}`;",
        f"- скидка не изменится: `{_int(summary.get('no_change_discount'))}`.",
        "",
        "Проверка ограничений:",
        f"- целевая цена ниже минимума: `{_int(summary.get('target_below_minimum'))}`;",
        f"- небезопасно для одного upload: `{_int(summary.get('unsafe_single_upload'))}`.",
        "",
        "Скидки в WB не изменялись. Полный HTML-отчёт приложен.",
    ]
    markup: dict[str, Any] = {}
    if safe_to_apply and run_id and int(summary.get("to_change_discount") or 0) > 0:
        markup = {
            "inline_keyboard": [
                [{"text": "Отклонить", "callback_data": f"wbmp_reject:{run_id}"}],
            ]
        }
    elif not safe_to_apply:
        lines.extend(
            [
                "",
                "Apply заблокирован: выберите другую скидку и постройте новый dry-run.",
            ]
        )
    else:
        lines.extend(["", "Изменений к применению нет."])
    return TelegramCommandResult(
        command="/wb-actions-min-price",
        ok=True,
        mode="dry_run",
        text="\n".join(lines),
        artifacts=artifacts,
        reply_markup=markup,
    )


def _wb_min_price_actions_apply(
    *,
    plan_run_id: str,
    data_dir: Path,
    credentials: AppCredentials | None,
) -> TelegramCommandResult:
    if not _valid_wb_min_price_plan_id(plan_run_id):
        return TelegramCommandResult(
            command="/wb-actions-min-price",
            ok=False,
            mode="apply",
            blocked_reason="invalid_plan_run_id",
            text=(
                "Акции от минимальной цены\n\n"
                "Apply заблокирован: идентификатор расчёта некорректен.\n\n"
                "Изменений в WB не выполнялось."
            ),
        )
    try:
        result = _run_plan_apply_job(
            task_id="wb-best-price-action-apply",
            plan_run_id=plan_run_id,
            data_dir=data_dir,
            credentials=credentials,
        )
    except Exception as exc:  # noqa: BLE001
        return TelegramCommandResult(
            command="/wb-actions-min-price",
            ok=False,
            mode="apply",
            blocked_reason="wb_best_price_apply_failed",
            text=(
                "Акции от минимальной цены\n\n"
                "Apply не выполнен или остановлен safety-контуром.\n\n"
                f"Причина: `{_safe_error(exc)}`"
            ),
        )
    return TelegramCommandResult(
        command="/wb-actions-min-price",
        ok=str(result.get("overall_status") or "") in {"ok", "warning"},
        mode="apply",
        text=(
            "Акции от минимальной цены\n\n"
            f"Итог: apply завершён со статусом `{result.get('overall_status') or 'н/д'}`.\n"
            f"Run ID: `{result.get('run_id') or 'н/д'}`\n"
            f"Отправлено строк: `{_int(_dict_value(result, 'apply').get('submitted_rows'))}`.\n"
            f"Цены проверены: `{_int(_dict_value(result, 'price_verify').get('matched_rows'))}` / "
            f"`{_int(_dict_value(result, 'price_verify').get('expected_rows'))}`.\n"
            f"Участие в акциях: `{_int(_dict_value(result, 'action_verify').get('confirmed'))}` / "
            f"`{_int(_dict_value(result, 'action_verify').get('target'))}`."
        ),
        artifacts=_safe_artifacts(result),
    )


def _wb_min_price_actions_cancel() -> TelegramCommandResult:
    return TelegramCommandResult(
        command="/wb-actions-min-price",
        ok=True,
        mode="cancelled",
        text="Акции от минимальной цены\n\nОперация отменена. Изменений в WB не выполнялось.",
        reply_markup=WB_MENU_KEYBOARD,
    )


def _wb_min_price_actions_reject(plan_run_id: str) -> TelegramCommandResult:
    if not _valid_wb_min_price_plan_id(plan_run_id):
        return TelegramCommandResult(
            command="/wb-actions-min-price",
            ok=False,
            mode="cancelled",
            blocked_reason="invalid_plan_run_id",
            text="Акции от минимальной цены\n\nРасчёт не отклонён: идентификатор некорректен.",
        )
    return TelegramCommandResult(
        command="/wb-actions-min-price",
        ok=True,
        mode="cancelled",
        text=f"Акции от минимальной цены\n\nРасчёт `{plan_run_id}` отклонён. WB не изменён.",
        reply_markup=WB_MENU_KEYBOARD,
    )


def _wb_manual_actions_start() -> TelegramCommandResult:
    return TelegramCommandResult(
        command="/wb-actions-manual",
        ok=True,
        mode="input",
        text=(
            "Ручная акция\n\n"
            "Введите три целых значения от 0 до 99 одной строкой в таком порядке:\n\n"
            "`порог  скидка_после_порога  скидка_вне_акций`\n\n"
            "Пример: `57 55 55`\n\n"
            "После проверки бот отдельно покажет параметры для подтверждения. "
            "Расчёт и изменение скидок сейчас не выполняются."
        ),
        reply_markup={
            "inline_keyboard": [[{"text": "Отменить", "callback_data": "wbam_cancel"}]]
        },
        conversation_state={"stage": "wb_manual_scheme_input"},
    )


def _wb_manual_actions_parameters(message: str) -> TelegramCommandResult:
    parsed = _parse_wb_manual_parameters(message)
    if parsed is None:
        return TelegramCommandResult(
            command="/wb-actions-manual",
            ok=False,
            mode="input",
            blocked_reason="invalid_wb_manual_parameters",
            text=(
                "Ручная акция\n\n"
                "Параметры не распознаны. Введите ровно три целых значения от 0 до 99:\n\n"
                "`порог  скидка_после_порога  скидка_вне_акций`\n\n"
                "Пример: `57 55 55`\n\n"
                "Изменений в WB не выполнял."
            ),
            reply_markup={
                "inline_keyboard": [[{"text": "Отменить", "callback_data": "wbam_cancel"}]]
            },
            conversation_state={"stage": "wb_manual_scheme_input"},
        )

    threshold, fallback_over_threshold, fallback_no_promo = parsed
    internal_scheme = f"{threshold}-{fallback_no_promo}-{fallback_over_threshold}"
    return TelegramCommandResult(
        command="/wb-actions-manual",
        ok=True,
        mode="review",
        text=(
            "Ручная акция\n\n"
            "Проверьте параметры:\n\n"
            f"- порог акции: `{threshold}%`;\n"
            f"- скидка после превышения порога: `{fallback_over_threshold}%`;\n"
            f"- скидка для товаров вне активных акций: `{fallback_no_promo}%`.\n\n"
            "Подтверждение запустит только свежий расчёт. Скидки в WB на этом этапе не изменятся."
        ),
        reply_markup={
            "inline_keyboard": [
                [
                    {
                        "text": "Подтвердить параметры",
                        "callback_data": f"wbam_confirm:{internal_scheme}",
                    }
                ],
                [{"text": "Отменить", "callback_data": "wbam_cancel"}],
            ]
        },
    )


def _wb_manual_actions_plan(
    *,
    scheme_text: str,
    data_dir: Path,
    credentials: AppCredentials | None,
) -> TelegramCommandResult:
    if _parse_internal_wb_scheme(scheme_text) is None:
        return TelegramCommandResult(
            command="/wb-actions-manual",
            ok=False,
            mode="dry_run",
            blocked_reason="invalid_wb_manual_scheme",
            text=(
                "Ручная акция\n\n"
                "Расчёт заблокирован: параметры подтверждения некорректны.\n\n"
                "Изменений в WB не выполнял. Запустите «Ручную акцию» заново."
            ),
        )
    return _wb_actions_plan_for_scheme(
        scheme_text=scheme_text,
        data_dir=data_dir,
        credentials=credentials,
        manual=True,
    )


def _wb_actions_plan_for_scheme(
    *,
    scheme_text: str,
    data_dir: Path,
    credentials: AppCredentials | None,
    manual: bool,
) -> TelegramCommandResult:
    scheme = _parse_internal_wb_scheme(scheme_text)
    if scheme is None:
        raise ValueError("Invalid WB actions scheme")
    threshold, fallback_no_promo, fallback_over_threshold = scheme
    title = "Ручная акция" if manual else "WB акции 70-55-55"
    command = "/wb-actions-manual" if manual else "/wb-actions"
    try:
        result = run_wb_actions_discount_plan(
            credentials=credentials or load_credentials(),
            data_dir=data_dir,
            scheme_text=scheme_text,
        )
    except Exception as exc:  # noqa: BLE001 - Telegram must return a safe failure.
        return TelegramCommandResult(
            command=command,
            ok=False,
            mode="dry_run",
            blocked_reason="wb_actions_plan_failed",
            text=(
                f"{title}\n\n"
                "Итог: свежий dry-run не удалось построить.\n\n"
                f"Причина: `{_safe_error(exc)}`\n\n"
                "Изменений в Ozon/WB не выполнял."
            ),
        )

    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    run_id = str(result.get("run_id") or "")
    changed_rows = int(summary.get("changed_rows") or summary.get("to_change") or 0)
    report_stats = _wb_actions_report_stats(_safe_artifacts(result).get("csv"))

    eligible_after = _wb_report_stat(report_stats, "eligible_after")
    current_participating = _wb_report_stat(report_stats, "current_participating")
    current_not_participating = _wb_report_stat(report_stats, "current_not_participating")
    offered_in_active_promos = _wb_report_stat(report_stats, "offered_in_active_promos")
    offered_not_participating = _wb_report_stat(report_stats, "offered_not_participating")
    outside_active_promos = _wb_report_stat(report_stats, "outside_active_promos")
    threshold_eligible = _wb_report_stat(report_stats, "threshold_eligible")
    excluded_after = _wb_report_stat(report_stats, "excluded_after")
    not_participating_after = _wb_report_stat(report_stats, "not_participating_after")
    newly_participating_after = _wb_report_stat(report_stats, "newly_participating_after")
    target_distribution = report_stats.get("target_discount_distribution", {})
    upload_distribution = report_stats.get("upload_discount_distribution", {})
    current_distribution = report_stats.get("current_participating_discount_distribution", {})
    after_distribution = report_stats.get("participating_after_discount_distribution", {})
    excluded_reasons = report_stats.get("excluded_reason_distribution", {})
    step_limited = _wb_report_stat(report_stats, "step_limited")

    lines = [
        title,
        "",
        "Свежий расчёт готов. Скидки в WB пока не изменены.",
        "",
        "Параметры:",
        f"- порог акции: `{threshold}%`;",
        f"- скидка после превышения порога: `{fallback_over_threshold}%`;",
        f"- скидка для товаров вне активных акций: `{fallback_no_promo}%`.",
        "",
        "Сейчас:",
        f"- всего товаров в магазине: `{_int(summary.get('total_goods'))}`",
        f"- подходят под доступные активные акции: `{_int(offered_in_active_promos)}`",
        f"- участвуют в акциях: `{_int(current_participating)}`",
        f"- не участвуют в акциях: `{_int(current_not_participating)}`",
        f"- из них доступны акциям, но не участвуют: `{_int(offered_not_participating)}`",
        f"- без доступных активных акций: `{_int(outside_active_promos)}`",
        f"- проходят заданный порог {threshold}%: `{_int(threshold_eligible)}`",
        "",
        "Скидки товаров, которые сейчас участвуют:",
        *_wb_discount_distribution_lines(current_distribution),
        "",
        "Что изменится:",
        f"- исключатся из текущих акций: `{_int(excluded_after)}`",
        *_wb_exclusion_reason_lines(excluded_reasons, threshold=str(threshold)),
        f"- начнут участвовать: `{_int(newly_participating_after)}`",
        "",
        "После применения целевых параметров:",
        f"- останутся или будут участвовать в акциях: `{_int(eligible_after)}`",
        f"- не будут участвовать в акциях: `{_int(not_participating_after)}`",
        f"- скидка изменится: `{_int(changed_rows)}`",
        f"- скидка останется без изменения: `{_int(summary.get('no_change'))}`",
        "",
        "Скидки товаров, которые будут участвовать:",
        *_wb_discount_distribution_lines(after_distribution),
        "",
        "Итоговые скидки всех товаров:",
        *_wb_discount_distribution_lines(target_distribution),
        "",
    ]
    if step_limited:
        lines.extend(
            [
                "После ближайшего безопасного upload:",
                f"- не достигнут цели за один шаг: `{_int(step_limited)}` товаров;",
                *_wb_discount_distribution_lines(upload_distribution),
                "",
            ]
        )
    else:
        lines.extend(["Все целевые скидки достигаются за один upload.", ""])

    lines.extend(
        [
            "Дополнительно:",
            f"- товаров в нескольких активных акциях: `{_int(summary.get('multiple_promos'))}`",
            f"- активных акций: `{_int(summary.get('active_promos'))}`",
            f"- будущих акций: `{_int(summary.get('future_promos'))}`",
            f"- Run ID: `{run_id or 'н/д'}`",
            "",
            "Решение:",
        ]
    )
    if changed_rows:
        lines.extend(
            [
                "В синхронном dry-run write-кнопка не показывается; apply доступен только через runtime approval.",
                "Перед записью apply сам выполнит fresh preflight, fresh dry-run, partial drift-check и verify.",
                "За один upload снижение итоговой цены ограничено `33%`, а изменение скидки - `35 п.п.`; если цель дальше, потребуется следующий подтверждённый запуск.",
                "Если часть строк изменилась, будут применены только неизменившиеся строки; изменившиеся останутся на новый review.",
            ]
        )
    else:
        lines.append("Изменений к применению нет, кнопку apply не показываю.")

    artifacts = _safe_artifacts(result)
    if artifacts.get("report"):
        lines.extend(["", "Полный отчёт приложен к сообщению."])
    lines.extend(["", "Изменений в WB не выполнял."])

    reply_markup: dict[str, Any] = {}
    if changed_rows and run_id and manual:
        reply_markup = {
            "inline_keyboard": [
                [{"text": "Отклонить", "callback_data": f"wbam_reject:{run_id}"}],
            ]
        }
    return TelegramCommandResult(
        command=command,
        ok=True,
        mode="dry_run",
        text="\n".join(lines),
        artifacts=artifacts,
        reply_markup=reply_markup,
    )


def _wb_actions_apply(
    *,
    plan_run_id: str,
    data_dir: Path,
    credentials: AppCredentials | None,
) -> TelegramCommandResult:
    if not _valid_wb_actions_plan_id(plan_run_id):
        return TelegramCommandResult(
            command="/wb_actions_apply",
            ok=False,
            mode="apply",
            blocked_reason="invalid_plan_run_id",
            text=(
                "WB акции apply\n\n"
                "Итог: apply заблокирован - некорректный `plan_run_id`.\n\n"
                f"Получено: `{_truncate(plan_run_id, 100)}`\n\n"
                "Изменений в Ozon/WB не выполнял."
            ),
        )

    try:
        result = _run_plan_apply_job(
            task_id="wb-actions-discount-apply",
            plan_run_id=plan_run_id,
            data_dir=data_dir,
            credentials=credentials,
        )
    except Exception as exc:  # noqa: BLE001 - Telegram must return a safe failure.
        return TelegramCommandResult(
            command="/wb_actions_apply",
            ok=False,
            mode="apply",
            blocked_reason="wb_actions_apply_failed",
            text=(
                "WB акции apply\n\n"
                "Итог: apply не выполнен или остановлен safety-контуром.\n\n"
                f"Plan ID: `{plan_run_id}`\n"
                f"Причина: `{_safe_error(exc)}`\n\n"
                "Если причина в drift или preflight, нужен новый dry-run и новое подтверждение."
            ),
        )

    applied = result.get("applied") if isinstance(result.get("applied"), dict) else {}
    drift = result.get("drift") if isinstance(result.get("drift"), dict) else {}
    verify = result.get("verify") if isinstance(result.get("verify"), dict) else {}
    staged = applied.get("staged") if isinstance(applied.get("staged"), dict) else {}
    error_summary = verify.get("error_summary") if isinstance(verify.get("error_summary"), dict) else {}
    fresh_plan = result.get("fresh_plan") if isinstance(result.get("fresh_plan"), dict) else {}
    latest_history = _wb_latest_history_data(verify)
    lines = [
        "WB акции apply",
        "",
        f"Итог: apply завершен со статусом `{result.get('overall_status') or 'н/д'}`.",
        f"Job ID: `{result.get('job_id') or 'н/д'}`",
        f"Approved plan: `{result.get('approved_plan_run_id') or plan_run_id}`",
        f"Fresh plan: `{fresh_plan.get('run_id') or 'н/д'}`",
        f"Apply run: `{result.get('run_id') or 'н/д'}`",
        f"Схема: `{result.get('scheme') or 'н/д'}`",
        "",
        "Применено:",
        f"- отправлено строк: `{_int(applied.get('payload_rows_count'))}`",
        f"- напрямую: `{_int(applied.get('regular_payload_rows_count'))}`",
        f"- через карантинный fallback: `{_int(applied.get('staged_payload_rows_count'))}`",
        f"- лимит шага скидки: `{_int(result.get('discount_step_limit_pp')) or 35} п.п.`",
        f"- лимит снижения итоговой цены: `{_int(result.get('quarantine_safe_price_drop_percent')) or 33}%`",
        f"- upload ID: `{applied.get('upload_id') or 'н/д'}`",
        f"- пропущено из-за drift: `{_int(drift.get('skipped_due_to_drift_count'))}` строк / `{_int(drift.get('skipped_due_to_drift_product_count'))}` товаров",
        "",
        "Проверка:",
        f"- verify status: `{verify.get('status') or 'н/д'}`",
        f"- regular successful goods: `{_int(latest_history.get('successGoodsNumber'))}` / `{_int(latest_history.get('overAllGoodsNumber'))}`",
        f"- successful rows: `{_int(verify.get('success_rows') or latest_history.get('successGoodsNumber'))}` / `{_int(verify.get('expected_rows') or latest_history.get('overAllGoodsNumber'))}`",
        f"- failed rows: `{_int(verify.get('failed_rows'))}`",
        f"- карантинных отказов: `{_int(error_summary.get('price_quarantine_rows_count'))}`",
    ]
    if staged:
        lines.extend(
            [
                "",
                "Пошаговая скидка:",
                f"- статус: `{staged.get('status') or 'н/д'}`",
                f"- шаг: `{staged.get('stage_discount') or 'н/д'}%`",
                f"- подтверждено на шаге: `{_int(staged.get('confirmed49_rows'))}`",
                f"- доведено до цели: `{_int(staged.get('final_verified_rows'))}` / `{_int(staged.get('target_rows'))}`",
            ]
        )
    if drift.get("skipped_due_to_drift_nm_ids"):
        nm_ids = ", ".join(str(item) for item in drift.get("skipped_due_to_drift_nm_ids", [])[:10])
        lines.extend(["", f"Drift товары на новый review: `{nm_ids}`"])
    artifacts = _safe_artifacts(result)
    if artifacts:
        lines.extend(["", "Файлы:"])
        if artifacts.get("report"):
            lines.append(f"- отчет: `{artifacts['report']}`")
        if artifacts.get("drift_check"):
            lines.append(f"- drift-check: `{artifacts['drift_check']}`")
        if artifacts.get("skipped_drift_rows"):
            lines.append(f"- skipped drift: `{artifacts['skipped_drift_rows']}`")
        if artifacts.get("staged_result"):
            lines.append(f"- staged result: `{artifacts['staged_result']}`")
    return TelegramCommandResult(
        command="/wb_actions_apply",
        ok=str(result.get("overall_status") or "") in {"ok", "warning"},
        mode="apply",
        text="\n".join(lines),
        artifacts=artifacts,
    )


def _ozon_elastic_plan(
    *,
    data_dir: Path,
    credentials: AppCredentials | None,
) -> TelegramCommandResult:
    try:
        result = run_ozon_elastic_plan(
            credentials=credentials or load_credentials(),
            data_dir=data_dir,
        )
    except Exception as exc:  # noqa: BLE001 - Telegram must return a safe failure.
        return TelegramCommandResult(
            command="/elastic",
            ok=False,
            mode="dry_run",
            blocked_reason="ozon_elastic_plan_failed",
            text=(
                "Ozon Elastic\n\n"
                "Итог: свежий dry-run не удалось построить.\n\n"
                f"Причина: `{_safe_error(exc)}`\n\n"
                "Изменений в Ozon/WB не выполнял."
            ),
        )

    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    run_id = str(result.get("run_id") or "")
    has_write_rows = (
        int(summary.get("add_to_action") or 0)
        + int(summary.get("update_action_price_with_changed_price") or 0)
        + int(summary.get("deactivate_from_action") or 0)
    ) > 0
    lines = [
        "Ozon Elastic",
        "",
        "Итог: свежий dry-run построен. Apply не выполнялся.",
        f"Run ID: `{run_id or 'н/д'}`",
        f"Акция: `{summary.get('action_name') or 'н/д'}` / `{summary.get('action_id') or 'н/д'}`",
        "",
        "Сводка:",
        f"- товаров в активной акции: `{_int(summary.get('active_rows'))}`",
        f"- кандидатов: `{_int(summary.get('candidate_rows'))}`",
        f"- уникальных товаров в расчете: `{_int(summary.get('merged_unique_products'))}`",
        f"- добавить в акцию: `{_int(summary.get('add_to_action'))}`",
        f"- обновить цену: `{_int(summary.get('update_action_price'))}`",
        f"- из них с реальным изменением цены: `{_int(summary.get('update_action_price_with_changed_price'))}`",
        f"- снять с акции: `{_int(summary.get('deactivate_from_action'))}`",
        f"- пропустить кандидатов: `{_int(summary.get('skip_candidate'))}`",
        f"- blocked: `{_int(summary.get('blocked'))}`",
        "",
        "Что дальше:",
    ]
    if has_write_rows:
        lines.extend(
            [
                "В синхронном dry-run write-кнопка не показывается; apply доступен только через runtime approval.",
                "Перед записью apply сам выполнит fresh preflight, fresh dry-run, partial drift-check и verify.",
                "Если часть строк изменилась, будут применены только неизменившиеся строки; изменившиеся останутся на новый review.",
            ]
        )
    else:
        lines.append("Изменений к применению нет, кнопку apply не показываю.")

    artifacts = _safe_artifacts(result)
    if artifacts:
        lines.extend(["", "Файлы:"])
        if artifacts.get("report"):
            lines.append(f"- отчет: `{artifacts['report']}`")
        if artifacts.get("xlsx"):
            lines.append(f"- Excel: `{artifacts['xlsx']}`")
        if artifacts.get("csv"):
            lines.append(f"- CSV: `{artifacts['csv']}`")
    lines.extend(["", "Изменений в Ozon/WB не выполнял."])

    return TelegramCommandResult(
        command="/elastic",
        ok=True,
        mode="dry_run",
        text="\n".join(lines),
        artifacts=artifacts,
        reply_markup={},
    )


def _ozon_elastic_apply(
    *,
    plan_run_id: str,
    data_dir: Path,
    credentials: AppCredentials | None,
) -> TelegramCommandResult:
    if not _valid_ozon_elastic_plan_id(plan_run_id):
        return TelegramCommandResult(
            command="/elastic_apply",
            ok=False,
            mode="apply",
            blocked_reason="invalid_plan_run_id",
            text=(
                "Ozon Elastic apply\n\n"
                "Итог: apply заблокирован - некорректный `plan_run_id`.\n\n"
                f"Получено: `{_truncate(plan_run_id, 100)}`\n\n"
                "Изменений в Ozon/WB не выполнял."
            ),
        )

    try:
        result = _run_plan_apply_job(
            task_id="ozon-elastic-apply",
            plan_run_id=plan_run_id,
            data_dir=data_dir,
            credentials=credentials,
        )
    except Exception as exc:  # noqa: BLE001 - Telegram must return a safe failure.
        return TelegramCommandResult(
            command="/elastic_apply",
            ok=False,
            mode="apply",
            blocked_reason="ozon_elastic_apply_failed",
            text=(
                "Ozon Elastic apply\n\n"
                "Итог: apply не выполнен или остановлен safety-контуром.\n\n"
                f"Plan ID: `{plan_run_id}`\n"
                f"Причина: `{_safe_error(exc)}`\n\n"
                "Если причина в drift или preflight, нужен новый dry-run и новое подтверждение."
            ),
        )

    applied = result.get("applied") if isinstance(result.get("applied"), dict) else {}
    drift = result.get("drift") if isinstance(result.get("drift"), dict) else {}
    verify = result.get("verify") if isinstance(result.get("verify"), dict) else {}
    fresh_plan = result.get("fresh_plan") if isinstance(result.get("fresh_plan"), dict) else {}
    lines = [
        "Ozon Elastic apply",
        "",
        f"Итог: apply завершен со статусом `{result.get('overall_status') or 'н/д'}`.",
        f"Job ID: `{result.get('job_id') or 'н/д'}`",
        f"Approved plan: `{result.get('approved_plan_run_id') or plan_run_id}`",
        f"Fresh plan: `{fresh_plan.get('run_id') or 'н/д'}`",
        f"Apply run: `{result.get('run_id') or 'н/д'}`",
        "",
        "Применено:",
        f"- добавить/обновить: `{_int(applied.get('activate_rows_count'))}`",
        f"- снять с акции: `{_int(applied.get('deactivate_rows_count'))}`",
        f"- пропущено из-за drift: `{_int(drift.get('skipped_due_to_drift_count'))}` строк / `{_int(drift.get('skipped_due_to_drift_product_count'))}` товаров",
        "",
        "Проверка:",
        f"- verify status: `{verify.get('status') or 'н/д'}`",
        f"- расхождения цен: `{_int(len(verify.get('price_mismatches') or []))}`",
        f"- остались активными после снятия: `{_int(len(verify.get('still_active_deactivated') or []))}`",
    ]
    if drift.get("skipped_due_to_drift_product_ids"):
        product_ids = ", ".join(str(item) for item in drift.get("skipped_due_to_drift_product_ids", [])[:10])
        lines.extend(["", f"Drift товары на новый review: `{product_ids}`"])
    artifacts = _safe_artifacts(result)
    if artifacts:
        lines.extend(["", "Файлы:"])
        if artifacts.get("report"):
            lines.append(f"- отчет: `{artifacts['report']}`")
        if artifacts.get("drift_check"):
            lines.append(f"- drift-check: `{artifacts['drift_check']}`")
        if artifacts.get("skipped_drift_rows"):
            lines.append(f"- skipped drift: `{artifacts['skipped_drift_rows']}`")
    return TelegramCommandResult(
        command="/elastic_apply",
        ok=str(result.get("overall_status") or "") in {"ok", "warning"},
        mode="apply",
        text="\n".join(lines),
        artifacts=artifacts,
    )


def _ozon_actions_plan(
    *,
    data_dir: Path,
    credentials: AppCredentials | None,
) -> TelegramCommandResult:
    try:
        result = run_ozon_actions_optimizer_plan(
            credentials=credentials or load_credentials(),
            data_dir=data_dir,
        )
    except Exception as exc:  # noqa: BLE001 - Telegram must return a safe failure.
        return TelegramCommandResult(
            command="/ozon-actions",
            ok=False,
            mode="dry_run",
            blocked_reason="ozon_actions_plan_failed",
            text=(
                "Ozon все акции\n\n"
                "Итог: свежий dry-run не удалось построить.\n\n"
                f"Причина: `{_safe_error(exc)}`\n\n"
                "Изменений в Ozon/WB не выполнял."
            ),
        )

    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    run_id = str(result.get("run_id") or "")
    apply_rows = (
        int(summary.get("recommended_add") or 0)
        + int(summary.get("recommended_update") or 0)
        + int(summary.get("recommended_switch_review") or 0)
    )
    lines = [
        "Ozon все акции",
        "",
        "Итог: свежий dry-run построен. Apply не выполнялся.",
        f"Run ID: `{run_id or 'н/д'}`",
        "",
        "Сводка:",
        f"- доступных акций: `{_int(summary.get('actions_total'))}`",
        f"- акций с товарами: `{_int(summary.get('actions_with_rows'))}`",
        f"- товаров в расчете: `{_int(summary.get('products_with_action_offers'))}`",
        f"- предложений всего: `{_int(summary.get('offers_total'))}`",
        f"- валидных предложений: `{_int(summary.get('valid_offers'))}`",
        f"- заблокированных предложений: `{_int(summary.get('blocked_offers'))}`",
        f"- LK-акций с числовым бустингом: `{_int(summary.get('lk_boost_actions_with_numeric_boost'))}`",
        "",
        "Рекомендации:",
        f"- оставить текущую акцию: `{_int(summary.get('recommended_keep'))}`",
        f"- добавить в лучшую акцию: `{_int(summary.get('recommended_add'))}`",
        f"- обновить цену в текущей акции: `{_int(summary.get('recommended_update'))}`",
        f"- переключить на другую акцию: `{_int(summary.get('recommended_switch_review'))}`",
        f"- пропустить: `{_int(summary.get('recommended_skip'))}`",
        "",
        "Логика выбора:",
        "- цена акции не ниже минимальной цены, есть FBO-остаток и подтвержденный бустинг;",
        "- новая акция выбирается только если дает больший бустинг или сопоставимый бустинг при цене не хуже;",
        "- спорные варианты с меньшей ценой остаются на review/test, не применяются молча.",
        "",
        "Что дальше:",
    ]
    if apply_rows:
        lines.extend(
            [
                "В синхронном dry-run write-кнопка не показывается; apply доступен только через runtime approval.",
                "Перед записью apply сам выполнит fresh preflight, fresh dry-run, partial drift-check и verify.",
                "Если часть строк изменилась, будут применены только неизменившиеся строки; изменившиеся останутся на новый review.",
            ]
        )
    else:
        lines.append("Изменений к применению нет, кнопку apply не показываю.")

    artifacts = _safe_artifacts(result)
    if artifacts:
        lines.extend(["", "Файлы:"])
        if artifacts.get("report"):
            lines.append(f"- отчет: `{artifacts['report']}`")
        if artifacts.get("xlsx"):
            lines.append(f"- Excel: `{artifacts['xlsx']}`")
        if artifacts.get("recommendations_csv"):
            lines.append(f"- рекомендации CSV: `{artifacts['recommendations_csv']}`")
    lines.extend(["", "Изменений в Ozon/WB не выполнял."])

    return TelegramCommandResult(
        command="/ozon-actions",
        ok=True,
        mode="dry_run",
        text="\n".join(lines),
        artifacts=artifacts,
        reply_markup={},
    )


def _ozon_actions_apply(
    *,
    plan_run_id: str,
    data_dir: Path,
    credentials: AppCredentials | None,
) -> TelegramCommandResult:
    if not _valid_ozon_actions_plan_id(plan_run_id):
        return TelegramCommandResult(
            command="/ozon_actions_apply",
            ok=False,
            mode="apply",
            blocked_reason="invalid_plan_run_id",
            text=(
                "Ozon все акции apply\n\n"
                "Итог: apply заблокирован - некорректный `plan_run_id`.\n\n"
                f"Получено: `{_truncate(plan_run_id, 100)}`\n\n"
                "Изменений в Ozon/WB не выполнял."
            ),
        )

    try:
        result = _run_plan_apply_job(
            task_id="ozon-actions-optimizer-apply",
            plan_run_id=plan_run_id,
            data_dir=data_dir,
            credentials=credentials,
        )
    except Exception as exc:  # noqa: BLE001 - Telegram must return a safe failure.
        return TelegramCommandResult(
            command="/ozon_actions_apply",
            ok=False,
            mode="apply",
            blocked_reason="ozon_actions_apply_failed",
            text=(
                "Ozon все акции apply\n\n"
                "Итог: apply не выполнен или остановлен safety-контуром.\n\n"
                f"Plan ID: `{plan_run_id}`\n"
                f"Причина: `{_safe_error(exc)}`\n\n"
                "Если причина в drift или preflight, нужен новый dry-run и новое подтверждение."
            ),
        )

    applied = result.get("applied") if isinstance(result.get("applied"), dict) else {}
    drift = result.get("drift") if isinstance(result.get("drift"), dict) else {}
    verify = result.get("verify") if isinstance(result.get("verify"), dict) else {}
    fresh_plan = result.get("fresh_plan") if isinstance(result.get("fresh_plan"), dict) else {}
    lines = [
        "Ozon все акции apply",
        "",
        f"Итог: apply завершен со статусом `{result.get('overall_status') or 'н/д'}`.",
        f"Approved plan: `{result.get('approved_plan_run_id') or plan_run_id}`",
        f"Fresh plan: `{fresh_plan.get('run_id') or 'н/д'}`",
        f"Apply run: `{result.get('run_id') or 'н/д'}`",
        "",
        "Применено:",
        f"- добавить/обновить/целевая акция: `{_int(applied.get('activate_rows_count'))}`",
        f"- переключений: `{_int(applied.get('switch_rows_count'))}`",
        f"- снятий из исходной акции: `{_int(applied.get('deactivate_rows_count'))}`",
        f"- отклонено Ozon: `{_int(applied.get('rejected_count'))}`",
        f"- пропущено из-за drift: `{_int(drift.get('skipped_due_to_drift_count'))}` строк / `{_int(drift.get('skipped_due_to_drift_product_count'))}` товаров",
        "",
        "Проверка:",
        f"- verify status: `{verify.get('status') or 'н/д'}`",
        f"- расхождения: `{_int(len(verify.get('mismatches') or []))}`",
    ]
    if drift.get("skipped_due_to_drift_product_ids"):
        product_ids = ", ".join(str(item) for item in drift.get("skipped_due_to_drift_product_ids", [])[:10])
        lines.extend(["", f"Drift товары на новый review: `{product_ids}`"])
    artifacts = _safe_artifacts(result)
    if artifacts:
        lines.extend(["", "Файлы:"])
        if artifacts.get("report"):
            lines.append(f"- отчет: `{artifacts['report']}`")
        if artifacts.get("drift_check"):
            lines.append(f"- drift-check: `{artifacts['drift_check']}`")
        if artifacts.get("skipped_drift_rows"):
            lines.append(f"- skipped drift: `{artifacts['skipped_drift_rows']}`")
    return TelegramCommandResult(
        command="/ozon_actions_apply",
        ok=str(result.get("overall_status") or "") in {"ok", "warning"},
        mode="apply",
        text="\n".join(lines),
        artifacts=artifacts,
    )


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
    unified_catalog = _dict_value(result, "unified_catalog")

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
        (
            f"- WB: создано заказов `{_int(wb_orders_day.get('total_orders', wb_orders_day.get('active_orders')))}` шт. / "
            f"`{_money(wb_orders_day.get('amount'))}`, из них активных `{_int(wb_orders_day.get('active_orders'))}` шт. / "
            f"`{_money(wb_orders_day.get('active_amount'))}`, отмен `{_int(wb_orders_day.get('cancelled_orders'))}`; "
            f"выкупы `{_int(wb_sales_day.get('sales_rows'))}` шт. / `{_money(wb_sales_day.get('sales_amount'))}`; "
            f"текущие расходы `{_money(wb_expenses.get('total_expenses'))}`; "
            f"корректировки/зачисления `{_money(wb_expenses.get('total_credits_and_adjustments'))}`."
        ),
        "",
        "Отзывы и вопросы:",
        f"- Ozon требуют внимания: отзывы `{_int(ozon_comm.get('unanswered_feedbacks'))}`, вопросы `{_int(ozon_comm.get('unanswered_questions'))}`.",
        f"- WB требуют внимания: отзывы `{_int(wb_comm.get('unanswered_feedbacks'))}`, вопросы `{_int(wb_comm.get('unanswered_questions'))}`.",
        "",
        "Остатки:",
        f"- Ozon: всего `{_int(ozon_stocks.get('present_total'))}` шт., нулевой остаток `{_int(ozon_stocks.get('out_of_stock_count'))}` товаров.",
        f"- WB: всего `{_int(wb_stocks.get('quantity_total'))}` шт., нулевой остаток `{_int(wb_stocks.get('zero_stock_count'))}` товаров.",
        "",
        "Каталог:",
        f"- Unified: товаров `{_int(unified_catalog.get('products'))}`, связанных Ozon+WB `{_int(unified_catalog.get('both_marketplaces_products'))}`, только Ozon `{_int(unified_catalog.get('active_ozon_only_products'))}`, только WB `{_int(unified_catalog.get('active_wb_only_products'))}`.",
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


def _wb_analytics_chat_text(result: dict[str, Any]) -> str:
    source = _dict_value(result, "source")
    metrics = _dict_value(result, "metrics")
    quality = _dict_value(result, "run_quality")
    lines = [
        "WB аналитика",
        "",
        f"Итог: свежая read-only аналитика построена, статус `{result.get('overall_status') or 'н/д'}`.",
        f"Run ID: `{result.get('run_id') or 'н/д'}`",
        f"Период parser warehouse: `{source.get('min_run_date') or 'н/д'}` - `{source.get('max_run_date') or 'н/д'}`.",
        f"Сборка UTC: `{source.get('built_at_utc') or 'н/д'}`.",
        "",
        "Видимость Vital Shevron:",
        f"- строк видимости: `{_int(metrics.get('visible_rows'))}`",
        f"- товаров в выдаче: `{_int(metrics.get('unique_products'))}`",
        f"- запросов с нашими товарами: `{_int(metrics.get('unique_queries'))}`",
        f"- лучшая позиция: `{_int(metrics.get('best_position'))}`",
        f"- top-10 / top-30 / top-100: `{_int(metrics.get('top10_rows'))}` / `{_int(metrics.get('top30_rows'))}` / `{_int(metrics.get('top100_rows'))}`",
        f"- видимых строк с остатком: `{_int(metrics.get('stock_visible_rows'))}`",
        f"- видимых строк без остатка: `{_int(metrics.get('zero_stock_visible_rows'))}`",
        "",
        "Динамика:",
        f"- daily changes строк: `{_int(metrics.get('daily_change_rows'))}`",
        f"- улучшений / ухудшений: `{_int(metrics.get('improved_rows'))}` / `{_int(metrics.get('declined_rows'))}`",
        f"- выпало из выдачи: `{_int(metrics.get('missing_rows'))}`",
        f"- кандидатов с остатком вне top-30: `{_int(metrics.get('weak_visible_candidates'))}`",
        "",
        "Качество данных:",
        f"- latest run quality: `{quality.get('latest_status') or 'н/д'}`",
        "",
        "Что дальше:",
        "- использовать кандидатов с остатком вне top-30 для очереди SEO/карточек;",
        "- соединять parser-видимость с продажами, остатками, акциями и ставками;",
        "- не трактовать позиции parser как продажи.",
    ]
    artifacts = _safe_artifacts(result)
    if artifacts:
        lines.extend(["", "Файлы:"])
        if artifacts.get("report"):
            lines.append(f"- отчет: `{artifacts['report']}`")
        if artifacts.get("weak_candidates_csv"):
            lines.append(f"- кандидаты SEO: `{artifacts['weak_candidates_csv']}`")
        if artifacts.get("summary"):
            lines.append(f"- summary: `{artifacts['summary']}`")
    lines.extend(["", "Изменений в WB не выполнял."])
    return "\n".join(lines)


def _ozon_stock_supplies_chat_text(result: dict[str, Any]) -> str:
    metrics = _dict_value(result, "metrics")
    general = _dict_value(metrics, "general_fbo")
    warehouses = _dict_value(metrics, "warehouses")
    supplies = _dict_value(metrics, "supplies")
    reconciliation = _dict_value(metrics, "reconciliation")
    lines = [
        "Остатки и поставки Ozon",
        "",
        f"Итог: свежий read-only отчет построен, статус `{result.get('overall_status') or 'н/д'}`.",
        f"Run ID: `{result.get('run_id') or 'н/д'}`",
        "",
        "FBO-остатки:",
        f"- общий present: `{_int(general.get('present'))}` товаров / `{_int(general.get('physical_present'))}` физических изделий;",
        f"- свободно по складам: `{_int(warehouses.get('free_to_sell'))}` товаров / `{_int(warehouses.get('physical_free_to_sell'))}` физических изделий;",
        f"- зарезервировано: `{_int(general.get('reserved'))}`;",
        f"- обещано по складскому отчету: `{_int(warehouses.get('promised'))}`;",
        f"- складов: `{_int(metrics.get('warehouse_count'))}`;",
        f"- расхождение general/warehouse: `{_int(reconciliation.get('difference'))}`.",
        "",
        "Активные заявки и поставки:",
        f"- заявок: `{_int(supplies.get('orders'))}`, поставок: `{_int(supplies.get('supplies'))}`;",
        f"- состав активного контура: `{_int(supplies.get('quantity'))}` товаров / `{_int(supplies.get('physical_quantity'))}` физических изделий;",
        f"- подтвержденный inbound: `{_int(supplies.get('confirmed_inbound_quantity'))}` товаров / `{_int(supplies.get('confirmed_inbound_physical'))}` физических изделий;",
        f"- виртуальных заявок-дублей исключено: `{_int(supplies.get('virtual_orders'))}`.",
    ]
    active = result.get("active_supplies") if isinstance(result.get("active_supplies"), list) else []
    if active:
        lines.extend(["", "По поставкам:"])
        for row in active[:10]:
            lines.append(
                f"- `{row.get('order_number') or row.get('order_id') or 'н/д'}`: "
                f"{row.get('state_label') or row.get('state') or 'статус не указан'}, "
                f"{row.get('storage_warehouse') or 'склад не указан'}, `{_int(row.get('quantity'))}` ед."
            )
        if len(active) > 10:
            lines.append(f"- ... еще `{len(active) - 10}` поставок в полном отчете")
    warnings = result.get("warnings") if isinstance(result.get("warnings"), list) else []
    if warnings:
        lines.extend(["", "Ограничения данных:"])
        lines.extend(f"- {warning}" for warning in warnings[:5])
    lines.extend(
        [
            "",
            "Важно: общий и складской остатки не складываются; активные поставки также показаны отдельно.",
            "Изменений в Ozon не выполнял.",
        ]
    )
    return "\n".join(lines)


def _wb_stock_supplies_chat_text(result: dict[str, Any]) -> str:
    metrics = _dict_value(result, "metrics")
    stocks = _dict_value(metrics, "stocks")
    supplies = _dict_value(metrics, "supplies")
    by_status = _dict_value(supplies, "by_status")
    lines = [
        "Остатки и поставки WB",
        "",
        f"Итог: свежий read-only отчет построен, статус `{result.get('overall_status') or 'н/д'}`.",
        f"Run ID: `{result.get('run_id') or 'н/д'}`",
        "",
        "Остатки на складах:",
        f"- доступно: `{_int(stocks.get('quantity'))}` товарных единиц / `{_int(stocks.get('physical_quantity'))}` физических изделий;",
        f"- в пути к покупателю: `{_int(stocks.get('in_way_to_client'))}`;",
        f"- поле inWayFromClient: `{_int(stocks.get('in_way_from_client'))}`; это не считается возвратами без сверки;",
        f"- складов в отчете: `{_int(metrics.get('warehouse_count'))}`.",
        "",
        "Активные поставки:",
        f"- всего: `{_int(supplies.get('count'))}` поставок, `{_int(supplies.get('quantity'))}` товарных единиц / `{_int(supplies.get('physical_quantity'))}` физических изделий;",
    ]
    for status_id in (1, 2, 3, 6, 4):
        row = _dict_value(by_status, str(status_id))
        lines.append(
            f"- {row.get('status') or f'Статус {status_id}'}: поставок `{_int(row.get('count'))}` / "
            f"`{_int(row.get('quantity'))}` ед."
        )

    active = result.get("active_supplies") if isinstance(result.get("active_supplies"), list) else []
    if active:
        lines.extend(["", "По поставкам:"])
        for row in active[:10]:
            lines.append(
                f"- `{row.get('supply_id') or 'н/д'}`: {row.get('warehouse_name') or 'склад не указан'}, "
                f"{row.get('status') or 'статус не указан'}, `{_int(row.get('quantity'))}` ед., "
                f"дата `{row.get('supply_date') or 'не указана'}`"
            )
        if len(active) > 10:
            lines.append(f"- ... еще `{len(active) - 10}` поставок в полном отчете")

    anomalies = result.get("anomalies") if isinstance(result.get("anomalies"), list) else []
    lines.extend(["", "Требует внимания:"])
    if anomalies:
        lines.extend(f"- {row.get('message') or 'Предупреждение без описания'}" for row in anomalies[:4])
        if len(anomalies) > 4:
            lines.append(f"- ... еще `{len(anomalies) - 4}` предупреждений в отчете")
    else:
        lines.append("- резких аномалий по доступным снимкам не обнаружено")

    warnings = result.get("warnings") if isinstance(result.get("warnings"), list) else []
    quality_warnings = [
        str(item)
        for item in warnings
        if str(item).strip() and "предупреждений по состояниям" not in str(item)
    ]
    if quality_warnings:
        lines.extend(["", "Ограничения данных:"])
        lines.extend(f"- {item}" for item in quality_warnings[:4])

    artifacts = _safe_artifacts(result)
    if artifacts:
        lines.extend(["", "Файлы:"])
        if artifacts.get("report"):
            lines.append(f"- отчет: `{artifacts['report']}`")
        if artifacts.get("warehouses_csv"):
            lines.append(f"- склады: `{artifacts['warehouses_csv']}`")
        if artifacts.get("supplies_csv"):
            lines.append(f"- поставки: `{artifacts['supplies_csv']}`")
    lines.extend(
        [
            "",
            "Важно: активные поставки не прибавляются к доступному остатку, чтобы не задвоить товар.",
            "Изменений в WB не выполнял.",
        ]
    )
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
    if task == "catalog-build-unified":
        catalog_summary = _catalog_build_summary(run)
        if catalog_summary:
            lines.extend(
                [
                    f"- товаров в unified catalog: `{_int(catalog_summary.get('unified_products'))}`",
                    f"- связанных Ozon+WB: `{_int(catalog_summary.get('confirmed_products'))}`",
                    f"- только Ozon: `{_int(catalog_summary.get('ozon_only_products'))}`",
                    f"- только WB: `{_int(catalog_summary.get('wb_only_products'))}`",
                    f"- замечаний сборки: `{_int(catalog_summary.get('issue_count'))}`",
                ]
            )
    lines.extend(["", "Следующий шаг:", next_step])
    if artifacts:
        lines.extend(["", "Файлы:"])
        for key, value in sorted(artifacts.items()):
            lines.append(f"- `{key}`: `{value}`")
    return TelegramCommandResult(command=command, ok=True, text="\n".join(lines), artifacts=artifacts)


def _catalog_build_summary(run: dict[str, Any]) -> dict[str, Any]:
    artifacts = _safe_artifacts(run)
    summary_path = artifacts.get("summary")
    if not summary_path:
        return {}
    try:
        summary = json.loads(Path(summary_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(summary, dict):
        return {}
    catalog_summary = summary.get("summary")
    return catalog_summary if isinstance(catalog_summary, dict) else {}


def _catalog_search(query: str, *, data_dir: Path) -> TelegramCommandResult:
    products_path = data_dir / "catalog" / "unified" / "products.json"
    products = _read_json_list(products_path)
    if not products:
        return TelegramCommandResult(
            command="/catalog",
            ok=False,
            blocked_reason="unified_catalog_missing",
            text=(
                "Каталог\n\n"
                "Итог: я не могу это подтвердить - unified catalog не найден или пуст.\n\n"
                f"Источник: `{products_path}`\n\n"
                "Следующий шаг:\n"
                "Запустить read-only `build-unified-catalog`, затем повторить `/catalog <запрос>`.\n\n"
                "Изменений в Ozon/WB не выполнял."
            ),
        )

    ozon_by_offer = _index_csv(data_dir / "catalog" / "ozon" / "processed" / "ozon_catalog.csv", "offer_id")
    wb_by_vendor = _index_csv(data_dir / "catalog" / "wb" / "processed" / "wb_catalog.csv", "vendor_code")
    matches = _catalog_matches(
        query=query,
        products=[_enrich_catalog_row(row, ozon_by_offer=ozon_by_offer, wb_by_vendor=wb_by_vendor) for row in products],
    )

    if not matches:
        return TelegramCommandResult(
            command="/catalog",
            ok=False,
            blocked_reason="catalog_item_not_found",
            text=(
                "Каталог\n\n"
                f"Итог: по запросу `{query}` товар в unified catalog не найден.\n\n"
                "Где искал: internal_sku, internal_product_id, название, Ozon offer_id/product_id/sku/barcode, "
                "WB vendorCode/nmID/barcode.\n\n"
                "Следующий шаг:\n"
                "Проверить написание артикула или обновить read-only `build-unified-catalog`.\n\n"
                "Изменений в Ozon/WB не выполнял."
            ),
            artifacts={"products_json": str(products_path)},
        )

    if len(matches) == 1:
        text = _catalog_product_card(query=query, row=matches[0]["row"], products_path=products_path)
    else:
        text = _catalog_search_results(query=query, matches=matches, products_path=products_path)
    return TelegramCommandResult(
        command="/catalog",
        ok=True,
        text=text,
        artifacts={"products_json": str(products_path)},
    )


def _read_json_list(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


def _index_csv(path: Path, key: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                value = str(row.get(key) or "").strip()
                if value and value not in result:
                    result[value] = {str(k): str(v or "").strip() for k, v in row.items()}
    except (OSError, csv.Error):
        return {}
    return result


def _enrich_catalog_row(
    row: dict[str, Any],
    *,
    ozon_by_offer: dict[str, dict[str, str]],
    wb_by_vendor: dict[str, dict[str, str]],
) -> dict[str, Any]:
    enriched = dict(row)
    ozon = ozon_by_offer.get(str(row.get("ozon_offer_id") or "").strip(), {})
    wb = wb_by_vendor.get(str(row.get("wb_vendor_code") or "").strip(), {})
    if ozon:
        enriched["ozon_barcode"] = ozon.get("barcode", "")
        enriched["ozon_status"] = ozon.get("status", "")
    if wb:
        enriched["wb_barcode"] = wb.get("barcode", "")
        enriched["wb_status"] = wb.get("status", "")
        enriched["wb_subject"] = wb.get("subject", "")
    return enriched


def _catalog_matches(query: str, products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized_query = _normalize_search_text(query)
    if not normalized_query:
        return []
    rows: list[dict[str, Any]] = []
    for row in products:
        score = _catalog_match_score(normalized_query, row)
        if score <= 0:
            continue
        rows.append({"score": score, "row": row})
    return sorted(
        rows,
        key=lambda item: (
            -int(item["score"]),
            str(item["row"].get("internal_sku") or item["row"].get("internal_product_id") or ""),
        ),
    )


def _catalog_match_score(query: str, row: dict[str, Any]) -> int:
    exact_fields = (
        "internal_sku",
        "internal_product_id",
        "ozon_offer_id",
        "ozon_product_id",
        "ozon_sku",
        "ozon_barcode",
        "wb_vendor_code",
        "wb_nm_id",
        "wb_barcode",
    )
    for field in exact_fields:
        value = _normalize_search_text(row.get(field))
        if value and value == query:
            return 100
    for field in exact_fields:
        value = _normalize_search_text(row.get(field))
        if value and value.startswith(query):
            return 85
    title = _normalize_search_text(row.get("product_name"))
    if title == query:
        return 80
    if title and query in title:
        return 60
    for field in exact_fields:
        value = _normalize_search_text(row.get(field))
        if value and query in value:
            return 50
    return 0


def _normalize_search_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().replace("ё", "е").split())


def _catalog_product_card(*, query: str, row: dict[str, Any], products_path: Path) -> str:
    title = str(row.get("product_name") or "без названия").strip()
    internal_sku = str(row.get("internal_sku") or row.get("internal_product_id") or "нет").strip()
    notes = _truncate(str(row.get("notes") or "").strip(), 180)
    lines = [
        "Каталог",
        "",
        f"Итог: по запросу `{query}` найден 1 товар.",
        "",
        f"Название: {title}",
        f"Внутренний артикул: `{internal_sku}`",
        f"Статус связи: `{row.get('mapping_status') or 'н/д'}`",
        f"Группа: `{row.get('product_group') or 'н/д'}`, комплектность: `{row.get('pack_qty') or 'н/д'}`",
        f"Себестоимость: `{row.get('cost_total') or 'н/д'}` ₽ всего, `{row.get('cost_per_unit') or 'н/д'}` ₽ за единицу.",
        "",
        "Ozon:",
        f"- offer_id: `{row.get('ozon_offer_id') or 'нет'}`",
        f"- product_id: `{row.get('ozon_product_id') or 'нет'}`",
        f"- sku: `{row.get('ozon_sku') or 'нет'}`",
        f"- barcode: `{row.get('ozon_barcode') or 'нет данных'}`",
        f"- active/status: `{row.get('active_ozon') or 'н/д'}` / `{row.get('ozon_status') or 'н/д'}`",
        "",
        "WB:",
        f"- vendorCode: `{row.get('wb_vendor_code') or 'нет'}`",
        f"- nmID: `{row.get('wb_nm_id') or 'нет'}`",
        f"- barcode: `{row.get('wb_barcode') or 'нет данных'}`",
        f"- active/status: `{row.get('active_wb') or 'н/д'}` / `{row.get('wb_status') or 'н/д'}`",
    ]
    if row.get("wb_subject"):
        lines.append(f"- subject: `{row.get('wb_subject')}`")
    if notes:
        lines.extend(["", f"Примечание: {notes}"])
    lines.extend(
        [
            "",
            f"Источник: `{products_path}`",
            "",
            "Изменений в Ozon/WB не выполнял.",
        ]
    )
    return "\n".join(lines)


def _catalog_search_results(*, query: str, matches: list[dict[str, Any]], products_path: Path) -> str:
    lines = [
        "Каталог",
        "",
        f"Итог: по запросу `{query}` найдено `{len(matches)}` товаров, показываю первые `{min(len(matches), 5)}`.",
        "",
        "Совпадения:",
    ]
    for item in matches[:5]:
        row = item["row"]
        internal_sku = row.get("internal_sku") or row.get("internal_product_id") or "нет"
        title = _truncate(str(row.get("product_name") or "без названия"), 90)
        lines.append(
            f"- `{internal_sku}` - {title}; "
            f"Ozon `{row.get('ozon_offer_id') or 'нет'}`, WB `{row.get('wb_vendor_code') or 'нет'}`."
        )
    if len(matches) > 5:
        lines.append(f"- ... еще `{len(matches) - 5}`")
    lines.extend(
        [
            "",
            "Для точной карточки отправь `/catalog <internal_sku>` или точный Ozon/WB артикул.",
            f"Источник: `{products_path}`",
            "",
            "Изменений в Ozon/WB не выполнял.",
        ]
    )
    return "\n".join(lines)


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _approvals(*, data_dir: Path, runtime_db: Path = DEFAULT_RUNTIME_DB) -> TelegramCommandResult:
    if runtime_db == DEFAULT_RUNTIME_DB and data_dir != Path("data"):
        runtime_db = data_dir.parent / "runtime" / "runtime.db"
    status = run_approvals_status(data_dir=data_dir, runtime_db=runtime_db, limit=10)
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
    keyboard: list[list[dict[str, str]]] = []
    for row in rows[:5]:
        lines.append(
            f"- `{row.get('kind')}` `{row.get('id')}`: `{row.get('lifecycle_status')}`"
        )
        if row.get("kind") != "runtime":
            continue
        token = _approval_callback_token(str(row.get("id") or ""))
        lifecycle = str(row.get("lifecycle_status") or "")
        if lifecycle == "pending_review":
            keyboard.append([
                {"text": "Согласовать", "callback_data": f"apa:{token}"},
                {"text": "Отклонить", "callback_data": f"apr:{token}"},
            ])
        elif lifecycle == "approved":
            keyboard.append([{"text": "Применить", "callback_data": f"app:{token}"}])
        elif lifecycle in {"applied", "applying_unknown"}:
            keyboard.append([{"text": "Проверить", "callback_data": f"apv:{token}"}])
    if len(rows) > 5:
        lines.append(f"- ... еще `{len(rows) - 5}`")
    lines.extend(
        [
            "",
            "Runtime-согласования управляются кнопками ниже; apply и verify выполняет только Job Worker.",
        ]
    )
    return TelegramCommandResult(
        command="/approvals",
        ok=True,
        text="\n".join(lines),
        artifacts=status.get("artifacts") if isinstance(status.get("artifacts"), dict) else {},
        reply_markup={"inline_keyboard": keyboard} if keyboard else {},
    )


def _approval_callback_token(approval_id: str) -> str:
    return approval_callback_token(approval_id)


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


def _jobs(*, runtime_db: Path) -> TelegramCommandResult:
    store = JobStore(runtime_db)
    jobs = store.list_jobs(limit=10)
    status_counts: dict[str, int] = {}
    for job in jobs:
        status_counts[job.status] = status_counts.get(job.status, 0) + 1
    lines = [
        "Runtime jobs",
        "",
        "Итог: последние job из SQLite runtime-очереди.",
        "",
        f"Runtime DB: `{runtime_db}`",
        "",
        "Статусы в последних 10:",
    ]
    if status_counts:
        for status, count in sorted(status_counts.items()):
            lines.append(f"- `{status}`: `{count}`")
    else:
        lines.append("- job нет")
    lines.extend(["", "Последние job:"])
    if not jobs:
        lines.append("- нет строк")
    for job in jobs:
        lines.append(f"- `{job.job_id}`: `{job.task_id}` / `{job.status}`")
    lines.extend(
        [
            "",
            "Команды:",
            "- `/job_<job_id>` - подробности job",
            "- `/cancel_<job_id>` - отменить только `created/queued` job",
            "",
            "Изменений в Ozon/WB не выполнял.",
        ]
    )
    return TelegramCommandResult(command="/jobs", ok=True, mode="maintenance", text="\n".join(lines))


def _job_show(job_id: str, *, runtime_db: Path) -> TelegramCommandResult:
    clean_job_id = _clean_job_id(job_id)
    if not clean_job_id:
        return TelegramCommandResult(
            command="/job",
            ok=False,
            mode="maintenance",
            blocked_reason="invalid_job_id",
            text="Runtime job\n\nИтог: не могу показать job - некорректный `job_id`.",
        )
    store = JobStore(runtime_db)
    job = store.get_job(clean_job_id)
    if job is None:
        return TelegramCommandResult(
            command="/job",
            ok=False,
            mode="maintenance",
            blocked_reason="unknown_job",
            text=f"Runtime job\n\nИтог: job не найдена.\n\nJob ID: `{clean_job_id}`",
        )
    events = store.list_events(clean_job_id)
    lines = [
        "Runtime job",
        "",
        f"Job ID: `{job.job_id}`",
        f"Task: `{job.task_id}`",
        f"Статус: `{job.status}`",
        f"Actor: `{job.actor}`",
        f"Создана: `{job.created_at}`",
        f"Обновлена: `{job.updated_at}`",
    ]
    if job.started_at:
        lines.append(f"Старт: `{job.started_at}`")
    if job.finished_at:
        lines.append(f"Финиш: `{job.finished_at}`")
    if job.error:
        lines.append(f"Ошибка: `{job.error}`")
    lines.extend(["", "События:"])
    for event in events[-6:]:
        lines.append(f"- `{event.created_at}` `{event.event_type}` {event.message}".rstrip())
    if len(events) > 6:
        lines.append(f"- ... еще `{len(events) - 6}`")
    lines.extend(["", "Изменений в Ozon/WB не выполнял."])
    return TelegramCommandResult(command="/job", ok=True, mode="maintenance", text="\n".join(lines))


def _job_cancel(job_id: str, *, runtime_db: Path) -> TelegramCommandResult:
    clean_job_id = _clean_job_id(job_id)
    if not clean_job_id:
        return TelegramCommandResult(
            command="/cancel",
            ok=False,
            mode="maintenance",
            blocked_reason="invalid_job_id",
            text="Runtime job cancel\n\nИтог: отмена не выполнена - некорректный `job_id`.",
        )
    service = JobService(store=JobStore(runtime_db), data_dir=Path("data"), runtime_db=runtime_db)
    try:
        result = service.cancel(clean_job_id, reason="cancelled_from_telegram_command")
    except KeyError:
        return TelegramCommandResult(
            command="/cancel",
            ok=False,
            mode="maintenance",
            blocked_reason="unknown_job",
            text=f"Runtime job cancel\n\nИтог: job не найдена.\n\nJob ID: `{clean_job_id}`",
        )
    lines = [
        "Runtime job cancel",
        "",
        f"Итог: {'job отменена' if result.ok else 'отмена заблокирована'}.",
        f"Job ID: `{result.job.job_id}`",
        f"Статус: `{result.job.status}`",
    ]
    if result.message:
        lines.append(f"Причина: `{result.message}`")
    lines.extend(["", "Изменений в Ozon/WB не выполнял."])
    return TelegramCommandResult(
        command="/cancel",
        ok=result.ok,
        mode="maintenance",
        blocked_reason="" if result.ok else "cancel_blocked",
        text="\n".join(lines),
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


def _wb_actions_report_stats(csv_path: str | None) -> dict[str, Any]:
    return wb_actions_report_stats(csv_path)


def _int_value(value: Any) -> int:
    try:
        return int(float(str(value or "0").replace(" ", "").replace(",", ".")))
    except (TypeError, ValueError):
        return 0


def _wb_report_stat(stats: dict[str, Any], key: str) -> int | None:
    if not stats.get("available"):
        return None
    value = stats.get(key)
    return int(value) if isinstance(value, int) else None


def _wb_discount_distribution_lines(distribution: Any) -> list[str]:
    if not isinstance(distribution, dict) or not distribution:
        return ["- нет данных"]
    rows: list[tuple[int, int]] = []
    for discount, count in distribution.items():
        try:
            rows.append((int(discount), int(count)))
        except (TypeError, ValueError):
            continue
    if not rows:
        return ["- нет данных"]
    return [f"- скидка {discount}%: `{count}` товаров" for discount, count in sorted(rows, reverse=True)]


def _wb_exclusion_reason_lines(reasons: Any, *, threshold: str) -> list[str]:
    if not isinstance(reasons, dict) or not reasons:
        return []
    lines: list[str] = []
    for reason, count in sorted(reasons.items(), key=lambda item: (-_int_value(item[1]), str(item[0]))):
        reason_text = str(reason)
        if reason_text.startswith("скидка до порога >"):
            label = (
                f"требуемая скидка акции выше порога {threshold}%; "
                "целевая скидка становится ниже требования акции"
            )
        else:
            label = reason_text or "причина не указана"
        lines.append(f"- причина снятия: {label}: `{_int_value(count)}` товаров")
    return lines


def _wb_latest_history_data(verify: dict[str, Any]) -> dict[str, Any]:
    polls = verify.get("polls") if isinstance(verify.get("polls"), list) else []
    for poll in reversed(polls):
        if not isinstance(poll, dict):
            continue
        data = ((((poll.get("status") or {}).get("history") or {}).get("data") or {}).get("data") or {})
        if isinstance(data, dict) and data:
            return data
    return {}


def _messenger_counts_from_pending(*, data_dir: Path, run_id: str) -> dict[str, int]:
    path = data_dir / "pending" / f"{run_id}_pending" / "inbox_pending.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    actions = data.get("messenger_actions") if isinstance(data, dict) else []
    if not isinstance(actions, list):
        return {}
    counts: dict[str, int] = {}
    for action in actions:
        if not isinstance(action, dict):
            continue
        action_type = str(action.get("action_type") or "")
        if not action_type:
            continue
        counts[action_type] = counts.get(action_type, 0) + 1
    return counts


def _review_action_counts_from_summary(summary: dict[str, Any]) -> dict[str, int]:
    artifacts = summary.get("artifacts") if isinstance(summary.get("artifacts"), dict) else {}
    actions_path = artifacts.get("actions")
    if not actions_path:
        return {}
    try:
        data = json.loads(Path(str(actions_path)).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    actions = data.get("actions") if isinstance(data, dict) else []
    if not isinstance(actions, list):
        return {}
    counts: dict[str, int] = {}
    for action in actions:
        if not isinstance(action, dict):
            continue
        action_type = str(action.get("action_type") or "")
        if not action_type:
            continue
        counts[action_type] = counts.get(action_type, 0) + 1
    return counts


def _valid_inbox_run_id(value: str, *, prefix: str) -> bool:
    text = str(value or "")
    if not text.startswith(prefix):
        return False
    return all(char.isalnum() or char in {"_", "-"} for char in text)


def _run_inbox_apply_job(
    *,
    task_id: str,
    source_run_id: str,
    data_dir: Path,
    credentials: AppCredentials | None,
) -> dict[str, Any]:
    _ = (task_id, source_run_id, data_dir, credentials)
    raise RuntimeError("Direct Telegram apply is disabled; use runtime approval and Job Worker.")


def _run_plan_apply_job(
    *,
    task_id: str,
    plan_run_id: str,
    data_dir: Path,
    credentials: AppCredentials | None,
) -> dict[str, Any]:
    _ = (task_id, plan_run_id, data_dir, credentials)
    raise RuntimeError("Direct Telegram apply is disabled; use runtime approval and Job Worker.")


def _inbox_apply_result_text(*, command: str, title: str, result: dict[str, Any]) -> TelegramCommandResult:
    reviews = result.get("reviews") if isinstance(result.get("reviews"), dict) else {}
    review_apply = reviews.get("apply") if isinstance(reviews.get("apply"), dict) else {}
    messenger = result.get("messenger") if isinstance(result.get("messenger"), dict) else {}
    applied_counts = review_apply.get("applied_counts") if isinstance(review_apply.get("applied_counts"), dict) else {}
    mark_read = messenger.get("mark_read") if isinstance(messenger.get("mark_read"), list) else []
    lines = [
        title,
        "",
        f"Итог: apply завершен со статусом `{result.get('overall_status') or 'н/д'}`.",
        f"Job ID: `{result.get('job_id') or 'н/д'}`",
        f"Run ID: `{result.get('run_id') or 'н/д'}`",
        "",
        "Применено:",
        f"- отзывы Ozon: `{_int(applied_counts.get('ozon_public_review_replies'))}`",
        f"- отметки Ozon отзывов просмотренными: `{_int(applied_counts.get('ozon_marked_viewed'))}`",
        f"- отзывы WB: `{_int(applied_counts.get('wb_public_review_replies'))}`",
        f"- вопросы WB: `{_int(applied_counts.get('wb_question_answers'))}`",
    ]
    if messenger:
        lines.extend(
            [
                f"- Ozon Messenger: `{messenger.get('status') or 'н/д'}`",
                f"- Ozon уведомления mark-read: `{sum(1 for row in mark_read if isinstance(row, dict) and row.get('ok'))}` из `{len(mark_read)}`",
            ]
        )
    artifacts = _safe_artifacts(result)
    if artifacts:
        lines.extend(["", "Файлы:"])
        if artifacts.get("report"):
            lines.append(f"- отчет: `{artifacts['report']}`")
    return TelegramCommandResult(
        command=command,
        ok=str(result.get("overall_status") or "") in {"ok", "warning"},
        mode="apply",
        text="\n".join(lines),
        artifacts=artifacts,
    )


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


def _valid_ozon_elastic_plan_id(value: str) -> bool:
    text = str(value or "")
    if not text.startswith("ozon_elastic_plan_"):
        return False
    return all(char.isalnum() or char in {"_", "-"} for char in text)


def _valid_ozon_actions_plan_id(value: str) -> bool:
    text = str(value or "")
    if not text.startswith("ozon_actions_optimizer_plan_"):
        return False
    return all(char.isalnum() or char in {"_", "-"} for char in text)


def _valid_wb_actions_plan_id(value: str) -> bool:
    text = str(value or "")
    if not text.startswith("wb_actions_discount_plan_"):
        return False
    return all(char.isalnum() or char in {"_", "-"} for char in text)


def _parse_wb_manual_parameters(value: str) -> tuple[int, int, int] | None:
    parts = [part for part in re.split(r"[\s,;/|:\-–—]+", str(value or "").strip()) if part]
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    values = tuple(int(part) for part in parts)
    if any(item < 0 or item > 99 for item in values):
        return None
    return values


def _parse_internal_wb_scheme(value: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"(\d{1,2})-(\d{1,2})-(\d{1,2})", str(value or "").strip())
    if not match:
        return None
    values = tuple(int(part) for part in match.groups())
    if any(item < 0 or item > 99 for item in values):
        return None
    return values


def _parse_wb_outside_discount(value: Any) -> int | None:
    text = str(value or "").strip().removesuffix("%").strip()
    if not text.isdigit():
        return None
    discount = int(text)
    return discount if 0 <= discount <= 99 else None


def _valid_wb_min_price_plan_id(value: str) -> bool:
    return bool(
        re.fullmatch(
            r"wb_best_price_actions_plan_[0-9]{1,2}_[A-Za-z0-9_-]+",
            str(value or "").strip(),
        )
    )


def _wb_manual_actions_cancel(*, stage: str) -> TelegramCommandResult:
    detail = "Ввод параметров отменён." if stage == "parameters" else "Операция отменена."
    return TelegramCommandResult(
        command="/wb-actions-manual",
        ok=True,
        mode="cancelled",
        text=f"Ручная акция\n\n{detail} Изменений в WB не выполнял.",
        reply_markup=WB_MENU_KEYBOARD,
    )


def _wb_manual_actions_reject(plan_run_id: str) -> TelegramCommandResult:
    if not _valid_wb_actions_plan_id(plan_run_id):
        return TelegramCommandResult(
            command="/wb-actions-manual",
            ok=False,
            mode="cancelled",
            blocked_reason="invalid_plan_run_id",
            text=(
                "Ручная акция\n\n"
                "Отклонение не принято: некорректный идентификатор расчёта.\n\n"
                "Изменений в WB не выполнял."
            ),
        )
    return TelegramCommandResult(
        command="/wb-actions-manual",
        ok=True,
        mode="cancelled",
        text=(
            "Ручная акция\n\n"
            f"Расчёт `{plan_run_id}` отклонён. Скидки в WB не изменены."
        ),
        reply_markup=WB_MENU_KEYBOARD,
    )


def _clean_job_id(value: str) -> str:
    text = str(value or "").strip().strip("`")
    if not text.startswith("job_"):
        return ""
    if len(text) > 160:
        return ""
    if not all(char.isalnum() or char in {"_", "-"} for char in text):
        return ""
    return text


def _safe_error(exc: BaseException) -> str:
    text = str(exc).replace("\n", " ").replace("\r", " ")
    for marker in ("token", "secret", "cookie", "storage", "auth", "api_key", "client_secret", "password"):
        text = text.replace(marker, "<redacted>")
    return _truncate(text, 500)


def _normalize_button_command(command: str) -> str:
    text = str(command or "").strip().lower().replace("ё", "е")
    aliases = {
        "статус": "/status",
        "помощь": "/help",
        "общий вчерашний отчет": "/today",
        "общий вчерашний отчёт": "/today",
        "отчет за вчера": "/today",
        "отчёт за вчера": "/today",
        "озон": "/ozon",
        "ozon": "/ozon",
        "вайлдберриз": "/wb",
        "wildberries": "/wb",
        "wb": "/wb",
        "вб": "/wb",
        "назад": "/menu",
        "главное меню": "/menu",
        "меню": "/menu",
        "ozon акции": "/ozon-actions",
        "озон акции": "/ozon-actions",
        "ozon все акции": "/ozon-actions",
        "озон все акции": "/ozon-actions",
        "ozon эластик": "/elastic",
        "ozon elastic": "/elastic",
        "озон эластик": "/elastic",
        "ozon входящие": "/ozon-inbox",
        "озон входящие": "/ozon-inbox",
        "остатки и поставки ozon": "/ozon-stock-supplies",
        "ozon остатки и поставки": "/ozon-stock-supplies",
        "озон остатки и поставки": "/ozon-stock-supplies",
        "остатки ozon": "/ozon-stock-supplies",
        "в работу ozon": "/ozon-work-plan",
        "ozon в работу": "/ozon-work-plan",
        "озон в работу": "/ozon-work-plan",
        "цены и маржа ozon": "/ozon-pricing-margin",
        "ozon цены и маржа": "/ozon-pricing-margin",
        "озон цены и маржа": "/ozon-pricing-margin",
        "цены и маржа wb": "/wb-pricing-margin",
        "цены и маржа вб": "/wb-pricing-margin",
        "wb цены и маржа": "/wb-pricing-margin",
        "вб цены и маржа": "/wb-pricing-margin",
        "отчет за период ozon": "/period-report-ozon",
        "ozon отчет за период": "/period-report-ozon",
        "wb акции": "/wb-actions",
        "вб акции": "/wb-actions",
        "wildberries акции": "/wb-actions",
        "ручная акция": "/wb-actions-manual",
        "wb ручная акция": "/wb-actions-manual",
        "акции от минимальной цены": "/wb-actions-min-price",
        "wb акции от минимальной цены": "/wb-actions-min-price",
        "вб акции от минимальной цены": "/wb-actions-min-price",
        "wb аналитика": "/wb-analytics",
        "вб аналитика": "/wb-analytics",
        "wildberries аналитика": "/wb-analytics",
        "остатки и поставки": "/wb-stock-supplies",
        "wb остатки и поставки": "/wb-stock-supplies",
        "вб остатки и поставки": "/wb-stock-supplies",
        "в работу": "/wb-work-plan",
        "wb в работу": "/wb-work-plan",
        "вб в работу": "/wb-work-plan",
        "wb входящие": "/wb-inbox",
        "вб входящие": "/wb-inbox",
        "wildberries входящие": "/wb-inbox",
        "отчет за период wb": "/period-report-wb",
        "wb отчет за период": "/period-report-wb",
        "отчет за период": "/period-report",
    }
    return aliases.get(text, command)


def _is_explicit_command_or_button(message: str) -> bool:
    text = str(message or "").strip()
    if text.startswith("/"):
        return True
    normalized = _normalize_button_command(text)
    return normalized != text and normalized.startswith("/")


def _parse_command(message: str) -> tuple[str, str]:
    text = str(message or "").strip()
    normalized_full = _normalize_button_command(text)
    if normalized_full != text and normalized_full.startswith("/"):
        return normalized_full, ""
    raw_command, _, argument = text.partition(" ")
    command = raw_command.lower()
    if "@" in command:
        command = command.split("@", 1)[0]
    return command or "/help", argument.strip()


def _owner_decimal(value: Any) -> Decimal | None:
    text = str(value or "").strip().replace(" ", "").replace(",", ".")
    try:
        result = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _decimal_text(value: Decimal) -> str:
    normalized = value.quantize(Decimal("0.01"))
    return format(normalized, "f").rstrip("0").rstrip(".")
