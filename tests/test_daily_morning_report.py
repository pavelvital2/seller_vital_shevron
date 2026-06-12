from __future__ import annotations

import json
from pathlib import Path

from takterra_agent.config import AppCredentials, OzonSellerCredentials, WbCredentials
from takterra_agent.tasks.daily_morning_report import (
    _pending_packages,
    _recommendations_summary,
    _summarize_wb_orders,
    latest_run_dirs,
    run_daily_morning_report,
)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_latest_run_dirs_filters_known_prefixes(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "runs" / "2026-06-11" / "status_preflight_test" / "summary.json",
        {"run_id": "status_preflight_test", "overall_status": "ok", "started_at": "2026-06-11T08:00:00"},
    )
    _write_json(
        tmp_path / "runs" / "2026-06-11" / "unknown_test" / "summary.json",
        {"run_id": "unknown_test", "overall_status": "ok"},
    )

    runs = latest_run_dirs(tmp_path)

    assert [run["run_id"] for run in runs] == ["status_preflight_test"]
    assert runs[0]["overall_status"] == "ok"


def test_pending_packages_marks_applied_by_apply_run(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "pending" / "actions_apply_pending_x" / "manifest.json",
        {"pending_id": "actions_apply_pending_x", "status": "pending_explicit_apply_approval"},
    )
    _write_json(
        tmp_path / "runs" / "2026-06-11" / "actions_apply_y" / "summary.json",
        {"run_id": "actions_apply_y", "overall_status": "ok", "pending_id": "actions_apply_pending_x"},
    )

    packages = _pending_packages(tmp_path)

    assert packages == [
        {
            "pending_id": "actions_apply_pending_x",
            "path": str(tmp_path / "pending" / "actions_apply_pending_x"),
            "manifest": str(tmp_path / "pending" / "actions_apply_pending_x" / "manifest.json"),
            "status": "applied",
            "created_at": "",
            "applied_run_id": "actions_apply_y",
        }
    ]


def test_recommendations_summary_counts_open_items(tmp_path: Path) -> None:
    path = tmp_path / "planning" / "recommendations_index.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "\n".join(
            [
                "| ID | Тема | Статус | Где детали | Следующий шаг |",
                "| --- | --- | --- | --- | --- |",
                "| REC-001 | Done | implemented | docs | no-op |",
                "| REC-002 | Open | proposed | docs | decide |",
                "| REC-003 | Accepted | accepted | docs | build |",
            ]
        ),
        encoding="utf-8",
    )

    summary = _recommendations_summary(tmp_path)

    assert summary["status_counts"] == {"accepted": 1, "implemented": 1, "proposed": 1}
    assert [item["id"] for item in summary["open_items"]] == ["REC-002", "REC-003"]


def test_daily_morning_report_uses_latest_preflight_without_refresh(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "takterra_agent.tasks.daily_morning_report.combined_session_status",
        lambda: {"overall_status": "ok", "sessions": {}},
    )
    _write_json(
        tmp_path / "runs" / "2026-06-11" / "status_preflight_test" / "summary.json",
        {
            "run_id": "status_preflight_test",
            "overall_status": "ok",
            "checks": {
                "ozon_api": {"status": "ok"},
                "ozon_performance_api": {"status": "ok"},
                "wb_api": {"status": "ok"},
                "master_catalog": {"status": "ok", "rows": 203, "matched_rows": 203},
                "ozon_refresh_state": {"status": "ok", "age_seconds": 10, "interval_seconds": 1800},
                "wb_refresh_state": {"status": "ok", "age_seconds": 20, "interval_seconds": 3600},
            },
        },
    )
    _write_json(
        tmp_path / "runs" / "2026-06-10" / "actions_apply_test" / "summary.json",
        {
            "run_id": "actions_apply_test",
            "overall_status": "ok",
            "pending_id": "pending_test",
            "ozon": {"activate_rows_count": 12, "deactivate_rows_count": 5},
            "wb": {"payload_rows_count": 151, "upload_id": 123},
        },
    )

    result = run_daily_morning_report(
        credentials=AppCredentials(ozon_seller=None, ozon_performance=None, wb=None),
        data_dir=tmp_path,
        run_id="daily_morning_report_test",
        refresh_preflight=False,
    )

    assert result["overall_status"] == "ok"
    assert result["project_health"]["preflight_run_id"] == "status_preflight_test"
    assert result["catalog"]["rows"] == 203
    assert result["actions"]["last_apply_summary"]["wb_upload_id"] == 123
    assert (Path(result["artifacts"]["report"])).exists()


