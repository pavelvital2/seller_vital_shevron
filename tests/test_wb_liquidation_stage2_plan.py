from seller_agent.tasks.wb_liquidation_stage2_plan import build_stage2_rows


def test_stage2_plan_uses_fresh_price_and_blocks_target_below_floor() -> None:
    cohort = [
        {"nm_id": "1", "requires_second_price_stage": "True", "base_price": "2100", "upload_discount_stage1": "66", "target_discount": "68", "target_price": "672", "clearance_floor": "630", "pack_qty": "2"},
        {"nm_id": "2", "requires_second_price_stage": "True", "base_price": "1000", "upload_discount_stage1": "66", "target_discount": "70", "target_price": "300", "clearance_floor": "400", "pack_qty": "1"},
        {"nm_id": "3", "requires_second_price_stage": "False"},
    ]
    prices = [
        {"nmID": 1, "prices": [2100], "discount": 66, "discountedPrices": [714]},
        {"nmID": 2, "prices": [1000], "discount": 66, "discountedPrices": [340]},
    ]

    rows = build_stage2_rows(cohort, prices)

    assert len(rows) == 2
    assert rows[0]["target_price"] == 672.0
    assert rows[0]["ready_for_owner_review"] is True
    assert rows[0]["already_at_target"] is False
    assert rows[1]["ready_for_owner_review"] is False
    assert rows[1]["blocked_reason"] == "target_below_floor"
