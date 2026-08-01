from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from seller_agent.bot.dispatcher import dispatch_callback, dispatch_message
from seller_agent.bot.job_notifier import (
    build_job_result_presentation,
    build_job_result_text,
    notify_telegram_job_result,
)
from seller_agent.bot.runtime_jobs import dispatch_runtime_job_callback, dispatch_runtime_job_message
from seller_agent.bot.telegram_runner import (
    TelegramRunnerError,
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
    assert result.reply_markup["keyboard"][0][0]["text"] == "Статус"


def test_bot_start_shows_main_reply_keyboard() -> None:
    result = dispatch_message("/start")

    assert result.ok is True
    assert result.command == "/menu"
    assert "Главное меню" in result.text
    assert result.reply_markup["keyboard"] == [
        [{"text": "Статус"}, {"text": "Помощь"}],
        [{"text": "Общий вчерашний отчет"}],
        [{"text": "Озон"}, {"text": "Вайлдберриз"}],
    ]
    assert result.reply_markup["resize_keyboard"] is True


def test_bot_main_keyboard_buttons_dispatch_existing_commands(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from seller_agent.bot import commands

    def fake_latest_run_command(**kwargs: object) -> commands.TelegramCommandResult:
        return commands.TelegramCommandResult(
            command=str(kwargs["command"]),
            ok=True,
            text=f"handled {kwargs['command']}",
        )

    monkeypatch.setattr(commands, "_latest_run_command", fake_latest_run_command)

    status = dispatch_message("Статус", data_dir=tmp_path)
    today = dispatch_message("Общий вчерашний отчет", data_dir=tmp_path)

    assert status.command == "/status"
    assert "handled /status" in status.text
    assert today.command == "/today"
    assert "handled /today" in today.text


def test_bot_marketplace_buttons_show_submenus() -> None:
    ozon = dispatch_message("Озон")
    wb = dispatch_message("Вайлдберриз")

    assert ozon.ok is True
    assert ozon.command == "/ozon"
    assert ozon.reply_markup["keyboard"][0][0]["text"] == "Ozon акции"
    assert wb.ok is True
    assert wb.command == "/wb"
    assert wb.reply_markup["keyboard"][0][0]["text"] == "WB акции"
    assert wb.reply_markup["keyboard"][1][0]["text"] == "Акции от минимальной цены"
    assert ozon.reply_markup["keyboard"][1][0]["text"] == "Отчёт за период Ozon"
    assert ozon.reply_markup["keyboard"][2][0]["text"] == "Остатки и поставки Ozon"
    assert ozon.reply_markup["keyboard"][3][0]["text"] == "В работу Ozon"
    assert wb.reply_markup["keyboard"][3][0]["text"] == "Остатки и поставки"
    assert wb.reply_markup["keyboard"][4][0]["text"] == "В работу"
    assert wb.reply_markup["keyboard"][5][0]["text"] == "Цены и маржа WB"
    assert wb.reply_markup["keyboard"][6][0]["text"] == "Отчёт за период WB"


def test_bot_wb_work_plan_collects_value_and_confirms_parameters() -> None:
    start = dispatch_message("В работу")
    assert start.ok is True
    assert start.command == "/wb-work-plan"
    assert start.reply_markup["inline_keyboard"][0][0]["callback_data"] == "wbwp_mode:c"

    capacity = dispatch_callback("wbwp_mode:c")
    assert capacity.conversation_state["stage"] == "wb_work_capacity_input"
    clusters = dispatch_message("1000", conversation_state=capacity.conversation_state)
    assert clusters.conversation_state == {
        "stage": "wb_work_clusters_input",
        "mode_code": "c",
        "value": 1000,
    }
    confirmation = dispatch_message("4", conversation_state=clusters.conversation_state)
    assert "1000 физических изделий" in confirmation.text
    assert "кластеров назначения: `4`" in confirmation.text
    assert confirmation.reply_markup["inline_keyboard"][0][0]["callback_data"] == "wbwp_run:c:1000:4"

    days = dispatch_callback("wbwp_mode:d")
    assert days.conversation_state["stage"] == "wb_work_days_input"
    days_clusters = dispatch_message("30", conversation_state=days.conversation_state)
    days_confirmation = dispatch_message("6", conversation_state=days_clusters.conversation_state)
    assert "30 дней" in days_confirmation.text
    assert days_confirmation.reply_markup["inline_keyboard"][0][0]["callback_data"] == "wbwp_run:d:30:6"


def test_bot_ozon_work_plan_collects_value_clusters_and_confirms_parameters() -> None:
    start = dispatch_message("В работу Ozon")
    assert start.ok is True
    assert start.command == "/ozon-work-plan"
    assert start.reply_markup["inline_keyboard"][0][0]["callback_data"] == "ozwp_mode:c"

    capacity = dispatch_callback("ozwp_mode:c")
    assert capacity.conversation_state["stage"] == "ozon_work_capacity_input"
    clusters = dispatch_message("1400", conversation_state=capacity.conversation_state)
    assert clusters.conversation_state == {
        "stage": "ozon_work_clusters_input",
        "mode_code": "c",
        "value": 1400,
    }
    confirmation = dispatch_message("5", conversation_state=clusters.conversation_state)
    assert "1400 физических изделий" in confirmation.text
    assert "кластеров назначения: `5`" in confirmation.text
    assert confirmation.reply_markup["inline_keyboard"][0][0]["callback_data"] == "ozwp_run:c:1400:5"


def test_bot_ozon_work_plan_confirmation_runs_cluster_local_read_only_workflow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from seller_agent.bot import commands
    from seller_agent.core.workflow_runner import WorkflowRunResult

    calls: list[tuple[str, dict]] = []

    class FakeWorkflowRunner:
        def __init__(self, **kwargs: object) -> None:
            pass

        def run_read_only(self, task: str, *, inputs: dict) -> WorkflowRunResult:
            calls.append((task, inputs))
            return WorkflowRunResult(
                task=task,
                command=task,
                title="Ozon production plan",
                ok=True,
                status="warning",
                mode="read_only",
                risk="low",
                summary={
                    "run_id": "ozon_production_work_plan_test",
                    "metrics": {
                        "marketplace_units": 90,
                        "physical_pieces": 100,
                        "articles": 10,
                        "selected_clusters": 3,
                        "control_rows": 1,
                        "unused_capacity_physical": 0,
                    },
                    "cluster_totals": [
                        {"priority": 1, "cluster": "Ростов", "marketplace_units": 40, "physical_pieces": 50}
                    ],
                    "warnings": ["Одна строка требует проверки."],
                },
                artifacts={"report": str(tmp_path / "plan.xlsx")},
            )

    monkeypatch.setattr(commands, "WorkflowRunner", FakeWorkflowRunner)
    result = dispatch_callback("ozwp_run:c:100:3", data_dir=tmp_path)

    assert result.ok is True
    assert "физических изделий: `100`" in result.text
    assert "Утвердить в работу" == result.reply_markup["inline_keyboard"][0][0]["text"]
    assert calls == [
        ("ozon-production-work-plan", {"mode": "capacity", "value": 100, "cluster_count": 3})
    ]


def test_bot_wb_work_plan_confirmation_runs_read_only_workflow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from seller_agent.bot import commands
    from seller_agent.core.workflow_runner import WorkflowRunResult

    calls: list[tuple[str, dict]] = []

    class FakeWorkflowRunner:
        def __init__(self, **kwargs: object) -> None:
            pass

        def run_read_only(self, task: str, *, inputs: dict) -> WorkflowRunResult:
            calls.append((task, inputs))
            return WorkflowRunResult(
                task=task,
                command=task,
                title="WB production plan",
                ok=True,
                status="warning",
                mode="read_only",
                risk="low",
                summary={
                    "run_id": "wb_production_work_plan_test",
                    "metrics": {
                        "marketplace_units": 80,
                        "physical_pieces": 100,
                        "articles": 10,
                        "regions": 3,
                        "selected_clusters": 3,
                        "control_rows": 1,
                        "unused_capacity_physical": 0,
                    },
                    "region_totals": [
                        {"priority": 1, "region": "Центральный", "marketplace_units": 40, "physical_pieces": 50}
                    ],
                    "warnings": ["Одна строка требует проверки."],
                },
                artifacts={"report": str(tmp_path / "plan.xlsx")},
            )

    monkeypatch.setattr(commands, "WorkflowRunner", FakeWorkflowRunner)
    result = dispatch_callback("wbwp_run:c:100:3", data_dir=tmp_path)

    assert result.ok is True
    assert "физических изделий: `100`" in result.text
    assert "Утвердить в работу" == result.reply_markup["inline_keyboard"][0][0]["text"]
    assert calls == [
        ("wb-production-work-plan", {"mode": "capacity", "value": 100, "cluster_count": 3})
    ]


def test_bot_wb_stock_supplies_builds_fresh_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from seller_agent.bot import commands
    from seller_agent.core.workflow_runner import WorkflowRunResult

    calls: list[dict] = []
    summary = {
        "run_id": "wb_stock_supply_monitor_test",
        "overall_status": "warning",
        "metrics": {
            "stocks": {
                "quantity": 500,
                "physical_quantity": 560,
                "in_way_to_client": 3,
                "in_way_from_client": 401,
            },
            "supplies": {
                "count": 4,
                "quantity": 884,
                "physical_quantity": 950,
                "by_status": {
                    "1": {"status": "Не запланировано", "count": 0, "quantity": 0},
                    "2": {"status": "Запланировано", "count": 0, "quantity": 0},
                    "3": {"status": "Отгрузка разрешена", "count": 3, "quantity": 566},
                    "4": {"status": "Идет приемка", "count": 1, "quantity": 318},
                    "6": {"status": "Выгружено на воротах", "count": 0, "quantity": 0},
                },
            },
            "warehouse_count": 5,
        },
        "active_supplies": [
            {
                "supply_id": "40816383",
                "warehouse_name": "Склад Шушары",
                "status": "Отгрузка разрешена",
                "quantity": 136,
                "supply_date": "2026-07-20",
            }
        ],
        "anomalies": [
            {
                "message": "Электросталь: поле inWayFromClient требует сверки и не является подтвержденным возвратом."
            }
        ],
        "artifacts": {"report": str(tmp_path / "report.md")},
    }

    class FakeWorkflowRunner:
        def __init__(self, **kwargs: object) -> None:
            calls.append({"init": kwargs})

        def run_read_only(self, task_name: str, *, inputs: dict | None = None) -> WorkflowRunResult:
            calls.append({"task_name": task_name, "inputs": inputs})
            return WorkflowRunResult(
                task="wb-stock-supply-monitor",
                command="wb-stock-supply-monitor",
                title="WB stock and supply monitor",
                ok=True,
                status="warning",
                mode="read_only",
                risk="low",
                summary=summary,
                artifacts=summary["artifacts"],
            )

    monkeypatch.setattr(commands, "WorkflowRunner", FakeWorkflowRunner)
    result = dispatch_message("Остатки и поставки", data_dir=tmp_path)

    assert result.ok is True
    assert result.command == "/wb-stock-supplies"
    assert "Отгрузка разрешена: поставок `3` / `566` ед." in result.text
    assert "Идет приемка: поставок `1` / `318` ед." in result.text
    assert "активные поставки не прибавляются" in result.text
    assert calls[1] == {"task_name": "wb-stock-supply-monitor", "inputs": None}


def test_bot_ozon_stock_supplies_builds_fresh_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from seller_agent.bot import commands
    from seller_agent.core.workflow_runner import WorkflowRunResult

    calls: list[dict] = []
    summary = {
        "run_id": "ozon_stock_supply_monitor_test",
        "overall_status": "warning",
        "metrics": {
            "general_fbo": {"present": 500, "physical_present": 560, "reserved": 10},
            "warehouses": {"free_to_sell": 490, "physical_free_to_sell": 550, "promised": 20},
            "supplies": {
                "orders": 2,
                "supplies": 3,
                "quantity": 100,
                "physical_quantity": 130,
                "confirmed_inbound_quantity": 80,
                "confirmed_inbound_physical": 110,
                "virtual_orders": 1,
            },
            "warehouse_count": 5,
            "reconciliation": {"difference": 0},
        },
        "active_supplies": [
            {
                "order_number": "10001",
                "state_label": "В пути",
                "storage_warehouse": "ТВЕРЬ_РФЦ",
                "quantity": 80,
            }
        ],
        "warnings": ["Источники расходятся на 10 ед."],
        "artifacts": {"report": str(tmp_path / "ozon_report.md")},
    }

    class FakeWorkflowRunner:
        def __init__(self, **kwargs: object) -> None:
            calls.append({"init": kwargs})

        def run_read_only(self, task_name: str, *, inputs: dict | None = None) -> WorkflowRunResult:
            calls.append({"task_name": task_name, "inputs": inputs})
            return WorkflowRunResult(
                task="ozon-stock-supply-monitor",
                command="ozon-stock-supply-monitor",
                title="Ozon stock and supply monitor",
                ok=True,
                status="warning",
                mode="read_only",
                risk="low",
                summary=summary,
                artifacts=summary["artifacts"],
            )

    monkeypatch.setattr(commands, "WorkflowRunner", FakeWorkflowRunner)
    result = dispatch_message("Остатки и поставки Ozon", data_dir=tmp_path)

    assert result.ok is True
    assert result.command == "/ozon-stock-supplies"
    assert "общий present: `500`" in result.text
    assert "подтвержденный inbound: `80`" in result.text
    assert "общий и складской остатки не складываются" in result.text
    assert calls[1] == {"task_name": "ozon-stock-supply-monitor", "inputs": None}


def test_period_report_button_flow_and_custom_dates() -> None:
    start = dispatch_message("Отчёт за период Ozon")
    assert start.ok is True
    assert start.reply_markup["inline_keyboard"][0][0]["callback_data"] == "mpr_type:o:s"

    report_type = dispatch_callback("mpr_type:o:f")
    assert "Выберите период" in report_type.text
    custom = dispatch_callback("mpr_period:o:f:c")
    assert custom.conversation_state["stage"] == "period_report_custom_from"

    date_from = dispatch_message("01.07.2026", conversation_state=custom.conversation_state)
    assert date_from.conversation_state["stage"] == "period_report_custom_to"
    confirmation = dispatch_message("16.07.2026", conversation_state=date_from.conversation_state)
    callback = confirmation.reply_markup["inline_keyboard"][0][0]["callback_data"]
    assert callback == "mpr_run:o:f:2026-07-01:2026-07-16"
    assert "Финансовый" in confirmation.text


def test_period_report_confirmation_runs_read_only_workflow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from seller_agent.bot import commands
    from seller_agent.core.workflow_runner import WorkflowRunResult

    calls: list[tuple[str, dict]] = []

    class FakeWorkflowRunner:
        def __init__(self, **kwargs: object) -> None:
            pass

        def run_read_only(self, task: str, *, inputs: dict) -> WorkflowRunResult:
            calls.append((task, inputs))
            return WorkflowRunResult(
                task=task,
                command=task,
                title="Period report",
                ok=True,
                status="ok",
                mode="read_only",
                risk="low",
                summary={
                    "run_id": "marketplace_period_report_wb_test",
                    "metrics": {
                        "orders": 10,
                        "order_amount": 4000,
                        "buyout_units": 8,
                        "physical_pieces": 12,
                        "returns": 1,
                        "cancellations": 2,
                        "gross": 3500,
                        "expenses": 1000,
                        "net": 2500,
                        "net_per_piece": 208.33,
                    },
                    "warnings": [],
                },
                artifacts={"report": str(tmp_path / "report.md")},
            )

    monkeypatch.setattr(commands, "WorkflowRunner", FakeWorkflowRunner)
    result = dispatch_callback("mpr_run:w:a:2026-07-01:2026-07-16", data_dir=tmp_path)

    assert result.ok is True
    assert "12` физических изделий" in result.text
    assert "2 500" in result.text
    assert result.artifacts["report"].endswith("report.md")
    assert calls == [
        (
            "marketplace-period-report",
            {
                "marketplace": "wb",
                "report_type": "full",
                "date_from": "2026-07-01",
                "date_to": "2026-07-16",
            },
        )
    ]


def test_bot_wb_analytics_builds_fresh_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from seller_agent.bot import commands
    from seller_agent.core.workflow_runner import WorkflowRunResult

    calls: list[dict] = []
    summary = {
        "run_id": "wb_parser_warehouse_analytics_test",
        "overall_status": "ok",
        "source": {
            "min_run_date": "2026-06-12",
            "max_run_date": "2026-07-04",
            "built_at_utc": "2026-07-05T06:07:13+00:00",
        },
        "metrics": {
            "visible_rows": 100,
            "unique_products": 50,
            "unique_queries": 20,
            "best_position": 12,
            "top10_rows": 2,
            "top30_rows": 8,
            "top100_rows": 30,
            "stock_visible_rows": 90,
            "zero_stock_visible_rows": 10,
            "daily_change_rows": 40,
            "improved_rows": 5,
            "declined_rows": 7,
            "missing_rows": 3,
            "weak_visible_candidates": 11,
        },
        "run_quality": {"latest_status": "success"},
        "artifacts": {
            "report": str(tmp_path / "report.md"),
            "weak_candidates_csv": str(tmp_path / "weak.csv"),
            "summary": str(tmp_path / "summary.json"),
        },
    }

    class FakeWorkflowRunner:
        def __init__(self, **kwargs: object) -> None:
            calls.append({"init": kwargs})

        def run_read_only(self, task_name: str, *, inputs: dict | None = None) -> WorkflowRunResult:
            calls.append({"task_name": task_name, "inputs": inputs})
            return WorkflowRunResult(
                task="wb-parser-warehouse-analytics",
                command="wb-parser-warehouse-analytics",
                title="WB parser warehouse analytics",
                ok=True,
                status="ok",
                mode="read_only",
                risk="low",
                summary=summary,
                artifacts=summary["artifacts"],
            )

    monkeypatch.setattr(commands, "WorkflowRunner", FakeWorkflowRunner)

    result = dispatch_message("WB аналитика", data_dir=tmp_path)

    assert result.ok is True
    assert result.command == "/wb-analytics"
    assert "свежая read-only аналитика построена" in result.text
    assert "товаров в выдаче: `50`" in result.text
    assert "кандидатов с остатком вне top-30: `11`" in result.text
    assert result.artifacts["report"].endswith("report.md")
    assert calls[1]["task_name"] == "wb-parser-warehouse-analytics"


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
                "orders": {
                    "yesterday": {
                        "total_orders": 13,
                        "amount": 2600,
                        "active_orders": 12,
                        "active_amount": 2400,
                        "cancelled_orders": 1,
                    }
                },
                "sales": {"yesterday": {"sales_rows": 9, "sales_amount": 1800}},
                "finance_expenses": {
                    "total_expenses": 600,
                    "total_credits_and_adjustments": 100,
                },
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
            "both_marketplaces_products": 269,
            "active_ozon_only_products": 279,
            "active_wb_only_products": 162,
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
    assert "WB: создано заказов `13`" in live_result.text
    assert "активных `12`" in live_result.text
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
            "job_id": "job_ozon_elastic_apply_test",
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

    monkeypatch.setattr(commands, "_run_plan_apply_job", fake_apply)

    result = dispatch_callback("oe_apply:ozon_elastic_plan_test", data_dir=tmp_path)

    assert result.ok is True
    assert result.mode == "apply"
    assert "apply завершен" in result.text
    assert "Job ID: `job_ozon_elastic_apply_test`" in result.text
    assert "добавить/обновить: `4`" in result.text
    assert calls[0]["task_id"] == "ozon-elastic-apply"
    assert calls[0]["data_dir"] == tmp_path
    assert calls[0]["plan_run_id"] == "ozon_elastic_plan_test"


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
        fieldnames = [
            "Акций",
            "Статусы в файлах акций",
            "Текущая скидка",
            "Скидка до порога",
            "Финальная скидка",
            "Скидка к загрузке",
            "Осталось до целевой, п.п.",
            "Действие",
            "Причина",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter=";")
        writer.writeheader()
        writer.writerow(
            {
                "Акций": "2",
                "Статусы в файлах акций": "Да, Нет",
                "Текущая скидка": "60",
                "Скидка до порога": "60",
                "Финальная скидка": "60",
                "Скидка к загрузке": "60",
                "Осталось до целевой, п.п.": "0",
                "Действие": "не менять",
                "Причина": "скидка до порога <= 70%",
            }
        )
        writer.writerow(
            {
                "Акций": "1",
                "Статусы в файлах акций": "Да",
                "Текущая скидка": "70",
                "Скидка до порога": "70",
                "Финальная скидка": "55",
                "Скидка к загрузке": "55",
                "Осталось до целевой, п.п.": "0",
                "Действие": "снизить скидку",
                "Причина": "скидка до порога > 70% -> 55%",
            }
        )
        writer.writerow(
            {
                "Акций": "0",
                "Статусы в файлах акций": "",
                "Текущая скидка": "70",
                "Скидка до порога": "",
                "Финальная скидка": "55",
                "Скидка к загрузке": "55",
                "Осталось до целевой, п.п.": "0",
                "Действие": "снизить скидку",
                "Причина": "товара нет в активных акциях -> 55%",
            }
        )
        writer.writerow(
            {
                "Акций": "1",
                "Статусы в файлах акций": "Нет",
                "Текущая скидка": "70",
                "Скидка до порога": "70",
                "Финальная скидка": "55",
                "Скидка к загрузке": "55",
                "Осталось до целевой, п.п.": "0",
                "Действие": "снизить скидку",
                "Причина": "скидка до порога > 70% -> 55%",
            }
        )

    def fake_plan(**kwargs: object) -> dict:
        assert kwargs["data_dir"] == tmp_path
        assert kwargs["scheme_text"] == "70-55-55"
        return {
            "run_id": "wb_actions_discount_plan_70-55-55_test",
            "summary": {
                "scheme": "70-55-55",
                "total_goods": 4,
                "in_promos": 3,
                "outside_promos": 1,
                "multiple_promos": 1,
                "changed_rows": 3,
                "to_change": 3,
                "increase": 0,
                "decrease": 3,
                "no_change": 1,
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
    assert "всего товаров в магазине: `4`" in result.text
    assert "подходят под доступные активные акции: `3`" in result.text
    assert "участвуют в акциях: `2`" in result.text
    assert "не участвуют в акциях: `2`" in result.text
    assert "проходят заданный порог 70%: `1`" in result.text
    assert "исключатся из текущих акций: `1`" in result.text
    assert "не будут участвовать в акциях: `3`" in result.text
    assert "скидка 60%: `1` товаров" in result.text
    assert "скидка 55%: `3` товаров" in result.text
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


def test_bot_wb_actions_omits_exclusion_reason_when_no_products_are_excluded() -> None:
    from seller_agent.bot import commands

    assert commands._wb_exclusion_reason_lines({}, threshold="60") == []


def test_bot_wb_manual_actions_collects_and_confirms_named_parameters() -> None:
    start = dispatch_message("Ручная акция")

    assert start.ok is True
    assert start.command == "/wb-actions-manual"
    assert start.conversation_state == {"stage": "wb_manual_scheme_input"}
    assert "`порог  скидка_после_порога  скидка_вне_акций`" in start.text

    invalid = dispatch_message("57 55", conversation_state=start.conversation_state)
    assert invalid.ok is False
    assert invalid.blocked_reason == "invalid_wb_manual_parameters"
    assert invalid.conversation_state == start.conversation_state

    review = dispatch_message("57 52 48", conversation_state=start.conversation_state)
    assert review.ok is True
    assert review.mode == "review"
    assert "порог акции: `57%`" in review.text
    assert "после превышения порога: `52%`" in review.text
    assert "вне активных акций: `48%`" in review.text
    assert review.conversation_state == {}
    assert (
        review.reply_markup["inline_keyboard"][0][0]["callback_data"]
        == "wbam_confirm:57-48-52"
    )
    assert review.reply_markup["inline_keyboard"][1][0]["callback_data"] == "wbam_cancel"


def test_bot_wb_manual_actions_confirm_builds_exact_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from seller_agent.bot import commands

    calls: list[dict] = []

    def fake_plan(**kwargs: object) -> dict:
        calls.append(dict(kwargs))
        return {
            "run_id": "wb_actions_discount_plan_57-48-52_test",
            "summary": {
                "scheme": "57-48-52",
                "total_goods": 10,
                "in_promos": 8,
                "outside_promos": 2,
                "multiple_promos": 4,
                "changed_rows": 3,
                "to_change": 3,
                "increase": 1,
                "decrease": 2,
                "no_change": 7,
                "active_promos": 3,
                "future_promos": 1,
            },
            "artifacts": {},
        }

    monkeypatch.setattr(commands, "run_wb_actions_discount_plan", fake_plan)

    result = dispatch_callback("wbam_confirm:57-48-52", data_dir=tmp_path)

    assert result.ok is True
    assert result.mode == "dry_run"
    assert calls[0]["scheme_text"] == "57-48-52"
    assert "порог акции: `57%`" in result.text
    assert "после превышения порога: `52%`" in result.text
    assert "вне активных акций: `48%`" in result.text
    assert (
        result.reply_markup["inline_keyboard"][0][0]["callback_data"]
        == "wba_apply:wb_actions_discount_plan_57-48-52_test"
    )
    assert (
        result.reply_markup["inline_keyboard"][1][0]["callback_data"]
        == "wbam_reject:wb_actions_discount_plan_57-48-52_test"
    )


def test_bot_wb_manual_actions_cancel_and_reject_are_noop() -> None:
    cancelled = dispatch_callback("wbam_cancel")
    rejected = dispatch_callback("wbam_reject:wb_actions_discount_plan_57-48-52_test")

    assert cancelled.ok is True
    assert cancelled.mode == "cancelled"
    assert "Изменений в WB не выполнял" in cancelled.text
    assert rejected.ok is True
    assert rejected.mode == "cancelled"
    assert "Скидки в WB не изменены" in rejected.text


def test_bot_wb_min_price_actions_uses_default_or_manual_outside_discount() -> None:
    start = dispatch_message("Акции от минимальной цены")

    assert start.ok is True
    assert start.command == "/wb-actions-min-price"
    assert start.conversation_state == {"stage": "wb_min_price_discount_input"}
    assert start.reply_markup["inline_keyboard"][0][0]["callback_data"] == "wbmp_review:50"

    default_review = dispatch_callback("wbmp_review:50")
    assert default_review.ok is True
    assert "вне подходящих акций: `50%`" in default_review.text
    assert default_review.reply_markup["inline_keyboard"][0][0]["callback_data"] == (
        "wbmp_confirm:50"
    )

    manual_review = dispatch_message("47", conversation_state=start.conversation_state)
    assert manual_review.ok is True
    assert "вне подходящих акций: `47%`" in manual_review.text
    assert manual_review.reply_markup["inline_keyboard"][0][0]["callback_data"] == (
        "wbmp_confirm:47"
    )

    invalid = dispatch_message("101", conversation_state=start.conversation_state)
    assert invalid.ok is False
    assert invalid.conversation_state == start.conversation_state


def test_bot_wb_actions_callback_applies_specific_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from seller_agent.bot import commands

    calls: list[dict] = []

    def fake_apply(**kwargs: object) -> dict:
        calls.append(kwargs)
        return {
            "job_id": "job_wb_actions_apply_test",
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

    monkeypatch.setattr(commands, "_run_plan_apply_job", fake_apply)

    result = dispatch_callback("wba_apply:wb_actions_discount_plan_70-55-55_test", data_dir=tmp_path)

    assert result.ok is True
    assert result.mode == "apply"
    assert "apply завершен" in result.text
    assert "Job ID: `job_wb_actions_apply_test`" in result.text
    assert "отправлено строк: `4`" in result.text
    assert "successful goods: `4` / `4`" in result.text
    assert calls[0]["task_id"] == "wb-actions-discount-apply"
    assert calls[0]["data_dir"] == tmp_path
    assert calls[0]["plan_run_id"] == "wb_actions_discount_plan_70-55-55_test"


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
    actions_path = tmp_path / "runs" / "2026-07-04" / "ozon_inbox_test" / "actions.json"
    actions_path.parent.mkdir(parents=True, exist_ok=True)
    actions_path.write_text(
        json.dumps(
            {
                "actions": [
                    {"source_type": "question", "action_type": "question_answer"},
                    {"source_type": "question", "action_type": "manual_question_review"},
                    {"source_type": "question", "action_type": "manual_question_review"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def fake_triage(**kwargs: object) -> dict:
        assert kwargs["data_dir"] == tmp_path
        return {
            "run_id": "ozon_inbox_test",
            "overall_status": "ok",
            "actions_count": 3,
            "reviews": {"actions_count": 1, "artifacts": {"actions": str(actions_path)}},
            "messenger": {"total_unread_count": 2},
            "artifacts": {"report": str(report)},
        }

    monkeypatch.setattr(commands, "run_ozon_inbox_triage", fake_triage)

    result = dispatch_message("/ozon-inbox", data_dir=tmp_path)

    assert result.ok is True
    assert result.mode == "dry_run"
    assert "Ozon входящие" in result.text
    assert "вопросы покупателей: `3`" in result.text
    assert "автоответы на вопросы: `1`" in result.text
    assert "вопросы на ручную проверку: `2`" in result.text
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


def test_poll_once_keeps_manual_wb_conversation_per_chat_and_thread(tmp_path: Path) -> None:
    calls: list[tuple[str, str, dict]] = []

    def fake_api(token: str, method: str, payload: dict) -> dict:
        calls.append((token, method, payload))
        if method == "getUpdates":
            return {
                "ok": True,
                "result": [
                    {
                        "update_id": 201,
                        "message": {
                            "chat": {"id": 123},
                            "text": "Ручная акция",
                            "message_thread_id": 55,
                        },
                    },
                    {
                        "update_id": 202,
                        "message": {
                            "chat": {"id": 123},
                            "text": "57 52 48",
                            "message_thread_id": 55,
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
    assert result["processed_updates"] == 2
    send_calls = [call for call in calls if call[1] == "sendMessage"]
    assert "Введите три целых значения" in send_calls[0][2]["text"]
    assert "порог акции: `57%`" in send_calls[1][2]["text"]
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["offset"] == 203
    assert "conversations" not in state


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


@pytest.mark.parametrize(
    ("message", "task_id"),
    [
        ("Ozon эластик", "ozon-elastic-plan"),
        ("Ozon акции", "ozon-actions-optimizer-plan"),
        ("Остатки и поставки Ozon", "ozon-stock-supply-monitor"),
        ("WB акции", "wb-actions-discount-plan"),
        ("WB аналитика", "wb-parser-warehouse-analytics"),
        ("Остатки и поставки", "wb-stock-supply-monitor"),
        ("Ozon входящие", "ozon-inbox"),
        ("WB входящие", "wb-inbox"),
    ],
)
def test_runtime_job_dispatch_queues_all_external_message_operations(
    message: str,
    task_id: str,
    tmp_path: Path,
) -> None:
    runtime_db = tmp_path / f"{task_id}.db"

    result = dispatch_runtime_job_message(
        message,
        update_id=3001,
        chat_id=123,
        data_dir=tmp_path / "data",
        runtime_db=runtime_db,
        live_today=True,
        live_status=True,
    )

    assert result is not None
    assert result.ok is True
    jobs = JobStore(runtime_db).list_jobs()
    assert len(jobs) == 1
    assert jobs[0].task_id == task_id
    assert jobs[0].status == "queued"


@pytest.mark.parametrize(
    ("callback_data", "task_id", "expected_params"),
    [
        (
            "mpr_run:o:f:2026-07-01:2026-07-16",
            "marketplace-period-report",
            {
                "marketplace": "ozon",
                "report_type": "financial",
                "date_from": "2026-07-01",
                "date_to": "2026-07-16",
            },
        ),
        (
            "ozwp_run:c:1000:4",
            "ozon-production-work-plan",
            {"mode": "capacity", "value": 1000, "cluster_count": 4},
        ),
        (
            "wbwp_run:d:30:6",
            "wb-production-work-plan",
            {"mode": "coverage_days", "value": 30, "cluster_count": 6},
        ),
        ("wbam_confirm:60-50-50", "wb-actions-discount-plan", {"scheme_text": "60-50-50"}),
        (
            "wbmp_confirm:47",
            "wb-best-price-action-plan",
            {"outside_discount": 47},
        ),
        (
            "oe_apply:ozon_elastic_plan_test",
            "ozon-elastic-apply",
            {"plan_run_id": "ozon_elastic_plan_test", "confirmed_by_user": True},
        ),
        (
            "oza_apply:ozon_actions_optimizer_plan_test",
            "ozon-actions-optimizer-apply",
            {"plan_run_id": "ozon_actions_optimizer_plan_test", "confirmed_by_user": True},
        ),
        (
            "wba_apply:wb_actions_discount_plan_test",
            "wb-actions-discount-apply",
            {"plan_run_id": "wb_actions_discount_plan_test", "confirmed_by_user": True},
        ),
        (
            "wbmp_apply:wb_best_price_actions_plan_47_20260729T120000",
            "wb-best-price-action-apply",
            {
                "plan_run_id": "wb_best_price_actions_plan_47_20260729T120000",
                "confirmed_by_user": True,
            },
        ),
        (
            "ozin_apply:ozon_inbox_test",
            "ozon-inbox-apply",
            {"source_run_id": "ozon_inbox_test", "confirmed_by_user": True},
        ),
        (
            "wbin_apply:wb_inbox_test",
            "wb-inbox-apply",
            {"source_run_id": "wb_inbox_test", "confirmed_by_user": True},
        ),
    ],
)
def test_runtime_job_dispatch_queues_all_operation_callbacks(
    callback_data: str,
    task_id: str,
    expected_params: dict,
    tmp_path: Path,
) -> None:
    runtime_db = tmp_path / f"{task_id}.db"

    result = dispatch_runtime_job_callback(
        callback_data,
        update_id=4001,
        chat_id=123,
        thread_id=55,
        data_dir=tmp_path / "data",
        runtime_db=runtime_db,
    )

    assert result is not None
    assert result.ok is True
    job = JobStore(runtime_db).list_jobs()[0]
    assert job.task_id == task_id
    for key, value in expected_params.items():
        assert job.params[key] == value
    update = JobStore(runtime_db).get_telegram_update(4001)
    assert update is not None
    assert update.payload["callback_data"] == callback_data
    assert update.payload["thread_id"] == 55


def test_ozon_pricing_margin_dialog_and_runtime_queue(tmp_path: Path) -> None:
    menu = dispatch_message("Озон")
    assert any(
        button.get("text") == "Цены и маржа Ozon"
        for row in menu.reply_markup["keyboard"]
        for button in row
    )

    start = dispatch_message("Цены и маржа Ozon")
    assert start.ok is True
    period = dispatch_callback("opm_period:30", data_dir=tmp_path)
    assert period.conversation_state == {"stage": "ozon_pricing_cost_input", "period_days": 30}
    cost = dispatch_message("85", conversation_state=period.conversation_state)
    assert cost.ok is True
    assert cost.conversation_state["stage"] == "ozon_pricing_margin_input"

    runtime_db = tmp_path / "runtime.db"
    queued = dispatch_runtime_job_message(
        "60",
        update_id=2001,
        chat_id=123,
        data_dir=tmp_path / "data",
        runtime_db=runtime_db,
        conversation_state=cost.conversation_state,
    )
    assert queued is not None
    assert queued.ok is True
    job = JobStore(runtime_db).list_jobs()[0]
    assert job.task_id == "ozon-pricing-margin"
    assert job.params == {"unit_cost": "85", "target_margin": "60", "period_days": 30}


def test_ozon_pricing_margin_rejects_invalid_owner_numbers(tmp_path: Path) -> None:
    period = dispatch_callback("opm_period:15", data_dir=tmp_path)
    invalid_cost = dispatch_message("ноль", conversation_state=period.conversation_state)
    assert invalid_cost.blocked_reason == "invalid_ozon_unit_cost"
    cost = dispatch_message("85,50", conversation_state=period.conversation_state)
    invalid_margin = dispatch_runtime_job_message(
        "-1",
        update_id=2002,
        chat_id=123,
        data_dir=tmp_path / "data",
        runtime_db=tmp_path / "runtime.db",
        conversation_state=cost.conversation_state,
    )
    assert invalid_margin is not None
    assert invalid_margin.blocked_reason == "invalid_ozon_target_margin"
    assert JobStore(tmp_path / "runtime.db").list_jobs() == []

    navigation = dispatch_runtime_job_message(
        "Назад",
        update_id=2003,
        chat_id=123,
        data_dir=tmp_path / "data",
        runtime_db=tmp_path / "runtime.db",
        conversation_state=cost.conversation_state,
    )
    assert navigation is None


def test_wb_pricing_margin_dialog_and_runtime_queue(tmp_path: Path) -> None:
    menu = dispatch_message("Вайлдберриз")
    assert any(
        button.get("text") == "Цены и маржа WB"
        for row in menu.reply_markup["keyboard"]
        for button in row
    )

    start = dispatch_message("Цены и маржа WB")
    assert start.ok is True
    period = dispatch_callback("wpm_period:15", data_dir=tmp_path)
    assert period.conversation_state == {"stage": "wb_pricing_cost_input", "period_days": 15}
    cost = dispatch_message("85", conversation_state=period.conversation_state)
    assert cost.conversation_state["stage"] == "wb_pricing_margin_input"

    runtime_db = tmp_path / "runtime.db"
    queued = dispatch_runtime_job_message(
        "50",
        update_id=2004,
        chat_id=123,
        data_dir=tmp_path / "data",
        runtime_db=runtime_db,
        conversation_state=cost.conversation_state,
    )
    assert queued is not None and queued.ok is True
    job = JobStore(runtime_db).list_jobs()[0]
    assert job.task_id == "wb-pricing-margin"
    assert job.params == {"unit_cost": "85", "target_margin": "50", "period_days": 15}


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
    update = store.get_telegram_update(501)
    assert update is not None
    assert update.processing_status == "completed"


def test_notify_telegram_job_result_marks_notification_failure(tmp_path: Path) -> None:
    runtime_db = tmp_path / "runtime.db"
    store = JobStore(runtime_db)
    job = store.create_job(
        task_id="status-preflight",
        actor="telegram:123",
        job_id="job_notify_failure_test",
        status="queued",
    )
    store.register_telegram_update(
        update_id=502,
        chat_id="123",
        command="/status",
        job_id=job.job_id,
        processing_status="queued",
    )
    store.update_job_status(job.job_id, "success", result={"status": "ok", "artifacts": {}})

    def failed_api(token: str, method: str, payload: dict) -> dict:
        raise TelegramRunnerError("send failed")

    result = notify_telegram_job_result(
        token="secret-token",
        job_id=job.job_id,
        store=store,
        data_dir=tmp_path / "data",
        api_request=failed_api,
    )

    assert result.ok is False
    update = store.get_telegram_update(502)
    assert update is not None
    assert update.processing_status == "notification_failed"


def test_job_result_text_redacts_sensitive_worker_error(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "runtime.db")
    job = store.create_job(
        task_id="status-preflight",
        actor="telegram:123",
        job_id="job_sensitive_error_test",
        status="queued",
    )
    store.update_job_status(
        job.job_id,
        "failed",
        error="Authorization: Bearer secret-token-value",
    )
    failed_job = store.get_job(job.job_id)
    assert failed_job is not None

    text = build_job_result_text(failed_job)

    assert "secret-token-value" not in text
    assert "подробности скрыты safety-фильтром" in text


def test_notify_telegram_plan_result_keeps_apply_button(tmp_path: Path) -> None:
    runtime_db = tmp_path / "runtime.db"
    store = JobStore(runtime_db)
    job = store.create_job(
        task_id="ozon-elastic-plan",
        actor="telegram:123",
        job_id="job_elastic_plan_notify",
        status="queued",
    )
    store.register_telegram_update(
        update_id=503,
        chat_id="123",
        command="/elastic",
        job_id=job.job_id,
        payload={"thread_id": 55, "kind": "message", "message": "Ozon эластик"},
        processing_status="queued",
    )
    store.update_job_status(
        job.job_id,
        "success",
        result={
            "status": "ok",
            "summary": {
                "run_id": "ozon_elastic_plan_notify_test",
                "overall_status": "ok",
                "summary": {
                    "active_rows": 10,
                    "candidate_rows": 5,
                    "add_to_action": 2,
                    "update_action_price_with_changed_price": 1,
                    "deactivate_from_action": 0,
                },
                "artifacts": {},
            },
            "artifacts": {},
        },
    )
    calls: list[tuple[str, str, dict]] = []

    def fake_api(token: str, method: str, payload: dict) -> dict:
        calls.append((token, method, payload))
        return {"ok": True, "result": {"message_id": 31}}

    result = notify_telegram_job_result(
        token="secret-token",
        job_id=job.job_id,
        store=store,
        data_dir=tmp_path / "data",
        api_request=fake_api,
    )

    assert result.ok is True
    payload = calls[0][2]
    assert "добавить: `2`" in payload["text"]
    assert payload["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == (
        "oe_apply:ozon_elastic_plan_notify_test"
    )


def test_job_worker_wb_actions_report_shows_participation_transitions(tmp_path: Path) -> None:
    csv_path = tmp_path / "wb-actions.csv"
    fieldnames = [
        "Акций",
        "Статусы в файлах акций",
        "Причина",
        "Текущая скидка",
        "Скидка до порога",
        "Финальная скидка",
        "Скидка к загрузке",
        "Осталось до целевой, п.п.",
    ]
    rows = [
        {
            "Акций": 1,
            "Статусы в файлах акций": "Да",
            "Причина": "скидка до порога <= 60%",
            "Текущая скидка": 57,
            "Скидка до порога": 57,
            "Финальная скидка": 57,
            "Скидка к загрузке": 57,
            "Осталось до целевой, п.п.": 0,
        },
        {
            "Акций": 1,
            "Статусы в файлах акций": "Да",
            "Причина": "скидка до порога > 60% -> 50%",
            "Текущая скидка": 64,
            "Скидка до порога": 64,
            "Финальная скидка": 50,
            "Скидка к загрузке": 50,
            "Осталось до целевой, п.п.": 0,
        },
        {
            "Акций": 1,
            "Статусы в файлах акций": "Нет",
            "Причина": "скидка до порога > 60% -> 50%",
            "Текущая скидка": 64,
            "Скидка до порога": 64,
            "Финальная скидка": 50,
            "Скидка к загрузке": 50,
            "Осталось до целевой, п.п.": 0,
        },
        {
            "Акций": 0,
            "Статусы в файлах акций": "",
            "Причина": "товара нет в активных акциях -> 50%",
            "Текущая скидка": 50,
            "Скидка до порога": "",
            "Финальная скидка": 50,
            "Скидка к загрузке": 50,
            "Осталось до целевой, п.п.": 0,
        },
        {
            "Акций": 1,
            "Статусы в файлах акций": "Да",
            "Причина": "скидка до порога > 60% -> 50%",
            "Текущая скидка": 50,
            "Скидка до порога": 64,
            "Финальная скидка": 50,
            "Скидка к загрузке": 50,
            "Осталось до целевой, п.п.": 0,
        },
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)

    store = JobStore(tmp_path / "runtime.db")
    job = store.create_job(
        task_id="wb-actions-discount-plan",
        actor="telegram:123",
        job_id="job_wb_actions_report_test",
        status="queued",
        params={"scheme_text": "60-50-50"},
    )
    store.update_job_status(
        job.job_id,
        "success",
        result={
            "status": "ok",
            "summary": {
                "run_id": "wb_actions_discount_plan_60-50-50_test",
                "overall_status": "ok",
                "summary": {
                    "scheme": "60-50-50",
                    "total_goods": 5,
                    "changed_rows": 2,
                    "no_change": 3,
                    "active_promos": 1,
                    "future_promos": 0,
                },
                "artifacts": {"csv": str(csv_path)},
            },
        },
    )
    completed = store.get_job(job.job_id)
    assert completed is not None

    text = build_job_result_text(completed)

    assert "участвуют в акциях: `2`" in text
    assert "скидка 64%: `1` товаров" in text
    assert "скидка 57%: `1` товаров" in text
    assert "снимутся с текущих акций: `1`" in text
    assert "требуемая скидка акции выше порога 60%" in text
    assert "будут участвовать в акциях: `1`" in text
    assert "не будут участвовать в акциях: `4`" in text


def test_job_worker_wb_min_price_plan_shows_apply_button(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "runtime.db")
    job = store.create_job(
        task_id="wb-best-price-action-plan",
        actor="telegram:123",
        job_id="job_wb_best_price_plan",
        status="queued",
        params={"outside_discount": 47},
    )
    store.update_job_status(
        job.job_id,
        "success",
        result={
            "status": "ok",
            "summary": {
                "run_id": "wb_best_price_actions_plan_47_20260729T120000",
                "overall_status": "ok",
                "outside_action_discount": 47,
                "safe_to_apply": True,
                "summary": {
                    "scope_total": 478,
                    "currently_participating": 11,
                    "offered_any_action": 355,
                    "not_offered": 123,
                    "eligible_any_action": 11,
                    "outside_action": 467,
                    "offered_but_below_minimum": 344,
                    "to_change_discount": 467,
                    "no_change_discount": 11,
                    "target_below_minimum": 0,
                    "unsafe_single_upload": 0,
                    "target_discount_distribution": {"47": 467, "59": 11},
                },
                "artifacts": {"report": str(tmp_path / "report.html")},
            },
        },
    )
    completed = store.get_job(job.job_id)
    assert completed is not None

    text, markup = build_job_result_presentation(completed)

    assert "Скидка вне подходящих акций: `47%`" in text
    assert "будут участвовать в лучшей допустимой акции: `11`" in text
    assert markup["inline_keyboard"][0][0]["callback_data"] == (
        "wbmp_apply:wb_best_price_actions_plan_47_20260729T120000"
    )


def test_job_worker_wb_actions_apply_reports_safe_step_and_offers_followup(
    tmp_path: Path,
) -> None:
    store = JobStore(tmp_path / "runtime.db")
    job = store.create_job(
        task_id="wb-actions-discount-apply",
        actor="telegram:123",
        job_id="job_wb_actions_safe_step_apply",
        status="queued",
        params={"plan_run_id": "wb_actions_discount_plan_60-50-50_test"},
    )
    store.update_job_status(
        job.job_id,
        "success",
        result={
            "status": "warning",
            "summary": {
                "run_id": "wb_actions_discount_apply_60-50-50_test",
                "overall_status": "warning",
                "approved_plan_run_id": "wb_actions_discount_plan_60-50-50_test",
                "scheme": "60-50-50",
                "applied": {"payload_rows_count": 328},
                "verify": {
                    "status": "ok",
                    "expected_rows": 328,
                    "success_rows": 328,
                    "failed_rows": 0,
                },
                "drift": {"skipped_due_to_drift_product_count": 0},
                "target_completion": {
                    "status": "safe_step_applied",
                    "rows_count": 328,
                    "direct_final_target_rows": 309,
                    "followup_required_rows": 19,
                    "followup_required": True,
                    "transitions": [
                        {
                            "current_discount": 0,
                            "uploaded_discount": 33,
                            "final_discount": 50,
                            "rows_count": 19,
                        }
                    ],
                },
                "artifacts": {},
            },
        },
    )
    completed = store.get_job(job.job_id)
    assert completed is not None

    text, markup = build_job_result_presentation(completed)

    assert "конечная схема достигнута не полностью" in text
    assert "остановились на безопасном промежуточном шаге: `19`" in text
    assert "`0% → 33% → 50%`: `19` товаров" in text
    assert "Осталось довести до конечной скидки: `19`" in text
    assert markup["inline_keyboard"][0][0]["callback_data"] == "wbam_confirm:60-50-50"


def test_poll_once_runtime_jobs_queues_operation_callback_without_direct_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from seller_agent.bot import telegram_runner

    calls: list[tuple[str, str, dict]] = []
    direct_callbacks: list[str] = []

    monkeypatch.setattr(
        telegram_runner,
        "dispatch_callback",
        lambda data, **kwargs: direct_callbacks.append(data) or pytest.fail("direct callback must not run"),
    )

    def fake_api(token: str, method: str, payload: dict) -> dict:
        calls.append((token, method, payload))
        if method == "getUpdates":
            return {
                "ok": True,
                "result": [
                    {
                        "update_id": 504,
                        "callback_query": {
                            "id": "cb-runtime",
                            "data": "mpr_run:o:f:2026-07-01:2026-07-16",
                            "message": {"chat": {"id": 123}, "message_thread_id": 55},
                        },
                    }
                ],
            }
        return {"ok": True, "result": {"message_id": 11}}

    runtime_db = tmp_path / "runtime.db"
    result = poll_once(
        token="secret-token",
        data_dir=tmp_path / "data",
        state_file=tmp_path / "state.json",
        allowed_chat_ids={123},
        runtime_jobs=True,
        runtime_db=runtime_db,
        api_request=fake_api,
    )

    assert result["ok"] is True
    assert direct_callbacks == []
    job = JobStore(runtime_db).list_jobs()[0]
    assert job.task_id == "marketplace-period-report"
    send_payload = next(payload for _, method, payload in calls if method == "sendMessage")
    assert "runtime-очередь" in send_payload["text"]


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


def test_poll_once_persists_conversation_state_from_callback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from seller_agent.bot import telegram_runner
    from seller_agent.bot.commands import TelegramCommandResult

    monkeypatch.setattr(
        telegram_runner,
        "dispatch_callback",
        lambda data, **kwargs: TelegramCommandResult(
            command="/period-report",
            ok=True,
            text="Введите дату начала",
            conversation_state={
                "stage": "period_report_custom_from",
                "market_code": "o",
                "report_code": "f",
            },
        ),
    )

    def fake_api(token: str, method: str, payload: dict) -> dict:
        if method == "getUpdates":
            return {
                "ok": True,
                "result": [
                    {
                        "update_id": 401,
                        "callback_query": {
                            "id": "cb-period",
                            "data": "mpr_period:o:f:c",
                            "message": {"chat": {"id": 123}, "message_thread_id": 55},
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
    state = json.loads(state_file.read_text(encoding="utf-8"))
    conversation = next(iter(state["conversations"].values()))
    assert conversation["stage"] == "period_report_custom_from"


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
