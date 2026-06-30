import pytest

from seller_agent.tasks.card_content_update import (
    _build_ozon_payload,
    _build_wb_payload,
    _verify_wb_payloads,
    _wb_media_urls_from_passport,
    run_apply_approved_card,
)
from seller_agent.tasks.approved_cards_apply import run_apply_approved_cards
from seller_agent.tasks.registry import get_task_definition


def test_build_wb_payload_preserves_identity_and_sizes() -> None:
    passport = {
        "content": {
            "wb_title": "Шеврон на липучке БПЛА Смерть сходящая с небес",
            "wb_description": "Описание товара\n\nМатериалы\n\nVital Shevron",
        },
        "physical": {"package_dimensions_wb_cm": "10*10*1 см", "package_weight_g": 10, "pack_qty": 1},
        "wb": {
            "attributes": [
                {"field": "Цвет", "value": "оливковый, черный, красный"},
                {"field": "Вид декора для одежды", "value": "шеврон"},
                {"field": "Состав", "value": "полиэстер; нейлон"},
                {"field": "Количество предметов", "value": "1 шт."},
                {"field": "Комплектация", "value": "шеврон на липучке 1 шт."},
            ]
        },
    }
    current = {
        "nmID": 1212515625,
        "vendorCode": "chev_nr_bpla_pict0023",
        "brand": "VitalEmb",
        "title": "Old title",
        "description": "Old description",
        "dimensions": {"length": 9, "width": 9, "height": 1, "weightBrutto": 0.01},
        "characteristics": [{"id": 14177449, "value": ["оливковый"]}],
        "sizes": [{"skus": ["2052807975386"], "techSize": "0", "wbSize": ""}],
    }

    payload, changes, errors = _build_wb_payload(passport, current)

    assert errors == []
    assert payload is not None
    assert payload["nmID"] == 1212515625
    assert payload["vendorCode"] == "chev_nr_bpla_pict0023"
    assert payload["sizes"] == [{"skus": ["2052807975386"], "techSize": "0", "wbSize": ""}]
    assert payload["dimensions"] == {"length": 10, "width": 10, "height": 1, "weightBrutto": 0.01}
    assert changes[0]["field"] == "title"


def test_build_ozon_payload_blocks_missing_color_dictionary_value() -> None:
    passport = {
        "content": {"ozon_title": "Шеврон на липучке ЧВК", "ozon_description": "Описание"},
        "physical": {"product_size_mm": "80*50 мм", "package_dimensions_ozon_mm": "100*60*10 мм", "package_weight_g": 10, "pack_qty": 1},
        "ozon": {"attributes": [{"field": "Цвет", "value": "черный, белый"}, {"field": "Название цвета", "value": "Славянский корпус"}]},
    }
    current = {
        "offer_id": "chev_kp_chvk_pict0001",
        "barcode": "123",
        "description_category_id": 17038663,
        "type_id": 970925348,
        "primary_image": "https://example.test/main.jpg",
        "images": ["https://example.test/2.jpg"],
        "attributes": [
            {"id": 10096, "values": [{"dictionary_value_id": 61574, "value": "черный"}]},
            {"id": 4191, "values": [{"dictionary_value_id": 0, "value": "old"}]},
        ],
    }
    price_row = {"ozon_price": "454", "ozon_old_price": "539"}

    payload, _, errors = _build_ozon_payload(passport=passport, current=current, price_row=price_row)

    assert payload is None
    assert "ozon_color_dictionary_value_missing" in errors


