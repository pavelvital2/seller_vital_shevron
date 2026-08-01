import pytest

import seller_agent.tasks.card_content_update as card_content_update
from seller_agent.config import AppCredentials
from seller_agent.tasks.card_content_update import (
    _build_ozon_payload,
    _build_ozon_verify_payload,
    _build_wb_payload,
    _normalize_ozon_hashtags,
    _ozon_attribute_update_items,
    _ozon_product_status_is_blocking,
    _ozon_task_id,
    _verify_wb_payloads,
    _verify_one,
    _wb_media_urls_from_passport,
    run_apply_approved_card,
)
from seller_agent.tasks.approved_cards_apply import run_apply_approved_cards
from seller_agent.tasks.registry import get_task_definition
from seller_agent.tasks.wb_media import build_wb_media_plan


def test_wb_media_plan_never_falls_back_to_generic_assets_when_wb_set_exists() -> None:
    passport = {
        "media": {
            "target_wb_photo_set": [
                {"position": 1, "action": "keep_current_protected"},
                {
                    "position": 5,
                    "action": "add_neutral_wearing_slide_unchanged",
                    "source_url": "https://ir.ozone.ru/new.jpg",
                },
            ],
            "target_assets": [
                {
                    "position": position,
                    "url": f"https://basket-34.wbbasket.ru/current-{position}.webp",
                }
                for position in range(1, 5)
            ]
            + [{"position": 5, "url": "https://ir.ozone.ru/new.jpg"}],
        }
    }

    media_plan = build_wb_media_plan(passport)

    assert [row["position"] for row in media_plan] == [5]
    assert [row["source_url"] for row in media_plan] == ["https://ir.ozone.ru/new.jpg"]


def test_plan_one_ozon_only_does_not_read_or_build_wb(
    tmp_path,
    monkeypatch,
) -> None:
    passport = {
        "identity": {
            "internal_sku": "loop_test_0001",
            "ozon_offer_id": "loop_test_0001",
            "wb_vendor_code": "loop_test_0001",
        }
    }
    monkeypatch.setattr(
        card_content_update,
        "_find_local_ozon",
        lambda data_dir, offer_id: {"offer_id": offer_id},
    )
    monkeypatch.setattr(
        card_content_update,
        "_build_ozon_payload",
        lambda **kwargs: ({"offer_id": "loop_test_0001"}, [], []),
    )

    def fail_wb_read(*args, **kwargs):
        raise AssertionError("WB must not be read for an Ozon-only plan")

    monkeypatch.setattr(card_content_update, "_find_local_wb", fail_wb_read)

    row = card_content_update._plan_one(
        passport=passport,
        data_dir=tmp_path,
        credentials=AppCredentials(
            ozon_seller=None,
            ozon_performance=None,
            wb=None,
        ),
        skip_api=True,
        price_rows={},
        run_dir=tmp_path,
        marketplaces={"ozon"},
    )

    assert row["ready"] is True
    assert set(row["marketplaces"]) == {"ozon"}


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


def test_build_wb_payload_applies_conditional_adult_and_preserves_barcode() -> None:
    passport = {
        "content": {"wb_title": "Шеврон на липучке ЧВК", "wb_description": "Описание"},
        "physical": {"package_dimensions_wb_cm": "10*10*1 см", "package_weight_g": 10, "pack_qty": 1},
        "wb": {
            "attributes": [
                {"field": "Цвет", "value": "красный, черный"},
                {"field": "Вид декора для одежды", "value": "шеврон"},
                {"field": "Состав", "value": "полиэстер; нейлон"},
                {"field": "Комплектация", "value": "шеврон на липучке 1 шт."},
            ],
            "write_constraints": {
                "barcode": {"action": "do_not_change_or_update", "include_in_write_payload": False},
                "isAdult": {"target": True, "apply_condition": "only_if_current_not_true"},
            },
        },
    }
    current = {
        "nmID": 707654779,
        "vendorCode": "svopklpict0009",
        "title": "Old",
        "description": "Old",
        "dimensions": {"length": 10, "width": 10, "height": 1, "weightBrutto": 0.01},
        "characteristics": [],
        "sizes": [{"skus": ["2052807975386"], "techSize": "0", "wbSize": ""}],
        "isAdult": False,
    }

    payload, changes, errors = _build_wb_payload(passport, current)

    assert errors == []
    assert payload is not None
    assert payload["isAdult"] is True
    assert payload["sizes"] == current["sizes"]
    assert next(change for change in changes if change["field"] == "isAdult") == {
        "field": "isAdult",
        "current": False,
        "target": True,
        "apply_condition": "only_if_current_not_true",
    }


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


