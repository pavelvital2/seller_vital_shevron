import json
from pathlib import Path

from seller_agent.tasks.approved_cards_apply import _run_post_apply_content_verify
from seller_agent.tasks.card_passport_promotion import (
    ensure_approved_passports_for_batch,
    run_promote_approved_card_passport,
    validate_wb_departmental_media_policy,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _minimal_owner_approved_audit(sku: str = "chev_nr_oborg_pict0001") -> dict:
    return {
        "identity": {
            "internal_sku": sku,
            "internal_product_id": sku,
            "marketplace_presence": "ozon_wb",
            "ozon": {"offer_id": "old_ozon", "product_id": "111", "sku": "222"},
            "wb": {"vendor_code": "old_wb", "nm_id": "333"},
        },
        "owner_review": {
            "status": "owner_approved_pending_batch_apply",
            "approved_at": "2026-06-29T19:56:18+03:00",
            "submitted_html_path": "data/catalog/card_audits/batch/card/review.html",
            "corrections": [{"summary": "owner approved"}],
        },
        "proposed_final_card": {
            "canonical_title": "Шеврон на липучке Русская народная дружина",
            "canonical_description": "Описание товара.\n\nМатериалы и качество.\n\nVital Shevron.",
            "description_blocks": ["Описание товара.", "Материалы и качество.", "Vital Shevron."],
            "color": ["черный", "белый"],
            "color_name": "Русская народная дружина",
            "material": "Габардин",
            "composition": ["полиэстер", "нейлон"],
            "ozon_model_name": "ОО",
            "ozon_hashtags": "#шеврон, #шеврон_на_липучке",
            "wb_tags": ["шеврон", "шеврон на липучке"],
            "target_physical_params": {
                "product_size_mm": "75*85",
                "ozon_package_mm": "100*100*10",
                "wb_package_cm": "10*10*1",
                "weight_g": 10,
                "pack_qty": 1,
            },
            "target_marketplace_photo_set": [{"position": 1, "role": "главная", "source": "Ozon 1"}],
        },
        "media": {
            "photos": [
                {
                    "marketplace": "ozon",
                    "position": 1,
                    "source_url": "https://example.test/ozon-1.jpg",
                }
            ]
        },
    }


def test_promote_approved_card_passport_writes_layer3_passport(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    audit_path = data_dir / "catalog" / "card_audits" / "batch" / "card" / "audit.json"
    _write_json(audit_path, _minimal_owner_approved_audit())

    result = run_promote_approved_card_passport(
        data_dir=data_dir,
        internal_skus=["chev_nr_oborg_pict0001"],
        run_id="promote_test",
        write=True,
    )

    assert result["overall_status"] == "ok"
    passport_path = data_dir / "catalog" / "master_passport" / "approved" / "chev_nr_oborg_pict0001.json"
    passport = json.loads(passport_path.read_text(encoding="utf-8"))
    assert passport["identity"]["ozon_offer_id"] == "old_ozon"
    assert passport["identity"]["wb_vendor_code"] == "old_wb"
    assert passport["content"]["canonical_title"] == "Шеврон на липучке Русская народная дружина"
    assert passport["physical"]["package_dimensions_wb_cm"] == "10*10*1 см"
    assert passport["media"]["target_assets"][0]["url"] == "https://example.test/ozon-1.jpg"
    assert passport["seo"]["ozon_hashtags"] == ["#шеврон", "#шеврон_на_липучке"]
    assert passport["seo"]["wb_tags"] == ["шеврон", "шеврон на липучке"]


def test_promote_supports_wb_camel_case_identity_contract(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    audit = _minimal_owner_approved_audit("chev_kit2_nr_rg_pict0006")
    audit["identity"]["wb"] = {
        "vendorCode": "rosgkit20009",
        "nmID": 707892601,
        "imtID": 723653877,
    }
    audit_path = data_dir / "catalog" / "card_audits" / "batch" / "card" / "audit.json"
    _write_json(audit_path, audit)

    result = run_promote_approved_card_passport(
        data_dir=data_dir,
        audit_paths=[audit_path],
        run_id="promote_wb_camel_case_identity_test",
        write=True,
    )

    assert result["overall_status"] == "ok"
    passport_path = data_dir / "catalog" / "master_passport" / "approved" / "chev_kit2_nr_rg_pict0006.json"
    passport = json.loads(passport_path.read_text(encoding="utf-8"))
    assert passport["identity"]["wb_vendor_code"] == "rosgkit20009"
    assert passport["identity"]["wb_nm_id"] == "707892601"


def test_promote_preserves_hashtag_list_and_ozon_create_intent(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    audit = _minimal_owner_approved_audit("chev_kp_prikol_text0014")
    audit["proposed_final_card"]["ozon_hashtags"] = [
        "#шеврон_прикол",
        "#прикольная_нашивка",
    ]
    audit["proposed_final_card"]["future_ozon_create"] = {
        "status": "owner_approved_for_passport_and_future_apply"
    }
    audit["seo"] = {
        "target_query_clusters": [
            {"query": "шеврон прикол"},
            {"query": "шеврон на липучке"},
        ],
        "confirmed_query_rows": [{"query": "шеврон прикол"}],
    }
    audit_path = data_dir / "catalog" / "card_audits" / "batch" / "card" / "audit.json"
    _write_json(audit_path, audit)

    result = run_promote_approved_card_passport(
        data_dir=data_dir,
        audit_paths=[audit_path],
        run_id="promote_ozon_create_test",
        write=True,
    )

    assert result["overall_status"] == "ok"
    passport_path = data_dir / "catalog" / "master_passport" / "approved" / "chev_kp_prikol_text0014.json"
    passport = json.loads(passport_path.read_text(encoding="utf-8"))
    assert passport["seo"]["ozon_hashtags"] == ["#шеврон_прикол", "#прикольная_нашивка"]
    assert passport["seo"]["search_queries"] == ["шеврон прикол", "шеврон на липучке"]
    assert "ozon_card_create" in passport["safety"]["dangerous_actions"]


def test_promote_preserves_separate_ozon_and_wb_photo_sets(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    audit = _minimal_owner_approved_audit("chev_kit2_nr_mvd_pict0004")
    audit["media"] = {
        "target_marketplace_photo_set": [
            {"position": 1, "source": "WB 1", "source_url": "https://example.test/wb1.webp"}
        ],
        "target_ozon_photo_set": [
            {"position": 1, "source": "Ozon 1", "source_url": "https://example.test/ozon1.jpg"},
            {"position": 2, "source": "Ozon 2", "source_url": "https://example.test/ozon2.jpg"},
        ],
        "target_wb_photo_set": [
            {"position": 1, "source": "WB 1", "source_url": "https://example.test/wb1.webp"}
        ],
    }
    audit["proposed_final_card"]["target_physical_params"] = {
        "product_size_mm": "75*100 мм",
        "ozon_package_mm": "100*100*20 мм",
        "wb_package_cm": "10*10*2 см",
        "weight_g": 20,
        "item_weight_g": 10,
        "pack_qty": 2,
    }
    audit_path = data_dir / "catalog" / "card_audits" / "batch" / "card" / "audit.json"
    _write_json(audit_path, audit)

    result = run_promote_approved_card_passport(
        data_dir=data_dir,
        audit_paths=[audit_path],
        run_id="promote_separate_media_test",
        write=True,
    )

    assert result["overall_status"] == "ok"
    passport_path = data_dir / "catalog" / "master_passport" / "approved" / "chev_kit2_nr_mvd_pict0004.json"
    passport = json.loads(passport_path.read_text(encoding="utf-8"))
    assert len(passport["media"]["target_ozon_photo_set"]) == 2
    assert len(passport["media"]["target_wb_photo_set"]) == 1
    assert passport["media"]["target_wb_photo_set"][0]["source"] == "WB 1"
    assert passport["physical"]["item_weight_g"] == 10
    assert passport["physical"]["package_weight_g"] == 20


def test_wb_departmental_media_policy_allows_neutral_wearing_slide() -> None:
    passport = {
        "media": {
            "target_wb_photo_set": [
                {"position": 1, "source": "WB 1"},
                {"position": 5, "source": "Ozon 5", "role": "варианты ношения"},
            ],
            "wb_departmental_symbol_policy": {
                "media_apply_status": "allowed_verified",
                "rule": "Symbol-bearing WB images are protected; neutral images may transfer.",
            },
        }
    }

    result = validate_wb_departmental_media_policy(passport)

    assert result["status"] == "ok"
    assert result["media_apply_status"] == "allowed_verified"


def test_wb_departmental_media_policy_blocks_unprotected_symbol_assets() -> None:
    passport = {
        "media": {
            "target_wb_photo_set": [{"position": 1, "source": "WB 1"}],
            "wb_departmental_symbol_policy": {
                "media_apply_status": "blocked_pending_watermarked_assets",
                "rule": "Symbol-bearing WB images require watermark or retouch.",
            },
        }
    }

    result = validate_wb_departmental_media_policy(passport)

    assert result["status"] == "blocked"
    assert result["errors"] == ["wb_media_update_blocked_pending_watermarked_assets"]


def test_promote_normalizes_description_alias_and_item_weight_each(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    audit = _minimal_owner_approved_audit("chev_kit2_nr_rg_pict0007")
    proposed = audit["proposed_final_card"]
    canonical_description = proposed["canonical_description"]
    proposed["ozon_description"] = "same_as_canonical_description"
    proposed["wb_description"] = "same_as_canonical_description"
    proposed["target_physical_params"] = {
        "product_size_mm_each": "75*100",
        "package_size_mm": "100*100*20",
        "wb_package_cm": "10*10*2",
        "item_weight_g_each": 10,
        "package_weight_g": 20,
        "pack_qty": 2,
    }
    audit_path = data_dir / "catalog" / "card_audits" / "batch" / "card" / "audit.json"
    _write_json(audit_path, audit)

    result = run_promote_approved_card_passport(
        data_dir=data_dir,
        audit_paths=[audit_path],
        run_id="promote_description_alias_test",
        write=True,
    )

    assert result["overall_status"] == "ok"
    passport_path = data_dir / "catalog" / "master_passport" / "approved" / "chev_kit2_nr_rg_pict0007.json"
    passport = json.loads(passport_path.read_text(encoding="utf-8"))
    assert passport["content"]["ozon_description"] == canonical_description
    assert passport["content"]["wb_description"] == canonical_description
    assert passport["physical"]["item_weight_g"] == 10
    assert passport["physical"]["package_weight_g"] == 20


def test_promote_supports_fresh_auditor_physical_contract(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    audit = _minimal_owner_approved_audit("chev_kit2_nr_mvd_pict0006")
    proposed = audit["proposed_final_card"]
    proposed.pop("target_physical_params")
    proposed.pop("color")
    proposed.pop("color_name")
    proposed.pop("material")
    proposed.pop("composition")
    proposed.pop("ozon_model_name")
    proposed["physical"] = {
        "product_size_mm": "75*100 мм",
        "physical_item_count": 2,
        "item_weight_g": 10,
        "package_weight_g": 20,
        "material": "Габардин",
        "composition": ["полиэстер", "нейлон"],
        "colors": ["оливковый", "чёрный"],
        "color_name": "Полиция МВД ГИБДД/ДПС, олива",
    }
    proposed["ozon_attributes"] = {
        "package_dimensions_mm": "100*100*20 мм",
        "package_weight_g": 20,
        "quantity_in_package": 2,
        "units_per_product": 2,
        "model": "МВД",
        "hashtags": ["#комплект_шевронов_мвд", "#шеврон_на_липучке_мвд"],
    }
    proposed["ozon_hashtags"] = "30 релевантных хештегов из proposed_final_card.ozon_attributes.hashtags"
    proposed["wb_attributes"] = {"package_dimensions_cm": "10*10*2 см"}
    audit["media"]["target_ozon_photo_set"] = [1, 2]
    audit["media"]["target_wb_photo_set"] = [1]
    audit_path = data_dir / "catalog" / "card_audits" / "batch" / "card" / "audit.json"
    _write_json(audit_path, audit)

    result = run_promote_approved_card_passport(
        data_dir=data_dir,
        audit_paths=[audit_path],
        run_id="promote_fresh_contract_test",
        write=True,
    )

    assert result["overall_status"] == "ok"
    passport_path = data_dir / "catalog" / "master_passport" / "approved" / "chev_kit2_nr_mvd_pict0006.json"
    passport = json.loads(passport_path.read_text(encoding="utf-8"))
    assert passport["physical"]["product_size_mm"] == "75*100 мм"
    assert passport["physical"]["package_dimensions_ozon_mm"] == "100*100*20 мм"
    assert passport["physical"]["package_dimensions_wb_cm"] == "10*10*2 см"
    assert passport["physical"]["package_weight_g"] == 20
    assert passport["physical"]["pack_qty"] == 2
    ozon_attributes = {row["field"]: row["value"] for row in passport["ozon"]["attributes"]}
    assert ozon_attributes["Цвет товара"] == "оливковый, чёрный"
    assert ozon_attributes["Название цвета"] == "Полиция МВД ГИБДД/ДПС, олива"
    assert passport["seo"]["ozon_hashtags"] == [
        "#комплект_шевронов_мвд",
        "#шеврон_на_липучке_мвд",
    ]
    assert passport["grouping"]["target_group_key"] == "МВД"
    assert passport["media"]["target_ozon_photo_set"] == [
        {"position": 1, "source": "OZON 1"},
        {"position": 2, "source": "OZON 2"},
    ]
    assert passport["media"]["target_wb_photo_set"] == [{"position": 1, "source": "WB 1"}]


def test_promote_supports_physical_parameters_contract(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    audit = _minimal_owner_approved_audit("chev_kit2_nr_mvd_pict0005")
    proposed = audit["proposed_final_card"]
    proposed.pop("target_physical_params")
    proposed.pop("material")
    proposed.pop("composition")
    proposed.pop("ozon_model_name")
    proposed["physical_parameters"] = {
        "product_size_mm": "75*100 мм каждый",
        "pack_qty": 2,
        "item_weight_g": 10,
        "package_weight_g": 20,
        "package_size_ozon_mm": "100*100*20 мм",
        "package_size_wb_cm": "10*10*2 см",
        "material": "Габардин",
        "composition": "полиэстер, нейлон",
    }
    proposed["ozon_attributes"] = {"9048_model": "МВД"}
    audit_path = data_dir / "catalog" / "card_audits" / "batch" / "card" / "audit.json"
    _write_json(audit_path, audit)

    result = run_promote_approved_card_passport(
        data_dir=data_dir,
        audit_paths=[audit_path],
        run_id="promote_physical_parameters_test",
        write=True,
    )

    assert result["overall_status"] == "ok"
    passport_path = data_dir / "catalog" / "master_passport" / "approved" / "chev_kit2_nr_mvd_pict0005.json"
    passport = json.loads(passport_path.read_text(encoding="utf-8"))
    assert passport["physical"]["product_size_mm"] == "75*100 мм"
    assert passport["physical"]["package_dimensions_ozon_mm"] == "100*100*20 мм"
    assert passport["physical"]["package_dimensions_wb_cm"] == "10*10*2 см"
    assert passport["physical"]["item_weight_g"] == 10
    assert passport["physical"]["package_weight_g"] == 20
    assert passport["physical"]["pack_qty"] == 2
    assert passport["materials"]["material"] == "Габардин"
    assert passport["materials"]["composition"] == ["полиэстер", "нейлон"]
    assert passport["grouping"]["target_group_key"] == "МВД"


def test_promote_supports_owner_review_full_wb_create_contract(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    audit = _minimal_owner_approved_audit("chev_back_fsin_text0005")
    audit["identity"]["marketplace_presence"] = "ozon_only"
    audit["identity"]["wb"] = None
    audit["owner_review"]["owner_corrections"] = [
        "Название цвета: Шеврон ФСИН на спину, синяя цифра"
    ]
    audit["owner_review"].pop("corrections")
    proposed = audit["proposed_final_card"]
    proposed.pop("target_physical_params")
    proposed["description_blocks"] = {
        "Описание товара": "Описание товара.",
        "Преимущества и характеристики товара": "Материалы и качество.",
        "О производителе": "Vital Shevron.",
    }
    proposed["physical_parameters"] = {
        "product_width_mm": 225,
        "product_height_mm": 70,
        "package_depth_mm": 300,
        "package_width_mm": 100,
        "package_height_mm": 10,
        "package_weight_g": 30,
        "pack_qty": 1,
    }
    proposed["color_name"] = "Шеврон ФСИН на спину, синяя цифра"
    proposed["wb_create"] = {
        "current_state": "card_absent",
        "subject_id": 2367,
        "vendor_code": "chev_back_fsin_text0005",
        "dimensions_cm": {"length": 30, "width": 10, "height": 1},
    }
    audit_path = data_dir / "catalog" / "card_audits" / "batch" / "card" / "audit.json"
    _write_json(audit_path, audit)

    result = run_promote_approved_card_passport(
        data_dir=data_dir,
        audit_paths=[audit_path],
        run_id="promote_full_wb_create_contract_test",
        write=True,
    )

    assert result["overall_status"] == "ok"
    passport_path = data_dir / "catalog" / "master_passport" / "approved" / "chev_back_fsin_text0005.json"
    passport = json.loads(passport_path.read_text(encoding="utf-8"))
    assert passport["physical"]["product_size_mm"] == "225*70 мм"
    assert passport["physical"]["package_dimensions_ozon_mm"] == "300*100*10 мм"
    assert passport["physical"]["package_dimensions_wb_cm"] == "30*10*1 см"
    assert passport["content"]["description_blocks"] == [
        "Описание товара.",
        "Материалы и качество.",
        "Vital Shevron.",
    ]
    assert passport["approval"]["owner_corrections"] == [
        "Название цвета: Шеврон ФСИН на спину, синяя цифра"
    ]
    assert passport["wb"]["create"]["subject_id"] == 2367
    assert "wb_card_create" in passport["safety"]["dangerous_actions"]


def test_promote_omits_wb_media_and_preserves_conditional_wb_constraints(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    audit = _minimal_owner_approved_audit("chev_nr_chvk_pict0003")
    proposed = audit["proposed_final_card"]
    proposed.pop("target_marketplace_photo_set")
    proposed["wb_characteristics"] = {
        "isAdult": True,
        "isAdult_apply_condition": "only_if_current_not_true",
        "barcode": {
            "current": None,
            "target": "keep_current_marketplace_value",
            "action": "do_not_change_or_update; omit_from_target_changes",
        },
    }
    audit["media"] = {
        "target_ozon_photo_set": "keep_current_5",
        "target_wb_photo_set": "keep_current_5; no_media_upload",
        "wb_departmental_symbol_policy": {
            "media_apply_status": "not_applicable",
            "reason": "Current marketplace photos stay unchanged.",
        },
    }
    audit_path = data_dir / "catalog" / "card_audits" / "batch" / "card" / "audit.json"
    _write_json(audit_path, audit)

    result = run_promote_approved_card_passport(
        data_dir=data_dir,
        audit_paths=[audit_path],
        run_id="promote_keep_wb_media_constraints_test",
        write=True,
    )

    assert result["overall_status"] == "ok"
    passport_path = data_dir / "catalog" / "master_passport" / "approved" / "chev_nr_chvk_pict0003.json"
    passport = json.loads(passport_path.read_text(encoding="utf-8"))
    assert "wb_media_update" not in passport["safety"]["dangerous_actions"]
    assert passport["media"]["target_wb_photo_set"] == []
    assert passport["wb"]["write_constraints"] == {
        "barcode": {
            "action": "do_not_change_or_update; omit_from_target_changes",
            "include_in_write_payload": False,
        },
        "isAdult": {
            "target": True,
            "apply_condition": "only_if_current_not_true",
        },
        "media": {
            "action": "keep_current",
            "include_in_write_payload": False,
        },
    }
    wb_attributes = {row["field"]: row["value"] for row in passport["wb"]["attributes"]}
    assert wb_attributes["18+ / isAdult"] == "true"
    assert validate_wb_departmental_media_policy(passport)["status"] == "ok"


def test_promote_omits_structured_keep_current_wb_photo_rows(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    audit = _minimal_owner_approved_audit("chev_nr_svo_pict0003")
    audit["owner_review"].pop("submitted_html_path")
    audit["owner_review"]["html_path"] = (
        "data/catalog/card_audits/batch/card/chev_nr_svo_pict0003.html"
    )
    proposed = audit["proposed_final_card"]
    proposed.pop("target_marketplace_photo_set")
    proposed["target_wb_photo_set"] = [
        {
            "position": position,
            "source": f"WB {position}",
            "source_url": f"https://example.invalid/wb/{position}.webp",
            "action": "keep_current_no_upload",
        }
        for position in range(1, 6)
    ]
    audit["media"] = {
        "wb_departmental_symbol_policy": {
            "media_apply_status": "no_media_update_required",
            "reason": "Current WB photos stay unchanged.",
        },
    }
    audit_path = data_dir / "catalog" / "card_audits" / "batch" / "card" / "audit.json"
    _write_json(audit_path, audit)

    result = run_promote_approved_card_passport(
        data_dir=data_dir,
        audit_paths=[audit_path],
        run_id="promote_structured_keep_wb_media_test",
        write=True,
    )

    assert result["overall_status"] == "ok"
    passport_path = data_dir / "catalog" / "master_passport" / "approved" / "chev_nr_svo_pict0003.json"
    passport = json.loads(passport_path.read_text(encoding="utf-8"))
    assert "wb_media_update" not in passport["safety"]["dangerous_actions"]
    assert passport["media"]["target_wb_photo_set"] == []
    assert passport["wb"]["write_constraints"]["media"] == {
        "action": "keep_current",
        "include_in_write_payload": False,
    }
    assert passport["approval"]["source_review_html"] == (
        "data/catalog/card_audits/batch/card/chev_nr_svo_pict0003.html"
    )
    assert validate_wb_departmental_media_policy(passport)["status"] == "ok"


def test_promote_supports_each_size_and_wb_dimensions_contract(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    audit = _minimal_owner_approved_audit("chev_kit2_nr_rg_pict0003")
    proposed = audit["proposed_final_card"]
    proposed.pop("target_physical_params")
    proposed["physical"] = {
        "product_size_mm_each": "75*100",
        "physical_item_count": 2,
        "item_weight_g": 10,
        "package_weight_g": 20,
        "package_size_mm": "100*100*20",
    }
    proposed["wb_characteristics"] = {
        "dimensions_cm": {
            "length": 10,
            "width": 10,
            "height": 2,
            "weight_brutto_kg": 0.02,
        }
    }
    audit_path = data_dir / "catalog" / "card_audits" / "batch" / "card" / "audit.json"
    _write_json(audit_path, audit)

    result = run_promote_approved_card_passport(
        data_dir=data_dir,
        audit_paths=[audit_path],
        run_id="promote_each_size_wb_dimensions_test",
        write=True,
    )

    assert result["overall_status"] == "ok"
    passport_path = data_dir / "catalog" / "master_passport" / "approved" / "chev_kit2_nr_rg_pict0003.json"
    passport = json.loads(passport_path.read_text(encoding="utf-8"))
    assert passport["physical"]["product_size_mm"] == "75*100 мм"
    assert passport["physical"]["package_dimensions_ozon_mm"] == "100*100*20 мм"
    assert passport["physical"]["package_dimensions_wb_cm"] == "10*10*2 см"
    assert passport["physical"]["item_weight_g"] == 10
    assert passport["physical"]["package_weight_g"] == 20
    assert passport["physical"]["pack_qty"] == 2


def test_ensure_approved_passports_for_batch_promotes_missing_only(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    audit_path = data_dir / "catalog" / "card_audits" / "batch" / "card" / "audit.json"
    _write_json(audit_path, _minimal_owner_approved_audit("chev_nr_oborg_pict0005"))

    result = ensure_approved_passports_for_batch(
        data_dir=data_dir,
        internal_skus=["chev_nr_oborg_pict0005"],
        base_run_id="apply_batch_test",
    )

    assert result["status"] == "ok"
    assert result["missing_skus"] == ["chev_nr_oborg_pict0005"]
    assert (data_dir / "catalog" / "master_passport" / "approved" / "chev_nr_oborg_pict0005.json").exists()


def test_post_apply_content_verify_skips_empty_input(tmp_path: Path) -> None:
    assert _run_post_apply_content_verify(credentials=None, data_dir=tmp_path, internal_skus=[], base_run_id="x")[
        "status"
    ] == "skipped"
