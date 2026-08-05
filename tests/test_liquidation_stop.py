from __future__ import annotations

import pytest

from seller_agent.tasks.liquidation_stop import (
    _source_targets,
    _verify_removed,
    _wb_campaign_snapshot,
)
from seller_agent.tasks.registry import default_task_registry


def test_source_targets_keeps_ozon_product_id_and_sku_separate() -> None:
    ozon, wb = _source_targets(
        [
            {
                "marketplace": "ozon",
                "product_id": "2830176606",
                "ozon_sku": "2936229456",
                "offer_id": "pict0170",
            },
            {
                "marketplace": "wb",
                "product_id": "707770409",
                "offer_id": "chev_kit2_nr_rg_pict0012",
            },
        ]
    )

    assert ozon["2936229456"]["product_id"] == "2830176606"
    assert wb[707770409]["offer_id"] == "chev_kit2_nr_rg_pict0012"


@pytest.mark.parametrize(
    "row",
    [
        {"marketplace": "ozon", "product_id": "2830176606"},
        {"marketplace": "ozon", "ozon_sku": "2936229456"},
        {"marketplace": "wb", "product_id": ""},
    ],
)
def test_source_targets_rejects_incomplete_identity(row: dict[str, str]) -> None:
    with pytest.raises(RuntimeError):
        _source_targets([row])


def test_wb_campaign_snapshot_preserves_non_target_bids() -> None:
    snapshot = _wb_campaign_snapshot(
        {
            "nm_settings": [
                {
                    "nm_id": 101,
                    "bids_kopecks": {"search": 300, "recommendations": 0},
                },
                {
                    "nm_id": 202,
                    "bids_kopecks": {"search": 500, "recommendations": 100},
                },
            ]
        }
    )

    assert snapshot == {
        "101": {"search_bid_kopecks": 300, "recommendation_bid_kopecks": 0},
        "202": {"search_bid_kopecks": 500, "recommendation_bid_kopecks": 100},
    }


def test_verify_removed_checks_only_exact_campaign_target() -> None:
    rows = _verify_removed(
        actions=[
            {
                "marketplace": "ozon",
                "campaign_id": "11",
                "ozon_sku": "1001",
            },
            {
                "marketplace": "wb",
                "campaign_id": "22",
                "nm_id": 2002,
            },
        ],
        ozon_campaigns={"11": {"1002": {"bid": "1000000"}}},
        wb_campaigns={"22": {"2001": {"search_bid_kopecks": 300}}},
    )

    assert [row["status"] for row in rows] == ["ok", "ok"]


def test_verify_removed_reports_target_that_is_still_active() -> None:
    rows = _verify_removed(
        actions=[
            {
                "marketplace": "ozon",
                "campaign_id": "11",
                "ozon_sku": "1001",
            }
        ],
        ozon_campaigns={"11": {"1001": {"bid": "1000000"}}},
        wb_campaigns={},
    )

    assert rows[0]["status"] == "still_active"


def test_liquidation_stop_runtime_chain_is_registered() -> None:
    registry = default_task_registry()
    plan = registry.get("liquidation-stop-plan")
    apply = registry.get("liquidation-stop-apply")
    verify = registry.get("liquidation-stop-verify")

    assert plan.mode == "dry_run"
    assert apply.mode == "apply"
    assert apply.requires_confirmation is True
    assert apply.source_plan_task == plan.name
    assert apply.verify_task == verify.name
    assert apply.policy_issues() == []
    assert verify.mode == "verify"
