from __future__ import annotations

import json
from pathlib import Path

import pytest

from seller_agent.bot.dispatcher import dispatch_message
from seller_agent.bot.telegram_runner import (
    load_telegram_bot_token,
    poll_loop,
    poll_once,
    safe_report_attachment_paths,
    send_preview_command,
)
from seller_agent.cli import main
from seller_agent.core.run_manifest import manifest_from_summary, write_run_manifest


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


def test_bot_status_live_mode_builds_fresh_preflight(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from seller_agent.bot import commands
    from seller_agent.core.workflow_runner import WorkflowRunResult

    workflow_calls: list[dict] = []
    summary = {
        "run_id": "status_preflight_test",
        "overall_status": "warning",
        "checks": {
            "ozon_api": {"status": "ok"},
            "ozon_performance_api": {"status": "ok"},
            "wb_api": {"status": "ok"},
            "master_catalog": {"status": "warning", "error": "catalog is stale"},
            "ozon_cdp": {"status": "error", "error": "CDP port is not listening"},
        },
        "artifacts": {
            "report": str(tmp_path / "runs" / "status_preflight_report.md"),
            "summary": str(tmp_path / "runs" / "summary.json"),
        },
    }

    class FakeWorkflowRunner:
        def __init__(self, **kwargs: object) -> None:
            workflow_calls.append({"init": kwargs})

        def run_read_only(self, task_name: str, *, inputs: dict | None = None) -> WorkflowRunResult:
            workflow_calls.append({"task_name": task_name, "inputs": inputs})
            return WorkflowRunResult(
                task="status-preflight",
                command="status-preflight",
                title="Status preflight",
                ok=True,
                status="warning",
                mode="read_only",
                risk="none",
                summary=summary,
                artifacts=summary["artifacts"],
            )

    monkeypatch.setattr(commands, "WorkflowRunner", FakeWorkflowRunner)

    latest_result = dispatch_message("/status", data_dir=tmp_path)
    assert latest_result.ok is False
    assert latest_result.blocked_reason == "no_runtime_data"

    live_result = dispatch_message("/status", data_dir=tmp_path, live_status=True)

    assert live_result.ok is True
    assert "свежая read-only проверка выполнена" in live_result.text
    assert "status_preflight_test" in live_result.text
    assert "Ozon Seller API: `ok`" in live_result.text
    assert "Master catalog: `warning`" in live_result.text
    assert "CDP port is not listening" in live_result.text
    assert live_result.artifacts["report"].endswith("status_preflight_report.md")
    assert workflow_calls[0]["init"]["data_dir"] == tmp_path
    assert workflow_calls[1] == {"task_name": "status-preflight", "inputs": None}


def test_bot_today_live_mode_builds_fresh_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from seller_agent.bot import commands
    from seller_agent.core.workflow_runner import WorkflowRunResult

    workflow_calls: list[dict] = []
    summary = {
        "run_id": "daily_morning_report_v3_test",
        "overall_status": "warning",
        "business": {
            "periods": {"yesterday": "2026-06-17"},
            "ozon": {
                "orders": {"yesterday": {"ordered_units": 10, "revenue": 2000}},
                "finance_buyouts": {"buyout_units": 8, "buyout_amount": 1600},
                "finance_expenses": {"total_expenses": 500},
                "stocks": {"present_total": 100, "out_of_stock_count": 2},
                "communications": {"unanswered_feedbacks": 1, "unanswered_questions": 0},
            },
            "wb": {
                "orders": {"yesterday": {"active_orders": 12, "amount": 2400}},
                "sales": {"yesterday": {"sales_rows": 9, "sales_amount": 1800}},
                "finance_expenses": {"total_expenses": 600},
                "stocks": {"quantity_total": 120, "zero_stock_count": 3},
                "communications": {"unanswered_feedbacks": 2, "unanswered_questions": 1},
            },
        },
        "actions_v3": {
            "ozon": {"active_actions": 1, "products_in_actions": 50, "products_not_in_actions": 5},
            "wb": {"active_actions": 2, "products_in_actions": 60, "products_not_in_actions": 6},
        },
        "unified_catalog": {
            "products": 710,
            "confirmed_products": 269,
            "ozon_only_products": 279,
            "wb_only_products": 162,
        },
        "executive_summary": ["Период отчета: 2026-06-17 00:00-23:59 MSK."],
        "artifacts": {
            "report": str(tmp_path / "daily_morning_report_v3.md"),
            "summary": str(tmp_path / "summary.json"),
        },
    }

    class FakeWorkflowRunner:
        def __init__(self, **kwargs: object) -> None:
            workflow_calls.append({"init": kwargs})

        def run_read_only(self, task_name: str, *, inputs: dict | None = None) -> WorkflowRunResult:
            workflow_calls.append({"task_name": task_name, "inputs": inputs})
            return WorkflowRunResult(
                task="daily-morning-report",
                command="daily-morning-report",
                title="Daily morning report",
                ok=True,
                status="warning",
                mode="read_only",
                risk="low",
                summary=summary,
                artifacts=summary["artifacts"],
            )

    monkeypatch.setattr(commands, "WorkflowRunner", FakeWorkflowRunner)

    latest_result = dispatch_message("/today", data_dir=tmp_path)
    assert latest_result.ok is False
    assert latest_result.blocked_reason == "no_runtime_data"

    live_result = dispatch_message("/today", data_dir=tmp_path, live_today=True)

    assert live_result.ok is True
    assert "свежий read-only отчет построен" in live_result.text
    assert "daily_morning_report_v3_test" in live_result.text
    assert "Ozon: заказы `10`" in live_result.text
    assert "WB: заказы `12`" in live_result.text
    assert "Unified: товаров `710`" in live_result.text
    assert live_result.artifacts["report"].endswith("daily_morning_report_v3.md")
    assert workflow_calls[0]["init"]["data_dir"] == tmp_path
    assert workflow_calls[1] == {
        "task_name": "daily-morning-report",
        "inputs": {"seller_v3": True},
    }


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


def test_bot_catalog_uses_unified_catalog_manifest(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "2026-06-18" / "catalog_build_unified_test"
    summary_path = run_dir / "summary.json"
    _write_json(
        summary_path,
        {
            "run_id": "catalog_build_unified_test",
            "started_at": "2026-06-18T10:00:00",
            "overall_status": "ok",
            "summary": {
                "unified_products": 710,
                "confirmed_products": 269,
                "ozon_only_products": 279,
                "wb_only_products": 162,
                "issue_count": 0,
            },
            "artifacts": {"summary": str(summary_path), "report": str(run_dir / "unified_catalog_report.md")},
        },
    )
    manifest = manifest_from_summary(
        summary=json.loads(summary_path.read_text(encoding="utf-8")),
        task="catalog-build-unified",
        mode="read_only",
        risk="low",
        marketplaces=["ozon", "wb"],
    )
    write_run_manifest(data_dir=tmp_path, run_dir=run_dir, manifest=manifest)

    result = dispatch_message("/catalog", data_dir=tmp_path)

    assert result.ok is True
    assert "последний запуск `catalog-build-unified`" in result.text
    assert "товаров в unified catalog: `710`" in result.text
    assert "связанных Ozon+WB: `269`" in result.text
    assert result.artifacts["summary"] == str(summary_path)


def test_bot_catalog_search_finds_product_by_ozon_offer_and_enriches_barcodes(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "catalog" / "unified" / "products.json",
        [
            {
                "internal_product_id": "chev_nr_svo_pict0001",
                "internal_sku": "chev_nr_svo_pict0001",
                "product_name": "Шеврон СВО Вспомни свои корни",
                "product_group": "chev",
                "pack_qty": "1",
                "cost_total": "85",
                "cost_per_unit": "85",
                "ozon_offer_id": "pict0152",
                "ozon_product_id": "2729922386",
                "ozon_sku": "2864486048",
                "wb_vendor_code": "svopict0028_pict0152",
                "wb_nm_id": "605088924",
                "mapping_status": "confirmed",
                "active_ozon": "true",
                "active_wb": "true",
            }
        ],
    )
    _write_text(
        tmp_path / "catalog" / "ozon" / "processed" / "ozon_catalog.csv",
        "barcode,offer_id,product_id,sku,status,title\n"
        "OZN2864486048,pict0152,2729922386,2864486048,Продается,Шеврон СВО\n",
    )
    _write_text(
        tmp_path / "catalog" / "wb" / "processed" / "wb_catalog.csv",
        "barcode,brand,nm_id,status,subject,title,vendor_code\n"
        "2043894180777,,605088924,present,Декор,Шеврон СВО,svopict0028_pict0152\n",
    )

    result = dispatch_message("/catalog pict0152", data_dir=tmp_path)

    assert result.ok is True
    assert "найден 1 товар" in result.text
    assert "chev_nr_svo_pict0001" in result.text
    assert "OZN2864486048" in result.text
    assert "2043894180777" in result.text
    assert "Изменений в Ozon/WB не выполнял" in result.text


def test_bot_catalog_search_finds_product_by_wb_barcode(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "catalog" / "unified" / "products.json",
        [
            {
                "internal_product_id": "wb:wb-only-1",
                "product_name": "Шеврон только WB",
                "wb_vendor_code": "wb-only-1",
                "wb_nm_id": "1001",
                "mapping_status": "wb_only",
                "active_ozon": "false",
                "active_wb": "true",
            }
        ],
    )
    _write_text(
        tmp_path / "catalog" / "wb" / "processed" / "wb_catalog.csv",
        "barcode,brand,nm_id,status,subject,title,vendor_code\n"
        "7777777777777,,1001,present,Декор,Шеврон только WB,wb-only-1\n",
    )

    result = dispatch_message("/catalog 7777777777777", data_dir=tmp_path)

    assert result.ok is True
    assert "wb:wb-only-1" in result.text
    assert "7777777777777" in result.text


def test_bot_catalog_search_returns_short_list_for_title_matches(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "catalog" / "unified" / "products.json",
        [
            {
                "internal_product_id": "chev_nr_svo_text0001",
                "internal_sku": "chev_nr_svo_text0001",
                "product_name": "Шеврон СВО текст",
                "ozon_offer_id": "svo-text",
                "mapping_status": "ozon_only",
            },
            {
                "internal_product_id": "chev_nr_svo_pict0001",
                "internal_sku": "chev_nr_svo_pict0001",
                "product_name": "Шеврон СВО картинка",
                "wb_vendor_code": "svo-pict",
                "mapping_status": "wb_only",
            },
        ],
    )

    result = dispatch_message("/catalog шеврон сво", data_dir=tmp_path)

    assert result.ok is True
    assert "найдено `2` товаров" in result.text
    assert "chev_nr_svo_pict0001" in result.text
    assert "chev_nr_svo_text0001" in result.text
    assert "Для точной карточки" in result.text


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


def test_safe_report_attachment_paths_only_allows_report_artifacts(tmp_path: Path) -> None:
    report = tmp_path / "runs" / "2026-06-18" / "daily_report" / "daily_morning_report_v3.md"
    report.parent.mkdir(parents=True)
    report.write_text("report", encoding="utf-8")
    summary = report.parent / "summary.json"
    summary.write_text("{}", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    secret_report = tmp_path / "runs" / "2026-06-18" / "secret_report.md"
    secret_report.write_text("secret", encoding="utf-8")

    paths = safe_report_attachment_paths(
        artifacts={
            "report": str(report),
            "summary": str(summary),
            "outside_report": str(outside),
            "secret": str(secret_report),
        },
        data_dir=tmp_path,
        project_root=tmp_path,
    )

    assert paths == [report.resolve()]


def test_send_preview_command_attaches_safe_report_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from seller_agent.bot import telegram_runner
    from seller_agent.bot.commands import TelegramCommandResult

    report = tmp_path / "runs" / "2026-06-18" / "daily_report" / "daily_morning_report_v3.md"
    report.parent.mkdir(parents=True)
    report.write_text("report", encoding="utf-8")

    monkeypatch.setattr(
        telegram_runner,
        "dispatch_message",
        lambda *args, **kwargs: TelegramCommandResult(
            command="/today",
            ok=True,
            text="Ежедневный отчет",
            artifacts={"report": str(report), "summary": str(report.parent / "summary.json")},
        ),
    )

    calls: list[tuple[str, str, dict]] = []
    document_calls: list[tuple[str, str, dict, Path]] = []

    def fake_api(token: str, method: str, payload: dict) -> dict:
        calls.append((token, method, payload))
        return {"ok": True, "result": {"message_id": 10}}

    def fake_document_api(token: str, method: str, payload: dict, document_path: Path) -> dict:
        document_calls.append((token, method, payload, document_path))
        return {"ok": True, "result": {"message_id": 11}}

    result = send_preview_command(
        token="secret-token",
        chat_id=123,
        message="/today",
        data_dir=tmp_path,
        api_request=fake_api,
        document_api_request=fake_document_api,
    )

    assert result["ok"] is True
    assert calls[0][1] == "sendMessage"
    assert document_calls == [
        ("secret-token", "sendDocument", {"chat_id": 123}, report.resolve())
    ]
    assert result["sent_documents"][0]["message_id"] == 11
    assert "secret-token" not in json.dumps(result, ensure_ascii=False)


def test_send_preview_command_forwards_live_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from seller_agent.bot import telegram_runner
    from seller_agent.bot.commands import TelegramCommandResult

    calls: list[dict] = []

    def fake_dispatch(message: str, **kwargs: object) -> TelegramCommandResult:
        calls.append({"message": message, **kwargs})
        return TelegramCommandResult(command="/status", ok=True, text="Статус проекта")

    def fake_api(token: str, method: str, payload: dict) -> dict:
        return {"ok": True, "result": {"message_id": 10}}

    monkeypatch.setattr(telegram_runner, "dispatch_message", fake_dispatch)

    result = send_preview_command(
        token="secret-token",
        chat_id=123,
        message="/status",
        data_dir=tmp_path,
        live_status=True,
        api_request=fake_api,
    )

    assert result["ok"] is True
    assert calls == [
        {
            "message": "/status",
            "data_dir": tmp_path,
            "live_today": False,
            "live_status": True,
        }
    ]


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
    assert result["received_chat_ids"] == [123, 999]
    assert result["processed_chat_ids"] == [123]
    assert result["skipped_chat_ids"] == [999]
    assert json.loads(state_file.read_text(encoding="utf-8"))["offset"] == 103
    send_call = [call for call in calls if call[1] == "sendMessage"][0]
    assert send_call[2]["message_thread_id"] == 55


def test_poll_loop_requires_allowed_chat_ids(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="allowed_chat_ids"):
        poll_loop(
            token="secret-token",
            data_dir=tmp_path,
            state_file=tmp_path / ".sessions" / "telegram" / "state.json",
            lock_file=tmp_path / ".sessions" / "telegram" / "lock",
            allowed_chat_ids=set(),
            max_iterations=1,
            emit_logs=False,
        )


def test_poll_loop_runs_one_iteration_with_lock(tmp_path: Path) -> None:
    def fake_api(token: str, method: str, payload: dict) -> dict:
        if method == "getUpdates":
            return {
                "ok": True,
                "result": [
                    {
                        "update_id": 201,
                        "message": {"chat": {"id": 123}, "text": "/help"},
                    }
                ],
            }
        return {"ok": True, "result": {"message_id": 12}}

    result = poll_loop(
        token="secret-token",
        data_dir=tmp_path,
        state_file=tmp_path / ".sessions" / "telegram" / "state.json",
        lock_file=tmp_path / ".sessions" / "telegram" / "lock",
        allowed_chat_ids={123},
        max_iterations=1,
        api_request=fake_api,
        emit_logs=False,
    )

    assert result["ok"] is True
    assert result["iterations"] == 1
    assert result["processed_updates"] == 1
    assert result["sent_messages"] == 1


def test_cli_bot_send_preview_requires_token(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["bot", "send-preview", "--message", "/help", "--chat-id", "123", "--data-dir", str(tmp_path)]) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is False
    assert "missing Telegram bot token" in output["error"]


def test_cli_bot_poll_loop_requires_allowlist(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    token_file = tmp_path / "token.txt"
    token_file.write_text("secret-token", encoding="utf-8")

    assert main(["bot", "poll-loop", "--token-file", str(token_file), "--max-iterations", "1"]) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is False
    assert "requires --allowed-chat-id" in output["error"]


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
