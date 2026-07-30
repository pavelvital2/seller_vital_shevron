import json
from pathlib import Path

from seller_agent.config import AppCredentials, WbCredentials
from seller_agent.tasks.registry import get_task_definition
from seller_agent.tasks.wb_card_create_apply import (
    run_wb_card_create_apply,
    run_wb_card_create_verify,
)
from seller_agent.tasks.wb_card_create_plan import _build_owner_approved_plan_items, _plain_text


def test_plain_text_keeps_line_breaks_between_html_blocks() -> None:
    value = "Заголовок<br/><br/>Текст<ul><li>Первое</li><li>Второе</li></ul>"

    assert _plain_text(value) == "Заголовок\nТекст\nПервое\nВторое"


def test_plain_text_removes_wb_forbidden_emoji_symbols() -> None:
    value = "<ul><li>⚡ Мгновенный доступ</li><li>🧵 Усиленные швы</li></ul>"

    assert _plain_text(value) == "Мгновенный доступ\nУсиленные швы"


def test_build_owner_approved_wb_plan_item_from_layer3_passport(tmp_path: Path) -> None:
    approved_dir = tmp_path / "catalog" / "master_passport" / "approved"
    approved_dir.mkdir(parents=True)
    passport = {
        "identity": {
            "internal_sku": "chev_nr_bpla_pict0001",
            "ozon_offer_id": "pict0001",
        },
        "approval": {
            "status": "owner_approved",
            "approved_by": "owner_telegram_confirmation",
            "source_review_html": "review.html",
        },
        "content": {
            "wb_title": "Шеврон на липучке БПЛА Тест",
            "wb_description": "Описание товара\n\nМатериалы\n\nVital Shevron",
            "package_contents": "шеврон на липучке 1 шт.",
        },
        "physical": {
            "package_dimensions_wb_cm": "10*10*1 см",
            "package_weight_g": 10,
            "pack_qty": 1,
        },
        "materials": {"composition": ["полиэстер", "нейлон"]},
        "classification": {"tnved": "5810999000", "country_of_origin": "Россия"},
        "media": {
            "target_assets": [
                {"url": "https://example.test/1.jpg"},
                {"url": "https://example.test/2.jpg"},
            ]
        },
        "wb": {
            "attributes": [
                {"field": "Цвет", "value": "черный; белый"},
                {"field": "Вид декора для одежды", "value": "шеврон"},
                {"field": "Комплектация", "value": "шеврон на липучке 1 шт."},
                {"field": "Количество предметов", "value": "1 шт."},
            ]
        },
    }
    (approved_dir / "chev_nr_bpla_pict0001.json").write_text(
        json.dumps(passport, ensure_ascii=False),
        encoding="utf-8",
    )

    items = _build_owner_approved_plan_items(
        data_dir=tmp_path,
        internal_skus=["chev_nr_bpla_pict0001"],
        ozon_info_by_offer={"pict0001": {"old_price": "1100"}},
    )

    assert len(items) == 1
    item = items[0]
    variant = item["draft_variant"]
    assert item["master_sku"] == "chev_nr_bpla_pict0001"
    assert item["needs_manual_review"] is False
    assert item["images_from_ozon"] == ["https://example.test/1.jpg", "https://example.test/2.jpg"]
    assert variant["vendorCode"] == "chev_nr_bpla_pict0001"
    assert variant["title"] == "Шеврон на липучке БПЛА Тест"
    assert variant["dimensions"] == {"length": 10, "width": 10, "height": 1, "weightBrutto": 0.01}
    assert variant["sizes"][0]["price"] == 1100
    assert {"id": 14177449, "value": ["черный", "белый"]} in variant["characteristics"]
    assert {"id": 15000001, "value": ["5810999000"]} in variant["characteristics"]


