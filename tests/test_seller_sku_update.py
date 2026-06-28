from pathlib import Path

import pytest

from seller_agent.config import AppCredentials
from seller_agent.tasks.registry import get_task_definition
from seller_agent.tasks.seller_sku_update import (
    _build_wb_vendor_update_variant,
    normalize_seller_sku_operations,
    run_seller_sku_update_apply,
)


def test_normalize_seller_sku_operations_from_products_csv(tmp_path: Path) -> None:
    products = tmp_path / "products.csv"
    products.write_text(
        "\n".join(
            [
                "internal_sku,ozon_offer_id,ozon_product_id,wb_vendor_code,wb_nm_id",
                "chev_test_0001,old_ozon,12345,old_wb,67890",
            ]
        ),
        encoding="utf-8",
    )

    operations = normalize_seller_sku_operations(
        input_path=None,
        internal_skus=["chev_test_0001"],
        products_path=products,
    )

    assert operations == [
        {
            "internal_sku": "chev_test_0001",
            "ozon": {
                "old_offer_id": "old_ozon",
                "new_offer_id": "chev_test_0001",
                "product_id": "12345",
            },
            "wb": {
                "old_vendor_code": "old_wb",
                "new_vendor_code": "chev_test_0001",
                "nm_id": "67890",
            },
        }
    ]


def test_normalize_seller_sku_operations_marks_already_ok(tmp_path: Path) -> None:
    products = tmp_path / "products.csv"
    products.write_text(
        "\n".join(
            [
                "internal_sku,ozon_offer_id,ozon_product_id,wb_vendor_code,wb_nm_id",
                "chev_test_0001,chev_test_0001,12345,chev_test_0001,67890",
            ]
        ),
        encoding="utf-8",
    )

    operations = normalize_seller_sku_operations(
        input_path=None,
        internal_skus=["chev_test_0001"],
        products_path=products,
    )

    assert operations == [{"internal_sku": "chev_test_0001", "status": "already_ok"}]


def test_build_wb_vendor_update_variant_preserves_card_fields() -> None:
    variant = _build_wb_vendor_update_variant(
        {
            "nmID": 123,
            "vendorCode": "old",
            "title": "Title",
            "description": "Description",
            "brand": "VitalEmb",
            "dimensions": {"length": 10, "width": 10, "height": 1},
            "characteristics": [{"id": 1, "value": ["Россия"]}],
            "sizes": [{"skus": ["2047000000000"], "techSize": "0"}],
        },
        "new_internal_sku",
    )

    assert variant["vendorCode"] == "new_internal_sku"
    assert variant["nmID"] == 123
    assert variant["title"] == "Title"
    assert variant["description"] == "Description"
    assert variant["dimensions"] == {"length": 10, "width": 10, "height": 1}
    assert variant["characteristics"] == [{"id": 1, "value": ["Россия"]}]
    assert variant["sizes"] == [{"skus": ["2047000000000"], "techSize": "0"}]


def test_apply_seller_sku_update_requires_owner_confirmation(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="explicit user confirmation"):
        run_seller_sku_update_apply(
            credentials=AppCredentials(ozon_seller=None, ozon_performance=None, wb=None),
            data_dir=tmp_path,
            confirmed_by_user=False,
        )


def test_task_registry_contains_seller_sku_update_commands() -> None:
    plan = get_task_definition("plan-seller-sku-update")
    apply = get_task_definition("apply-seller-sku-update")

    assert plan["name"] == "seller-sku-update-plan"
    assert plan["mode"] == "dry_run"
    assert plan["requires_mapping"] is True
    assert apply["name"] == "seller-sku-update-apply"
    assert apply["mode"] == "apply"
    assert apply["requires_confirmation"] is True
