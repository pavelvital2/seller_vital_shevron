from decimal import Decimal

import pytest

from takterra_agent.config import AppCredentials
from takterra_agent.tasks.ozon_cpc_bids_apply import _split_apply_rows, run_ozon_cpc_bids_apply


def test_ozon_cpc_bids_apply_requires_confirmation() -> None:
    with pytest.raises(RuntimeError, match="explicit owner confirmation"):
        run_ozon_cpc_bids_apply(
            credentials=AppCredentials(ozon_seller=None, ozon_performance=None, wb=None),
            confirmed_by_user=False,
        )


def test_split_apply_rows_skips_zero_and_below_minimum() -> None:
    rows = [
        {
            "sku": "one",
            "current_bid": "1.00",
            "target_bid": "0.70",
            "bid_change_amount": "-0.30",
            "bid_reference_type": "current_bid_api",
        },
        {
            "sku": "pause",
            "current_bid": "4.00",
            "target_bid": "0.00",
            "bid_change_amount": "-4.00",
            "bid_reference_type": "current_bid_api",
        },
        {
            "sku": "ok",
            "current_bid": "4.00",
            "target_bid": "2.80",
            "bid_change_amount": "-1.20",
            "bid_reference_type": "current_bid_api",
        },
    ]

    apply_rows, skipped_rows = _split_apply_rows(rows, min_bid=Decimal("1.00"))

    assert [row["sku"] for row in apply_rows] == ["ok"]
    assert apply_rows[0]["raw_target_bid"] == "2800000"
    assert [row["sku"] for row in skipped_rows] == ["one", "pause"]
    assert "below safe minimum" in skipped_rows[0]["skip_reason"]
    assert "disable" in skipped_rows[1]["skip_reason"]
