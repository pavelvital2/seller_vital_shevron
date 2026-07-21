from __future__ import annotations

from dataclasses import dataclass
import fcntl
import json
import mimetypes
import os
from pathlib import Path
import time
from typing import Any, Callable
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from seller_agent.bot.dispatcher import dispatch_callback, dispatch_message
from seller_agent.bot.runtime_jobs import dispatch_runtime_job_callback, dispatch_runtime_job_message
from seller_agent.config import read_non_empty_lines
from seller_agent.core.job_store import DEFAULT_RUNTIME_DB


DEFAULT_TOKEN_FILE_ENVS = (
    "VITAL_SHEVRON_TELEGRAM_BOT_TOKEN_FILE",
    "SELLER_TELEGRAM_BOT_TOKEN_FILE",
)
DEFAULT_TOKEN_ENVS = (
    "VITAL_SHEVRON_TELEGRAM_BOT_TOKEN",
    "TELEGRAM_BOT_TOKEN",
)
DEFAULT_STATE_FILE = Path(".sessions/telegram/vital_shevron_bot_state.json")
DEFAULT_LOCK_FILE = Path(".sessions/telegram/vital_shevron_bot_polling.lock")
TELEGRAM_MAX_TEXT_LENGTH = 4096
SAFE_CHUNK_LENGTH = 3900
SAFE_DOCUMENT_ARTIFACT_KEYS = {"report"}
SAFE_DOCUMENT_EXTENSIONS = {".csv", ".html", ".md", ".pdf", ".txt", ".xlsx"}
SAFE_DOCUMENT_MAX_BYTES = 20 * 1024 * 1024
UNSAFE_PATH_MARKERS = ("token", "secret", "cookie", "storage", "auth", "password", "credential")
UNSAFE_HTML_CONTENT_MARKERS = (
    "api-key",
    "api_key",
    "authorization:",
    "bot_token",
    "client_secret",
    "cookie",
    "storage_state",
)


class TelegramRunnerError(RuntimeError):
    """Raised for Telegram runner errors without exposing secrets."""


@dataclass(frozen=True)
class TelegramSendResult:
    ok: bool
    message_id: int | None = None
    chat_id: int | None = None
    error: str = ""


ApiRequest = Callable[[str, str, dict[str, Any]], dict[str, Any]]
DocumentApiRequest = Callable[[str, str, dict[str, Any], Path], dict[str, Any]]


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


def telegram_api_document_request(
    token: str,
    method: str,
    payload: dict[str, Any],
    document_path: Path,
) -> dict[str, Any]:
    if not token:
        raise TelegramRunnerError("missing Telegram bot token")
    body, content_type = _multipart_body(payload=payload, document_path=document_path)
    request = Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=body,
        headers={"Content-Type": content_type},
        method="POST",
    )
    try:
        with urlopen(request, timeout=60) as response:
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
    reply_markup: dict[str, Any] | None = None,
    api_request: ApiRequest = telegram_api_request,
) -> list[TelegramSendResult]:
    results: list[TelegramSendResult] = []
    for index, chunk in enumerate(_split_telegram_text(text)):
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": chunk,
            "disable_web_page_preview": True,
        }
        if thread_id is not None:
            payload["message_thread_id"] = thread_id
        if index == 0 and reply_markup:
            payload["reply_markup"] = reply_markup
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


def send_telegram_document(
    *,
    token: str,
    chat_id: int,
    document_path: Path,
    thread_id: int | None = None,
    document_api_request: DocumentApiRequest = telegram_api_document_request,
) -> TelegramSendResult:
    payload: dict[str, Any] = {"chat_id": chat_id}
    if thread_id is not None:
        payload["message_thread_id"] = thread_id
    try:
        response = document_api_request(token, "sendDocument", payload, document_path)
        message = response.get("result") if isinstance(response.get("result"), dict) else {}
        return TelegramSendResult(
            ok=True,
            message_id=_maybe_int(message.get("message_id")),
            chat_id=chat_id,
        )
    except TelegramRunnerError as exc:
        return TelegramSendResult(ok=False, chat_id=chat_id, error=str(exc))


