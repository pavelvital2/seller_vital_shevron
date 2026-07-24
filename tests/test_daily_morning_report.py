from __future__ import annotations

from datetime import date
import json
from pathlib import Path

from seller_agent.config import AppCredentials, OzonSellerCredentials, WbCredentials
from seller_agent.tasks.daily_morning_report import (
    _pending_packages,
    _recommendations_summary,
    _summarize_wb_analytics_stocks,
    _summarize_wb_finance_expenses,
    _summarize_wb_orders,
    _summarize_wb_sales,
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
        "seller_agent.tasks.daily_morning_report.combined_session_status",
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
            {"supplierArticle": "sku-1", "finishedPrice": 100, "priceWithDisc": 150, "isCancel": False},
            {"supplierArticle": "sku-1", "priceWithDisc": "50.5", "isCancel": "false"},
            {"supplierArticle": "sku-2", "finishedPrice": 20, "priceWithDisc": 30, "isCancel": True},
        ]
    )

    assert summary["total_rows"] == 3
    assert summary["total_orders"] == 3
    assert summary["active_orders"] == 2
    assert summary["cancelled_orders"] == 1
    assert summary["amount"] == 230.5
    assert summary["active_amount"] == 200.5
    assert summary["cancelled_amount"] == 30
    assert summary["price_basis"] == "priceWithDisc"
    assert summary["amount_fields"]["all_rows"]["finishedPrice"] == 120
    assert summary["top_skus"] == [{"sku": "sku-1", "orders": 2}, {"sku": "sku-2", "orders": 1}]


def test_summarize_wb_sales_uses_dashboard_price_and_preserves_for_pay() -> None:
    summary = _summarize_wb_sales(
        [
            {"saleID": "S1", "priceWithDisc": 500, "finishedPrice": 400, "forPay": 350},
            {"saleID": "R1", "priceWithDisc": 100, "finishedPrice": 80, "forPay": 70},
        ]
    )

    assert summary["sales_amount"] == 500
    assert summary["returns_amount"] == 100
    assert summary["net_amount_estimate"] == 400
    assert summary["for_pay_amount"] == 350
    assert summary["net_for_pay_estimate"] == 280
    assert summary["price_basis"] == "priceWithDisc"


def test_summarize_wb_analytics_stocks_aggregates_warehouses_by_nm_id() -> None:
    summary = _summarize_wb_analytics_stocks(
        stock_rows=[
            {"nmId": 201, "quantity": 1, "inWayToClient": 2, "inWayFromClient": 0},
            {"nmId": 201, "quantity": 2, "inWayToClient": 0, "inWayFromClient": 1},
            {"nmId": 202, "quantity": 0, "inWayToClient": 0, "inWayFromClient": 0},
        ],
        catalog_rows=[
            {"wb_nm_id": "201", "wb_vendor_code": "sku-1", "internal_sku": "internal-1", "product_name": "One"},
            {"wb_nm_id": "202", "wb_vendor_code": "sku-2", "internal_sku": "internal-2", "product_name": "Two"},
            {"wb_nm_id": "203", "wb_vendor_code": "sku-3", "internal_sku": "internal-3", "product_name": "Three"},
        ],
    )

    assert summary["quantity_total"] == 3
    assert summary["nm_rows"] == 2
    assert summary["low_stock_count"] == 1
    assert summary["zero_stock_count"] == 1
    assert summary["missing_in_stock_source_count"] == 1
    assert summary["in_way_to_client"] == 2
    assert summary["in_way_from_client"] == 1