def test_build_ozon_payload_overwrites_marking_and_package_weight_attrs() -> None:
    passport = {
        "content": {"ozon_title": "Шеврон на липучке ГБР на спину", "ozon_description": "Описание"},
        "physical": {
            "product_size_mm": "250*100 мм",
            "package_dimensions_ozon_mm": "300*100*10 мм",
            "package_weight_g": 30,
            "pack_qty": 1,
        },
        "ozon": {"attributes": [{"field": "Цвет", "value": "черный, серый"}, {"field": "Название цвета", "value": "ГБР"}]},
    }
    current = {
        "offer_id": "chev_back_form_text0002",
        "barcode": "123",
        "description_category_id": 17028963,
        "type_id": 970886657,
        "primary_image": "https://example.test/main.jpg",
        "images": ["https://example.test/2.jpg"],
        "attributes": [
            {"id": 10096, "values": [{"dictionary_value_id": 61574, "value": "черный"}, {"dictionary_value_id": 61576, "value": "серый"}]},
            {"id": 23536, "values": [{"dictionary_value_id": 0, "value": "true"}]},
            {"id": 4497, "values": [{"dictionary_value_id": 0, "value": "6"}]},
        ],
    }
    price_row = {"ozon_price": "550", "ozon_old_price": "1100"}

    payload, _, errors = _build_ozon_payload(passport=passport, current=current, price_row=price_row)

    assert errors == []
    marking_attr = next(attr for attr in payload["attributes"] if attr["id"] == 23536)
    package_weight_attr = next(attr for attr in payload["attributes"] if attr["id"] == 4497)
    assert marking_attr["values"] == [{"dictionary_value_id": 0, "value": "false"}]
    assert package_weight_attr["values"] == [{"dictionary_value_id": 0, "value": "30"}]
    assert payload["weight"] == 30


def test_build_ozon_payload_omits_current_hashtags_when_owner_disables_field() -> None:
    passport = {
        "content": {
            "ozon_title": "Петлицы на липучке ФСБ, олива",
            "ozon_description": "Описание",
        },
        "physical": {
            "product_size_mm": "80*30*5 мм",
            "package_dimensions_ozon_mm": "100*40*10 мм",
            "package_weight_g": 10,
            "pack_qty": 1,
        },
        "seo": {"ozon_hashtags": []},
        "ozon": {
            "attributes": [
                {"field": "Цвет", "value": "оливковый"},
                {"field": "Название цвета", "value": "Петлицы ФСБ, олива"},
            ],
            "write_constraints": {
                "hashtags": {
                    "include_in_write_payload": False,
                    "reason_code": "FB_OBSCENE_MODEL_hashtag",
                }
            },
        },
    }
    current = {
        "offer_id": "loop_fsb_0001",
        "barcode": "123",
        "description_category_id": 17038663,
        "type_id": 970925348,
        "primary_image": "https://example.test/main.jpg",
        "images": ["https://example.test/2.jpg"],
        "attributes": [
            {
                "id": 23171,
                "values": [{"dictionary_value_id": 0, "value": "#петлицы_фсб"}],
            },
            {
                "id": 10096,
                "values": [{"dictionary_value_id": 61605, "value": "оливковый"}],
            },
        ],
    }

    payload, _, errors = _build_ozon_payload(
        passport=passport,
        current=current,
        price_row={"ozon_price": "454", "ozon_old_price": "539"},
    )

    assert errors == []
    assert not any(attr["id"] == 23171 for attr in payload["attributes"])


def test_ozon_attribute_update_items_contains_all_payload_attrs() -> None:
    payloads = [
        {
            "offer_id": "chev_back_form_text0002",
            "attributes": [
                {"id": 4180, "values": [{"dictionary_value_id": 0, "value": "Название"}]},
                {"id": 23536, "values": [{"dictionary_value_id": 0, "value": "false"}]},
                {"id": 4497, "values": [{"dictionary_value_id": 0, "value": "30"}]},
            ],
        }
    ]

    assert _ozon_attribute_update_items(payloads) == [
        {
            "offer_id": "chev_back_form_text0002",
            "attributes": [
                {"id": 4180, "values": [{"dictionary_value_id": 0, "value": "Название"}]},
                {"id": 23536, "values": [{"dictionary_value_id": 0, "value": "false"}]},
                {"id": 4497, "values": [{"dictionary_value_id": 0, "value": "30"}]},
            ],
        }
    ]