def send_preview_command(
    *,
    token: str,
    chat_id: int,
    message: str,
    data_dir: Path = Path("data"),
    thread_id: int | None = None,
    live_today: bool = False,
    live_status: bool = False,
    runtime_db: Path = DEFAULT_RUNTIME_DB,
    api_request: ApiRequest = telegram_api_request,
    document_api_request: DocumentApiRequest = telegram_api_document_request,
) -> dict[str, Any]:
    command_result = dispatch_message(
        message,
        data_dir=data_dir,
        live_today=live_today,
        live_status=live_status,
        runtime_db=runtime_db,
    )
    send_results = send_telegram_text(
        token=token,
        chat_id=chat_id,
        text=command_result.text,
        thread_id=thread_id,
        reply_markup=command_result.reply_markup,
        api_request=api_request,
    )
    document_results = _send_command_artifacts(
        token=token,
        chat_id=chat_id,
        thread_id=thread_id,
        data_dir=data_dir,
        artifacts=command_result.artifacts if command_result.ok else {},
        document_api_request=document_api_request,
    )
    return {
        "ok": command_result.ok
        and all(result.ok for result in send_results)
        and all(result.ok for result in document_results),
        "command": command_result.command,
        "blocked_reason": command_result.blocked_reason,
        "sent_messages": [result.__dict__ for result in send_results],
        "sent_documents": [result.__dict__ for result in document_results],
        "artifacts": command_result.artifacts,
    }


