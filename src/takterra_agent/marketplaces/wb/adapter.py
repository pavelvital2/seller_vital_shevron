from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from ...config import WbCredentials
from ...http import request_json


@dataclass
class WbContentAdapter:
    credentials: WbCredentials
    base_url: str = "https://content-api.wildberries.ru"

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": self.credentials.token,
            "Content-Type": "application/json",
        }

    def post(self, path: str, payload: Any) -> dict[str, Any]:
        return request_json(
            "POST",
            f"{self.base_url}{path}",
            headers=self.headers,
            payload=payload,
        )

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query = f"?{urlencode(params)}" if params else ""
        return request_json("GET", f"{self.base_url}{path}{query}", headers=self.headers)

    def fetch_cards(self, *, limit: int = 100) -> list[dict[str, Any]]:
        cards: list[dict[str, Any]] = []
        cursor: dict[str, Any] = {"limit": limit}
        previous_marker: tuple[str, str] | None = None

        while True:
            payload = {
                "settings": {
                    "sort": {"ascending": True},
                    "cursor": cursor,
                    "filter": {"withPhoto": -1},
                }
            }
            data = self.post("/content/v2/get/cards/list", payload)
            page_cards = data.get("cards") or []
            if not isinstance(page_cards, list):
                page_cards = []
            cards.extend(page_cards)

            response_cursor = data.get("cursor") or {}
            updated_at = str(response_cursor.get("updatedAt") or "")
            nm_id = str(response_cursor.get("nmID") or response_cursor.get("nmId") or "")
            total = int(response_cursor.get("total") or len(page_cards))
            marker = (updated_at, nm_id)

            if not page_cards or total < limit or marker == previous_marker:
                break
            if not updated_at or not nm_id:
                break

            previous_marker = marker
            cursor = {
                "limit": limit,
                "updatedAt": updated_at,
                "nmID": int(nm_id) if nm_id.isdigit() else nm_id,
            }

        return cards

    def fetch_cards_page(self, *, limit: int = 1) -> dict[str, Any]:
        return self.post(
            "/content/v2/get/cards/list",
            {
                "settings": {
                    "sort": {"ascending": True},
                    "cursor": {"limit": limit},
                    "filter": {"withPhoto": -1},
                }
            },
        )

    def find_cards_by_vendor_codes(self, vendor_codes: set[str]) -> dict[str, dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        for vendor_code in sorted(vendor_codes):
            payload = {
                "settings": {
                    "cursor": {"limit": 100},
                    "filter": {"textSearch": vendor_code, "withPhoto": -1},
                }
            }
            data = self.post("/content/v2/get/cards/list", payload)
            for card in data.get("cards") or []:
                if str(card.get("vendorCode")) == vendor_code:
                    found[vendor_code] = card
        return found

    def find_trash_cards_by_vendor_codes(self, vendor_codes: set[str]) -> dict[str, dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        for vendor_code in sorted(vendor_codes):
            payload = {
                "settings": {
                    "cursor": {"limit": 100},
                    "filter": {"textSearch": vendor_code, "withPhoto": -1},
                }
            }
            data = self.post("/content/v2/get/cards/trash", payload)
            for card in data.get("cards") or []:
                if str(card.get("vendorCode")) == vendor_code:
                    found[vendor_code] = card
        return found

    def generate_barcodes(self, count: int) -> list[str]:
        data = self.post("/content/v2/barcodes", {"count": count})
        values = data.get("data") or []
        if not isinstance(values, list):
            return []
        return [str(value).strip() for value in values if str(value).strip()]

    def upload_cards(self, payload: list[dict[str, Any]]) -> dict[str, Any]:
        return self.post("/content/v2/cards/upload", payload)

    def upload_add_cards(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.post("/content/v2/cards/upload/add", payload)

    def fetch_card_errors(self, *, limit: int = 100) -> dict[str, Any]:
        return self.post(
            "/content/v2/cards/error/list",
            {
                "cursor": {"limit": limit},
                "order": {"ascending": False},
            },
        )

    def recover_cards_from_trash(self, nm_ids: list[int]) -> dict[str, Any]:
        return self.post("/content/v2/cards/recover", {"nmIDs": nm_ids})

    def save_media_links(self, *, nm_id: int, urls: list[str]) -> dict[str, Any]:
        return self.post("/content/v3/media/save", {"nmId": nm_id, "data": urls})

    def find_subjects(self, name: str, *, limit: int = 20, locale: str = "ru") -> list[dict[str, Any]]:
        data = self.get(
            "/content/v2/object/all",
            {"name": name, "limit": limit, "locale": locale},
        )
        result = data.get("data") or []
        return result if isinstance(result, list) else []

    def get_subject_characteristics(
        self,
        subject_id: int,
        *,
        locale: str = "ru",
    ) -> list[dict[str, Any]]:
        data = self.get(
            f"/content/v2/object/charcs/{subject_id}",
            {"locale": locale},
        )
        result = data.get("data") or []
        return result if isinstance(result, list) else []
