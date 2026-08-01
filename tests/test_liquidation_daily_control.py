from __future__ import annotations

from decimal import Decimal

from seller_agent.tasks.liquidation_daily_control import (
    _ozon_elastic_active,
    _wb_active_cpc_nm_ids,
    build_liquidation_rows,
)


def test_liquidation_control_reads_current_ozon_actions_and_wb_campaign_membership() -> None:
    assert _ozon_elastic_active(
        {"marketing_actions": {"actions": [{"title": "Эластичный бустинг", "value": 382}]}}
    ) is True
    assert _wb_active_cpc_nm_ids(
        {
            "adverts": [
                {
                    "nm_settings": [
                        {"nm_id": 100, "bids_kopecks": {"search": 210}},
                        {"nm_id": 101, "bids_kopecks": {"search": 0}},
                    ]
                }
            ]
        }
    ) == {100}


def test_build_liquidation_rows_keeps_zero_rows_and_marks_only_seller_order_free_stops() -> None:
    ozon_rows, wb_rows, stop_rows = build_liquidation_rows(
        ozon_cohort=[
            {
                "decision_group": "1",
                "offer_id": "offer-1",
                "product_id": "101",
                "ozon_sku": "1001",
                "title": "Single",
                "pack_qty": "1",
                "target_min_price": "312",
            },
            {
                "decision_group": "2",
                "offer_id": "offer-2",
                "product_id": "102",
                "ozon_sku": "1002",
                "title": "Pack",
                "pack_qty": "2",
                "target_min_price": "537",
            },
        ],
        wb_cohort=[
            {
                "group": "weak_clearance",
                "nm_id": "2001",
                "internal_sku": "wb-1",
                "title": "WB one",
                "pack_qty": "1",
                "post_apply_click_stop": "10",
                "post_apply_spend_stop": "20",
            },
            {
                "group": "weak_clearance",
                "nm_id": "2002",
                "internal_sku": "wb-2",
                "title": "WB two",
                "pack_qty": "2",
                "post_apply_click_stop": "10",
                "post_apply_spend_stop": "20",
                "requires_second_price_stage": "True",
            },
        ],
        ozon_stocks=[
            {"offer_id": "offer-1", "stocks": [{"type": "fbo", "present": 3, "reserved": 1}]},
            {"offer_id": "offer-2", "stocks": [{"type": "fbo", "present": 4, "reserved": 0}]},
        ],
        ozon_prices=[
            {"offer_id": "offer-1", "price": {"min_price": "312"}},
            {"offer_id": "offer-2", "price": {"min_price": "530"}},
        ],
        ozon_current_cpc_skus={"1001", "1002"},
        ozon_ad_totals={
            "1001": {"views": 100, "clicks": 12, "to_cart": 0, "orders": 0, "spend": Decimal("50"), "revenue": Decimal("0")},
            "1002": {"views": 100, "clicks": 15, "to_cart": 1, "orders": 0, "spend": Decimal("110"), "revenue": Decimal("0")},
        },
        ozon_orders={"offer-2": {"units": 1, "value": Decimal("811")}},
        wb_stocks=[
            {"nmId": 2001, "quantity": 5, "inWayToClient": 1, "inWayFromClient": 0},
            {"nmId": 2002, "quantity": 6, "inWayToClient": 0, "inWayFromClient": 1},
        ],
        wb_active_cpc_nm_ids={2001},
        wb_ad_totals={
            2001: {"views": 40, "clicks": 10, "to_cart": 0, "orders": 0, "spend": Decimal("10"), "revenue": Decimal("0")},
            2002: {"views": 50, "clicks": 12, "to_cart": 1, "orders": 0, "spend": Decimal("25"), "revenue": Decimal("0")},
        },
        wb_orders={2002: {"units": 1, "value": Decimal("500")}},
    )

    assert len(ozon_rows) == 2
    assert len(wb_rows) == 2
    assert ozon_rows[0]["hard_stop_reached"] is True
    assert ozon_rows[1]["hard_stop_reached"] is False
    assert ozon_rows[1]["min_price_document_mismatch"] is True
    assert wb_rows[0]["hard_stop_reached"] is True
    assert wb_rows[1]["hard_stop_reached"] is False
    assert wb_rows[1]["requires_second_price_stage"] is True
    assert {(row["marketplace"], str(row["product_id"])) for row in stop_rows} == {
        ("ozon", "1001"),
        ("wb", "2001"),
    }
