from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from seller_agent.marketplaces.parser_data_api import ParserDataApiClient
from seller_agent.tasks.store_analytics_overview import run_store_analytics_overview


pytestmark = pytest.mark.skipif(
    os.environ.get("VITAL_SHEVRON_RUN_PARSER_LIVE_SMOKE") != "1",
    reason="explicit read-only Parser Data API smoke only",
)
MOSCOW = ZoneInfo("Europe/Moscow")


def _period_runner(**kwargs) -> dict:  # type: ignore[no-untyped-def]
    current = str(kwargs["run_id"]).endswith("_sales_current")
    return {
        "run_id": str(kwargs["run_id"]),
        "started_at": datetime.now(MOSCOW).isoformat(timespec="seconds"),
        "overall_status": "ok",
        "metrics": {
            "orders": 2 if current else 1,
            "order_amount": 200.0 if current else 100.0,
            "returns": 0,
            "net": 100.0 if current else 50.0,
        },
    }


def _stock_runner(**kwargs) -> dict:  # type: ignore[no-untyped-def]
    marketplace = str(kwargs["marketplace"])
    metrics = (
        {"general_fbo": {"present": 1}}
        if marketplace == "ozon"
        else {"stocks": {"quantity": 1}}
    )
    return {
        "run_id": str(kwargs["run_id"]),
        "started_at": datetime.now(MOSCOW).isoformat(timespec="seconds"),
        "overall_status": "ok",
        "metrics": metrics,
    }


@pytest.mark.parametrize("marketplace", ["wb", "ozon"])
def test_parser_moscow_live_read_only_contract(
    tmp_path: Path,
    marketplace: str,
) -> None:
    result = run_store_analytics_overview(
        credentials=object(),
        data_dir=tmp_path,
        marketplace=marketplace,
        period_days=7,
        run_id=f"live-parser-smoke-{marketplace}",
        period_report_runner=_period_runner,
        stock_monitor_runner=_stock_runner,
        parser_client=ParserDataApiClient(),
        wb_supplier_id="4516781",
        ozon_seller_slug="vital-shevron",
        region_id="moscow",
        query_pack_id="shevron-core",
    )

    visibility = result["parser_visibility"]
    assert visibility["data_available"] is True
    assert visibility["region_id"] == "moscow"
    assert visibility["query_pack_id"] == "shevron-core"
    comparison = visibility["comparison"]
    if comparison["available"] is True:
        assert comparison["previous_date"] < comparison["current_date"]
        assert visibility["metrics"]["total_rows"] >= 0
    else:
        assert marketplace == "ozon"
        assert comparison["reason_code"] == "parser_comparison_unavailable"
        assert len(comparison["snapshot_dates"]) == 1
        assert visibility["movements"] == {}
