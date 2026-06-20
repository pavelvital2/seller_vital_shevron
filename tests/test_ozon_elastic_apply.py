import pytest

from seller_agent.tasks.ozon_elastic_apply import (
    _action_rows,
    _assert_no_drift,
    _build_partial_drift_plan,
)


def test_action_rows_selects_add_changed_updates_and_deactivations() -> None:
    rows = [
        {
            "planned_action": "add_to_action",
            "product_id": "1",
            "offer_id": "a",
            "current_action_price": "",
            "calculated_action_price": "100",
        },
        {
            "planned_action": "update_action_price",
            "product_id": "2",
            "offer_id": "b",
            "current_action_price": "100",
            "calculated_action_price": "100",
        },
        {
            "planned_action": "update_action_price",
            "product_id": "3",
            "offer_id": "c",
            "current_action_price": "100",
            "calculated_action_price": "101",
        },
        {
            "planned_action": "deactivate_from_action",
            "product_id": "4",
            "offer_id": "d",
            "current_action_price": "100",
            "calculated_action_price": "",
        },
    ]

    activate_rows, deactivate_rows = _action_rows(rows)

    assert [row["product_id"] for row in activate_rows] == ["1", "3"]
    assert [row["product_id"] for row in deactivate_rows] == ["4"]


def test_assert_no_drift_accepts_same_action_sets() -> None:
    rows = [
        {
            "planned_action": "add_to_action",
            "product_id": "1",
            "offer_id": "a",
            "calculated_action_price": "100",
        }
    ]

    drift = _assert_no_drift(
        approved_rows=rows,
        fresh_rows=list(rows),
        approved_summary={"action_id": "10"},
        fresh_summary={"action_id": "10"},
    )

    assert drift["approved_activate_count"] == 1
    assert drift["activate_added"] == []


def test_assert_no_drift_blocks_changed_price() -> None:
    approved_rows = [
        {
            "planned_action": "add_to_action",
            "product_id": "1",
            "offer_id": "a",
            "calculated_action_price": "100",
        }
    ]
    fresh_rows = [
        {
            "planned_action": "add_to_action",
            "product_id": "1",
            "offer_id": "a",
            "calculated_action_price": "101",
        }
    ]

    with pytest.raises(RuntimeError, match="drift-check failed"):
        _assert_no_drift(
            approved_rows=approved_rows,
            fresh_rows=fresh_rows,
            approved_summary={"action_id": "10"},
            fresh_summary={"action_id": "10"},
        )


def test_build_partial_drift_plan_applies_unchanged_rows_and_skips_drift() -> None:
    approved_rows = [
        {
            "planned_action": "update_action_price",
            "product_id": "1",
            "offer_id": "same",
            "current_action_price": "90",
            "calculated_action_price": "100",
        },
        {
            "planned_action": "update_action_price",
            "product_id": "2",
            "offer_id": "changed",
            "current_action_price": "90",
            "calculated_action_price": "100",
        },
        {
            "planned_action": "deactivate_from_action",
            "product_id": "3",
            "offer_id": "deactivate",
            "current_action_price": "90",
            "calculated_action_price": "",
        },
    ]
    fresh_rows = [
        {
            "planned_action": "update_action_price",
            "product_id": "1",
            "offer_id": "same",
            "current_action_price": "90",
            "calculated_action_price": "100",
        },
        {
            "planned_action": "update_action_price",
            "product_id": "2",
            "offer_id": "changed",
            "current_action_price": "90",
            "calculated_action_price": "101",
        },
        {
            "planned_action": "deactivate_from_action",
            "product_id": "3",
            "offer_id": "deactivate",
            "current_action_price": "90",
            "calculated_action_price": "",
        },
    ]

    drift, activate_rows, deactivate_rows = _build_partial_drift_plan(
        approved_rows=approved_rows,
        fresh_rows=fresh_rows,
        approved_summary={"action_id": "10"},
        fresh_summary={"action_id": "10"},
    )

    assert drift["mode"] == "partial_apply_unchanged_rows"
    assert [row["product_id"] for row in activate_rows] == ["1"]
    assert [row["product_id"] for row in deactivate_rows] == ["3"]
    assert drift["skipped_due_to_drift_product_ids"] == ["2"]
    assert drift["skipped_due_to_drift_product_count"] == 1
    assert drift["skipped_due_to_drift_count"] == 2
