from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
import hashlib
from typing import Any

from seller_agent.bot.commands import (
    TelegramCommandResult,
    _is_explicit_command_or_button,
    _normalize_button_command,
    _parse_command,
)
from seller_agent.core.job_service import JobService
from seller_agent.core.job_store import DEFAULT_RUNTIME_DB, JobStore


@dataclass(frozen=True)
class RuntimeJobRequest:
    command: str
    task_id: str
    params: dict[str, Any]
    title: str


def dispatch_runtime_job_message(
    message: str,
    *,
    update_id: int,
    chat_id: int,
    thread_id: int | None = None,
    data_dir: Path = Path("data"),
    runtime_db: Path = DEFAULT_RUNTIME_DB,
    live_today: bool = False,
    live_status: bool = False,
    conversation_state: dict[str, Any] | None = None,
) -> TelegramCommandResult | None:
    runtime_request = _runtime_request_for_message(
        message,
        live_today=live_today,
        live_status=live_status,
        conversation_state=conversation_state,
    )
    if isinstance(runtime_request, TelegramCommandResult):
        return runtime_request
    if runtime_request is None:
        return None
    return _enqueue_runtime_request(
        request=runtime_request,
        update_id=update_id,
        chat_id=chat_id,
        thread_id=thread_id,
        data_dir=data_dir,
        runtime_db=runtime_db,
        payload={"kind": "message", "message": message},
    )


def dispatch_runtime_job_callback(
    callback_data: str,
    *,
    update_id: int,
    chat_id: int,
    thread_id: int | None = None,
    data_dir: Path = Path("data"),
    runtime_db: Path = DEFAULT_RUNTIME_DB,
) -> TelegramCommandResult | None:
    if callback_data.startswith(("apa:", "apr:", "app:", "apv:")):
        return _handle_runtime_approval_callback(
            callback_data,
            chat_id=chat_id,
            data_dir=data_dir,
            runtime_db=runtime_db,
        )
    runtime_request = _runtime_request_for_callback(callback_data)
    if isinstance(runtime_request, TelegramCommandResult):
        return runtime_request
    if runtime_request is None:
        return None
    return _enqueue_runtime_request(
        request=runtime_request,
        update_id=update_id,
        chat_id=chat_id,
        thread_id=thread_id,
        data_dir=data_dir,
        runtime_db=runtime_db,
        payload={"kind": "callback", "callback_data": callback_data},
    )


def _handle_runtime_approval_callback(
    callback_data: str,
    *,
    chat_id: int,
    data_dir: Path,
    runtime_db: Path,
) -> TelegramCommandResult:
    action, token = callback_data.split(":", 1)
    store = JobStore(runtime_db)
    matches = [
        approval
        for approval in store.list_approvals(limit=500)
        if hashlib.sha256(approval.approval_id.encode("utf-8")).hexdigest()[:16] == token
    ]
    if len(matches) != 1:
        return _invalid_callback("Согласование", "Runtime approval не найден или идентификатор неоднозначен.")
    approval = matches[0]
    service = JobService(store=store, data_dir=data_dir, runtime_db=runtime_db)
    try:
        if action == "apa":
            current = service.approve(approval.approval_id)
            message = f"Согласование `{current.approval_id}` подтверждено. Для записи нажмите «Применить» в /approvals."
        elif action == "apr":
            current = service.reject(approval.approval_id)
            message = f"Согласование `{current.approval_id}` отклонено. Изменений в магазинах не выполнено."
        elif action == "app":
            job = service.submit_approval_apply(approval.approval_id, actor=f"telegram:{chat_id}")
            message = f"Apply поставлен в Job Worker. Job ID: `{job.job_id}`."
        else:
            job = service.submit_approval_verify(approval.approval_id, actor=f"telegram:{chat_id}")
            message = f"Verify поставлен в Job Worker. Job ID: `{job.job_id}`."
    except (KeyError, RuntimeError, ValueError) as exc:
        return _invalid_callback("Согласование", _safe_enqueue_error(exc))
    return TelegramCommandResult(command="/approvals", ok=True, text="Согласования\n\n" + message)


