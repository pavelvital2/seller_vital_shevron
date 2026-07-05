from __future__ import annotations

from seller_agent.tasks.ozon_actions_optimizer_apply import _build_partial_drift_plan


def test_ozon_actions_optimizer_drift_keeps_only_unchanged_apply_rows() -> None:
    approved_rows = [
        {
            "recommended_action": "add_to_best_action",
            "product_id": "101",
            "offer_id": "offer101",
            "action_id": "1",
            "target_action_price": "600",
            "current_active_action_id": "",
        },
        {
            "recommended_action": "switch_to_better_action_review",
            "product_id": "102",
            "offer_id": "offer102",
            "action_id": "2",
            "target_action_price": "580",
            "current_active_action_id": "1",
        },
    ]
    fresh_rows = [
        {
            "recommended_action": "add_to_best_action",
            "product_id": "101",
            "offer_id": "offer101",
            "action_id": "1",
            "target_action_price": "600",
            "current_active_action_id": "",
        },
        {
            "recommended_action": "switch_to_better_action_review",
            "product_id": "102",
            "offer_id": "offer102",
            "action_id": "2",
            "target_action_price": "570",
            "current_active_action_id": "1",
        },
    ]

    drift, eligible_rows = _build_partial_drift_plan(approved_rows=approved_rows, fresh_rows=fresh_rows)

    assert len(eligible_rows) == 1
    assert eligible_rows[0]["product_id"] == "101"
    assert drift["skipped_due_to_drift_count"] == 2
    assert drift["skipped_due_to_drift_product_ids"] == ["102"]
