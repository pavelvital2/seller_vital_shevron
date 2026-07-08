import json
from pathlib import Path

from seller_agent.tasks.approved_cards_apply import _run_post_apply_content_verify
from seller_agent.tasks.card_passport_promotion import (
    ensure_approved_passports_for_batch,
    run_promote_approved_card_passport,
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
