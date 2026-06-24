from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from ...config import WbCredentials
from ...http import request_json


@dataclass
class WbStatisticsAdapter:
    credentials: WbCredentials
    base_url: str = "https://statistics-api.wildberries.ru"

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": self.credentials.token}

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        query = f"?{urlencode(params)}" if params else ""
        return request_json("GET", f"{self.base_url}{path}{query}", headers=self.headers)

    def fetch_orders(self, *, date_from: str, flag: int = 1) -> list[dict[str, Any]]:
        data = self.get("/api/v1/supplier/orders", {"dateFrom": date_from, "flag": flag})
        return data if isinstance(data, list) else []

    def fetch_sales(self, *, date_from: str, flag: int = 1) -> list[dict[str, Any]]:
        data = self.get("/api/v1/supplier/sales", {"dateFrom": date_from, "flag": flag})
        return data if isinstance(data, list) else []

    def fetch_stocks_legacy(self, *, date_from: str) -> list[dict[str, Any]]:
        data = self.get("/api/v1/supplier/stocks", {"dateFrom": date_from})
        return data if isinstance(data, list) else []
