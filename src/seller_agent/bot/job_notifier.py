from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from seller_agent.bot.telegram_runner import (
    ApiRequest,
    DocumentApiRequest,
    TelegramSendResult,
    safe_report_attachment_paths,
    send_telegram_document,
    send_telegram_text,
    telegram_api_document_request,
    telegram_api_request,
)
from seller_agent.core.job_models import ApprovalRecord, JobRecord, TelegramUpdateRecord
from seller_agent.core.job_store import (
    DEFAULT_RUNTIME_DB,
    ApprovalSourceJobAmbiguousError,
    JobStore,
    TERMINAL_JOB_STATUSES,
    approval_callback_token,
)


@dataclass(frozen=True)
class JobNotificationResult:
    ok: bool
    job_id: str
    chat_id: int | None = None
    thread_id: int | None = None
    sent_messages: list[TelegramSendResult] = field(default_factory=list)
    sent_documents: list[TelegramSendResult] = field(default_factory=list)
    blocked_reason: str = ""
    error: str = ""


def notify_telegram_job_result(
    *,
    token: str,
    job_id: str,
    store: JobStore | None = None,
    runtime_db: Path = DEFAULT_RUNTIME_DB,
    data_dir: Path = Path("data"),
    api_request: ApiRequest = telegram_api_request,
    document_api_request: DocumentApiRequest = telegram_api_document_request,
) -> JobNotificationResult:
    job_store = store or JobStore(runtime_db)
    job = job_store.get_job(job_id)
    if job is None:
        return JobNotificationResult(ok=False, job_id=job_id, blocked_reason="unknown_job")
    if job.status not in TERMINAL_JOB_STATUSES:
        return JobNotificationResult(
            ok=True,
            job_id=job_id,
            blocked_reason="job_not_terminal",
        )
    update = job_store.get_telegram_update_by_job_id(job_id)
    if update is None:
        return JobNotificationResult(ok=False, job_id=job_id, blocked_reason="missing_telegram_update")
    chat_id = _int_or_none(update.chat_id)
    if chat_id is None:
        return JobNotificationResult(ok=False, job_id=job_id, blocked_reason="invalid_chat_id")

    summary = _job_summary(job)
    if job.task_id in {"ozon-lk-state-monitor", "ozon-min-price-timer-plan"} and summary.get("notification_required") is False:
        job_store.update_telegram_update_status(update_id=update.update_id, processing_status="completed", job_id=job_id)
        return JobNotificationResult(ok=True, job_id=job_id, chat_id=chat_id, thread_id=_thread_id_from_update(update), blocked_reason="unchanged_state_suppressed")

    thread_id = _thread_id_from_update(update)
    try:
        approval = job_store.get_approval_by_source_job_id(job.job_id)
    except ApprovalSourceJobAmbiguousError as exc:
        job_store.update_telegram_update_status(
            update_id=update.update_id,
            processing_status="notification_failed",
            job_id=job_id,
        )
        return JobNotificationResult(
            ok=False,
            job_id=job_id,
            chat_id=chat_id,
            thread_id=thread_id,
            blocked_reason="ambiguous_runtime_approval",
            error=str(exc),
        )
    text, reply_markup = build_job_result_presentation(
        job,
        update=update,
        approval=approval,
        data_dir=data_dir,
    )
    text_results = send_telegram_text(
        token=token,
        chat_id=chat_id,
        thread_id=thread_id,
        text=text,
        reply_markup=reply_markup,
        api_request=api_request,
    )
    document_results: list[TelegramSendResult] = []
    for path in safe_report_attachment_paths(artifacts=_job_artifacts(job), data_dir=data_dir):
        document_results.append(
            send_telegram_document(
                token=token,
                chat_id=chat_id,
                thread_id=thread_id,
                document_path=path,
                document_api_request=document_api_request,
            )
        )

    ok = all(result.ok for result in text_results) and all(result.ok for result in document_results)
    job_store.update_telegram_update_status(
        update_id=update.update_id,
        processing_status="completed" if ok else "notification_failed",
        job_id=job_id,
    )
    return JobNotificationResult(
        ok=ok,
        job_id=job_id,
        chat_id=chat_id,
        thread_id=thread_id,
        sent_messages=text_results,
        sent_documents=document_results,
        error="; ".join(result.error for result in [*text_results, *document_results] if result.error),
    )


def build_job_result_text(job: JobRecord) -> str:
    text, _ = build_job_result_presentation(job)
    return text


