from __future__ import annotations

from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlencode

import pytest

from seller_agent.control_plane.auth import (
    ControlAuthError,
    SessionSigner,
    authenticate_telegram_init_data,
)
from seller_agent.core.job_store import JobStore


NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
BOT_TOKEN = "123456:test-only-token"
OWNER_ID = 424242


def _signed_init_data(
    *,
    user_id: int = OWNER_ID,
    auth_date: int | None = None,
    token: str = BOT_TOKEN,
    overrides: dict[str, str] | None = None,
) -> str:
    fields = {
        "auth_date": str(auth_date if auth_date is not None else int(NOW.timestamp())),
        "query_id": "test-query-id",
        "user": json.dumps(
            {"id": user_id, "first_name": "Owner", "language_code": "ru"},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }
    fields.update(overrides or {})
    data_check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_init_data_auth_positive_and_durable_replay(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "runtime.db")
    raw = _signed_init_data()

    user = authenticate_telegram_init_data(
        raw,
        bot_token=BOT_TOKEN,
        allowed_owner_ids={OWNER_ID},
        store=store,
        now=NOW,
    )

    assert user.user_id == OWNER_ID
    assert user.first_name == "Owner"
    with sqlite3.connect(tmp_path / "runtime.db") as connection:
        persisted = "\n".join(
            str(value)
            for row in connection.execute("SELECT * FROM control_auth_replays")
            for value in row
        )
    assert "test-query-id" not in persisted
    assert raw not in persisted
    with pytest.raises(ControlAuthError, match="init_data_replayed"):
        authenticate_telegram_init_data(
            raw,
            bot_token=BOT_TOKEN,
            allowed_owner_ids={OWNER_ID},
            store=JobStore(tmp_path / "runtime.db"),
            now=NOW,
        )


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (_signed_init_data(overrides={"query_id": "tampered"}) + "x", "init_data_invalid"),
        (_signed_init_data(auth_date=int(NOW.timestamp()) - 301), "init_data_stale"),
        (_signed_init_data(auth_date=int(NOW.timestamp()) + 31), "init_data_from_future"),
        (_signed_init_data(user_id=999), "owner_denied"),
    ],
)
def test_init_data_auth_rejects_invalid_inputs(
    tmp_path: Path,
    raw: str,
    code: str,
) -> None:
    with pytest.raises(ControlAuthError, match=code):
        authenticate_telegram_init_data(
            raw,
            bot_token=BOT_TOKEN,
            allowed_owner_ids={OWNER_ID},
            store=JobStore(tmp_path / f"{code}.db"),
            now=NOW,
        )


def test_init_data_rejects_bad_user_json_and_duplicate_fields(tmp_path: Path) -> None:
    malformed = _signed_init_data(overrides={"user": "not-json"})
    with pytest.raises(ControlAuthError, match="init_data_user_invalid"):
        authenticate_telegram_init_data(
            malformed,
            bot_token=BOT_TOKEN,
            allowed_owner_ids={OWNER_ID},
            store=JobStore(tmp_path / "bad-user.db"),
            now=NOW,
        )

    duplicate = _signed_init_data() + "&auth_date=1"
    with pytest.raises(ControlAuthError, match="init_data_invalid"):
        authenticate_telegram_init_data(
            duplicate,
            bot_token=BOT_TOKEN,
            allowed_owner_ids={OWNER_ID},
            store=JobStore(tmp_path / "duplicate.db"),
            now=NOW,
        )


def test_signed_session_is_short_lived_owner_bound_and_has_csrf() -> None:
    signer = SessionSigner(b"s" * 32, ttl_seconds=600)
    issued = signer.issue(OWNER_ID, now=NOW)

    session = signer.verify(issued.cookie_value, now=NOW)
    assert session.owner_id == OWNER_ID
    assert hmac.compare_digest(session.csrf_token, issued.csrf_token)

    with pytest.raises(ControlAuthError, match="session_invalid"):
        signer.verify(issued.cookie_value[:-1] + "x", now=NOW)
    with pytest.raises(ControlAuthError, match="session_expired"):
        signer.verify(
            issued.cookie_value,
            now=datetime.fromtimestamp(NOW.timestamp() + 601, timezone.utc),
        )


def test_concurrent_init_data_replay_has_one_winner(tmp_path: Path) -> None:
    raw = _signed_init_data(overrides={"query_id": "concurrent"})

    def attempt(_: int) -> str:
        try:
            authenticate_telegram_init_data(
                raw,
                bot_token=BOT_TOKEN,
                allowed_owner_ids={OWNER_ID},
                store=JobStore(tmp_path / "runtime.db"),
                now=NOW,
            )
        except ControlAuthError as exc:
            return exc.code
        return "ok"

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(attempt, range(5)))

    assert results.count("ok") == 1
    assert results.count("init_data_replayed") == 4
