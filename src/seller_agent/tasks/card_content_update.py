from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import re
import time
from typing import Any

from seller_agent.config import AppCredentials
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.http import ApiError
from seller_agent.marketplaces.ozon.adapter import OzonSellerAdapter
from seller_agent.marketplaces.wb.adapter import WbContentAdapter
from seller_agent.reports.writer import ensure_dir, write_json
from seller_agent.safety.approvals import assert_apply_not_repeated, canonical_checksum, mark_approved_applied
from seller_agent.tasks.wb_media import (
    build_wb_media_plan,
    materialize_wb_media_entry,
    validate_wb_media_plan,
)


OZON_ATTR_IDS = {
    "title": 4180,
    "description": 4191,
    "hashtags": 23171,
    "material": 7405,
    "size": 4382,
    "qty_pack": 8513,
    "qty_unit": 8962,
    "color": 10096,
    "color_name": 10097,
    "adult": 9390,
    "release_type": 22270,
    "country": 4389,
    "factory_packs": 11650,
    "tnved": 22232,
    "marking_required": 23536,
    "package_weight": 4497,
}
WB_CHAR_IDS = {
    "title": 15000000,
    "description": 14177452,
    "color": 14177449,
    "composition": 14177450,
    "country": 14177451,
    "tnved": 15000001,
    "decor_type": 384944,
    "qty": 179792,
    "kit": 378533,
}

WB_COLOR_ALIASES = {
    "мох": "зеленый",
    "олива": "оливковый",
    "зелёный": "зеленый",
    "чёрный": "черный",
}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_space(value: Any) -> str:
    return " ".join(_normalize_text(value).split())


def _normalize_int_text(value: Any) -> str:
    text = _normalize_text(value)
    return text[:-2] if text.endswith(".0") else text


def _split_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_normalize_text(item) for item in value if _normalize_text(item)]
    return [_normalize_text(item) for item in re.split(r"[,;]", _normalize_text(value)) if _normalize_text(item)]


def _normalize_ozon_hashtags(value: Any) -> str:
    if isinstance(value, list):
        raw_items = [_normalize_text(item) for item in value]
    else:
        raw_items = re.split(r"[,;]", _normalize_text(value))
    tags: list[str] = []
    for raw_item in raw_items:
        item = _normalize_text(raw_item).lstrip("#")
        if not item:
            continue
        item = re.sub(r"\s+", "_", item)
        item = re.sub(r"[^\wА-Яа-яЁё]", "_", item, flags=re.UNICODE)
        item = re.sub(r"_+", "_", item).strip("_")
        if not item or len(item) > 29:
            continue
        tag = f"#{item}"
        if tag not in tags:
            tags.append(tag)
    return " ".join(tags[:30])