def test_build_owner_approved_wb_plan_uses_target_wb_photo_urls_when_assets_empty(
    tmp_path: Path,
) -> None:
    approved_dir = tmp_path / "catalog" / "master_passport" / "approved"
    approved_dir.mkdir(parents=True)
    passport = {
        "identity": {
            "internal_sku": "chev_nr_svo_pict0001",
            "ozon_offer_id": "pict0001",
        },
        "approval": {
            "status": "owner_approved_pending_batch_apply",
            "approved_by": "owner_telegram_confirmation",
            "source_review_html": "review.html",
        },
        "content": {
            "wb_title": "Шеврон на липучке СВО Тест",
            "wb_description": "Описание товара\n\nМатериалы\n\nVital Shevron",
            "package_contents": "шеврон на липучке 1 шт.",
        },
        "physical": {
            "package_dimensions_wb_cm": "10*10*1 см",
            "package_weight_g": 10,
            "pack_qty": 1,
        },
        "materials": {"composition": ["полиэстер", "нейлон"]},
        "classification": {"tnved": "5810999000", "country_of_origin": "Россия"},
        "media": {
            "target_assets": [],
            "target_wb_photo_set": [
                {
                    "position": 1,
                    "action": "copy_as_is_after_owner_approval",
                    "source_url": "https://example.test/1.jpg",
                },
                {
                    "position": 2,
                    "action": "keep_current_no_upload",
                    "source_url": "https://example.test/ignore.jpg",
                },
                {
                    "position": 3,
                    "action": "copy_as_is_untouched_after_owner_approval",
                    "source_url": "https://example.test/3.jpg",
                },
            ],
        },
        "wb": {
            "attributes": [
                {"field": "Цвет", "value": "черный; белый"},
                {"field": "Вид декора для одежды", "value": "шеврон"},
                {"field": "Комплектация", "value": "шеврон на липучке 1 шт."},
                {"field": "Количество предметов", "value": "1 шт."},
            ]
        },
    }
    (approved_dir / "chev_nr_svo_pict0001.json").write_text(
        json.dumps(passport, ensure_ascii=False),
        encoding="utf-8",
    )

    items = _build_owner_approved_plan_items(
        data_dir=tmp_path,
        internal_skus=["chev_nr_svo_pict0001"],
        ozon_info_by_offer={"pict0001": {"old_price": "1100"}},
    )

    assert items[0]["needs_manual_review"] is False
    assert items[0]["images_from_ozon"] == [
        "https://example.test/1.jpg",
        "https://example.test/3.jpg",
    ]


def test_build_owner_approved_wb_plan_preserves_local_media_and_sha256(
    tmp_path: Path,
) -> None:
    approved_dir = tmp_path / "catalog" / "master_passport" / "approved"
    approved_dir.mkdir(parents=True)
    media_path = tmp_path / "approved.png"
    media_path.write_bytes(b"owner-approved-image")
    import hashlib

    digest = hashlib.sha256(media_path.read_bytes()).hexdigest()
    passport = {
        "identity": {"internal_sku": "loop_test_0001", "ozon_offer_id": "loop0001"},
        "approval": {
            "status": "owner_approved_pending_batch_apply",
            "approved_by": "owner",
        },
        "content": {
            "wb_title": "Петлицы на липучке ФСБ, олива",
            "wb_description": "Описание\n\nМатериалы\n\nVital Shevron",
            "package_contents": "петлицы 1 шт.",
        },
        "physical": {
            "package_dimensions_wb_cm": "10*4*1 см",
            "package_weight_g": 10,
        },
        "materials": {"composition": ["полиэстер", "нейлон"]},
        "classification": {"tnved": "5810999000", "country_of_origin": "Россия"},
        "media": {
            "target_wb_photo_set": [
                {
                    "position": 1,
                    "action": "owner_approved_watermarked_asset_ready",
                    "local_path": str(media_path),
                    "sha256": digest,
                    "source_url": "https://ir.ozone.ru/unsafe-original.jpg",
                }
            ]
        },
        "wb": {
            "attributes": [
                {"field": "Цвет", "value": "оливковый"},
                {"field": "Вид декора для одежды", "value": "петлица"},
                {"field": "Комплектация", "value": "петлицы 1 шт."},
                {"field": "Количество предметов", "value": "1 шт."},
            ]
        },
    }
    (approved_dir / "loop_test_0001.json").write_text(
        json.dumps(passport, ensure_ascii=False),
        encoding="utf-8",
    )

    items = _build_owner_approved_plan_items(
        data_dir=tmp_path,
        internal_skus=["loop_test_0001"],
        ozon_info_by_offer={"loop0001": {"old_price": "1100"}},
    )

    assert items[0]["images_from_ozon"] == []
    assert items[0]["media_plan"] == [
        {
            "position": 1,
            "source_kind": "owner_approved_local",
            "local_path": str(media_path),
            "sha256": digest,
            "source_url": "https://ir.ozone.ru/unsafe-original.jpg",
            "action": "owner_approved_watermarked_asset_ready",
        }
    ]
    assert items[0]["needs_manual_review"] is False