def test_daily_morning_report_seller_v2_uses_business_adapters(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "seller_agent.tasks.daily_morning_report.combined_session_status",
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
    _write_json(
        tmp_path / "catalog" / "unified" / "products.json",
        [
            {
                "internal_product_id": "chev_nr_svo_text0001",
                "internal_sku": "chev_nr_svo_text0001",
                "product_name": "Unified product",
                "product_group": "chev",
                "pack_qty": "1",
                "cost_total": "85",
                "cost_per_unit": "85",
                "ozon_offer_id": "sku-1",
                "ozon_product_id": "101",
                "ozon_sku": "901",
                "wb_vendor_code": "sku-1",
                "wb_nm_id": "201",
                "mapping_status": "confirmed",
                "active_ozon": "true",
                "active_wb": "true",
                "notes": "",
            },
            {
                "internal_product_id": "ozon:sku-2",
                "product_name": "Ozon only",
                "ozon_offer_id": "sku-2",
                "ozon_product_id": "102",
                "mapping_status": "ozon_only",
                "active_ozon": "true",
                "active_wb": "false",
            },
            {
                "internal_product_id": "wb:sku-3",
                "product_name": "WB only",
                "wb_vendor_code": "sku-3",
                "wb_nm_id": "203",
                "mapping_status": "wb_only",
                "active_ozon": "false",
                "active_wb": "true",
            },
        ],
    )

    class FakeOzonAdapter:
        def __init__(self, credentials):
            self.credentials = credentials

        def fetch_analytics_data(self, **kwargs):
            return {"result": {"totals": [1200, 3], "data": [{"metrics": [1200, 3]}]}}

        def fetch_product_stocks(self, product_ids):
            return [{"product_id": "101", "offer_id": "sku-1", "stocks": [{"present": 2, "reserved": 1}]}]

        def fetch_finance_transactions(
            self,
            *,
            date_from: str,
            date_to: str,
            transaction_type: str = "all",
            operation_type: list[str] | None = None,
            page_size: int = 1000,
        ):
            return [
                {
                    "operation_date": "2026-06-13 00:00:00",
                    "operation_type": "OperationAgentDeliveredToCustomer",
                    "accruals_for_sale": 600,
                    "amount": 250,
                    "items": [{"sku": "101"}],
                }
            ]

        def fetch_fbo_postings(self, *, since: str, to: str, status: str = "", limit: int = 100):
            return [
                {
                    "status": "delivered",
                    "products": [{"offer_id": "sku-1", "quantity": 2, "price": "300"}],
                },
                {
                    "status": "cancelled",
                    "products": [{"offer_id": "sku-1", "quantity": 1, "price": "300"}],
                },
            ]

        def fetch_review_count(self):
            return {"result": {"UNPROCESSED": 3}}

        def fetch_review_list(self, *, status: str = "ALL", limit: int = 100, sort_dir: str = "DESC"):
            return {"result": {"reviews": [{"published_at": "2026-06-13T09:00:00Z"}]}}

        def fetch_question_count(self):
            return {"result": {"NEW": 2}}

        def fetch_question_list(self, *, status: str = "ALL", limit: int = 100, offset: int = 0, sort_dir: str = "DESC"):
            return {"result": {"questions": [{"published_at": "2026-06-13T10:00:00Z"}]}}

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

    class FakeWbAnalyticsAdapter:
        def __init__(self, credentials):
            self.credentials = credentials

        def fetch_wb_warehouse_stocks(self):
            return [{"nmId": 201, "quantity": 2, "inWayToClient": 0, "inWayFromClient": 0}]

    monkeypatch.setattr("seller_agent.tasks.daily_morning_report.OzonSellerAdapter", FakeOzonAdapter)
    monkeypatch.setattr("seller_agent.tasks.daily_morning_report.WbStatisticsAdapter", FakeWbStatisticsAdapter)
    monkeypatch.setattr("seller_agent.tasks.daily_morning_report.WbCommunicationsAdapter", FakeWbCommunicationsAdapter)
    monkeypatch.setattr("seller_agent.tasks.daily_morning_report.WbAnalyticsAdapter", FakeWbAnalyticsAdapter)

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


def test_daily_morning_report_seller_v3_uses_new_template(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "seller_agent.tasks.daily_morning_report.combined_session_status",
        lambda: {"overall_status": "ok", "sessions": {}},
    )
    monkeypatch.setattr("seller_agent.tasks.daily_morning_report._moscow_today", lambda: date(2026, 6, 14))
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
                "ozon_refresh_state": {"status": "ok"},
                "wb_refresh_state": {"status": "ok"},
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
    _write_json(
        tmp_path / "catalog" / "unified" / "products.json",
        [
            {
                "internal_product_id": "chev_nr_svo_text0001",
                "internal_sku": "chev_nr_svo_text0001",
                "product_name": "Unified product",
                "product_group": "chev",
                "pack_qty": "1",
                "cost_total": "85",
                "cost_per_unit": "85",
                "ozon_offer_id": "sku-1",
                "ozon_product_id": "101",
                "ozon_sku": "901",
                "wb_vendor_code": "sku-1",
                "wb_nm_id": "201",
                "mapping_status": "confirmed",
                "active_ozon": "true",
                "active_wb": "true",
                "notes": "",
            },
            {
                "internal_product_id": "ozon:sku-2",
                "product_name": "Ozon only",
                "ozon_offer_id": "sku-2",
                "ozon_product_id": "102",
                "mapping_status": "ozon_only",
                "active_ozon": "true",
                "active_wb": "false",
            },
            {
                "internal_product_id": "wb:sku-3",
                "product_name": "WB only",
                "wb_vendor_code": "sku-3",
                "wb_nm_id": "203",
                "mapping_status": "wb_only",
                "active_ozon": "false",
                "active_wb": "true",
            },
        ],
    )
    _write_json(
        tmp_path / "runs" / "2026-06-11" / "ozon_elastic_plan_test" / "summary.json",
        {
            "run_id": "ozon_elastic_plan_test",
            "mode": "dry-run",
            "summary": {
                "action_name": "Elastic",
                "merged_unique_products": 10,
                "active_rows": 8,
                "skip_candidate": 2,
            },
        },
    )
    _write_json(
        tmp_path / "runs" / "2026-06-11" / "wb_actions_discount_plan_test" / "summary.json",
        {
            "run_id": "wb_actions_discount_plan_test",
            "mode": "dry-run",
            "summary": {
                "active_promos": 2,
                "future_promos": 1,
                "in_promos": 7,
                "outside_promos": 3,
            },
        },
    )

    class FakeOzonAdapter:
        analytics_calls = 0

        def __init__(self, credentials):
            self.credentials = credentials

        def fetch_analytics_data(self, **kwargs):
            type(self).analytics_calls += 1
            return {"result": {"totals": [1200, 3], "data": [{"metrics": [1200, 3]}]}}

        def fetch_product_stocks(self, product_ids):
            return [{"product_id": "101", "offer_id": "sku-1", "stocks": [{"present": 2, "reserved": 1}]}]

        def fetch_finance_transactions(
            self,
            *,
            date_from: str,
            date_to: str,
            transaction_type: str = "all",
            operation_type: list[str] | None = None,
            page_size: int = 1000,
        ):
            return [
                {
                    "operation_date": "2026-06-13 00:00:00",
                    "operation_type": "OperationAgentDeliveredToCustomer",
                    "accruals_for_sale": 600,
                    "amount": 250,
                    "items": [{"sku": "101"}],
                }
            ]

        def fetch_fbo_postings(self, *, since: str, to: str, status: str = "", limit: int = 100):
            return [
                {
                    "status": "delivered",
                    "products": [{"offer_id": "sku-1", "quantity": 2, "price": "300"}],
                },
                {
                    "status": "cancelled",
                    "products": [{"offer_id": "sku-1", "quantity": 1, "price": "300"}],
                },
            ]

        def fetch_review_count(self):
            return {"result": {"UNPROCESSED": 3}}

        def fetch_review_list(self, *, status: str = "ALL", limit: int = 100, sort_dir: str = "DESC"):
            return {"result": {"reviews": [{"published_at": "2026-06-13T09:00:00Z"}]}}

        def fetch_question_count(self):
            return {"result": {"NEW": 2}}

        def fetch_question_list(self, *, status: str = "ALL", limit: int = 100, offset: int = 0, sort_dir: str = "DESC"):
            return {"result": {"questions": [{"published_at": "2026-06-13T10:00:00Z"}]}}

        def fetch_supply_order_ids(self, *, states, limit: int = 100):
            return ["ozon-supply-1"]

        def fetch_supply_orders(self, order_ids, *, batch_size: int = 50):
            return [{"order_id": "ozon-supply-1", "state": "IN_TRANSIT"}]

    class FakeWbStatisticsAdapter:
        def __init__(self, credentials):
            self.credentials = credentials

        def fetch_orders(self, *, date_from: str, flag: int = 1):
            return [
                {"date": date_from, "supplierArticle": "sku-1", "finishedPrice": 500, "isCancel": False},
                {"date": date_from, "supplierArticle": "sku-1", "finishedPrice": 500, "isCancel": True},
            ]

        def fetch_sales(self, *, date_from: str, flag: int = 1):
            return [
                {"date": date_from, "supplierArticle": "sku-1", "forPay": 450, "saleID": "S123"},
                {"date": date_from, "supplierArticle": "sku-1", "forPay": 100, "saleID": "R123"},
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

        def fetch_feedbacks(self, *, is_answered: bool = False, take: int = 5000, skip: int = 0, order: str = "dateDesc"):
            return {"data": {"feedbacks": [{"createdDate": "2026-06-13T10:00:00Z"}]}}

        def fetch_questions(self, *, is_answered: bool = False, take: int = 10000, skip: int = 0, order: str = "dateDesc"):
            return {"data": {"questions": [{"createdDate": "2026-06-13T11:00:00Z"}]}}

    class FakeWbFinanceAdapter:
        def __init__(self, credentials):
            self.credentials = credentials

        def fetch_sales_reports(self, *, date_from: str, date_to: str, period: str = "daily", limit: int = 1000):
            return [
                {
                    "reportId": 1,
                    "dateFrom": date_from,
                    "dateTo": date_to,
                    "createDate": "2026-06-14",
                    "retailAmountSum": "600",
                    "forPaySum": "450",
                    "deliveryServiceSum": "50",
                    "paidStorageSum": "10",
                    "paidAcceptanceSum": "0",
                    "deductionSum": "5",
                    "penaltySum": "0",
                    "additionalPaymentSum": "0",
                    "cashbackAmountSum": "0",
                    "cashbackDiscountSum": "0",
                    "cashbackCommissionChangeSum": "0",
                    "bankPaymentSum": "385",
                }
            ]

        def fetch_acquiring_reports(self, *, date_from: str, date_to: str, limit: int = 1000):
            return []

    class FakeWbPromotionAdapter:
        def __init__(self, credentials):
            self.credentials = credentials

        def fetch_campaign_count(self):
            return {"adverts": [{"advert_list": [{"advertId": 10}]}]}

        def fetch_fullstats(self, *, ids, date_from: str, date_to: str):
            return [{"advertId": 10, "days": [{"date": date_from, "sum": 15}]}]

    class FakeWbAnalyticsAdapter:
        def __init__(self, credentials):
            self.credentials = credentials

        def fetch_wb_warehouse_stocks(self):
            return [{"nmId": 201, "quantity": 2, "inWayToClient": 0, "inWayFromClient": 0}]

    class FakeWbFbwSuppliesAdapter:
        def __init__(self, credentials):
            self.credentials = credentials

        def fetch_supplies(self):
            return [{"supplyID": 1, "statusID": 3}]

    monkeypatch.setattr("seller_agent.tasks.daily_morning_report.OzonSellerAdapter", FakeOzonAdapter)
    monkeypatch.setattr("seller_agent.tasks.daily_morning_report.WbStatisticsAdapter", FakeWbStatisticsAdapter)
    monkeypatch.setattr("seller_agent.tasks.daily_morning_report.WbCommunicationsAdapter", FakeWbCommunicationsAdapter)
    monkeypatch.setattr("seller_agent.tasks.daily_morning_report.WbFinanceAdapter", FakeWbFinanceAdapter)
    monkeypatch.setattr("seller_agent.tasks.daily_morning_report.WbPromotionAdapter", FakeWbPromotionAdapter)
    monkeypatch.setattr("seller_agent.tasks.daily_morning_report.WbAnalyticsAdapter", FakeWbAnalyticsAdapter)
    monkeypatch.setattr("seller_agent.tasks.daily_morning_report.WbFbwSuppliesAdapter", FakeWbFbwSuppliesAdapter)

    result = run_daily_morning_report(
        credentials=AppCredentials(
            ozon_seller=OzonSellerCredentials(client_id="id", api_key="key"),
            ozon_performance=None,
            wb=WbCredentials(token="token"),
        ),
        data_dir=tmp_path,
        run_id="daily_morning_report_v3_test",
        refresh_preflight=False,
        seller_v3=True,
    )

    assert result["report_version"] == "seller_v3"
    assert result["unified_catalog"]["products"] == 3
    assert result["unified_catalog"]["confirmed_products"] == 1
    assert result["unified_catalog"]["target_products"] == 1
    assert result["unified_catalog"]["target_with_internal_sku"] == 1
    assert result["unified_catalog"]["target_identification_complete"] is True
    assert result["unified_catalog"]["both_marketplaces_products"] == 1
    assert result["unified_catalog"]["active_ozon_only_products"] == 1
    assert result["unified_catalog"]["active_wb_only_products"] == 1
    assert result["unified_catalog"]["ozon_only_products"] == 1
    assert result["unified_catalog"]["wb_only_products"] == 1
    assert result["business"]["catalog_source"] == "unified_catalog"
    assert result["business"]["ozon"]["stocks"]["low_stock_sample"][0]["internal_sku"] == "chev_nr_svo_text0001"
    assert result["business"]["wb"]["stocks"]["low_stock_sample"][0]["internal_sku"] == "chev_nr_svo_text0001"
    assert result["actions_v3"]["ozon"]["products_not_in_actions"] == 2
    assert result["business"]["ozon"]["finance_buyouts"]["buyout_units"] == 1
    assert result["business"]["ozon"]["finance_expenses"]["total_expenses"] == 350.0
    assert result["business"]["ozon"]["fbo_postings"]["cancelled_units"] == 1
    assert result["business"]["wb"]["finance_expenses"]["total_expenses"] == 230.0
    assert result["business"]["ozon"]["communications"]["unanswered_questions"] == 2
    assert result["business"]["wb"]["communications"]["unanswered_questions"] == 5
    assert FakeOzonAdapter.analytics_calls == 1
    assert result["supplies_v3"]["ozon"]["in_transit"] == 1
    assert result["supplies_v3"]["wb"]["ready_to_ship"] == 1
    report_text = Path(result["artifacts"]["report"]).read_text(encoding="utf-8")
    assert "Период данных:" in report_text
    assert "Единый Каталог" in report_text
    assert "| Товаров всего | 3 |" in report_text
    assert "| Целевой ассортимент идентифицирован | да |" in report_text
    assert "| Связанные пары Ozon+WB | 1 |" in report_text
    assert "Mapping подтвержден" not in report_text
    assert "Заказы, Выкупы, Отмены За Период" in report_text
    assert "Деньги И Расходы За Период" in report_text
    assert "| Выкупы, шт. | 1 | 1 |" in report_text
    assert "| Отмены, шт. | 1 | 1 |" in report_text
    assert "| Расходы всего, ₽ | 350 ₽ | 230 ₽ |" in report_text
    assert "| Отзывы за период 00:00-23:59 | 1 | 2 |" in report_text
    assert "| Товаров участвует | 8 | 7 |" in report_text
    assert "| WB-артикулы без строки в источнике остатков | - | 1 |" in report_text
    assert "пакеты на согласование" in report_text
    assert "| Есть текущие поставки | да | да |" in report_text


def test_wb_finance_expenses_separates_credit_from_current_expenses() -> None:
    result = _summarize_wb_finance_expenses(
        reports=[
            {
                "reportId": 1,
                "retailAmountSum": "4410",
                "forPaySum": "3801.93",
                "deliveryServiceSum": "1006.82",
                "paidStorageSum": "607.32",
                "deductionSum": "-21640.68",
                "paymentSchedule": "195.65",
                "bankPaymentSum": "23632.82",
            },
            {
                "reportId": 2,
                "retailAmountSum": "313.27",
                "forPaySum": "211.15",
                "deliveryServiceSum": "60.99",
                "bankPaymentSum": "150.16",
            },
        ],
        acquiring_reports=[],
        ad_spend=275.08,
    )

    assert result["total_expenses"] == 2856.05
    assert result["net_after_expenses"] == 1867.22
    assert result["total_credits_and_adjustments"] == 21640.68
    assert result["cash_after_adjustments_and_ads"] == 23507.9
    assert result["bank_payment_reconciliation_delta"] == 0.0
    assert result["expenses"]["deductions"] == 0.0
    assert result["credits_and_adjustments"]["deductions_credit"] == 21640.68