def _enqueue_runtime_request(
    *,
    request: RuntimeJobRequest,
    update_id: int,
    chat_id: int,
    thread_id: int | None,
    data_dir: Path,
    runtime_db: Path,
    payload: dict[str, Any],
) -> TelegramCommandResult:
    store = JobStore(runtime_db)
    update_payload = {
        **payload,
        "params": request.params,
        "task_id": request.task_id,
        "title": request.title,
        "thread_id": thread_id,
    }
    registered = store.register_telegram_update(
        update_id=update_id,
        chat_id=str(chat_id),
        command=request.command,
        payload=update_payload,
        processing_status="received",
    )
    if not registered:
        existing = store.get_telegram_update(update_id)
        job_id = existing.job_id if existing else ""
        return TelegramCommandResult(
            command=request.command,
            ok=True,
            text=(
                request.title
                + "\n\n"
                + "Итог: повторный Telegram update не поставлен в очередь второй раз.\n\n"
                + f"Job ID: `{job_id or 'не найден'}`\n"
                + f"Runtime DB: `{runtime_db}`"
            ),
            artifacts={"runtime_db": str(runtime_db)},
        )

    try:
        service = JobService(store=store, data_dir=data_dir, runtime_db=runtime_db)
        job = service.submit(
            task_id=request.task_id,
            params=request.params,
            actor=f"telegram:{chat_id}",
            source="telegram",
        )
    except Exception as exc:
        store.update_telegram_update_status(update_id=update_id, processing_status="enqueue_failed")
        return TelegramCommandResult(
            command=request.command,
            ok=False,
            blocked_reason="runtime_enqueue_failed",
            text=(
                f"{request.title}\n\n"
                "Задача не поставлена в очередь.\n\n"
                f"Причина: `{_safe_enqueue_error(exc)}`\n\n"
                "Изменений в Ozon/WB не выполнялось."
            ),
        )

    store.update_telegram_update_status(update_id=update_id, processing_status="queued", job_id=job.job_id)
    return TelegramCommandResult(
        command=request.command,
        ok=True,
        text=(
            request.title
            + "\n\n"
            + "Итог: задача поставлена в runtime-очередь.\n\n"
            + f"Job ID: `{job.job_id}`\n"
            + f"Task: `{request.task_id}`\n"
            + f"Статус: `{job.status}`\n\n"
            + "Job Worker выполнит задачу и отправит итоговый отчёт."
        ),
        artifacts={"runtime_db": str(runtime_db)},
    )


