from datetime import datetime, timezone

from seller_agent.config import WbCredentials
from seller_agent.marketplaces.wb.documents_adapter import WbDocumentsAdapter
from seller_agent.tasks.ozon_lk_state_monitor import classify_ozon_lk_state
from seller_agent.tasks.ozon_min_price_timer_plan import build_timer_rows
from seller_agent.tasks.ozon_stars_control import stars_fee_breakdown


def test_ozon_lk_state_distinguishes_login_from_cdp_failure() -> None:
    login = {"checks": {"ozon_cdp": {"listening": True}, "ozon_keepalive": {"ok": False, "checks": [{"needsLogin": True}]}}}
    unavailable = {"checks": {"ozon_cdp": {"listening": False}}}
    assert classify_ozon_lk_state(login)[0] == "login_required"
    assert classify_ozon_lk_state(unavailable) == ("unavailable", "cdp_not_listening")


def test_min_price_timer_marks_disabled_missing_and_near_expiry() -> None:
    now = datetime(2026, 7, 31, tzinfo=timezone.utc)
    rows = build_timer_rows(
        ["1", "2", "3", "4"],
        [
            {"product_id": "1", "min_price_for_auto_actions_enabled": True, "expired_at": "2026-08-20T00:00:00Z"},
            {"product_id": "2", "min_price_for_auto_actions_enabled": True, "expired_at": "2026-08-03T00:00:00Z"},
            {"product_id": "3", "min_price_for_auto_actions_enabled": False, "expired_at": "2026-08-20T00:00:00Z"},
        ],
        now=now,
        warning_days=5,
    )
    assert [row["refresh_candidate"] for row in rows] == [False, True, True, True]
    assert rows[3]["reason"] == "disabled"


def test_stars_fee_uses_order_date_after_deactivation() -> None:
    result = stars_fee_breakdown(
        [
            {"operation_type": "StarsMembership", "amount": -9.75, "posting": {"order_date": "2026-07-29T14:30:00Z"}},
            {"operation_type": "StarsMembership", "amount": -8.0, "posting": {"order_date": "2026-07-29T12:00:00Z"}},
            {"operation_type": "Other", "amount": -100},
        ]
    )
    assert result["rows"] == 2
    assert result["amount"] == 17.75
    assert result["post_deactivation_order_rows"] == 1
    assert result["post_deactivation_order_amount"] == 9.75


def test_wb_documents_adapter_paginates_at_official_limit() -> None:
    class FakeAdapter(WbDocumentsAdapter):
        calls: list[dict[str, object]]

        def __init__(self) -> None:
            super().__init__(WbCredentials(token="test"))
            self.calls = []

        def get(self, path: str, params: dict[str, object] | None = None) -> object:
            assert path == "/api/v1/documents/list"
            assert params is not None
            self.calls.append(params)
            offset = int(params["offset"])
            count = 50 if offset == 0 else 1
            return {"data": {"documents": [{"serviceName": f"doc-{offset + index}"} for index in range(count)]}}

    adapter = FakeAdapter()
    rows = adapter.fetch_documents(begin_time="2026-07-19", end_time="2026-07-31", limit=100, page_delay_seconds=0)

    assert len(rows) == 51
    assert [call["limit"] for call in adapter.calls] == [50, 50]
    assert [call["offset"] for call in adapter.calls] == [0, 50]
