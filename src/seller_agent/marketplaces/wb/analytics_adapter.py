from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...config import WbCredentials
from ...http import request_json


@dataclass
class WbAnalyticsAdapter:
    credentials: WbCredentials
    base_url: str = "https://seller-analytics-api.wildberries.ru"

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": self.credentials.token,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def post(self, path: str, payload: dict[str, Any]) -> Any:
        return request_json(
            "POST",
            f"{self.base_url}{path}",
            headers=self.headers,
            payload=payload,
        )

    def fetch_wb_warehouse_stocks(
        self,
        *,
        nm_ids: list[int] | None = None,
        chrt_ids: list[int] | None = None,
        limit: int = 250_000,
    ) -> list[dict[str, Any]]:
        normalized_limit = min(max(int(limit), 1), 250_000)
        offset = 0
        rows: list[dict[str, Any]] = []

        while True:
            payload: dict[str, Any] = {
                "nmIds": list(nm_ids or [])[:1000],
                "chrtIds": list(chrt_ids or []),
                "limit": normalized_limit,
                "offset": offset,
            }
            data = self.post("/api/analytics/v1/stocks-report/wb-warehouses", payload)
            page = data.get("data", {}).get("items", []) if isinstance(data, dict) else []
            page_rows = [row for row in page if isinstance(row, dict)]
            rows.extend(page_rows)
            if len(page_rows) < normalized_limit:
                break
            offset += normalized_limit

        return rows