def _normalize_wb_colors(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        normalized = WB_COLOR_ALIASES.get(value.lower(), value)
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _field_value(attrs: list[dict[str, Any]], field: str) -> str:
    for item in attrs:
        if _normalize_text(item.get("field")).lower() == field.lower():
            return _normalize_text(item.get("value"))
    return ""


def _passport_paths(
    *,
    data_dir: Path,
    passport_paths: list[Path],
    internal_skus: list[str],
) -> list[Path]:
    paths = list(passport_paths)
    approved_dir = data_dir / "catalog" / "master_passport" / "approved"
    for sku in internal_skus:
        paths.append(approved_dir / f"{sku}.json")
    if not paths:
        paths = sorted(approved_dir.glob("*.json"))
    missing = [path for path in paths if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Approved passport not found: {missing[0]}")
    return paths


def _attr_values(attr: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for item in attr.get("values") or []:
        raw = item.get("value") if isinstance(item, dict) else item
        if _normalize_text(raw):
            values.append(_normalize_text(raw))
    return values


def _first_attr_value(item: dict[str, Any], attr_id: int) -> str:
    for attr in item.get("attributes") or []:
        if int(attr.get("id") or 0) == attr_id:
            values = _attr_values(attr)
            return values[0] if values else ""
    return ""


def _set_ozon_attr(
    attrs: list[dict[str, Any]],
    attr_id: int,
    values: list[str],
    dictionary_by_value: dict[str, int] | None = None,
) -> None:
    values = [_normalize_text(value) for value in values if _normalize_text(value)]
    if not values:
        return
    target = None
    for attr in attrs:
        if int(attr.get("id") or 0) == attr_id:
            target = attr
            break
    if target is None:
        target = {"id": attr_id, "complex_id": 0, "values": []}
        attrs.append(target)
    current_by_value = {
        _normalize_text(value.get("value")).lower(): int(value.get("dictionary_value_id") or 0)
        for value in target.get("values") or []
        if isinstance(value, dict)
    }
    dictionary_by_value = dictionary_by_value or {}
    target["values"] = [
        {
            "dictionary_value_id": current_by_value.get(value.lower()) or dictionary_by_value.get(value.lower(), 0),
            "value": value,
        }
        for value in values
    ]


def _remove_ozon_attr(attrs: list[dict[str, Any]], attr_id: int) -> None:
    attrs[:] = [
        attr
        for attr in attrs
        if int(attr.get("id") or 0) != attr_id
    ]


def _parse_ozon_package_mm(value: str) -> dict[str, int]:
    numbers = [int(item) for item in re.findall(r"\d+", value or "")]
    if len(numbers) < 3:
        return {}
    return {"depth": numbers[0], "width": numbers[1], "height": numbers[2]}


def _parse_wb_package_cm(value: str) -> dict[str, Any]:
    numbers = [float(item.replace(",", ".")) for item in re.findall(r"\d+(?:[,.]\d+)?", value or "")]
    if len(numbers) < 3:
        return {}
    return {"length": int(numbers[0]), "width": int(numbers[1]), "height": int(numbers[2])}


def _kg_from_g(value: Any) -> float:
    try:
        return round(float(value) / 1000, 3)
    except (TypeError, ValueError):
        return 0.01


def _price_rows(data_dir: Path) -> dict[str, dict[str, Any]]:
    path = data_dir / "pricing" / "pricing_status.json"
    if not path.exists():
        return {}
    rows = _read_json(path)
    if not isinstance(rows, list):
        return {}
    return {_normalize_text(row.get("internal_sku")): row for row in rows if isinstance(row, dict)}


def _price_value(row: dict[str, Any], key: str) -> str:
    value = _normalize_text(row.get(key))
    if not value:
        return ""
    try:
        return f"{float(value):.2f}"
    except ValueError:
        return value


def _find_local_ozon(data_dir: Path, offer_id: str) -> dict[str, Any] | None:
    path = data_dir / "catalog" / "content" / "ozon_card_content.json"
    if not path.exists():
        return None
    payload = _read_json(path)
    for item in payload.get("attributes", []) if isinstance(payload, dict) else []:
        if _normalize_text(item.get("offer_id")) == offer_id:
            return item
    return None


def _find_local_wb(data_dir: Path, vendor_code: str) -> dict[str, Any] | None:
    path = data_dir / "catalog" / "content" / "wb_card_content.json"
    if not path.exists():
        return None
    payload = _read_json(path)
    for item in payload if isinstance(payload, list) else []:
        if _normalize_text(item.get("vendorCode")) == vendor_code:
            return item
    return None


def _ozon_target(passport: dict[str, Any]) -> dict[str, Any]:
    content = passport.get("content") or {}
    physical = passport.get("physical") or {}
    seo = passport.get("seo") or {}
    ozon = passport.get("ozon") or {}
    ozon_attrs = ozon.get("attributes") or []
    hashtag_constraints = (
        (ozon.get("write_constraints") or {}).get("hashtags") or {}
    )
    hashtags = seo.get("ozon_hashtags")
    return {
        "title": _normalize_text(content.get("ozon_title") or content.get("canonical_title")),
        "description": _normalize_text(content.get("ozon_description") or content.get("canonical_description")),
        "hashtags": _normalize_ozon_hashtags(hashtags),
        "hashtags_in_write_payload": (
            hashtag_constraints.get("include_in_write_payload") is not False
        ),
        "material": _field_value(ozon_attrs, "Материал") or "Габардин",
        "product_size": _normalize_text(physical.get("product_size_mm")),
        "pack_qty": _normalize_text(physical.get("pack_qty") or "1"),
        "colors": _split_values(_field_value(ozon_attrs, "Цвет") or _field_value(ozon_attrs, "Цвет товара")),
        "color_name": _field_value(ozon_attrs, "Название цвета"),
        "tnved": "5810999000 - Прочие вышивки из прочих текстильных материалов",
        "country": "Россия",
        "release_type": "Фабричное производство",
        "adult": "Взрослая",
        "factory_packs": "1",
        "marking_required": "false",
        "package_mm": _normalize_text(physical.get("package_dimensions_ozon_mm")),
        "weight_g": _normalize_text(physical.get("package_weight_g") or physical.get("item_weight_g")),
    }


def _build_ozon_payload(
    *,
    passport: dict[str, Any],
    current: dict[str, Any],
    price_row: dict[str, Any],
    dictionary_values: dict[int, dict[str, int]] | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[str]]:
    target = _ozon_target(passport)
    errors: list[str] = []
    attrs = deepcopy(current.get("attributes") or [])
    _set_ozon_attr(attrs, OZON_ATTR_IDS["title"], [target["title"]])
    _set_ozon_attr(attrs, OZON_ATTR_IDS["description"], [target["description"]])
    if not target["hashtags_in_write_payload"]:
        _remove_ozon_attr(attrs, OZON_ATTR_IDS["hashtags"])
    elif target["hashtags"]:
        _set_ozon_attr(attrs, OZON_ATTR_IDS["hashtags"], [target["hashtags"]])
    _set_ozon_attr(attrs, OZON_ATTR_IDS["material"], [target["material"]])
    _set_ozon_attr(attrs, OZON_ATTR_IDS["size"], [target["product_size"]])
    _set_ozon_attr(attrs, OZON_ATTR_IDS["qty_pack"], [target["pack_qty"]])
    _set_ozon_attr(attrs, OZON_ATTR_IDS["qty_unit"], [target["pack_qty"]])
    _set_ozon_attr(attrs, OZON_ATTR_IDS["color"], target["colors"], (dictionary_values or {}).get(OZON_ATTR_IDS["color"]))
    _set_ozon_attr(attrs, OZON_ATTR_IDS["color_name"], [target["color_name"]])
    _set_ozon_attr(attrs, OZON_ATTR_IDS["tnved"], [target["tnved"]])
    _set_ozon_attr(attrs, OZON_ATTR_IDS["country"], [target["country"]])
    _set_ozon_attr(attrs, OZON_ATTR_IDS["release_type"], [target["release_type"]])
    _set_ozon_attr(attrs, OZON_ATTR_IDS["adult"], [target["adult"]])
    _set_ozon_attr(attrs, OZON_ATTR_IDS["factory_packs"], [target["factory_packs"]])
    _set_ozon_attr(attrs, OZON_ATTR_IDS["marking_required"], [target["marking_required"]])
    _set_ozon_attr(attrs, OZON_ATTR_IDS["package_weight"], [target["weight_g"]])

    dimensions = _parse_ozon_package_mm(target["package_mm"])
    price = _price_value(price_row, "ozon_price")
    old_price = _price_value(price_row, "ozon_old_price")
    if not price:
        errors.append("ozon_price_missing_for_v3_import")
    if not old_price:
        errors.append("ozon_old_price_missing_for_v3_import")
    if not current.get("primary_image"):
        errors.append("ozon_primary_image_missing_for_v3_import")
    if not current.get("images"):
        errors.append("ozon_images_missing_for_v3_import")
    if not dimensions:
        errors.append("ozon_package_dimensions_missing")
    if not target["title"] or not target["description"]:
        errors.append("ozon_title_or_description_missing")
    for attr in attrs:
        if int(attr.get("id") or 0) == OZON_ATTR_IDS["color"]:
            if any(int(value.get("dictionary_value_id") or 0) == 0 for value in attr.get("values") or [] if isinstance(value, dict)):
                errors.append("ozon_color_dictionary_value_missing")
    if errors:
        return None, [], errors

    item = {
        "attributes": attrs,
        "barcode": _normalize_text(current.get("barcode") or (current.get("barcodes") or [""])[0]),
        "color_image": _normalize_text(current.get("color_image")),
        "complex_attributes": deepcopy(current.get("complex_attributes") or []),
        "currency_code": "RUB",
        "description_category_id": int(current.get("description_category_id")),
        "depth": dimensions["depth"],
        "dimension_unit": "mm",
        "height": dimensions["height"],
        "images": list(current.get("images") or []),
        "images360": list(current.get("images360") or []),
        "name": target["title"],
        "offer_id": _normalize_text(current.get("offer_id")),
        "old_price": old_price,
        "pdf_list": list(current.get("pdf_list") or []),
        "price": price,
        "primary_image": _normalize_text(current.get("primary_image")),
        "type_id": int(current.get("type_id")),
        "vat": _normalize_text(price_row.get("ozon_vat") or current.get("vat") or "0.00"),
        "weight": int(float(target["weight_g"] or current.get("weight") or 10)),
        "weight_unit": "g",
        "width": dimensions["width"],
    }
    changes = [
        {"field": "name/4180", "current": _normalize_text(current.get("name")), "target": target["title"]},
        {"field": "4191", "current": _first_attr_value(current, OZON_ATTR_IDS["description"]), "target": target["description"]},
        {"field": "10096", "current": ", ".join(_attr_values(next((a for a in current.get("attributes", []) if a.get("id") == OZON_ATTR_IDS["color"]), {}))), "target": ", ".join(target["colors"])},
        {"field": "dimensions", "current": f"{current.get('depth')}*{current.get('width')}*{current.get('height')}", "target": target["package_mm"]},
        {"field": "weight", "current": _normalize_text(current.get("weight")), "target": target["weight_g"]},
    ]
    return item, changes, []


def _build_ozon_verify_payload(passport: dict[str, Any], current: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    target = _ozon_target(passport)
    errors: list[str] = []
    dimensions = _parse_ozon_package_mm(target["package_mm"])
    if not dimensions:
        errors.append("ozon_package_dimensions_missing")
    if not target["title"] or not target["description"]:
        errors.append("ozon_title_or_description_missing")
    if not current.get("offer_id"):
        errors.append("ozon_current_offer_id_missing")
    if errors:
        return None, errors

    attrs = deepcopy(current.get("attributes") or [])
    _set_ozon_attr(attrs, OZON_ATTR_IDS["title"], [target["title"]])
    _set_ozon_attr(attrs, OZON_ATTR_IDS["description"], [target["description"]])
    if not target["hashtags_in_write_payload"]:
        _remove_ozon_attr(attrs, OZON_ATTR_IDS["hashtags"])
    elif target["hashtags"]:
        _set_ozon_attr(attrs, OZON_ATTR_IDS["hashtags"], [target["hashtags"]])
    _set_ozon_attr(attrs, OZON_ATTR_IDS["color"], target["colors"])
    _set_ozon_attr(attrs, OZON_ATTR_IDS["color_name"], [target["color_name"]])
    _set_ozon_attr(attrs, OZON_ATTR_IDS["marking_required"], [target["marking_required"]])
    _set_ozon_attr(attrs, OZON_ATTR_IDS["package_weight"], [target["weight_g"]])

    return {
        "attributes": attrs,
        "depth": dimensions["depth"],
        "height": dimensions["height"],
        "images": list(current.get("images") or []),
        "name": target["title"],
        "offer_id": _normalize_text(current.get("offer_id")),
        "primary_image": _normalize_text(current.get("primary_image")),
        "weight": int(float(target["weight_g"] or current.get("weight") or 0)),
        "width": dimensions["width"],
    }, []


def _wb_target(passport: dict[str, Any]) -> dict[str, Any]:
    content = passport.get("content") or {}
    physical = passport.get("physical") or {}
    wb = passport.get("wb") or {}
    wb_attrs = wb.get("attributes") or []
    adult_constraint = (wb.get("write_constraints") or {}).get("isAdult") or {}
    return {
        "title": _normalize_text(content.get("wb_title") or content.get("canonical_title")),
        "description": _normalize_text(content.get("wb_description") or content.get("canonical_description")),
        "colors": _normalize_wb_colors(_split_values(_field_value(wb_attrs, "Цвет"))),
        "dimensions": _parse_wb_package_cm(_normalize_text(physical.get("package_dimensions_wb_cm"))),
        "weight": _kg_from_g(physical.get("package_weight_g") or physical.get("item_weight_g")),
        "decor_type": _field_value(wb_attrs, "Вид декора для одежды") or "шеврон",
        "composition": _split_values(_field_value(wb_attrs, "Состав") or "полиэстер; нейлон"),
        "country": "Россия",
        "qty": _field_value(wb_attrs, "Количество предметов") or f"{physical.get('pack_qty') or 1} шт.",
        "kit": _field_value(wb_attrs, "Комплектация") or _normalize_text((passport.get("content") or {}).get("package_contents")),
        "tnved": "5810999000",
        "is_adult": adult_constraint.get("target") is True,
        "is_adult_apply_condition": _normalize_text(adult_constraint.get("apply_condition")),
    }


def _set_wb_char(chars: list[dict[str, Any]], char_id: int, value: list[str] | str) -> None:
    values = value if isinstance(value, list) else [value]
    values = [_normalize_text(item) for item in values if _normalize_text(item)]
    if not values:
        return
    for char in chars:
        if int(char.get("id") or 0) == char_id:
            char["value"] = values
            return
    chars.append({"id": char_id, "value": values})


def _build_wb_payload(passport: dict[str, Any], current: dict[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[str]]:
    target = _wb_target(passport)
    errors: list[str] = []
    if not target["title"] or not target["description"]:
        errors.append("wb_title_or_description_missing")
    if not target["dimensions"]:
        errors.append("wb_dimensions_missing")
    if not current.get("nmID") or not current.get("vendorCode") or not current.get("sizes"):
        errors.append("wb_required_identity_or_sizes_missing")
    if errors:
        return None, [], errors
    chars = deepcopy(current.get("characteristics") or [])
    _set_wb_char(chars, WB_CHAR_IDS["color"], target["colors"])
    _set_wb_char(chars, WB_CHAR_IDS["decor_type"], target["decor_type"])
    _set_wb_char(chars, WB_CHAR_IDS["composition"], target["composition"])
    _set_wb_char(chars, WB_CHAR_IDS["country"], target["country"])
    _set_wb_char(chars, WB_CHAR_IDS["qty"], target["qty"])
    _set_wb_char(chars, WB_CHAR_IDS["kit"], target["kit"])
    _set_wb_char(chars, WB_CHAR_IDS["tnved"], target["tnved"])
    dimensions = {**target["dimensions"], "weightBrutto": target["weight"]}
    variant = {
        "nmID": int(current["nmID"]),
        "vendorCode": _normalize_text(current["vendorCode"]),
        "title": target["title"],
        "description": target["description"],
        "brand": current.get("brand") or "VitalEmb",
        "dimensions": dimensions,
        "characteristics": chars,
        "sizes": deepcopy(current.get("sizes") or []),
    }
    if "isAdult" in current:
        variant["isAdult"] = bool(current.get("isAdult"))
    if target["is_adult"]:
        variant["isAdult"] = True
    if "kizMarked" in current:
        variant["kizMarked"] = bool(current.get("kizMarked"))
    changes = [
        {"field": "title", "current": _normalize_text(current.get("title")), "target": target["title"]},
        {"field": "description", "current": _normalize_text(current.get("description")), "target": target["description"]},
        {"field": "dimensions", "current": current.get("dimensions"), "target": dimensions},
        {"field": "color", "current": _normalize_text(current.get("characteristics")), "target": target["colors"]},
    ]
    if target["is_adult"] and current.get("isAdult") is not True:
        changes.append(
            {
                "field": "isAdult",
                "current": current.get("isAdult"),
                "target": True,
                "apply_condition": target["is_adult_apply_condition"] or "only_if_current_not_true",
            }
        )
    return variant, changes, []


def _wb_media_urls_from_passport(passport: dict[str, Any]) -> list[str]:
    media = passport.get("media") or {}
    urls: list[str] = []
    for asset in media.get("target_assets") or []:
        if not isinstance(asset, dict):
            continue
        url = _normalize_text(asset.get("url"))
        if url and url not in urls:
            urls.append(url)
    return urls


def _wb_media_plan_from_passport(
    passport: dict[str, Any],
    *,
    data_dir: Path,
) -> tuple[list[dict[str, Any]], list[str]]:
    dangerous_actions = set((passport.get("safety") or {}).get("dangerous_actions") or [])
    if "wb_media_update" not in dangerous_actions:
        return [], []
    media_plan = build_wb_media_plan(passport)
    errors = validate_wb_media_plan(
        data_dir=data_dir,
        media_plan=media_plan,
        require_contiguous=False,
    )
    return media_plan, errors


def _plan_one(
    *,
    passport: dict[str, Any],
    data_dir: Path,
    credentials: AppCredentials,
    skip_api: bool,
    price_rows: dict[str, dict[str, Any]],
    run_dir: Path,
    marketplaces: set[str],
) -> dict[str, Any]:
    identity = passport.get("identity") or {}
    sku = _normalize_text(identity.get("internal_sku"))
    row: dict[str, Any] = {"internal_sku": sku, "identity": identity, "ready": True, "errors": [], "marketplaces": {}}
    ozon_offer_id = _normalize_text(identity.get("ozon_offer_id"))
    if ozon_offer_id and "ozon" in marketplaces:
        current_ozon = None
        ozon_dictionary_values: dict[int, dict[str, int]] = {}
        if credentials.ozon_seller and not skip_api:
            ozon = OzonSellerAdapter(credentials.ozon_seller)
            items = ozon.fetch_product_attributes([ozon_offer_id])
            current_ozon = items[0] if items else None
            if current_ozon:
                try:
                    values = ozon.fetch_description_category_attribute_values(
                        description_category_id=int(current_ozon.get("description_category_id")),
                        type_id=int(current_ozon.get("type_id")),
                        attribute_id=OZON_ATTR_IDS["color"],
                        limit=100,
                    )
                    ozon_dictionary_values[OZON_ATTR_IDS["color"]] = {
                        _normalize_text(item.get("value")).lower(): int(item.get("id") or 0)
                        for item in values
                        if _normalize_text(item.get("value")) and int(item.get("id") or 0)
                    }
                except ApiError as exc:
                    write_json(run_dir / f"ozon_color_dictionary_error_{sku}.json", {"status": exc.status, "message": exc.message[:1000]})
        current_ozon = current_ozon or _find_local_ozon(data_dir, ozon_offer_id)
        if current_ozon:
            write_json(run_dir / f"ozon_current_{sku}.json", current_ozon)
            payload, changes, errors = _build_ozon_payload(
                passport=passport,
                current=current_ozon,
                price_row=price_rows.get(sku, {}),
                dictionary_values=ozon_dictionary_values,
            )
            row["marketplaces"]["ozon"] = {
                "status": "ready" if payload else "blocked",
                "payload": payload,
                "changes": changes,
                "errors": errors,
            }
            row["ready"] = row["ready"] and bool(payload)
            row["errors"].extend(errors)
        else:
            row["marketplaces"]["ozon"] = {"status": "blocked", "errors": ["ozon_current_card_not_found"]}
            row["ready"] = False
            row["errors"].append("ozon_current_card_not_found")
    wb_vendor_code = _normalize_text(identity.get("wb_vendor_code"))
    if wb_vendor_code and "wb" in marketplaces:
        current_wb = None
        if credentials.wb and not skip_api:
            current_wb = WbContentAdapter(credentials.wb).find_cards_by_vendor_codes({wb_vendor_code}).get(wb_vendor_code)
        current_wb = current_wb or _find_local_wb(data_dir, wb_vendor_code)
        if current_wb:
            write_json(run_dir / f"wb_current_{sku}.json", current_wb)
            payload, changes, errors = _build_wb_payload(passport, current_wb)
            media_plan, media_errors = _wb_media_plan_from_passport(
                passport,
                data_dir=data_dir,
            )
            errors.extend(media_errors)
            row["marketplaces"]["wb"] = {
                "status": "ready" if payload and not media_errors else "blocked",
                "payload": payload,
                "changes": changes,
                "errors": errors,
                "media_urls": [
                    _normalize_text(item.get("source_url"))
                    for item in media_plan
                    if item.get("source_kind") == "owner_approved_url"
                ],
                "media_plan": media_plan,
            }
            row["ready"] = row["ready"] and bool(payload) and not media_errors
            row["errors"].extend(errors)
        else:
            row["marketplaces"]["wb"] = {"status": "blocked", "errors": ["wb_current_card_not_found"]}
            row["ready"] = False
            row["errors"].append("wb_current_card_not_found")
    if not row["marketplaces"]:
        row["ready"] = False
        row["errors"].append("no_marketplace_identity")
    return row


def run_card_content_update_plan(
    *,
    credentials: AppCredentials,
    data_dir: Path = Path("data"),
    passport_paths: list[Path] | None = None,
    internal_skus: list[str] | None = None,
    run_id: str | None = None,
    skip_api: bool = False,
    marketplaces: list[str] | None = None,
) -> dict[str, Any]:
    started_at = datetime.now()
    run_id = run_id or f"card_content_update_plan_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    paths = _passport_paths(data_dir=data_dir, passport_paths=passport_paths or [], internal_skus=internal_skus or [])
    prices = _price_rows(data_dir)
    selected_marketplaces = set(marketplaces or ["ozon", "wb"])
    invalid_marketplaces = selected_marketplaces - {"ozon", "wb"}
    if invalid_marketplaces:
        raise ValueError(
            f"Unsupported marketplaces: {sorted(invalid_marketplaces)}"
        )
    if not selected_marketplaces:
        raise ValueError("At least one marketplace is required")
    plan = [
        _plan_one(
            passport=_read_json(path),
            data_dir=data_dir,
            credentials=credentials,
            skip_api=skip_api,
            price_rows=prices,
            run_dir=run_dir,
            marketplaces=selected_marketplaces,
        )
        for path in paths
    ]
    plan_path = run_dir / "card_content_update_plan.json"
    write_json(plan_path, plan)
    ready_rows = sum(1 for row in plan if row.get("ready"))
    summary = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "dry_run",
        "overall_status": "ok" if ready_rows == len(plan) else "warning",
        "input_rows": len(plan),
        "ready_rows": ready_rows,
        "blocked_rows": len(plan) - ready_rows,
        "skip_api": skip_api,
        "marketplaces": sorted(selected_marketplaces),
        "artifacts": {"run_dir": str(run_dir), "plan": str(plan_path)},
    }
    write_json(run_dir / "summary.json", summary)
    manifest = write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=summary,
        task="card-content-update-plan",
        mode="dry_run",
        risk="high",
        marketplaces=sorted(selected_marketplaces),
        inputs={
            "passport_paths": [str(path) for path in paths],
            "internal_skus": internal_skus or [],
            "skip_api": skip_api,
            "marketplaces": sorted(selected_marketplaces),
        },
        pending_id=run_id,
        lifecycle_status="pending_review",
    )
    summary["artifacts"]["run_manifest"] = manifest["manifest"]
    write_json(run_dir / "summary.json", summary)
    return summary


def _resolve_plan_dir(data_dir: Path, plan_run_id: str | None) -> Path:
    runs_dir = data_dir / "runs"
    candidates = sorted(runs_dir.glob(f"*/{plan_run_id}")) if plan_run_id else sorted(runs_dir.glob("*/card_content_update_plan_*"))
    candidates = [path for path in candidates if path.is_dir() and (path / "card_content_update_plan.json").exists()]
    if not candidates:
        raise FileNotFoundError(f"Card content update plan not found: {plan_run_id or 'latest'}")
    return candidates[-1]


def _wait_ozon_import(
    ozon: OzonSellerAdapter,
    task_id: int,
    run_dir: Path,
    wait_seconds: int,
    poll_interval: int,
    *,
    artifact_prefix: str = "ozon_import_info",
) -> dict[str, Any]:
    deadline = time.monotonic() + max(wait_seconds, 0)
    attempt = 0
    result: dict[str, Any] = {}
    while True:
        attempt += 1
        result = ozon.fetch_product_import_info(task_id)
        write_json(run_dir / f"{artifact_prefix}_{attempt:02d}.json", result)
        text = json.dumps(result, ensure_ascii=False).lower()
        if "imported" in text or "failed" in text or time.monotonic() >= deadline:
            return result
        time.sleep(max(poll_interval, 1))


def _ozon_attribute_update_items(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for payload in payloads:
        attrs = [
            deepcopy(attr)
            for attr in payload.get("attributes") or []
            if attr.get("values")
        ]
        offer_id = _normalize_text(payload.get("offer_id"))
        if offer_id and attrs:
            items.append({"offer_id": offer_id, "attributes": attrs})
    return items


def _ozon_task_id(response: dict[str, Any]) -> Any:
    if not isinstance(response, dict):
        return None
    result = response.get("result") if isinstance(response.get("result"), dict) else {}
    return result.get("task_id") or response.get("task_id")


def _ozon_attr_values(item: dict[str, Any], attr_id: int) -> list[str]:
    for attr in item.get("attributes") or []:
        if int(attr.get("id") or 0) == attr_id:
            return _attr_values(attr)
    return []


def _same_list(left: list[Any], right: list[Any]) -> bool:
    return [_normalize_text(item) for item in left] == [_normalize_text(item) for item in right]


def _same_number(left: Any, right: Any) -> bool:
    try:
        return float(left) == float(right)
    except (TypeError, ValueError):
        return _normalize_text(left) == _normalize_text(right)


def _same_dimensions(left: dict[str, Any] | None, right: dict[str, Any] | None) -> bool:
    left = left or {}
    right = right or {}
    for key in ("length", "width", "height", "depth", "weightBrutto"):
        if key not in left and key not in right:
            continue
        if not _same_number(left.get(key), right.get(key)):
            return False
    return True


def _ozon_product_status_is_blocking(statuses: dict[str, Any]) -> bool:
    status_failed = _normalize_text(statuses.get("status_failed")).lower()
    status_description = _normalize_text(statuses.get("status_description")).lower()
    status_tooltip = _normalize_text(statuses.get("status_tooltip")).lower()
    if status_failed and status_failed not in {"imported"}:
        return True
    if "не обновлен" in status_description or "не обновлён" in status_description:
        return True
    if "не удалось обновить" in status_tooltip:
        return True
    return False


def _ozon_target_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "offer_id": _normalize_text(payload.get("offer_id")),
        "product_id": _normalize_text(payload.get("id") or payload.get("product_id")),
        "name": _normalize_text(payload.get("name")),
        "description": _first_attr_value(payload, OZON_ATTR_IDS["description"]),
        "hashtags": _ozon_attr_values(payload, OZON_ATTR_IDS["hashtags"]),
        "colors": _ozon_attr_values(payload, OZON_ATTR_IDS["color"]),
        "marking_required": _ozon_attr_values(payload, OZON_ATTR_IDS["marking_required"]),
        "package_weight_attr": _ozon_attr_values(payload, OZON_ATTR_IDS["package_weight"]),
        "dimensions": {
            "depth": int(payload.get("depth") or 0),
            "width": int(payload.get("width") or 0),
            "height": int(payload.get("height") or 0),
        },
        "weight": int(float(payload.get("weight") or 0)),
        "photo_count": (1 if _normalize_text(payload.get("primary_image")) else 0) + len(payload.get("images") or []),
    }


def _verify_ozon_payloads(ozon: OzonSellerAdapter, payloads: list[dict[str, Any]], run_dir: Path) -> dict[str, Any]:
    targets = {_normalize_text(payload.get("offer_id")): _ozon_target_from_payload(payload) for payload in payloads}
    items = ozon.fetch_product_attributes(list(targets))
    write_json(run_dir / "ozon_verify_cards.json", items)
    product_ids = [
        _normalize_text(item.get("id") or item.get("product_id"))
        for item in items
        if _normalize_text(item.get("id") or item.get("product_id"))
    ]
    status_by_offer_id: dict[str, dict[str, Any]] = {}
    if product_ids:
        info_items = ozon.fetch_product_info(product_ids)
        write_json(run_dir / "ozon_verify_product_info.json", info_items)
        status_by_product_id = {
            _normalize_text(item.get("id") or item.get("product_id")): item
            for item in info_items
            if isinstance(item, dict)
        }
        for item in items:
            offer_id = _normalize_text(item.get("offer_id"))
            product_id = _normalize_text(item.get("id") or item.get("product_id"))
            if offer_id and product_id:
                status_by_offer_id[offer_id] = status_by_product_id.get(product_id, {})
    results = []
    for offer_id, target in targets.items():
        item = next((row for row in items if _normalize_text(row.get("offer_id")) == offer_id), None)
        checks: dict[str, Any] = {}
        if not item:
            results.append({"offer_id": offer_id, "status": "missing", "checks": checks})
            continue
        product_info = status_by_offer_id.get(offer_id, {})
        statuses = product_info.get("statuses") if isinstance(product_info.get("statuses"), dict) else {}
        product_errors = product_info.get("errors") if isinstance(product_info.get("errors"), list) else []
        checks["name"] = _normalize_space(item.get("name")) == _normalize_space(target["name"])
        checks["description"] = _normalize_space(_first_attr_value(item, OZON_ATTR_IDS["description"])) == _normalize_space(target["description"])
        checks["hashtags"] = _same_list(
            _ozon_attr_values(item, OZON_ATTR_IDS["hashtags"]),
            target["hashtags"],
        )
        checks["colors"] = _same_list(_ozon_attr_values(item, OZON_ATTR_IDS["color"]), target["colors"])
        checks["marking_required"] = _same_list(_ozon_attr_values(item, OZON_ATTR_IDS["marking_required"]), target["marking_required"])
        checks["package_weight_attr"] = _same_list(_ozon_attr_values(item, OZON_ATTR_IDS["package_weight"]), target["package_weight_attr"])
        checks["dimensions"] = {
            "depth": int(item.get("depth") or 0),
            "width": int(item.get("width") or 0),
            "height": int(item.get("height") or 0),
        } == target["dimensions"]
        checks["weight"] = int(float(item.get("weight") or 0)) == target["weight"]
        checks["photo_count"] = ((1 if _normalize_text(item.get("primary_image")) else 0) + len(item.get("images") or [])) >= target["photo_count"]
        checks["product_info_errors"] = not product_errors
        checks["product_status"] = not _ozon_product_status_is_blocking(statuses)
        results.append(
            {
                "offer_id": offer_id,
                "product_id": _normalize_text(item.get("id") or item.get("product_id")),
                "status": "ok" if all(checks.values()) else "warning",
                "checks": checks,
                "product_statuses": statuses,
                "product_errors": product_errors,
            }
        )
    summary = {
        "status": "ok" if results and all(item["status"] == "ok" for item in results) else "warning",
        "rows": len(results),
        "results": results,
    }
    write_json(run_dir / "ozon_verify_summary.json", summary)
    return summary


def _verify_one(
    *,
    passport: dict[str, Any],
    data_dir: Path,
    credentials: AppCredentials,
    skip_api: bool,
    run_dir: Path,
) -> dict[str, Any]:
    identity = passport.get("identity") or {}
    sku = _normalize_text(identity.get("internal_sku"))
    row: dict[str, Any] = {"internal_sku": sku, "identity": identity, "status": "ok", "errors": [], "marketplaces": {}}

    # Target offer IDs do not prove that an Ozon card exists, especially for
    # WB-only passports that may be prepared for a future Ozon create.
    ozon_offer_id = _normalize_text(identity.get("ozon_offer_id"))
    if ozon_offer_id:
        current_ozon = None
        if credentials.ozon_seller and not skip_api:
            current_items = OzonSellerAdapter(credentials.ozon_seller).fetch_product_attributes([ozon_offer_id])
            current_ozon = current_items[0] if current_items else None
        current_ozon = current_ozon or _find_local_ozon(data_dir, ozon_offer_id)
        if not current_ozon:
            row["marketplaces"]["ozon"] = {"status": "missing", "errors": ["ozon_current_card_not_found"]}
            row["errors"].append("ozon_current_card_not_found")
        else:
            write_json(run_dir / f"ozon_verify_current_{sku}.json", current_ozon)
            payload, errors = _build_ozon_verify_payload(passport, current_ozon)
            row["marketplaces"]["ozon"] = {"status": "ready" if payload else "blocked", "payload": payload, "errors": errors}
            row["errors"].extend(errors)

    wb_vendor_code = _normalize_text(
        identity.get("wb_vendor_code_after_seller_sku_update")
        or identity.get("wb_vendor_code")
        or (passport.get("wb") or {}).get("vendor_code_after_seller_sku_update")
    )
    if wb_vendor_code:
        current_wb = None
        if credentials.wb and not skip_api:
            current_wb = WbContentAdapter(credentials.wb).find_cards_by_vendor_codes({wb_vendor_code}).get(wb_vendor_code)
        current_wb = current_wb or _find_local_wb(data_dir, wb_vendor_code)
        if not current_wb:
            row["marketplaces"]["wb"] = {"status": "missing", "errors": ["wb_current_card_not_found"]}
            row["errors"].append("wb_current_card_not_found")
        else:
            write_json(run_dir / f"wb_verify_current_{sku}.json", current_wb)
            payload, _, errors = _build_wb_payload(passport, current_wb)
            row["marketplaces"]["wb"] = {"status": "ready" if payload else "blocked", "payload": payload, "errors": errors}
            row["errors"].extend(errors)

    if not row["marketplaces"]:
        row["errors"].append("no_marketplace_identity")
    if row["errors"]:
        row["status"] = "blocked"
    return row


def run_card_content_update_verify(
    *,
    credentials: AppCredentials,
    data_dir: Path = Path("data"),
    passport_paths: list[Path] | None = None,
    internal_skus: list[str] | None = None,
    run_id: str | None = None,
    skip_api: bool = False,
) -> dict[str, Any]:
    started_at = datetime.now()
    run_id = run_id or f"card_content_update_verify_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    paths = _passport_paths(data_dir=data_dir, passport_paths=passport_paths or [], internal_skus=internal_skus or [])
    rows = [
        _verify_one(
            passport=_read_json(path),
            data_dir=data_dir,
            credentials=credentials,
            skip_api=skip_api,
            run_dir=run_dir,
        )
        for path in paths
    ]
    write_json(run_dir / "card_content_update_verify_targets.json", rows)
    ozon_payloads = [
        row["marketplaces"]["ozon"]["payload"]
        for row in rows
        if row.get("marketplaces", {}).get("ozon", {}).get("payload")
    ]
    wb_payloads = [
        row["marketplaces"]["wb"]["payload"]
        for row in rows
        if row.get("marketplaces", {}).get("wb", {}).get("payload")
    ]
    ozon_verify: dict[str, Any] = {"status": "skipped", "rows": 0}
    wb_verify: dict[str, Any] = {"status": "skipped", "rows": 0}
    if ozon_payloads:
        if not credentials.ozon_seller and not skip_api:
            raise RuntimeError("Ozon credentials are required")
        if credentials.ozon_seller and not skip_api:
            ozon_verify = _verify_ozon_payloads(OzonSellerAdapter(credentials.ozon_seller), ozon_payloads, run_dir)
    if wb_payloads:
        if not credentials.wb and not skip_api:
            raise RuntimeError("WB credentials are required")
        if credentials.wb and not skip_api:
            wb_verify = _verify_wb_payloads(WbContentAdapter(credentials.wb), wb_payloads, run_dir)
    blocked_rows = [row for row in rows if row.get("status") == "blocked"]
    verify_ok = (
        not blocked_rows
        and ozon_verify.get("status") in {"ok", "skipped"}
        and wb_verify.get("status") in {"ok", "skipped"}
    )
    summary = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "verify",
        "overall_status": "ok" if verify_ok else "warning",
        "input_rows": len(rows),
        "blocked_rows": len(blocked_rows),
        "skip_api": skip_api,
        "verify": {"ozon": ozon_verify, "wb": wb_verify},
        "artifacts": {
            "run_dir": str(run_dir),
            "targets": str(run_dir / "card_content_update_verify_targets.json"),
            "summary": str(run_dir / "summary.json"),
        },
    }
    write_json(run_dir / "summary.json", summary)
    manifest = write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=summary,
        task="card-content-update-verify",
        mode="verify",
        risk="low",
        marketplaces=["ozon", "wb"],
        inputs={"passport_paths": [str(path) for path in paths], "internal_skus": internal_skus or [], "skip_api": skip_api},
        lifecycle_status="closed" if verify_ok else "needs_attention",
        closed=verify_ok,
    )
    summary["artifacts"]["run_manifest"] = manifest["manifest"]
    write_json(run_dir / "summary.json", summary)
    return summary


def _wb_char_values(item: dict[str, Any], char_id: int) -> list[str]:
    for char in item.get("characteristics") or []:
        if int(char.get("id") or 0) == char_id:
            value = char.get("value")
            return [_normalize_text(row) for row in value] if isinstance(value, list) else [_normalize_text(value)]
    return []


def _verify_wb_payloads(
    wb: WbContentAdapter,
    payloads: list[dict[str, Any]],
    run_dir: Path,
    media_by_vendor_code: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    codes = {_normalize_text(item.get("vendorCode")) for item in payloads}
    found = wb.find_cards_by_vendor_codes(codes)
    write_json(run_dir / "wb_verify_cards.json", found)
    results = []
    for payload in payloads:
        code = _normalize_text(payload.get("vendorCode"))
        item = found.get(code)
        checks: dict[str, Any] = {}
        if not item:
            results.append({"vendorCode": code, "status": "missing", "checks": checks})
            continue
        checks["title"] = _normalize_space(item.get("title")) == _normalize_space(payload.get("title"))
        checks["description"] = _normalize_space(item.get("description")) == _normalize_space(payload.get("description"))
        checks["dimensions"] = _same_dimensions(item.get("dimensions"), payload.get("dimensions"))
        checks["colors"] = _same_list(_wb_char_values(item, WB_CHAR_IDS["color"]), _wb_char_values(payload, WB_CHAR_IDS["color"]))
        if "isAdult" in payload:
            checks["isAdult"] = bool(item.get("isAdult")) == bool(payload.get("isAdult"))
        target_media = (media_by_vendor_code or {}).get(code) or []
        if target_media:
            checks["photo_count"] = len(item.get("photos") or []) >= len(target_media)
        results.append({"vendorCode": code, "status": "ok" if all(checks.values()) else "warning", "checks": checks})
    summary = {
        "status": "ok" if results and all(item["status"] == "ok" for item in results) else "warning",
        "rows": len(results),
        "results": results,
    }
    write_json(run_dir / "wb_verify_summary.json", summary)
    return summary


def run_card_content_update_apply(
    *,
    credentials: AppCredentials,
    data_dir: Path = Path("data"),
    plan_run_id: str | None = None,
    run_id: str | None = None,
    confirmed_by_user: bool = False,
    wait_seconds: int = 180,
    poll_interval: int = 10,
) -> dict[str, Any]:
    if not confirmed_by_user:
        raise RuntimeError("Apply requires explicit user confirmation")
    started_at = datetime.now()
    run_id = run_id or f"card_content_update_apply_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    plan_dir = _resolve_plan_dir(data_dir, plan_run_id)
    approved_id = plan_dir.name
    assert_apply_not_repeated(data_dir=data_dir, approved_id=approved_id, current_run_id=run_id)
    plan = _read_json(plan_dir / "card_content_update_plan.json")
    if not isinstance(plan, list) or not plan:
        raise RuntimeError(f"Card content update plan has no rows: {plan_dir}")
    if any(not row.get("ready") for row in plan):
        raise RuntimeError("Card content update plan contains blocked rows")
    checksum = canonical_checksum(plan)
    ozon_payload = [row["marketplaces"]["ozon"]["payload"] for row in plan if row.get("marketplaces", {}).get("ozon", {}).get("payload")]
    wb_payload = [row["marketplaces"]["wb"]["payload"] for row in plan if row.get("marketplaces", {}).get("wb", {}).get("payload")]
    wb_media_by_code = {
        _normalize_text(row["marketplaces"]["wb"]["payload"].get("vendorCode")): list(
            row["marketplaces"]["wb"].get("media_plan") or []
        )
        for row in plan
        if row.get("marketplaces", {}).get("wb", {}).get("payload")
    }
    if ozon_payload and not credentials.ozon_seller:
        raise RuntimeError("Ozon credentials are required")
    if wb_payload and not credentials.wb:
        raise RuntimeError("WB credentials are required")
    ozon_result: dict[str, Any] = {}
    wb_result: dict[str, Any] = {}
    ozon_verify: dict[str, Any] = {"status": "skipped", "rows": 0}
    wb_verify: dict[str, Any] = {"status": "skipped", "rows": 0}
    if ozon_payload:
        ozon = OzonSellerAdapter(credentials.ozon_seller)
        ozon_attr_update_items = _ozon_attribute_update_items(ozon_payload)
        if ozon_attr_update_items:
            write_json(run_dir / "ozon_attributes_update_request.json", {"items": ozon_attr_update_items})
            ozon_attr_update_result = ozon.update_product_attributes(ozon_attr_update_items)
            write_json(run_dir / "ozon_attributes_update_response.json", ozon_attr_update_result)
            task_id = _ozon_task_id(ozon_attr_update_result)
            if task_id:
                _wait_ozon_import(
                    ozon,
                    int(task_id),
                    run_dir,
                    wait_seconds,
                    poll_interval,
                    artifact_prefix="ozon_attributes_update_info",
                )
        write_json(run_dir / "ozon_import_request.json", {"items": ozon_payload})
        ozon_result = ozon.import_products(ozon_payload)
        write_json(run_dir / "ozon_import_response.json", ozon_result)
        task_id = _ozon_task_id(ozon_result)
        if task_id:
            _wait_ozon_import(ozon, int(task_id), run_dir, wait_seconds, poll_interval)
        ozon_verify = _verify_ozon_payloads(ozon, ozon_payload, run_dir)
    if wb_payload:
        wb = WbContentAdapter(credentials.wb)
        write_json(run_dir / "wb_update_cards_request.json", wb_payload)
        wb_result = wb.update_cards(wb_payload)
        write_json(run_dir / "wb_update_cards_response.json", wb_result)
        media_results = []
        for payload in wb_payload:
            code = _normalize_text(payload.get("vendorCode"))
            media_plan = wb_media_by_code.get(code) or []
            if not media_plan:
                continue
            nm_id = int(payload.get("nmID"))
            has_local = any(
                item.get("source_kind") == "owner_approved_local"
                for item in media_plan
            )
            if not has_local:
                urls = [
                    _normalize_text(item.get("source_url"))
                    for item in media_plan
                    if _normalize_text(item.get("source_url"))
                ]
                request = {"nmId": nm_id, "data": urls}
                write_json(run_dir / f"wb_media_save_request_{code}.json", request)
                response = wb.save_media_links(nm_id=nm_id, urls=urls)
                write_json(run_dir / f"wb_media_save_response_{code}.json", response)
                media_results.append(
                    {
                        "vendorCode": code,
                        "mode": "links",
                        "positions": len(urls),
                        "error": response.get("error"),
                        "errorText": response.get("errorText"),
                    }
                )
                continue
            for entry in sorted(
                media_plan,
                key=lambda item: int(item.get("position") or 0),
            ):
                position = int(entry.get("position") or 0)
                path, actual_sha256 = materialize_wb_media_entry(
                    data_dir=data_dir,
                    run_dir=run_dir,
                    vendor_code=code,
                    entry=entry,
                )
                request = {
                    "nmId": nm_id,
                    "photoNumber": position,
                    "path": str(path),
                    "sha256": actual_sha256,
                    "source_kind": entry.get("source_kind"),
                }
                write_json(
                    run_dir / f"wb_media_file_request_{code}_{position:02d}.json",
                    request,
                )
                response = wb.upload_media_file(
                    nm_id=nm_id,
                    photo_number=position,
                    file_path=path,
                )
                write_json(
                    run_dir / f"wb_media_file_response_{code}_{position:02d}.json",
                    response,
                )
                media_results.append(
                    {
                        "vendorCode": code,
                        "mode": "file",
                        "position": position,
                        "sha256": actual_sha256,
                        "error": response.get("error"),
                        "errorText": response.get("errorText"),
                    }
                )
        if media_results:
            write_json(run_dir / "wb_media_save_summary.json", media_results)
            time.sleep(3)
        wb_verify = _verify_wb_payloads(wb, wb_payload, run_dir, wb_media_by_code)
        try:
            write_json(run_dir / "wb_card_errors_after_content_update.json", wb.fetch_card_errors(limit=100))
        except ApiError as exc:
            write_json(run_dir / "wb_card_errors_after_content_update_error.json", {"status": exc.status, "message": exc.message[:1000]})
    ozon_ok = not ozon_payload or bool((ozon_result.get("result") or {}).get("task_id"))
    wb_ok = not wb_payload or not wb_result.get("error")
    verify_ok = ozon_verify.get("status") in {"ok", "skipped"} and wb_verify.get("status") in {"ok", "skipped"}
    overall_ok = ozon_ok and wb_ok and verify_ok
    summary = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "apply",
        "overall_status": "ok" if overall_ok else "warning",
        "approved_plan_run_id": approved_id,
        "plan_checksum": checksum,
        "ozon_rows": len(ozon_payload),
        "wb_rows": len(wb_payload),
        "verify": {"ozon": ozon_verify, "wb": wb_verify},
        "artifacts": {"run_dir": str(run_dir), "summary": str(run_dir / "summary.json")},
    }
    write_json(run_dir / "summary.json", summary)
    manifest = write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=summary,
        task="card-content-update-apply",
        mode="apply",
        risk="high",
        marketplaces=["ozon", "wb"],
        inputs={"plan_run_id": plan_run_id or ""},
        source_run_ids=[approved_id],
        approved_id=approved_id,
        lifecycle_status="verified" if overall_ok else "applied",
        closed=overall_ok,
    )
    summary["artifacts"]["run_manifest"] = manifest["manifest"]
    write_json(run_dir / "summary.json", summary)
    if overall_ok:
        mark_approved_applied(
            data_dir=data_dir,
            approved_id=approved_id,
            apply_run_id=run_id,
            task="card-content-update-apply",
            status="verified",
            run_manifest_path=manifest["manifest"],
            checksum=checksum,
        )
    return summary


