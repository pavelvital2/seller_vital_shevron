from __future__ import annotations

from pathlib import Path

from seller_agent.tasks.ozon_messenger_workflow import run_ozon_messenger_workflow
from seller_agent.tasks.telegram_report_sender import run_send_telegram_report


def test_send_telegram_report_sends_summary_and_document(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    report_path = tmp_path / "data" / "runs" / "2026-06-29" / "report.md"
    report_path.parent.mkdir(parents=True)
    report_path.write_text("# Report\n", encoding="utf-8")
    calls: list[tuple[str, dict, Path | None]] = []

    def fake_api_request(token, method, payload):  # type: ignore[no-untyped-def]
        calls.append((method, payload, None))
        return {"ok": True, "result": {"message_id": 10}}

    def fake_document_request(token, method, payload, document_path):  # type: ignore[no-untyped-def]
        calls.append((method, payload, document_path))
        return {"ok": True, "result": {"message_id": 11}}

    result = run_send_telegram_report(
        token="test-token",
        chat_id=-1001,
        thread_id=42336,
        report_path=Path("data/runs/2026-06-29/report.md"),
        summary="Краткий отчет",
        data_dir=Path("data"),
        api_request=fake_api_request,
        document_api_request=fake_document_request,
    )

    assert result["overall_status"] == "ok"
    assert result["summary_sent"] is True
    assert result["document_sent"] is True
    assert [call[0] for call in calls] == ["sendMessage", "sendDocument"]
    assert calls[0][1]["message_thread_id"] == 42336
    assert calls[1][2] == report_path.resolve()


def test_send_telegram_report_blocks_missing_or_unsafe_report(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = run_send_telegram_report(
        token="test-token",
        chat_id=-1001,
        report_path=Path("outside.md"),
        summary="Краткий отчет",
        data_dir=Path("data"),
        api_request=lambda *args, **kwargs: {"ok": True},  # type: ignore[arg-type]
        document_api_request=lambda *args, **kwargs: {"ok": True},  # type: ignore[arg-type]
    )

    assert result["overall_status"] == "blocked"
    assert result["blocked_reason"] == "report_file_not_safe_or_missing"
    assert result["document_sent"] is False


def test_ozon_messenger_workflow_blocks_until_adapter_is_implemented(tmp_path: Path) -> None:
    approved_path = tmp_path / "approved_apply_plan.json"
    approved_path.write_text("{}", encoding="utf-8")

    result = run_ozon_messenger_workflow(
        data_dir=tmp_path / "data",
        stage="apply",
        approved_path=approved_path,
        confirmed_by_user=True,
    )

    assert result["overall_status"] == "blocked"
    assert result["blocked_reason"] == "workflow_adapter_not_implemented"
    assert result["preflight"]["has_confirmation"] is True
    assert result["preflight"]["approved_path_exists"] is True
