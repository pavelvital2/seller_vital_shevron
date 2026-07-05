from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from seller_agent.http import request_json


DEFAULT_PARSER_DATA_API_ENV = Path("/home/pavel/.parser-data-api.env")
DEFAULT_PARSER_DATA_API_BASE_URL = "http://127.0.0.1:8787"


@dataclass(frozen=True, repr=False)
class ParserDataApiConfig:
    base_url: str
    token: str


def load_parser_data_api_config(
    *,
    env_path: Path = DEFAULT_PARSER_DATA_API_ENV,
) -> ParserDataApiConfig:
    values: dict[str, str] = {}
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("'").strip('"')

    base_url = values.get("PARSER_DATA_API_BASE_URL") or DEFAULT_PARSER_DATA_API_BASE_URL
    token = values.get("PARSER_DATA_API_TOKEN") or ""
    return ParserDataApiConfig(base_url=base_url.rstrip("/"), token=token)


class ParserDataApiClient:
    def __init__(self, config: ParserDataApiConfig | None = None) -> None:
        self.config = config or load_parser_data_api_config()

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        normalized_path = path if path.startswith("/") else f"/{path}"
        query = urlencode({key: value for key, value in (params or {}).items() if value not in (None, "")})
        url = f"{self.config.base_url}{normalized_path}"
        if query:
            url = f"{url}?{query}"
        headers: dict[str, str] = {}
        if self.config.token:
            headers["Authorization"] = f"Bearer {self.config.token}"
        return request_json("GET", url, headers=headers, timeout=60, retries=1)
