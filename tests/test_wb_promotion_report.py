from takterra_agent.tasks.wb_promotion_report import build_wb_promotion_rows


def test_wb_promotion_rows_aggregate_campaigns_and_products() -> None:
    count_data = {
        "adverts": [
            {
                "type": 9,
                "status": 9,
                "advert_list": [{"advertId": 101, "changeTime": "2026-06-12T10:00:00+03:00"}],
            }
        ],
        "all": 1,
    }
    campaigns = [
        {
            "id": 101,
            "bid_type": "manual",
            "status": 9,
            "settings": {"name": "Search", "payment_type": "cpc"},
            "nm_settings": [
                {"nm_id": 1, "bids_kopecks": {"search": 150, "recommendations": 0}},
                {"nm_id": 2, "bids_kopecks": {"search": 200, "recommendations": 0}},
            ],
        }
    ]
    stats_rows = [
        {
            "advertId": 101,
            "views": 100,
            "clicks": 10,
            "atbs": 3,
            "orders": 2,
            "sum": 50,
            "sum_price": 500,
            "days": [
                {
                    "apps": [
                        {
                            "nms": [
                                {
                                    "nmId": 1,
                                    "name": "One",
                                    "views": 80,
                                    "clicks": 8,
                                    "atbs": 2,
                                    "orders": 1,
                                    "sum": 40,
                                    "sum_price": 300,
                                },
                                {
                                    "nmId": 2,
                                    "name": "Two",
                                    "views": 20,
                                    "clicks": 2,
                                    "atbs": 1,
                                    "orders": 1,
                                    "sum": 10,
                                    "sum_price": 200,
                                },
                            ]
                        }
                    ]
                }
            ],
        }
    ]

    campaign_rows, nm_rows, summary = build_wb_promotion_rows(
        count_data=count_data,
        campaigns=campaigns,
        stats_rows=stats_rows,
    )

    assert summary["campaigns_total"] == 1
    assert summary["spend"] == "50.00"
    assert summary["revenue"] == "500.00"
    assert summary["drr_percent"] == "10.00"
    assert summary["roas"] == "10.00"
    assert campaign_rows[0]["search_bid_min"] == "1.50"
    assert campaign_rows[0]["search_bid_max"] == "2.00"
    assert len(nm_rows) == 2
    assert nm_rows[0]["nm_id"] == 1
