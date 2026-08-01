from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any

from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.reports.writer import ensure_dir, write_json


DESCRIPTION_BLOCK_NAMES = (
    "Описание товара",
    "Преимущества и характеристики товара",
    "О производителе",
)
ATOMIC_MEDIA_ACTIONS = {
    "add",
    "exclude",
    "keep",
    "keep_current",
    "keep_current_no_upload",
    "no_media_upload",
    "replace",
    "do_not_touch",
}
NO_UPLOAD_ACTIONS = {"keep", "keep_current", "keep_current_no_upload", "no_media_upload", "do_not_touch"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("audit_root_must_be_object")
    return payload


def _nested(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _sku(payload: dict[str, Any]) -> str:
    return _text(_nested(payload, "identity", "internal_sku") or payload.get("internal_sku"))


def _proposed(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("proposed_final_card")
    return value if isinstance(value, dict) else {}


def _has_marketplace(payload: dict[str, Any], marketplace: str) -> bool:
    identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
    presence = _text(identity.get("marketplace_presence")).lower()
    if marketplace in presence:
        return True
    values = identity.get(marketplace)
    if isinstance(values, dict) and any(_text(value) for value in values.values()):
        return True
    return bool(_nested(payload, "current_state", marketplace))


def _description_blocks(proposed: dict[str, Any]) -> list[str]:
    value = proposed.get("description_blocks")
    if isinstance(value, dict):
        return [_text(value.get(name)) for name in DESCRIPTION_BLOCK_NAMES]
    if isinstance(value, list):
        return [_text(item) for item in value]
    description = _text(proposed.get("canonical_description"))
    return [part.strip() for part in re.split(r"\n\s*\n", description) if part.strip()]


def _hashtags(proposed: dict[str, Any]) -> list[str]:
    value = proposed.get("ozon_hashtags")
    if not value:
        attrs = proposed.get("ozon_attributes") if isinstance(proposed.get("ozon_attributes"), dict) else {}
        value = attrs.get("#Хештеги") or attrs.get("hashtags")
    raw = value if isinstance(value, list) else re.split(r"[\s,;]+", _text(value))
    result: list[str] = []
    for item in raw:
        normalized = _text(item).rstrip(",;").lower()
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _physical(proposed: dict[str, Any]) -> dict[str, Any]:
    for key in ("target_physical_params", "target_physical_parameters", "physical", "physical_parameters"):
        value = proposed.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _has_any(container: dict[str, Any], keys: tuple[str, ...]) -> bool:
    return any(_text(container.get(key)) for key in keys)


def _photo_set(payload: dict[str, Any], proposed: dict[str, Any], key: str) -> list[dict[str, Any]]:
    media = payload.get("media") if isinstance(payload.get("media"), dict) else {}
    value = proposed.get(key) if key in proposed else media.get(key)
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _validate_photo_set(name: str, rows: list[dict[str, Any]], errors: list[str]) -> None:
    positions: list[int] = []
    for index, row in enumerate(rows, start=1):
        try:
            position = int(row.get("position") or index)
        except (TypeError, ValueError):
            errors.append(f"{name}_position_invalid:{index}")
            continue
        positions.append(position)
        action = _text(row.get("action") or "keep").lower()
        if action not in ATOMIC_MEDIA_ACTIONS or re.search(r"\bили\b|/", action, flags=re.IGNORECASE):
            errors.append(f"{name}_action_not_atomic:{position}:{action or 'missing'}")
    if len(positions) != len(set(positions)):
        errors.append(f"{name}_positions_not_unique")


def validate_card_audit(payload: dict[str, Any], *, strict: bool = True) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    proposed = _proposed(payload)
    sku = _sku(payload)
    if not sku:
        errors.append("internal_sku_missing")
    if not proposed:
        errors.append("proposed_final_card_missing")
        return {"status": "blocked", "internal_sku": sku, "errors": errors, "warnings": warnings}

    titles = {
        "canonical": _text(proposed.get("canonical_title")),
        "ozon": _text(proposed.get("ozon_title")),
        "wb": _text(proposed.get("wb_title")),
    }
    if not titles["canonical"]:
        errors.append("canonical_title_missing")
    for marketplace in ("ozon", "wb"):
        if _has_marketplace(payload, marketplace) and not titles[marketplace]:
            (errors if strict else warnings).append(f"{marketplace}_title_missing")
        if titles[marketplace] and titles["canonical"] and titles[marketplace] != titles["canonical"]:
            errors.append(f"title_mismatch:{marketplace}")
    if any(len(value) > 60 for value in titles.values() if value):
        errors.append("title_exceeds_60_characters")

    blocks = _description_blocks(proposed)
    if len(blocks) != 3 or any(not block for block in blocks):
        errors.append("description_must_have_three_nonempty_blocks")
    semantic_plan = proposed.get("description_semantic_plan") or _nested(payload, "seo", "description_semantic_plan")
    coverage = proposed.get("description_seo_coverage") or _nested(payload, "seo", "description_seo_coverage")
    if not semantic_plan:
        (errors if strict else warnings).append("description_semantic_plan_missing")
    if not coverage:
        (errors if strict else warnings).append("description_seo_coverage_missing")

    hashtags = _hashtags(proposed)
    shortfall = _text(proposed.get("hashtag_shortfall_reason") or _nested(payload, "seo", "hashtag_shortfall_reason"))
    if len(hashtags) > 30:
        errors.append(f"ozon_hashtags_over_limit:{len(hashtags)}")
    if len(hashtags) < 20 and not shortfall:
        (errors if strict else warnings).append(f"ozon_hashtags_short_without_reason:{len(hashtags)}")
    elif len(hashtags) < 30:
        warnings.append(f"ozon_hashtags_below_target_30:{len(hashtags)}")

    physical = _physical(proposed)
    ozon_attributes = proposed.get("ozon_attributes") if isinstance(proposed.get("ozon_attributes"), dict) else {}
    wb_attributes = proposed.get("wb_attributes") if isinstance(proposed.get("wb_attributes"), dict) else {}
    wb_characteristics = proposed.get("wb_characteristics") if isinstance(proposed.get("wb_characteristics"), dict) else {}
    wb_create = proposed.get("wb_create") if isinstance(proposed.get("wb_create"), dict) else {}
    ozon_package_present = _has_any(
        physical,
        ("package_dimensions_ozon_mm", "ozon_package_mm", "ozon_package", "package_size_ozon_mm", "package_size_mm"),
    ) or _text(ozon_attributes.get("package_dimensions_mm")) or all(
        physical.get(key) is not None for key in ("package_depth_mm", "package_width_mm", "package_height_mm")
    )
    wb_package_present = _has_any(
        physical,
        ("package_dimensions_wb_cm", "wb_package_cm", "wb_package", "package_size_wb_cm"),
    ) or _text(wb_attributes.get("package_dimensions_cm")) or isinstance(wb_characteristics.get("dimensions_cm"), dict) or isinstance(wb_create.get("dimensions_cm"), dict)
    if not ozon_package_present:
        errors.append("ozon_package_dimensions_missing")
    if not wb_package_present:
        errors.append("wb_package_dimensions_missing")
    if not _has_any(physical, ("pack_qty", "units_in_one_product", "physical_item_count")) and not _text(proposed.get("pack_qty")):
        errors.append("pack_qty_missing")

    generic = _photo_set(payload, proposed, "target_marketplace_photo_set")
    ozon = _photo_set(payload, proposed, "target_ozon_photo_set")
    wb = _photo_set(payload, proposed, "target_wb_photo_set")
    _validate_photo_set("generic_media", generic, errors)
    _validate_photo_set("ozon_media", ozon, errors)
    _validate_photo_set("wb_media", wb, errors)
    generic_write = any(_text(row.get("action") or "keep").lower() not in NO_UPLOAD_ACTIONS for row in generic)
    if generic_write and _has_marketplace(payload, "ozon") and not ozon:
        (errors if strict else warnings).append("ozon_media_set_missing_for_media_change")
    if generic_write and _has_marketplace(payload, "wb") and not wb:
        (errors if strict else warnings).append("wb_media_set_missing_for_media_change")
    if generic_write:
        warnings.append("generic_media_set_is_legacy_only")

    return {
        "status": "ok" if not errors else "blocked",
        "internal_sku": sku,
        "errors": errors,
        "warnings": warnings,
        "checks": {
            "title_length": len(titles["canonical"]),
            "description_blocks": len(blocks),
            "ozon_hashtags": len(hashtags),
            "ozon_media_rows": len(ozon),
            "wb_media_rows": len(wb),
        },
    }


def run_card_audit_prevalidate(
    *,
    data_dir: Path = Path("data"),
    audit_paths: list[Path] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    started_at = datetime.now().astimezone()
    run_id = run_id or f"card_audit_prevalidate_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.date().isoformat() / run_id)
    rows: list[dict[str, Any]] = []
    for path in audit_paths or []:
        try:
            result = validate_card_audit(_read_json(path))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            result = {"status": "blocked", "internal_sku": "", "errors": [f"audit_read_failed:{exc}"], "warnings": []}
        rows.append({"audit_path": str(path), **result})
    blocked = sum(row["status"] == "blocked" for row in rows)
    summary = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "dry_run",
        "overall_status": "ok" if rows and blocked == 0 else "blocked",
        "checked_rows": len(rows),
        "blocked_rows": blocked,
        "rows": rows,
        "artifacts": {
            "run_dir": str(run_dir),
            "validation": str(run_dir / "validation.json"),
            "summary": str(run_dir / "summary.json"),
        },
    }
    write_json(run_dir / "validation.json", rows)
    write_json(run_dir / "summary.json", summary)
    manifest = write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=summary,
        task="card-audit-prevalidate",
        mode="dry_run",
        risk="low",
        marketplaces=["ozon", "wb"],
        inputs={"audit_paths": [str(path) for path in audit_paths or []]},
        lifecycle_status="verified" if blocked == 0 and rows else "blocked",
        closed=blocked == 0 and bool(rows),
    )
    summary["artifacts"]["run_manifest"] = manifest["manifest"]
    write_json(run_dir / "summary.json", summary)
    return summary
