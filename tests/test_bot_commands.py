from __future__ import annotations

import json
from pathlib import Path

import pytest

from takterra_agent.bot.dispatcher import dispatch_message
from takterra_agent.bot.telegram_runner import (
    load_telegram_bot_token,
    poll_once,
    send_preview_command,
)
from takterra_agent.cli import main
from takterra_agent.core.run_manifest import manifest_from_summary, write_run_manifest


def test_bot_help_lists_read_only_mvp_commands() -> None:
    result = dispatch_message("/help")

    assert result.ok is True
    assert "Write-кнопок нет" in result.text
    assert "`/status`" in result.text
    assert "`/approvals`" in result.text


def test_bot_status_uses_latest_run_manifest(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "2026-06-18" / "status_preflight_test"
    manifest = manifest_from_summary(
        summary={
            "run_id": "status_preflight_test",
            "started_at": "2026-06-18T10:00:00",
            "overall_status": "ok",
            "artifacts": {"summary": str(run_dir / "summary.json")},
        },
        task="status-preflight",
        mode="read_only",
        risk="none",
        marketplaces=["ozon", "wb"],
    )
    write_run_manifest(data_dir=tmp_path, run_dir=run_dir, manifest=manifest)

    result = dispatch_message("/status", data_dir=tmp_path)

    assert result.ok is True
    assert "Статус проекта" in result.text
    assert "`status_preflight_test`" in result.text
    assert "Изменений в магазинах не выполнял" in result.text
    assert result.artifacts["summary"] == str(run_dir / "summary.json")


def test_bot_status_reports_missing_runtime_data(tmp_path: Path) -> None:
    result = dispatch_message("/status", data_dir=tmp_path)

    assert result.ok is False
    assert result.blocked_reason == "no_runtime_data"
    assert "я не могу это подтвердить" in result.text


def test_bot_approvals_summarizes_open_packages(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "pending" / "reviews_questions_test_pending" / "manifest.json",
        {
            "pending_id": "reviews_questions_test_pending",
            "run_id": "reviews_questions_test",
            "status": "pending_owner_review",
            "created_at": "2026-06-18T10:00:00",
        },
    )
    _write_json(
        tmp_path / "approved" / "reviews_questions_test_approved" / "approved_apply_plan.json",
        {
            "schema_version": "approval-package/v1",
            "package_type": "reviews_questions",
            "status": "approved",
            "approved_id": "reviews_questions_test_approved",
            "pending_id": "reviews_questions_test_pending",
            "source_run_id": "reviews_questions_test",
            "created_at": "2026-06-18T10:10:00",
            "actions": [],
        },
    )

    result = dispatch_message("/approvals", data_dir=tmp_path)

    assert result.ok is True
    assert "Согласования" in result.text
    assert "`approved`: `2`" in result.text
    assert "reviews_questions_test_approved" in result.text


def test_bot_rejects_unsupported_write_like_command() -> None:
    result = dispatch_message("/apply-ozon-elastic")

    assert result.ok is False
    assert result.blocked_reason == "unsupported_command"
    assert "не поддерживается" in result.text


def test_cli_bot_preview_text_and_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["bot", "preview", "--message", "/help", "--data-dir", str(tmp_path)]) == 0
    assert "Telegram MVP" in capsys.readouterr().out

    assert main(["bot", "preview", "--message", "/status", "--data-dir", str(tmp_path), "--json"]) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["blocked_reason"] == "no_runtime_data"


def test_telegram_token_loads_from_external_file(tmp_path: Path) -> None:
    token_file = tmp_path / "telegram-token.txt"
    token_file.write_text("secret-token\n", encoding="utf-8")

    assert load_telegram_bot_token(token_file=token_file) == "secret-token"


def test_send_preview_command_uses_mock_api_without_exposing_token(tmp_path: Path) -> None:
    calls: list[tuple[str, str, dict]] = []

    def fake_api(token: str, method: str, payload: dict) -> dict:
        calls.append((token, method, payload))
        return {"ok": True, "result": {"message_id": 10}}

    result = send_preview_command(
        token="secret-token",
        chat_id=123,
        message="/help",
        data_dir=tmp_path,
        api_request=fake_api,
    )

    assert result["ok"] is True
    assert calls[0][0] == "secret-token"
    assert calls[0][1] == "sendMessage"
    assert "Telegram MVP" in calls[0][2]["text"]
    assert "secret-token" not in json.dumps(result, ensure_ascii=False)


def test_poll_once_dispatches_allowed_chat_and_writes_offset(tmp_path: Path) -> None:
    calls: list[tuple[str, str, dict]] = []

    def fake_api(token: str, method: str, payload: dict) -> dict:
        calls.append((token, method, payload))
        if method == "getUpdates":
            return {
                "ok": True,
                "result": [
                    {
                        "update_id": 101,
                        "message": {
                            "chat": {"id": 123},
                            "text": "/help",
                            "message_thread_id": 55,
                        },
                    },
                    {
                        "update_id": 102,
                        "message": {
                            "chat": {"id": 999},
                            "text": "/help",
                        },
                    },
                ],
            }
        return {"ok": True, "result": {"message_id": 11}}

    state_file = tmp_path / ".sessions" / "telegram" / "state.json"
    result = poll_once(
        token="secret-token",
        data_dir=tmp_path,
        state_file=state_file,
        allowed_chat_ids={123},
        api_request=fake_api,
    )

    assert result["ok"] is True
    assert result["processed_updates"] == 1
    assert result["sent_messages"] == 1
    assert result["skipped_updates"] == 1
    assert json.loads(state_file.read_text(encoding="utf-8"))["offset"] == 103
    send_call = [call for call in calls if call[1] == "sendMessage"][0]
    assert send_call[2]["message_thread_id"] == 55


def test_cli_bot_send_preview_requires_token(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["bot", "send-preview", "--message", "/help", "--chat-id", "123", "--data-dir", str(tmp_path)]) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is False
    assert "missing Telegram bot token" in output["error"]


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
