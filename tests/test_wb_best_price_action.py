from decimal import Decimal

from scripts.actions.wb_best_price_action_apply import plan_drift, target_payload
from scripts.actions.wb_best_price_action_plan import build_plan
from seller_agent.tasks.wb_best_price_action import _target_plan_check


def _offer(action_id: int, price: int, discount: int) -> dict:
    return {
        "action_id": action_id,
        "action_name": f"action-{action_id}",
        "action_start": "2026-07-01T00:00:00Z",
        "action_end": "2026-08-01T00:00:00Z",
        "currently_participates": False,
        "status": "Не участвует",
        "vendor_code": "sku",
        "plan_price": Decimal(price),
        "required_discount": discount,
        "actual_price": Decimal(price),
    }


def test_best_price_plan_selects_highest_eligible_price_and_fallback() -> None:
    price_plan = {
        "rows": [
            {
                "nm_id": 1,
                "internal_sku": "kit2",
                "vendor_code": "kit2",
                "pack_qty": 2,
                "target_minimum": 810,
            },
            {
                "nm_id": 2,
                "internal_sku": "single",
                "vendor_code": "single",
                "pack_qty": 1,
                "target_minimum": 500,
            },
        ]
    }
    prices = {
        "goods": [
            {
                "nmID": 1,
                "vendorCode": "kit2",
                "title": "Комплект",
                "prices": [2100],
                "discount": 50,
                "discountedPrices": [1050],
            },
            {
                "nmID": 2,
                "vendorCode": "single",
                "title": "Одиночный",
                "prices": [1300],
                "discount": 50,
                "discountedPrices": [650],
            },
        ]
    }
    promo_rows = {
        1: [_offer(10, 861, 59), _offer(20, 840, 60)],
        2: [_offer(10, 494, 62)],
    }

    products, candidates, summary = build_plan(
        snapshot={"checkedAt": "now", "promos": [{}, {}], "futurePromos": []},
        prices=prices,
        price_plan=price_plan,
        promo_rows_by_nm=promo_rows,
        outside_discount=50,
    )

    assert len(candidates) == 3
    assert products[0]["chosen_action_id"] == 10
    assert products[0]["target_price"] == Decimal("861")
    assert products[0]["target_discount"] == 59
    assert products[1]["chosen_action_id"] == ""
    assert products[1]["target_discount"] == 50
    assert summary["eligible_any_action"] == 1
    assert summary["outside_action"] == 1
    assert summary["selected_multiple_choice"] == 1
    assert summary["target_below_minimum"] == 0
    assert summary["unsafe_single_upload"] == 0


def test_best_price_plan_blocks_outside_discount_below_minimum() -> None:
    price_plan = {
        "rows": [
            {
                "nm_id": 1,
                "internal_sku": "single",
                "vendor_code": "single",
                "pack_qty": 1,
                "target_minimum": 530,
            }
        ]
    }
    prices = {
        "goods": [
            {
                "nmID": 1,
                "vendorCode": "single",
                "title": "Одиночный",
                "prices": [1300],
                "discount": 50,
                "discountedPrices": [650],
            }
        ]
    }

    products, _, summary = build_plan(
        snapshot={"checkedAt": "now", "promos": [], "futurePromos": []},
        prices=prices,
        price_plan=price_plan,
        promo_rows_by_nm={},
        outside_discount=60,
    )

    assert products[0]["target_price"] == Decimal("520.00")
    assert products[0]["target_below_minimum"] is True
    assert products[0]["safe_single_upload"] is False
    assert summary["target_below_minimum"] == 1
    assert summary["unsafe_single_upload"] == 1


def test_apply_payload_and_drift_are_exact() -> None:
    products = [
        {
            "nm_id": 1,
            "base_price": 2100,
            "minimum": 810,
            "current_discount": 50,
            "current_price": 1050,
            "chosen_action_id": 10,
            "target_discount": 59,
            "target_price": 861,
        },
        {
            "nm_id": 2,
            "base_price": 1300,
            "minimum": 500,
            "current_discount": 50,
            "current_price": 650,
            "chosen_action_id": "",
            "target_discount": 50,
            "target_price": 650,
        },
    ]

    assert plan_drift(products, products)["status"] == "ok"
    payload = target_payload(products, changes_only=True)
    assert payload == {"data": [{"nmID": 1, "price": 2100, "discount": 59}]}

    changed = [dict(row) for row in products]
    changed[0]["target_discount"] = 60
    drift = plan_drift(products, changed)
    assert drift["status"] == "blocked"
    assert drift["drift_rows_count"] == 1


def test_verify_target_signature_normalizes_json_numbers() -> None:
    approved = [
        {
            "nm_id": 1,
            "minimum": 530.0,
            "chosen_action_id": "",
            "target_discount": 50,
            "target_price": 650.0,
        }
    ]
    fresh = [
        {
            "nm_id": 1,
            "minimum": Decimal("530"),
            "chosen_action_id": "",
            "target_discount": 50,
            "target_price": Decimal("650.00"),
        }
    ]

    assert _target_plan_check(approved, fresh)["status"] == "ok"