def test_verify_wb_card_create_checks_card_barcode_and_media_without_write(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    plan_dir = tmp_path / "runs" / "2026-07-14" / "wb_card_create_plan_test"
    plan_dir.mkdir(parents=True)
    (plan_dir / "wb_card_create_plan.json").write_text(
        json.dumps(
            [
                {
                    "master_sku": "chev_test_0001",
                    "draft_variant": {"vendorCode": "chev_test_0001"},
                    "images_from_ozon": ["https://example.test/1.jpg"],
                }
            ]
        ),
        encoding="utf-8",
    )

    class FakeWb:
        def __init__(self, credentials):
            self.credentials = credentials

        def find_cards_by_vendor_codes(self, vendor_codes):
            return {
                "chev_test_0001": {
                    "vendorCode": "chev_test_0001",
                    "nmID": 123,
                    "sizes": [{"skus": ["2047000000000"]}],
                    "photos": [{"big": "https://example.test/wb.jpg"}],
                }
            }

        def find_trash_cards_by_vendor_codes(self, vendor_codes):
            return {}

        def fetch_card_errors(self, limit=100):
            return {"data": []}

        def upload_cards(self, payload):
            raise AssertionError("verify must not upload cards")

        def save_media_links(self, nm_id, urls):
            raise AssertionError("verify must not upload media")

    monkeypatch.setattr("seller_agent.tasks.wb_card_create_apply.WbContentAdapter", FakeWb)

    result = run_wb_card_create_verify(
        credentials=AppCredentials(None, None, WbCredentials("token")),
        data_dir=tmp_path,
        plan_run_id="wb_card_create_plan_test",
        run_id="wb_card_create_verify_test",
    )

    assert result["overall_status"] == "ok"
    assert result["verified_rows"] == 1


def test_wb_card_create_apply_uses_safe_verify_task() -> None:
    apply = get_task_definition("apply-wb-card-create")
    verify = get_task_definition("verify-wb-card-create")

    assert apply["verify_task"] == "wb-card-create-verify"
    assert verify["mode"] == "verify"


def test_wb_card_create_apply_uploads_owner_approved_local_file(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    plan_dir = tmp_path / "runs" / "2026-07-26" / "wb_local_media_plan"
    plan_dir.mkdir(parents=True)
    media_path = tmp_path / "approved.png"
    media_path.write_bytes(b"approved-watermarked")
    import hashlib

    digest = hashlib.sha256(media_path.read_bytes()).hexdigest()
    plan = [
        {
            "master_sku": "loop_test_0001",
            "needs_manual_review": False,
            "target": {"action": "upload", "subject_id": 2367},
            "draft_variant": {
                "vendorCode": "loop_test_0001",
                "characteristics": [],
                "sizes": [{"skus": ["GENERATE_AT_APPLY"]}],
            },
            "images_from_ozon": [],
            "media_plan": [
                {
                    "position": 1,
                    "source_kind": "owner_approved_local",
                    "local_path": str(media_path),
                    "sha256": digest,
                }
            ],
        }
    ]
    (plan_dir / "wb_card_create_plan.json").write_text(
        json.dumps(plan),
        encoding="utf-8",
    )

    class FakeWb:
        def __init__(self, credentials):
            self.created = False
            self.photos = []

        def find_cards_by_vendor_codes(self, vendor_codes):
            if not self.created:
                return {}
            return {
                "loop_test_0001": {
                    "vendorCode": "loop_test_0001",
                    "nmID": 123,
                    "sizes": [{"skus": ["2047000000000"]}],
                    "photos": list(self.photos),
                }
            }

        def find_trash_cards_by_vendor_codes(self, vendor_codes):
            return {}

        def generate_barcodes(self, count):
            return ["2047000000000"]

        def upload_cards(self, payload):
            self.created = True
            return {"error": False}

        def upload_add_cards(self, payload):
            raise AssertionError("not expected")

        def upload_media_file(self, nm_id, photo_number, file_path):
            assert file_path == media_path
            self.photos = [{"big": "https://example.test/1.webp"}]
            return {"error": False}

        def save_media_links(self, nm_id, urls):
            raise AssertionError("unprotected URL route must not be used")

        def fetch_card_errors(self, limit=100):
            return {"data": []}

    monkeypatch.setattr(
        "seller_agent.tasks.wb_card_create_apply.WbContentAdapter",
        FakeWb,
    )

    result = run_wb_card_create_apply(
        credentials=AppCredentials(None, None, WbCredentials("token")),
        data_dir=tmp_path,
        plan_run_id="wb_local_media_plan",
        run_id="wb_local_media_apply",
        confirmed_by_user=True,
        wait_seconds=0,
        poll_interval=1,
    )

    assert result["overall_status"] == "ok"
    assert result["summary"]["media_upload_attempts"] == 1
    assert result["summary"]["pending_media_uploads"] == 0
