from __future__ import annotations

from datetime import date
import json
from pathlib import Path

from seller_agent.config import AppCredentials, OzonSellerCredentials, WbCredentials
from seller_agent.tasks.daily_morning_report import (
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

    monkeypatch.setattr("seller_agent.tasks.daily_morning_report.OzonSellerAdapter", FakeOzonAdapter)
    monkeypatch.setattr("seller_agent.tasks.daily_morning_report.WbStatisticsAdapter", FakeWbStatisticsAdapter)
    monkeypatch.setattr("seller_agent.tasks.daily_morning_report.WbCommunicationsAdapter", FakeWbCommunicationsAdapter)

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

    class FakeWbPromotionAdapter:
        def __init__(self, credentials):
            self.credentials = credentials

        def fetch_campaign_count(self):
            return {"adverts": [{"advert_list": [{"advertId": 10}]}]}

        def fetch_fullstats(self, *, ids, date_from: str, date_to: str):
            return [{"advertId": 10, "days": [{"date": date_from, "sum": 15}]}]

    monkeypatch.setattr("seller_agent.tasks.daily_morning_report.OzonSellerAdapter", FakeOzonAdapter)
    monkeypatch.setattr("seller_agent.tasks.daily_morning_report.WbStatisticsAdapter", FakeWbStatisticsAdapter)
    monkeypatch.setattr("seller_agent.tasks.daily_morning_report.WbCommunicationsAdapter", FakeWbCommunicationsAdapter)
    monkeypatch.setattr("seller_agent.tasks.daily_morning_report.WbFinanceAdapter", FakeWbFinanceAdapter)
    monkeypatch.setattr("seller_agent.tasks.daily_morning_report.WbPromotionAdapter", FakeWbPromotionAdapter)

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
    assert result["actions_v3"]["ozon"]["products_not_in_actions"] == 2
    assert result["business"]["ozon"]["finance_buyouts"]["buyout_units"] == 1
    assert result["business"]["ozon"]["finance_expenses"]["total_expenses"] == 350.0
    assert result["business"]["ozon"]["fbo_postings"]["cancelled_units"] == 1
    assert result["business"]["wb"]["finance_expenses"]["total_expenses"] == 230.0
    assert result["business"]["ozon"]["communications"]["unanswered_questions"] == 2
    assert result["business"]["wb"]["communications"]["unanswered_questions"] == 5
    report_text = Path(result["artifacts"]["report"]).read_text(encoding="utf-8")
    assert "Период данных:" in report_text
    assert "Заказы, Выкупы, Отмены За Период" in report_text
    assert "Деньги И Расходы За Период" in report_text
    assert "| Выкупы, шт. | 1 | 1 |" in report_text
    assert "| Отмены, шт. | 1 | 1 |" in report_text
    assert "| Расходы всего, ₽ | 350 ₽ | 230 ₽ |" in report_text
    assert "| Отзывы за период 00:00-23:59 | 1 | 2 |" in report_text
    assert "| Товаров участвует | 8 | 7 |" in report_text
    assert "| Товаров участвует | 8 | 7 |" in report_text
    assert "| WB-артикулы без строки в источнике остатков | - | 0 |" in report_text
    assert "пакеты на согласование" in report_text
    assert "| Есть текущие поставки | не подтверждено | не подтверждено |" in report_text