def _compact_apply_result(plan_summary: dict[str, Any], apply_summary: dict[str, Any] | None) -> dict[str, Any]:
    if not apply_summary:
        return {
            "overall_status": "blocked",
            "plan_run_id": plan_summary.get("run_id"),
            "ready_rows": plan_summary.get("ready_rows", 0),
            "blocked_rows": plan_summary.get("blocked_rows", 0),
            "message": "dry-run has blocked rows; apply was not started",
        }
    verify = apply_summary.get("verify") or {}
    return {
        "overall_status": apply_summary.get("overall_status"),
        "plan_run_id": plan_summary.get("run_id"),
        "apply_run_id": apply_summary.get("run_id"),
        "ready_rows": plan_summary.get("ready_rows", 0),
        "blocked_rows": plan_summary.get("blocked_rows", 0),
        "ozon": {
            "rows": apply_summary.get("ozon_rows", 0),
            "verify": (verify.get("ozon") or {}).get("status", "skipped"),
        },
        "wb": {
            "rows": apply_summary.get("wb_rows", 0),
            "verify": (verify.get("wb") or {}).get("status", "skipped"),
        },
        "run_dir": (apply_summary.get("artifacts") or {}).get("run_dir"),
    }


def run_apply_approved_card(
    *,
    credentials: AppCredentials,
    data_dir: Path = Path("data"),
    passport_paths: list[Path] | None = None,
    internal_skus: list[str] | None = None,
    run_id: str | None = None,
    confirmed_by_user: bool = False,
    wait_seconds: int = 180,
    poll_interval: int = 10,
) -> dict[str, Any]:
    if not confirmed_by_user:
        raise RuntimeError("Apply requires explicit user confirmation")
    started_at = datetime.now()
    base_run_id = run_id or f"apply_approved_card_{started_at.strftime('%Y%m%dT%H%M%S')}"
    plan_run_id = f"{base_run_id}_plan"
    apply_run_id = f"{base_run_id}_apply"
    plan_summary = run_card_content_update_plan(
        credentials=credentials,
        data_dir=data_dir,
        passport_paths=passport_paths or [],
        internal_skus=internal_skus or [],
        run_id=plan_run_id,
        skip_api=False,
    )
    apply_summary: dict[str, Any] | None = None
    if plan_summary.get("overall_status") == "ok" and int(plan_summary.get("blocked_rows") or 0) == 0:
        apply_summary = run_card_content_update_apply(
            credentials=credentials,
            data_dir=data_dir,
            plan_run_id=plan_run_id,
            run_id=apply_run_id,
            confirmed_by_user=True,
            wait_seconds=wait_seconds,
            poll_interval=poll_interval,
        )
    result = {
        "run_id": base_run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "apply",
        "task": "apply-approved-card",
        "summary": _compact_apply_result(plan_summary, apply_summary),
        "plan": plan_summary,
        "apply": apply_summary,
    }
    result["overall_status"] = result["summary"]["overall_status"]
    return result
