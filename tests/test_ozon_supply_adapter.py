from __future__ import annotations

from seller_agent.config import OzonSellerCredentials
from seller_agent.marketplaces.ozon.adapter import OzonSellerAdapter


def _adapter() -> OzonSellerAdapter:
    return OzonSellerAdapter(OzonSellerCredentials(client_id="test", api_key="test"))


def test_fetch_supply_order_ids_reads_top_level_and_uses_last_id() -> None:
    adapter = _adapter()
    calls: list[dict] = []

    def fake_post(path, payload):  # type: ignore[no-untyped-def]
        calls.append({"path": path, "payload": payload})
        if len(calls) == 1:
            return {"order_ids": ["1", "2"], "last_id": "page-2"}
        return {"order_ids": ["3"], "last_id": ""}

    adapter.post = fake_post  # type: ignore[method-assign]

    result = adapter.fetch_supply_order_ids(states=["COMPLETED"], limit=2)

    assert result == ["1", "2", "3"]
    assert calls[0]["payload"]["last_id"] == ""
    assert calls[1]["payload"]["last_id"] == "page-2"
    assert "offset" not in calls[0]["payload"]


def test_fetch_supply_orders_reads_current_top_level_response() -> None:
    adapter = _adapter()
    adapter.post = lambda path, payload: {"orders": [{"order_id": value} for value in payload["order_ids"]]}  # type: ignore[method-assign]

    result = adapter.fetch_supply_orders(["1", "2"])

    assert result == [{"order_id": "1"}, {"order_id": "2"}]


def test_fetch_supply_order_bundle_uses_bundle_ids_and_current_pagination() -> None:
    adapter = _adapter()
    calls: list[dict] = []

    def fake_post(path, payload):  # type: ignore[no-untyped-def]
        calls.append({"path": path, "payload": payload})
        if len(calls) == 1:
            return {"items": [{"offer_id": "a"}], "last_id": "next", "has_next": True}
        return {"items": [{"offer_id": "b"}], "last_id": "done", "has_next": False}

    adapter.post = fake_post  # type: ignore[method-assign]

    result = adapter.fetch_supply_order_bundle("bundle-1", limit=1000)

    assert result == [{"offer_id": "a"}, {"offer_id": "b"}]
    assert calls[0]["payload"] == {"bundle_ids": ["bundle-1"], "limit": 100}
    assert calls[1]["payload"]["last_id"] == "next"


def test_fetch_fbo_clusters_reads_v2_cluster_list() -> None:
    adapter = _adapter()
    calls: list[dict] = []

    def fake_post(path, payload):  # type: ignore[no-untyped-def]
        calls.append({"path": path, "payload": payload})
        return {"result": [{"macrolocal_cluster_id": 4002, "data": {}}]}

    adapter.post = fake_post  # type: ignore[method-assign]

    assert adapter.fetch_fbo_clusters() == [{"macrolocal_cluster_id": 4002, "data": {}}]
    assert calls == [{"path": "/v2/cluster/list", "payload": {}}]
