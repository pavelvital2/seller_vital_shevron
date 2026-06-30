from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from ...config import WbCredentials
from ...http import request_json


@dataclass
class WbFbwSuppliesAdapter:
    credentials: WbCredentials
    base_url: str = "https://supplies-api.wildberries.ru"

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": self.credentials.token,
            "Content-Type": "application/json",
        }

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        query = f"?{urlencode(params)}" if params else ""
        return request_json("GET", f"{self.base_url}{path}{query}", headers=self.headers)

    def post(self, path: str, payload: Any, params: dict[str, Any] | None = None) -> Any:
        query = f"?{urlencode(params)}" if params else ""
        return request_json("POST", f"{self.base_url}{path}{query}", headers=self.headers, payload=payload)

    def fetch_supplies(self, *, limit: int = 1000) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        offset = 0
        page_limit = min(max(int(limit), 1), 1000)
        while True:
            data = self.post("/api/v1/supplies", {}, {"limit": page_limit, "offset": offset})
            page = data if isinstance(data, list) else data.get("result") if isinstance(data, dict) else []
            if not isinstance(page, list):
                page = []
            rows.extend(row for row in page if isinstance(row, dict))
            if len(page) < page_limit:
                break
            offset += page_limit
        return rows

    def fetch_supply(self, supply_id: str | int, *, is_preorder_id: bool = False) -> dict[str, Any]:
        data = self.get(
            f"/api/v1/supplies/{supply_id}",
            {"isPreorderID": str(is_preorder_id).lower()},
        )
        return data if isinstance(data, dict) else {}

    def fetch_supply_goods(
        self,
        supply_id: str | int,
        *,
        is_preorder_id: bool = False,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        offset = 0
        page_limit = min(max(int(limit), 1), 1000)
        while True:
            data = self.get(
                f"/api/v1/supplies/{supply_id}/goods",
                {
                    "limit": page_limit,
                    "offset": offset,
                    "isPreorderID": str(is_preorder_id).lower(),
                },
            )
            page = data if isinstance(data, list) else data.get("result") if isinstance(data, dict) else []
            if not isinstance(page, list):
                page = []
            rows.extend(row for row in page if isinstance(row, dict))
            if len(page) < page_limit:
                break
            offset += page_limit
        return rows