def build_job_result_presentation(
    job: JobRecord,
    *,
    update: TelegramUpdateRecord | None = None,
    approval: ApprovalRecord | None = None,
    data_dir: Path = Path("data"),
) -> tuple[str, dict[str, Any]]:
    if job.task_id == "ozon-pricing-margin":
        return _ozon_pricing_margin_result_text(job), {}
    if job.status not in {"success", "partial_success"}:
        return _failed_job_text(job), {}

    summary = _job_summary(job)
    if not summary:
        return _generic_job_text(job), {}

    # Reuse the established owner-facing formatters without rerunning tasks.
    from seller_agent.bot import commands

    if job.task_id == "status-preflight":
        return _with_job_id(commands._status_preflight_chat_text(summary), job.job_id), {}
    if job.task_id == "daily-morning-report":
        return _with_job_id(commands._daily_report_chat_text(summary), job.job_id), {}
    if job.task_id == "liquidation-daily-control":
        return _liquidation_control_result(job, summary), {}
    if job.task_id == "wb-liquidation-stage2-plan":
        return _wb_liquidation_stage2_result(job, summary), {}
    if job.task_id in {"wb-liquidation-stage2-apply", "wb-liquidation-stage2-verify"}:
        return _wb_liquidation_stage2_apply_result(job, summary), {}
    if job.task_id == "ozon-stars-control":
        return _ozon_stars_control_result(job, summary), {}
    if job.task_id == "ozon-lk-state-monitor":
        return _ozon_lk_state_result(job, summary), {}
    if job.task_id == "ozon-min-price-timer-plan":
        return _ozon_min_price_timer_result(job, summary), {}
    if job.task_id == "wb-parser-warehouse-analytics":
        return _with_job_id(commands._wb_analytics_chat_text(summary), job.job_id), {}
    if job.task_id == "wb-stock-supply-monitor":
        return _with_job_id(commands._wb_stock_supplies_chat_text(summary), job.job_id), {}
    if job.task_id == "ozon-stock-supply-monitor":
        return _with_job_id(commands._ozon_stock_supplies_chat_text(summary), job.job_id), {}
    if job.task_id == "marketplace-period-report":
        return _period_report_result(job, summary), {}
    if job.task_id in {"ozon-production-work-plan", "wb-production-work-plan"}:
        return _production_plan_result(job, summary)
    if job.task_id == "ozon-elastic-plan":
        return _ozon_elastic_plan_result(job, summary, approval=approval)
    if job.task_id == "ozon-actions-optimizer-plan":
        return _ozon_actions_plan_result(job, summary, approval=approval)
    if job.task_id == "wb-actions-discount-plan":
        return _wb_actions_plan_result(job, summary, update=update, approval=approval)
    if job.task_id == "wb-best-price-action-plan":
        return _wb_best_price_action_plan_result(job, summary, approval=approval)
    if job.task_id in {"ozon-inbox", "wb-inbox"}:
        return _inbox_plan_result(job, summary, approval=approval, data_dir=data_dir)
    if job.task_id == "wb-actions-discount-apply":
        return _wb_actions_apply_result(job, summary)
    if job.task_id == "wb-best-price-action-apply":
        return _wb_best_price_action_apply_result(job, summary)
    if job.task_id in {
        "ozon-elastic-apply",
        "ozon-actions-optimizer-apply",
        "ozon-inbox-apply",
        "wb-inbox-apply",
    }:
        return _apply_result(job, summary), {}
    return _generic_job_text(job), {}


def _failed_job_text(job: JobRecord) -> str:
    title = _job_title(job)
    return "\n".join(
        [
            title,
            "",
            f"Итог: задача завершена со статусом `{job.status}`.",
            f"Job ID: `{job.job_id}`",
            f"Task: `{job.task_id}`",
            f"Причина: `{_safe_job_error(job.error, fallback='результат не получен')}`",
            "",
            "Повторный apply автоматически не выполнялся.",
        ]
    )


def _generic_job_text(job: JobRecord) -> str:
    result_status = str(job.result.get("status") or job.result.get("summary", {}).get("overall_status") or "")
    lines = [
        _job_title(job),
        "",
        f"Итог: задача завершена со статусом `{job.status}`.",
        f"Job ID: `{job.job_id}`",
        f"Task: `{job.task_id}`",
    ]
    if result_status:
        lines.append(f"Result status: `{result_status}`")
    if job.error:
        lines.append(f"Ошибка: `{_safe_job_error(job.error)}`")
    artifacts = _job_artifacts(job)
    if artifacts.get("report"):
        lines.extend(["", "Файл полного отчета будет прикреплен, если он проходит safety-фильтр."])
    return "\n".join(lines)


def _liquidation_control_result(job: JobRecord, summary: dict[str, Any]) -> str:
    ozon = _dict(summary.get("ozon"))
    wb = _dict(summary.get("wb"))
    period = _dict(summary.get("period"))
    return "\n".join(
        [
            "Ежедневный контроль распродажи",
            "",
            f"Период: `{period.get('date_from') or 'н/д'} - {period.get('date_to') or 'н/д'}`; текущий незавершённый день исключён.",
            "",
            "Ozon:",
            f"- когорта: `{_int(ozon.get('cohort'))}` SKU; остаток: `{_int(ozon.get('stock_products'))}` товаров / `{_int(ozon.get('stock_physical_items'))}` изделий;",
            f"- Seller API заказы: `{_int(ozon.get('seller_order_units'))}`; CPC-заказы: `{_int(ozon.get('ad_orders'))}`; расход: `{_money(ozon.get('ad_spend'))}`;",
            f"- кандидаты hard stop: `{_int(ozon.get('hard_stop_candidates'))}`; расхождения minimum: `{_int(ozon.get('min_price_mismatches'))}`.",
            "",
            "Wildberries:",
            f"- когорта: `{_int(wb.get('cohort'))}` nmID; остаток: `{_int(wb.get('stock_goods'))}` товаров / `{_int(wb.get('stock_physical_items'))}` изделий;",
            f"- Statistics API заказы: `{_int(wb.get('seller_order_units'))}`; CPC-заказы: `{_int(wb.get('ad_orders'))}`; расход: `{_money(wb.get('ad_spend'))}`;",
            f"- кандидаты hard stop: `{_int(wb.get('hard_stop_candidates'))}`; второй ценовой шаг: `{_int(wb.get('second_price_stage_pending'))}`.",
            "",
            "Отключения рекламы, цены и акции автоматически не менялись. Stop-review имеет контрольную сумму и требует отдельного согласования.",
            f"Run ID: `{summary.get('run_id') or 'н/д'}`",
            f"Job ID: `{job.job_id}`",
        ]
    )


