import json
from pathlib import Path

from seller_agent.config import AppCredentials, WbCredentials
from seller_agent.tasks.registry import get_task_definition
from seller_agent.tasks.wb_card_create_apply import run_wb_card_create_verify
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
