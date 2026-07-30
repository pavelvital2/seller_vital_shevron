from seller_agent.config import WbCredentials
from seller_agent.marketplaces.wb.promotion_adapter import WbPromotionAdapter


def test_update_campaign_products_uses_official_auction_nms_payload(monkeypatch):
    captured = {}

    def fake_request_json(method, url, *, headers, payload):
        captured.update(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "payload": payload,
            }
        )
        return {"ok": True}

    monkeypatch.setattr(
        "seller_agent.marketplaces.wb.promotion_adapter.request_json",
        fake_request_json,
    )
    adapter = WbPromotionAdapter(WbCredentials(token="secret"))

    result = adapter.update_campaign_products(
        advert_id=37041670,
        add=[656160633],
    )

    assert result == {"ok": True}
    assert captured["method"] == "PATCH"
    assert captured["url"].endswith("/adv/v0/auction/nms")
    assert captured["payload"] == {
        "nms": [
            {
                "advert_id": 37041670,
                "nms": {"add": [656160633], "delete": []},
            }
        ]
    }