def test_build_ozon_verify_payload_does_not_require_price_and_includes_safety_attrs() -> None:
    passport = {
        "content": {"ozon_title": "Шеврон на липучке ГБР на спину", "ozon_description": "Описание"},
        "physical": {
            "package_dimensions_ozon_mm": "300*100*10 мм",
            "package_weight_g": 30,
            "pack_qty": 1,
        },
        "seo": {"ozon_hashtags": "шеврон, шеврон на липучке, группа быстрого реагирования"},
        "ozon": {"attributes": [{"field": "Цвет", "value": "черный, серый"}, {"field": "Название цвета", "value": "ГБР"}]},
    }
    current = {
        "offer_id": "chev_back_form_text0002",
        "primary_image": "https://example.test/main.jpg",
        "images": ["https://example.test/2.jpg"],
        "attributes": [],
    }

    payload, errors = _build_ozon_verify_payload(passport, current)

    assert errors == []
    assert payload["offer_id"] == "chev_back_form_text0002"
    assert payload["depth"] == 300
    assert payload["width"] == 100
    assert payload["height"] == 10
    assert payload["weight"] == 30
    assert next(attr for attr in payload["attributes"] if attr["id"] == 23536)["values"] == [
        {"dictionary_value_id": 0, "value": "false"}
    ]
    assert next(attr for attr in payload["attributes"] if attr["id"] == 4497)["values"] == [
        {"dictionary_value_id": 0, "value": "30"}
    ]
    assert next(attr for attr in payload["attributes"] if attr["id"] == 23171)["values"] == [
        {"dictionary_value_id": 0, "value": "#шеврон #шеврон_на_липучке #группа_быстрого_реагирования"}
    ]


def test_ozon_task_id_supports_root_and_result_formats() -> None:
    assert _ozon_task_id({"task_id": 11}) == 11
    assert _ozon_task_id({"result": {"task_id": 22}}) == 22


def test_ozon_product_status_blocks_failed_update_but_not_no_stock() -> None:
    assert _ozon_product_status_is_blocking({"status_description": "Не обновлен"}) is True
    assert _ozon_product_status_is_blocking({"status_tooltip": "Не удалось обновить товар"}) is True
    assert _ozon_product_status_is_blocking({"status_name": "Готов к продаже", "status_description": "Нет на складе"}) is False


def test_normalize_ozon_hashtags_uses_hash_underscore_and_space_separator() -> None:
    assert _normalize_ozon_hashtags("шеврон, шеврон на липучке; #ГБР на спину; шеврон группа быстрого реагирования") == (
        "#шеврон #шеврон_на_липучке #ГБР_на_спину"
    )


def test_wb_media_urls_from_passport_requires_explicit_target_assets() -> None:
    assert _wb_media_urls_from_passport({"media": {"designer_tasks": ["перенести фото"]}}) == []
    assert _wb_media_urls_from_passport(
        {"media": {"target_assets": [{"url": "https://example.test/1.jpg"}, {"url": "https://example.test/1.jpg"}]}}
    ) == ["https://example.test/1.jpg"]


def test_verify_one_does_not_treat_future_ozon_offer_id_as_existing_card(tmp_path) -> None:
    passport = {
        "identity": {
            "internal_sku": "wb_only_1",
            "ozon_offer_id": "",
            "ozon_offer_id_after_seller_sku_update": "wb_only_1",
            "wb_vendor_code": "wb_only_1",
        }
    }

    result = _verify_one(
        passport=passport,
        data_dir=tmp_path,
        credentials=AppCredentials(ozon_seller=None, ozon_performance=None, wb=None),
        skip_api=True,
        run_dir=tmp_path / "verify",
    )

    assert "ozon" not in result["marketplaces"]
    assert result["marketplaces"]["wb"]["status"] == "missing"
    assert result["errors"] == ["wb_current_card_not_found"]


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
    verify = get_task_definition("verify-card-content-update")
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
    assert apply["verify_task"] == "card-content-update-verify"
    assert verify["name"] == "card-content-update-verify"
    assert verify["mode"] == "verify"
    assert verify["requires_confirmation"] is False
    assert fast["name"] == "approved-card-apply"
    assert fast["mode"] == "apply"
    assert fast["requires_confirmation"] is True
    assert fast["verify_task"] == "card-content-update-verify"
    assert batch["name"] == "approved-cards-batch-apply"
    assert batch["mode"] == "apply"
    assert batch["requires_confirmation"] is True
    assert batch["verify_task"] == "card-content-update-verify"


def test_apply_approved_card_requires_confirmation(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="explicit user confirmation"):
        run_apply_approved_card(credentials=None, data_dir=tmp_path, internal_skus=["x"])


def test_apply_approved_cards_requires_confirmation(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="explicit user confirmation"):
        run_apply_approved_cards(credentials=None, data_dir=tmp_path, internal_skus=["x"])