def _runtime_request_for_message(
    message: str,
    *,
    live_today: bool,
    live_status: bool,
    conversation_state: dict[str, Any] | None = None,
) -> RuntimeJobRequest | TelegramCommandResult | None:
    state = conversation_state if isinstance(conversation_state, dict) else {}
    if state.get("stage") == "ozon_pricing_margin_input" and not _is_explicit_command_or_button(message):
        margin = _decimal_input(message)
        unit_cost = _decimal_input(state.get("unit_cost"))
        try:
            period_days = int(state.get("period_days") or 0)
        except (TypeError, ValueError):
            period_days = 0
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
                conversation_state=state,
            )
        if unit_cost is None or unit_cost <= 0 or period_days not in {15, 30}:
            return TelegramCommandResult(
                command="/ozon-pricing-margin",
                ok=False,
                mode="input",
                blocked_reason="invalid_ozon_pricing_state",
                text="Параметры расчёта потеряны. Запустите «Цены и маржа Ozon» заново.",
            )
        return RuntimeJobRequest(
            command="/ozon-pricing-margin",
            task_id="ozon-pricing-margin",
            params={
                "unit_cost": _decimal_text(unit_cost),
                "target_margin": _decimal_text(margin),
                "period_days": period_days,
            },
            title="Ozon: цены и маржа",
        )

    if state.get("stage") == "wb_pricing_margin_input" and not _is_explicit_command_or_button(message):
        margin = _decimal_input(message)
        unit_cost = _decimal_input(state.get("unit_cost"))
        try:
            period_days = int(state.get("period_days") or 0)
        except (TypeError, ValueError):
            period_days = 0
        if margin is None or margin < 0 or margin > Decimal("100000"):
            return TelegramCommandResult(
                command="/wb-pricing-margin",
                ok=False,
                mode="input",
                blocked_reason="invalid_wb_target_margin",
                text=(
                    "WB: цены и маржа\n\nВведите неотрицательную маржу на одно "
                    "физическое изделие в рублях, например `60`."
                ),
                conversation_state=state,
            )
        if unit_cost is None or unit_cost <= 0 or period_days not in {15, 30}:
            return TelegramCommandResult(
                command="/wb-pricing-margin",
                ok=False,
                mode="input",
                blocked_reason="invalid_wb_pricing_state",
                text="Параметры расчёта потеряны. Запустите «Цены и маржа WB» заново.",
            )
        return RuntimeJobRequest(
            command="/wb-pricing-margin",
            task_id="wb-pricing-margin",
            params={
                "unit_cost": _decimal_text(unit_cost),
                "target_margin": _decimal_text(margin),
                "period_days": period_days,
            },
            title="WB: цены и маржа",
        )

    command, _ = _parse_command(message)
    command = _normalize_button_command(command)
    aliases = {
        "/elastic": RuntimeJobRequest("/elastic", "ozon-elastic-plan", {}, "Ozon Elastic"),
        "/ozon-elastic": RuntimeJobRequest("/elastic", "ozon-elastic-plan", {}, "Ozon Elastic"),
        "/ozon_elastic": RuntimeJobRequest("/elastic", "ozon-elastic-plan", {}, "Ozon Elastic"),
        "/ozon-actions": RuntimeJobRequest("/ozon-actions", "ozon-actions-optimizer-plan", {}, "Ozon все акции"),
        "/ozon_actions": RuntimeJobRequest("/ozon-actions", "ozon-actions-optimizer-plan", {}, "Ozon все акции"),
        "/ozon-all-actions": RuntimeJobRequest("/ozon-actions", "ozon-actions-optimizer-plan", {}, "Ozon все акции"),
        "/ozon-stock-supplies": RuntimeJobRequest("/ozon-stock-supplies", "ozon-stock-supply-monitor", {}, "Остатки и поставки Ozon"),
        "/ozon_stock_supplies": RuntimeJobRequest("/ozon-stock-supplies", "ozon-stock-supply-monitor", {}, "Остатки и поставки Ozon"),
        "/wb-actions": RuntimeJobRequest("/wb-actions", "wb-actions-discount-plan", {"scheme_text": "70-55-55"}, "WB акции 70-55-55"),
        "/wb_actions": RuntimeJobRequest("/wb-actions", "wb-actions-discount-plan", {"scheme_text": "70-55-55"}, "WB акции 70-55-55"),
        "/wb-actions-70-55-55": RuntimeJobRequest("/wb-actions", "wb-actions-discount-plan", {"scheme_text": "70-55-55"}, "WB акции 70-55-55"),
        "/wb-actions-min-price": None,
        "/wb_actions_min_price": None,
        "/wb-analytics": RuntimeJobRequest("/wb-analytics", "wb-parser-warehouse-analytics", {"supplier_id": "4516781", "limit": 500, "report_limit": 50}, "WB аналитика"),
        "/wb_analytics": RuntimeJobRequest("/wb-analytics", "wb-parser-warehouse-analytics", {"supplier_id": "4516781", "limit": 500, "report_limit": 50}, "WB аналитика"),
        "/wb-stock-supplies": RuntimeJobRequest("/wb-stock-supplies", "wb-stock-supply-monitor", {}, "Остатки и поставки WB"),
        "/wb_stock_supplies": RuntimeJobRequest("/wb-stock-supplies", "wb-stock-supply-monitor", {}, "Остатки и поставки WB"),
        "/wb-pricing-margin": None,
        "/wb_pricing_margin": None,
        "/ozon-inbox": RuntimeJobRequest("/ozon-inbox", "ozon-inbox", {}, "Ozon входящие"),
        "/ozon_inbox": RuntimeJobRequest("/ozon-inbox", "ozon-inbox", {}, "Ozon входящие"),
        "/wb-inbox": RuntimeJobRequest("/wb-inbox", "wb-inbox", {}, "WB входящие"),
        "/wb_inbox": RuntimeJobRequest("/wb-inbox", "wb-inbox", {}, "WB входящие"),
    }
    if command == "/status" and live_status:
        return RuntimeJobRequest("/status", "status-preflight", {"include_lk": True}, "Статус проекта")
    if command == "/today" and live_today:
        return RuntimeJobRequest(
            "/today",
            "daily-morning-report",
            {"refresh_preflight": True, "seller_v2": False, "seller_v3": True},
            "Ежедневный отчёт",
        )
    return aliases.get(command)


