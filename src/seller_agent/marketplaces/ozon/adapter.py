from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from ...config import OzonSellerCredentials
from ...http import request_json


@dataclass
class OzonSellerAdapter:
    credentials: OzonSellerCredentials
    base_url: str = "https://api-seller.ozon.ru"

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Client-Id": self.credentials.client_id,
            "Api-Key": self.credentials.api_key,
            "Content-Type": "application/json",
        }

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return request_json(
            "POST",
            f"{self.base_url}{path}",
            headers=self.headers,
            payload=payload,
        )

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query = f"?{urlencode(params)}" if params else ""
        return request_json("GET", f"{self.base_url}{path}{query}", headers=self.headers)

    def fetch_product_list(self, *, visibility: str = "ALL", limit: int = 1000) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        last_id = ""

        while True:
            payload = {
                "filter": {"visibility": visibility},
                "last_id": last_id,
                "limit": limit,
            }
            data = self.post("/v3/product/list", payload)
            result = data.get("result") or {}
            page_items = result.get("items") or []
            if not isinstance(page_items, list):
                page_items = []

            items.extend(page_items)
            next_last_id = result.get("last_id") or ""
            if not page_items or not next_last_id or next_last_id == last_id:
                break
            last_id = next_last_id

        return items

    def fetch_product_list_page(
        self,
        *,
        visibility: str = "ALL",
        limit: int = 1,
    ) -> dict[str, Any]:
        return self.post(
            "/v3/product/list",
            {
                "filter": {"visibility": visibility},
                "last_id": "",
                "limit": limit,
            },
        )

    def fetch_product_info(
        self,
        product_ids: list[str],
        *,
        batch_size: int = 1000,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        normalized_ids = [int(item) for item in product_ids if str(item).strip().isdigit()]

        for start in range(0, len(normalized_ids), batch_size):
            batch = normalized_ids[start : start + batch_size]
            if not batch:
                continue
            data = self.post("/v3/product/info/list", {"product_id": batch})
            result = data.get("result") or {}
            page_items = data.get("items") or result.get("items") or []
            if isinstance(page_items, list):
                items.extend(page_items)

        return items

    def fetch_product_attributes(
        self,
        offer_ids: list[str],
        *,
        batch_size: int = 100,
        visibility: str = "ALL",
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        normalized_ids = [item for item in offer_ids if str(item).strip()]

        for start in range(0, len(normalized_ids), batch_size):
            batch = normalized_ids[start : start + batch_size]
            if not batch:
                continue
            data = self.post(
                "/v4/product/info/attributes",
                {
                    "filter": {"offer_id": batch, "visibility": visibility},
                    "limit": batch_size,
                    "last_id": "",
                },
            )
            page_items = data.get("result") if isinstance(data.get("result"), list) else []
            if isinstance(page_items, list):
                items.extend(page_items)

        return items

    def fetch_product_descriptions(
        self,
        offer_ids: list[str],
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        normalized_ids = [item for item in offer_ids if str(item).strip()]

        for offer_id in normalized_ids:
            data = self.post("/v1/product/info/description", {"offer_id": str(offer_id)})
            result = data.get("result") if isinstance(data, dict) else {}
            if isinstance(result, dict):
                result.setdefault("offer_id", str(offer_id))
                items.append(result)
            elif isinstance(data, dict):
                data.setdefault("offer_id", str(offer_id))
                items.append(data)

        return items

    def fetch_product_info_prices(
        self,
        *,
        visibility: str = "ALL",
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        cursor = ""
        page_limit = min(max(int(limit), 1), 1000)

        while True:
            payload: dict[str, Any] = {
                "filter": {"visibility": visibility},
                "limit": page_limit,
            }
            if cursor:
                payload["cursor"] = cursor
            else:
                payload["last_id"] = ""

            data = self.post("/v5/product/info/prices", payload)
            page_items = data.get("items") or []
            if not isinstance(page_items, list):
                page_items = []

            items.extend(row for row in page_items if isinstance(row, dict))
            next_cursor = data.get("cursor") or ""
            if not page_items or not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor

        return items

    def fetch_product_stocks(
        self,
        product_ids: list[str],
        *,
        batch_size: int = 100,
        visibility: str = "ALL",
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        normalized_ids = [item for item in product_ids if str(item).strip()]

        for start in range(0, len(normalized_ids), batch_size):
            batch = normalized_ids[start : start + batch_size]
            if not batch:
                continue
            data = self.post(
                "/v4/product/info/stocks",
                {
                    "filter": {"product_id": [str(item) for item in batch], "visibility": visibility},
                    "limit": batch_size,
                },
            )
            result = data.get("result") or {}
            page_items = result.get("items") or result.get("products") or data.get("items") or []
            if isinstance(page_items, list):
                items.extend(page_items)

        return items

    def fetch_fbo_postings(
        self,
        *,
        since: str,
        to: str,
        status: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page_limit = min(max(int(limit), 1), 100)
        offset = 0

        while True:
            data = self.post(
                "/v2/posting/fbo/list",
                {
                    "dir": "ASC",
                    "filter": {
                        "since": since,
                        "status": status,
                        "to": to,
                    },
                    "limit": page_limit,
                    "offset": offset,
                    "translit": True,
                    "with": {
                        "analytics_data": True,
                        "financial_data": True,
                    },
                },
            )
            result = data.get("result") if isinstance(data, dict) else []
            page_items = result.get("postings") if isinstance(result, dict) else result
            if not isinstance(page_items, list):
                page_items = []
            items.extend(row for row in page_items if isinstance(row, dict))
            if len(page_items) < page_limit:
                break
            offset += page_limit

        return items

    def fetch_finance_transactions(
        self,
        *,
        date_from: str,
        date_to: str,
        transaction_type: str = "all",
        operation_type: list[str] | None = None,
        page_size: int = 1000,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page = 1
        normalized_page_size = min(max(int(page_size), 1), 1000)

        while True:
            data = self.post(
                "/v3/finance/transaction/list",
                {
                    "filter": {
                        "date": {
                            "from": date_from,
                            "to": date_to,
                        },
                        "operation_type": operation_type or [],
                        "posting_number": "",
                        "transaction_type": transaction_type,
                    },
                    "page": page,
                    "page_size": normalized_page_size,
                },
            )
            result = data.get("result") if isinstance(data, dict) else {}
            page_items = result.get("operations") if isinstance(result, dict) else []
            if isinstance(page_items, list):
                items.extend(row for row in page_items if isinstance(row, dict))
            page_count = int(result.get("page_count") or 0) if isinstance(result, dict) else 0
            if not page_items or not page_count or page >= page_count:
                break
            page += 1

        return items

    def fetch_review_count(self) -> dict[str, Any]:
        return self.post("/v1/review/count", {})

    def fetch_review_list(
        self,
        *,
        status: str = "ALL",
        limit: int = 100,
        sort_dir: str = "DESC",
    ) -> dict[str, Any]:
        return self.post(
            "/v1/review/list",
            {
                "limit": min(max(int(limit), 1), 100),
                "sort_dir": sort_dir,
                "status": status,
            },
        )

    def fetch_question_count(self) -> dict[str, Any]:
        return self.post("/v1/question/count", {})

    def fetch_question_list(
        self,
        *,
        status: str = "ALL",
        limit: int = 100,
        offset: int = 0,
        sort_dir: str = "DESC",
    ) -> dict[str, Any]:
        return self.post(
            "/v1/question/list",
            {
                "filter": {"status": status},
                "limit": min(max(int(limit), 1), 100),
                "offset": max(int(offset), 0),
                "sort_dir": sort_dir,
            },
        )

    def fetch_analytics_data(
        self,
        *,
        date_from: str,
        date_to: str,
        metrics: list[str],
        dimensions: list[str],
        filters: list[dict[str, Any]] | None = None,
        sort: list[dict[str, Any]] | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> dict[str, Any]:
        return self.post(
            "/v1/analytics/data",
            {
                "date_from": date_from,
                "date_to": date_to,
                "metrics": metrics,
                "dimension": dimensions,
                "filters": filters or [],
                "sort": sort or [],
                "limit": limit,
                "offset": offset,
            },
        )
