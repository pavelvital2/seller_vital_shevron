from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from ...config import WbCredentials
from ...http import request_json


@dataclass
class WbPricesAdapter:
    credentials: WbCredentials
    base_url: str = "https://discounts-prices-api.wildberries.ru"

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": self.credentials.token,
            "Content-Type": "application/json",
        }

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query = f"?{urlencode(params)}" if params else ""
        return request_json("GET", f"{self.base_url}{path}{query}", headers=self.headers)

    def post(self, path: str, payload: Any) -> dict[str, Any]:
        return request_json(
            "POST",
            f"{self.base_url}{path}",
            headers=self.headers,
            payload=payload,
        )

    def fetch_goods_prices(
        self,
        *,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page_limit = min(max(int(limit), 1), 1000)
        offset = 0

        while True:
            data = self.get(
                "/api/v2/list/goods/filter",
                {"limit": page_limit, "offset": offset},
            )
            page_items = _extract_goods(data)
            rows.extend(page_items)
            if len(page_items) < page_limit:
                break
            offset += page_limit

        return rows


def _extract_goods(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("listGoods", "goods", "items", "rows"):
        value = data.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    for key in ("data", "result"):
        nested = data.get(key)
        if isinstance(nested, list):
            return [row for row in nested if isinstance(row, dict)]
        if isinstance(nested, dict):
            rows = _extract_goods(nested)
            if rows:
                return rows
    return []
