from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any

from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.reports.writer import ensure_dir, write_json


OWNER_APPROVED_PREFIX = "owner_approved"
WB_DEPARTMENTAL_MEDIA_POLICY_STATUSES = {
    "allowed_verified",
    "blocked_pending_watermarked_assets",
    "no_media_update_required",
    "not_applicable",
}
NO_MEDIA_UPDATE_ACTIONS = {
    "do_not_touch",
    "keep_current",
    "keep_current_no_upload",
    "no_media_upload",
}
NO_WB_MEDIA_UPDATE_STATUSES = {"no_media_update_required", "not_applicable"}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_text(value: Any) -> str:
    text = str(value or "").strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text


def _split_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_normalize_text(item) for item in value if _normalize_text(item)]
    return [_normalize_text(item) for item in re.split(r"[,;]", _normalize_text(value)) if _normalize_text(item)]


def _split_hashtags(value: Any) -> list[str]:
    if isinstance(value, list):
        raw_items = value
    else:
        raw_items = re.split(r"[\s,;]+", _normalize_text(value))
    return [item.rstrip(",;") for item in (_normalize_text(item) for item in raw_items) if item.rstrip(",;")]


def _search_queries(audit: dict[str, Any]) -> list[str]:
    candidates: list[Any] = []
    query_pack_terms = _get_nested(audit, "seo", "query_pack", "terms")
    if isinstance(query_pack_terms, list):
        candidates.extend(query_pack_terms)
    for key in ("target_query_clusters", "confirmed_query_rows"):
        rows = _get_nested(audit, "seo", key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            candidates.append(row.get("query") if isinstance(row, dict) else row)
    result: list[str] = []
    for value in candidates:
        query = _normalize_text(value)
        if query and query not in result:
            result.append(query)
    return result


def _numbers(value: Any) -> list[float]:
    return [float(item.replace(",", ".")) for item in re.findall(r"\d+(?:[,.]\d+)?", _normalize_text(value))]


def _clean_size(value: Any, unit: str) -> str:
    text = _normalize_text(value)
    if not text:
        return ""
    text = re.sub(r"\s+(?:каждый|каждое|каждая|каждые)\b", "", text, flags=re.IGNORECASE)
    text = re.sub(rf"\s*{re.escape(unit)}\s*$", "", text, flags=re.IGNORECASE)
    return f"{text.strip()} {unit}"


def _as_int(value: Any, default: int = 0) -> int:
    nums = _numbers(value)
    return int(nums[0]) if nums else default


def _get_nested(data: dict[str, Any], *path: str) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _first_existing(*values: Any) -> str:
    for value in values:
        text = _normalize_text(value)
        if text:
            return text
    return ""


def _audit_internal_sku(audit: dict[str, Any]) -> str:
    return _first_existing(
        _get_nested(audit, "identity", "internal_sku"),
        _get_nested(audit, "proposed_final_card", "internal_sku"),
        audit.get("internal_sku"),
    )


def _find_audit_path(data_dir: Path, sku: str, audit_root: Path | None = None) -> Path | None:
    roots = [audit_root] if audit_root else [data_dir / "catalog" / "card_audits"]
    for root in roots:
        if not root or not root.exists():
            continue
        for path in sorted(root.rglob("audit.json")):
            try:
                audit = _read_json(path)
            except (OSError, json.JSONDecodeError):
                continue
            if _audit_internal_sku(audit) == sku:
                return path
    return None


def _owner_review_status(audit: dict[str, Any]) -> str:
    return _normalize_text(_get_nested(audit, "owner_review", "status") or _get_nested(audit, "approval", "status"))


def _is_owner_approved(audit: dict[str, Any]) -> bool:
    return _owner_review_status(audit).startswith(OWNER_APPROVED_PREFIX)


def _photo_url_map(audit: dict[str, Any]) -> dict[tuple[str, int], str]:
    media = audit.get("media") if isinstance(audit.get("media"), dict) else {}
    photos = media.get("photos") or media.get("photo_audit") or []
    result: dict[tuple[str, int], str] = {}
    for photo in photos if isinstance(photos, list) else []:
        if not isinstance(photo, dict):
            continue
        marketplace = _normalize_text(photo.get("marketplace")).lower()
        position = _as_int(photo.get("position"))
        url = _first_existing(photo.get("source_url"), photo.get("url"))
        if not marketplace and "ozone.ru" in url:
            marketplace = "ozon"
        if marketplace and position and url:
            result[(marketplace, position)] = url
    return result


def _target_photo_set(audit: dict[str, Any], proposed: dict[str, Any]) -> list[dict[str, Any]]:
    value = proposed.get("target_marketplace_photo_set") or _get_nested(audit, "media", "target_marketplace_photo_set") or []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _marketplace_photo_set(
    audit: dict[str, Any], proposed: dict[str, Any], marketplace: str
) -> list[dict[str, Any]]:
    key = f"target_{marketplace}_photo_set"
    value = proposed.get(key) or _get_nested(audit, "media", key) or []
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            result.append(item)
            continue
        position = _as_int(item)
        if position:
            result.append({"position": position, "source": f"{marketplace.upper()} {position}"})
    return result


def _photo_set_requests_upload(photo_set: list[dict[str, Any]]) -> bool:
    if not photo_set:
        return False
    for row in photo_set:
        action = _normalize_text(row.get("action")).lower()
        if action not in NO_MEDIA_UPDATE_ACTIONS:
            return True
    return False


def _media_assets(audit: dict[str, Any], proposed: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    urls = _photo_url_map(audit)
    warnings: list[str] = []
    assets: list[dict[str, Any]] = []
    for row in _target_photo_set(audit, proposed):
        position = _as_int(row.get("position"))
        source = _normalize_text(row.get("source"))
        source_lower = source.lower()
        marketplace = "wb" if "wb" in source_lower else "ozon"
        source_numbers = _numbers(source)
        source_position = int(source_numbers[-1]) if source_numbers else position
        url = _first_existing(row.get("source_url"), row.get("url"))
        if not url:
            url = urls.get((marketplace, source_position)) or urls.get(("ozon", position)) or urls.get(("wb", position))
        if not url:
            warnings.append(f"missing_photo_url:{source or position}")
            continue
        assets.append(
            {
                "position": position,
                "role": _first_existing(row.get("role"), row.get("name"), "additional"),
                "source": source or f"{marketplace.upper()} {source_position}",
                "marketplace": "wb_update_from_ozon" if marketplace == "ozon" else "wb_current",
                "note": _first_existing(row.get("note"), row.get("target")),
                "url": url,
            }
        )
    return assets, warnings


def _physical(proposed: dict[str, Any], identity: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[str]]:
    identity = identity or {}
    source = proposed.get("target_physical_params")
    if not isinstance(source, dict):
        source = proposed.get("target_physical_parameters")
    if not isinstance(source, dict):
        source = proposed.get("physical")
    if not isinstance(source, dict):
        source = proposed.get("physical_parameters")
    if not isinstance(source, dict):
        source = {}
    ozon_attributes = proposed.get("ozon_attributes") if isinstance(proposed.get("ozon_attributes"), dict) else {}
    wb_attributes = proposed.get("wb_attributes") if isinstance(proposed.get("wb_attributes"), dict) else {}
    wb_characteristics = (
        proposed.get("wb_characteristics") if isinstance(proposed.get("wb_characteristics"), dict) else {}
    )
    wb_create = proposed.get("wb_create") if isinstance(proposed.get("wb_create"), dict) else {}
    wb_dimensions = (
        wb_characteristics.get("dimensions_cm")
        if isinstance(wb_characteristics.get("dimensions_cm"), dict)
        else {}
    )
    if not wb_dimensions and isinstance(wb_create.get("dimensions_cm"), dict):
        wb_dimensions = wb_create["dimensions_cm"]
    wb_dimensions_package = "*".join(
        _normalize_text(wb_dimensions.get(key)) for key in ("length", "width", "height")
    ) if all(wb_dimensions.get(key) is not None for key in ("length", "width", "height")) else ""
    errors: list[str] = []
    product_size = _first_existing(
        source.get("product_size_mm"),
        source.get("product_size_mm_each"),
        source.get("product_size"),
        source.get("product_size_display"),
        proposed.get("product_size_mm"),
        ozon_attributes.get("product_size_mm"),
    )
    if not product_size and source.get("product_width_mm") is not None and source.get("product_height_mm") is not None:
        product_size = f"{_normalize_text(source.get('product_width_mm'))}*{_normalize_text(source.get('product_height_mm'))}"
    ozon_package = _first_existing(
        source.get("ozon_package_mm"),
        source.get("ozon_package"),
        source.get("package_size_ozon_mm"),
        source.get("package_size_mm"),
        source.get("package_size_display"),
        ozon_attributes.get("package_dimensions_mm"),
    )
    if not ozon_package and all(source.get(key) is not None for key in ("package_depth_mm", "package_width_mm", "package_height_mm")):
        ozon_package = "*".join(
            _normalize_text(source.get(key))
            for key in ("package_depth_mm", "package_width_mm", "package_height_mm")
        )
    wb_package = _first_existing(
        source.get("wb_package_cm"),
        source.get("wb_package"),
        source.get("package_size_wb_cm"),
        wb_attributes.get("package_dimensions_cm"),
        wb_dimensions_package,
    )
    weight = _as_int(
        source.get("weight_g")
        or source.get("ozon_weight_g")
        or source.get("package_weight_g")
        or source.get("weight")
        or ozon_attributes.get("package_weight_g"),
        0,
    )
    item_weight = _as_int(
        source.get("item_weight_g") or source.get("item_weight_g_each"),
        weight,
    )
    pack_qty = _as_int(
        source.get("pack_qty")
        or source.get("units_in_one_product")
        or source.get("physical_item_count")
        or proposed.get("pack_qty")
        or ozon_attributes.get("units_per_product")
        or ozon_attributes.get("quantity_in_package")
        or identity.get("pack_qty"),
        1,
    )
    if not product_size:
        errors.append("product_size_missing")
    if not ozon_package:
        errors.append("ozon_package_missing")
    if not wb_package:
        errors.append("wb_package_missing")
    if not weight:
        errors.append("weight_missing")
    return (
        {
            "product_size_mm": _clean_size(product_size, "мм"),
            "package_dimensions_ozon_mm": _clean_size(ozon_package, "мм"),
            "package_dimensions_wb_cm": _clean_size(wb_package, "см"),
            "item_weight_g": item_weight,
            "package_weight_g": weight,
            "pack_qty": pack_qty,
        },
        errors,
    )


def _content(proposed: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    title = _first_existing(proposed.get("canonical_title"), proposed.get("ozon_title"), proposed.get("wb_title"))
    description = _first_existing(
        proposed.get("canonical_description"),
        proposed.get("ozon_description"),
        proposed.get("wb_description"),
    )
    errors: list[str] = []
    if not title:
        errors.append("title_missing")
    if not description:
        errors.append("description_missing")
    ozon_description = _normalize_text(proposed.get("ozon_description"))
    if ozon_description.lower() == "same_as_canonical_description":
        ozon_description = description
    wb_description = _normalize_text(proposed.get("wb_description"))
    if wb_description.lower() == "same_as_canonical_description":
        wb_description = description
    blocks = proposed.get("description_blocks")
    if isinstance(blocks, dict):
        blocks = [
            _normalize_text(blocks.get(name))
            for name in ("Описание товара", "Преимущества и характеристики товара", "О производителе")
            if _normalize_text(blocks.get(name))
        ]
    if not isinstance(blocks, list) or not blocks:
        blocks = [part.strip() for part in description.split("\n\n") if part.strip()]
    return (
        {
            "canonical_title": title,
            "ozon_title": _first_existing(proposed.get("ozon_title"), title),
            "wb_title": _first_existing(proposed.get("wb_title"), title),
            "canonical_description": description,
            "ozon_description": _first_existing(ozon_description, description),
            "wb_description": _first_existing(wb_description, description),
            "description_blocks": blocks,
        },
        errors,
    )


def _colors(proposed: dict[str, Any]) -> list[str]:
    return _split_values(
        proposed.get("color")
        or proposed.get("colors")
        or _get_nested(proposed, "physical", "colors")
        or _get_nested(proposed, "target_physical_parameters", "colors")
        or _get_nested(proposed, "target_physical_params", "colors")
        or _get_nested(proposed, "physical_parameters", "colors")
    )


def _is_patch_product(identity: dict[str, Any], proposed: dict[str, Any], sku: str) -> bool:
    values = [
        sku,
        identity.get("product_type"),
        identity.get("product_group"),
        proposed.get("canonical_title"),
        proposed.get("ozon_title"),
        proposed.get("wb_title"),
    ]
    text = " ".join(_normalize_text(value).lower() for value in values)
    return sku.startswith("nash_") or "нашивк" in text


def _composition(proposed: dict[str, Any], *, is_patch: bool = False) -> list[str]:
    return _split_values(
        proposed.get("composition")
        or _get_nested(proposed, "physical", "composition")
        or _get_nested(proposed, "target_physical_parameters", "composition")
        or _get_nested(proposed, "target_physical_params", "composition")
        or _get_nested(proposed, "physical_parameters", "composition")
        or (["полиэстер"] if is_patch else ["полиэстер", "нейлон"])
    )


def _attachment_type(*, is_patch: bool = False) -> str:
    if is_patch:
        return "пришивная без липучки"
    return "липучка Velcro: крючок пришит с обратной стороны, петля в комплекте"


def _wb_decor_type(proposed: dict[str, Any], *, is_patch: bool = False) -> str:
    explicit = _field_value(proposed.get("wb_attributes") or {}, "Вид декора для одежды", "decor_type")
    if explicit:
        return explicit
    return "нашивка" if is_patch else "шеврон"


def _package_contents(pack_qty: str, *, is_patch: bool = False) -> str:
    if is_patch:
        return f"нашивка без липучки пришивная {pack_qty} шт."
    return f"шеврон на липучке {pack_qty} шт."


def _material(proposed: dict[str, Any]) -> str:
    return _first_existing(
        proposed.get("material"),
        _get_nested(proposed, "physical", "material"),
        _get_nested(proposed, "target_physical_parameters", "material"),
        _get_nested(proposed, "target_physical_params", "material"),
        _get_nested(proposed, "physical_parameters", "material"),
        "Габардин",
    )


def _field_value(data: dict[str, Any], *names: str) -> str:
    for name in names:
        value = data.get(name)
        if value:
            return _normalize_text(value)
    return ""


def _marketplace_attributes(
    *,
    proposed: dict[str, Any],
    content: dict[str, Any],
    physical: dict[str, Any],
    colors: list[str],
    color_name: str,
    material: str,
    composition: list[str],
    ozon_model_name: str,
    hashtags: str,
    is_patch: bool,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    pack_qty = str(physical["pack_qty"])
    composition_text = "; ".join(composition)
    ozon_attrs = [
        {"field": "Название", "value": content["ozon_title"]},
        {"field": "Аннотация", "value": content["ozon_description"]},
        {"field": "#Хештеги", "value": hashtags},
        {"field": "Бренд", "value": "VitalEmb"},
        {"field": "Название модели", "value": ozon_model_name},
        {"field": "Материал", "value": material},
        {"field": "Состав", "value": composition_text},
        {"field": "Размеры, мм", "value": physical["product_size_mm"]},
        {"field": "Габариты упаковки", "value": physical["package_dimensions_ozon_mm"]},
        {"field": "Вес с упаковкой, г", "value": str(physical["package_weight_g"])},
        {"field": "Количество в упаковке, шт", "value": pack_qty},
        {"field": "Единиц в одном товаре", "value": pack_qty},
        {"field": "Количество заводских упаковок", "value": "1"},
        {"field": "Цвет товара", "value": ", ".join(colors)},
        {"field": "Название цвета", "value": color_name},
        {"field": "Целевая аудитория", "value": "Взрослая"},
        {"field": "Вид выпуска товара", "value": "Фабричное производство"},
        {"field": "Страна-изготовитель", "value": "Россия"},
        {"field": "ТН ВЭД коды ЕАЭС", "value": "5810999000 - Прочие вышивки из прочих текстильных материалов"},
        {"field": "Нужен код маркировки", "value": "false"},
    ]
    wb_attrs = [
        {"field": "Наименование", "value": content["wb_title"]},
        {"field": "Описание", "value": content["wb_description"]},
        {"field": "Бренд", "value": "VitalEmb"},
        {"field": "Категория продавца", "value": "Декор для одежды"},
        {"field": "Цвет", "value": ", ".join(colors)},
        {"field": "Вид декора для одежды", "value": _wb_decor_type(proposed, is_patch=is_patch)},
        {"field": "Состав", "value": composition_text},
        {"field": "Страна производства", "value": "Россия"},
        {"field": "Количество предметов в упаковке", "value": f"{pack_qty} шт."},
        {"field": "Комплектация", "value": _package_contents(pack_qty, is_patch=is_patch)},
        {"field": "ТНВЭД", "value": "5810999000"},
        {"field": "КИЗ", "value": "false / unchecked"},
    ]
    wb_characteristics = (
        proposed.get("wb_characteristics") if isinstance(proposed.get("wb_characteristics"), dict) else {}
    )
    if wb_characteristics.get("isAdult") is True:
        wb_attrs.append({"field": "18+ / isAdult", "value": "true"})
    return ozon_attrs, wb_attrs


def _wb_write_constraints(audit: dict[str, Any], proposed: dict[str, Any]) -> dict[str, Any]:
    wb_characteristics = (
        proposed.get("wb_characteristics") if isinstance(proposed.get("wb_characteristics"), dict) else {}
    )
    barcode = wb_characteristics.get("barcode")
    barcode_action = _normalize_text(barcode.get("action")) if isinstance(barcode, dict) else ""
    policy = _get_nested(audit, "media", "wb_departmental_symbol_policy")
    media_status = _normalize_text(policy.get("media_apply_status")) if isinstance(policy, dict) else ""
    constraints: dict[str, Any] = {}
    if barcode_action:
        constraints["barcode"] = {
            "action": barcode_action,
            "include_in_write_payload": False,
        }
    if wb_characteristics.get("isAdult") is True:
        constraints["isAdult"] = {
            "target": True,
            "apply_condition": _first_existing(
                wb_characteristics.get("isAdult_apply_condition"),
                "only_if_current_not_true",
            ),
        }
    if media_status in NO_WB_MEDIA_UPDATE_STATUSES:
        constraints["media"] = {
            "action": "keep_current",
            "include_in_write_payload": False,
        }
    return constraints


def build_passport_from_audit(audit: dict[str, Any], audit_path: Path) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not _is_owner_approved(audit):
        return None, ["audit_not_owner_approved"], []
    proposed = audit.get("proposed_final_card")
    if not isinstance(proposed, dict) or not proposed:
        return None, ["proposed_final_card_missing"], []
    identity = audit.get("identity") if isinstance(audit.get("identity"), dict) else {}
    sku = _audit_internal_sku(audit)
    if not sku:
        errors.append("internal_sku_missing")
    is_patch = _is_patch_product(identity, proposed, sku)
    content, content_errors = _content(proposed)
    physical, physical_errors = _physical(proposed, identity)
    errors.extend(content_errors)
    errors.extend(physical_errors)
    colors = _colors(proposed)
    if not colors:
        errors.append("colors_missing")
    color_name = _first_existing(
        proposed.get("color_name"),
        _get_nested(proposed, "physical", "color_name"),
        _get_nested(proposed, "ozon_attributes", "color_name"),
        _get_nested(proposed, "target_physical_parameters", "color_name"),
        _get_nested(proposed, "target_physical_params", "color_name"),
    )
    if not color_name:
        errors.append("color_name_missing")
    material = _material(proposed)
    composition = _composition(proposed, is_patch=is_patch)
    ozon_model_name = _first_existing(
        proposed.get("ozon_model_name"),
        _get_nested(proposed, "ozon_attributes", "Название модели"),
        _get_nested(proposed, "ozon_attributes", "model_name"),
        _get_nested(proposed, "ozon_attributes", "model"),
        _get_nested(proposed, "ozon_attributes", "9048_model"),
    )
    hashtags_value = proposed.get("ozon_hashtags")
    if isinstance(hashtags_value, str) and "#" not in hashtags_value:
        hashtags_value = None
    if not hashtags_value:
        hashtags_value = _get_nested(proposed, "ozon_attributes", "#Хештеги")
    if not hashtags_value:
        hashtags_value = _get_nested(proposed, "ozon_attributes", "hashtags")
    hashtags = " ".join(_split_hashtags(hashtags_value))
    wb_tags = _split_values(_get_nested(proposed, "wb_attributes", "wb_tags") or proposed.get("wb_tags"))
    media_assets, media_warnings = _media_assets(audit, proposed)
    warnings.extend(media_warnings)
    if errors:
        return None, errors, warnings
    ozon_attrs, wb_attrs = _marketplace_attributes(
        proposed=proposed,
        content=content,
        physical=physical,
        colors=colors,
        color_name=color_name,
        material=material,
        composition=composition,
        ozon_model_name=ozon_model_name,
        hashtags=hashtags,
        is_patch=is_patch,
    )
    owner_review = audit.get("owner_review") if isinstance(audit.get("owner_review"), dict) else {}
    approved_at = _first_existing(owner_review.get("approved_at"), owner_review.get("final_review_sent_at"), datetime.now().isoformat(timespec="seconds"))
    target_marketplace_photo_set = _target_photo_set(audit, proposed)
    target_ozon_photo_set = _marketplace_photo_set(audit, proposed, "ozon")
    target_wb_photo_set = _marketplace_photo_set(audit, proposed, "wb")
    wb_media_update_requested = _photo_set_requests_upload(
        target_marketplace_photo_set
    ) or _photo_set_requests_upload(target_wb_photo_set)
    wb_media_status = _normalize_text(
        _get_nested(audit, "media", "wb_departmental_symbol_policy", "media_apply_status")
    )
    if wb_media_status in NO_WB_MEDIA_UPDATE_STATUSES and not wb_media_update_requested:
        media_assets = []
        target_marketplace_photo_set = []
        target_wb_photo_set = []
    dangerous_actions = ["card_content_update", "seller_sku_update"]
    if media_assets or wb_media_update_requested:
        dangerous_actions.append("wb_media_update")
    if proposed.get("future_ozon_create"):
        dangerous_actions.append("ozon_card_create")
    wb_create = proposed.get("wb_create") if isinstance(proposed.get("wb_create"), dict) else None
    if wb_create and _normalize_text(wb_create.get("current_state")) == "card_absent":
        dangerous_actions.append("wb_card_create")
    passport = {
        "$schema": "./master_product_passport_approved.schema.json",
        "approval": {
            "status": "owner_approved_pending_batch_apply",
            "approved_at": approved_at,
            "approved_by": _first_existing(owner_review.get("approved_by"), "owner_telegram_confirmation"),
            "source_audit_id": str(audit_path.parent.relative_to(audit_path.parents[2])) if len(audit_path.parents) > 2 else audit_path.parent.name,
            "source_review_html": _first_existing(
                owner_review.get("final_review_html_path"),
                owner_review.get("submitted_html_path"),
                owner_review.get("html_path"),
            ),
            "change_notes": "Promoted from owner-approved Layer 2 audit by штатный passport promotion command.",
            "owner_corrections": owner_review.get("corrections") or owner_review.get("owner_corrections") or [],
            "marketplace_apply": {"status": "not_applied"},
        },
        "identity": {
            "internal_product_id": _first_existing(identity.get("internal_product_id"), sku),
            "internal_sku": sku,
            "marketplace_presence": _first_existing(identity.get("marketplace_presence"), "ozon_wb"),
            "ozon_offer_id": _first_existing(_get_nested(identity, "ozon", "offer_id"), identity.get("ozon_offer_id"), _get_nested(audit, "current_state", "ozon", "offer_id")),
            "ozon_offer_id_after_seller_sku_update": sku,
            "ozon_product_id": _first_existing(_get_nested(identity, "ozon", "product_id"), identity.get("ozon_product_id"), _get_nested(audit, "current_state", "ozon", "product_id")),
            "ozon_sku": _first_existing(_get_nested(identity, "ozon", "sku"), identity.get("ozon_sku"), _get_nested(audit, "current_state", "ozon", "sku")),
            "wb_vendor_code": _first_existing(
                _get_nested(identity, "wb", "vendor_code"),
                _get_nested(identity, "wb", "vendorCode"),
                identity.get("wb_vendor_code"),
                identity.get("wb_vendorCode"),
                _get_nested(audit, "current_state", "wb", "vendor_code"),
                _get_nested(audit, "current_state", "wb", "vendorCode"),
            ),
            "wb_vendor_code_after_seller_sku_update": sku,
            "wb_nm_id": _first_existing(
                _get_nested(identity, "wb", "nm_id"),
                _get_nested(identity, "wb", "nmID"),
                identity.get("wb_nm_id"),
                identity.get("wb_nmID"),
                _get_nested(audit, "current_state", "wb", "nm_id"),
                _get_nested(audit, "current_state", "wb", "nmID"),
            ),
            "wb_barcode": _first_existing(_get_nested(identity, "wb", "barcode"), identity.get("wb_barcode")),
        },
        "content": content,
        "physical": physical,
        "materials": {
            "material": material,
            "composition": composition,
            "attachment_type": _attachment_type(is_patch=is_patch),
        },
        "classification": {"tnved": "5810999000", "country_of_origin": "Россия"},
        "grouping": {
            "target_group_key": ozon_model_name,
            "decision": "owner approved grouping/model name; apply only through approved dry-run",
        },
        "media": {
            "designer_tasks": audit.get("designer_tasks") or [],
            "target_assets": media_assets,
            "target_marketplace_photo_set": target_marketplace_photo_set,
            "target_ozon_photo_set": target_ozon_photo_set,
            "target_wb_photo_set": target_wb_photo_set,
            "wb_departmental_symbol_policy": (
                (audit.get("media") or {}).get("wb_departmental_symbol_policy")
                if isinstance(audit.get("media"), dict)
                else None
            ),
        },
        "seo": {
            "search_queries": _search_queries(audit),
            "ozon_hashtags": _split_hashtags(hashtags),
            "wb_tags": wb_tags,
        },
        "ozon": {"attributes": ozon_attrs},
        "wb": {
            "attributes": wb_attrs,
            "create": wb_create,
            "write_constraints": _wb_write_constraints(audit, proposed),
        },
        "safety": {
            "dangerous_actions": dangerous_actions,
            "approval_source": "owner-reviewed HTML and owner-approved Layer 2 audit",
        },
    }
    return passport, [], warnings


def validate_wb_departmental_media_policy(passport: dict[str, Any]) -> dict[str, Any]:
    """Validate WB media policy separately from non-media card changes."""
    media = passport.get("media") if isinstance(passport.get("media"), dict) else {}
    policy = (
        media.get("wb_departmental_symbol_policy")
        if isinstance(media.get("wb_departmental_symbol_policy"), dict)
        else {}
    )
    status = _normalize_text(policy.get("media_apply_status"))
    errors: list[str] = []
    if not status:
        errors.append("wb_departmental_symbol_policy_missing")
    elif status not in WB_DEPARTMENTAL_MEDIA_POLICY_STATUSES:
        errors.append(f"wb_departmental_symbol_policy_invalid_status:{status}")
    elif status == "blocked_pending_watermarked_assets":
        errors.append("wb_media_update_blocked_pending_watermarked_assets")
    return {
        "status": "blocked" if errors else "ok",
        "media_apply_status": status or "missing",
        "errors": errors,
        "rule": policy.get("rule") or "",
    }


def run_promote_approved_card_passport(
    *,
    data_dir: Path = Path("data"),
    internal_skus: list[str] | None = None,
    audit_paths: list[Path] | None = None,
    audit_root: Path | None = None,
    run_id: str | None = None,
    write: bool = False,
    overwrite: bool = False,
) -> dict[str, Any]:
    started_at = datetime.now()
    run_id = run_id or f"promote_approved_card_passport_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    approved_dir = data_dir / "catalog" / "master_passport" / "approved"
    inputs = [sku.strip() for sku in (internal_skus or []) if sku and sku.strip()]
    explicit_paths = list(audit_paths or [])
    rows: list[dict[str, Any]] = []
    paths: list[Path] = []
    for path in explicit_paths:
        if path.exists():
            paths.append(path)
        else:
            rows.append({"audit_path": str(path), "status": "blocked", "errors": ["audit_path_not_found"]})
    for sku in inputs:
        path = _find_audit_path(data_dir, sku, audit_root=audit_root)
        if path:
            paths.append(path)
        else:
            rows.append({"internal_sku": sku, "status": "blocked", "errors": ["owner_approved_audit_not_found"]})
    seen: set[Path] = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        try:
            audit = _read_json(path)
        except json.JSONDecodeError as exc:
            rows.append({"audit_path": str(path), "status": "blocked", "errors": [f"invalid_json:{exc}"]})
            continue
        sku = _audit_internal_sku(audit)
        out_path = approved_dir / f"{sku}.json" if sku else approved_dir / "missing_sku.json"
        if out_path.exists() and not overwrite:
            rows.append({"internal_sku": sku, "audit_path": str(path), "passport_path": str(out_path), "status": "skipped_existing", "errors": [], "warnings": []})
            continue
        passport, errors, warnings = build_passport_from_audit(audit, path)
        if errors or not passport:
            rows.append({"internal_sku": sku, "audit_path": str(path), "passport_path": str(out_path), "status": "blocked", "errors": errors, "warnings": warnings})
            continue
        status = "ready_to_write"
        if write:
            ensure_dir(out_path.parent)
            write_json(out_path, passport)
            status = "written"
        rows.append({"internal_sku": sku, "audit_path": str(path), "passport_path": str(out_path), "status": status, "errors": [], "warnings": warnings})
    plan_path = run_dir / "passport_promotion_plan.json"
    write_json(plan_path, rows)
    written = sum(1 for row in rows if row["status"] == "written")
    ready = sum(1 for row in rows if row["status"] in {"written", "ready_to_write", "skipped_existing"})
    blocked = sum(1 for row in rows if row["status"] == "blocked")
    summary = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "apply" if write else "dry_run",
        "overall_status": "ok" if blocked == 0 else "warning",
        "input_rows": len(rows),
        "ready_rows": ready,
        "blocked_rows": blocked,
        "written_rows": written,
        "artifacts": {"run_dir": str(run_dir), "plan": str(plan_path), "summary": str(run_dir / "summary.json")},
    }
    write_json(run_dir / "summary.json", summary)
    manifest = write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=summary,
        task="promote-approved-card-passport",
        mode="apply" if write else "dry_run",
        risk="medium",
        marketplaces=["ozon", "wb"],
        inputs={"internal_skus": inputs, "audit_paths": [str(path) for path in explicit_paths], "write": write, "overwrite": overwrite},
        lifecycle_status="verified" if write and blocked == 0 else "pending_review",
        closed=write and blocked == 0,
    )
    summary["artifacts"]["run_manifest"] = manifest["manifest"]
    write_json(run_dir / "summary.json", summary)
    return summary


def ensure_approved_passports_for_batch(*, data_dir: Path, internal_skus: list[str], base_run_id: str) -> dict[str, Any]:
    approved_dir = data_dir / "catalog" / "master_passport" / "approved"
    missing = [sku for sku in internal_skus if not (approved_dir / f"{sku}.json").exists()]
    if not missing:
        media_policy = {
            sku: validate_wb_departmental_media_policy(
                _read_json(approved_dir / f"{sku}.json")
            )
            for sku in internal_skus
        }
        return {
            "status": "ok",
            "missing_skus": [],
            "promotion": None,
            "wb_media_policy": media_policy,
            "wb_media_blocked_skus": [
                sku for sku, result in media_policy.items() if result["status"] == "blocked"
            ],
        }
    promotion = run_promote_approved_card_passport(
        data_dir=data_dir,
        internal_skus=missing,
        run_id=f"{base_run_id}_passport_promotion",
        write=True,
        overwrite=False,
    )
    plan_path = Path(promotion["artifacts"]["plan"])
    rows = _read_json(plan_path)
    still_missing = [
        sku
        for sku in missing
        if not (approved_dir / f"{sku}.json").exists()
    ]
    blocked = [row for row in rows if row.get("status") == "blocked"]
    media_policy = {}
    for sku in internal_skus:
        passport_path = approved_dir / f"{sku}.json"
        if passport_path.exists():
            media_policy[sku] = validate_wb_departmental_media_policy(
                _read_json(passport_path)
            )
    return {
        "status": "ok" if not still_missing and not blocked else "blocked",
        "missing_skus": missing,
        "still_missing_skus": still_missing,
        "blocked": blocked,
        "promotion": promotion,
        "wb_media_policy": media_policy,
        "wb_media_blocked_skus": [
            sku for sku, result in media_policy.items() if result["status"] == "blocked"
        ],
    }
