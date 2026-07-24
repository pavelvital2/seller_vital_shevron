from __future__ import annotations

from dataclasses import dataclass
import time
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

    def fetch_acquiring_reports(
        self,
        *,
        date_from: str,
        date_to: str,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        data = self.post(
            "/api/finance/v1/acquiring/list",
            {
                "dateFrom": date_from,
                "dateTo": date_to,
                "limit": min(max(int(limit), 1), 1000),
                "offset": 0,
            },
        )
        return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []

    def fetch_sales_report_details(
        self,
        *,
        date_from: str,
        date_to: str,
        period: str = "daily",
        fields: list[str] | None = None,
        limit: int = 100000,
        page_delay_seconds: float = 61.0,
    ) -> list[dict[str, Any]]:
        """Fetch period details using rrdId cursor pagination.

        WB limits this endpoint to one request per minute per seller. A normal
        seller report fits into one 100k-row page; the delay is used only when
        another page is actually required.
        """
        rows: list[dict[str, Any]] = []
        normalized_limit = min(max(int(limit), 1), 100000)
        rrd_id = 0

        while True:
            payload: dict[str, Any] = {
                "dateFrom": date_from,
                "dateTo": date_to,
                "limit": normalized_limit,
                "rrdId": rrd_id,
                "period": period,
            }
            if fields:
                payload["fields"] = fields
            data = self.post("/api/finance/v1/sales-reports/detailed", payload)
            page_rows = data if isinstance(data, list) else []
            rows.extend(row for row in page_rows if isinstance(row, dict))
            if len(page_rows) < normalized_limit:
                break
            next_rrd_id = int(page_rows[-1].get("rrdId") or 0) if page_rows else 0
            if not next_rrd_id or next_rrd_id == rrd_id:
                break
            rrd_id = next_rrd_id
            if page_delay_seconds > 0:
                time.sleep(page_delay_seconds)

        return rows
