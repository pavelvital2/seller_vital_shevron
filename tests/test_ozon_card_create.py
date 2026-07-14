import json
from pathlib import Path

import pytest

from seller_agent.config import AppCredentials, OzonSellerCredentials
from seller_agent.http import ApiError
from seller_agent.tasks.ozon_card_create_apply import run_ozon_card_create_apply, run_ozon_card_create_verify
from seller_agent.tasks.ozon_card_create_plan import _build_ozon_create_payload, run_ozon_card_create_plan
from seller_agent.tasks.registry import get_task_definition


def _passport() -> dict:
    return {
        "identity": {
            "internal_sku": "chev_pz_ng_text0043",
            "wb_vendor_code": "chev_pz_ng_text0043",
            "wb_nm_id": "611952974",
            "wb_barcode": "2047238654351",
        },
        "approval": {"status": "owner_approved"},
        "classification": {"tnved": "5810999000", "country_of_origin": "Россия"},
        "content": {
            "ozon_title": "Шеврон на липучке позывной Барс нагрудный мох",
            "ozon_description": "Описание товара\n\nМатериалы\n\nVital Shevron",
            "package_contents": "шеврон на липучке 1 шт.; мягкая часть липучки в комплекте",
        },
        "physical": {
            "product_size_mm": "125*25 мм",
            "package_dimensions_ozon_mm": "130*50*10 мм",
            "package_weight_g": 10,
            "pack_qty": 1,
        },
        "materials": {"material": "Габардин", "composition": ["полиэстер", "нейлон"]},
        "grouping": {"target_group_key": "Позывные нагрудные мох"},
        "seo": {"ozon_hashtags": ["#шеврон", "#шеврон_на_липучке"]},
        "media": {
            "target_assets": [
                {"url": "https://example.test/1.jpg"},
                {"url": "https://example.test/2.jpg"},
            ]
        },
        "ozon": {
            "attributes": [
                {"field": "Цвет товара", "value": "зеленый; черный"},
                {"field": "Название цвета", "value": "Барс, мох"},
                {"field": "Название модели", "value": "Позывные нагрудные мох"},
                {"field": "Материал", "value": "Габардин"},
                {"field": "Вид выпуска товара", "value": "Фабричное производство"},
            ]
        },
    }


def test_build_ozon_create_payload_from_owner_approved_passport() -> None:
    payload, price_payload, errors, target = _build_ozon_create_payload(
        passport=_passport(),
        price="363.00",
        old_price="1100.00",
        min_price="400.00",
        dictionaries={},
    )

    assert errors == []
    assert payload is not None
    assert price_payload is not None
    assert payload["offer_id"] == "chev_pz_ng_text0043"
    assert payload["description_category_id"] == 17028963
    assert payload["type_id"] == 970886657
    assert payload["name"] == "Шеврон на липучке позывной Барс нагрудный мох"
    assert payload["primary_image"] == "https://example.test/1.jpg"
    assert payload["images"] == ["https://example.test/2.jpg"]
    assert payload["depth"] == 130
    assert payload["width"] == 50
    assert payload["height"] == 10
    assert payload["price"] == "363.00"
    assert payload["old_price"] == "1100.00"
    attrs_by_id = {item["id"]: item for item in payload["attributes"]}
    assert attrs_by_id[23536]["values"] == [{"dictionary_value_id": 0, "value": "false"}]
    assert price_payload == {
        "currency_code": "RUB",
        "offer_id": "chev_pz_ng_text0043",
        "old_price": "1100.00",
        "price": "363.00",
        "min_price": "400.00",
        "min_price_for_auto_actions_enabled": True,
    }
    assert target["model_name"] == "Позывные нагрудные мох"


def test_ozon_create_payload_blocks_existing_ozon_identity() -> None:
    passport = _passport()
    passport["identity"]["ozon_offer_id"] = "old_offer"

    payload, price_payload, errors, _ = _build_ozon_create_payload(
        passport=passport,
        price="363.00",
        old_price="1100.00",
        min_price="400.00",
        dictionaries={},
    )

    assert payload is None
    assert price_payload is None
    assert "ozon_identity_already_present" in errors


def test_ozon_create_payload_requires_min_price() -> None:
    payload, price_payload, errors, _ = _build_ozon_create_payload(
        passport=_passport(),
        price="363.00",
        old_price="1100.00",
        min_price="",
        dictionaries={},
    )

    assert payload is None
    assert price_payload is None
    assert "ozon_min_price_missing" in errors


