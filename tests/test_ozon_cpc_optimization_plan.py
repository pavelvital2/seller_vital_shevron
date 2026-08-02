from decimal import Decimal

from seller_agent.tasks.ozon_cpc_optimization_plan import build_cpc_optimization_rows


def test_cpc_optimization_classifies_zero_order_spend_with_cart_for_review() -> None:
    rows, summary = build_cpc_optimization_rows(
        [
            {
                "campaign_id": "1",
                "campaign_title": "CPC",
                "sku": "sku-1",
                "title": "Card",
                "views": "1000",
                "clicks": "60",
                "to_cart": "12",
                "orders": "0",
                "spend": "60",
                "orders_money": "0",
            }
        ]
    )

    assert rows[0]["recommended_action"] == "review_card_then_reduce_or_pause"
    assert rows[0]["bid_reference_type"] == "avg_cpc_from_report"
    assert rows[0]["proposed_bid_change_percent"] == "-30.00"
    assert rows[0]["bid_reference"] == "1.00"
    assert rows[0]["target_bid"] == "0.70"
    assert summary["action_counts"]["review_card_then_reduce_or_pause"] == 1
    assert summary["potential_cut_spend"] == "60.00"


def test_cpc_optimization_uses_current_bid_when_available() -> None:
    rows, summary = build_cpc_optimization_rows(
        [
            {
                "campaign_id": "1",
                "campaign_title": "CPC",
                "sku": "sku-1",
                "title": "Card",
                "views": "1000",
                "clicks": "60",
                "to_cart": "12",
                "orders": "0",
                "spend": "60",
                "orders_money": "0",
            }
        ],
        current_bids={"sku-1": Decimal("2.00")},
    )

    assert rows[0]["bid_reference_type"] == "current_bid_api"
    assert rows[0]["current_bid"] == "2.00"
    assert rows[0]["target_bid"] == "1.40"
    assert rows[0]["bid_change_amount"] == "-0.60"
    assert summary["current_bid_matches"] == 1
    assert summary["apply_payload_rows"] == 1


def test_cpc_candidate_excludes_rows_below_default_apply_floor() -> None:
    rows, summary = build_cpc_optimization_rows(
        [
            {
                "campaign_id": "1",
                "campaign_title": "CPC",
                "sku": "sku-low-bid",
                "title": "Card",
                "views": "1000",
                "clicks": "60",
                "to_cart": "12",
                "orders": "0",
                "spend": "60",
                "orders_money": "0",
            }
        ],
        current_bids={"sku-low-bid": Decimal("0.50")},
    )

    assert rows[0]["recommended_action"] != "keep_monitor"
    assert summary["action_rows_with_current_bid"] == 1
    assert summary["apply_payload_rows"] == 0


def test_cpc_optimization_classifies_high_drr_and_scale_candidates() -> None:
    rows, summary = build_cpc_optimization_rows(
        [
            {
                "campaign_id": "1",
                "campaign_title": "CPC",
                "sku": "bad",
                "title": "Bad",
                "views": "100",
                "clicks": "50",
                "to_cart": "5",
                "orders": "1",
                "spend": "120",
                "orders_money": "600",
            },
            {
                "campaign_id": "1",
                "campaign_title": "CPC",
                "sku": "good",
                "title": "Good",
                "views": "100",
                "clicks": "20",
                "to_cart": "10",
                "orders": "9",
                "spend": "20",
                "orders_money": "1000",
            },
        ]
    )

    actions = {row["sku"]: row["recommended_action"] for row in rows}
    changes = {row["sku"]: row["proposed_bid_change_percent"] for row in rows}
    assert actions["bad"] == "reduce_bid_or_review"
    assert actions["good"] == "scale_candidate"
    assert changes["bad"] == "-40.00"
    assert changes["good"] == "20.00"
    assert summary["action_counts"]["reduce_bid_or_review"] == 1
    assert summary["scale_candidates"] == 1
