from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from seller_agent.tasks.wb_stock_supply_monitor import run_wb_stock_supply_monitor


class FakeAnalyticsAdapter:
    def fetch_wb_warehouse_stocks(self):  # type: ignore[no-untyped-def]
        return [
            {
                "nmId": 1001,
                "warehouseId": 1,
                "warehouseName": "Электросталь",
                "regionName": "Центральный",
                "quantity": 9,
                "inWayToClient": 3,
                "inWayFromClient": 401,
            },
            {
                "nmId": 1002,
                "warehouseId": 2,
                "warehouseName": "Коледино",
                "regionName": "Центральный",
                "quantity": 100,
                "inWayToClient": 2,
                "inWayFromClient": 1,
            },
        ]


class FakeSuppliesAdapter:
    def fetch_supplies(self):  # type: ignore[no-untyped-def]
        return [
            {"supplyID": 10, "statusID": 3, "createDate": "2026-07-13T10:00:00+03:00"},
            {"supplyID": 20, "statusID": 4, "createDate": "2026-07-13T11:00:00+03:00"},
            {"supplyID": 30, "statusID": 5, "createDate": "2026-07-10T11:00:00+03:00"},
        ]

    def fetch_supply(self, supply_id):  # type: ignore[no-untyped-def]
        if supply_id == 10:
            return {
                "statusID": 3,
                "warehouseName": "Шушары",
                "supplyDate": "2026-07-20T00:00:00+03:00",
                "quantity": 2,
            }
        return {
            "statusID": 4,
            "warehouseName": "Электросталь",
            "supplyDate": "2026-07-18T00:00:00+03:00",
            "quantity": 3,
            "acceptedQuantity": 2,
            "readyForSaleQuantity": 1,
            "unloadingQuantity": 1,
        }

    def fetch_supply_goods(self, supply_id):  # type: ignore[no-untyped-def]
        if supply_id == 10:
            return [{"nmID": 1001, "vendorCode": "kit", "quantity": 2}]
        return [{"nmID": 1002, "vendorCode": "pet", "quantity": 3, "acceptedQuantity": 2}]


def test_monitor_builds_stock_supply_report_and_keeps_statuses_separate(tmp_path: Path) -> None:
    catalog = tmp_path / "data" / "catalog" / "unified" / "products.csv"
    catalog.parent.mkdir(parents=True)
    catalog.write_text(
        "wb_nm_id,wb_vendor_code,internal_sku,pack_qty\n"
        "1001,kit,kit_internal,2\n"
        "1002,pet,pet_internal,1\n",
        encoding="utf-8",
    )

    result = run_wb_stock_supply_monitor(
        data_dir=tmp_path / "data",
        run_id="wb_stock_supply_monitor_test",
        analytics_adapter=FakeAnalyticsAdapter(),  # type: ignore[arg-type]
        supplies_adapter=FakeSuppliesAdapter(),  # type: ignore[arg-type]
        now=datetime(2026, 7, 18, 12, 0, tzinfo=ZoneInfo("Europe/Moscow")),
    )

    stocks = result["metrics"]["stocks"]
    supplies = result["metrics"]["supplies"]
    assert result["overall_status"] == "warning"
    assert stocks["quantity"] == 109
    assert stocks["physical_quantity"] == 118
    assert supplies["count"] == 2
    assert supplies["quantity"] == 5
    assert supplies["physical_quantity"] == 7
    assert supplies["by_status"]["3"]["quantity"] == 2
    assert supplies["by_status"]["4"]["quantity"] == 3
    assert any("нельзя считать возвратами" in row["message"] for row in result["anomalies"])
    assert Path(result["artifacts"]["report"]).exists()
    assert Path(result["artifacts"]["warehouses_csv"]).exists()
    assert Path(result["artifacts"]["supplies_csv"]).exists()
    assert Path(result["artifacts"]["manifest"]).exists()
    report = Path(result["artifacts"]["report"]).read_text(encoding="utf-8")
    assert "Отгрузка разрешена" in report
    assert "Идет приемка" in report
    assert "Остатки и активные поставки не складываются" in report
