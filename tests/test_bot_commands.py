from __future__ import annotations

import json
from pathlib import Path

import pytest

from takterra_agent.bot.dispatcher import dispatch_message
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


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
