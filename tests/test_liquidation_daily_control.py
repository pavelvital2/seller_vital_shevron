from __future__ import annotations

from datetime import date
from decimal import Decimal

from seller_agent.tasks.liquidation_daily_control import (
    WB_STAGE2_APPLIED_NM_IDS,
    WB_STAGE2_COMPLETED_NM_IDS,
    _ozon_ad_rows,
    _ozon_elastic_active,
    _ozon_seller_orders,
    _wb_active_cpc_nm_ids,
    _wb_seller_orders,
    build_liquidation_rows,
)


def test_verified_stage2_monitoring_cohort_contains_exact_applied_rows() -> None:
    assert len(WB_STAGE2_APPLIED_NM_IDS) == 17
    assert 707892598 in WB_STAGE2_APPLIED_NM_IDS
    assert 690790447 not in WB_STAGE2_APPLIED_NM_IDS
    assert len(WB_STAGE2_COMPLETED_NM_IDS) == 18
    assert 690790447 in WB_STAGE2_COMPLETED_NM_IDS


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


def test_wb_stage2_order_window_excludes_orders_before_full_price_day() -> None:
    rows = [
        {"nmId": 100, "date": "2026-08-01T20:00:00", "finishedPrice": 630, "isCancel": False},
        {"nmId": 100, "date": "2026-08-02T10:00:00", "finishedPrice": 630, "isCancel": False},
    ]

    totals = _wb_seller_orders(rows, {100}, date_from=date(2026, 8, 2))

    assert totals == {100: {"units": 1, "value": Decimal("630")}}


def test_ozon_daily_ad_rows_normalize_localized_report_date() -> None:
    totals, daily = _ozon_ad_rows(
        report={
            "campaign-1": {
                "report": {
                    "rows": [
                        {
                            "date": "30.07.2026",
                            "sku": "1001",
                            "views": "10",
                            "clicks": "2",
                            "orders": "1",
                            "moneySpent": "3,50",
                            "ordersMoney": "530,00",
                        }
                    ]
                }
            }
        },
        campaign_ids={"campaign-1"},
    )

    assert daily[("1001", "2026-07-30")]["views"] == 10
    assert daily[("1001", "2026-07-30")]["spend"] == Decimal("3.50")
    assert totals["1001"]["orders"] == 1


