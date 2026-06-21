from decimal import Decimal

from seller_agent.tasks.catalog_unified import build_unified_products, parse_pack_qty
from seller_agent.tasks.registry import get_task_definition


def test_parse_pack_qty_defaults_to_one_and_reads_kit_prefix() -> None:
    assert parse_pack_qty("chev_nr_svo_text0001") == 1
    assert parse_pack_qty("chev_kit2_pz_text0001") == 2
    assert parse_pack_qty("nash_kit4_mvd_pict0001") == 4


def test_build_unified_products_combines_confirmed_mapping_and_marketplace_only_rows() -> None:
    products, issues, summary = build_unified_products(
        ozon_rows=[
            {
                "offer_id": "oz-1",
                "product_id": "101",
                "sku": "901",
                "title": "Ozon mapped",
                "status": "visible",
            },
            {
                "offer_id": "oz-only",
                "product_id": "102",
                "sku": "902",
                "title": "Only Ozon",
                "status": "visible",
            },
        ],
        wb_rows=[
            {"vendor_code": "wb-1", "nm_id": "201", "title": "WB mapped", "status": "present"},
            {"vendor_code": "wb-only", "nm_id": "202", "title": "Only WB", "status": "present"},
        ],
        mapping_rows=[
            {
                "review_status": "confirmed",
                "internal_sku": "chev_kit2_pz_text0001",
                "product_name": "Mapped product",
                "ozon_offer_id": "oz-1",
                "ozon_product_id": "101",
                "ozon_sku": "901",
                "wb_vendor_code": "wb-1",
                "wb_nm_id": "201",
            }
        ],
        unit_cost_rub=Decimal("85"),
    )

    by_status = {product.mapping_status: product for product in products}
    assert issues == []
    assert summary["confirmed_products"] == 1
    assert summary["ozon_only_products"] == 1
    assert summary["wb_only_products"] == 1
    assert by_status["confirmed"].internal_product_id == "chev_kit2_pz_text0001"
    assert by_status["confirmed"].pack_qty == "2"
    assert by_status["confirmed"].cost_total == "170"
    assert by_status["confirmed"].active_ozon == "true"
    assert by_status["confirmed"].active_wb == "true"
    assert by_status["ozon_only"].active_wb == "false"
    assert by_status["wb_only"].active_ozon == "false"


def test_build_unified_products_reports_duplicate_confirmed_mapping_values() -> None:
    _products, issues, summary = build_unified_products(
        ozon_rows=[{"offer_id": "oz-1", "title": "Ozon"}],
        wb_rows=[{"vendor_code": "wb-1", "title": "WB"}],
        mapping_rows=[
            {
                "review_status": "confirmed",
                "internal_sku": "chev_nr_svo_text0001",
                "ozon_offer_id": "oz-1",
                "wb_vendor_code": "wb-1",
            },
            {
                "review_status": "confirmed",
                "internal_sku": "chev_nr_svo_text0001",
                "ozon_offer_id": "oz-1",
                "wb_vendor_code": "wb-1",
            },
        ],
    )

    duplicate_fields = {issue["field"] for issue in issues if issue["kind"] == "duplicate_mapping_value"}
    assert duplicate_fields == {"internal_sku", "ozon_offer_id", "wb_vendor_code"}
    assert summary["issue_count"] == 3


def test_task_registry_contains_unified_catalog_builder() -> None:
    task = get_task_definition("build-unified-catalog")

    assert task["name"] == "catalog-build-unified"
    assert task["mode"] == "read_only"
    assert task["requires_mapping"] is True
