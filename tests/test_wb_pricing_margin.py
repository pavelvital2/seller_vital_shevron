from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from seller_agent.config import AppCredentials, WbCredentials
from seller_agent.tasks.wb_pricing_margin import run_wb_pricing_margin


def _write_catalog(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "internal_sku",
        "product_name",
        "pack_qty",
        "active_wb",
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
                "active_wb": "true",
                "wb_nm_id": "200",
                "wb_vendor_code": "wb-kit2",
            }
        )


class _Statistics:
    def fetch_orders(self, **kwargs):  # type: ignore[no-untyped-def]
        return [{"date": "2026-07-31T10:00:00", "isCancel": False, "priceWithDisc": 500}]


class _Finance:
    def fetch_sales_reports(self, **kwargs):  # type: ignore[no-untyped-def]
        return [
            {
                "dateFrom": "2026-07-31",
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

    def fetch_acquiring_reports(self, **kwargs):  # type: ignore[no-untyped-def]
        return []

    def fetch_sales_report_details(self, **kwargs):  # type: ignore[no-untyped-def]
        return [
            {
                "nmId": 200,
                "vendorCode": "wb-kit2",
                "docTypeName": "Продажа",
                "quantity": 1,
                "retailAmount": "500",
                "forPay": "350",
                "saleDt": "2026-07-31T11:00:00Z",
            }
        ]


class _Promotion:
    def fetch_campaign_count(self):  # type: ignore[no-untyped-def]
        return {"adverts": [{"advert_list": [{"advertId": 10}]}]}

    def fetch_fullstats(self, **kwargs):  # type: ignore[no-untyped-def]
        return [{"days": [{"sum": 20}]}]


class _Prices:
    def fetch_goods_prices(self):  # type: ignore[no-untyped-def]
        return [{"nmID": 200, "prices": [1200], "discount": 50, "discountedPrices": [600]}]


class _Analytics:
    def fetch_wb_warehouse_stocks(self):  # type: ignore[no-untyped-def]
        return [{"nmId": 200, "quantity": 12}]


def test_wb_pricing_margin_builds_dry_run_with_stock_and_action_gates(tmp_path: Path) -> None:
    _write_catalog(tmp_path / "catalog" / "unified" / "products.csv")

    result = run_wb_pricing_margin(
        credentials=AppCredentials(None, None, WbCredentials("token")),
        data_dir=tmp_path,
        unit_cost="85",
        target_margin="50",
        period_days=30,
        run_id="wb_pricing_margin_test",
        today=date(2026, 8, 1),
        statistics=_Statistics(),  # type: ignore[arg-type]
        finance=_Finance(),  # type: ignore[arg-type]
        promotion=_Promotion(),  # type: ignore[arg-type]
        prices=_Prices(),  # type: ignore[arg-type]
        analytics=_Analytics(),  # type: ignore[arg-type]
        action_membership={200: [{"currently_participates": True}]},
    )

    assert result["mode"] == "dry_run"
    assert set(result["period_comparison"]) == {"15", "30"}
    assert result["metrics"]["physical_pieces"] == 2
    assert result["products"][0]["stock_gate"] == "pass"
    assert result["products"][0]["in_action"] is True
    assert {row["pack_qty"] for row in result["price_ladder"]} >= {1, 2, 3, 4, 5}
    assert Path(result["artifacts"]["report"]).exists()
    assert Path(result["artifacts"]["csv"]).exists()
