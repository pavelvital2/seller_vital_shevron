from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from seller_agent.config import OzonPerformanceCredentials
from seller_agent.marketplaces.ozon import performance_adapter
from seller_agent.marketplaces.ozon.performance_adapter import OzonPerformanceAdapter


PLAN_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "analysis"
    / "ozon_dormant_reset_plan.py"
)
PLAN_SPEC = importlib.util.spec_from_file_location("ozon_dormant_reset_plan", PLAN_SCRIPT)
assert PLAN_SPEC and PLAN_SPEC.loader
PLAN_MODULE = importlib.util.module_from_spec(PLAN_SPEC)
PLAN_SPEC.loader.exec_module(PLAN_MODULE)

APPLY_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "analysis"
    / "apply_ozon_dormant_reset.py"
)
APPLY_SPEC = importlib.util.spec_from_file_location("apply_ozon_dormant_reset", APPLY_SCRIPT)
assert APPLY_SPEC and APPLY_SPEC.loader
APPLY_MODULE = importlib.util.module_from_spec(APPLY_SPEC)
APPLY_SPEC.loader.exec_module(APPLY_MODULE)


def _adapter() -> OzonPerformanceAdapter:
    return OzonPerformanceAdapter(
        OzonPerformanceCredentials(client_id="client", client_secret="secret")
    )


def test_add_campaign_products_uses_post(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, object]] = []

    def fake_request_json(
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        payload: object = None,
    ) -> dict[str, object]:
        calls.append((method, url, payload))
        return {}

    monkeypatch.setattr(performance_adapter, "request_json", fake_request_json)
    monkeypatch.setattr(OzonPerformanceAdapter, "_headers", lambda self: {})

    _adapter().add_campaign_products(
        "20233460",
        [{"sku": "2425632140", "bid": "3000000"}],
    )

    assert calls == [
        (
            "POST",
            "https://api-performance.ozon.ru/api/client/campaign/20233460/products",
            {"bids": [{"sku": "2425632140", "bid": "3000000"}]},
        )
    ]


def test_update_campaign_product_bids_keeps_put(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, object]] = []

    def fake_request_json(
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        payload: object = None,
    ) -> dict[str, object]:
        calls.append((method, url, payload))
        return {}

    monkeypatch.setattr(performance_adapter, "request_json", fake_request_json)
    monkeypatch.setattr(OzonPerformanceAdapter, "_headers", lambda self: {})

    _adapter().update_campaign_product_bids(
        "20233460",
        [{"sku": "2425632140", "bid": "3000000"}],
    )

    assert calls[0][0] == "PUT"


def test_remove_campaign_products_uses_delete_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, object]] = []

    def fake_request_json(
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        payload: object = None,
    ) -> dict[str, object]:
        calls.append((method, url, payload))
        return {}

    monkeypatch.setattr(performance_adapter, "request_json", fake_request_json)
    monkeypatch.setattr(OzonPerformanceAdapter, "_headers", lambda self: {})

    _adapter().remove_campaign_products(
        "20233460",
        ["2409630261", "2850099121"],
    )

    assert calls == [
        (
            "POST",
            "https://api-performance.ozon.ru/api/client/campaign/20233460/products/delete",
            {"sku": ["2409630261", "2850099121"]},
        )
    ]


def test_update_campaign_weekly_budget_uses_patch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, object]] = []

    def fake_request_json(
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        payload: object = None,
    ) -> dict[str, object]:
        calls.append((method, url, payload))
        return {}

    monkeypatch.setattr(performance_adapter, "request_json", fake_request_json)
    monkeypatch.setattr(OzonPerformanceAdapter, "_headers", lambda self: {})

    _adapter().update_campaign_weekly_budget("20233460", "844000000000")

    assert calls == [
        (
            "PATCH",
            "https://api-performance.ozon.ru/api/client/campaign/20233460",
            {"weeklyBudget": "844000000000"},
        )
    ]


def test_dormant_plan_preserves_min_price_separately_from_action_price() -> None:
    assert PLAN_MODULE._preserve_min_price("312") == "312.00"
    assert PLAN_MODULE._preserve_min_price("537.00") == "537.00"


def test_dormant_apply_skips_already_verified_elastic_rows() -> None:
    rows = [
        {"product_id": "101", "target_action_price": "312.00"},
        {"product_id": "102", "target_action_price": "537.00"},
    ]
    active = [
        {"product_id": "101", "current_action_price": "312.00"},
        {"product_id": "102", "current_action_price": "550.00"},
    ]

    assert APPLY_MODULE._pending_action_payload(rows, active) == [
        {"product_id": 102, "action_price": 537.0}
    ]
