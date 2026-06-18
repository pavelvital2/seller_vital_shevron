from decimal import Decimal

import pytest

from seller_agent.config import AppCredentials
from seller_agent.tasks.wb_promotion_bids_apply import (
    _group_bid_payload,
    _split_apply_rows,
    run_wb_promotion_bids_apply,
)


def test_wb_promotion_bids_apply_requires_confirmation() -> None:
    with pytest.raises(RuntimeError, match="explicit owner confirmation"):
        run_wb_promotion_bids_apply(
            credentials=AppCredentials(ozon_seller=None, ozon_performance=None, wb=None),
            confirmed_by_user=False,
        )


def test_split_apply_rows_only_allows_selected_ready_rows() -> None:
    rows = [
        {
            "advert_id": "101",
            "nm_id": "1",
            "recommended_action": "scale_candidate",
            "current_bid": "1.00",
            "target_bid": "1.10",
            "current_bid_source": "current_bid_api",
            "current_bid_place": "search",
        },
        {
            "advert_id": "101",
            "nm_id": "2",
            "recommended_action": "reduce_bid_or_review",
            "current_bid": "2.00",
            "target_bid": "1.80",
            "current_bid_source": "current_bid_api",
            "current_bid_place": "search",
        },
        {
            "advert_id": "101",
            "nm_id": "3",
            "recommended_action": "scale_candidate",
            "current_bid": "",
            "target_bid": "",
            "current_bid_source": "missing_current_bid",
            "current_bid_place": "",
        },
    ]

    apply_rows, skipped_rows = _split_apply_rows(
        rows,
        allowed_actions={"scale_candidate"},
        min_bid=Decimal("1.00"),
    )

    assert [row["nm_id"] for row in apply_rows] == ["1"]
    assert apply_rows[0]["target_bid_kopecks"] == 110
    assert [row["nm_id"] for row in skipped_rows] == ["2", "3"]
    assert skipped_rows[0]["skip_reason"] == "action not selected for this apply"
    assert skipped_rows[1]["skip_reason"] == "missing current_bid or target_bid"


def test_group_bid_payload_groups_rows_by_campaign() -> None:
    payload = _group_bid_payload(
        [
            {
                "advert_id": "101",
                "nm_id": "1",
                "target_bid_kopecks": 110,
                "current_bid_place": "search",
            },
            {
                "advert_id": "101",
                "nm_id": "2",
                "target_bid_kopecks": 120,
                "current_bid_place": "search",
            },
            {
                "advert_id": "102",
                "nm_id": "3",
                "target_bid_kopecks": 130,
                "current_bid_place": "recommendations",
            },
        ]
    )

    assert payload == [
        {
            "advert_id": 101,
            "nm_bids": [
                {"nm_id": 1, "bid_kopecks": 110, "placement": "search"},
                {"nm_id": 2, "bid_kopecks": 120, "placement": "search"},
            ],
        },
        {
            "advert_id": 102,
            "nm_bids": [{"nm_id": 3, "bid_kopecks": 130, "placement": "recommendations"}],
        },
    ]
