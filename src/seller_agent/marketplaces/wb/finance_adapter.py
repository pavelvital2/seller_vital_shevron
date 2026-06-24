from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...config import WbCredentials
from ...http import request_json


@dataclass
class WbFinanceAdapter:
    credentials: WbCredentials
    base_url: str = "https://finance-api.wildberries.ru"

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": self.credentials.token,
            "Content-Type": "application/json",
        }

    def post(self, path: str, payload: dict[str, Any]) -> Any:
        return request_json(
            "POST",
            f"{self.base_url}{path}",
            headers=self.headers,
            payload=payload,
        )

    def fetch_sales_reports(
        self,
        *,
        date_from: str,
        date_to: str,
        period: str = "daily",
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        normalized_limit = min(max(int(limit), 1), 1000)
        offset = 0

        while True:
            data = self.post(
                "/api/finance/v1/sales-reports/list",
                {
                    "dateFrom": date_from,
                    "dateTo": date_to,
                    "limit": normalized_limit,
                    "offset": offset,
                    "period": period,
                },
            )
            page_rows = data if isinstance(data, list) else []
            rows.extend(row for row in page_rows if isinstance(row, dict))
            if len(page_rows) < normalized_limit:
                break
            offset += normalized_limit

        return rows
