from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen


NO_UPLOAD_ACTIONS = {
    "do_not_touch",
    "keep_current",
    "keep_current_no_upload",
    "keep_current_untouched_no_upload",
    "no_media_upload",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _project_path(data_dir: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return data_dir.parent / path


def build_wb_media_plan(passport: dict[str, Any]) -> list[dict[str, Any]]:
    """Build an ordered WB media plan, preferring marketplace-specific rows."""
    media = passport.get("media") if isinstance(passport.get("media"), dict) else {}
    rows = media.get("target_wb_photo_set")
    result: list[dict[str, Any]] = []
    if isinstance(rows, list) and rows:
        for index, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                continue
            action = _text(row.get("action"))
            if action in NO_UPLOAD_ACTIONS:
                continue
            position = int(row.get("position") or index)
            local_path = _text(row.get("local_path"))
            url = _text(row.get("source_url") or row.get("url"))
            if local_path:
                result.append(
                    {
                        "position": position,
                        "source_kind": "owner_approved_local",
                        "local_path": local_path,
                        "sha256": _text(row.get("sha256")),
                        "source_url": url,
                        "action": action,
                    }
                )
            elif url:
                result.append(
                    {
                        "position": position,
                        "source_kind": "owner_approved_url",
                        "source_url": url,
                        "action": action,
                    }
                )
        if result:
            ordered = sorted(result, key=lambda item: int(item["position"]))
            return ordered

    # Marketplace-specific sets are authoritative. A present WB set, even an
    # all-keep set, must never fall back to generic/Ozon-derived assets.
    if isinstance(rows, list):
        return sorted(result, key=lambda item: int(item["position"]))

    assets = media.get("target_assets")
    if isinstance(assets, list):
        for index, asset in enumerate(assets, start=1):
            if not isinstance(asset, dict):
                continue
            url = _text(asset.get("url"))
            if url:
                result.append(
                    {
                        "position": int(asset.get("position") or index),
                        "source_kind": "owner_approved_url",
                        "source_url": url,
                        "action": _text(asset.get("action")),
                    }
                )
    return sorted(result, key=lambda item: int(item["position"]))


def validate_wb_media_plan(
    *,
    data_dir: Path,
    media_plan: list[dict[str, Any]],
    require_contiguous: bool = True,
) -> list[str]:
    errors: list[str] = []
    positions = [int(item.get("position") or 0) for item in media_plan]
    if require_contiguous and positions and positions != list(range(1, len(positions) + 1)):
        errors.append(f"wb_media_positions_not_contiguous:{positions}")
    for item in media_plan:
        if item.get("source_kind") != "owner_approved_local":
            continue
        value = _text(item.get("local_path"))
        path = _project_path(data_dir, value)
        if not value or not path.is_file():
            errors.append(f"wb_media_local_file_missing:{value}")
            continue
        expected = _text(item.get("sha256")).removeprefix("sha256:")
        actual = _sha256(path)
        if expected and actual != expected:
            errors.append(f"wb_media_local_sha256_mismatch:{value}")
    return errors


def media_urls_if_remote_only(media_plan: list[dict[str, Any]]) -> list[str]:
    if any(item.get("source_kind") == "owner_approved_local" for item in media_plan):
        return []
    return [
        _text(item.get("source_url"))
        for item in media_plan
        if _text(item.get("source_url"))
    ]


def materialize_wb_media_entry(
    *,
    data_dir: Path,
    run_dir: Path,
    vendor_code: str,
    entry: dict[str, Any],
) -> tuple[Path, str]:
    local_path = _text(entry.get("local_path"))
    if local_path:
        path = _project_path(data_dir, local_path)
        if not path.is_file():
            raise RuntimeError(f"WB media local file missing: {local_path}")
        actual = _sha256(path)
        expected = _text(entry.get("sha256")).removeprefix("sha256:")
        if expected and actual != expected:
            raise RuntimeError(f"WB media SHA-256 drift: {local_path}")
        return path, actual

    url = _text(entry.get("source_url"))
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (
        host.endswith(".ozone.ru") or host.endswith(".wbbasket.ru")
    ):
        raise RuntimeError(f"WB media remote source is not an approved marketplace CDN: {url}")
    position = int(entry.get("position") or 0)
    suffix = Path(parsed.path).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
        suffix = ".jpg"
    target_dir = run_dir / "media_inputs"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{vendor_code}_{position:02d}{suffix}"
    request = Request(url, headers={"User-Agent": "seller-agent/1.0"})
    with urlopen(request, timeout=60) as response:
        payload = response.read(25 * 1024 * 1024 + 1)
    if not payload or len(payload) > 25 * 1024 * 1024:
        raise RuntimeError(f"WB media remote source has invalid size: {url}")
    target.write_bytes(payload)
    return target, _sha256(target)