def poll_once(
    *,
    token: str,
    data_dir: Path = Path("data"),
    state_file: Path = DEFAULT_STATE_FILE,
    allowed_chat_ids: set[int] | None = None,
    live_today: bool = False,
    live_status: bool = False,
    runtime_jobs: bool = False,
    runtime_db: Path = DEFAULT_RUNTIME_DB,
    timeout_seconds: int = 0,
    limit: int = 20,
    api_request: ApiRequest = telegram_api_request,
    document_api_request: DocumentApiRequest = telegram_api_document_request,
) -> dict[str, Any]:
    state = _read_state(state_file)
    offset = _maybe_int(state.get("offset"))
    conversations_value = state.get("conversations")
    conversations = dict(conversations_value) if isinstance(conversations_value, dict) else {}
    payload: dict[str, Any] = {
        "timeout": timeout_seconds,
        "limit": limit,
        "allowed_updates": ["message", "edited_message", "callback_query"],
    }
    if offset is not None:
        payload["offset"] = offset

    response = api_request(token, "getUpdates", payload)
    updates = response.get("result") if isinstance(response.get("result"), list) else []
    next_offset = offset
    processed = 0
    sent = 0
    sent_documents = 0
    skipped = 0
    errors: list[str] = []
    received_chat_ids: set[int] = set()
    processed_chat_ids: set[int] = set()
    skipped_chat_ids: set[int] = set()

    for update in updates:
        if not isinstance(update, dict):
            skipped += 1
            continue
        update_id = _maybe_int(update.get("update_id"))
        if update_id is not None:
            next_offset = max(next_offset or 0, update_id + 1)

        callback_query = update.get("callback_query")
        if isinstance(callback_query, dict):
            callback_id = str(callback_query.get("id") or "")
            callback_message = callback_query.get("message")
            message_obj = callback_message if isinstance(callback_message, dict) else {}
            chat = message_obj.get("chat") if isinstance(message_obj.get("chat"), dict) else {}
            chat_id = _maybe_int(chat.get("id"))
            data = str(callback_query.get("data") or "").strip()
            if chat_id is None or not data:
                skipped += 1
                continue
            received_chat_ids.add(chat_id)
            if allowed_chat_ids is not None and chat_id not in allowed_chat_ids:
                skipped += 1
                skipped_chat_ids.add(chat_id)
                continue

            if callback_id:
                try:
                    callback_text = "Принято. Выполняю действие..."
                    if data in {"wbam_cancel", "mpr_cancel", "wbwp_cancel", "opm_cancel"} or data.startswith(
                        ("wbam_reject:", "wbwp_reject:")
                    ):
                        callback_text = "Отменено."
                    elif data.startswith("wbam_confirm:"):
                        callback_text = "Параметры подтверждены. Выполняю расчёт..."
                    elif data.startswith(("mpr_market:", "mpr_type:", "mpr_period:")):
                        callback_text = "Выбрано."
                    elif data.startswith("mpr_run:"):
                        callback_text = "Формирую read-only отчёт..."
                    elif data.startswith("opm_period:"):
                        callback_text = "Период выбран."
                    elif data.startswith("wbwp_mode:"):
                        callback_text = "Выбрано."
                    elif data.startswith("wbwp_run:"):
                        callback_text = "Формирую Excel в работу..."
                    elif data.startswith("wbwp_approve:"):
                        callback_text = "План утверждён в работу."
                    api_request(
                        token,
                        "answerCallbackQuery",
                        {
                            "callback_query_id": callback_id,
                            "text": callback_text,
                            "show_alert": False,
                        },
                    )
                except TelegramRunnerError as exc:
                    errors.append(str(exc))

            thread_id = _maybe_int(message_obj.get("message_thread_id"))
            conversation_key = _conversation_key(chat_id, thread_id)
            conversations.pop(conversation_key, None)
            command_result = None
            if runtime_jobs and update_id is not None:
                command_result = dispatch_runtime_job_callback(
                    data,
                    update_id=update_id,
                    chat_id=chat_id,
                    thread_id=thread_id,
                    data_dir=data_dir,
                    runtime_db=runtime_db,
                )
            if command_result is None:
                command_result = dispatch_callback(data, data_dir=data_dir)
            if command_result.conversation_state:
                conversations[conversation_key] = command_result.conversation_state
            send_results = send_telegram_text(
                token=token,
                chat_id=chat_id,
                text=command_result.text,
                thread_id=thread_id,
                reply_markup=command_result.reply_markup,
                api_request=api_request,
            )
            document_results = _send_command_artifacts(
                token=token,
                chat_id=chat_id,
                thread_id=thread_id,
                data_dir=data_dir,
                artifacts=command_result.artifacts if command_result.ok else {},
                document_api_request=document_api_request,
            )
            processed += 1
            processed_chat_ids.add(chat_id)
            sent += sum(1 for result in send_results if result.ok)
            sent_documents += sum(1 for result in document_results if result.ok)
            errors.extend(result.error for result in send_results if result.error)
            errors.extend(result.error for result in document_results if result.error)
            continue

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
        received_chat_ids.add(chat_id)
        if allowed_chat_ids is not None and chat_id not in allowed_chat_ids:
            skipped += 1
            skipped_chat_ids.add(chat_id)
            continue

        thread_id = _maybe_int(message_obj.get("message_thread_id"))
        conversation_key = _conversation_key(chat_id, thread_id)
        command_result = None
        if runtime_jobs and update_id is not None:
            command_result = dispatch_runtime_job_message(
                text,
                update_id=update_id,
                chat_id=chat_id,
                thread_id=thread_id,
                data_dir=data_dir,
                runtime_db=runtime_db,
                live_today=live_today,
                live_status=live_status,
                conversation_state=conversations.get(conversation_key),
            )
        if command_result is None:
            command_result = dispatch_message(
                text,
                data_dir=data_dir,
                live_today=live_today,
                live_status=live_status,
                runtime_db=runtime_db,
                conversation_state=conversations.get(conversation_key),
            )
        if command_result.conversation_state:
            conversations[conversation_key] = command_result.conversation_state
        else:
            conversations.pop(conversation_key, None)
        send_results = send_telegram_text(
            token=token,
            chat_id=chat_id,
            text=command_result.text,
            thread_id=thread_id,
            reply_markup=command_result.reply_markup,
            api_request=api_request,
        )
        document_results = _send_command_artifacts(
            token=token,
            chat_id=chat_id,
            thread_id=thread_id,
            data_dir=data_dir,
            artifacts=command_result.artifacts if command_result.ok else {},
            document_api_request=document_api_request,
        )
        processed += 1
        processed_chat_ids.add(chat_id)
        sent += sum(1 for result in send_results if result.ok)
        sent_documents += sum(1 for result in document_results if result.ok)
        errors.extend(result.error for result in send_results if result.error)
        errors.extend(result.error for result in document_results if result.error)

    if next_offset is not None:
        state["offset"] = next_offset
        if conversations:
            state["conversations"] = conversations
        else:
            state.pop("conversations", None)
        _write_state(state_file, state)

    return {
        "ok": not errors,
        "processed_updates": processed,
        "sent_messages": sent,
        "sent_documents": sent_documents,
        "skipped_updates": skipped,
        "received_chat_ids": sorted(received_chat_ids),
        "processed_chat_ids": sorted(processed_chat_ids),
        "skipped_chat_ids": sorted(skipped_chat_ids),
        "next_offset": next_offset,
        "errors": errors,
        "state_file": str(state_file),
    }


