from datetime import datetime, timezone

import pytest

from seller_agent.config import AppCredentials, OzonSellerCredentials, WbCredentials
from seller_agent.http import ApiError
from seller_agent.marketplaces.wb.documents_adapter import WbDocumentsAdapter
from seller_agent.tasks.ozon_lk_state_monitor import classify_ozon_lk_state
from seller_agent.tasks.ozon_min_price_timer_plan import build_timer_rows
from seller_agent.tasks.ozon_stars_control import (
    retry_ozon_read_after_rate_limit,
    run_ozon_stars_control,
    stars_fee_breakdown,
)


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


def test_stars_read_retries_only_explicit_rate_limit() -> None:
    calls = 0
    sleeps: list[float] = []

    def operation() -> dict[str, bool]:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise ApiError("POST", "https://api-seller.ozon.ru/v1/analytics/data", 429, "rate limit")
        return {"ok": True}

    result, retries = retry_ozon_read_after_rate_limit(
        operation,
        sleep=sleeps.append,
    )

    assert result == {"ok": True}
    assert retries == 2
    assert sleeps == [5.0, 10.0]


def test_stars_read_does_not_retry_non_rate_limit_error() -> None:
    sleeps: list[float] = []

    with pytest.raises(ApiError, match=r"failed \(500\)"):
        retry_ozon_read_after_rate_limit(
            lambda: (_ for _ in ()).throw(
                ApiError("POST", "https://api-seller.ozon.ru/v1/analytics/data", 500, "server error")
            ),
            sleep=sleeps.append,
        )

    assert sleeps == []


def test_stars_control_uses_full_timestamps_for_finance_check(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    from seller_agent.tasks import ozon_stars_control as module

    finance_calls: list[dict[str, str]] = []

    class FakeOzonAdapter:
        def __init__(self, credentials) -> None:  # type: ignore[no-untyped-def]
            self.credentials = credentials

        def fetch_finance_transactions(self, **kwargs):  # type: ignore[no-untyped-def]
            finance_calls.append(kwargs)
            return []

    report_calls = 0

    def fake_period_report(**kwargs):  # type: ignore[no-untyped-def]
        nonlocal report_calls
        report_calls += 1
        return {
            "metrics": {"buyout_units": 10, "gross": 5000},
            "artifacts": {"report": str(tmp_path / f"period-{report_calls}.md")},
        }

    monkeypatch.setattr(module, "OzonSellerAdapter", FakeOzonAdapter)
    monkeypatch.setattr(module, "run_marketplace_period_report", fake_period_report)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)

    result = run_ozon_stars_control(
        credentials=AppCredentials(
            ozon_seller=OzonSellerCredentials(client_id="client", api_key="key"),
            ozon_performance=None,
            wb=None,
        ),
        data_dir=tmp_path,
        window_days=7,
        date_to="2026-08-05",
        run_id="stars_timestamp_test",
    )

    assert result["overall_status"] == "ok"
    assert finance_calls == [
        {
            "date_from": "2026-07-30T00:00:00.000Z",
            "date_to": "2026-08-05T23:59:59.999Z",
        }
    ]


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
