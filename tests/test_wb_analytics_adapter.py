from seller_agent.config import WbCredentials
from seller_agent.marketplaces.wb.analytics_adapter import WbAnalyticsAdapter


def test_fetch_wb_warehouse_stocks_uses_offset_pagination() -> None:
    calls: list[dict] = []

    class FakeAdapter(WbAnalyticsAdapter):
        def post(self, path: str, payload: dict):
            calls.append({"path": path, "payload": payload})
            if payload["offset"] == 0:
                return {"data": {"items": [{"nmId": 1}, {"nmId": 2}]}}
            return {"data": {"items": [{"nmId": 3}]}}

    adapter = FakeAdapter(WbCredentials(token="test"))
    rows = adapter.fetch_wb_warehouse_stocks(nm_ids=[1, 2, 3], limit=2)

    assert [row["nmId"] for row in rows] == [1, 2, 3]
    assert calls == [
        {
            "path": "/api/analytics/v1/stocks-report/wb-warehouses",
            "payload": {"nmIds": [1, 2, 3], "chrtIds": [], "limit": 2, "offset": 0},
        },
        {
            "path": "/api/analytics/v1/stocks-report/wb-warehouses",
            "payload": {"nmIds": [1, 2, 3], "chrtIds": [], "limit": 2, "offset": 2},
        },
    ]