def poll_loop(
    *,
    token: str,
    data_dir: Path = Path("data"),
    state_file: Path = DEFAULT_STATE_FILE,
    lock_file: Path = DEFAULT_LOCK_FILE,
    allowed_chat_ids: set[int],
    live_today: bool = False,
    live_status: bool = False,
    runtime_jobs: bool = False,
    runtime_db: Path = DEFAULT_RUNTIME_DB,
    timeout_seconds: int = 20,
    limit: int = 20,
    poll_interval_seconds: float = 1.0,
    error_sleep_seconds: float = 5.0,
    max_iterations: int | None = None,
    api_request: ApiRequest = telegram_api_request,
    emit_logs: bool = True,
) -> dict[str, Any]:
    if not allowed_chat_ids:
        raise TelegramRunnerError("poll-loop requires allowed_chat_ids")

    total_processed = 0
    total_sent = 0
    total_sent_documents = 0
    total_skipped = 0
    iterations = 0
    loop_errors: list[str] = []
    with _ExclusiveLock(lock_file):
        while max_iterations is None or iterations < max_iterations:
            iterations += 1
            try:
                result = poll_once(
                    token=token,
                    data_dir=data_dir,
                    state_file=state_file,
                    allowed_chat_ids=allowed_chat_ids,
                    live_today=live_today,
                    live_status=live_status,
                    runtime_jobs=runtime_jobs,
                    runtime_db=runtime_db,
                    timeout_seconds=timeout_seconds,
                    limit=limit,
                    api_request=api_request,
                )
            except TelegramRunnerError as exc:
                result = {
                    "ok": False,
                    "processed_updates": 0,
                    "sent_messages": 0,
                    "skipped_updates": 0,
                    "errors": [str(exc)],
                    "state_file": str(state_file),
                }

            total_processed += int(result.get("processed_updates") or 0)
            total_sent += int(result.get("sent_messages") or 0)
            total_sent_documents += int(result.get("sent_documents") or 0)
            total_skipped += int(result.get("skipped_updates") or 0)
            loop_errors.extend(str(error) for error in result.get("errors") or [])
            if emit_logs:
                print(json.dumps(_loop_log_row(iterations, result), ensure_ascii=False), flush=True)
            if not result.get("ok"):
                time.sleep(error_sleep_seconds)
            elif max_iterations is None or iterations < max_iterations:
                time.sleep(poll_interval_seconds)

    return {
        "ok": not loop_errors,
        "iterations": iterations,
        "processed_updates": total_processed,
        "sent_messages": total_sent,
        "sent_documents": total_sent_documents,
        "skipped_updates": total_skipped,
        "errors": loop_errors,
        "state_file": str(state_file),
        "lock_file": str(lock_file),
        "allowed_chat_ids_count": len(allowed_chat_ids),
    }


