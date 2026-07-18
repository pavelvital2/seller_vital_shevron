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
from seller_agent.core.job_models import JobRecord, TelegramUpdateRecord
from seller_agent.core.job_store import DEFAULT_RUNTIME_DB, JobStore


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
    update = job_store.get_telegram_update_by_job_id(job_id)
    if update is None:
        return JobNotificationResult(ok=False, job_id=job_id, blocked_reason="missing_telegram_update")
    chat_id = _int_or_none(update.chat_id)
    if chat_id is None:
        return JobNotificationResult(ok=False, job_id=job_id, blocked_reason="invalid_chat_id")

    thread_id = _thread_id_from_update(update)
    text_results = send_telegram_text(
        token=token,
        chat_id=chat_id,
        thread_id=thread_id,
        text=build_job_result_text(job),
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
    result_status = str(job.result.get("status") or job.result.get("summary", {}).get("overall_status") or "")
    lines = [
        "Runtime job",
        "",
        f"Итог: задача завершена со статусом `{job.status}`.",
        f"Job ID: `{job.job_id}`",
        f"Task: `{job.task_id}`",
    ]
    if result_status:
        lines.append(f"Result status: `{result_status}`")
    if job.error:
        lines.append(f"Ошибка: `{job.error}`")
    artifacts = _job_artifacts(job)
    if artifacts.get("report"):
        lines.extend(["", "Файл полного отчета будет прикреплен, если он проходит safety-фильтр."])
    return "\n".join(lines)


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
