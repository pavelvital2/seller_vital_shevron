from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(frozen=True)
class OzonSellerCredentials:
    client_id: str
    api_key: str


@dataclass(frozen=True)
class OzonPerformanceCredentials:
    client_id: str
    client_secret: str


@dataclass(frozen=True)
class WbCredentials:
    token: str


@dataclass(frozen=True)
class AppCredentials:
    ozon_seller: OzonSellerCredentials | None
    ozon_performance: OzonPerformanceCredentials | None
    wb: WbCredentials | None


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def read_non_empty_lines(path: str | Path) -> list[str]:
    file_path = Path(path).expanduser()
    return [
        line.strip()
        for line in file_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _first_file_env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value.strip()
    return None


def _value_from_env_or_file(env_name: str, *file_env_names: str) -> str | None:
    value = os.environ.get(env_name)
    if value:
        return value.strip()

    file_name = _first_file_env(*file_env_names)
    if not file_name:
        return None

    if not Path(file_name).expanduser().exists():
        return None

    lines = read_non_empty_lines(file_name)
    return lines[0] if lines else None


def _pair_from_env_or_file(
    first_env_name: str,
    second_env_name: str,
    *file_env_names: str,
) -> tuple[str | None, str | None]:
    first = os.environ.get(first_env_name)
    second = os.environ.get(second_env_name)

    file_name = _first_file_env(*file_env_names)
    if (not first or not second) and file_name:
        if not Path(file_name).expanduser().exists():
            return first, second
        lines = read_non_empty_lines(file_name)
        if len(lines) >= 2:
            first = first or lines[0]
            second = second or lines[1]

    return first, second


def load_ozon_seller_credentials() -> OzonSellerCredentials | None:
    client_id, api_key = _pair_from_env_or_file(
        "OZON_SELLER_CLIENT_ID",
        "OZON_SELLER_API_KEY",
        "VITAL_SHEVRON_OZON_SELLER_CREDENTIALS_FILE",
        "SELLER_OZON_SELLER_CREDENTIALS_FILE",
        "TAKTERRA_OZON_SELLER_CREDENTIALS_FILE",
    )

    if not client_id or not api_key:
        return None

    return OzonSellerCredentials(client_id=client_id.strip(), api_key=api_key.strip())


def load_ozon_performance_credentials() -> OzonPerformanceCredentials | None:
    client_id, client_secret = _pair_from_env_or_file(
        "OZON_PERFORMANCE_CLIENT_ID",
        "OZON_PERFORMANCE_CLIENT_SECRET",
        "VITAL_SHEVRON_OZON_PERFORMANCE_CREDENTIALS_FILE",
        "SELLER_OZON_PERFORMANCE_CREDENTIALS_FILE",
        "TAKTERRA_OZON_PERFORMANCE_CREDENTIALS_FILE",
    )

    if not client_id or not client_secret:
        return None

    return OzonPerformanceCredentials(
        client_id=client_id.strip(),
        client_secret=client_secret.strip(),
    )


def load_wb_credentials() -> WbCredentials | None:
    token = _value_from_env_or_file(
        "WB_API_TOKEN",
        "VITAL_SHEVRON_WB_TOKEN_FILE",
        "SELLER_WB_TOKEN_FILE",
        "TAKTERRA_WB_TOKEN_FILE",
    )
    if not token:
        return None
    return WbCredentials(token=token.strip())


def load_credentials() -> AppCredentials:
    load_dotenv()
    return AppCredentials(
        ozon_seller=load_ozon_seller_credentials(),
        ozon_performance=load_ozon_performance_credentials(),
        wb=load_wb_credentials(),
    )
