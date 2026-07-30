import json
from pathlib import Path

import pytest

from seller_agent.config import AppCredentials, OzonSellerCredentials, WbCredentials
from seller_agent.tasks.registry import get_task_definition
from seller_agent.tasks.seller_sku_update import (
    _build_wb_vendor_update_variant,
    _ozon_precheck,
    _update_local_layers,
    normalize_seller_sku_operations,
    run_seller_sku_update_apply,
    run_seller_sku_update_verify,
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


def test_ozon_precheck_treats_matching_new_offer_as_already_applied(
    tmp_path: Path,
) -> None:
    class FakeOzon:
        def fetch_product_attributes(self, offer_ids):
            if offer_ids == ["old_offer"]:
                return []
            return [{"id": 123, "offer_id": "new_offer"}]

    result = _ozon_precheck(
        ozon=FakeOzon(),
        operation={
            "internal_sku": "new_offer",
            "ozon": {
                "old_offer_id": "old_offer",
                "new_offer_id": "new_offer",
                "product_id": "123",
            },
        },
        run_dir=tmp_path,
        skip_api=False,
    )

    assert result["status"] == "already_applied"
    assert result["ready"] is True
    assert result["errors"] == []


def test_local_layer_update_keeps_mapping_sources_aligned(tmp_path: Path) -> None:
    mapping_dir = tmp_path / "catalog" / "mapping"
    unified_dir = tmp_path / "catalog" / "unified"
    mapping_dir.mkdir(parents=True)
    unified_dir.mkdir(parents=True)
    (mapping_dir / "ozon_wb_internal_sku_confirmed.csv").write_text(
        "\n".join(
            [
                "internal_sku,ozon_offer_id,ozon_product_id,wb_vendor_code,wb_nm_id,notes",
                "chev_kit2_test_0001,old_ozon,123,old_wb,456,confirmed",
            ]
        ),
        encoding="utf-8",
    )
    (unified_dir / "internal_sku_assignment_owner_review.csv").write_text(
        "\n".join(
            [
                "review_status,source_marketplace,source_id,source_secondary_id,current_internal_product_id,approved_internal_sku,notes",
                "owner_confirmed_internal_sku,ozon,old_ozon,123,ozon:old_ozon,chev_kit2_test_0001,approved",
                "owner_confirmed_internal_sku,wb,old_wb,456,wb:old_wb,chev_kit2_test_0001,approved",
            ]
        ),
        encoding="utf-8",
    )

    changed = _update_local_layers(
        data_dir=tmp_path,
        plan=[
            {
                "internal_sku": "chev_kit2_test_0001",
                "ozon": {
                    "new_offer_id": "chev_kit2_test_0001",
                    "product_id": "123",
                },
                "wb": {
                    "new_vendor_code": "chev_kit2_test_0001",
                    "nm_id": "456",
                },
            }
        ],
        run_id="seller_sku_update_apply_test",
    )

    mapping = (mapping_dir / "ozon_wb_internal_sku_confirmed.csv").read_text(encoding="utf-8")
    owner_review = (
        unified_dir / "internal_sku_assignment_owner_review.csv"
    ).read_text(encoding="utf-8")
    assert "chev_kit2_test_0001,chev_kit2_test_0001,123,chev_kit2_test_0001,456" in mapping
    assert "ozon,chev_kit2_test_0001,123,ozon:chev_kit2_test_0001" in owner_review
    assert "wb,chev_kit2_test_0001,456,wb:chev_kit2_test_0001" in owner_review
    assert changed["catalog/mapping/ozon_wb_internal_sku_confirmed.csv"] == 1
    assert changed["catalog/unified/internal_sku_assignment_owner_review.csv"] == 2


def test_apply_seller_sku_update_requires_owner_confirmation(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="explicit user confirmation"):
        run_seller_sku_update_apply(
            credentials=AppCredentials(ozon_seller=None, ozon_performance=None, wb=None),
            data_dir=tmp_path,
            confirmed_by_user=False,
        )


def test_verify_seller_sku_update_reads_native_ids(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    plan_dir = tmp_path / "runs" / "2026-07-14" / "seller_sku_update_plan_test"
    plan_dir.mkdir(parents=True)
    (plan_dir / "seller_sku_update_plan.json").write_text(
        json.dumps(
            [
                {
                    "internal_sku": "chev_test_0001",
                    "ready": True,
                    "ozon": {
                        "old_offer_id": "old_ozon",
                        "new_offer_id": "chev_test_0001",
                        "product_id": "123",
                    },
                    "wb": {
                        "old_vendor_code": "old_wb",
                        "new_vendor_code": "chev_test_0001",
                        "nm_id": "456",
                    },
                }
            ]
        ),
        encoding="utf-8",
    )

    class FakeOzon:
        def __init__(self, credentials):
            self.credentials = credentials

        def fetch_product_info(self, product_ids):
            return [{"id": 123, "offer_id": "chev_test_0001"}]

        def update_offer_ids(self, payload):
            raise AssertionError("verify must not update offer ids")

    class FakeWb:
        def __init__(self, credentials):
            self.credentials = credentials

        def find_cards_by_vendor_codes(self, vendor_codes):
            return {"chev_test_0001": {"vendorCode": "chev_test_0001", "nmID": 456}}

        def fetch_card_errors(self, limit=100):
            return {"data": []}

        def update_cards(self, payload):
            raise AssertionError("verify must not update cards")

    monkeypatch.setattr("seller_agent.tasks.seller_sku_update.OzonSellerAdapter", FakeOzon)
    monkeypatch.setattr("seller_agent.tasks.seller_sku_update.WbContentAdapter", FakeWb)

    result = run_seller_sku_update_verify(
        credentials=AppCredentials(OzonSellerCredentials("id", "key"), None, WbCredentials("token")),
        data_dir=tmp_path,
        plan_run_id="seller_sku_update_plan_test",
        run_id="seller_sku_update_verify_test",
    )

    assert result["overall_status"] == "ok"
    assert result["ozon_verified_rows"] == 1
    assert result["wb_verified_rows"] == 1


def test_task_registry_contains_seller_sku_update_commands() -> None:
    plan = get_task_definition("plan-seller-sku-update")
    apply = get_task_definition("apply-seller-sku-update")
    verify = get_task_definition("verify-seller-sku-update")

    assert plan["name"] == "seller-sku-update-plan"
    assert plan["mode"] == "dry_run"
    assert plan["requires_mapping"] is True
    assert apply["name"] == "seller-sku-update-apply"
    assert apply["mode"] == "apply"
    assert apply["requires_confirmation"] is True
    assert apply["verify_task"] == "seller-sku-update-verify"
    assert verify["mode"] == "verify"
