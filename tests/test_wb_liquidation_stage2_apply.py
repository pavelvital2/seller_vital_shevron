from decimal import Decimal

from seller_agent.tasks.wb_liquidation_stage2_apply import _payload_from_plan


def test_stage2_payload_changes_only_discount_and_preserves_reviewed_base_price() -> None:
    pending = {
        "actions": [
            {"nm_id": 101, "discount": 70, "expected_price": 630.0},
            {"nm_id": 102, "discount": 68, "expected_price": 672.0},
        ]
    }
    rows = [
        {
            "nm_id": "101",
            "internal_sku": "kit-1",
            "live_base_price": "2100.0",
            "live_discount": "66",
            "live_discounted_price": "714.0",
            "clearance_floor": "630.0",
        },
        {
            "nm_id": "102",
            "internal_sku": "kit-2",
            "live_base_price": "2100.0",
            "live_discount": "67",
            "live_discounted_price": "693.0",
            "clearance_floor": "630.0",
        },
    ]

    payload, review = _payload_from_plan(pending=pending, rows=rows)

    assert payload == {
        "data": [
            {"nmID": 101, "price": 2100, "discount": 70},
            {"nmID": 102, "price": 2100, "discount": 68},
        ]
    }
    assert [Decimal(str(row["target_price"])) for row in review] == [Decimal("630.0"), Decimal("672.0")]


def test_stage2_payload_blocks_price_below_floor() -> None:
    pending = {"actions": [{"nm_id": 101, "discount": 70, "expected_price": 300.0}]}
    rows = [
        {
            "nm_id": "101",
            "live_base_price": "1000.0",
            "live_discount": "60",
            "live_discounted_price": "400.0",
            "clearance_floor": "350.0",
        }
    ]

    try:
        _payload_from_plan(pending=pending, rows=rows)
    except RuntimeError as exc:
        assert "below clearance floor" in str(exc)
    else:
        raise AssertionError("target below floor must be blocked")
