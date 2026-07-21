from __future__ import annotations

from datetime import date
from decimal import Decimal
import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/analytics/ozon_cpc_efficiency_report.py"
SPEC = importlib.util.spec_from_file_location("ozon_cpc_efficiency_report", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_normalize_and_aggregate_sku_metrics() -> None:
    report = {
        "20233460": {
            "report": {
                "rows": [
                    {
                        "date": "18.07.2026",
                        "sku": "123",
                        "title": "Тестовый шеврон",
                        "views": "100",
                        "clicks": "10",
                        "toCart": "3",
                        "orders": "2",
                        "moneySpent": "20,00",
                        "ordersMoney": "500,00",
                        "avgBid": "2,00",
                        "product_gmv": "500,00",
                    }
                ]
            }
        }
    }
    rows = MODULE.normalize_rows(
        report,
        {"20233460": {"title": "Все кроме позывных"}},
    )
    totals = MODULE.metrics(rows)
    assert totals["ctr_percent"] == Decimal("10.00")
    assert totals["avg_cpc"] == Decimal("2.00")
    assert totals["cpa"] == Decimal("10.00")
    assert totals["drr_percent"] == Decimal("4.00")

    by_sku = MODULE.aggregate_skus(
        rows,
        date_from=date(2026, 7, 4),
        date_to=date(2026, 7, 18),
        current_products={"123": {"bid": "2300000"}},
        portfolio={"123": {"recommendation_code": "recovery_a", "stock": "8"}},
    )
    assert len(by_sku) == 1
    assert by_sku[0]["current_bid"] == Decimal("2.30")
    assert by_sku[0]["portfolio_code"] == "recovery_a"


def test_metrics_recompute_ratios_from_sums() -> None:
    rows = [
        {
            "views": 100,
            "clicks": 10,
            "to_cart": 1,
            "orders": 1,
            "spend": Decimal("10"),
            "orders_money": Decimal("100"),
        },
        {
            "views": 300,
            "clicks": 30,
            "to_cart": 6,
            "orders": 3,
            "spend": Decimal("90"),
            "orders_money": Decimal("900"),
        },
    ]
    totals = MODULE.metrics(rows)
    assert totals["ctr_percent"] == Decimal("10.00")
    assert totals["avg_cpc"] == Decimal("2.50")
    assert totals["drr_percent"] == Decimal("10.00")
