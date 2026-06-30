from __future__ import annotations

from pathlib import Path
from typing import Any

from seller_agent.bot.telegram_runner import (
    ApiRequest,
    DocumentApiRequest,
    safe_report_attachment_paths,
    send_telegram_document,
    send_telegram_text,
    telegram_api_document_request,
    telegram_api_request,
)


def run_send_telegram_report(
    *,
    token: str,
    chat_id: int,
    report_path: Path,
    summary: str | None = None,
    summary_path: Path | None = None,
    data_dir: Path = Path("data"),
    thread_id: int | None = None,
    api_request: ApiRequest = telegram_api_request,
    document_api_request: DocumentApiRequest = telegram_api_document_request,
) -> dict[str, Any]:
    summary_text = _summary_text(summary=summary, summary_path=summary_path)
    safe_paths = safe_report_attachment_paths(
        artifacts={"report": str(report_path)},
        data_dir=data_dir,
    )
    if not safe_paths:
        return {
            "overall_status": "blocked",
            "blocked_reason": "report_file_not_safe_or_missing",
            "summary_sent": False,
            "document_sent": False,
            "report_path": str(report_path),
            "sent_messages": [],
            "sent_documents": [],
            "next_step": (
                "Проверь, что файл существует, лежит в data/runs или data/reports, "
                "имеет разрешенное расширение и не содержит секретных маркеров в пути."
            ),
        }

    text_results = send_telegram_text(
        token=token,
        chat_id=chat_id,
        thread_id=thread_id,
        text=summary_text,
        api_request=api_request,
    )
    text_ok = bool(text_results) and all(result.ok for result in text_results)
    if not text_ok:
        return {
            "overall_status": "error",
            "blocked_reason": "summary_send_failed",
            "summary_sent": False,
            "document_sent": False,
            "report_path": str(safe_paths[0]),
            "sent_messages": [result.__dict__ for result in text_results],
            "sent_documents": [],
        }

    document_result = send_telegram_document(
        token=token,
        chat_id=chat_id,
        thread_id=thread_id,
        document_path=safe_paths[0],
        document_api_request=document_api_request,
    )
    return {
        "overall_status": "ok" if document_result.ok else "error",
        "blocked_reason": "" if document_result.ok else "document_send_failed",
        "summary_sent": True,
        "document_sent": document_result.ok,
        "report_path": str(safe_paths[0]),
        "sent_messages": [result.__dict__ for result in text_results],
        "sent_documents": [document_result.__dict__],
    }


def _summary_text(*, summary: str | None, summary_path: Path | None) -> str:
    if summary_path:
        text = summary_path.read_text(encoding="utf-8").strip()
        if text:
            return text
    if summary and summary.strip():
        return summary.strip()
    return "Отчет готов. Полный файл прикреплен ниже."
