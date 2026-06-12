from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass
class ApiError(RuntimeError):
    method: str
    url: str
    status: int | None
    message: str

    def __str__(self) -> str:
        status = self.status if self.status is not None else "network"
        return f"{self.method} {self.url} failed ({status}): {self.message}"


def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    payload: Any | None = None,
    timeout: int = 60,
    retries: int = 2,
) -> Any:
    body = None
    request_headers = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")

    last_error: ApiError | None = None
    for attempt in range(retries + 1):
        req = Request(url, data=body, headers=request_headers, method=method.upper())
        try:
            with urlopen(req, timeout=timeout) as response:
                raw = response.read()
                if not raw:
                    return {}
                return json.loads(raw.decode("utf-8"))
        except HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            message = error_body[:1500]
            last_error = ApiError(method.upper(), url, exc.code, message)
            if exc.code not in {429, 500, 502, 503, 504} or attempt >= retries:
                raise last_error from exc
        except (URLError, TimeoutError) as exc:
            last_error = ApiError(method.upper(), url, None, str(exc))
            if attempt >= retries:
                raise last_error from exc

        time.sleep(1.5 * (attempt + 1))

    assert last_error is not None
    raise last_error