def safe_report_attachment_paths(
    *,
    artifacts: dict[str, str],
    data_dir: Path = Path("data"),
    project_root: Path | None = None,
) -> list[Path]:
    base = (project_root or Path.cwd()).resolve()
    data_root = data_dir if data_dir.is_absolute() else base / data_dir
    allowed_roots = [
        (data_root / "runs").resolve(strict=False),
        (data_root / "reports").resolve(strict=False),
    ]
    paths: list[Path] = []
    for key, value in artifacts.items():
        if str(key) not in SAFE_DOCUMENT_ARTIFACT_KEYS:
            continue
        candidate = Path(str(value)).expanduser()
        if not candidate.is_absolute():
            candidate = base / candidate
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError:
            continue
        if not resolved.is_file():
            continue
        if resolved.suffix.lower() not in SAFE_DOCUMENT_EXTENSIONS:
            continue
        if resolved.stat().st_size > SAFE_DOCUMENT_MAX_BYTES:
            continue
        lowered_path = str(resolved).lower()
        if any(marker in lowered_path for marker in UNSAFE_PATH_MARKERS):
            continue
        if resolved.suffix.lower() == ".html" and _html_contains_unsafe_marker(resolved):
            continue
        if not any(_is_relative_to(resolved, root) for root in allowed_roots):
            continue
        paths.append(resolved)
    return paths


def _html_contains_unsafe_marker(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
    except OSError:
        return True
    return any(marker in text for marker in UNSAFE_HTML_CONTENT_MARKERS)


def _send_command_artifacts(
    *,
    token: str,
    chat_id: int,
    thread_id: int | None,
    data_dir: Path,
    artifacts: dict[str, str],
    document_api_request: DocumentApiRequest,
) -> list[TelegramSendResult]:
    results: list[TelegramSendResult] = []
    for path in safe_report_attachment_paths(artifacts=artifacts, data_dir=data_dir):
        results.append(
            send_telegram_document(
                token=token,
                chat_id=chat_id,
                thread_id=thread_id,
                document_path=path,
                document_api_request=document_api_request,
            )
        )
    return results


def _multipart_body(*, payload: dict[str, Any], document_path: Path) -> tuple[bytes, str]:
    boundary = f"----vital-shevron-{uuid.uuid4().hex}"
    body = bytearray()
    for key, value in payload.items():
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")

    mime_type = mimetypes.guess_type(document_path.name)[0] or "application/octet-stream"
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(
        (
            'Content-Disposition: form-data; name="document"; '
            f'filename="{document_path.name}"\r\n'
        ).encode("utf-8")
    )
    body.extend(f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8"))
    body.extend(document_path.read_bytes())
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    return bytes(body), f"multipart/form-data; boundary={boundary}"


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


def _conversation_key(chat_id: int, thread_id: int | None) -> str:
    return f"{chat_id}:{thread_id or 0}"


def _loop_log_row(iteration: int, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "event": "telegram_poll_iteration",
        "iteration": iteration,
        "ok": bool(result.get("ok")),
        "processed_updates": result.get("processed_updates", 0),
        "sent_messages": result.get("sent_messages", 0),
        "sent_documents": result.get("sent_documents", 0),
        "skipped_updates": result.get("skipped_updates", 0),
        "received_chat_ids": result.get("received_chat_ids", []),
        "processed_chat_ids": result.get("processed_chat_ids", []),
        "skipped_chat_ids": result.get("skipped_chat_ids", []),
        "errors_count": len(result.get("errors") or []),
        "state_file": result.get("state_file", ""),
    }


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


class _ExclusiveLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._file: Any | None = None

    def __enter__(self) -> "_ExclusiveLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._file.close()
            self._file = None
            raise TelegramRunnerError(f"another Telegram polling process holds lock: {self.path}") from exc
        self.path.chmod(0o600)
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._file is None:
            return
        fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        self._file.close()
        self._file = None


def _maybe_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
