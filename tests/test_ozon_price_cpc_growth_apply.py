from decimal import Decimal

from scripts.pricing.apply_ozon_price_cpc_growth import classify_bid_rows, classify_price_rows


def _price_row() -> dict[str, object]:
    return {
        "offer_id": "offer-1",
        "product_id": "101",
        "current_min_price": "530.00",
        "target_min_price": "530.00",
        "current_price": "550.00",
        "target_price": "650.00",
        "current_old_price": "1100.00",
        "target_old_price": "1300.00",
    }


def test_classify_price_rows_supports_current_target_and_drift() -> None:
    current = {
        "product_id": 101,
        "min_price": 530,
        "price": 550,
        "old_price": 1100,
    }
    apply_rows, already, drifted = classify_price_rows([_price_row()], {"offer-1": current})
    assert len(apply_rows) == 1
    assert not already
    assert not drifted

    target = {**current, "price": 650, "old_price": 1300}
    apply_rows, already, drifted = classify_price_rows([_price_row()], {"offer-1": target})
    assert not apply_rows
    assert len(already) == 1
    assert not drifted

    changed = {**current, "price": 600}
    apply_rows, already, drifted = classify_price_rows([_price_row()], {"offer-1": changed})
    assert not apply_rows
    assert not already
    assert drifted[0]["reason"] == "price_state_drift"


def test_growth_bid_requires_verified_price_but_reduction_does_not() -> None:
    rows = [
        {"sku": "1", "action": "increase_growth", "current_bid": "1.00", "target_bid": "2.00"},
        {"sku": "2", "action": "reduce_high_drr", "current_bid": "4.00", "target_bid": "2.00"},
    ]
    apply_rows, already, skipped = classify_bid_rows(
        rows,
        {"1": Decimal("1.00"), "2": Decimal("4.00")},
        {"1": "offer-1"},
        set(),
    )
    assert [row["sku"] for row in apply_rows] == ["2"]
    assert not already
    assert skipped[0]["skip_reason"] == "growth_price_not_verified"


def test_bid_drift_is_skipped_and_target_is_idempotent() -> None:
    rows = [
        {"sku": "1", "action": "increase_growth", "current_bid": "1.00", "target_bid": "2.00"},
        {"sku": "2", "action": "reduce_high_drr", "current_bid": "4.00", "target_bid": "2.00"},
    ]
    apply_rows, already, skipped = classify_bid_rows(
        rows,
        {"1": Decimal("1.50"), "2": Decimal("2.00")},
        {"1": "offer-1"},
        {"offer-1"},
    )
    assert not apply_rows
    assert [row["sku"] for row in already] == ["2"]
    assert skipped[0]["skip_reason"] == "current_bid_drift"