def _runtime_request_for_callback(data: str) -> RuntimeJobRequest | TelegramCommandResult | None:
    value = str(data or "").strip()
    if value.startswith("mpr_run:"):
        parts = value.split(":")
        if len(parts) != 5 or parts[1] not in {"o", "w"} or parts[2] not in {"s", "f", "a"}:
            return _invalid_callback("Отчёт за период", "Параметры отчёта повреждены.")
        date_from = _date_value(parts[3])
        date_to = _date_value(parts[4])
        if date_from is None or date_to is None or date_from > date_to or date_to > date.today():
            return _invalid_callback("Отчёт за период", "Период отчёта некорректен.")
        return RuntimeJobRequest(
            "/period-report",
            "marketplace-period-report",
            {
                "marketplace": "ozon" if parts[1] == "o" else "wb",
                "report_type": {"s": "short", "f": "financial", "a": "full"}[parts[2]],
                "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(),
            },
            "Отчёт за период",
        )
    if value.startswith(("ozwp_run:", "wbwp_run:")):
        parts = value.split(":")
        is_ozon = value.startswith("ozwp_run:")
        max_clusters = 20 if is_ozon else 6
        if (
            len(parts) != 4
            or parts[1] not in {"c", "d"}
            or not parts[2].isdigit()
            or not parts[3].isdigit()
        ):
            return _invalid_callback("В работу", "Параметры производственного плана повреждены.")
        amount = int(parts[2])
        clusters = int(parts[3])
        maximum = 100_000 if parts[1] == "c" else 365
        if not 1 <= amount <= maximum or not 1 <= clusters <= max_clusters:
            return _invalid_callback("В работу", "Параметры производственного плана вышли за допустимые границы.")
        return RuntimeJobRequest(
            "/ozon-work-plan" if is_ozon else "/wb-work-plan",
            "ozon-production-work-plan" if is_ozon else "wb-production-work-plan",
            {
                "mode": "capacity" if parts[1] == "c" else "coverage_days",
                "value": amount,
                "cluster_count": clusters,
            },
            "Ozon: в работу" if is_ozon else "WB: в работу",
        )
    if value.startswith("wbam_confirm:"):
        scheme = value.removeprefix("wbam_confirm:").strip()
        if not _valid_wb_scheme(scheme):
            return _invalid_callback("Ручная акция", "Параметры схемы повреждены.")
        return RuntimeJobRequest(
            "/wb-actions-manual",
            "wb-actions-discount-plan",
            {"scheme_text": scheme},
            "Ручная акция",
        )
    if value.startswith("wbmp_confirm:"):
        discount = value.removeprefix("wbmp_confirm:").strip()
        if not discount.isdigit() or not 0 <= int(discount) <= 99:
            return _invalid_callback(
                "Акции от минимальной цены",
                "Скидка для товаров вне акций повреждена.",
            )
        return RuntimeJobRequest(
            "/wb-actions-min-price",
            "wb-best-price-action-plan",
            {"outside_discount": int(discount)},
            "Акции от минимальной цены",
        )

    apply_prefixes = {
        "oe_apply:": ("/elastic_apply", "ozon-elastic-apply", "plan_run_id", "Ozon Elastic apply"),
        "oza_apply:": ("/ozon_actions_apply", "ozon-actions-optimizer-apply", "plan_run_id", "Ozon все акции apply"),
        "wba_apply:": ("/wb_actions_apply", "wb-actions-discount-apply", "plan_run_id", "WB акции apply"),
        "wbmp_apply:": (
            "/wb-actions-min-price",
            "wb-best-price-action-apply",
            "plan_run_id",
            "Акции от минимальной цены apply",
        ),
        "ozin_apply:": ("/ozon_inbox_apply", "ozon-inbox-apply", "source_run_id", "Ozon входящие apply"),
        "wbin_apply:": ("/wb_inbox_apply", "wb-inbox-apply", "source_run_id", "WB входящие apply"),
    }
    for prefix, (command, task_id, source_key, title) in apply_prefixes.items():
        if not value.startswith(prefix):
            continue
        source_id = value.removeprefix(prefix).strip()
        if not _safe_runtime_id(source_id):
            return _invalid_callback(title, "Идентификатор согласованного пакета некорректен.")
        return RuntimeJobRequest(
            command,
            task_id,
            {source_key: source_id, "confirmed_by_user": True},
            title,
        )
    return None


def _invalid_callback(title: str, reason: str) -> TelegramCommandResult:
    return TelegramCommandResult(
        command="callback",
        ok=False,
        blocked_reason="invalid_runtime_callback",
        text=f"{title}\n\n{reason}\n\nИзменений в Ozon/WB не выполнялось.",
    )


def _date_value(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _valid_wb_scheme(value: str) -> bool:
    parts = value.split("-")
    return len(parts) == 3 and all(part.isdigit() and 0 <= int(part) <= 99 for part in parts)


def _safe_runtime_id(value: str) -> bool:
    return bool(value) and len(value) <= 160 and all(char.isalnum() or char in {"_", "-"} for char in value)


def _safe_enqueue_error(exc: Exception) -> str:
    text = " ".join(str(exc).split())
    lowered = text.lower()
    if any(marker in lowered for marker in ("token", "secret", "cookie", "storage", "auth", "api_key")):
        return exc.__class__.__name__
    return text[:500] or exc.__class__.__name__


def _decimal_input(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value or "").strip().replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _decimal_text(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f").rstrip("0").rstrip(".")
