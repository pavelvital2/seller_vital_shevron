from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from seller_agent.tasks.ozon_stock_supply_monitor import run_ozon_stock_supply_monitor


class FakeOzonAdapter:
    def fetch_product_list(self, *, visibility: str):  # type: ignore[no-untyped-def]
        return [
            {"product_id": 10, "offer_id": "single"},
            {"product_id": 20, "offer_id": "kit"},
        ]

    def fetch_product_info(self, product_ids):  # type: ignore[no-untyped-def]
        return [
            {"product_id": 10, "offer_id": "single", "sku": 100, "name": "Шеврон"},
            {"product_id": 20, "offer_id": "kit", "sku": 200, "name": "Комплект 2 шт."},
        ]

    def fetch_product_stocks(self, product_ids):  # type: ignore[no-untyped-def]
        return [
            {
                "product_id": 10,
                "offer_id": "single",
                "stocks": [
                    {"type": "fbo", "present": 5, "reserved": 1, "sku": 100},
                    {"type": "fbs", "present": 99, "reserved": 0, "sku": 100},
                ],
            },
            {
                "product_id": 20,
                "offer_id": "kit",
                "stocks": [{"source": "fbo", "present": 3, "reserved": 0, "sku": 200}],
            },
        ]

    def fetch_stock_on_warehouses(self):  # type: ignore[no-untyped-def]
        return [
            {
                "sku": 100,
                "warehouse_name": "ТВЕРЬ_РФЦ",
                "item_code": "single",
                "item_name": "Шеврон",
                "free_to_sell_amount": 4,
                "reserved_amount": 1,
                "promised_amount": 0,
            },
            {
                "sku": 200,
                "warehouse_name": "СПБ_БУГРЫ_РФЦ",
                "item_code": "kit",
                "item_name": "Комплект 2 шт.",
                "free_to_sell_amount": 3,
                "reserved_amount": 0,
                "promised_amount": 2,
            },
        ]

    def fetch_supply_order_ids(self, *, states):  # type: ignore[no-untyped-def]
        assert "READY_TO_SUPPLY" in states
        return ["1", "2"]

    def fetch_supply_orders(self, order_ids):  # type: ignore[no-untyped-def]
        return [
            {
                "order_id": 1,
                "order_number": "10001",
                "created_date": "2026-07-18T10:00:00Z",
                "state": "READY_TO_SUPPLY",
                "order_tags": {"is_virtual": False},
                "drop_off_warehouse": {"name": "ЯРОСЛАВЛЬ_КРОССДОК"},
                "supplies": [
                    {
                        "supply_id": 101,
                        "state": "READY_TO_SUPPLY",
                        "bundle_id": "bundle-1",
                        "storage_warehouse": {"name": "ТВЕРЬ_РФЦ"},
                    }
                ],
            },
            {
                "order_id": 2,
                "order_number": "10002",
                "state": "READY_TO_SUPPLY",
                "order_tags": {"is_virtual": True, "original_supply_id": 101},
                "supplies": [{"supply_id": 102, "bundle_id": "bundle-2"}],
            },
        ]

    def fetch_supply_order_bundle(self, bundle_id):  # type: ignore[no-untyped-def]
        return [
            {"offer_id": "single", "sku": 100, "product_id": 10, "quantity": 3},
            {"offer_id": "kit", "sku": 200, "product_id": 20, "quantity": 2},
        ]

    def fetch_supply_order_details(self, supply_id):  # type: ignore[no-untyped-def]
        raise AssertionError("Bundle is already present")


def test_ozon_monitor_separates_general_warehouse_and_supply_quantities(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    catalog = data_dir / "catalog" / "unified" / "products.csv"
    catalog.parent.mkdir(parents=True)
    catalog.write_text(
        "internal_sku,pack_qty,ozon_offer_id,ozon_product_id,ozon_sku\n"
        "single,1,single,10,100\n"
        "kit,2,kit,20,200\n",
        encoding="utf-8",
    )

    result = run_ozon_stock_supply_monitor(
        data_dir=data_dir,
        run_id="ozon_stock_supply_monitor_test",
        adapter=FakeOzonAdapter(),  # type: ignore[arg-type]
        now=datetime(2026, 7, 18, 12, 0, tzinfo=ZoneInfo("Europe/Moscow")),
    )

    general = result["metrics"]["general_fbo"]
    warehouses = result["metrics"]["warehouses"]
    supplies = result["metrics"]["supplies"]
    assert result["overall_status"] == "warning"
    assert general["present"] == 8
    assert general["physical_present"] == 11
    assert warehouses["free_to_sell"] == 7
    assert warehouses["physical_free_to_sell"] == 10
    assert warehouses["promised"] == 2
    assert supplies["orders"] == 1
    assert supplies["supplies"] == 1
    assert supplies["quantity"] == 5
    assert supplies["physical_quantity"] == 7
    assert supplies["confirmed_inbound_quantity"] == 5
    assert supplies["virtual_orders"] == 1
    assert result["metrics"]["reconciliation"]["warehouse_accounted"] == 8
    assert result["metrics"]["reconciliation"]["difference"] == 0
    assert Path(result["artifacts"]["report"]).exists()
    assert Path(result["artifacts"]["warehouse_csv"]).exists()
    assert Path(result["artifacts"]["supplies_csv"]).exists()
    report = Path(result["artifacts"]["report"]).read_text(encoding="utf-8")
    assert "Общий и складской источники не складываются" in report
    assert "Активные поставки не прибавляются" in report
    assert "Готова к отгрузке" in report