def test_build_ozon_payload_uses_dictionary_lookup_for_new_color() -> None:
    passport = {
        "content": {"ozon_title": "Шеврон на липучке ЧВК", "ozon_description": "Описание"},
        "physical": {"product_size_mm": "80*50 мм", "package_dimensions_ozon_mm": "100*60*10 мм", "package_weight_g": 10, "pack_qty": 1},
        "ozon": {"attributes": [{"field": "Цвет", "value": "черный, белый"}, {"field": "Название цвета", "value": "Славянский корпус"}]},
    }
    current = {
        "offer_id": "chev_kp_chvk_pict0001",
        "barcode": "123",
        "description_category_id": 17038663,
        "type_id": 970925348,
        "primary_image": "https://example.test/main.jpg",
        "images": ["https://example.test/2.jpg"],
        "attributes": [
            {"id": 10096, "values": [{"dictionary_value_id": 61574, "value": "черный"}]},
            {"id": 4191, "values": [{"dictionary_value_id": 0, "value": "old"}]},
        ],
    }
    price_row = {"ozon_price": "454", "ozon_old_price": "539"}

    payload, _, errors = _build_ozon_payload(
        passport=passport,
        current=current,
        price_row=price_row,
        dictionary_values={10096: {"белый": 61571}},
    )

    assert errors == []
    color_attr = next(attr for attr in payload["attributes"] if attr["id"] == 10096)
    assert color_attr["values"] == [
        {"dictionary_value_id": 61574, "value": "черный"},
        {"dictionary_value_id": 61571, "value": "белый"},
    ]


def test_wb_media_urls_from_passport_requires_explicit_target_assets() -> None:
    assert _wb_media_urls_from_passport({"media": {"designer_tasks": ["перенести фото"]}}) == []
    assert _wb_media_urls_from_passport(
        {"media": {"target_assets": [{"url": "https://example.test/1.jpg"}, {"url": "https://example.test/1.jpg"}]}}
    ) == ["https://example.test/1.jpg"]


def test_wb_verify_normalizes_collapsed_blank_lines_and_is_valid_dimension(tmp_path) -> None:
    class FakeWb:
        def find_cards_by_vendor_codes(self, codes):
            return {
                "sku1": {
                    "vendorCode": "sku1",
                    "title": "Шеврон на липучке БПЛА Улыбнись",
                    "description": "Блок 1.\nБлок 2.\nБлок 3.",
                    "dimensions": {"length": 10, "width": 10, "height": 1, "weightBrutto": 0.01, "isValid": True},
                    "characteristics": [{"id": 14177449, "value": ["черный", "белый"]}],
                    "photos": [{"big": "1"}, {"big": "2"}],
                }
            }

    payloads = [
        {
            "vendorCode": "sku1",
            "nmID": 1,
            "title": "Шеврон на липучке БПЛА Улыбнись",
            "description": "Блок 1.\n\nБлок 2.\n\nБлок 3.",
            "dimensions": {"length": 10, "width": 10, "height": 1, "weightBrutto": 0.01},
            "characteristics": [{"id": 14177449, "value": ["черный", "белый"]}],
        }
    ]

    result = _verify_wb_payloads(FakeWb(), payloads, tmp_path, {"sku1": ["u1", "u2"]})

    assert result["status"] == "ok"
    assert result["results"][0]["checks"] == {
        "title": True,
        "description": True,
        "dimensions": True,
        "colors": True,
        "photo_count": True,
    }


def test_task_registry_contains_card_content_update_commands() -> None:
    plan = get_task_definition("plan-card-content-update")
    promote = get_task_definition("promote-approved-card-passport")
    apply = get_task_definition("apply-card-content-update")
    fast = get_task_definition("apply-approved-card")
    batch = get_task_definition("apply-approved-cards")

    assert plan["name"] == "card-content-update-plan"
    assert plan["mode"] == "dry_run"
    assert plan["requires_mapping"] is True
    assert promote["name"] == "approved-card-passport-promote"
    assert promote["mode"] == "dry_run"
    assert apply["name"] == "card-content-update-apply"
    assert apply["mode"] == "apply"
    assert apply["requires_confirmation"] is True
    assert fast["name"] == "approved-card-apply"
    assert fast["mode"] == "apply"
    assert fast["requires_confirmation"] is True
    assert batch["name"] == "approved-cards-batch-apply"
    assert batch["mode"] == "apply"
    assert batch["requires_confirmation"] is True


def test_apply_approved_card_requires_confirmation(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="explicit user confirmation"):
        run_apply_approved_card(credentials=None, data_dir=tmp_path, internal_skus=["x"])


def test_apply_approved_cards_requires_confirmation(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="explicit user confirmation"):
        run_apply_approved_cards(credentials=None, data_dir=tmp_path, internal_skus=["x"])
