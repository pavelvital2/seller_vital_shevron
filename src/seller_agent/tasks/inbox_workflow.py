from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import re
import subprocess
from typing import Any

from seller_agent.config import AppCredentials
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.marketplaces.ozon.adapter import OzonSellerAdapter
from seller_agent.reports.writer import ensure_dir, write_json
from seller_agent.safety.approvals import (
    action_rows_checksum,
    apply_marker_for,
    assert_apply_not_repeated,
    canonical_checksum,
    mark_approved_applied,
)
from seller_agent.tasks.approvals import run_approvals_close
from seller_agent.tasks.reviews_questions import (
    run_reviews_questions,
    run_reviews_questions_apply,
    run_reviews_questions_prepare_approved,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
IMPORTANT_NOTIFICATION_RE = re.compile(
    r"договор|тариф|комисс|логист|маркиров|карточ|блок|претенз|штраф|акци|продвиж|fbo|поставка|отгруз",
    re.IGNORECASE,
)


def _now() -> datetime:
    return datetime.now().astimezone()


def _safe_read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _safe_error(exc: BaseException) -> str:
    return str(exc).replace("\n", " ").replace("\r", " ")[:800]


def _valid_run_id(value: str, *, prefix: str) -> bool:
    text = str(value or "")
    return text.startswith(prefix) and all(char.isalnum() or char in {"_", "-"} for char in text)


def _extract_chat_id(chat: dict[str, Any]) -> str:
    nested = chat.get("chat") if isinstance(chat.get("chat"), dict) else {}
    return str(chat.get("chat_id") or chat.get("id") or nested.get("chat_id") or nested.get("id") or "").strip()


def _extract_messages(history: Any) -> list[dict[str, Any]]:
    if not isinstance(history, dict):
        return []
    for key in ("messages", "items", "history", "chat_history"):
        value = history.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    result = history.get("result")
    if isinstance(result, dict):
        return _extract_messages(result)
    if isinstance(result, list):
        return [row for row in result if isinstance(row, dict)]
    return []


def _extract_message_text(message: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("text", "message", "title"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    data = message.get("data")
    if isinstance(data, list):
        for item in data:
            if isinstance(item, str) and item.strip():
                parts.append(item.strip())
            elif isinstance(item, dict):
                for key in ("text", "message", "title", "value"):
                    value = item.get(key)
                    if isinstance(value, str) and value.strip():
                        parts.append(value.strip())
    elif isinstance(data, dict):
        for key in ("text", "message", "title", "value"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
    return " ".join(parts).strip()


def _message_user_type(message: dict[str, Any]) -> str:
    user = message.get("user") if isinstance(message.get("user"), dict) else {}
    return str(user.get("type") or message.get("user_type") or message.get("author_type") or "").strip()


def _message_id(message: dict[str, Any]) -> str:
    return str(message.get("message_id") or message.get("id") or message.get("uuid") or "").strip()


def _is_unread(message: dict[str, Any]) -> bool:
    value = message.get("is_read")
    if value is False:
        return True
    if str(value).lower() == "false":
        return True
    return False


def _draft_customer_chat_reply(text: str) -> str:
    lower = text.lower()
    if re.search(r"налич|есть|под заказ|сделать|изготов|пух|позывн|можно", lower):
        return (
            "Здравствуйте! К сожалению, наличие такого варианта сейчас не подтверждаю, "
            "а под заказ мы не изготавливаем. Можно выбрать доступные варианты из нашего магазина."
        )
    return ""


def _is_customer_tail_closing_text(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(text or "").strip().lower())
    normalized = normalized.strip(" .,!?:;\"'«»")
    return normalized in {
        "спасибо",
        "спасибо но нет",
        "спасибо, но нет",
        "нет спасибо",
        "нет, спасибо",
        "отказ",
        "отказываюсь",
        "не надо",
        "не нужно",
        "уже не нужно",
    }


def _classify_messenger_action(*, chat_id: str, message: dict[str, Any], previous_message: dict[str, Any] | None = None) -> dict[str, Any]:
    text = _extract_message_text(message)
    user_type = _message_user_type(message)
    message_id = _message_id(message)
    base = {
        "platform": "ozon",
        "source_type": "messenger",
        "chat_id": chat_id,
        "message_id": message_id,
        "from_message_id": message_id,
        "user_type": user_type,
        "source_text": text,
        "approved": False,
        "risk": "normal",
    }
    if user_type == "Customer":
        previous_user_type = _message_user_type(previous_message or {})
        if previous_user_type == "Seller" and _is_customer_tail_closing_text(text):
            return {
                **base,
                "action_type": "mark_chat_read",
                "state": "pending_owner_confirmation",
                "risk": "low",
                "draft_reply": "",
                "processing_status": "customer_tail_closing_no_reply",
                "notes": "Короткий закрывающий ответ покупателя после ответа продавца; закрыть без нового сообщения после approval.",
            }
        draft = _draft_customer_chat_reply(text)
        if draft:
            return {
                **base,
                "action_type": "send_chat_message",
                "state": "pending_owner_confirmation",
                "draft_reply": draft,
                "notes": "Покупательский чат. Ответ отправлять только после approval.",
            }
        return {
            **base,
            "action_type": "manual_chat_review",
            "state": "needs_owner_input",
            "draft_reply": "",
            "notes": "Покупательский чат требует ручного ответа: правило черновика не сработало.",
        }
    if user_type == "NotificationUser":
        important = bool(IMPORTANT_NOTIFICATION_RE.search(text))
        return {
            **base,
            "action_type": "mark_chat_read",
            "state": "pending_owner_confirmation",
            "risk": "normal" if important else "low",
            "draft_reply": "",
            "processing_status": "important_platform_message" if important else "platform_noise_or_info",
            "notes": "Важное уведомление Ozon." if important else "Информационное уведомление Ozon/шум.",
        }
    return {
        **base,
        "action_type": "mark_chat_read",
        "state": "pending_owner_confirmation",
        "risk": "low",
        "draft_reply": "",
        "processing_status": "service_or_unknown_sender",
        "notes": f"Сервисный или неизвестный отправитель Ozon: {user_type or 'н/д'}.",
    }


def _collect_ozon_messenger_actions(
    *,
    credentials: AppCredentials,
    run_dir: Path,
    limit: int,
) -> dict[str, Any]:
    raw_dir = ensure_dir(run_dir / "raw" / "ozon_messenger")
    if not credentials.ozon_seller:
        return {
            "status": "missing_credentials",
            "source": "Ozon Seller API /v3/chat/list /v3/chat/history",
            "actions": [],
            "error": "Ozon Seller credentials are not available.",
        }

    adapter = OzonSellerAdapter(credentials.ozon_seller)
    try:
        chat_pages: list[dict[str, Any]] = []
        cursor = ""
        total_unread_count: Any = None
        max_chats = min(max(limit, 1), 500)
        page_limit = min(max(max_chats, 1), 100)
        seen_cursors: set[str] = set()
        while len(chat_pages) * page_limit < max_chats:
            payload: dict[str, Any] = {"filter": {"chat_status": "All"}, "limit": page_limit}
            if cursor:
                payload["cursor"] = cursor
            page = adapter.post("/v3/chat/list", payload)
            chat_pages.append(page)
            if total_unread_count is None and isinstance(page, dict):
                total_unread_count = page.get("total_unread_count")
            next_cursor = str(page.get("cursor") or "") if isinstance(page, dict) else ""
            if not isinstance(page, dict) or not page.get("has_next") or not next_cursor or next_cursor in seen_cursors:
                break
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        chat_list = {
            "chats": [
                chat
                for page in chat_pages
                for chat in (page.get("chats") if isinstance(page, dict) and isinstance(page.get("chats"), list) else [])
            ][:max_chats],
            "has_next": bool(chat_pages[-1].get("has_next")) if chat_pages and isinstance(chat_pages[-1], dict) else False,
            "cursor": chat_pages[-1].get("cursor") if chat_pages and isinstance(chat_pages[-1], dict) else "",
            "total_unread_count": total_unread_count,
            "pages_checked": len(chat_pages),
        }
        write_json(raw_dir / "chat_list.json", chat_list)
        write_json(raw_dir / "chat_list_pages.json", chat_pages)
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "source": "Ozon Seller API /v3/chat/list",
            "actions": [],
            "error": _safe_error(exc),
        }

    chats = chat_list.get("chats") if isinstance(chat_list, dict) else []
    if not isinstance(chats, list):
        chats = chat_list.get("items") if isinstance(chat_list, dict) and isinstance(chat_list.get("items"), list) else []

    actions: list[dict[str, Any]] = []
    histories_checked = 0
    checked_chats = chats[: min(max(limit, 1), 500)]
    for chat in checked_chats:
        if not isinstance(chat, dict):
            continue
        chat_id = _extract_chat_id(chat)
        if not chat_id:
            continue
        try:
            history = adapter.post("/v3/chat/history", {"chat_id": chat_id, "limit": 50})
            write_json(raw_dir / f"history_{chat_id}.json", history)
            histories_checked += 1
        except Exception as exc:  # noqa: BLE001
            actions.append(
                {
                    "platform": "ozon",
                    "source_type": "messenger",
                    "chat_id": chat_id,
                    "action_type": "manual_chat_review",
                    "state": "needs_owner_input",
                    "risk": "normal",
                    "source_text": "",
                    "draft_reply": "",
                    "notes": f"Не удалось прочитать историю чата: {_safe_error(exc)}",
                }
            )
            continue
        messages = _extract_messages(history)
        for index, message in enumerate(messages):
            if not _is_unread(message):
                continue
            previous_message = messages[index + 1] if index + 1 < len(messages) else None
            actions.append(_classify_messenger_action(chat_id=chat_id, message=message, previous_message=previous_message))

    return {
        "status": "ok",
        "source": "Ozon Seller API /v3/chat/list /v3/chat/history",
        "total_unread_count": chat_list.get("total_unread_count") if isinstance(chat_list, dict) else None,
        "pages_checked": chat_list.get("pages_checked") if isinstance(chat_list, dict) else None,
        "chats_checked": len(checked_chats),
        "histories_checked": histories_checked,
        "actions": actions,
    }


def _collect_wb_notifications(*, run_dir: Path, limit: int) -> dict[str, Any]:
    raw_dir = ensure_dir(run_dir / "raw" / "wb_notifications")
    output_path = raw_dir / "wb_news_v2.json"
    script = PROJECT_ROOT / "scripts" / "notifications" / "wb_news_readonly.js"
    completed = subprocess.run(
        ["node", str(script), "--out", str(output_path), "--limit", str(min(max(limit, 1), 100))],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    data = _safe_read_json(output_path)
    if not isinstance(data, dict):
        return {
            "status": "error",
            "source": "WB LK news-v2 read-only",
            "items": [],
            "important_items": [],
            "raw_path": str(output_path),
            "error": (completed.stderr or completed.stdout or "WB notifications script did not produce JSON").strip()[:800],
        }
    items = data.get("items") if isinstance(data.get("items"), list) else []
    normalized_items = [item for item in items if isinstance(item, dict)]
    important = [
        item
        for item in normalized_items
        if IMPORTANT_NOTIFICATION_RE.search(f"{item.get('title') or ''} {item.get('text') or ''}")
    ]
    return {
        "status": data.get("status") or ("ok" if completed.returncode == 0 else "error"),
        "source": "WB LK news-v2 read-only",
        "url": data.get("url") or "https://seller.wildberries.ru/news-v2",
        "checked_at": data.get("checkedAt") or "",
        "items_count": len(normalized_items),
        "important_count": len(important),
        "items": normalized_items[: min(max(limit, 1), 100)],
        "important_items": important[: min(max(limit, 1), 100)],
        "raw_path": str(output_path),
        "blocker": data.get("blocker") or "",
    }


def _write_inbox_pending(
    *,
    data_dir: Path,
    run_id: str,
    marketplace: str,
    reviews_pending_id: str,
    messenger_actions: list[dict[str, Any]] | None = None,
    wb_notifications: dict[str, Any] | None = None,
) -> dict[str, str]:
    pending_id = f"{run_id}_pending"
    pending_dir = ensure_dir(data_dir / "pending" / pending_id)
    package = {
        "schema_version": "inbox-pending/v1",
        "package_type": f"{marketplace}_inbox",
        "status": "pending_owner_review",
        "run_id": run_id,
        "pending_id": pending_id,
        "marketplace": marketplace,
        "reviews_pending_id": reviews_pending_id,
        "messenger_actions": messenger_actions or [],
        "wb_notifications": wb_notifications or {},
        "actions_checksum": action_rows_checksum(messenger_actions or []),
    }
    write_json(pending_dir / "inbox_pending.json", package)
    write_json(pending_dir / "manifest.json", package)
    return {
        "pending_dir": str(pending_dir),
        "pending_manifest": str(pending_dir / "manifest.json"),
        "inbox_pending": str(pending_dir / "inbox_pending.json"),
    }


def _reviews_counts(summary: dict[str, Any]) -> dict[str, int]:
    actions_path = ((summary.get("artifacts") or {}).get("actions") if isinstance(summary.get("artifacts"), dict) else None)
    actions_data = _safe_read_json(Path(actions_path)) if actions_path else None
    actions = actions_data.get("actions") if isinstance(actions_data, dict) and isinstance(actions_data.get("actions"), list) else []
    counts = Counter(str(action.get("action_type") or "") for action in actions if isinstance(action, dict))
    return {key: int(value) for key, value in counts.items()}


def _review_actions(summary: dict[str, Any]) -> list[dict[str, Any]]:
    actions_path = ((summary.get("artifacts") or {}).get("actions") if isinstance(summary.get("artifacts"), dict) else None)
    actions_data = _safe_read_json(Path(actions_path)) if actions_path else None
    actions = actions_data.get("actions") if isinstance(actions_data, dict) and isinstance(actions_data.get("actions"), list) else []
    return [action for action in actions if isinstance(action, dict)]


def _product_rating_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for action in _review_actions(summary):
        if action.get("source_type") != "review":
            continue
        rating = str(action.get("rating") or "н/д").strip() or "н/д"
        platform = str(action.get("platform") or "").strip()
        offer_id = str(action.get("offer_id") or "").strip()
        sku = str(action.get("sku") or "").strip()
        title = str(action.get("product_title") or "без названия").strip()
        key = (platform, offer_id, sku, title, rating)
        if key not in grouped:
            grouped[key] = {
                "platform": platform,
                "offer_id": offer_id,
                "sku": sku,
                "product_title": title,
                "rating": rating,
                "count": 0,
                "action_counts": {},
            }
        row = grouped[key]
        row["count"] = int(row["count"]) + 1
        action_type = str(action.get("action_type") or "unknown")
        row["action_counts"][action_type] = int(row["action_counts"].get(action_type, 0)) + 1
    return sorted(
        grouped.values(),
        key=lambda row: (
            _rating_sort_key(str(row.get("rating") or "н/д")),
            str(row.get("platform") or ""),
            str(row.get("offer_id") or row.get("sku") or ""),
        ),
    )


def _question_action_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [action for action in _review_actions(summary) if action.get("source_type") == "question"]
    return sorted(
        rows,
        key=lambda row: (
            0 if row.get("action_type") == "question_answer" else 1,
            str(row.get("offer_id") or row.get("sku") or ""),
        ),
    )


def _low_rating_product_rows_count(rows: list[dict[str, Any]]) -> int:
    count = 0
    for row in rows:
        try:
            rating = int(float(str(row.get("rating") or "")))
        except ValueError:
            continue
        if rating <= 3:
            count += int(row.get("count") or 0)
    return count


def _rating_sort_key(value: str) -> tuple[int, str]:
    try:
        return (int(float(value)), value)
    except ValueError:
        return (-1, value)


def _format_product_rating_rows(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["- оценок по товарам нет"]
    lines: list[str] = []
    for index, row in enumerate(rows[:100], 1):
        actions = row.get("action_counts") if isinstance(row.get("action_counts"), dict) else {}
        action_text = ", ".join(f"{key}: {value}" for key, value in sorted(actions.items())) or "н/д"
        product_id = row.get("offer_id") or row.get("sku") or "без артикула"
        lines.append(
            f"{index}. `{row.get('platform') or 'н/д'}` / `{product_id}` / оценка `{row.get('rating') or 'н/д'}` "
            f"/ отзывов `{row.get('count') or 0}` - {row.get('product_title') or 'без названия'}; действия: {action_text}"
        )
    if len(rows) > 100:
        lines.append(f"- ... еще `{len(rows) - 100}` строк")
    return lines


def _format_question_action_rows(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["- вопросов покупателей нет"]
    lines: list[str] = []
    for index, row in enumerate(rows[:100], 1):
        product_id = row.get("offer_id") or row.get("sku") or "без артикула"
        question_text = str(row.get("source_text") or "").strip() or "текст не получен"
        draft_text = str(row.get("draft_text") or "").strip()
        lines.append(
            f"{index}. `{product_id}` - {row.get('product_title') or 'без названия'}; "
            f"действие: `{row.get('action_type') or 'н/д'}`; вопрос: {question_text}"
        )
        if draft_text:
            lines.append(f"   Черновик ответа: {draft_text}")
        else:
            lines.append("   Черновик ответа: требуется ручная проверка.")
    if len(rows) > 100:
        lines.append(f"- ... еще `{len(rows) - 100}` строк")
    return lines


def _build_ozon_report(
    *,
    run_id: str,
    reviews_summary: dict[str, Any],
    messenger_summary: dict[str, Any],
    artifacts: dict[str, str],
) -> str:
    review_counts = _reviews_counts(reviews_summary)
    product_rating_rows = _product_rating_rows(reviews_summary)
    question_rows = _question_action_rows(reviews_summary)
    messenger_actions = messenger_summary.get("actions") if isinstance(messenger_summary.get("actions"), list) else []
    verification_warnings = reviews_summary.get("verification_warnings") if isinstance(reviews_summary.get("verification_warnings"), list) else []
    messenger_counts = Counter(str(action.get("action_type") or "") for action in messenger_actions if isinstance(action, dict))
    important = [
        action
        for action in messenger_actions
        if isinstance(action, dict) and action.get("processing_status") == "important_platform_message"
    ]
    customer_replies = [
        action for action in messenger_actions if isinstance(action, dict) and action.get("action_type") == "send_chat_message"
    ]
    customer_mark_read = [
        action
        for action in messenger_actions
        if isinstance(action, dict) and action.get("action_type") == "mark_chat_read" and action.get("user_type") == "Customer"
    ]
    lines = [
        "# Ozon inbox: отзывы, вопросы и уведомления",
        "",
        f"Run ID: `{run_id}`",
        "",
        "Режим: dry-run/read-only. Ответы не отправлены, уведомления не отмечены прочитанными.",
        "",
        "## Сводка",
        "",
        f"- отзывы/вопросы требуют действий: `{reviews_summary.get('actions_count') or 0}`",
        f"- публичные ответы на отзывы: `{review_counts.get('public_review_reply', 0)}`",
        f"- отметить отзывы просмотренными: `{review_counts.get('mark_review_viewed', 0)}`",
        f"- вопросы покупателей: `{len(question_rows)}`",
        f"- автоответы на вопросы: `{review_counts.get('question_answer', 0)}`",
        f"- вопросы на ручную проверку: `{review_counts.get('manual_question_review', 0)}`",
        f"- оценок по конкретным товарам: `{sum(int(row.get('count') or 0) for row in product_rating_rows)}`",
        f"- низких оценок 1-3 по товарам: `{_low_rating_product_rows_count(product_rating_rows)}`",
        f"- Ozon Messenger действий: `{len(messenger_actions)}`",
        f"- покупательские ответы: `{messenger_counts.get('send_chat_message', 0)}`",
        f"- уведомления отметить прочитанными: `{messenger_counts.get('mark_chat_read', 0)}`",
        f"- ручная проверка чатов: `{messenger_counts.get('manual_chat_review', 0)}`",
        "",
        "## Покупательские чаты",
        "",
    ]
    if not customer_replies:
        lines.append("- черновиков ответов нет")
    for index, action in enumerate(customer_replies[:20], 1):
        lines.extend(
            [
                f"{index}. Chat `{action.get('chat_id')}`",
                f"   Покупатель: {action.get('source_text') or 'без текста'}",
                f"   Черновик: {action.get('draft_reply')}",
            ]
        )
    lines.extend(["", "### Закрыть без ответа", ""])
    if not customer_mark_read:
        lines.append("- чатов к закрытию без ответа нет")
    for index, action in enumerate(customer_mark_read[:20], 1):
        lines.extend(
            [
                f"{index}. Chat `{action.get('chat_id')}`",
                f"   Покупатель: {action.get('source_text') or 'без текста'}",
                f"   Причина: {action.get('notes') or 'закрыть без ответа'}",
            ]
        )
    lines.extend(["", "## Оценки по конкретным товарам", ""])
    lines.extend(_format_product_rating_rows(product_rating_rows))
    lines.extend(["", "## Вопросы покупателей", ""])
    lines.extend(_format_question_action_rows(question_rows))
    lines.extend(["", "## Важные уведомления Ozon", ""])
    if not important:
        lines.append("- важных уведомлений не найдено")
    for index, action in enumerate(important[:20], 1):
        lines.append(f"{index}. Chat `{action.get('chat_id')}`: {action.get('source_text') or 'без текста'}")
    lines.extend(["", "## Предупреждения проверки", ""])
    if not verification_warnings:
        lines.append("- предупреждений проверки нет")
    for warning in verification_warnings[:20]:
        if not isinstance(warning, dict):
            continue
        lines.append(f"- `{warning.get('type') or 'warning'}`: {warning.get('message') or warning}")
    lines.extend(["", "## Артефакты", ""])
    for key, value in sorted(artifacts.items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.append("")
    return "\n".join(lines)


def _build_wb_report(
    *,
    run_id: str,
    reviews_summary: dict[str, Any],
    wb_notifications: dict[str, Any],
    artifacts: dict[str, str],
) -> str:
    review_counts = _reviews_counts(reviews_summary)
    product_rating_rows = _product_rating_rows(reviews_summary)
    notification_items = wb_notifications.get("items") if isinstance(wb_notifications.get("items"), list) else []
    important_items = wb_notifications.get("important_items") if isinstance(wb_notifications.get("important_items"), list) else []
    lines = [
        "# WB inbox: отзывы, вопросы и уведомления",
        "",
        f"Run ID: `{run_id}`",
        "",
        "Режим: dry-run/read-only. Ответы не отправлены.",
        "",
        "## Сводка",
        "",
        f"- отзывы/вопросы требуют действий: `{reviews_summary.get('actions_count') or 0}`",
        f"- публичные ответы на отзывы: `{review_counts.get('public_review_reply', 0)}`",
        f"- ответы на вопросы WB: `{review_counts.get('question_answer', 0)}`",
        f"- вопросы на ручную проверку: `{review_counts.get('manual_question_review', 0)}`",
        f"- оценок по конкретным товарам: `{sum(int(row.get('count') or 0) for row in product_rating_rows)}`",
        f"- низких оценок 1-3 по товарам: `{_low_rating_product_rows_count(product_rating_rows)}`",
        f"- WB уведомления: `{wb_notifications.get('status') or 'н/д'}`",
        f"- WB новости/уведомления прочитано: `{len(notification_items)}`",
        f"- важных WB новостей/уведомлений: `{len(important_items)}`",
        "",
        "## Оценки по конкретным товарам",
        "",
        *_format_product_rating_rows(product_rating_rows),
        "",
        "## WB уведомления",
        "",
        f"Источник: `{wb_notifications.get('source') or 'н/д'}`",
        f"URL: `{wb_notifications.get('url') or 'н/д'}`",
        "",
        "### Важные",
        "",
    ]
    if not important_items:
        lines.append("- важных WB новостей/уведомлений не найдено")
    for index, item in enumerate(important_items[:20], 1):
        lines.append(f"{index}. {item.get('date') or 'без даты'} - {item.get('title') or 'без заголовка'} ({item.get('href') or 'без ссылки'})")
    lines.extend(["", "### Последние новости/уведомления", ""])
    if not notification_items:
        lines.append(
            f"- нет данных; статус `{wb_notifications.get('status') or 'н/д'}`, "
            f"причина: {wb_notifications.get('blocker') or wb_notifications.get('error') or 'не указана'}"
        )
    for index, item in enumerate(notification_items[:20], 1):
        lines.append(f"{index}. {item.get('date') or 'без даты'} - {item.get('title') or 'без заголовка'} ({item.get('href') or 'без ссылки'})")
    lines.extend(["", "## Артефакты", ""])
    for key, value in sorted(artifacts.items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.append("")
    return "\n".join(lines)


def run_ozon_inbox_triage(
    *,
    credentials: AppCredentials,
    data_dir: Path = Path("data"),
    run_id: str | None = None,
    limit: int = 300,
) -> dict[str, Any]:
    started_at = _now()
    run_id = run_id or f"ozon_inbox_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.date().isoformat() / run_id)

    reviews = run_reviews_questions(
        credentials=credentials,
        data_dir=data_dir,
        run_id=f"{run_id}_reviews",
        marketplace="ozon",
        limit=limit,
    )
    messenger = _collect_ozon_messenger_actions(credentials=credentials, run_dir=run_dir, limit=limit)
    artifacts: dict[str, str] = {
        "run_dir": str(run_dir),
        "reviews_report": str((reviews.get("artifacts") or {}).get("report") or ""),
        "reviews_summary": str((reviews.get("artifacts") or {}).get("summary") or ""),
        "messenger_raw": str(run_dir / "raw" / "ozon_messenger"),
        "run_manifest": str(run_dir / "manifest.json"),
    }
    artifacts.update(
        _write_inbox_pending(
            data_dir=data_dir,
            run_id=run_id,
            marketplace="ozon",
            reviews_pending_id=str(reviews.get("pending_id") or ""),
            messenger_actions=messenger.get("actions") if isinstance(messenger.get("actions"), list) else [],
        )
    )
    product_rating_rows = _product_rating_rows(reviews)
    report_text = _build_ozon_report(run_id=run_id, reviews_summary=reviews, messenger_summary=messenger, artifacts=artifacts)
    report_path = run_dir / "ozon_inbox_approval.md"
    report_path.write_text(report_text, encoding="utf-8")
    artifacts["report"] = str(report_path)

    actions_count = int(reviews.get("actions_count") or 0) + len(messenger.get("actions") or [])
    review_warnings = reviews.get("verification_warnings") if isinstance(reviews.get("verification_warnings"), list) else []
    summary = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "overall_status": "ok" if reviews.get("overall_status") in {"ok", "warning"} and messenger.get("status") == "ok" and not review_warnings else "warning",
        "mode": "dry_run",
        "marketplace": "ozon",
        "pending_id": f"{run_id}_pending",
        "reviews": reviews,
        "messenger": {k: v for k, v in messenger.items() if k != "actions"},
        "product_rating_rows_count": sum(int(row.get("count") or 0) for row in product_rating_rows),
        "low_rating_product_rows_count": _low_rating_product_rows_count(product_rating_rows),
        "actions_count": actions_count,
        "verification_warnings": review_warnings,
        "artifacts": artifacts,
    }
    write_json(run_dir / "summary.json", summary)
    summary["artifacts"]["summary"] = str(run_dir / "summary.json")
    write_json(run_dir / "summary.json", summary)
    write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=summary,
        task="ozon-inbox",
        mode="dry_run",
        risk="low",
        marketplaces=["ozon"],
        inputs={"limit": limit},
        pending_id=summary["pending_id"],
    )
    return summary


def run_wb_inbox_triage(
    *,
    credentials: AppCredentials,
    data_dir: Path = Path("data"),
    run_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    started_at = _now()
    run_id = run_id or f"wb_inbox_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.date().isoformat() / run_id)
    reviews = run_reviews_questions(
        credentials=credentials,
        data_dir=data_dir,
        run_id=f"{run_id}_reviews",
        marketplace="wb",
        limit=limit,
    )
    wb_notifications = _collect_wb_notifications(run_dir=run_dir, limit=limit)
    artifacts: dict[str, str] = {
        "run_dir": str(run_dir),
        "reviews_report": str((reviews.get("artifacts") or {}).get("report") or ""),
        "reviews_summary": str((reviews.get("artifacts") or {}).get("summary") or ""),
        "wb_notifications_raw": str(wb_notifications.get("raw_path") or ""),
        "run_manifest": str(run_dir / "manifest.json"),
    }
    artifacts.update(
        _write_inbox_pending(
            data_dir=data_dir,
            run_id=run_id,
            marketplace="wb",
            reviews_pending_id=str(reviews.get("pending_id") or ""),
            wb_notifications=wb_notifications,
        )
    )
    product_rating_rows = _product_rating_rows(reviews)
    report_text = _build_wb_report(run_id=run_id, reviews_summary=reviews, wb_notifications=wb_notifications, artifacts=artifacts)
    report_path = run_dir / "wb_inbox_approval.md"
    report_path.write_text(report_text, encoding="utf-8")
    artifacts["report"] = str(report_path)
    summary = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "overall_status": "ok" if reviews.get("overall_status") in {"ok", "warning"} and wb_notifications.get("status") == "ok" else "warning",
        "mode": "dry_run",
        "marketplace": "wb",
        "pending_id": f"{run_id}_pending",
        "reviews": reviews,
        "wb_notifications": wb_notifications,
        "product_rating_rows_count": sum(int(row.get("count") or 0) for row in product_rating_rows),
        "low_rating_product_rows_count": _low_rating_product_rows_count(product_rating_rows),
        "actions_count": int(reviews.get("actions_count") or 0),
        "artifacts": artifacts,
    }
    write_json(run_dir / "summary.json", summary)
    summary["artifacts"]["summary"] = str(run_dir / "summary.json")
    write_json(run_dir / "summary.json", summary)
    write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=summary,
        task="wb-inbox",
        mode="dry_run",
        risk="low",
        marketplaces=["wb"],
        inputs={"limit": limit},
        pending_id=summary["pending_id"],
    )
    return summary


def _load_inbox_pending(data_dir: Path, source_run_id: str, *, marketplace: str) -> dict[str, Any]:
    if not _valid_run_id(source_run_id, prefix=f"{marketplace}_inbox_"):
        raise ValueError(f"Invalid {marketplace} inbox run id: {source_run_id}")
    path = data_dir / "pending" / f"{source_run_id}_pending" / "inbox_pending.json"
    data = _safe_read_json(path)
    if not isinstance(data, dict):
        raise FileNotFoundError(f"Inbox pending package not found: {path}")
    if data.get("marketplace") != marketplace:
        raise RuntimeError(f"Inbox pending marketplace mismatch: {path}")
    return data


def _cleanup_inbox_apply_tail(
    *,
    data_dir: Path,
    source_run_id: str,
    pending: dict[str, Any],
    reviews_result: dict[str, Any],
    messenger_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    targets: list[tuple[str, str]] = []

    top_pending_id = f"{source_run_id}_pending"
    if top_pending_id:
        targets.append(("pending", top_pending_id))

    reviews_pending_id = str(pending.get("reviews_pending_id") or "")
    if reviews_pending_id:
        targets.append(("pending", reviews_pending_id))

    approved = reviews_result.get("approved") if isinstance(reviews_result.get("approved"), dict) else {}
    reviews_approved_id = str(approved.get("approved_id") or "")
    if reviews_approved_id:
        targets.append(("approved", reviews_approved_id))

    if isinstance(messenger_result, dict):
        messenger_approved = messenger_result.get("approved") if isinstance(messenger_result.get("approved"), dict) else {}
        messenger_approved_id = str(messenger_approved.get("approved_id") or "")
        if messenger_approved_id:
            targets.append(("approved", messenger_approved_id))

    closed: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, (kind, target_id) in enumerate(targets, 1):
        if not target_id or (kind, target_id) in seen:
            continue
        seen.add((kind, target_id))
        cleanup_run_id = f"{source_run_id}_cleanup_{index:02d}"
        try:
            result = run_approvals_close(
                data_dir=data_dir,
                target_id=target_id,
                kind=kind,
                closed_by="codex_auto",
                reason="inbox_apply_verified_cleanup",
                force=True,
                run_id=cleanup_run_id,
            )
            closed.append({"kind": kind, "id": target_id, "result": result})
        except Exception as exc:  # noqa: BLE001
            failed.append({"kind": kind, "id": target_id, "error": _safe_error(exc)})

    return {
        "status": "ok" if not failed else "warning",
        "closed_count": len(closed),
        "failed_count": len(failed),
        "closed": closed,
        "failed": failed,
    }


def _prepare_and_apply_reviews(
    *,
    credentials: AppCredentials,
    data_dir: Path,
    reviews_pending_id: str,
    source_run_id: str,
    marketplace: str,
) -> dict[str, Any]:
    if not reviews_pending_id:
        return {"status": "skipped", "reason": "no reviews pending id", "selected_actions_count": 0}
    approved_id = f"{reviews_pending_id}_approved_{_now().strftime('%Y%m%dT%H%M%S')}"
    try:
        approved = run_reviews_questions_prepare_approved(
            data_dir=data_dir,
            source_pending=reviews_pending_id,
            mode="all",
            approved_id=approved_id,
            approved_by="telegram_owner",
        )
    except RuntimeError as exc:
        if "No approvable" in str(exc):
            return {"status": "skipped", "reason": str(exc), "selected_actions_count": 0}
        raise
    package_path = Path(approved["artifacts"]["approved_package"])
    apply_result = run_reviews_questions_apply(
        credentials=credentials,
        approved_path=package_path,
        data_dir=data_dir,
        run_id=f"{source_run_id}_{marketplace}_reviews_apply",
        confirmed_by_user=True,
    )
    return {"status": apply_result.get("overall_status"), "approved": approved, "apply": apply_result}


def _write_ozon_messenger_approved(
    *,
    data_dir: Path,
    source_run_id: str,
    actions: list[dict[str, Any]],
) -> dict[str, str]:
    selected = [
        {**action, "approved": True, "state": "approved", "approved_by": "telegram_owner", "approved_at": _now().isoformat(timespec="seconds")}
        for action in actions
        if action.get("action_type") in {"send_chat_message", "mark_chat_read"}
        and (action.get("action_type") == "mark_chat_read" or str(action.get("draft_reply") or "").strip())
    ]
    if not selected:
        return {}
    approved_id = f"{source_run_id}_ozon_messenger_approved_{_now().strftime('%Y%m%dT%H%M%S')}"
    approved_dir = ensure_dir(data_dir / "approved" / approved_id)
    package_path = approved_dir / "approved_apply_plan.json"
    package = {
        "schema_version": "approval-package/v1",
        "package_type": "ozon_messenger",
        "status": "approved",
        "approved_id": approved_id,
        "source_run_id": source_run_id,
        "created_at": _now().isoformat(timespec="seconds"),
        "approved_by": "telegram_owner",
        "selected_actions_count": len(selected),
        "actions_checksum": action_rows_checksum(selected),
        "actions": selected,
    }
    write_json(package_path, package)
    return {"approved_id": approved_id, "approved_package": str(package_path), "selected_actions_count": str(len(selected))}


def _apply_ozon_messenger(
    *,
    credentials: AppCredentials,
    data_dir: Path,
    source_run_id: str,
    actions: list[dict[str, Any]],
    run_dir: Path,
) -> dict[str, Any]:
    approved = _write_ozon_messenger_approved(data_dir=data_dir, source_run_id=source_run_id, actions=actions)
    if not approved:
        return {"status": "skipped", "reason": "no approvable Ozon messenger actions", "artifacts": {}}

    package_path = Path(approved["approved_package"])
    raw_dir = ensure_dir(run_dir / "raw" / "ozon_messenger_apply")
    send_actions = [
        action
        for action in actions
        if action.get("action_type") == "send_chat_message" and str(action.get("draft_reply") or "").strip()
    ]
    if send_actions:
        script = PROJECT_ROOT / "scripts" / "messenger" / "ozon_send_messages_cdp.js"
        completed = subprocess.run(
            ["node", str(script), "--approved-path", str(package_path), "--run-dir", str(run_dir)],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            timeout=240,
            check=False,
        )
        send_result = _safe_read_json(raw_dir / "ozon_messenger_lk_send" / "send_result.json")
        if not isinstance(send_result, dict):
            send_result = {"ok": completed.returncode == 0, "blocker": (completed.stderr or completed.stdout or "").strip()[:1000]}
    else:
        send_result = {"ok": True, "skipped": True, "reason": "no approved send_chat_message actions"}

    mark_read: list[dict[str, Any]] = []
    if credentials.ozon_seller:
        adapter = OzonSellerAdapter(credentials.ozon_seller)
        for action in actions:
            if action.get("action_type") != "mark_chat_read":
                continue
            row = {"chat_id": action.get("chat_id"), "from_message_id": action.get("from_message_id"), "ok": False, "error": ""}
            try:
                response = adapter.post(
                    "/v2/chat/read",
                    {"chat_id": str(action.get("chat_id") or ""), "from_message_id": str(action.get("from_message_id") or "")},
                )
                row["ok"] = True
                row["response"] = response
            except Exception as exc:  # noqa: BLE001
                row["error"] = _safe_error(exc)
            mark_read.append(row)
    write_json(raw_dir / "mark_read_result.json", mark_read)
    ok = bool(send_result.get("ok", True)) and all(row.get("ok") for row in mark_read)
    return {
        "status": "ok" if ok else "blocked",
        "approved": approved,
        "send": send_result,
        "mark_read": mark_read,
        "artifacts": {
            "ozon_messenger_approved_package": str(package_path),
            "ozon_messenger_mark_read": str(raw_dir / "mark_read_result.json"),
        },
    }


def _build_apply_report(*, run_id: str, marketplace: str, reviews_result: dict[str, Any], messenger_result: dict[str, Any] | None, artifacts: dict[str, str]) -> str:
    lines = [
        f"# {marketplace.upper()} inbox apply result",
        "",
        f"Run ID: `{run_id}`",
        "",
        "## Итог",
        "",
        f"- reviews/questions apply: `{reviews_result.get('status')}`",
    ]
    if messenger_result is not None:
        lines.append(f"- messenger/notifications apply: `{messenger_result.get('status')}`")
    lines.extend(["", "## Артефакты", ""])
    for key, value in sorted(artifacts.items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.append("")
    return "\n".join(lines)


def run_ozon_inbox_apply(
    *,
    credentials: AppCredentials,
    data_dir: Path = Path("data"),
    source_run_id: str,
    confirmed_by_user: bool = False,
) -> dict[str, Any]:
    started_at = _now()
    run_id = f"{source_run_id}_apply"
    run_dir = ensure_dir(data_dir / "runs" / started_at.date().isoformat() / run_id)
    approved_id = f"{source_run_id}_owner_approved"
    artifacts: dict[str, str] = {"run_dir": str(run_dir), "apply_marker": str(apply_marker_for(data_dir=data_dir, approved_id=approved_id))}
    if not confirmed_by_user:
        return {"run_id": run_id, "overall_status": "blocked", "blocker": "Apply requires owner confirmation", "artifacts": artifacts}

    assert_apply_not_repeated(data_dir=data_dir, approved_id=approved_id)
    pending = _load_inbox_pending(data_dir, source_run_id, marketplace="ozon")
    reviews_result = _prepare_and_apply_reviews(
        credentials=credentials,
        data_dir=data_dir,
        reviews_pending_id=str(pending.get("reviews_pending_id") or ""),
        source_run_id=source_run_id,
        marketplace="ozon",
    )
    messenger_actions = pending.get("messenger_actions") if isinstance(pending.get("messenger_actions"), list) else []
    messenger_result = _apply_ozon_messenger(
        credentials=credentials,
        data_dir=data_dir,
        source_run_id=source_run_id,
        actions=[row for row in messenger_actions if isinstance(row, dict)],
        run_dir=run_dir,
    )
    artifacts.update(((reviews_result.get("apply") or {}).get("artifacts") or {}) if isinstance(reviews_result.get("apply"), dict) else {})
    artifacts.update(messenger_result.get("artifacts") or {})
    report_path = run_dir / "ozon_inbox_apply_result.md"
    report_text = _build_apply_report(run_id=run_id, marketplace="ozon", reviews_result=reviews_result, messenger_result=messenger_result, artifacts=artifacts)
    report_path.write_text(report_text, encoding="utf-8")
    artifacts["report"] = str(report_path)
    overall_status = "ok" if reviews_result.get("status") in {"ok", "warning", "skipped"} and messenger_result.get("status") in {"ok", "skipped"} else "blocked"
    summary = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "mode": "apply",
        "source_run_id": source_run_id,
        "reviews": reviews_result,
        "messenger": messenger_result,
        "artifacts": artifacts,
    }
    write_json(run_dir / "summary.json", summary)
    summary["artifacts"]["summary"] = str(run_dir / "summary.json")
    write_json(run_dir / "summary.json", summary)
    manifest_paths = write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=summary,
        task="ozon-inbox-apply",
        mode="apply",
        risk="low",
        marketplaces=["ozon"],
        inputs={"source_run_id": source_run_id, "confirmed_by_user": confirmed_by_user},
        approved_id=approved_id,
    )
    mark_approved_applied(
        data_dir=data_dir,
        approved_id=approved_id,
        apply_run_id=run_id,
        task="ozon-inbox-apply",
        status=overall_status,
        run_manifest_path=manifest_paths["manifest"],
        checksum=canonical_checksum({"source_run_id": source_run_id, "pending": pending}),
    )
    if overall_status == "ok":
        cleanup = _cleanup_inbox_apply_tail(
            data_dir=data_dir,
            source_run_id=source_run_id,
            pending=pending,
            reviews_result=reviews_result,
            messenger_result=messenger_result,
        )
        summary["cleanup"] = cleanup
        write_json(run_dir / "summary.json", summary)
    return summary


def run_wb_inbox_apply(
    *,
    credentials: AppCredentials,
    data_dir: Path = Path("data"),
    source_run_id: str,
    confirmed_by_user: bool = False,
) -> dict[str, Any]:
    started_at = _now()
    run_id = f"{source_run_id}_apply"
    run_dir = ensure_dir(data_dir / "runs" / started_at.date().isoformat() / run_id)
    approved_id = f"{source_run_id}_owner_approved"
    artifacts: dict[str, str] = {"run_dir": str(run_dir), "apply_marker": str(apply_marker_for(data_dir=data_dir, approved_id=approved_id))}
    if not confirmed_by_user:
        return {"run_id": run_id, "overall_status": "blocked", "blocker": "Apply requires owner confirmation", "artifacts": artifacts}

    assert_apply_not_repeated(data_dir=data_dir, approved_id=approved_id)
    pending = _load_inbox_pending(data_dir, source_run_id, marketplace="wb")
    reviews_result = _prepare_and_apply_reviews(
        credentials=credentials,
        data_dir=data_dir,
        reviews_pending_id=str(pending.get("reviews_pending_id") or ""),
        source_run_id=source_run_id,
        marketplace="wb",
    )
    report_path = run_dir / "wb_inbox_apply_result.md"
    report_text = _build_apply_report(run_id=run_id, marketplace="wb", reviews_result=reviews_result, messenger_result=None, artifacts=artifacts)
    report_path.write_text(report_text, encoding="utf-8")
    artifacts["report"] = str(report_path)
    overall_status = "ok" if reviews_result.get("status") in {"ok", "warning", "skipped"} else "blocked"
    summary = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "mode": "apply",
        "source_run_id": source_run_id,
        "reviews": reviews_result,
        "wb_notifications": pending.get("wb_notifications") or {},
        "artifacts": artifacts,
    }
    write_json(run_dir / "summary.json", summary)
    summary["artifacts"]["summary"] = str(run_dir / "summary.json")
    write_json(run_dir / "summary.json", summary)
    manifest_paths = write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=summary,
        task="wb-inbox-apply",
        mode="apply",
        risk="low",
        marketplaces=["wb"],
        inputs={"source_run_id": source_run_id, "confirmed_by_user": confirmed_by_user},
        approved_id=approved_id,
    )
    mark_approved_applied(
        data_dir=data_dir,
        approved_id=approved_id,
        apply_run_id=run_id,
        task="wb-inbox-apply",
        status=overall_status,
        run_manifest_path=manifest_paths["manifest"],
        checksum=canonical_checksum({"source_run_id": source_run_id, "pending": pending}),
    )
    if overall_status == "ok":
        cleanup = _cleanup_inbox_apply_tail(
            data_dir=data_dir,
            source_run_id=source_run_id,
            pending=pending,
            reviews_result=reviews_result,
            messenger_result=None,
        )
        summary["cleanup"] = cleanup
        write_json(run_dir / "summary.json", summary)
    return summary
