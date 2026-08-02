from __future__ import annotations

import fcntl
from pathlib import Path

import pytest

from seller_agent.control_plane.bot import (
    ControlBotError,
    control_bot_preflight,
    configure_control_bot_menu,
    poll_control_bot_once,
    run_control_bot_loop,
)
from seller_agent.control_plane.config import (
    DEFAULT_CONTROL_LOCK_FILE,
    DEFAULT_CONTROL_STATE_FILE,
)
from seller_agent.bot.telegram_runner import DEFAULT_LOCK_FILE, DEFAULT_STATE_FILE


def test_control_bot_start_owner_opens_only_https_mini_app(tmp_path: Path) -> None:
    calls: list[tuple[str, str, dict]] = []

    def api(token: str, method: str, payload: dict) -> dict:
        calls.append((token, method, payload))
        if method == "getUpdates":
            return {
                "ok": True,
                "result": [
                    {
                        "update_id": 100,
                        "message": {
                            "from": {"id": 42},
                            "chat": {"id": 42, "type": "private"},
                            "text": "/start",
                        },
                    }
                ],
            }
        return {"ok": True, "result": {"message_id": 10}}

    state_file = tmp_path / "control-state.json"
    result = poll_control_bot_once(
        token="control-token",
        allowed_owner_ids={42},
        public_app_url="https://example.test/vital-shevron/",
        state_file=state_file,
        api_request=api,
    )

    assert result["processed_updates"] == 1
    send = next(call for call in calls if call[1] == "sendMessage")
    assert send[0] == "control-token"
    assert send[2]["reply_markup"] == {
        "inline_keyboard": [
            [
                {
                    "text": "Открыть управление",
                    "web_app": {"url": "https://example.test/vital-shevron/"},
                }
            ]
        ]
    }
    assert "apply" not in str(send[2]).lower()
    assert state_file.stat().st_mode & 0o777 == 0o600


def test_control_bot_denies_non_owner_and_deduplicates_offset(tmp_path: Path) -> None:
    calls: list[tuple[str, str, dict]] = []

    def api(token: str, method: str, payload: dict) -> dict:
        calls.append((token, method, payload))
        if method == "getUpdates":
            return {
                "ok": True,
                "result": [
                    {
                        "update_id": 50,
                        "message": {
                            "from": {"id": 99},
                            "chat": {"id": 99, "type": "private"},
                            "text": "/start",
                        },
                    }
                ],
            }
        return {"ok": True, "result": {"message_id": 11}}

    state_file = tmp_path / "state.json"
    first = poll_control_bot_once(
        token="control-token",
        allowed_owner_ids={42},
        public_app_url="https://example.test/vital-shevron/",
        state_file=state_file,
        api_request=api,
    )
    second = poll_control_bot_once(
        token="control-token",
        allowed_owner_ids={42},
        public_app_url="https://example.test/vital-shevron/",
        state_file=state_file,
        api_request=api,
    )

    sends = [call for call in calls if call[1] == "sendMessage"]
    assert first["denied_updates"] == 1
    assert second["processed_updates"] == 0
    assert len(sends) == 1
    assert sends[0][2]["text"] == "Доступ запрещён."


def test_control_bot_preflight_blocks_webhook_and_has_separate_files() -> None:
    assert DEFAULT_CONTROL_STATE_FILE != DEFAULT_STATE_FILE
    assert DEFAULT_CONTROL_LOCK_FILE != DEFAULT_LOCK_FILE

    with pytest.raises(ControlBotError, match="webhook_active"):
        control_bot_preflight(
            token="control-token",
            api_request=lambda token, method, payload: {
                "ok": True,
                "result": {"url": "https://webhook.example/secret"},
            },
        )
    ok = control_bot_preflight(
        token="control-token",
        api_request=lambda token, method, payload: {"ok": True, "result": {"url": ""}},
    )
    assert ok == {"ok": True, "webhook": "disabled"}
    with pytest.raises(ControlBotError, match="webhook_preflight_invalid"):
        control_bot_preflight(
            token="control-token",
            api_request=lambda token, method, payload: {"ok": True, "result": []},
        )


def test_control_bot_menu_is_owner_scoped_web_app_only() -> None:
    calls: list[tuple[str, str, dict]] = []
    configure_control_bot_menu(
        token="control-token",
        allowed_owner_ids={42},
        public_app_url="https://example.test/vital-shevron/",
        api_request=lambda token, method, payload: calls.append((token, method, payload))
        or {"ok": True},
    )
    assert calls == [
        (
            "control-token",
            "setChatMenuButton",
            {
                "chat_id": 42,
                "menu_button": {
                    "type": "web_app",
                    "text": "Открыть управление",
                    "web_app": {"url": "https://example.test/vital-shevron/"},
                },
            },
        )
    ]


def test_control_bot_loop_uses_exclusive_short_polling_lock(tmp_path: Path) -> None:
    lock_file = tmp_path / "control.lock"
    lock_file.touch(mode=0o600)
    with lock_file.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ControlBotError, match="polling_lock_busy"):
            run_control_bot_loop(
                token="control-token",
                allowed_owner_ids={42},
                public_app_url="https://example.test/vital-shevron/",
                state_file=tmp_path / "state.json",
                lock_file=lock_file,
                max_iterations=1,
                api_request=lambda token, method, payload: {"ok": True, "result": []},
            )