def test_plan_ozon_card_create_from_layer3_with_wb_price_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    approved_dir = tmp_path / "catalog" / "master_passport" / "approved"
    approved_dir.mkdir(parents=True)
    (approved_dir / "chev_pz_ng_text0043.json").write_text(json.dumps(_passport(), ensure_ascii=False), encoding="utf-8")
    pricing_dir = tmp_path / "pricing"
    pricing_dir.mkdir()
    (pricing_dir / "pricing_status.json").write_text(
        json.dumps(
            [
                {
                    "internal_sku": "chev_pz_ng_text0043",
                    "wb_discounted_price": "363",
                    "wb_base_price": "1100",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class FakeOzon:
        def __init__(self, credentials):
            self.credentials = credentials

        def fetch_product_attributes(self, offer_ids):
            return []

    monkeypatch.setattr("seller_agent.tasks.ozon_card_create_plan.OzonSellerAdapter", FakeOzon)

    result = run_ozon_card_create_plan(
        credentials=AppCredentials(OzonSellerCredentials("id", "key"), None, None),
        data_dir=tmp_path,
        internal_skus=["chev_pz_ng_text0043"],
        allow_wb_price_fallback=True,
        min_price="400",
        skip_schema_api=True,
    )

    assert result["overall_status"] == "warning"
    assert result["ready_rows"] == 1
    assert result["manual_review_items"] == 1
    plan = json.loads(Path(result["artifacts"]["plan"]).read_text(encoding="utf-8"))
    assert plan[0]["price_source"] == "wb_price_fallback_requires_owner_review"
    assert plan[0]["payload"]["offer_id"] == "chev_pz_ng_text0043"
    assert plan[0]["price_payload"]["offer_id"] == "chev_pz_ng_text0043"
    assert plan[0]["price_payload"]["min_price"] == "400.00"
    price_draft = json.loads(Path(result["artifacts"]["price_payload_draft"]).read_text(encoding="utf-8"))
    assert price_draft["prices"][0]["min_price"] == "400.00"


def test_plan_ozon_card_create_treats_missing_offer_404_as_available(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    approved_dir = tmp_path / "catalog" / "master_passport" / "approved"
    approved_dir.mkdir(parents=True)
    (approved_dir / "chev_pz_ng_text0043.json").write_text(json.dumps(_passport(), ensure_ascii=False), encoding="utf-8")
    pricing_dir = tmp_path / "pricing"
    pricing_dir.mkdir()
    (pricing_dir / "pricing_status.json").write_text(
        json.dumps([{"internal_sku": "chev_pz_ng_text0043", "ozon_price": "363", "ozon_old_price": "1100"}], ensure_ascii=False),
        encoding="utf-8",
    )

    class FakeOzon:
        def __init__(self, credentials):
            self.credentials = credentials

        def fetch_product_attributes(self, offer_ids):
            raise ApiError("POST", "https://api-seller.ozon.ru/v4/product/info/attributes", 404, '{"message":"item not found"}')

    monkeypatch.setattr("seller_agent.tasks.ozon_card_create_plan.OzonSellerAdapter", FakeOzon)

    result = run_ozon_card_create_plan(
        credentials=AppCredentials(OzonSellerCredentials("id", "key"), None, None),
        data_dir=tmp_path,
        internal_skus=["chev_pz_ng_text0043"],
        min_price="400",
        skip_schema_api=True,
    )

    assert result["overall_status"] == "ok"
    plan = json.loads(Path(result["artifacts"]["plan"]).read_text(encoding="utf-8"))
    assert plan[0]["ready"] is True
    assert "ozon_offer_id_not_found_ok_for_create" in plan[0]["warnings"]


def test_plan_ozon_card_create_prefers_group_ozon_price_before_wb_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    approved_dir = tmp_path / "catalog" / "master_passport" / "approved"
    approved_dir.mkdir(parents=True)
    (approved_dir / "chev_pz_ng_text0043.json").write_text(json.dumps(_passport(), ensure_ascii=False), encoding="utf-8")
    pricing_dir = tmp_path / "pricing"
    pricing_dir.mkdir()
    (pricing_dir / "pricing_status.json").write_text(
        json.dumps(
            [
                {"internal_sku": "chev_pz_ng_text0002", "ozon_price": "550", "ozon_old_price": "1100"},
                {"internal_sku": "chev_pz_ng_text0003", "ozon_price": "550", "ozon_old_price": "1100"},
                {
                    "internal_sku": "chev_pz_ng_text0043",
                    "wb_discounted_price": "363",
                    "wb_base_price": "1100",
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class FakeOzon:
        def __init__(self, credentials):
            self.credentials = credentials

        def fetch_product_attributes(self, offer_ids):
            return []

    monkeypatch.setattr("seller_agent.tasks.ozon_card_create_plan.OzonSellerAdapter", FakeOzon)

    result = run_ozon_card_create_plan(
        credentials=AppCredentials(OzonSellerCredentials("id", "key"), None, None),
        data_dir=tmp_path,
        internal_skus=["chev_pz_ng_text0043"],
        allow_wb_price_fallback=True,
        min_price="400",
        skip_schema_api=True,
    )

    plan = json.loads(Path(result["artifacts"]["plan"]).read_text(encoding="utf-8"))
    assert plan[0]["price_source"] == "ozon_group_price_template_requires_owner_review"
    assert plan[0]["payload"]["price"] == "550.00"
    assert plan[0]["payload"]["old_price"] == "1100.00"
    assert plan[0]["price_payload"]["min_price"] == "400.00"


def test_apply_ozon_card_create_requires_confirmation(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="explicit user confirmation"):
        run_ozon_card_create_apply(
            credentials=AppCredentials(OzonSellerCredentials("id", "key"), None, None),
            data_dir=tmp_path,
            plan_run_id="x",
        )


def test_verify_ozon_card_create_reads_current_card_and_price_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_dir = tmp_path / "runs" / "2026-07-14" / "ozon_card_create_plan_test"
    plan_dir.mkdir(parents=True)
    (plan_dir / "ozon_card_create_plan.json").write_text(
        json.dumps(
            [
                {
                    "ready": True,
                    "payload": {"offer_id": "chev_test_0001"},
                    "price_payload": {
                        "offer_id": "chev_test_0001",
                        "price": "550.00",
                        "old_price": "1100.00",
                        "min_price": "400.00",
                    },
                }
            ]
        ),
        encoding="utf-8",
    )

    class FakeOzon:
        def __init__(self, credentials):
            self.credentials = credentials

        def fetch_product_attributes(self, offer_ids):
            return [{"offer_id": offer_ids[0], "id": 123, "sku": 456, "name": "Test"}]

        def fetch_product_info(self, product_ids):
            return [
                {
                    "id": 123,
                    "offer_id": "chev_test_0001",
                    "sku": 456,
                    "statuses": {"is_created": True, "status": "price_sent"},
                    "errors": [],
                }
            ]

        def fetch_product_info_prices_by_offer_ids(self, offer_ids):
            return [
                {
                    "offer_id": offer_ids[0],
                    "price": {"price": "550.00", "old_price": "1100.00", "min_price": "400.00"},
                }
            ]

        def import_products(self, payload):
            raise AssertionError("verify must not import products")

        def import_product_prices(self, payload):
            raise AssertionError("verify must not import prices")

    monkeypatch.setattr("seller_agent.tasks.ozon_card_create_apply.OzonSellerAdapter", FakeOzon)

    result = run_ozon_card_create_verify(
        credentials=AppCredentials(OzonSellerCredentials("id", "key"), None, None),
        data_dir=tmp_path,
        plan_run_id="ozon_card_create_plan_test",
        run_id="ozon_card_create_verify_test",
    )

    assert result["overall_status"] == "ok"
    assert result["verified_rows"] == 1
    assert result["price_verify_status"] == "ok"


def test_task_registry_contains_ozon_card_create_commands() -> None:
    plan = get_task_definition("plan-ozon-card-create")
    apply = get_task_definition("apply-ozon-card-create")
    verify = get_task_definition("verify-ozon-card-create")

    assert plan["mode"] == "dry_run"
    assert plan["requires_credentials"] is True
    assert apply["mode"] == "apply"
    assert apply["requires_confirmation"] is True
    assert apply["verify_task"] == "ozon-card-create-verify"
    assert verify["mode"] == "verify"
    assert "apply_missing_source_plan_task" not in apply["policy_issues"]