def _wb_liquidation_stage2_result(job: JobRecord, summary: dict[str, Any]) -> str:
    return "\n".join(
        [
            "WB: второй ценовой шаг распродажи",
            "",
            f"Точная когорта: `{_int(summary.get('cohort_rows'))}`; требуется изменение: `{_int(summary.get('ready_for_owner_review'))}`; уже цель: `{_int(summary.get('already_at_target'))}`; блокированы: `{_int(summary.get('blocked_rows'))}`; drift: `{_int(summary.get('drift_rows'))}`.",
            f"Checksum: `{summary.get('actions_checksum') or 'н/д'}`.",
            "",
            "Это fresh dry-run. Скидки, цены, minimum и акции в WB не менялись.",
            f"Run ID: `{summary.get('run_id') or 'н/д'}`",
            f"Job ID: `{job.job_id}`",
        ]
    )


def _wb_liquidation_stage2_apply_result(job: JobRecord, summary: dict[str, Any]) -> str:
    verify = _dict(summary.get("verify"))
    applied = _dict(summary.get("applied"))
    return "\n".join(
        [
            "WB: второй ценовой шаг распродажи",
            "",
            f"Статус: `{summary.get('overall_status') or 'warning'}`; отправлено: `{_int(applied.get('payload_rows_count'))}`; подтверждено: `{_int(verify.get('matched_rows'))}` / `{_int(verify.get('expected_rows'))}`.",
            f"Drift: `{_int(_dict(summary.get('drift')).get('drift_rows'))}`; upload ID: `{applied.get('upload_id') or 'н/д'}`.",
            "",
            "Базовые и минимальные цены не изменялись.",
            f"Run ID: `{summary.get('run_id') or 'н/д'}`",
            f"Job ID: `{job.job_id}`",
        ]
    )


def _ozon_stars_control_result(job: JobRecord, summary: dict[str, Any]) -> str:
    fees = _dict(summary.get("stars_fees"))
    return "\n".join([f"Ozon Stars: контроль `{_int(summary.get('window_days'))}` дней", "", f"Изменение выкупов: `{summary.get('buyout_units_change_pct') if summary.get('buyout_units_change_pct') is not None else 'н/д'}%`; оборота: `{summary.get('gross_change_pct') if summary.get('gross_change_pct') is not None else 'н/д'}%`.", f"Новые StarsMembership по заказам после отключения: `{_int(fees.get('post_deactivation_order_rows'))}` строк / `{_money(fees.get('post_deactivation_order_amount'))}`.", f"Решение: `{summary.get('decision') or 'н/д'}`.", "Повторное подключение автоматически не выполнялось.", f"Run ID: `{summary.get('run_id') or 'н/д'}`", f"Job ID: `{job.job_id}`"])


def _ozon_lk_state_result(job: JobRecord, summary: dict[str, Any]) -> str:
    return "\n".join(["Ozon LK: изменилось состояние", "", f"Было: `{summary.get('previous_state') or 'не зафиксировано'}`.", f"Стало: `{summary.get('state') or 'unknown'}`.", f"Причина: `{summary.get('reason') or 'н/д'}`.", "", "Marketplace write не выполнялся.", f"Job ID: `{job.job_id}`"])


def _ozon_min_price_timer_result(job: JobRecord, summary: dict[str, Any]) -> str:
    return "\n".join(["Ozon: истекает защита минимальной цены", "", f"Проверено: `{_int(summary.get('products_checked'))}`; refresh-кандидатов: `{_int(summary.get('refresh_candidates'))}` в горизонте `{_int(summary.get('warning_days'))}` дней.", f"Checksum: `{summary.get('actions_checksum') or 'н/д'}`.", "", "Срок не продлевался автоматически. Цены и minimum не менялись.", f"Run ID: `{summary.get('run_id') or 'н/д'}`", f"Job ID: `{job.job_id}`"])


def _period_report_result(job: JobRecord, summary: dict[str, Any]) -> str:
    metrics = _dict(summary.get("metrics"))
    warnings = summary.get("warnings") if isinstance(summary.get("warnings"), list) else []
    marketplace = str(job.params.get("marketplace") or summary.get("marketplace") or "")
    title = "Ozon" if marketplace == "ozon" else "Wildberries"
    report_type = str(job.params.get("report_type") or summary.get("report_type") or "")
    report_label = {"short": "Краткий", "financial": "Финансовый", "full": "Полный отчёт"}.get(
        report_type,
        "Отчёт",
    )
    date_from = str(job.params.get("date_from") or summary.get("date_from") or "н/д")
    date_to = str(job.params.get("date_to") or summary.get("date_to") or "н/д")
    lines = [
        f"{title}: отчёт за период",
        "",
        f"Период: `{date_from} - {date_to}`.",
        f"Вид: `{report_label}`.",
        "",
        "Основные показатели:",
        f"- заказы: `{_int(metrics.get('orders'))}` товаров на `{_money(metrics.get('order_amount'))}`;",
        f"- выкупы: `{_int(metrics.get('buyout_units'))}` товаров / `{_int(metrics.get('physical_pieces'))}` физических изделий;",
        f"- возвратные события: `{_int(metrics.get('returns'))}`, отмены: `{_int(metrics.get('cancellations'))}`;",
        f"- продажи до расходов: `{_money(metrics.get('gross'))}`;",
        f"- расходы: `{_money(metrics.get('expenses'))}`;",
        f"- к выплате после расходов: `{_money(metrics.get('net'))}`;",
        f"- на одно физическое изделие: `{_money(metrics.get('net_per_piece'))}`.",
    ]
    if marketplace == "ozon":
        lines.extend(
            [
                "",
                "Возвратные события Ozon:",
                f"- отмены/невыкупы: `{_int(metrics.get('return_cancellations'))}`;",
                f"- возвраты после покупки: `{_int(metrics.get('client_returns'))}`;",
                f"- другие: `{_int(metrics.get('return_unknown'))}`.",
            ]
        )
    if warnings:
        lines.extend(["", "Ограничения:"])
        lines.extend(f"- {warning}" for warning in warnings[:5])
    lines.extend(
        [
            "",
            f"Run ID: `{summary.get('run_id') or 'н/д'}`",
            f"Job ID: `{job.job_id}`",
            "",
            "Полный файл отчёта приложен. Изменений в кабинете не выполнялось.",
        ]
    )
    return "\n".join(lines)


