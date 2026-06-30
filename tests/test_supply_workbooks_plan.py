from __future__ import annotations

from pathlib import Path

from seller_agent.tasks.supply_workbooks_plan import run_supply_workbooks_plan


def test_supply_workbooks_plan_blocks_without_credentials_or_adapters(tmp_path: Path) -> None:
    result = run_supply_workbooks_plan(
        data_dir=tmp_path / "data",
        run_id="supply_workbooks_plan_test",
        target_days=30,
        cycle_days=5,
        daily_capacity=200,
    )

    assert result["overall_status"] == "blocked"
    assert result["blocked_reason"] == "missing_credentials_or_adapters"
    assert result["source_status"]["production_constraints"] == "ok"
    assert Path(result["artifacts"]["report"]).exists()
    assert Path(result["artifacts"]["summary"]).exists()
    assert Path(result["artifacts"]["run_manifest"]).exists()


def test_supply_workbooks_plan_uses_source_adapters_and_writes_workbooks(tmp_path: Path) -> None:
    _write_unified(tmp_path / "data" / "catalog" / "unified" / "products.csv")

    result = run_supply_workbooks_plan(
        data_dir=tmp_path / "data",
        run_id="supply_workbooks_plan_live_like",
        today=__import__("datetime").date(2026, 6, 29),
        ozon_weekly_physical=16,
        wb_target_physical=200,
        ozon_adapter=FakeOzonAdapter(),
        wb_stats_adapter=FakeWbStatsAdapter(),
        wb_content_adapter=FakeWbContentAdapter(),
        wb_supplies_adapter=FakeWbSuppliesAdapter(),
    )

    assert result["overall_status"] == "ok"
    assert result["summary"]["generated_files"] == 3
    assert result["summary"]["ozon_week1_physical"] == 16
    assert result["summary"]["wb_physical"] == 200
    assert Path(result["artifacts"]["ozon_week1_xlsx"]).exists()
    assert Path(result["artifacts"]["ozon_week2_xlsx"]).exists()
    assert Path(result["artifacts"]["wb_xlsx"]).exists()
    assert Path(result["artifacts"]["report"]).read_text(encoding="utf-8").startswith("# Файлы в работу")


def _write_unified(path: Path) -> None:
    path.parent.mkdir(parents=True)
    path.write_text(
        "\n".join(
            [
                "internal_product_id,internal_sku,product_name,product_group,pack_qty,cost_total,cost_per_unit,ozon_offer_id,ozon_product_id,ozon_sku,wb_vendor_code,wb_nm_id,mapping_status,active_ozon,active_wb",
                "chev_test,chev_test,Шеврон тестовый,chev,1,85,85,ozon001,1001,9001,wb001,7001,confirmed,true,true",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


class FakeOzonAdapter:
    def fetch_product_list(self, visibility: str = "ALL"):
        return [{"offer_id": "ozon001", "product_id": "1001", "sku": "9001"}]

    def fetch_product_info(self, product_ids):
        return [{"offer_id": "ozon001", "product_id": "1001", "sku": "9001", "name": "Шеврон тестовый"}]

    def fetch_product_stocks(self, product_ids):
        return [{"offer_id": "ozon001", "stocks": [{"type": "fbo", "present": 1, "sku": "9001"}]}]

    def fetch_stock_on_warehouses(self):
        return [{"item_code": "ozon001", "warehouse_name": "Ростов", "promised_amount": 0}]

    def fetch_fbo_postings(self, *, since, to, status="", limit=100):
        return [
            {
                "status": "delivered",
                "financial_data": {"cluster_to": "Ростов"},
                "products": [{"offer_id": "ozon001", "quantity": 10, "name": "Шеврон тестовый"}],
            }
        ]

    def fetch_supply_order_ids(self, *, states, limit=100):
        return ["order1"]

    def fetch_supply_orders(self, order_ids, *, batch_size=50):
        return [
            {
                "order_id": "order1",
                "macrolocal_cluster_name": "Ростов",
                "supplies": [{"supply_id": "supply1", "bundle_id": "bundle1"}],
            }
        ]

    def fetch_supply_order_bundle(self, bundle_id, *, limit=1000):
        return [{"offer_id": "ozon001", "quantity": 2}]


class FakeWbStatsAdapter:
    def fetch_stocks_legacy(self, *, date_from):
        return [{"supplierArticle": "wb001", "quantity": 1, "barcode": "123", "subject": "Шеврон"}]

    def fetch_sales(self, *, date_from, flag=1):
        return [
            {
                "supplierArticle": "wb001",
                "oblastOkrugName": "Центральный федеральный округ",
                "regionName": "Москва",
                "subject": "Шеврон",
            }
            for _ in range(10)
        ]


class FakeWbContentAdapter:
    def fetch_cards(self, *, limit=100):
        return [
            {
                "vendorCode": "wb001",
                "nmID": 7001,
                "title": "Шеврон тестовый",
                "subjectName": "Шеврон",
                "sizes": [{"skus": ["123"]}],
            }
        ]


class FakeWbSuppliesAdapter:
    def fetch_supplies(self):
        return [{"supplyID": 1, "statusID": 3}]

    def fetch_supply(self, supply_id, *, is_preorder_id=False):
        return {"supplyID": supply_id, "warehouseName": "Центральный", "quantity": 2}

    def fetch_supply_goods(self, supply_id, *, is_preorder_id=False, limit=1000):
        return [{"vendorCode": "wb001", "barcode": "123", "quantity": 2, "readyForSaleQuantity": 0}]
