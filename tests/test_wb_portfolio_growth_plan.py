from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "analytics" / "build_wb_portfolio_growth_plan.py"
SPEC = spec_from_file_location("wb_portfolio_growth_plan", SCRIPT)
assert SPEC and SPEC.loader
MODULE = module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def campaign(*, orders=0, drr=None, clicks=0, atbs=0, views=0, has_bid=True):
    return {
        "orders": orders,
        "drr": drr,
        "clicks": clicks,
        "atbs": atbs,
        "views": views,
        "has_bid": has_bid,
    }


def test_classify_portfolio_segments() -> None:
    assert MODULE.classify(stock=0, campaign=None, organic_sales=0, parser_position=0) == "blocked_no_stock"
    assert MODULE.classify(stock=3, campaign=None, organic_sales=2, parser_position=100) == "blocked_low_stock"
    assert MODULE.classify(stock=10, campaign=None, organic_sales=2, parser_position=0) == "launch_priority"
    assert MODULE.classify(stock=10, campaign=None, organic_sales=0, parser_position=500) == "launch_discovery"
    assert MODULE.classify(
        stock=10,
        campaign=campaign(orders=2, drr=1, has_bid=False),
        organic_sales=2,
        parser_position=500,
    ) == "repair_bid"
    assert MODULE.classify(
        stock=4,
        campaign=campaign(orders=4, drr=1),
        organic_sales=11,
        parser_position=500,
    ) == "replenish_then_scale"
    assert MODULE.classify(
        stock=20,
        campaign=campaign(orders=4, drr=1),
        organic_sales=5,
        parser_position=500,
    ) == "scale_strong"
    assert MODULE.classify(
        stock=20,
        campaign=campaign(orders=1, drr=2),
        organic_sales=1,
        parser_position=500,
    ) == "scale_proven"
    assert MODULE.classify(
        stock=20,
        campaign=campaign(clicks=6, atbs=2, views=300),
        organic_sales=1,
        parser_position=500,
    ) == "conversion_fix"
    assert MODULE.classify(
        stock=20,
        campaign=campaign(clicks=1, views=20),
        organic_sales=1,
        parser_position=500,
    ) == "visibility_test"


def test_target_bid_applies_increase_and_cap() -> None:
    assert MODULE.target_bid(1.10, 0.15) == "1.27"
    assert MODULE.target_bid(2.40, 0.20) == "2.50"


def test_merge_campaign_memberships_keeps_zero_stat_product_in_campaign() -> None:
    campaigns = {}
    MODULE.merge_campaign_memberships(
        campaigns,
        {
            "adverts": [
                {
                    "id": 10,
                    "settings": {"name": "Тест"},
                    "nm_settings": [{"nm_id": 20, "bids_kopecks": {"search": 110}}],
                }
            ]
        },
    )

    assert campaigns["20"]["has_bid"] is True
    assert campaigns["20"]["campaign_ids"] == ["10"]
    assert campaigns["20"]["rows"][0]["current_bid"] == "1.10"


def test_validate_complete_campaign_payload_rejects_partial_snapshot() -> None:
    with pytest.raises(RuntimeError, match="partial"):
        MODULE.validate_complete_campaign_payload(
            {"adverts": [{"id": 10}]},
            {
                "adverts": [
                    {
                        "status": 9,
                        "advert_list": [{"advertId": 10}, {"advertId": 20}],
                    }
                ]
            },
        )