def test_summarize_wb_orders_counts_active_cancelled_and_amount() -> None:
    summary = _summarize_wb_orders(
        [
            {"supplierArticle": "sku-1", "finishedPrice": 100, "isCancel": False},
            {"supplierArticle": "sku-1", "priceWithDisc": "50.5", "isCancel": "false"},
            {"supplierArticle": "sku-2", "finishedPrice": 30, "isCancel": True},
        ]
    )

    assert summary["total_rows"] == 3
    assert summary["active_orders"] == 2
    assert summary["cancelled_orders"] == 1
    assert summary["amount"] == 150.5
    assert summary["top_skus"] == [{"sku": "sku-1", "orders": 2}]


def test_daily_morning_report_seller_v2_uses_business_adapters(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "takterra_agent.tasks.daily_morning_report.combined_session_status",
        lambda: {"overall_status": "ok", "sessions": {}},
    )
    _write_json(
        tmp_path / "runs" / "2026-06-11" / "status_preflight_test" / "summary.json",
        {
            "run_id": "status_preflight_test",
            "overall_status": "ok",
            "checks": {
                "ozon_api": {"status": "ok"},
                "ozon_performance_api": {"status": "ok"},
                "wb_api": {"status": "ok"},
                "master_catalog": {"status": "ok", "rows": 1, "matched_rows": 1},
            },
        },
    )
    _write_json(
        tmp_path / "catalog" / "processed" / "master_catalog.json",
        [
            {
                "master_sku": "sku-1",
                "title": "Test product",
                "ozon_product_id": "101",
                "wb_vendor_code": "sku-1",
            }
        ],
    )

    class FakeOzonAdapter:
        def __init__(self, credentials):
            self.credentials = credentials

        def fetch_analytics_data(self, **kwargs):
            return {"result": {"totals": [1200, 3], "data": [{"metrics": [1200, 3]}]}}

        def fetch_product_stocks(self, product_ids):
            return [{"product_id": "101", "offer_id": "sku-1", "stocks": [{"present": 2, "reserved": 1}]}]

    class FakeWbStatisticsAdapter:
        def __init__(self, credentials):
            self.credentials = credentials

        def fetch_orders(self, *, date_from: str, flag: int = 1):
            return [
                {"supplierArticle": "sku-1", "finishedPrice": 500, "isCancel": False},
                {"supplierArticle": "sku-1", "finishedPrice": 500, "isCancel": True},
            ]

        def fetch_sales(self, *, date_from: str, flag: int = 1):
            return [
                {"supplierArticle": "sku-1", "forPay": 450, "saleID": "S123"},
                {"supplierArticle": "sku-1", "forPay": 100, "saleID": "R123"},
            ]

        def fetch_stocks_legacy(self, *, date_from: str):
            return [{"supplierArticle": "sku-1", "quantity": 2}]

    class FakeWbCommunicationsAdapter:
        def __init__(self, credentials):
            self.credentials = credentials

        def fetch_unanswered_feedbacks_count(self):
            return {"data": {"count": 4}}

        def fetch_unanswered_questions_count(self):
            return {"data": {"count": 5}}

    monkeypatch.setattr("takterra_agent.tasks.daily_morning_report.OzonSellerAdapter", FakeOzonAdapter)
    monkeypatch.setattr("takterra_agent.tasks.daily_morning_report.WbStatisticsAdapter", FakeWbStatisticsAdapter)
    monkeypatch.setattr("takterra_agent.tasks.daily_morning_report.WbCommunicationsAdapter", FakeWbCommunicationsAdapter)

    result = run_daily_morning_report(
        credentials=AppCredentials(
            ozon_seller=OzonSellerCredentials(client_id="id", api_key="key"),
            ozon_performance=None,
            wb=WbCredentials(token="token"),
        ),
        data_dir=tmp_path,
        run_id="daily_morning_report_v2_test",
        refresh_preflight=False,
        seller_v2=True,
    )

    assert result["report_version"] == "seller_v2"
    assert result["business"]["ozon"]["orders"]["today"]["ordered_units"] == 3
    assert result["business"]["ozon"]["stocks"]["low_stock_count"] == 1
    assert result["business"]["wb"]["orders"]["today"]["active_orders"] == 1
    assert result["business"]["wb"]["communications"]["unanswered_questions"] == 5
    report_path = Path(result["artifacts"]["report"])
    assert report_path.exists()
    assert "Что Важно Сегодня" in report_path.read_text(encoding="utf-8")
