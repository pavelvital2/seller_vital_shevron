from seller_agent.tasks.wb_promotion_bid_plan import build_wb_promotion_bid_plan_rows


def test_wb_promotion_bid_plan_uses_current_bid_and_min_floor() -> None:
    product_rows = [
        {
            "advert_id": "101",
            "campaign_name": "Search",
            "status": "active",
            "type": "auction",
            "payment_type": "cpc",
            "bid_type": "manual",
            "nm_id": "1",
            "name": "High DRR",
            "views": "100",
            "clicks": "10",
            "atbs": "4",
            "orders": "1",
            "spend": "40",
            "revenue": "500",
        },
        {
            "advert_id": "101",
            "campaign_name": "Search",
            "status": "active",
            "type": "auction",
            "payment_type": "cpc",
            "bid_type": "manual",
            "nm_id": "2",
            "name": "Good DRR",
            "views": "100",
            "clicks": "10",
            "atbs": "4",
            "orders": "3",
            "spend": "5",
            "revenue": "1000",
        },
        {
            "advert_id": "101",
            "campaign_name": "Search",
            "status": "active",
            "type": "auction",
            "payment_type": "cpc",
            "bid_type": "manual",
            "nm_id": "3",
            "name": "No Orders",
            "views": "100",
            "clicks": "10",
            "atbs": "0",
            "orders": "0",
            "spend": "20",
            "revenue": "0",
        },
    ]
    campaigns = [
        {
            "id": 101,
            "settings": {"payment_type": "cpc", "placements": {"search": True, "recommendations": False}},
            "nm_settings": [
                {"nm_id": 1, "bids_kopecks": {"search": 200, "recommendations": 0}},
                {"nm_id": 2, "bids_kopecks": {"search": 100, "recommendations": 0}},
                {"nm_id": 3, "bids_kopecks": {"search": 100, "recommendations": 0}},
            ],
        }
    ]

    rows, summary = build_wb_promotion_bid_plan_rows(product_rows, campaigns=campaigns)
    by_nm = {row["nm_id"]: row for row in rows}

    assert by_nm["1"]["recommended_action"] == "reduce_bid_or_review"
    assert by_nm["1"]["current_bid"] == "2.00"
    assert by_nm["1"]["target_bid"] == "1.40"
    assert by_nm["1"]["bid_change_amount"] == "-0.60"
    assert by_nm["2"]["recommended_action"] == "scale_candidate"
    assert by_nm["2"]["target_bid"] == "1.20"
    assert by_nm["2"]["bid_change_amount"] == "0.20"
    assert by_nm["3"]["recommended_action"] == "reduce_bid_or_pause"
    assert by_nm["3"]["target_bid"] == "1.00"
    assert by_nm["3"]["bid_change_amount"] == "0.00"
    assert by_nm["3"]["target_adjustment_note"] == "min_bid_floor"
    assert summary["action_rows"] == 3
    assert summary["changed_rows"] == 2
    assert summary["current_bid_matches"] == 3


def test_wb_promotion_bid_plan_filters_inactive_cpm_by_default() -> None:
    product_rows = [
        {
            "advert_id": "101",
            "status": "paused",
            "payment_type": "cpm",
            "nm_id": "1",
            "orders": "0",
            "spend": "100",
            "revenue": "0",
        }
    ]

    rows, summary = build_wb_promotion_bid_plan_rows(product_rows, campaigns=[])

    assert rows == []
    assert summary["eligible_rows"] == 0