def _production_plan_result(job: JobRecord, summary: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    ozon = job.task_id.startswith("ozon-")
    marketplace = "Ozon" if ozon else "WB"
    metrics = _dict(summary.get("metrics"))
    totals_key = "cluster_totals" if ozon else "region_totals"
    label_key = "cluster" if ozon else "region"
    totals = summary.get(totals_key) if isinstance(summary.get(totals_key), list) else []
    warnings = summary.get("warnings") if isinstance(summary.get("warnings"), list) else []
    run_id = str(summary.get("run_id") or "")
    lines = [
        f"{marketplace}: в работу",
        "",
        f"Excel сформирован. Производство и поставка {marketplace} не запускались.",
        "",
        f"- товарных единиц: `{_int(metrics.get('marketplace_units'))}`;",
        f"- физических изделий: `{_int(metrics.get('physical_pieces'))}`;",
        f"- артикулов: `{_int(metrics.get('articles'))}`;",
        f"- выбрано кластеров: `{_int(metrics.get('selected_clusters'))}`;",
        f"- строк контроля: `{_int(metrics.get('control_rows'))}`.",
    ]
    if str(job.params.get("mode") or "") == "capacity":
        lines.append(
            f"- не распределено мощности: `{_int(metrics.get('unused_capacity_physical'))}` физических изделий."
        )
    if totals:
        lines.extend(["", "По кластерам:"])
        for row in totals[:10]:
            if isinstance(row, dict):
                lines.append(
                    f"- #{_int(row.get('priority'))} {row.get(label_key)}: "
                    f"`{_int(row.get('marketplace_units'))}` ед. / "
                    f"`{_int(row.get('physical_pieces'))}` изделий."
                )
    if warnings:
        lines.extend(["", "Ограничения:"])
        lines.extend(f"- {warning}" for warning in warnings[:5])
    lines.extend(["", f"Run ID: `{run_id or 'н/д'}`", f"Job ID: `{job.job_id}`", "", "Проверьте Excel."])
    prefix = "ozwp" if ozon else "wbwp"
    markup = {}
    if run_id:
        markup = {
            "inline_keyboard": [
                [{"text": "Утвердить в работу", "callback_data": f"{prefix}_approve:{run_id}"}],
                [{"text": "Отклонить", "callback_data": f"{prefix}_reject:{run_id}"}],
            ]
        }
    return "\n".join(lines), markup


def _runtime_approval_markup(approval: ApprovalRecord | None) -> dict[str, Any]:
    if approval is None:
        return {}
    token = approval_callback_token(approval.approval_id)
    if approval.status == "pending_review":
        return {
            "inline_keyboard": [
                [
                    {"text": "Согласовать", "callback_data": f"apa:{token}"},
                    {"text": "Отклонить", "callback_data": f"apr:{token}"},
                ]
            ]
        }
    if approval.status == "approved":
        return {"inline_keyboard": [[{"text": "Применить", "callback_data": f"app:{token}"}]]}
    if approval.status in {"applied", "applying_unknown"}:
        return {"inline_keyboard": [[{"text": "Проверить", "callback_data": f"apv:{token}"}]]}
    return {}


def _ozon_elastic_plan_result(
    job: JobRecord,
    result: dict[str, Any],
    *,
    approval: ApprovalRecord | None,
) -> tuple[str, dict[str, Any]]:
    summary = _dict(result.get("summary"))
    run_id = str(result.get("run_id") or "")
    changed = sum(
        _int(summary.get(key))
        for key in ("add_to_action", "update_action_price_with_changed_price", "deactivate_from_action")
    )
    lines = [
        "Ozon Elastic",
        "",
        f"Итог: свежий dry-run построен со статусом `{result.get('overall_status') or 'н/д'}`.",
        f"Run ID: `{run_id or 'н/д'}`",
        f"Job ID: `{job.job_id}`",
        f"Акция: `{summary.get('action_name') or 'н/д'}` / `{summary.get('action_id') or 'н/д'}`",
        "",
        f"- активных товаров: `{_int(summary.get('active_rows'))}`;",
        f"- кандидатов: `{_int(summary.get('candidate_rows'))}`;",
        f"- добавить: `{_int(summary.get('add_to_action'))}`;",
        f"- изменить цену: `{_int(summary.get('update_action_price_with_changed_price'))}`;",
        f"- снять: `{_int(summary.get('deactivate_from_action'))}`;",
        f"- заблокировано: `{_int(summary.get('blocked'))}`.",
        "",
        "Изменений в Ozon не выполнялось.",
    ]
    markup = _runtime_approval_markup(approval) if changed and run_id else {}
    return "\n".join(lines), markup


def _ozon_actions_plan_result(
    job: JobRecord,
    result: dict[str, Any],
    *,
    approval: ApprovalRecord | None,
) -> tuple[str, dict[str, Any]]:
    summary = _dict(result.get("summary"))
    run_id = str(result.get("run_id") or "")
    changed = sum(
        _int(summary.get(key))
        for key in ("recommended_add", "recommended_update", "recommended_switch_review")
    )
    lines = [
        "Ozon все акции",
        "",
        f"Итог: fresh dry-run построен со статусом `{result.get('overall_status') or 'н/д'}`.",
        f"Run ID: `{run_id or 'н/д'}`",
        f"Job ID: `{job.job_id}`",
        "",
        f"- доступных акций: `{_int(summary.get('actions_total'))}`;",
        f"- товаров в расчёте: `{_int(summary.get('products_with_action_offers'))}`;",
        f"- оставить текущую: `{_int(summary.get('recommended_keep'))}`;",
        f"- добавить: `{_int(summary.get('recommended_add'))}`;",
        f"- обновить цену: `{_int(summary.get('recommended_update'))}`;",
        f"- переключить: `{_int(summary.get('recommended_switch_review'))}`;",
        f"- пропустить: `{_int(summary.get('recommended_skip'))}`.",
        "",
        "Изменений в Ozon не выполнялось.",
    ]
    markup = _runtime_approval_markup(approval) if changed and run_id else {}
    return "\n".join(lines), markup


def _wb_actions_plan_result(
    job: JobRecord,
    result: dict[str, Any],
    *,
    update: TelegramUpdateRecord | None,
    approval: ApprovalRecord | None,
) -> tuple[str, dict[str, Any]]:
    summary = _dict(result.get("summary"))
    run_id = str(result.get("run_id") or "")
    changed = _int(summary.get("changed_rows") or summary.get("to_change"))
    scheme = str(summary.get("scheme") or job.params.get("scheme_text") or "70-55-55")
    callback_data = str(update.payload.get("callback_data") or "") if update else ""
    manual = callback_data.startswith("wbam_confirm:") or scheme != "70-55-55"
    title = "Ручная акция" if manual else "WB акции 70-55-55"
    artifacts = _dict(result.get("artifacts"))

    from seller_agent.bot import commands

    report_stats = commands._wb_actions_report_stats(artifacts.get("csv"))
    if not report_stats.get("available"):
        lines = [
            title,
            "",
            f"Итог: fresh dry-run построен со статусом `{result.get('overall_status') or 'н/д'}`.",
            f"Схема: `{scheme}`",
            f"Run ID: `{run_id or 'н/д'}`",
            f"Job ID: `{job.job_id}`",
            "",
            f"- всего товаров: `{_int(summary.get('total_goods'))}`;",
            f"- изменить скидку: `{changed}`;",
            f"- без изменения: `{_int(summary.get('no_change'))}`.",
            "",
            "Разбивка участия и скидок не подтверждена: CSV полного расчета отсутствует или поврежден.",
            "Скидки в WB не изменялись.",
        ]
        markup = _runtime_approval_markup(approval) if changed and run_id else {}
        return "\n".join(lines), markup

    current_participating = commands._wb_report_stat(report_stats, "current_participating")
    current_not_participating = commands._wb_report_stat(report_stats, "current_not_participating")
    offered_in_active_promos = commands._wb_report_stat(report_stats, "offered_in_active_promos")
    offered_not_participating = commands._wb_report_stat(report_stats, "offered_not_participating")
    outside_active_promos = commands._wb_report_stat(report_stats, "outside_active_promos")
    eligible_after = commands._wb_report_stat(report_stats, "eligible_after")
    excluded_after = commands._wb_report_stat(report_stats, "excluded_after")
    newly_participating_after = commands._wb_report_stat(report_stats, "newly_participating_after")
    not_participating_after = commands._wb_report_stat(report_stats, "not_participating_after")
    step_limited = commands._wb_report_stat(report_stats, "step_limited")
    current_distribution = report_stats.get("current_participating_discount_distribution", {})
    after_distribution = report_stats.get("participating_after_discount_distribution", {})
    outside_after_distribution = report_stats.get("not_participating_after_discount_distribution", {})
    excluded_reasons = report_stats.get("excluded_reason_distribution", {})

    threshold_text = scheme.split("-", maxsplit=1)[0]
    lines = [
        title,
        "",
        f"Итог: fresh dry-run построен со статусом `{result.get('overall_status') or 'н/д'}`.",
        f"Схема: `{scheme}`",
        f"Run ID: `{run_id or 'н/д'}`",
        f"Job ID: `{job.job_id}`",
        "",
        "Сейчас:",
        f"- всего товаров в магазине: `{_int(summary.get('total_goods'))}`;",
        f"- доступны активные акции: `{_int(offered_in_active_promos)}`;",
        f"- участвуют в акциях: `{_int(current_participating)}`;",
        f"- не участвуют в акциях: `{_int(current_not_participating)}`;",
        f"- из них доступны акциям, но не участвуют: `{_int(offered_not_participating)}`;",
        f"- без доступных активных акций: `{_int(outside_active_promos)}`.",
        "",
        "Скидки товаров, которые сейчас участвуют:",
        *commands._wb_discount_distribution_lines(current_distribution),
        "",
        "Что изменится:",
        f"- снимутся с текущих акций: `{_int(excluded_after)}`;",
        *commands._wb_exclusion_reason_lines(excluded_reasons, threshold=threshold_text),
        f"- начнут участвовать: `{_int(newly_participating_after)}`;",
        f"- всего изменить скидку: `{changed}`;",
        f"- без изменения: `{_int(summary.get('no_change'))}`.",
        "",
        "После применения целевых параметров:",
        f"- будут участвовать в акциях: `{_int(eligible_after)}`;",
        f"- не будут участвовать в акциях: `{_int(not_participating_after)}`.",
        "",
        "Скидки товаров, которые останутся в акциях:",
        *commands._wb_discount_distribution_lines(after_distribution),
        "",
        "Скидки товаров, которые не будут участвовать:",
        *commands._wb_discount_distribution_lines(outside_after_distribution),
        "",
        "Техническая информация:",
        f"- активных акций: `{_int(summary.get('active_promos'))}`;",
        f"- будущих акций: `{_int(summary.get('future_promos'))}`;",
        f"- не достигнут целевой скидки за один безопасный upload: `{_int(step_limited)}`.",
    ]
    lines.extend(
        [
            "",
            "Скидки в WB не изменялись. Полный расчёт приложен.",
        ]
    )
    markup = _runtime_approval_markup(approval) if changed and run_id else {}
    return "\n".join(lines), markup


def _wb_best_price_action_plan_result(
    job: JobRecord,
    result: dict[str, Any],
    *,
    approval: ApprovalRecord | None,
) -> tuple[str, dict[str, Any]]:
    summary = _dict(result.get("summary"))
    run_id = str(result.get("run_id") or "")
    outside_discount = _int(result.get("outside_action_discount"))
    safe_to_apply = bool(result.get("safe_to_apply"))
    changed = _int(summary.get("to_change_discount"))
    lines = [
        "Акции от минимальной цены",
        "",
        f"Итог: fresh dry-run построен со статусом `{result.get('overall_status') or 'н/д'}`.",
        f"Run ID: `{run_id or 'н/д'}`",
        f"Job ID: `{job.job_id}`",
        f"Скидка вне подходящих акций: `{outside_discount}%`.",
        "",
        "Сейчас:",
        f"- товаров в расчёте: `{_int(summary.get('scope_total'))}`;",
        f"- участвуют в активных акциях: `{_int(summary.get('currently_participating'))}`;",
        f"- предложены хотя бы одной акцией: `{_int(summary.get('offered_any_action'))}`;",
        f"- не предложены активным акциям: `{_int(summary.get('not_offered'))}`.",
        "",
        "После применения:",
        f"- будут участвовать в лучшей допустимой акции: `{_int(summary.get('eligible_any_action'))}`;",
        f"- останутся вне акций: `{_int(summary.get('outside_action'))}`;",
        f"- из них доступны акциям, но цена ниже минимума: "
        f"`{_int(summary.get('offered_but_below_minimum'))}`;",
        f"- скидка изменится: `{changed}`;",
        f"- скидка не изменится: `{_int(summary.get('no_change_discount'))}`.",
        "",
        "Целевые скидки:",
        *_discount_distribution_lines(summary.get("target_discount_distribution")),
        "",
        "Safety:",
        f"- целевая цена ниже минимума: `{_int(summary.get('target_below_minimum'))}`;",
        f"- небезопасно для одного upload: `{_int(summary.get('unsafe_single_upload'))}`.",
        "",
        "Скидки в WB не изменялись. Полный HTML-отчёт приложен.",
    ]
    markup: dict[str, Any] = {}
    if safe_to_apply and changed > 0 and run_id:
        markup = _runtime_approval_markup(approval)
    elif not safe_to_apply:
        lines.extend(
            [
                "",
                "Apply заблокирован. Укажите другую скидку и постройте новый dry-run.",
            ]
        )
    else:
        lines.extend(["", "Изменений к применению нет."])
    return "\n".join(lines), markup


def _wb_best_price_action_apply_result(
    job: JobRecord,
    summary: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    minimum = _dict(summary.get("minimum_verify"))
    applied = _dict(summary.get("apply"))
    price = _dict(summary.get("price_verify"))
    action = _dict(summary.get("action_verify"))
    drift = _dict(summary.get("drift"))
    lines = [
        "Акции от минимальной цены apply",
        "",
        f"Итог: apply завершён со статусом `{summary.get('overall_status') or job.status}`.",
        f"Job ID: `{job.job_id}`",
        f"Approved plan: `{job.params.get('plan_run_id') or 'н/д'}`",
        f"Apply run: `{summary.get('run_id') or 'н/д'}`",
        "",
        "Проверка перед записью:",
        f"- минимальные цены: `{_int(minimum.get('verified'))}` / `{_int(minimum.get('target'))}`;",
        f"- drift: `{_int(drift.get('drift_rows_count'))}` товаров.",
        "",
        "Применение:",
        f"- отправлено строк: `{_int(applied.get('submitted_rows'))}`;",
        f"- upload ID: `{applied.get('upload_id') or 'н/д'}`;",
        f"- upload verify: `{applied.get('upload_verify_status') or 'н/д'}`.",
        "",
        "Итоговая проверка:",
        f"- цены и скидки: `{_int(price.get('matched_rows'))}` / "
        f"`{_int(price.get('expected_rows'))}`;",
        f"- выбранные акции: `{_int(action.get('confirmed'))}` / `{_int(action.get('target'))}`.",
        "",
        "Полный apply/verify отчёт приложен.",
    ]
    return "\n".join(lines), {}


def _discount_distribution_lines(value: Any) -> list[str]:
    distribution = value if isinstance(value, dict) else {}
    if not distribution:
        return ["- данных нет."]
    return [
        f"- скидка `{discount}%`: `{_int(count)}` товаров;"
        for discount, count in sorted(
            distribution.items(),
            key=lambda item: int(str(item[0])),
        )
    ]


def _inbox_plan_result(
    job: JobRecord,
    summary: dict[str, Any],
    *,
    approval: ApprovalRecord | None,
    data_dir: Path,
) -> tuple[str, dict[str, Any]]:
    ozon = job.task_id == "ozon-inbox"
    marketplace = "Ozon" if ozon else "WB"
    reviews = _dict(summary.get("reviews"))
    run_id = str(summary.get("run_id") or "")
    actions_count = _int(summary.get("actions_count"))
    lines = [
        f"{marketplace} входящие",
        "",
        f"Итог: fresh dry-run построен со статусом `{summary.get('overall_status') or 'н/д'}`.",
        f"Run ID: `{run_id or 'н/д'}`",
        f"Job ID: `{job.job_id}`",
        "",
        f"- отзывы/вопросы к действию: `{_int(reviews.get('actions_count'))}`;",
        f"- оценок по товарам: `{_int(summary.get('product_rating_rows_count'))}`;",
        f"- низких оценок 1-3: `{_int(summary.get('low_rating_product_rows_count'))}`;",
        f"- всего действий в пакете: `{actions_count}`.",
        "",
        "Ответы не отправлялись. Полный пакет приложен.",
    ]
    if ozon:
        messenger = _dict(summary.get("messenger"))
        lines.insert(-2, f"- непрочитано в Messenger API: `{_int(messenger.get('total_unread_count'))}`.")
    else:
        notifications = _dict(summary.get("wb_notifications"))
        items = notifications.get("items") if isinstance(notifications.get("items"), list) else []
        lines.insert(-2, f"- прочитано WB уведомлений: `{len(items)}`.")
    markup = _runtime_approval_markup(approval) if actions_count and run_id else {}
    _ = data_dir
    return "\n".join(lines), markup


def _apply_result(job: JobRecord, summary: dict[str, Any]) -> str:
    titles = {
        "ozon-elastic-apply": "Ozon Elastic apply",
        "ozon-actions-optimizer-apply": "Ozon все акции apply",
        "wb-actions-discount-apply": "WB акции apply",
        "ozon-inbox-apply": "Ozon входящие apply",
        "wb-inbox-apply": "WB входящие apply",
    }
    verify = _dict(summary.get("verify"))
    drift = _dict(summary.get("drift"))
    lines = [
        titles.get(job.task_id, "Apply"),
        "",
        f"Итог: apply завершён со статусом `{summary.get('overall_status') or job.status}`.",
        f"Job ID: `{job.job_id}`",
        f"Approved source: `{summary.get('approved_plan_run_id') or job.params.get('plan_run_id') or job.params.get('source_run_id') or 'н/д'}`",
        f"Apply run: `{summary.get('run_id') or 'н/д'}`",
    ]
    if verify:
        lines.extend(
            [
                "",
                "Проверка:",
                f"- verify status: `{verify.get('status') or verify.get('overall_status') or 'н/д'}`;",
                f"- расхождения: `{len(verify.get('mismatches') or verify.get('price_mismatches') or [])}`.",
            ]
        )
    if drift:
        lines.extend(
            [
                "",
                f"Пропущено из-за drift: `{_int(drift.get('skipped_due_to_drift_count'))}` строк / "
                f"`{_int(drift.get('skipped_due_to_drift_product_count'))}` товаров.",
            ]
        )
    lines.extend(["", "Полный apply/verify отчёт приложен, если он сформирован."])
    return "\n".join(lines)


def _wb_actions_apply_result(
    job: JobRecord,
    summary: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    verify = _dict(summary.get("verify"))
    drift = _dict(summary.get("drift"))
    applied = _dict(summary.get("applied"))
    target_completion = _dict(summary.get("target_completion"))
    followup_rows = _int(target_completion.get("followup_required_rows"))
    transitions = (
        target_completion.get("transitions")
        if isinstance(target_completion.get("transitions"), list)
        else []
    )
    scheme = str(summary.get("scheme") or "")
    if followup_rows:
        conclusion = (
            "ближайший безопасный upload применён и проверен, "
            "но конечная схема достигнута не полностью"
        )
    else:
        conclusion = "конечная схема применена и проверена"
    lines = [
        "WB акции apply",
        "",
        f"Итог: {conclusion}.",
        f"Статус: `{summary.get('overall_status') or job.status}`.",
        f"Схема: `{scheme or 'н/д'}`",
        f"Job ID: `{job.job_id}`",
        f"Approved plan: `{summary.get('approved_plan_run_id') or job.params.get('plan_run_id') or 'н/д'}`",
        f"Apply run: `{summary.get('run_id') or 'н/д'}`",
        "",
        "Проверка ближайшего upload:",
        f"- отправлено строк: `{_int(applied.get('payload_rows_count'))}`;",
        f"- успешно проверено: `{_int(verify.get('success_rows'))}` / `{_int(verify.get('expected_rows'))}`;",
        f"- ошибок: `{_int(verify.get('failed_rows'))}`;",
        f"- пропущено из-за drift: `{_int(drift.get('skipped_due_to_drift_product_count'))}` товаров.",
        "",
        "Конечная схема:",
        f"- сразу достигли конечной скидки: `{_int(target_completion.get('direct_final_target_rows'))}`;",
        f"- остановились на безопасном промежуточном шаге: `{followup_rows}`;",
    ]
    for transition in transitions:
        if not isinstance(transition, dict):
            continue
        lines.append(
            "- переход "
            f"`{_int(transition.get('current_discount'))}% → "
            f"{_int(transition.get('uploaded_discount'))}% → "
            f"{_int(transition.get('final_discount'))}%`: "
            f"`{_int(transition.get('rows_count'))}` товаров."
        )
    markup: dict[str, Any] = {}
    if followup_rows:
        lines.extend(
            [
                "",
                f"Осталось довести до конечной скидки: `{followup_rows}` товаров.",
                "Следующая кнопка строит свежий dry-run. Скидки без нового подтверждения не изменяются.",
            ]
        )
        parts = scheme.split("-")
        if len(parts) == 3 and all(part.isdigit() and 0 <= int(part) <= 99 for part in parts):
            markup = {
                "inline_keyboard": [
                    [
                        {
                            "text": "Рассчитать следующий шаг",
                            "callback_data": f"wbam_confirm:{scheme}",
                        }
                    ]
                ]
            }
    lines.extend(["", "Полный apply/verify отчёт приложен, если он сформирован."])
    return "\n".join(lines), markup


def _job_summary(job: JobRecord) -> dict[str, Any]:
    summary = job.result.get("summary")
    return summary if isinstance(summary, dict) else {}


def _job_title(job: JobRecord) -> str:
    titles = {
        "status-preflight": "Статус проекта",
        "daily-morning-report": "Ежедневный отчёт",
        "marketplace-period-report": "Отчёт за период",
        "wb-parser-warehouse-analytics": "WB аналитика",
        "wb-stock-supply-monitor": "Остатки и поставки WB",
        "ozon-stock-supply-monitor": "Остатки и поставки Ozon",
        "ozon-production-work-plan": "Ozon: в работу",
        "wb-production-work-plan": "WB: в работу",
        "ozon-elastic-plan": "Ozon Elastic",
        "ozon-actions-optimizer-plan": "Ozon все акции",
        "wb-actions-discount-plan": "WB акции",
        "wb-best-price-action-plan": "Акции от минимальной цены",
        "wb-best-price-action-apply": "Акции от минимальной цены apply",
        "ozon-inbox": "Ozon входящие",
        "wb-inbox": "WB входящие",
    }
    return titles.get(job.task_id, "Runtime job")


def _safe_job_error(value: Any, *, fallback: str = "результат не получен") -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return fallback
    lowered = text.lower()
    sensitive_markers = (
        "authorization",
        "bearer ",
        "api-key",
        "api_key",
        "client-secret",
        "client_secret",
        "cookie",
        "password",
        "storage_state",
        "token",
    )
    if any(marker in lowered for marker in sensitive_markers):
        return "подробности скрыты safety-фильтром"
    return text.replace("`", "'")[:500]


def _with_job_id(text: str, job_id: str) -> str:
    if f"`{job_id}`" in text:
        return text
    return f"{text}\n\nJob ID: `{job_id}`"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _int(value: Any) -> int:
    try:
        return int(float(str(value or "0").replace(" ", "").replace(",", ".")))
    except (TypeError, ValueError):
        return 0


def _money(value: Any) -> str:
    try:
        return f"{float(value or 0):,.2f} руб.".replace(",", " ")
    except (TypeError, ValueError):
        return "н/д"


def _ozon_pricing_margin_result_text(job: JobRecord) -> str:
    summary = job.result.get("summary") if isinstance(job.result.get("summary"), dict) else {}
    metrics = summary.get("metrics") if isinstance(summary.get("metrics"), dict) else {}
    inputs = summary.get("inputs") if isinstance(summary.get("inputs"), dict) else {}
    ladder = summary.get("price_ladder") if isinstance(summary.get("price_ladder"), list) else []
    warnings = summary.get("warnings") if isinstance(summary.get("warnings"), list) else []
    if job.status not in {"success", "partial_success"} or not metrics:
        return "\n".join(
            [
                "Ozon: цены и маржа",
                "",
                f"Итог: расчёт завершён со статусом `{job.status}`.",
                f"Job ID: `{job.job_id}`",
                f"Причина: `{_safe_job_error(job.error, fallback='результат расчёта не получен')}`",
                "",
                "Изменений в Ozon не выполнялось.",
            ]
        )
    lines = [
        "Ozon: цены и маржа",
        "",
        f"Итог: read-only расчёт завершён со статусом `{summary.get('overall_status') or 'warning'}`.",
        f"Период: `{metrics.get('date_from')} - {metrics.get('date_to')}` ({metrics.get('period_days')} дней).",
        f"Себестоимость изделия: `{_rub(inputs.get('unit_cost'))}`; маржа: `{_rub(inputs.get('target_margin'))}`.",
        "",
        "Расходы Ozon:",
        f"- выкупы: `{metrics.get('buyout_units', 0)}` товаров / `{metrics.get('physical_pieces', 0)}` изделий;",
        f"- всего: `{_rub(metrics.get('total_expenses'))}`;",
        f"- на товар/комплект: `{_rub(metrics.get('expense_per_sold_product'))}`;",
        f"- на физическое изделие: `{_rub(metrics.get('expense_per_physical_item'))}`;",
        f"- логистика на товар/комплект: `{_rub(metrics.get('fixed_logistics_per_sold_product'))}`;",
        f"- переменная доля для расчёта: `{metrics.get('model_variable_rate_pct', 0)}%`.",
    ]
    if ladder:
        lines.extend(["", "Расчётные цены:"])
        for row in ladder[:5]:
            lines.append(
                f"- {row.get('pack_qty')} шт.: минимум `{_rub(row.get('minimum_price'))}`, "
                f"со скидкой `{_rub(row.get('discounted_price'))}`, базовая `{_rub(row.get('base_price'))}`."
            )
    if warnings:
        lines.extend(["", f"Ограничения: `{len(warnings)}`. Подробности находятся в Excel."])
    lines.extend(
        [
            "",
            f"Job ID: `{job.job_id}`",
            "Excel с расходами и расчётом по товарам будет прикреплён.",
            "Изменений в Ozon не выполнялось.",
        ]
    )
    return "\n".join(lines)


def _rub(value: Any) -> str:
    try:
        return f"{float(value or 0):,.2f} руб.".replace(",", " ")
    except (TypeError, ValueError):
        return "н/д"


def _job_artifacts(job: JobRecord) -> dict[str, str]:
    artifacts = job.result.get("artifacts")
    if isinstance(artifacts, dict):
        return {str(key): str(value) for key, value in artifacts.items()}
    summary = job.result.get("summary")
    if isinstance(summary, dict) and isinstance(summary.get("artifacts"), dict):
        return {str(key): str(value) for key, value in summary["artifacts"].items()}
    return {}


def _thread_id_from_update(update: TelegramUpdateRecord) -> int | None:
    return _int_or_none(update.payload.get("thread_id"))


def _int_or_none(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None
