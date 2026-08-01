from __future__ import annotations

from seller_agent.tasks.card_audit_prevalidator import validate_card_audit


def _audit() -> dict:
    return {
        "identity": {
            "internal_sku": "chev_test_0001",
            "marketplace_presence": "ozon_wb",
            "ozon": {"offer_id": "old"},
            "wb": {"nm_id": "1"},
        },
        "proposed_final_card": {
            "canonical_title": "Шеврон на липучке тестовый, чёрный",
            "ozon_title": "Шеврон на липучке тестовый, чёрный",
            "wb_title": "Шеврон на липучке тестовый, чёрный",
            "canonical_description": "Товар.\n\nПреимущества.\n\nО производителе.",
            "description_blocks": ["Товар.", "Преимущества.", "О производителе."],
            "description_semantic_plan": {"primary": ["шеврон на липучке"]},
            "description_seo_coverage": {"covered": ["шеврон на липучке"]},
            "ozon_hashtags": [f"#шеврон_{index}" for index in range(1, 21)],
            "target_physical_params": {
                "product_size_mm": "80*95",
                "package_dimensions_ozon_mm": "100*100*20",
                "package_dimensions_wb_cm": "10*10*2",
                "pack_qty": 1,
            },
            "target_ozon_photo_set": [{"position": 1, "action": "keep_current"}],
            "target_wb_photo_set": [{"position": 1, "action": "keep_current"}],
        },
    }


def test_card_audit_prevalidator_accepts_current_package_contract() -> None:
    result = validate_card_audit(_audit())

    assert result["status"] == "ok"
    assert result["checks"]["description_blocks"] == 3
    assert result["checks"]["ozon_hashtags"] == 20


def test_card_audit_prevalidator_blocks_title_and_marketplace_media_drift() -> None:
    audit = _audit()
    proposed = audit["proposed_final_card"]
    proposed["wb_title"] = "Другое название"
    proposed.pop("target_wb_photo_set")
    proposed["target_marketplace_photo_set"] = [{"position": 1, "action": "replace"}]

    result = validate_card_audit(audit)

    assert result["status"] == "blocked"
    assert "title_mismatch:wb" in result["errors"]
    assert "wb_media_set_missing_for_media_change" in result["errors"]
