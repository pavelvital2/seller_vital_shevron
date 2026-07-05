from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from seller_agent.bot.dispatcher import dispatch_callback, dispatch_message
from seller_agent.bot.job_notifier import notify_telegram_job_result
from seller_agent.bot.runtime_jobs import dispatch_runtime_job_message
from seller_agent.bot.telegram_runner import (
    load_telegram_bot_token,
    poll_loop,
    poll_once,
    safe_report_attachment_paths,
    send_preview_command,
)
from seller_agent.cli import main
from seller_agent.core.job_store import JobStore
from seller_agent.core.run_manifest import manifest_from_summary, write_run_manifest


def test_bot_help_lists_read_only_mvp_commands() -> None:
    result = dispatch_message("/help")

    assert result.ok is True
    assert "безопасные кнопки Ozon Elastic / WB акции" in result.text
    assert "`/status`" in result.text
    assert "`/approvals`" in result.text
    assert "`/elastic`" in result.text
    assert "`/ozon-actions`" in result.text
    assert "`/wb-actions`" in result.text
    assert "`/jobs`" in result.text


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


def test_bot_elastic_builds_plan_and_apply_button(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from seller_agent.bot import commands

    report = tmp_path / "runs" / "2026-06-30" / "ozon_elastic_plan_test" / "ozon_elastic_dry_run.md"

    def fake_plan(**kwargs: object) -> dict:
        assert kwargs["data_dir"] == tmp_path
        return {
            "run_id": "ozon_elastic_plan_test",
            "summary": {
                "action_id": "123",
                "action_name": "Эластичный бустинг",
                "active_rows": 10,
                "candidate_rows": 20,
                "merged_unique_products": 25,
                "add_to_action": 2,
                "update_action_price": 3,
                "update_action_price_with_changed_price": 1,
                "deactivate_from_action": 0,
                "skip_candidate": 20,
                "blocked": 0,
            },
            "artifacts": {
                "report": str(report),
                "xlsx": str(report.with_suffix(".xlsx")),
                "csv": str(report.with_suffix(".csv")),
            },
        }

    monkeypatch.setattr(commands, "run_ozon_elastic_plan", fake_plan)

    result = dispatch_message("/elastic", data_dir=tmp_path)

    assert result.ok is True
    assert result.mode == "dry_run"
    assert "свежий dry-run построен" in result.text
    assert "добавить в акцию: `2`" in result.text
    assert result.reply_markup["inline_keyboard"][0][0]["callback_data"] == "oe_apply:ozon_elastic_plan_test"
    assert result.artifacts["report"] == str(report)


def test_bot_elastic_plan_without_write_rows_has_no_apply_button(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from seller_agent.bot import commands

    def fake_plan(**kwargs: object) -> dict:
        return {
            "run_id": "ozon_elastic_plan_no_changes",
            "summary": {
                "action_id": "123",
                "action_name": "Эластичный бустинг",
                "active_rows": 10,
                "candidate_rows": 20,
                "merged_unique_products": 25,
                "add_to_action": 0,
                "update_action_price": 3,
                "update_action_price_with_changed_price": 0,
                "deactivate_from_action": 0,
                "skip_candidate": 20,
                "blocked": 0,
            },
            "artifacts": {},
        }

    monkeypatch.setattr(commands, "run_ozon_elastic_plan", fake_plan)

    result = dispatch_message("/elastic", data_dir=tmp_path)

    assert result.ok is True
    assert "Изменений к применению нет" in result.text
    assert result.reply_markup == {}


def test_bot_elastic_callback_applies_specific_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from seller_agent.bot import commands

    calls: list[dict] = []

    def fake_apply(**kwargs: object) -> dict:
        calls.append(kwargs)
        return {
            "run_id": "ozon_elastic_apply_test",
            "overall_status": "warning",
            "approved_plan_run_id": "ozon_elastic_plan_test",
            "fresh_plan": {"run_id": "ozon_elastic_plan_fresh"},
            "applied": {"activate_rows_count": 4, "deactivate_rows_count": 1},
            "drift": {
                "skipped_due_to_drift_count": 2,
                "skipped_due_to_drift_product_count": 2,
                "skipped_due_to_drift_product_ids": ["111", "222"],
            },
            "verify": {
                "status": "ok",
                "price_mismatches": [],
                "still_active_deactivated": [],
            },
            "artifacts": {"report": str(tmp_path / "runs" / "apply.md")},
        }

    monkeypatch.setattr(commands, "run_ozon_elastic_apply", fake_apply)

    result = dispatch_callback("oe_apply:ozon_elastic_plan_test", data_dir=tmp_path)

    assert result.ok is True
    assert result.mode == "apply"
    assert "apply завершен" in result.text
    assert "добавить/обновить: `4`" in result.text
    assert calls[0]["data_dir"] == tmp_path
    assert calls[0]["plan_run_id"] == "ozon_elastic_plan_test"
    assert calls[0]["confirmed_by_user"] is True


def test_bot_elastic_callback_rejects_invalid_plan_id(tmp_path: Path) -> None:
    result = dispatch_callback("oe_apply:../../bad", data_dir=tmp_path)

    assert result.ok is False
    assert result.blocked_reason == "invalid_plan_run_id"
    assert "Изменений в Ozon/WB не выполнял" in result.text


def test_bot_ozon_actions_builds_plan_and_apply_button(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from seller_agent.bot import commands

    report = tmp_path / "runs" / "2026-07-05" / "ozon_actions_optimizer_plan_test" / "ozon_actions_optimizer_report.md"

    def fake_plan(**kwargs: object) -> dict:
        assert kwargs["data_dir"] == tmp_path
        return {
            "run_id": "ozon_actions_optimizer_plan_test",
            "summary": {
                "actions_total": 8,
                "actions_with_rows": 8,
                "products_with_action_offers": 545,
                "offers_total": 3370,
                "valid_offers": 1004,
                "blocked_offers": 2366,
                "lk_boost_actions_with_numeric_boost": 5,
                "recommended_keep": 501,
                "recommended_add": 1,
                "recommended_update": 0,
                "recommended_switch_review": 2,
                "recommended_skip": 43,
            },
            "artifacts": {
                "report": str(report),
                "xlsx": str(report.with_suffix(".xlsx")),
                "recommendations_csv": str(report.with_name("ozon_actions_optimizer_recommendations.csv")),
            },
        }

    monkeypatch.setattr(commands, "run_ozon_actions_optimizer_plan", fake_plan)

    result = dispatch_message("/ozon-actions", data_dir=tmp_path)

    assert result.ok is True
    assert result.mode == "dry_run"
    assert "Ozon все акции" in result.text
    assert "переключить на другую акцию: `2`" in result.text
    assert result.reply_markup["inline_keyboard"][0][0]["callback_data"] == "oza_apply:ozon_actions_optimizer_plan_test"
    assert result.artifacts["report"] == str(report)


def test_bot_ozon_actions_plan_without_write_rows_has_no_apply_button(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from seller_agent.bot import commands

    def fake_plan(**kwargs: object) -> dict:
        return {
            "run_id": "ozon_actions_optimizer_plan_no_changes",
            "summary": {
                "actions_total": 8,
                "actions_with_rows": 8,
                "products_with_action_offers": 545,
                "offers_total": 3370,
                "valid_offers": 1004,
                "blocked_offers": 2366,
                "lk_boost_actions_with_numeric_boost": 5,
                "recommended_keep": 502,
                "recommended_add": 0,
                "recommended_update": 0,
                "recommended_switch_review": 0,
                "recommended_skip": 43,
            },
            "artifacts": {},
        }

    monkeypatch.setattr(commands, "run_ozon_actions_optimizer_plan", fake_plan)

    result = dispatch_message("/ozon-actions", data_dir=tmp_path)

    assert result.ok is True
    assert "Изменений к применению нет" in result.text
    assert result.reply_markup == {}


def test_bot_ozon_actions_callback_applies_specific_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from seller_agent.bot import commands

    calls: list[dict] = []

    def fake_apply(**kwargs: object) -> dict:
        calls.append(kwargs)
        return {
            "run_id": "ozon_actions_optimizer_apply_test",
            "overall_status": "warning",
            "approved_plan_run_id": "ozon_actions_optimizer_plan_test",
            "fresh_plan": {"run_id": "ozon_actions_optimizer_plan_fresh"},
            "applied": {
                "activate_rows_count": 3,
                "switch_rows_count": 1,
                "deactivate_rows_count": 1,
                "rejected_count": 0,
            },
            "drift": {
                "skipped_due_to_drift_count": 2,
                "skipped_due_to_drift_product_count": 2,
                "skipped_due_to_drift_product_ids": ["111", "222"],
            },
            "verify": {"status": "ok", "mismatches": []},
            "artifacts": {"report": str(tmp_path / "runs" / "ozon_actions_apply.md")},
        }

    monkeypatch.setattr(commands, "run_ozon_actions_optimizer_apply", fake_apply)

    result = dispatch_callback("oza_apply:ozon_actions_optimizer_plan_test", data_dir=tmp_path)

    assert result.ok is True
    assert result.mode == "apply"
    assert "apply завершен" in result.text
    assert "переключений: `1`" in result.text
    assert calls[0]["data_dir"] == tmp_path
    assert calls[0]["plan_run_id"] == "ozon_actions_optimizer_plan_test"
    assert calls[0]["confirmed_by_user"] is True


def test_bot_ozon_actions_callback_rejects_invalid_plan_id(tmp_path: Path) -> None:
    result = dispatch_callback("oza_apply:../../bad", data_dir=tmp_path)

    assert result.ok is False
    assert result.blocked_reason == "invalid_plan_run_id"
    assert "Изменений в Ozon/WB не выполнял" in result.text


def test_bot_wb_actions_builds_plan_and_apply_button(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from seller_agent.bot import commands

    run_dir = tmp_path / "runs" / "2026-06-30" / "wb_actions_discount_plan_70-55-55_test"
    report = run_dir / "wb-discount-calculation-active-actions-70-55-55.md"
    csv_path = run_dir / "wb-discount-calculation-active-actions-70-55-55.csv"
    run_dir.mkdir(parents=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["Действие", "Причина"], delimiter=";")
        writer.writeheader()
        writer.writerow({"Действие": "снизить скидку", "Причина": "скидка до порога <= 70%"})
        writer.writerow({"Действие": "снизить скидку", "Причина": "скидка до порога > 70% -> 55%"})
        writer.writerow({"Действие": "не менять", "Причина": "товара нет в активных акциях -> 55%"})

    def fake_plan(**kwargs: object) -> dict:
        assert kwargs["data_dir"] == tmp_path
        assert kwargs["scheme_text"] == "70-55-55"
        return {
            "run_id": "wb_actions_discount_plan_70-55-55_test",
            "summary": {
                "scheme": "70-55-55",
                "total_goods": 10,
                "in_promos": 8,
                "outside_promos": 2,
                "multiple_promos": 4,
                "changed_rows": 2,
                "to_change": 2,
                "increase": 0,
                "decrease": 2,
                "no_change": 8,
                "active_promos": 3,
                "future_promos": 1,
            },
            "artifacts": {
                "report": str(report),
                "xlsx": str(report.with_suffix(".xlsx")),
                "csv": str(csv_path),
            },
        }

    monkeypatch.setattr(commands, "run_wb_actions_discount_plan", fake_plan)

    result = dispatch_message("/wb-actions", data_dir=tmp_path)

    assert result.ok is True
    assert result.mode == "dry_run"
    assert "WB акции 70-55-55" in result.text
    assert "изменить скидку: `2`" in result.text
    assert "участие в акции с меньшей требуемой скидкой: `1`" in result.text
    assert result.reply_markup["inline_keyboard"][0][0]["callback_data"] == "wba_apply:wb_actions_discount_plan_70-55-55_test"
    assert result.artifacts["report"] == str(report)


def test_bot_wb_actions_plan_without_write_rows_has_no_apply_button(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from seller_agent.bot import commands

    def fake_plan(**kwargs: object) -> dict:
        return {
            "run_id": "wb_actions_discount_plan_70-55-55_no_changes",
            "summary": {
                "scheme": "70-55-55",
                "total_goods": 10,
                "in_promos": 8,
                "outside_promos": 2,
                "multiple_promos": 4,
                "changed_rows": 0,
                "to_change": 0,
                "increase": 0,
                "decrease": 0,
                "no_change": 10,
                "active_promos": 3,
                "future_promos": 1,
            },
            "artifacts": {},
        }

    monkeypatch.setattr(commands, "run_wb_actions_discount_plan", fake_plan)

    result = dispatch_message("/wb-actions", data_dir=tmp_path)

    assert result.ok is True
    assert "Изменений к применению нет" in result.text
    assert result.reply_markup == {}


def test_bot_wb_actions_callback_applies_specific_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from seller_agent.bot import commands

    calls: list[dict] = []

    def fake_apply(**kwargs: object) -> dict:
        calls.append(kwargs)
        return {
            "run_id": "wb_actions_discount_apply_test",
            "overall_status": "warning",
            "approved_plan_run_id": "wb_actions_discount_plan_70-55-55_test",
            "scheme": "70-55-55",
            "fresh_plan": {"run_id": "wb_actions_discount_plan_70-55-55_fresh"},
            "applied": {"payload_rows_count": 4, "upload_id": 12345},
            "drift": {
                "skipped_due_to_drift_count": 2,
                "skipped_due_to_drift_product_count": 2,
                "skipped_due_to_drift_nm_ids": [111, 222],
            },
            "verify": {
                "status": "ok",
                "polls": [
                    {
                        "status": {
                            "history": {
                                "data": {"data": {"successGoodsNumber": 4, "overAllGoodsNumber": 4}}
                            }
                        }
                    }
                ],
            },
            "artifacts": {"report": str(tmp_path / "runs" / "wb_apply.md")},
        }

    monkeypatch.setattr(commands, "run_wb_actions_discount_apply", fake_apply)

    result = dispatch_callback("wba_apply:wb_actions_discount_plan_70-55-55_test", data_dir=tmp_path)

    assert result.ok is True
    assert result.mode == "apply"
    assert "apply завершен" in result.text
    assert "отправлено строк: `4`" in result.text
    assert "successful goods: `4` / `4`" in result.text
    assert calls[0]["data_dir"] == tmp_path
    assert calls[0]["plan_run_id"] == "wb_actions_discount_plan_70-55-55_test"
    assert calls[0]["confirmed_by_user"] is True


def test_bot_wb_actions_callback_rejects_invalid_plan_id(tmp_path: Path) -> None:
    result = dispatch_callback("wba_apply:../../bad", data_dir=tmp_path)

    assert result.ok is False
    assert result.blocked_reason == "invalid_plan_run_id"
    assert "Изменений в Ozon/WB не выполнял" in result.text


def test_bot_ozon_inbox_builds_fresh_package_and_apply_button(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from seller_agent.bot import commands

    pending_dir = tmp_path / "pending" / "ozon_inbox_test_pending"
    pending_dir.mkdir(parents=True)
    (pending_dir / "inbox_pending.json").write_text(
        json.dumps(
            {
                "messenger_actions": [
                    {"action_type": "send_chat_message"},
                    {"action_type": "mark_chat_read"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    report = tmp_path / "runs" / "2026-07-04" / "ozon_inbox_test" / "ozon_inbox_approval.md"

    def fake_triage(**kwargs: object) -> dict:
        assert kwargs["data_dir"] == tmp_path
        return {
            "run_id": "ozon_inbox_test",
            "overall_status": "ok",
            "actions_count": 3,
            "reviews": {"actions_count": 1},
            "messenger": {"total_unread_count": 2},
            "artifacts": {"report": str(report)},
        }

    monkeypatch.setattr(commands, "run_ozon_inbox_triage", fake_triage)

    result = dispatch_message("/ozon-inbox", data_dir=tmp_path)

    assert result.ok is True
    assert result.mode == "dry_run"
    assert "Ozon входящие" in result.text
    assert "ответы покупателям в чатах: `1`" in result.text
    assert result.reply_markup["inline_keyboard"][0][0]["callback_data"] == "ozin_apply:ozon_inbox_test"
    assert result.artifacts["report"] == str(report)


def test_bot_wb_inbox_builds_fresh_package_and_reports_notifications(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from seller_agent.bot import commands

    report = tmp_path / "runs" / "2026-07-04" / "wb_inbox_test" / "wb_inbox_approval.md"

    def fake_triage(**kwargs: object) -> dict:
        assert kwargs["data_dir"] == tmp_path
        return {
            "run_id": "wb_inbox_test",
            "overall_status": "warning",
            "actions_count": 2,
            "reviews": {"actions_count": 2},
            "wb_notifications": {
                "status": "ok",
                "items": [{"title": "Новость WB"}],
                "important_items": [{"title": "Изменение тарифов"}],
            },
            "artifacts": {"report": str(report)},
        }

    monkeypatch.setattr(commands, "run_wb_inbox_triage", fake_triage)

    result = dispatch_message("/wb-inbox", data_dir=tmp_path)

    assert result.ok is True
    assert "WB вопросы входят" in result.text
    assert "WB уведомления: `ok`" in result.text
    assert "важных WB новостей/уведомлений: `1`" in result.text
    assert result.reply_markup["inline_keyboard"][0][0]["callback_data"] == "wbin_apply:wb_inbox_test"


def test_bot_inbox_callbacks_apply_specific_packages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from seller_agent.bot import commands

    calls: list[tuple[str, dict]] = []

    def fake_run_inbox_apply_job(**kwargs: object) -> dict:
        calls.append((str(kwargs["task_id"]), kwargs))
        if kwargs["task_id"] == "ozon-inbox-apply":
            return {
                "job_id": "job_ozon",
                "run_id": "ozon_inbox_test_apply",
                "overall_status": "ok",
                "reviews": {
                    "apply": {
                        "applied_counts": {
                            "ozon_public_review_replies": 1,
                            "ozon_marked_viewed": 2,
                        }
                    }
                },
                "messenger": {"status": "ok", "mark_read": [{"ok": True}]},
                "artifacts": {"report": str(tmp_path / "ozon_apply.md")},
            }
        return {
            "job_id": "job_wb",
            "run_id": "wb_inbox_test_apply",
            "overall_status": "ok",
            "reviews": {
                "apply": {
                    "applied_counts": {
                        "wb_public_review_replies": 1,
                        "wb_question_answers": 1,
                    }
                }
            },
            "artifacts": {"report": str(tmp_path / "wb_apply.md")},
        }

    monkeypatch.setattr(commands, "_run_inbox_apply_job", fake_run_inbox_apply_job)

    ozon_result = dispatch_callback("ozin_apply:ozon_inbox_test", data_dir=tmp_path)
    wb_result = dispatch_callback("wbin_apply:wb_inbox_test", data_dir=tmp_path)

    assert ozon_result.ok is True
    assert "Ozon входящие apply" in ozon_result.text
    assert "Ozon уведомления mark-read: `1` из `1`" in ozon_result.text
    assert wb_result.ok is True
    assert "вопросы WB: `1`" in wb_result.text
    assert calls[0][0] == "ozon-inbox-apply"
    assert calls[0][1]["source_run_id"] == "ozon_inbox_test"
    assert calls[1][0] == "wb-inbox-apply"
    assert calls[1][1]["source_run_id"] == "wb_inbox_test"


def test_bot_inbox_callbacks_reject_invalid_ids(tmp_path: Path) -> None:
    ozon_result = dispatch_callback("ozin_apply:../../bad", data_dir=tmp_path)
    wb_result = dispatch_callback("wbin_apply:../../bad", data_dir=tmp_path)

    assert ozon_result.ok is False
    assert ozon_result.blocked_reason == "invalid_source_run_id"
    assert wb_result.ok is False
    assert wb_result.blocked_reason == "invalid_source_run_id"


def test_cli_bot_preview_text_and_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["bot", "preview", "--message", "/help", "--data-dir", str(tmp_path)]) == 0
    assert "Telegram bot" in capsys.readouterr().out

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
    assert "Telegram bot" in calls[0][2]["text"]
    assert "secret-token" not in json.dumps(result, ensure_ascii=False)


def test_send_preview_command_forwards_reply_markup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from seller_agent.bot import telegram_runner
    from seller_agent.bot.commands import TelegramCommandResult

    calls: list[tuple[str, str, dict]] = []
    markup = {"inline_keyboard": [[{"text": "Apply", "callback_data": "oe_apply:ozon_elastic_plan_test"}]]}

    monkeypatch.setattr(
        telegram_runner,
        "dispatch_message",
        lambda *args, **kwargs: TelegramCommandResult(
            command="/elastic",
            ok=True,
            text="Ozon Elastic",
            reply_markup=markup,
        ),
    )

    def fake_api(token: str, method: str, payload: dict) -> dict:
        calls.append((token, method, payload))
        return {"ok": True, "result": {"message_id": 10}}

    result = send_preview_command(
        token="secret-token",
        chat_id=123,
        message="/elastic",
        data_dir=tmp_path,
        api_request=fake_api,
    )

    assert result["ok"] is True
    assert calls[0][1] == "sendMessage"
    assert calls[0][2]["reply_markup"] == markup


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
            "runtime_db": Path("runtime/runtime.db"),
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


def test_runtime_job_dispatch_queues_live_status_and_deduplicates(tmp_path: Path) -> None:
    runtime_db = tmp_path / "runtime.db"

    first = dispatch_runtime_job_message(
        "/status",
        update_id=1001,
        chat_id=123,
        data_dir=tmp_path / "data",
        runtime_db=runtime_db,
        live_status=True,
    )
    duplicate = dispatch_runtime_job_message(
        "/status",
        update_id=1001,
        chat_id=123,
        data_dir=tmp_path / "data",
        runtime_db=runtime_db,
        live_status=True,
    )

    assert first is not None
    assert first.ok is True
    assert "поставлена в runtime-очередь" in first.text
    assert duplicate is not None
    assert "не поставлен в очередь второй раз" in duplicate.text

    store = JobStore(runtime_db)
    jobs = store.list_jobs()
    updates = store.get_telegram_update(1001)
    assert len(jobs) == 1
    assert jobs[0].task_id == "status-preflight"
    assert jobs[0].actor == "telegram:123"
    assert jobs[0].params == {"include_lk": True}
    assert updates is not None
    assert updates.processing_status == "queued"
    assert updates.job_id == jobs[0].job_id


def test_bot_jobs_show_and_cancel_runtime_jobs(tmp_path: Path) -> None:
    runtime_db = tmp_path / "runtime.db"
    store = JobStore(runtime_db)
    job = store.create_job(
        task_id="status-preflight",
        actor="telegram:123",
        job_id="job_status-preflight_test_001",
        status="queued",
    )

    jobs_result = dispatch_message("/jobs", runtime_db=runtime_db)
    show_result = dispatch_message(f"/job_{job.job_id}", runtime_db=runtime_db)
    cancel_result = dispatch_message(f"/cancel_{job.job_id}", runtime_db=runtime_db)
    after_cancel = store.get_job(job.job_id)

    assert jobs_result.ok is True
    assert job.job_id in jobs_result.text
    assert show_result.ok is True
    assert "status-preflight" in show_result.text
    assert cancel_result.ok is True
    assert "job отменена" in cancel_result.text
    assert after_cancel is not None
    assert after_cancel.status == "cancelled"


def test_poll_once_runtime_jobs_queues_live_today(tmp_path: Path) -> None:
    calls: list[tuple[str, str, dict]] = []
    runtime_db = tmp_path / "runtime.db"

    def fake_api(token: str, method: str, payload: dict) -> dict:
        calls.append((token, method, payload))
        if method == "getUpdates":
            return {
                "ok": True,
                "result": [
                    {
                        "update_id": 401,
                        "message": {
                            "chat": {"id": 123},
                            "text": "/today",
                            "message_thread_id": 55,
                        },
                    }
                ],
            }
        return {"ok": True, "result": {"message_id": 21}}

    result = poll_once(
        token="secret-token",
        data_dir=tmp_path / "data",
        state_file=tmp_path / ".sessions" / "telegram" / "state.json",
        allowed_chat_ids={123},
        live_today=True,
        runtime_jobs=True,
        runtime_db=runtime_db,
        api_request=fake_api,
    )

    assert result["ok"] is True
    assert result["processed_updates"] == 1
    send_call = [call for call in calls if call[1] == "sendMessage"][0]
    assert "Job ID:" in send_call[2]["text"]
    assert send_call[2]["message_thread_id"] == 55

    jobs = JobStore(runtime_db).list_jobs()
    assert len(jobs) == 1
    assert jobs[0].task_id == "daily-morning-report"
    assert jobs[0].params == {"refresh_preflight": True, "seller_v2": False, "seller_v3": True}


def test_notify_telegram_job_result_sends_text_and_safe_report(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    report_path = data_dir / "runs" / "2026-06-30" / "job_test" / "report.md"
    report_path.parent.mkdir(parents=True)
    report_path.write_text("# report", encoding="utf-8")
    runtime_db = tmp_path / "runtime.db"
    store = JobStore(runtime_db)
    job = store.create_job(
        task_id="status-preflight",
        actor="telegram:123",
        job_id="job_notify_test",
        status="queued",
    )
    store.register_telegram_update(
        update_id=501,
        chat_id="123",
        command="/status",
        job_id=job.job_id,
        payload={"thread_id": 55},
        processing_status="queued",
    )
    store.update_job_status(
        job.job_id,
        "success",
        result={"status": "ok", "artifacts": {"report": str(report_path)}},
    )
    calls: list[tuple[str, str, dict]] = []
    docs: list[tuple[str, str, dict, Path]] = []

    def fake_api(token: str, method: str, payload: dict) -> dict:
        calls.append((token, method, payload))
        return {"ok": True, "result": {"message_id": 31}}

    def fake_doc_api(token: str, method: str, payload: dict, document_path: Path) -> dict:
        docs.append((token, method, payload, document_path))
        return {"ok": True, "result": {"message_id": 32}}

    result = notify_telegram_job_result(
        token="secret-token",
        job_id=job.job_id,
        store=store,
        data_dir=data_dir,
        api_request=fake_api,
        document_api_request=fake_doc_api,
    )

    assert result.ok is True
    assert result.chat_id == 123
    assert result.thread_id == 55
    assert calls[0][1] == "sendMessage"
    assert "job_notify_test" in calls[0][2]["text"]
    assert calls[0][2]["message_thread_id"] == 55
    assert docs[0][1] == "sendDocument"
    assert docs[0][2]["message_thread_id"] == 55
    assert docs[0][3] == report_path.resolve()


def test_poll_once_dispatches_callback_query(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from seller_agent.bot import telegram_runner
    from seller_agent.bot.commands import TelegramCommandResult

    calls: list[tuple[str, str, dict]] = []
    dispatched: list[str] = []

    monkeypatch.setattr(
        telegram_runner,
        "dispatch_callback",
        lambda data, **kwargs: dispatched.append(data)
        or TelegramCommandResult(command="/elastic_apply", ok=True, text="Applied"),
    )

    def fake_api(token: str, method: str, payload: dict) -> dict:
        calls.append((token, method, payload))
        if method == "getUpdates":
            return {
                "ok": True,
                "result": [
                    {
                        "update_id": 301,
                        "callback_query": {
                            "id": "cb1",
                            "data": "oe_apply:ozon_elastic_plan_test",
                            "message": {
                                "chat": {"id": 123},
                                "message_thread_id": 55,
                            },
                        },
                    }
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
    assert dispatched == ["oe_apply:ozon_elastic_plan_test"]
    methods = [call[1] for call in calls]
    assert "answerCallbackQuery" in methods
    send_call = [call for call in calls if call[1] == "sendMessage"][0]
    assert send_call[2]["text"] == "Applied"
    assert send_call[2]["message_thread_id"] == 55
    assert json.loads(state_file.read_text(encoding="utf-8"))["offset"] == 302


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
