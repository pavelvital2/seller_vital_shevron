from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from takterra_agent.bot.dispatcher import dispatch_message
from takterra_agent.config import read_non_empty_lines


DEFAULT_TOKEN_FILE_ENVS = (
    "VITAL_SHEVRON_TELEGRAM_BOT_TOKEN_FILE",
    "SELLER_TELEGRAM_BOT_TOKEN_FILE",
)
DEFAULT_TOKEN_ENVS = (
    "VITAL_SHEVRON_TELEGRAM_BOT_TOKEN",
    "TELEGRAM_BOT_TOKEN",
)
DEFAULT_STATE_FILE = Path(".sessions/telegram/vital_shevron_bot_state.json")
TELEGRAM_MAX_TEXT_LENGTH = 4096
SAFE_CHUNK_LENGTH = 3900


class TelegramRunnerError(RuntimeError):
    """Raised for Telegram runner errors without exposing secrets."""


@dataclass(frozen=True)
class TelegramSendResult:
    ok: bool
    message_id: int | None = None
    chat_id: int | None = None
    error: str = ""


ApiRequest = Callable[[str, str, dict[str, Any]], dict[str, Any]]


def load_telegram_bot_token(
    *,
    token_file: str | Path | None = None,
    token_file_envs: tuple[str, ...] = DEFAULT_TOKEN_FILE_ENVS,
    token_envs: tuple[str, ...] = DEFAULT_TOKEN_ENVS,
) -> str | None:
    if token_file:
        path = Path(token_file).expanduser()
        if not path.exists():
            return None
        lines = read_non_empty_lines(path)
        return lines[0] if lines else None

    for env_name in token_file_envs:
        file_name = os.environ.get(env_name)
        if file_name:
            token = load_telegram_bot_token(token_file=file_name)
            if token:
                return token

    for env_name in token_envs:
        value = os.environ.get(env_name)
        if value:
            return value.strip()

    return None


def telegram_api_request(token: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not token:
        raise TelegramRunnerError("missing Telegram bot token")
    request = Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise TelegramRunnerError(f"Telegram API HTTP error {exc.code}") from exc
    except URLError as exc:
        raise TelegramRunnerError(f"Telegram API network error: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise TelegramRunnerError("Telegram API returned invalid JSON") from exc

    if not data.get("ok"):
        description = str(data.get("description") or "unknown Telegram API error")
        raise TelegramRunnerError(description)
    return data


def send_telegram_text(
    *,
    token: str,
    chat_id: int,
    text: str,
    thread_id: int | None = None,
    api_request: ApiRequest = telegram_api_request,
) -> list[TelegramSendResult]:
    results: list[TelegramSendResult] = []
    for chunk in _split_telegram_text(text):
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": chunk,
            "disable_web_page_preview": True,
        }
        if thread_id is not None:
            payload["message_thread_id"] = thread_id
        try:
            response = api_request(token, "sendMessage", payload)
            message = response.get("result") if isinstance(response.get("result"), dict) else {}
            results.append(
                TelegramSendResult(
                    ok=True,
                    message_id=_maybe_int(message.get("message_id")),
                    chat_id=chat_id,
                )
            )
        except TelegramRunnerError as exc:
            results.append(TelegramSendResult(ok=False, chat_id=chat_id, error=str(exc)))
            break
    return results


def send_preview_command(
    *,
    token: str,
    chat_id: int,
    message: str,
    data_dir: Path = Path("data"),
    thread_id: int | None = None,
    api_request: ApiRequest = telegram_api_request,
) -> dict[str, Any]:
    command_result = dispatch_message(message, data_dir=data_dir)
    send_results = send_telegram_text(
        token=token,
        chat_id=chat_id,
        text=command_result.text,
        thread_id=thread_id,
        api_request=api_request,
    )
    return {
        "ok": command_result.ok and all(result.ok for result in send_results),
        "command": command_result.command,
        "blocked_reason": command_result.blocked_reason,
        "sent_messages": [result.__dict__ for result in send_results],
        "artifacts": command_result.artifacts,
    }


def poll_once(
    *,
    token: str,
    data_dir: Path = Path("data"),
    state_file: Path = DEFAULT_STATE_FILE,
    allowed_chat_ids: set[int] | None = None,
    timeout_seconds: int = 0,
    limit: int = 20,
    api_request: ApiRequest = telegram_api_request,
) -> dict[str, Any]:
    state = _read_state(state_file)
    offset = _maybe_int(state.get("offset"))
    payload: dict[str, Any] = {
        "timeout": timeout_seconds,
        "limit": limit,
        "allowed_updates": ["message", "edited_message"],
    }
    if offset is not None:
        payload["offset"] = offset

    response = api_request(token, "getUpdates", payload)
    updates = response.get("result") if isinstance(response.get("result"), list) else []
    next_offset = offset
    processed = 0
    sent = 0
    skipped = 0
    errors: list[str] = []

    for update in updates:
        if not isinstance(update, dict):
            skipped += 1
            continue
        update_id = _maybe_int(update.get("update_id"))
        if update_id is not None:
            next_offset = max(next_offset or 0, update_id + 1)

        message_obj = update.get("message") or update.get("edited_message")
        if not isinstance(message_obj, dict):
            skipped += 1
            continue
        chat = message_obj.get("chat") if isinstance(message_obj.get("chat"), dict) else {}
        chat_id = _maybe_int(chat.get("id"))
        text = str(message_obj.get("text") or "").strip()
        if chat_id is None or not text:
            skipped += 1
            continue
        if allowed_chat_ids is not None and chat_id not in allowed_chat_ids:
            skipped += 1
            continue

        thread_id = _maybe_int(message_obj.get("message_thread_id"))
        command_result = dispatch_message(text, data_dir=data_dir)
        send_results = send_telegram_text(
            token=token,
            chat_id=chat_id,
            text=command_result.text,
            thread_id=thread_id,
            api_request=api_request,
        )
        processed += 1
        sent += sum(1 for result in send_results if result.ok)
        errors.extend(result.error for result in send_results if result.error)

    if next_offset is not None:
        _write_state(state_file, {"offset": next_offset})

    return {
        "ok": not errors,
        "processed_updates": processed,
        "sent_messages": sent,
        "skipped_updates": skipped,
        "next_offset": next_offset,
        "errors": errors,
        "state_file": str(state_file),
    }


def _split_telegram_text(text: str) -> list[str]:
    if len(text) <= TELEGRAM_MAX_TEXT_LENGTH:
        return [text]
    chunks: list[str] = []
    remaining = text
    while remaining:
        chunk = remaining[:SAFE_CHUNK_LENGTH]
        split_at = chunk.rfind("\n")
        if split_at > 0:
            chunk = chunk[:split_at]
        chunks.append(chunk)
        remaining = remaining[len(chunk) :].lstrip("\n")
    return chunks


def _read_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    path.chmod(0o600)


def _maybe_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
