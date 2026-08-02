from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import hmac
import json
import secrets
from typing import Any
from urllib.parse import parse_qsl

from seller_agent.core.job_store import JobStore


DEFAULT_INIT_DATA_MAX_AGE_SECONDS = 300
DEFAULT_FUTURE_SKEW_SECONDS = 30
DEFAULT_SESSION_TTL_SECONDS = 900
MAX_INIT_DATA_BYTES = 16_384
_SESSION_DOMAIN = b"seller-agent-control-session/v1"


class ControlAuthError(ValueError):
    """Safe authentication failure carrying only a stable public code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class AuthenticatedTelegramUser:
    user_id: int
    first_name: str
    auth_date: int


@dataclass(frozen=True)
class ControlSession:
    owner_id: int
    csrf_token: str
    issued_at: int
    expires_at: int
    session_id: str


@dataclass(frozen=True)
class IssuedSession:
    cookie_value: str
    csrf_token: str
    expires_at: int


def authenticate_telegram_init_data(
    raw_init_data: str,
    *,
    bot_token: str,
    allowed_owner_ids: set[int],
    store: JobStore,
    now: datetime | None = None,
    max_age_seconds: int = DEFAULT_INIT_DATA_MAX_AGE_SECONDS,
    future_skew_seconds: int = DEFAULT_FUTURE_SKEW_SECONDS,
) -> AuthenticatedTelegramUser:
    """Validate and consume raw Telegram Mini App initData exactly once."""
    current = _utc_now(now)
    if not isinstance(raw_init_data, str) or not raw_init_data:
        raise ControlAuthError("init_data_missing")
    if len(raw_init_data.encode("utf-8")) > MAX_INIT_DATA_BYTES:
        raise ControlAuthError("init_data_too_large")
    if not bot_token:
        raise ControlAuthError("auth_not_configured")

    try:
        pairs = parse_qsl(raw_init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise ControlAuthError("init_data_invalid") from exc
    fields: dict[str, str] = {}
    for key, value in pairs:
        if not key or key in fields:
            raise ControlAuthError("init_data_invalid")
        fields[key] = value
    supplied_hash = fields.pop("hash", "")
    if len(supplied_hash) != 64:
        raise ControlAuthError("init_data_invalid")
    data_check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    expected_hash = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_hash, supplied_hash):
        raise ControlAuthError("init_data_invalid")

    try:
        auth_date = int(fields.get("auth_date", ""))
    except (TypeError, ValueError) as exc:
        raise ControlAuthError("init_data_auth_date_invalid") from exc
    age = int(current.timestamp()) - auth_date
    if age > max_age_seconds:
        raise ControlAuthError("init_data_stale")
    if age < -future_skew_seconds:
        raise ControlAuthError("init_data_from_future")

    try:
        user_payload: Any = json.loads(fields.get("user", ""))
        if not isinstance(user_payload, dict):
            raise TypeError
        user_id = int(user_payload["id"])
        first_name = str(user_payload.get("first_name") or "")[:128]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ControlAuthError("init_data_user_invalid") from exc
    if user_id not in allowed_owner_ids:
        raise ControlAuthError("owner_denied")

    fingerprint = hashlib.sha256(raw_init_data.encode("utf-8")).hexdigest()
    replay_expires = datetime.fromtimestamp(
        auth_date + max_age_seconds + future_skew_seconds,
        timezone.utc,
    ).isoformat(timespec="seconds").replace("+00:00", "Z")
    if not store.register_control_auth_replay(
        init_data_hash=fingerprint,
        owner_id=str(user_id),
        auth_date=auth_date,
        expires_at=replay_expires,
        consumed_at=current.isoformat(timespec="seconds").replace("+00:00", "Z"),
    ):
        raise ControlAuthError("init_data_replayed")
    return AuthenticatedTelegramUser(
        user_id=user_id,
        first_name=first_name,
        auth_date=auth_date,
    )


class SessionSigner:
    def __init__(
        self,
        secret: bytes,
        *,
        ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
    ) -> None:
        if len(secret) < 32:
            raise ValueError("Session signing secret must contain at least 32 bytes.")
        if ttl_seconds <= 0 or ttl_seconds > 3600:
            raise ValueError("Session TTL must be between 1 and 3600 seconds.")
        self._key = hmac.new(secret, _SESSION_DOMAIN, hashlib.sha256).digest()
        self.ttl_seconds = int(ttl_seconds)

    def issue(self, owner_id: int, *, now: datetime | None = None) -> IssuedSession:
        current = _utc_now(now)
        issued_at = int(current.timestamp())
        expires_at = issued_at + self.ttl_seconds
        csrf_token = secrets.token_urlsafe(24)
        payload = {
            "exp": expires_at,
            "iat": issued_at,
            "sid": secrets.token_urlsafe(18),
            "uid": int(owner_id),
            "csrf": csrf_token,
        }
        encoded = _b64url(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        signature = _b64url(hmac.new(self._key, encoded.encode("ascii"), hashlib.sha256).digest())
        return IssuedSession(
            cookie_value=f"{encoded}.{signature}",
            csrf_token=csrf_token,
            expires_at=expires_at,
        )

    def verify(self, cookie_value: str, *, now: datetime | None = None) -> ControlSession:
        try:
            encoded, supplied_signature = cookie_value.split(".", 1)
            expected_signature = _b64url(
                hmac.new(self._key, encoded.encode("ascii"), hashlib.sha256).digest()
            )
            if not hmac.compare_digest(expected_signature, supplied_signature):
                raise ControlAuthError("session_invalid")
            payload = json.loads(_b64url_decode(encoded))
            if not isinstance(payload, dict):
                raise TypeError
            session = ControlSession(
                owner_id=int(payload["uid"]),
                csrf_token=str(payload["csrf"]),
                issued_at=int(payload["iat"]),
                expires_at=int(payload["exp"]),
                session_id=str(payload["sid"]),
            )
        except ControlAuthError:
            raise
        except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
            raise ControlAuthError("session_invalid") from exc
        if int(_utc_now(now).timestamp()) > session.expires_at:
            raise ControlAuthError("session_expired")
        return session


def _utc_now(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> str:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding).decode("utf-8")
