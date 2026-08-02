from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import stat
from urllib.parse import urlsplit

from seller_agent.control_plane.auth import SessionSigner


DEFAULT_CONTROL_BOT_TOKEN_FILE = Path(
    "/home/pavel/.secrets/vital_shevron_control_bot_token"
)
DEFAULT_CONTROL_SESSION_SECRET_FILE = Path(
    "/home/pavel/.secrets/vital_shevron_control_session_secret"
)
DEFAULT_CONTROL_STATE_FILE = Path(
    ".sessions/telegram/vital_shevron_control_bot_state.json"
)
DEFAULT_CONTROL_LOCK_FILE = Path(
    ".sessions/telegram/vital_shevron_control_bot.lock"
)
CONTROL_ANALYTICS_TASK = "store-analytics-overview"


@dataclass(frozen=True, repr=False)
class ControlPlaneConfig:
    bot_token: str = field(repr=False)
    allowed_owner_ids: frozenset[int]
    session_signer: SessionSigner = field(repr=False)
    public_app_url: str
    allowed_task_ids: frozenset[str] = frozenset({CONTROL_ANALYTICS_TASK})
    wb_supplier_id: str = "4516781"
    ozon_seller_slug: str = "vital-shevron"
    state_file: Path = DEFAULT_CONTROL_STATE_FILE
    lock_file: Path = DEFAULT_CONTROL_LOCK_FILE

    @classmethod
    def from_environment(cls) -> "ControlPlaneConfig":
        token_file = Path(
            os.environ.get("VITAL_SHEVRON_CONTROL_BOT_TOKEN_FILE")
            or DEFAULT_CONTROL_BOT_TOKEN_FILE
        )
        session_file = Path(
            os.environ.get("VITAL_SHEVRON_CONTROL_SESSION_SECRET_FILE")
            or DEFAULT_CONTROL_SESSION_SECRET_FILE
        )
        owner_text = os.environ.get("VITAL_SHEVRON_CONTROL_OWNER_IDS", "")
        owner_file = os.environ.get("VITAL_SHEVRON_CONTROL_OWNER_IDS_FILE", "")
        if owner_file:
            owner_text = Path(owner_file).read_text(encoding="utf-8")
        owner_ids = _parse_owner_ids(owner_text)
        public_url = os.environ.get("VITAL_SHEVRON_CONTROL_PUBLIC_URL", "").strip()
        _validate_public_url(public_url)
        wb_supplier_id = os.environ.get(
            "VITAL_SHEVRON_CONTROL_WB_SUPPLIER_ID", "4516781"
        ).strip()
        ozon_seller_slug = os.environ.get(
            "VITAL_SHEVRON_CONTROL_OZON_SELLER_SLUG", "vital-shevron"
        ).strip()
        _validate_marketplace_owner_scope(wb_supplier_id, ozon_seller_slug)
        state_file = Path(
            os.environ.get("VITAL_SHEVRON_CONTROL_STATE_FILE")
            or DEFAULT_CONTROL_STATE_FILE
        )
        lock_file = Path(
            os.environ.get("VITAL_SHEVRON_CONTROL_LOCK_FILE")
            or DEFAULT_CONTROL_LOCK_FILE
        )
        if (
            state_file == lock_file
            or state_file.name == "vital_shevron_bot_state.json"
            or lock_file.name == "vital_shevron_bot_polling.lock"
        ):
            raise ValueError("Control polling state and lock must be isolated.")
        return cls(
            bot_token=_read_secret_file(token_file),
            allowed_owner_ids=frozenset(owner_ids),
            session_signer=SessionSigner(_read_secret_file(session_file).encode("utf-8")),
            public_app_url=public_url,
            wb_supplier_id=wb_supplier_id,
            ozon_seller_slug=ozon_seller_slug,
            state_file=state_file,
            lock_file=lock_file,
        )


def _read_secret_file(path: Path) -> str:
    metadata = path.stat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_uid != os.geteuid()
        or metadata.st_gid != os.getegid()
    ):
        raise PermissionError("Control secret file must be a regular 0600 file.")
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError("Control secret file is empty.")
    return value


def load_control_bot_token(
    path: Path = DEFAULT_CONTROL_BOT_TOKEN_FILE,
) -> str:
    return _read_secret_file(path)


def _parse_owner_ids(value: str) -> set[int]:
    normalized = value.replace("\n", ",")
    try:
        owner_ids = {int(item.strip()) for item in normalized.split(",") if item.strip()}
    except ValueError as exc:
        raise ValueError("Control owner allowlist contains an invalid ID.") from exc
    if not owner_ids or any(item <= 0 for item in owner_ids):
        raise ValueError("Control owner allowlist is required.")
    return owner_ids


def _validate_public_url(value: str) -> None:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != "/vital-shevron/"
    ):
        raise ValueError("Control public URL must be HTTPS with /vital-shevron/ prefix.")


def _validate_marketplace_owner_scope(
    wb_supplier_id: str,
    ozon_seller_slug: str,
) -> None:
    if not wb_supplier_id.isascii() or not wb_supplier_id.isdigit():
        raise ValueError("Control WB supplier ID is invalid.")
    if (
        not ozon_seller_slug
        or not ozon_seller_slug.isascii()
        or any(
            char not in "abcdefghijklmnopqrstuvwxyz0123456789-_"
            for char in ozon_seller_slug
        )
    ):
        raise ValueError("Control Ozon seller slug is invalid.")
