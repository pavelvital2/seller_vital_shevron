from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...config import OzonPerformanceCredentials
from ...http import request_json


@dataclass
class OzonPerformanceAdapter:
    credentials: OzonPerformanceCredentials
    base_url: str = "https://api-performance.ozon.ru"

    def _access_token(self) -> str:
        data = request_json(
            "POST",
            f"{self.base_url}/api/client/token",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            payload={
                "client_id": self.credentials.client_id,
                "client_secret": self.credentials.client_secret,
                "grant_type": "client_credentials",
            },
        )
        token = str(data.get("access_token") or "")
        if not token:
            raise RuntimeError("Ozon Performance API did not return access_token")
        return token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def get(self, path: str) -> Any:
        return request_json("GET", f"{self.base_url}{path}", headers=self._headers())

    def post(self, path: str, payload: Any) -> Any:
        return request_json("POST", f"{self.base_url}{path}", headers=self._headers(), payload=payload)

    def put(self, path: str, payload: Any) -> Any:
        return request_json("PUT", f"{self.base_url}{path}", headers=self._headers(), payload=payload)

    def patch(self, path: str, payload: Any) -> Any:
        return request_json("PATCH", f"{self.base_url}{path}", headers=self._headers(), payload=payload)

    def fetch_access_token_metadata(self) -> dict[str, Any]:
        data = request_json(
            "POST",
            f"{self.base_url}/api/client/token",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            payload={
                "client_id": self.credentials.client_id,
                "client_secret": self.credentials.client_secret,
                "grant_type": "client_credentials",
            },
        )
        return {
            "has_access_token": bool(data.get("access_token")),
            "token_type": data.get("token_type", ""),
            "expires_in": data.get("expires_in"),
        }

    def fetch_campaign_products(self, campaign_id: str, *, page_size: int = 100) -> list[dict[str, Any]]:
        products: list[dict[str, Any]] = []
        page = 1
        while True:
            data = self.get(f"/api/client/campaign/{campaign_id}/v2/products?page={page}&pageSize={page_size}")
            page_products = data.get("products") if isinstance(data, dict) else None
            if not page_products:
                break
            products.extend([row for row in page_products if isinstance(row, dict)])
            if len(page_products) < page_size:
                break
            page += 1
        return products

    def update_campaign_product_bids(self, campaign_id: str, bids: list[dict[str, str]]) -> Any:
        response = self.put(f"/api/client/campaign/{campaign_id}/products", {"bids": bids})
        if isinstance(response, dict) and int(response.get("status") or 0) >= 400:
            raise RuntimeError(f"Ozon Performance API bid update failed: {response.get('message') or response}")
        return response

    def add_campaign_products(self, campaign_id: str, bids: list[dict[str, str]]) -> Any:
        response = self.post(f"/api/client/campaign/{campaign_id}/products", {"bids": bids})
        if isinstance(response, dict) and int(response.get("status") or 0) >= 400:
            raise RuntimeError(f"Ozon Performance API product add failed: {response.get('message') or response}")
        return response

    def remove_campaign_products(self, campaign_id: str, skus: list[str]) -> Any:
        response = self.post(
            f"/api/client/campaign/{campaign_id}/products/delete",
            {"sku": skus},
        )
        if isinstance(response, dict) and int(response.get("status") or 0) >= 400:
            raise RuntimeError(
                f"Ozon Performance API product removal failed: "
                f"{response.get('message') or response}"
            )
        return response

    def update_campaign_weekly_budget(self, campaign_id: str, weekly_budget: str) -> Any:
        response = self.patch(
            f"/api/client/campaign/{campaign_id}",
            {"weeklyBudget": weekly_budget},
        )
        if isinstance(response, dict) and int(response.get("status") or 0) >= 400:
            raise RuntimeError(
                f"Ozon Performance API weekly budget update failed: "
                f"{response.get('message') or response}"
            )
        return response
