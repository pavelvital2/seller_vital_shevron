import json
from pathlib import Path

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
