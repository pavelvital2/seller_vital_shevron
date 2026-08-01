from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from seller_agent.config import AppCredentials
from seller_agent.tasks.ozon_pricing_margin import run_ozon_pricing_margin


class FakeOzonAdapter:
    def fetch_finance_transactions(self, *, date_from: str, date_to: str):  # type: ignore[no-untyped-def]
        assert date_from.startswith(("2026-06-15", "2026-05-31"))
        assert date_to.startswith("2026-06-29")
        return [
            {
                "operation_type": "OperationAgentDeliveredToCustomer",
                "accruals_for_sale": 500,
                "amount": 200,
                "sale_commission": -200,
                "services": [{"price": -100}],
                "items": [{"sku": "sku-1", "quantity": 1}],
            },
            {
                "operation_type": "OperationAgentDeliveredToCustomer",
                "accruals_for_sale": 700,
                "amount": 320,
                "sale_commission": -280,
                "services": [{"price": -100}],
                "items": [{"sku": "sku-2", "quantity": 1}],
            },
        ]

    def fetch_analytics_data(self, **kwargs):  # type: ignore[no-untyped-def]
        return {"result": {"data": []}}

    def fetch_returns(self, **kwargs):  # type: ignore[no-untyped-def]
        return []

    def fetch_product_info_prices(self):  # type: ignore[no-untyped-def]
        return [
            {
                "offer_id": "offer-1",
                "product_id": 101,
                "sku": "sku-1",
                "price": {"price": "500", "old_price": "1000", "min_price": "400"},
                "commissions": {"sales_percent_fbo": 44},
            },
            {
                "offer_id": "offer-2",
                "product_id": 102,
                "sku": "sku-2",
                "price": {"price": "700", "old_price": "1400", "min_price": "600"},
                "commissions": {"sales_percent_fbo": 44},
            },
        ]

    def fetch_stock_on_warehouses(self):  # type: ignore[no-untyped-def]
        return [
            {"sku": "sku-1", "free_to_sell_amount": 3},
            {"sku": "sku-2", "free_to_sell_amount": 4},
        ]


def _write_catalog(data_dir: Path) -> None:
    path = data_dir / "catalog" / "unified" / "products.csv"
    path.parent.mkdir(parents=True)
    fields = [
        "internal_sku",
        "product_name",
        "pack_qty",
        "ozon_offer_id",
        "ozon_product_id",
        "ozon_sku",
        "active_ozon",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(
            [
                {
                    "internal_sku": "single",
                    "product_name": "Single",
                    "pack_qty": "1",
                    "ozon_offer_id": "offer-1",
                    "ozon_product_id": "101",
                    "ozon_sku": "sku-1",
                    "active_ozon": "true",
                },
                {
                    "internal_sku": "kit2",
                    "product_name": "Kit 2",
                    "pack_qty": "2",
                    "ozon_offer_id": "offer-2",
                    "ozon_product_id": "102",
                    "ozon_sku": "sku-2",
                    "active_ozon": "true",
                },
            ]
        )


def test_ozon_pricing_margin_calculates_expenses_and_kit_prices(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_catalog(data_dir)

    result = run_ozon_pricing_margin(
        credentials=AppCredentials(ozon_seller=None, ozon_performance=None, wb=None),
        data_dir=data_dir,
        unit_cost="85",
        target_margin="60",
        period_days=15,
        run_id="ozon_pricing_margin_test",
        today=date(2026, 6, 30),
        adapter=FakeOzonAdapter(),  # type: ignore[arg-type]
    )

    metrics = result["metrics"]
    assert result["overall_status"] == "ok"
    assert metrics["buyout_units"] == 2
    assert metrics["physical_pieces"] == 3
    assert metrics["total_expenses"] == 680.0
    assert metrics["expense_per_sold_product"] == 340.0
    assert metrics["expense_per_physical_item"] == 226.67
    assert metrics["fixed_logistics_per_sold_product"] == 100.0
    assert metrics["historical_variable_rate_pct"] == 40.0
    assert metrics["model_variable_rate_pct"] == 44.0

    ladder = {row["pack_qty"]: row for row in result["price_ladder"]}
    assert ladder[1]["minimum_price"] == 440.0
    assert ladder[1]["discounted_price"] == 550.0
    assert ladder[1]["base_price"] == 1100.0
    assert ladder[2]["minimum_price"] == 700.0
    assert ladder[2]["discounted_price"] == 880.0
    assert ladder[2]["base_price"] == 1760.0
    assert Path(result["artifacts"]["report"]).exists()
    assert Path(result["artifacts"]["markdown"]).exists()


def test_ozon_pricing_margin_blocks_ladder_without_completed_sales(tmp_path: Path) -> None:
    class EmptyAdapter(FakeOzonAdapter):
        def fetch_finance_transactions(self, *, date_from: str, date_to: str):  # type: ignore[no-untyped-def]
            return []

    data_dir = tmp_path / "data"
    _write_catalog(data_dir)
    result = run_ozon_pricing_margin(
        credentials=AppCredentials(ozon_seller=None, ozon_performance=None, wb=None),
        data_dir=data_dir,
        unit_cost=85,
        target_margin=60,
        period_days=15,
        today=date(2026, 6, 30),
        adapter=EmptyAdapter(),  # type: ignore[arg-type]
    )

    assert result["overall_status"] == "warning"
    assert result["metrics"]["calculation_ready"] is False
    assert result["price_ladder"] == []
    assert any("Недостаточно" in warning for warning in result["warnings"])
