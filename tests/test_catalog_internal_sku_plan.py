from seller_agent.tasks.catalog_internal_sku_plan import (
    build_internal_sku_plan,
    infer_sku_parts,
)
from seller_agent.tasks.registry import get_task_definition


def test_infer_sku_parts_for_supported_chevron_title() -> None:
    parts = infer_sku_parts(
        {
            "product_name": 'Шеврон "МВД России", на спину, 270х85',
            "mapping_status": "ozon_only",
        }
    )

    assert parts.status == "auto_candidate"
    assert parts.product_prefix == "chev"
    assert parts.purpose_prefix == "back"
    assert parts.structure_prefix == "mvd"
    assert parts.content_prefix == "text"


def test_build_internal_sku_plan_uses_next_number_in_existing_scope() -> None:
    proposals, summary = build_internal_sku_plan(
        products=[
            {
                "internal_sku": "chev_back_mvd_text0001",
                "mapping_status": "confirmed",
                "product_name": "Existing",
            },
            {
                "internal_product_id": "ozon:back0004",
                "mapping_status": "ozon_only",
                "product_name": 'Шеврон "МВД России", на спину, 270х85',
                "ozon_offer_id": "back0004",
                "ozon_sku": "2423094446",
            },
        ]
    )

    assert summary["marketplace_only_products"] == 1
    assert summary["auto_candidate_rows"] == 1
    assert proposals[0].proposed_internal_sku == "chev_back_mvd_text0002"
    assert proposals[0].source_marketplace == "ozon"


def test_build_internal_sku_plan_sends_existing_title_match_to_review() -> None:
    proposals, summary = build_internal_sku_plan(
        products=[
            {
                "internal_sku": "chev_back_mvd_pict0001",
                "mapping_status": "confirmed",
                "product_name": 'Шеврон "МВД России", на спину, 270х85',
            },
            {
                "internal_product_id": "ozon:back0004",
                "mapping_status": "ozon_only",
                "product_name": 'Шеврон "МВД России", на спину, 270х85',
                "ozon_offer_id": "back0004",
            },
        ]
    )

    assert summary["needs_owner_review_rows"] == 1
    assert proposals[0].proposal_status == "needs_owner_review"
    assert "title_matches_confirmed_product" in proposals[0].notes


def test_build_internal_sku_plan_marks_unsupported_product_type() -> None:
    proposals, summary = build_internal_sku_plan(
        products=[
            {
                "internal_product_id": "wb:111001",
                "mapping_status": "wb_only",
                "product_name": "Головной убор форменный ФСИН, Росгвардия.",
                "wb_vendor_code": "111001",
                "wb_nm_id": "410018811",
            }
        ]
    )

    assert summary["unsupported_product_type_rows"] == 1
    assert proposals[0].proposal_status == "unsupported_product_type"
    assert proposals[0].proposed_internal_sku == ""


def test_callsign_does_not_get_raz_theme() -> None:
    proposals, summary = build_internal_sku_plan(
        products=[
            {
                "internal_product_id": "ozon:pzmh0008",
                "mapping_status": "ozon_only",
                "product_name": 'Шеврон "Позывной Волк", нагрудный, мох',
                "ozon_offer_id": "pzmh0008",
                "ozon_sku": "2408799099",
            }
        ]
    )

    assert summary["auto_candidate_rows"] == 1
    assert proposals[0].theme_prefix == ""
    assert proposals[0].purpose_prefix == "pz_ng"
    assert proposals[0].proposed_internal_sku == "chev_pz_ng_text0001"


def test_vdv_theme_has_priority_over_svo() -> None:
    parts = infer_sku_parts(
        {
            "product_name": "Шеврон СВО ВДВ разведка, на кепку 80х50 на липучке",
            "mapping_status": "ozon_only",
        }
    )

    assert parts.theme_prefix == "voisk"
    assert parts.purpose_prefix == "kp"


def test_task_registry_contains_internal_sku_plan() -> None:
    task = get_task_definition("plan-internal-skus")

    assert task["name"] == "catalog-internal-sku-plan"
    assert task["mode"] == "read_only"
    assert task["requires_mapping"] is True
