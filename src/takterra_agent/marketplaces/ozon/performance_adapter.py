from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...config import OzonPerformanceCredentials
from ...http import request_json


@dataclass
class OzonPerformanceAdapter:
    credentials: OzonPerformanceCredentials
    base_url: str = "https://api-performance.ozon.ru"

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

