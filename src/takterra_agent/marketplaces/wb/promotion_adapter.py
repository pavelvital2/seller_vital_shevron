from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from ...config import WbCredentials
from ...http import request_json


@dataclass
class WbPromotionAdapter:
    credentials: WbCredentials
    base_url: str = "https://advert-api.wildberries.ru"

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": self.credentials.token,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        query = f"?{urlencode(params)}" if params else ""
        return request_json("GET", f"{self.base_url}{path}{query}", headers=self.headers)

    def patch(self, path: str, payload: dict[str, Any]) -> Any:
        return request_json("PATCH", f"{self.base_url}{path}", headers=self.headers, payload=payload)

    def fetch_campaign_count(self) -> dict[str, Any]:
        data = self.get("/adv/v1/promotion/count")
        return data if isinstance(data, dict) else {}

    def fetch_campaigns(
        self,
        *,
        ids: list[int],
        statuses: list[int] | None = None,
        payment_type: str | None = None,
    ) -> list[dict[str, Any]]:
        campaigns: list[dict[str, Any]] = []
        for offset in range(0, len(ids), 50):
            chunk = ids[offset : offset + 50]
            params: dict[str, Any] = {"ids": ",".join(str(value) for value in chunk)}
            if statuses:
                params["statuses"] = ",".join(str(value) for value in statuses)
            if payment_type:
                params["payment_type"] = payment_type
            data = self.get("/api/advert/v2/adverts", params)
            adverts = data.get("adverts") if isinstance(data, dict) else []
            campaigns.extend([row for row in adverts or [] if isinstance(row, dict)])
        return campaigns

    def fetch_fullstats(self, *, ids: list[int], date_from: str, date_to: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for offset in range(0, len(ids), 50):
            chunk = ids[offset : offset + 50]
            data = self.get(
                "/adv/v3/fullstats",
                {
                    "ids": ",".join(str(value) for value in chunk),
                    "beginDate": date_from,
                    "endDate": date_to,
                },
            )
            rows.extend([row for row in data or [] if isinstance(row, dict)])
        return rows

    def fetch_balance(self) -> dict[str, Any]:
        data = self.get("/adv/v1/balance")
        return data if isinstance(data, dict) else {}

    def update_bids(self, bids: list[dict[str, Any]]) -> dict[str, Any]:
        data = self.patch("/api/advert/v1/bids", {"bids": bids})
        return data if isinstance(data, dict) else {}
