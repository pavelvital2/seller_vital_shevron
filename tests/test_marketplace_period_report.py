from __future__ import annotations

import csv
from pathlib import Path

from seller_agent.config import AppCredentials, OzonSellerCredentials, WbCredentials
from seller_agent.tasks.marketplace_period_report import run_marketplace_period_report


def _write_catalog(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "internal_sku",
        "product_name",
        "pack_qty",
        "ozon_sku",
        "ozon_offer_id",
        "ozon_product_id",
        "wb_nm_id",
        "wb_vendor_code",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow(
            {
                "internal_sku": "kit2",
                "product_name": "Комплект 2 шт.",
                "pack_qty": "2",
                "ozon_sku": "100",
                "wb_nm_id": "200",
                "wb_vendor_code": "wb-kit2",
            }
        )


def test_ozon_period_report_counts_physical_pieces(tmp_path: Path, monkeypatch) -> None:
    from seller_agent.tasks import marketplace_period_report as module

    _write_catalog(tmp_path / "catalog" / "unified" / "products.csv")

    class FakeOzon:
        def __init__(self, credentials) -> None:  # type: ignore[no-untyped-def]
            self.credentials = credentials

        def fetch_finance_transactions(self, **kwargs):  # type: ignore[no-untyped-def]
            return [
                {
                    "operation_type": "OperationAgentDeliveredToCustomer",
                    "operation_date": "2026-07-01T12:00:00Z",
                    "accruals_for_sale": 300,
                    "sale_commission": -60,
                    "amount": 200,
                    "services": [{"price": -40}],
                    "items": [{"sku": 100, "quantity": 1}],
                },
                {
                    "operation_type": "OperationMarketplaceCostPerClick",
                    "operation_date": "2026-07-01T13:00:00Z",
                    "amount": -20,
                },
            ]

        def fetch_analytics_data(self, **kwargs):  # type: ignore[no-untyped-def]
            return {
                "result": {
                    "data": [
                        {
                            "dimensions": [{"id": "2026-07-01"}],
                            "metrics": [2, 500, 1, 0],
                        }
                    ]
                }
            }

    monkeypatch.setattr(module, "OzonSellerAdapter", FakeOzon)
    result = run_marketplace_period_report(
        credentials=AppCredentials(OzonSellerCredentials("id", "key"), None, None),
        data_dir=tmp_path,
        marketplace="ozon",
        report_type="full",
        date_from="2026-07-01",
        date_to="2026-07-01",
        run_id="marketplace_period_report_ozon_test",
    )

    metrics = result["metrics"]
    assert metrics["orders"] == 2
    assert metrics["buyout_units"] == 1
    assert metrics["physical_pieces"] == 2
    assert metrics["net"] == 180.0
    assert metrics["net_per_piece"] == 90.0
    assert Path(result["artifacts"]["report"]).exists()
    assert Path(result["artifacts"]["xlsx"]).exists()


def test_wb_period_report_uses_finance_details_and_ads(tmp_path: Path, monkeypatch) -> None:
    from seller_agent.tasks import marketplace_period_report as module

    _write_catalog(tmp_path / "catalog" / "unified" / "products.csv")

    class FakeStatistics:
        def __init__(self, credentials) -> None:  # type: ignore[no-untyped-def]
            pass

        def fetch_orders(self, **kwargs):  # type: ignore[no-untyped-def]
            return [{"date": "2026-07-01T10:00:00", "isCancel": False, "priceWithDisc": 400}]

    class FakeFinance:
        def __init__(self, credentials) -> None:  # type: ignore[no-untyped-def]
            pass

        def fetch_sales_reports(self, **kwargs):  # type: ignore[no-untyped-def]
            return [
                {
                    "dateFrom": "2026-07-01",
                    "retailAmountSum": "500",
                    "forPaySum": "350",
                    "bankPaymentSum": "300",
                    "deliveryServiceSum": "60",
                    "paidStorageSum": "10",
                    "paidAcceptanceSum": "5",
                    "deductionSum": "0",
                    "penaltySum": "0",
                }
            ]

        def fetch_sales_report_details(self, **kwargs):  # type: ignore[no-untyped-def]
            return [
                {
                    "nmId": 200,
                    "vendorCode": "wb-kit2",
                    "docTypeName": "Продажа",
                    "quantity": 1,
                    "retailAmount": "500",
                    "forPay": "350",
                    "saleDt": "2026-07-01T11:00:00Z",
                },
                {
                    "nmId": 200,
                    "docTypeName": "Возврат",
                    "quantity": 1,
                    "saleDt": "2026-07-01T12:00:00Z",
                },
            ]

    class FakePromotion:
        def __init__(self, credentials) -> None:  # type: ignore[no-untyped-def]
            pass

        def fetch_campaign_count(self):  # type: ignore[no-untyped-def]
            return {"adverts": [{"advert_list": [{"advertId": 10}]}]}

        def fetch_fullstats(self, **kwargs):  # type: ignore[no-untyped-def]
            return [{"days": [{"sum": 20}]}]

    monkeypatch.setattr(module, "WbStatisticsAdapter", FakeStatistics)
    monkeypatch.setattr(module, "WbFinanceAdapter", FakeFinance)
    monkeypatch.setattr(module, "WbPromotionAdapter", FakePromotion)
    result = run_marketplace_period_report(
        credentials=AppCredentials(None, None, WbCredentials("token")),
        data_dir=tmp_path,
        marketplace="wb",
        report_type="financial",
        date_from="2026-07-01",
        date_to="2026-07-01",
        run_id="marketplace_period_report_wb_test",
    )

    metrics = result["metrics"]
    assert metrics["orders"] == 1
    assert metrics["buyout_units"] == 1
    assert metrics["physical_pieces"] == 2
    assert metrics["advertising"] == 20.0
    assert metrics["net"] == 280.0
    assert metrics["net_per_piece"] == 140.0
