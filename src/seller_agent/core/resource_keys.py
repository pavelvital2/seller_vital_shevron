from __future__ import annotations

import re
from collections.abc import Iterable


SUPPORTED_MARKETPLACES = frozenset({"ozon", "wb"})
VITAL_SHEVRON_STORE_ID = "vital-shevron"
OZON_LK_PROFILE_ID = "chrome-profile"
WB_LK_PROFILE_ID = "browser-profile"

_RESOURCE_SEGMENT = re.compile(r"[a-z0-9][a-z0-9._-]*\Z")
_PROFILE_IDS = {
    "ozon": OZON_LK_PROFILE_ID,
    "wb": WB_LK_PROFILE_ID,
}


def lk_profile_key(marketplace: str, profile_id: str) -> str:
    marketplace = _marketplace(marketplace)
    profile_id = _resource_segment(profile_id, name="profile_id")
    return f"lk:{marketplace}:profile:{profile_id}"


def api_write_key(marketplace: str, store_id: str) -> str:
    marketplace = _marketplace(marketplace)
    store_id = _resource_segment(store_id, name="store_id")
    return f"api:{marketplace}:{store_id}:write"


def canonical_lk_profile_key(marketplace: str) -> str:
    marketplace = _marketplace(marketplace)
    return lk_profile_key(marketplace, _PROFILE_IDS[marketplace])


def canonical_api_write_key(marketplace: str) -> str:
    return api_write_key(marketplace, VITAL_SHEVRON_STORE_ID)


def is_canonical_lk_profile_key(resource_key: str) -> bool:
    return resource_key in {OZON_LK_PROFILE_KEY, WB_LK_PROFILE_KEY}


def is_canonical_api_write_key(resource_key: str) -> bool:
    return resource_key in {OZON_API_WRITE_KEY, WB_API_WRITE_KEY}


def task_resource_keys(
    *,
    subject_keys: Iterable[str] = (),
    lk_marketplaces: Iterable[str] = (),
    api_write_marketplaces: Iterable[str] = (),
) -> tuple[str, ...]:
    keys = [canonical_api_write_key(marketplace) for marketplace in api_write_marketplaces]
    keys.extend(canonical_lk_profile_key(marketplace) for marketplace in lk_marketplaces)
    keys.extend(_subject_key(key) for key in subject_keys)
    return tuple(dict.fromkeys(keys))


def _marketplace(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in SUPPORTED_MARKETPLACES:
        raise ValueError("marketplace must be one of: ozon, wb")
    return normalized


def _resource_segment(value: str, *, name: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _RESOURCE_SEGMENT.fullmatch(normalized):
        raise ValueError(f"{name} must be a non-empty canonical resource segment")
    return normalized


def _subject_key(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or any(character.isspace() for character in normalized):
        raise ValueError("subject resource key must be non-empty and contain no whitespace")
    if (
        normalized.startswith(("lk:", "api:"))
        or normalized.endswith("-lk")
        or normalized.startswith("marketplace:")
    ):
        raise ValueError("LK/API keys must be built by their canonical factories")
    return normalized


OZON_LK_PROFILE_KEY = canonical_lk_profile_key("ozon")
WB_LK_PROFILE_KEY = canonical_lk_profile_key("wb")
OZON_API_WRITE_KEY = canonical_api_write_key("ozon")
WB_API_WRITE_KEY = canonical_api_write_key("wb")