def test_ozon_seller_orders_use_stable_sku_after_offer_id_change() -> None:
    totals = _ozon_seller_orders(
        [
            {
                "status": "awaiting_deliver",
                "products": [
                    {
                        "offer_id": "new-offer",
                        "sku": 1001,
                        "quantity": 2,
                        "price": "412.00",
                    }
                ],
            }
        ],
        {"1001"},
    )

    assert totals == {"1001": {"units": 2, "value": Decimal("824.00")}}


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
                "target_discount": "70",
            },
        ],
        ozon_stocks=[
            {"product_id": 101, "offer_id": "new-offer-1", "stocks": [{"type": "fbo", "present": 3, "reserved": 1}]},
            {"product_id": 102, "offer_id": "new-offer-2", "stocks": [{"type": "fbo", "present": 4, "reserved": 0}]},
        ],
        ozon_prices=[
            {"product_id": 101, "offer_id": "new-offer-1", "price": {"min_price": "312"}},
            {"product_id": 102, "offer_id": "new-offer-2", "price": {"min_price": "530"}},
        ],
        ozon_current_cpc_skus={"1001", "1002"},
        ozon_ad_totals={
            "1001": {"views": 100, "clicks": 12, "to_cart": 0, "orders": 0, "spend": Decimal("50"), "revenue": Decimal("0")},
            "1002": {"views": 100, "clicks": 15, "to_cart": 1, "orders": 0, "spend": Decimal("110"), "revenue": Decimal("0")},
        },
        ozon_orders={"1002": {"units": 1, "value": Decimal("811")}},
        wb_stocks=[
            {"nmId": 2001, "quantity": 5, "inWayToClient": 1, "inWayFromClient": 0},
            {"nmId": 2002, "quantity": 6, "inWayToClient": 0, "inWayFromClient": 1},
        ],
        wb_prices=[
            {"nmID": 2001, "discount": 50},
            {"nmID": 2002, "discount": 70},
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
    assert ozon_rows[0]["stop_action_required"] is True
    assert ozon_rows[1]["hard_stop_reached"] is False
    assert ozon_rows[0]["source_offer_id"] == "offer-1"
    assert ozon_rows[0]["offer_id"] == "new-offer-1"
    assert ozon_rows[0]["stock_fbo"] == 3
    assert ozon_rows[0]["cohort_status"] == "approved_liquidation_cohort"
    assert ozon_rows[1]["min_price_document_mismatch"] is True
    assert wb_rows[0]["hard_stop_reached"] is True
    assert wb_rows[0]["stop_action_required"] is True
    assert wb_rows[1]["hard_stop_reached"] is False
    assert wb_rows[1]["second_price_stage_configured"] is True
    assert wb_rows[1]["second_price_stage_applied"] is False
    assert wb_rows[1]["requires_second_price_stage"] is True
    assert wb_rows[1]["action_state"] == "not_historically_completed"
    assert {(row["marketplace"], str(row["product_id"])) for row in stop_rows} == {
        ("ozon", "101"),
        ("wb", "2001"),
    }
    ozon_stop = next(row for row in stop_rows if row["marketplace"] == "ozon")
    assert ozon_stop["ozon_sku"] == "1001"
    assert ozon_rows[1]["min_price_actionable_mismatch"] is False


def test_hard_stop_already_removed_from_cpc_does_not_create_repeat_action() -> None:
    ozon_rows, wb_rows, stop_rows = build_liquidation_rows(
        ozon_cohort=[{"offer_id": "oz", "product_id": "101", "ozon_sku": "1001", "pack_qty": "1", "target_min_price": "530"}],
        wb_cohort=[{"nm_id": "2001", "internal_sku": "wb", "post_apply_click_stop": "10", "post_apply_spend_stop": "20"}],
        ozon_stocks=[],
        ozon_prices=[],
        ozon_current_cpc_skus=set(),
        ozon_ad_totals={"1001": {"views": 100, "clicks": 20, "to_cart": 0, "orders": 0, "spend": Decimal("60"), "revenue": Decimal("0")}},
        ozon_orders={},
        wb_stocks=[],
        wb_prices=[],
        wb_active_cpc_nm_ids=set(),
        wb_ad_totals={2001: {"views": 100, "clicks": 15, "to_cart": 0, "orders": 0, "spend": Decimal("30"), "revenue": Decimal("0")}},
        wb_orders={},
    )

    assert ozon_rows[0]["hard_stop_reached"] is True
    assert ozon_rows[0]["stop_action_required"] is False
    assert ozon_rows[0]["decision"] == "hard_stop_already_inactive"
    assert wb_rows[0]["hard_stop_reached"] is True
    assert wb_rows[0]["stop_action_required"] is False
    assert wb_rows[0]["decision"] == "hard_stop_already_inactive"
    assert stop_rows == []


def test_wb_stage2_verified_history_is_not_reported_as_pending_after_later_discount_drift() -> None:
    nm_id = next(iter(WB_STAGE2_APPLIED_NM_IDS))
    _, rows, _ = build_liquidation_rows(
        ozon_cohort=[],
        wb_cohort=[
            {
                "nm_id": str(nm_id),
                "internal_sku": "stage2",
                "requires_second_price_stage": "True",
                "target_discount": "70",
                "post_apply_click_stop": "10",
                "post_apply_spend_stop": "20",
            }
        ],
        ozon_stocks=[],
        ozon_prices=[],
        ozon_current_cpc_skus=set(),
        ozon_ad_totals={},
        ozon_orders={},
        wb_stocks=[],
        wb_prices=[{"nmID": nm_id, "discount": 60}],
        wb_active_cpc_nm_ids=set(),
        wb_ad_totals={},
        wb_orders={},
    )

    assert rows[0]["second_price_stage_applied"] is True
    assert rows[0]["requires_second_price_stage"] is False
    assert rows[0]["second_price_stage_live_drift"] is True
    assert rows[0]["action_state"] == "applied_then_overwritten"
