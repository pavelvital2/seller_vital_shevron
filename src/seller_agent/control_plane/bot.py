from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import time
from threading import Event
from typing import Any

from seller_agent.bot.telegram_runner import ApiRequest, telegram_api_request
from seller_agent.control_plane.config import (
    DEFAULT_CONTROL_LOCK_FILE,
    DEFAULT_CONTROL_STATE_FILE,
)


class ControlBotError(RuntimeError):
    """Safe control-bot error with no Telegram payload or secret."""


def control_bot_preflight(
    *,
    token: str,
    api_request: ApiRequest = telegram_api_request,
) -> dict[str, Any]:
    response = api_request(token, "getWebhookInfo", {})
    if response.get("ok") is not True or not isinstance(response.get("result"), dict):
        raise ControlBotError("webhook_preflight_invalid")
    result = response["result"]
    if not isinstance(result.get("url"), str):
        raise ControlBotError("webhook_preflight_invalid")
    if str(result.get("url") or "").strip():
        raise ControlBotError("webhook_active")
    return {"ok": True, "webhook": "disabled"}


def configure_control_bot_menu(
    *,
    token: str,
    allowed_owner_ids: set[int] | frozenset[int],
    public_app_url: str,
    api_request: ApiRequest = telegram_api_request,
) -> None:
    for owner_id in sorted(allowed_owner_ids):
        api_request(
            token,
            "setChatMenuButton",
            {
                "chat_id": owner_id,
                "menu_button": {
                    "type": "web_app",
                    "text": "Открыть управление",
                    "web_app": {"url": public_app_url},
                },
            },
        )


def poll_control_bot_once(
    *,
    token: str,
    allowed_owner_ids: set[int] | frozenset[int],
    public_app_url: str,
    state_file: Path = DEFAULT_CONTROL_STATE_FILE,
    timeout_seconds: int = 20,
    limit: int = 20,
    api_request: ApiRequest = telegram_api_request,
) -> dict[str, Any]:
    if not allowed_owner_ids:
        raise ControlBotError("owner_allowlist_missing")
    if not public_app_url.startswith("https://") or not public_app_url.endswith(
        "/vital-shevron/"
    ):
        raise ControlBotError("public_app_url_invalid")
    state = _read_state(state_file)
    offset = _int_or_none(state.get("offset"))
    request_payload: dict[str, Any] = {
        "timeout": max(0, min(int(timeout_seconds), 30)),
        "limit": max(1, min(int(limit), 100)),
        "allowed_updates": ["message"],
    }
    if offset is not None:
        request_payload["offset"] = offset
    response = api_request(token, "getUpdates", request_payload)
    if response.get("ok") is not True or not isinstance(response.get("result"), list):
        raise ControlBotError("poll_response_invalid")
    updates = response["result"]
    next_offset = offset
    processed = 0
    denied = 0
    skipped = 0
    for update in updates:
        if not isinstance(update, dict):
            skipped += 1
            continue
        update_id = _int_or_none(update.get("update_id"))
        if update_id is None:
            skipped += 1
            continue
        if offset is not None and update_id < offset:
            skipped += 1
            continue
        next_offset = max(next_offset or 0, update_id + 1)
        message = update.get("message")
        if not isinstance(message, dict):
            skipped += 1
            continue
        sender = message.get("from") if isinstance(message.get("from"), dict) else {}
        chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
        sender_id = _int_or_none(sender.get("id"))
        chat_id = _int_or_none(chat.get("id"))
        is_owner = (
            sender_id is not None
            and chat_id is not None
            and sender_id == chat_id
            and str(chat.get("type") or "") == "private"
            and sender_id in allowed_owner_ids
        )
        if not is_owner:
            if chat_id is not None:
                api_request(
                    token,
                    "sendMessage",
                    {"chat_id": chat_id, "text": "Доступ запрещён."},
                )
            denied += 1
            continue
        api_request(
            token,
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": (
                    "Панель Vital Shevron\n\n"
                    "Первый этап доступен только для read-only аналитики Ozon/WB."
                ),
                "reply_markup": {
                    "inline_keyboard": [
                        [
                            {
                                "text": "Открыть управление",
                                "web_app": {"url": public_app_url},
                            }
                        ]
                    ]
                },
            },
        )
        processed += 1
    if next_offset is not None:
        _write_state(state_file, {"offset": next_offset})
    return {
        "ok": True,
        "processed_updates": processed,
        "denied_updates": denied,
        "skipped_updates": skipped,
        "next_offset": next_offset,
        "state_file": str(state_file),
    }


def run_control_bot_loop(
    *,
    token: str,
    allowed_owner_ids: set[int] | frozenset[int],
    public_app_url: str,
    state_file: Path = DEFAULT_CONTROL_STATE_FILE,
    lock_file: Path = DEFAULT_CONTROL_LOCK_FILE,
    timeout_seconds: int = 20,
    limit: int = 20,
    poll_interval_seconds: float = 1.0,
    max_iterations: int | None = None,
    stop_event: Event | None = None,
    api_request: ApiRequest = telegram_api_request,
) -> dict[str, Any]:
    total_processed = 0
    total_denied = 0
    iterations = 0
    with _ControlPollingLock(lock_file):
        control_bot_preflight(token=token, api_request=api_request)
        configure_control_bot_menu(
            token=token,
            allowed_owner_ids=allowed_owner_ids,
            public_app_url=public_app_url,
            api_request=api_request,
        )
        while (
            (max_iterations is None or iterations < max_iterations)
            and not (stop_event is not None and stop_event.is_set())
        ):
            iterations += 1
            result = poll_control_bot_once(
                token=token,
                allowed_owner_ids=allowed_owner_ids,
                public_app_url=public_app_url,
                state_file=state_file,
                timeout_seconds=timeout_seconds,
                limit=limit,
                api_request=api_request,
            )
            total_processed += int(result["processed_updates"])
            total_denied += int(result["denied_updates"])
            if (
                (max_iterations is None or iterations < max_iterations)
                and not (stop_event is not None and stop_event.is_set())
            ):
                time.sleep(max(0.1, float(poll_interval_seconds)))
    return {
        "ok": True,
        "iterations": iterations,
        "processed_updates": total_processed,
        "denied_updates": total_denied,
        "state_file": str(state_file),
        "lock_file": str(lock_file),
    }


class _ControlPollingLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: Any | None = None

    def __enter__(self) -> "_ControlPollingLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._handle.close()
            self._handle = None
            raise ControlBotError("polling_lock_busy") from exc
        self.path.chmod(0o600)
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._handle is None:
            return
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()
        self._handle = None


def _read_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ControlBotError("polling_state_invalid") from exc
    if not isinstance(value, dict):
        raise ControlBotError("polling_state_invalid")
    return value


def _write_state(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temp.write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        temp.chmod(0o600)
        temp.replace(path)
        path.chmod(0o600)
    finally:
        if temp.exists():
            temp.unlink()


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
