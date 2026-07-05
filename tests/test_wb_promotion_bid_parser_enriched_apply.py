from decimal import Decimal

import pytest

from seller_agent.config import AppCredentials
from seller_agent.tasks.registry import get_task_definition
from seller_agent.tasks.wb_promotion_bid_parser_enriched_apply import (
    _enriched_apply_signature,
    _map_enriched_row_for_apply,
    _partial_drift_rows,
    run_wb_promotion_bid_parser_enriched_apply,
)
from seller_agent.tasks.wb_promotion_bids_apply import _split_apply_rows


def test_wb_promotion_parser_enriched_apply_requires_confirmation() -> None:
    with pytest.raises(RuntimeError, match="explicit owner confirmation"):
        run_wb_promotion_bid_parser_enriched_apply(
            credentials=AppCredentials(ozon_seller=None, ozon_performance=None, wb=None),
            confirmed_by_user=False,
        )


def test_enriched_apply_signature_uses_final_target_bid() -> None:
    signature = _enriched_apply_signature(
        {
            "advert_id": "101",
            "nm_id": "1",
            "current_bid_place": "search",
            "current_bid": "2.00",
            "target_bid": "2.20",
            "final_target_bid": "2.40",
            "parser_enriched_action": "apply_ready",
        }
    )

    assert signature == ("101", "1", "search", "2.00", "2.40", "apply_ready")


def test_partial_drift_keeps_unchanged_and_skips_changed_or_new_rows() -> None:
    approved_rows = [
        _row(nm_id="1", current_bid="2.00", final_target_bid="2.40"),
        _row(nm_id="2", current_bid="3.00", final_target_bid="3.30"),
    ]
    fresh_rows = [
        _row(nm_id="1", current_bid="2.00", final_target_bid="2.40"),
        _row(nm_id="2", current_bid="3.20", final_target_bid="3.52"),
        _row(nm_id="3", current_bid="1.00", final_target_bid="1.10"),
    ]

    drift = _partial_drift_rows(approved_rows=approved_rows, fresh_rows=fresh_rows)

    assert [row["nm_id"] for row in drift["unchanged_rows"]] == ["1"]
    assert [row["nm_id"] for row in drift["drift_rows"]] == ["2"]
    assert drift["drift_rows"][0]["skip_reason"] == "drifted_or_missing_in_fresh_plan"
    assert [row["nm_id"] for row in drift["new_rows"]] == ["3"]
    assert drift["new_rows"][0]["skip_reason"] == "new_fresh_row_not_owner_approved"


def test_enriched_rows_map_to_existing_apply_splitter() -> None:
    mapped = _map_enriched_row_for_apply(
        _row(nm_id="1", current_bid="2.00", final_target_bid="2.40", final_bid_change_amount="0.40")
    )

    assert mapped["recommended_action"] == "apply_ready"
    assert mapped["target_bid"] == "2.40"
    assert mapped["bid_change_amount"] == "0.40"

    apply_rows, skipped_rows = _split_apply_rows(
        [mapped],
        allowed_actions={"apply_ready"},
        min_bid=Decimal("1.00"),
    )
    assert skipped_rows == []
    assert apply_rows[0]["target_bid_kopecks"] == 240


def test_task_registry_contains_wb_promotion_parser_enriched_apply() -> None:
    task = get_task_definition("wb-promotion-bids-parser-enriched-apply")

    assert task["command"] == "apply-wb-promotion-bids-parser-enriched"
    assert task["mode"] == "apply"
    assert task["requires_confirmation"] is True
    assert task["requires_mapping"] is True
    assert task["source_plan_task"] == "wb-promotion-bid-parser-enriched-plan"
    assert task["verify_task"] == "wb-promotion-bids-parser-enriched-apply"
    assert task["lock_keys"] == ["marketplace:wb", "ads:wb:promotion-bids"]


def _row(
    *,
    nm_id: str,
    current_bid: str,
    final_target_bid: str,
    final_bid_change_amount: str = "0.10",
) -> dict[str, str]:
    return {
        "advert_id": "101",
        "nm_id": nm_id,
        "parser_enriched_action": "apply_ready",
        "current_bid": current_bid,
        "final_target_bid": final_target_bid,
        "final_bid_change_amount": final_bid_change_amount,
        "current_bid_source": "current_bid_api",
        "current_bid_place": "search",
    }
