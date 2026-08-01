from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any
from urllib.parse import urlencode

from ...config import WbCredentials
from ...http import request_json


@dataclass
class WbDocumentsAdapter:
    credentials: WbCredentials
    base_url: str = "https://documents-api.wildberries.ru"

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": self.credentials.token, "Accept": "application/json"}

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        query = f"?{urlencode(params)}" if params else ""
        return request_json("GET", f"{self.base_url}{path}{query}", headers=self.headers)

    def fetch_categories(self, *, locale: str = "ru") -> list[dict[str, Any]]:
        data = self.get("/api/v1/documents/categories", {"locale": locale})
        rows = (data.get("data") or {}).get("categories") if isinstance(data, dict) else []
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    def fetch_documents(
        self,
        *,
        begin_time: str,
        end_time: str,
        locale: str = "ru",
        limit: int = 50,
        page_delay_seconds: float = 10.1,
    ) -> list[dict[str, Any]]:
        normalized_limit = min(max(int(limit), 1), 50)
        offset = 0
        result: list[dict[str, Any]] = []

        while True:
            data = self.get(
                "/api/v1/documents/list",
                {
                    "locale": locale,
                    "beginTime": begin_time,
                    "endTime": end_time,
                    "sort": "date",
                    "order": "desc",
                    "limit": normalized_limit,
                    "offset": offset,
                },
            )
            rows = (data.get("data") or {}).get("documents") if isinstance(data, dict) else []
            page = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
            result.extend(page)
            if len(page) < normalized_limit:
                break
            offset += normalized_limit
            if page_delay_seconds > 0:
                time.sleep(page_delay_seconds)

        return result
