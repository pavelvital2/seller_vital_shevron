from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any

from seller_agent.config import AppCredentials
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.http import ApiError
from seller_agent.marketplaces.ozon.adapter import OzonSellerAdapter
from seller_agent.reports.writer import ensure_dir, write_json


OZON_DESCRIPTION_CATEGORY_ID = 17028963
OZON_TYPE_ID = 970886657

OZON_ATTR_IDS = {
    "brand": 85,
    "title": 4180,
    "description": 4191,
    "size": 4382,
    "country": 4389,
    "material": 7405,
    "qty_pack": 8513,
    "product_type": 8229,
    "model_name": 9048,
    "adult": 9390,
    "color": 10096,
    "color_name": 10097,
    "factory_packs": 11650,
    "hashtags": 23171,
    "release_type": 22270,
    "tnved": 22232,
    "qty_unit": 8962,
    "needs_marking_code": 23536,
}

OWNER_APPROVED_PASSPORT_STATUSES = {
    "owner_approved",
    "owner_approved_pending_apply",
    "owner_approved_pending_batch_apply",
    "applied_verified",
}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_text(value: Any) -> str:
    text = str(value or "").strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text


def _split_values(value: Any) -> list[str]:
    if isinstance(value, list):
        raw_values = value
    else:
        raw_values = re.split(r"[,;]", _normalize_text(value))
    return [_normalize_text(item) for item in raw_values if _normalize_text(item)]


def _field_value(attrs: list[dict[str, Any]], field: str) -> str:
    for item in attrs:
        if _normalize_text(item.get("field")).lower() == field.lower():
            return _normalize_text(item.get("value"))
    return ""


def _parse_ozon_package_mm(value: str) -> dict[str, int]:
    numbers = [int(item) for item in re.findall(r"\d+", value or "")]
    if len(numbers) < 3:
        return {}
    return {"depth": numbers[0], "width": numbers[1], "height": numbers[2]}


def _passport_paths(data_dir: Path, internal_skus: list[str]) -> list[Path]:
    approved_dir = data_dir / "catalog" / "master_passport" / "approved"
    paths = [approved_dir / f"{sku}.json" for sku in internal_skus if _normalize_text(sku)]
    if not paths:
        paths = sorted(approved_dir.glob("*.json"))
    missing = [path for path in paths if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Approved master passport not found: {missing[0]}")
    return paths


def _price_rows(data_dir: Path) -> dict[str, dict[str, Any]]:
    path = data_dir / "pricing" / "pricing_status.json"
    if not path.exists():
        return {}
    rows = _read_json(path)
    if not isinstance(rows, list):
        return {}
    return {
        _normalize_text(row.get("internal_sku")): row
        for row in rows
        if isinstance(row, dict) and _normalize_text(row.get("internal_sku"))
    }


def _local_dictionary_values(
    *,
    data_dir: Path,
    description_category_id: int,
    type_id: int,
) -> dict[int, dict[str, int]]:
    path = data_dir / "catalog" / "content" / "ozon_card_content.json"
    if not path.exists():
        return {}
    payload = _read_json(path)
    rows = payload.get("attributes", []) if isinstance(payload, dict) else []
    result: dict[int, dict[str, int]] = {}
    for item in rows:
        if int(item.get("description_category_id") or 0) != int(description_category_id):
            continue
        if int(item.get("type_id") or 0) != int(type_id):
            continue
        for attr in item.get("attributes") or []:
            attr_id = int(attr.get("id") or 0)
            if not attr_id:
                continue
            for value in attr.get("values") or []:
                if not isinstance(value, dict):
                    continue
                text = _normalize_text(value.get("value")).lower()
                dictionary_id = int(value.get("dictionary_value_id") or 0)
                if text and dictionary_id:
                    result.setdefault(attr_id, {}).setdefault(text, dictionary_id)
    return result


def _merge_dictionary_values(*maps: dict[int, dict[str, int]]) -> dict[int, dict[str, int]]:
    result: dict[int, dict[str, int]] = {}
    for mapping in maps:
        for attr_id, values in mapping.items():
            result.setdefault(attr_id, {}).update(values)
    return result


def _money(value: Any) -> str:
    text = _normalize_text(value)
    if not text:
        return ""
    try:
        amount = float(text.replace(",", "."))
    except ValueError:
        return ""
    if amount <= 0:
        return ""
    return f"{amount:.2f}"


def _sku_prefix(value: str) -> str:
    return re.sub(r"\d+$", "", _normalize_text(value))


def _most_common_price_pair(
    *,
    sku: str,
    price_rows: dict[str, dict[str, Any]],
) -> tuple[str, str]:
    prefix = _sku_prefix(sku)
    counts: dict[tuple[str, str], int] = {}
    for row_sku, row in price_rows.items():
        if row_sku == sku or _sku_prefix(row_sku) != prefix:
            continue
        price = _money(row.get("ozon_price"))
        old_price = _money(row.get("ozon_old_price"))
        if not price or not old_price:
            continue
        counts[(price, old_price)] = counts.get((price, old_price), 0) + 1
    if not counts:
        return "", ""
    (price, old_price), _ = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0]
    return price, old_price


def _price_pair(
    *,
    sku: str,
    price_rows: dict[str, dict[str, Any]],
    allow_wb_price_fallback: bool,
) -> tuple[str, str, str, list[str]]:
    row = price_rows.get(sku) or {}
    price = _money(row.get("ozon_price"))
    old_price = _money(row.get("ozon_old_price"))
    if price and old_price:
        return price, old_price, "ozon_price_snapshot", []

    template_price, template_old_price = _most_common_price_pair(sku=sku, price_rows=price_rows)
    if template_price and template_old_price:
        return template_price, template_old_price, "ozon_group_price_template_requires_owner_review", [
            "ozon_price_missing_used_group_template_price"
        ]

    if allow_wb_price_fallback:
        fallback_price = _money(row.get("wb_discounted_price") or row.get("wb_action_price"))
        fallback_old_price = _money(row.get("wb_base_price"))
        if fallback_price and fallback_old_price:
            return fallback_price, fallback_old_price, "wb_price_fallback_requires_owner_review", [
                "ozon_price_missing_used_wb_price_fallback"
            ]

    return "", "", "missing", ["ozon_price_missing"]


def _min_price_value(value: str | None) -> tuple[str, list[str]]:
    text = _money(value)
    if text:
        return text, []
    return "", ["ozon_min_price_missing"]


def _media_urls(passport: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for item in (passport.get("media") or {}).get("target_assets") or []:
        if not isinstance(item, dict):
            continue
        url = _normalize_text(item.get("url"))
        if url.startswith("http") and url not in urls:
            urls.append(url)
    return urls


def _category(passport: dict[str, Any]) -> tuple[int, int]:
    ozon = passport.get("ozon") or {}
    category = ozon.get("description_category_id") or ozon.get("descriptionCategoryId")
    type_id = ozon.get("type_id") or ozon.get("typeId")
    try:
        category_int = int(category or OZON_DESCRIPTION_CATEGORY_ID)
        type_int = int(type_id or OZON_TYPE_ID)
    except (TypeError, ValueError):
        return OZON_DESCRIPTION_CATEGORY_ID, OZON_TYPE_ID
    return category_int, type_int


def _attribute_schema_by_id(
    *,
    ozon: OzonSellerAdapter | None,
    description_category_id: int,
    type_id: int,
    skip_schema_api: bool,
) -> dict[int, dict[str, Any]]:
    if not ozon or skip_schema_api:
        return {}
    try:
        rows = ozon.fetch_description_category_attributes(
            description_category_id=description_category_id,
            type_id=type_id,
        )
    except ApiError:
        return {}
    return {int(row.get("id") or 0): row for row in rows if int(row.get("id") or 0)}


def _dictionary_values(
    *,
    ozon: OzonSellerAdapter | None,
    description_category_id: int,
    type_id: int,
    schema_by_id: dict[int, dict[str, Any]],
    values_by_attr: dict[int, list[str]],
    skip_schema_api: bool,
) -> tuple[dict[int, dict[str, int]], list[str]]:
    if not ozon or skip_schema_api:
        return {}, []
    result: dict[int, dict[str, int]] = {}
    warnings: list[str] = []
    for attr_id, values in values_by_attr.items():
        schema = schema_by_id.get(attr_id) or {}
        if not int(schema.get("dictionary_id") or 0):
            continue
        result[attr_id] = {}
        for value in values:
            try:
                rows = ozon.fetch_description_category_attribute_values(
                    description_category_id=description_category_id,
                    type_id=type_id,
                    attribute_id=attr_id,
                    value=value,
                    limit=20,
                )
            except ApiError as exc:
                warnings.append(f"dictionary_lookup_failed:{attr_id}:{value}:{exc.status}")
                continue
            for row in rows:
                row_value = _normalize_text(row.get("value")).lower()
                row_id = int(row.get("id") or 0)
                if row_value and row_id:
                    result[attr_id][row_value] = row_id
    return result, warnings


def _attr(attr_id: int, values: list[str], dictionaries: dict[int, dict[str, int]]) -> dict[str, Any]:
    return {
        "id": attr_id,
        "complex_id": 0,
        "values": [
            {
                "dictionary_value_id": dictionaries.get(attr_id, {}).get(value.lower(), 0),
                "value": value,
            }
            for value in values
            if _normalize_text(value)
        ],
    }


def _target_values(passport: dict[str, Any]) -> dict[str, Any]:
    content = passport.get("content") or {}
    physical = passport.get("physical") or {}
    materials = passport.get("materials") or {}
    classification = passport.get("classification") or {}
    ozon_attrs = (passport.get("ozon") or {}).get("attributes") or []
    grouping = passport.get("grouping") or {}
    seo = passport.get("seo") or {}

    title = _normalize_text(content.get("ozon_title") or content.get("canonical_title"))
    description = _normalize_text(content.get("ozon_description") or content.get("canonical_description"))
    hashtags = seo.get("ozon_hashtags")
    hashtag_text = " ".join(hashtags) if isinstance(hashtags, list) else _field_value(ozon_attrs, "#Хештеги")
    product_type = _field_value(ozon_attrs, "Тип") or _field_value(ozon_attrs, "Вид декора") or "шеврон"
    model_name = _field_value(ozon_attrs, "Название модели") or _normalize_text(grouping.get("target_group_key"))
    return {
        "brand": _field_value(ozon_attrs, "Бренд") or "VitalEmb",
        "title": title,
        "description": description,
        "product_size": _normalize_text(physical.get("product_size_mm")) or _field_value(ozon_attrs, "Размеры, мм"),
        "country": _field_value(ozon_attrs, "Страна-изготовитель") or _normalize_text(classification.get("country_of_origin")) or "Россия",
        "material": _field_value(ozon_attrs, "Материал") or _normalize_text(materials.get("material")) or "Габардин",
        "qty_pack": _normalize_text(physical.get("pack_qty") or "1"),
        "product_type": product_type,
        "model_name": model_name,
        "adult": _field_value(ozon_attrs, "Целевая аудитория") or "Взрослая",
        "colors": _split_values(_field_value(ozon_attrs, "Цвет товара") or _field_value(ozon_attrs, "Цвет")),
        "color_name": _field_value(ozon_attrs, "Название цвета"),
        "factory_packs": _field_value(ozon_attrs, "Количество заводских упаковок") or "1",
        "hashtags": _normalize_text(hashtag_text),
        "release_type": _field_value(ozon_attrs, "Вид выпуска товара") or "Фабричное производство",
        "tnved": _field_value(ozon_attrs, "ТН ВЭД") or (
            f"{classification.get('tnved') or '5810999000'} - Прочие вышивки из прочих текстильных материалов"
        ),
        "package_mm": _normalize_text(physical.get("package_dimensions_ozon_mm")),
        "weight_g": _normalize_text(physical.get("package_weight_g") or physical.get("item_weight_g") or "10"),
    }


def _build_ozon_create_payload(
    *,
    passport: dict[str, Any],
    price: str,
    old_price: str,
    min_price: str,
    dictionaries: dict[int, dict[str, int]] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[str], dict[str, Any]]:
    identity = passport.get("identity") or {}
    sku = _normalize_text(identity.get("internal_sku"))
    description_category_id, type_id = _category(passport)
    target = _target_values(passport)
    dimensions = _parse_ozon_package_mm(target["package_mm"])
    images = _media_urls(passport)
    errors: list[str] = []

    if _normalize_text(identity.get("ozon_offer_id") or identity.get("ozon_product_id")):
        errors.append("ozon_identity_already_present")
    if not sku:
        errors.append("internal_sku_missing")
    if not target["title"] or not target["description"]:
        errors.append("ozon_title_or_description_missing")
    if not target["colors"]:
        errors.append("ozon_colors_missing")
    if not target["color_name"]:
        errors.append("ozon_color_name_missing")
    if not target["product_size"]:
        errors.append("ozon_product_size_missing")
    if not target["model_name"]:
        errors.append("ozon_model_name_missing")
    if not dimensions:
        errors.append("ozon_package_dimensions_missing")
    if not images:
        errors.append("ozon_images_missing")
    if not price or not old_price:
        errors.append("ozon_price_missing")
    if not min_price:
        errors.append("ozon_min_price_missing")

    dictionaries = dictionaries or {}
    values_by_attr = {
        OZON_ATTR_IDS["brand"]: [target["brand"]],
        OZON_ATTR_IDS["product_type"]: [target["product_type"]],
        OZON_ATTR_IDS["color"]: target["colors"],
        OZON_ATTR_IDS["country"]: [target["country"]],
        OZON_ATTR_IDS["material"]: [target["material"]],
        OZON_ATTR_IDS["adult"]: [target["adult"]],
        OZON_ATTR_IDS["release_type"]: [target["release_type"]],
        OZON_ATTR_IDS["tnved"]: [target["tnved"]],
    }
    for attr_id, values in values_by_attr.items():
        missing = [value for value in values if value and not dictionaries.get(attr_id, {}).get(value.lower())]
        if dictionaries.get(attr_id) and missing:
            errors.extend(f"ozon_dictionary_value_missing:{attr_id}:{value}" for value in missing)

    if errors:
        return None, None, errors, target

    attributes = [
        _attr(OZON_ATTR_IDS["brand"], [target["brand"]], dictionaries),
        _attr(OZON_ATTR_IDS["title"], [target["title"]], dictionaries),
        _attr(OZON_ATTR_IDS["description"], [target["description"]], dictionaries),
        _attr(OZON_ATTR_IDS["size"], [target["product_size"]], dictionaries),
        _attr(OZON_ATTR_IDS["country"], [target["country"]], dictionaries),
        _attr(OZON_ATTR_IDS["material"], [target["material"]], dictionaries),
        _attr(OZON_ATTR_IDS["qty_pack"], [target["qty_pack"]], dictionaries),
        _attr(OZON_ATTR_IDS["product_type"], [target["product_type"]], dictionaries),
        _attr(OZON_ATTR_IDS["model_name"], [target["model_name"]], dictionaries),
        _attr(OZON_ATTR_IDS["adult"], [target["adult"]], dictionaries),
        _attr(OZON_ATTR_IDS["color"], target["colors"], dictionaries),
        _attr(OZON_ATTR_IDS["color_name"], [target["color_name"]], dictionaries),
        _attr(OZON_ATTR_IDS["factory_packs"], [target["factory_packs"]], dictionaries),
        _attr(OZON_ATTR_IDS["release_type"], [target["release_type"]], dictionaries),
        _attr(OZON_ATTR_IDS["tnved"], [target["tnved"]], dictionaries),
        _attr(OZON_ATTR_IDS["qty_unit"], [target["qty_pack"]], dictionaries),
        _attr(OZON_ATTR_IDS["needs_marking_code"], ["false"], dictionaries),
    ]
    if target["hashtags"]:
        attributes.append(_attr(OZON_ATTR_IDS["hashtags"], [target["hashtags"]], dictionaries))

    product_payload = {
        "attributes": [item for item in attributes if item.get("values")],
        "barcode": _normalize_text(identity.get("ozon_barcode")),
        "currency_code": "RUB",
        "description_category_id": description_category_id,
        "depth": dimensions["depth"],
        "dimension_unit": "mm",
        "height": dimensions["height"],
        "images": images[1:],
        "images360": [],
        "name": target["title"],
        "offer_id": sku,
        "old_price": old_price,
        "price": price,
        "primary_image": images[0],
        "type_id": type_id,
        "vat": "0.00",
        "weight": int(float(target["weight_g"])),
        "weight_unit": "g",
        "width": dimensions["width"],
    }
    price_payload = {
        "currency_code": "RUB",
        "offer_id": sku,
        "old_price": old_price,
        "price": price,
        "min_price": min_price,
        "min_price_for_auto_actions_enabled": True,
    }
    return product_payload, price_payload, [], target


def run_ozon_card_create_plan(
    *,
    credentials: AppCredentials,
    data_dir: Path = Path("data"),
    run_id: str | None = None,
    internal_skus: list[str] | None = None,
    allow_wb_price_fallback: bool = False,
    min_price: str | None = None,
    skip_schema_api: bool = False,
) -> dict[str, Any]:
    if not credentials.ozon_seller:
        raise RuntimeError("Ozon Seller credentials are required")

    started_at = datetime.now()
    run_id = run_id or f"ozon_card_create_plan_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    paths = _passport_paths(data_dir, internal_skus or [])
    prices = _price_rows(data_dir)
    ozon = OzonSellerAdapter(credentials.ozon_seller)
    plan_items: list[dict[str, Any]] = []
    payload_items: list[dict[str, Any]] = []
    price_payload_items: list[dict[str, Any]] = []
    schema_cache: dict[tuple[int, int], dict[int, dict[str, Any]]] = {}

    for path in paths:
        passport = _read_json(path)
        identity = passport.get("identity") or {}
        approval = passport.get("approval") or {}
        sku = _normalize_text(identity.get("internal_sku") or path.stem)
        status = _normalize_text(approval.get("status"))
        item: dict[str, Any] = {
            "internal_sku": sku,
            "source_passport": str(path),
            "owner_approval_status": status,
            "ready": False,
            "needs_manual_review": False,
            "errors": [],
            "warnings": [],
            "target": {},
            "payload": None,
            "price_payload": None,
            "price_source": "",
        }
        if status not in OWNER_APPROVED_PASSPORT_STATUSES:
            item["errors"].append("passport_is_not_owner_approved")

        category_id, type_id = _category(passport)
        schema_key = (category_id, type_id)
        if schema_key not in schema_cache:
            schema_cache[schema_key] = _attribute_schema_by_id(
                ozon=ozon,
                description_category_id=category_id,
                type_id=type_id,
                skip_schema_api=skip_schema_api,
            )
        target_preview = _target_values(passport)
        values_by_attr = {
            OZON_ATTR_IDS["brand"]: [target_preview["brand"]],
            OZON_ATTR_IDS["product_type"]: [target_preview["product_type"]],
            OZON_ATTR_IDS["color"]: target_preview["colors"],
            OZON_ATTR_IDS["country"]: [target_preview["country"]],
            OZON_ATTR_IDS["material"]: [target_preview["material"]],
            OZON_ATTR_IDS["adult"]: [target_preview["adult"]],
            OZON_ATTR_IDS["release_type"]: [target_preview["release_type"]],
            OZON_ATTR_IDS["tnved"]: [target_preview["tnved"]],
        }
        api_dictionaries, dict_warnings = _dictionary_values(
            ozon=ozon,
            description_category_id=category_id,
            type_id=type_id,
            schema_by_id=schema_cache[schema_key],
            values_by_attr=values_by_attr,
            skip_schema_api=skip_schema_api,
        )
        dictionaries = _merge_dictionary_values(
            api_dictionaries,
            _local_dictionary_values(
                data_dir=data_dir,
                description_category_id=category_id,
                type_id=type_id,
            ),
        )
        item["warnings"].extend(dict_warnings)

        price, old_price, price_source, price_warnings = _price_pair(
            sku=sku,
            price_rows=prices,
            allow_wb_price_fallback=allow_wb_price_fallback,
        )
        item["price_source"] = price_source
        item["warnings"].extend(price_warnings)
        min_price_target, min_price_warnings = _min_price_value(min_price)
        item["warnings"].extend(min_price_warnings)
        if price_source.endswith("requires_owner_review"):
            item["needs_manual_review"] = True

        try:
            existing = ozon.fetch_product_attributes([sku])
        except ApiError as exc:
            if exc.status == 404 and "item not found" in exc.message.lower():
                existing = []
                item["warnings"].append("ozon_offer_id_not_found_ok_for_create")
            else:
                raise
        if existing:
            item["errors"].append("ozon_offer_id_already_exists")
            write_json(run_dir / f"existing_ozon_{sku}.json", existing)

        payload, price_payload, payload_errors, target = _build_ozon_create_payload(
            passport=passport,
            price=price,
            old_price=old_price,
            min_price=min_price_target,
            dictionaries=dictionaries,
        )
        item["target"] = {
            "description_category_id": category_id,
            "type_id": type_id,
            "title": target.get("title"),
            "model_name": target.get("model_name"),
            "colors": target.get("colors"),
            "photo_count": len(_media_urls(passport)),
            "price": price,
            "old_price": old_price,
            "min_price": min_price_target,
        }
        item["errors"].extend(payload_errors)
        if payload and not item["errors"]:
            item["payload"] = payload
            item["price_payload"] = price_payload
            item["ready"] = True
            payload_items.append(payload)
            if price_payload:
                price_payload_items.append(price_payload)
        plan_items.append(item)

    plan_path = run_dir / "ozon_card_create_plan.json"
    payload_path = run_dir / "ozon_product_import_payload_draft.json"
    price_payload_path = run_dir / "ozon_product_import_prices_payload_draft.json"
    write_json(plan_path, plan_items)
    write_json(payload_path, {"items": payload_items})
    write_json(price_payload_path, {"prices": price_payload_items})
    summary = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "dry_run",
        "overall_status": "ok" if plan_items and all(item["ready"] and not item["needs_manual_review"] for item in plan_items) else "warning",
        "input_rows": len(plan_items),
        "ready_rows": sum(1 for item in plan_items if item["ready"]),
        "blocked_rows": sum(1 for item in plan_items if not item["ready"]),
        "manual_review_items": sum(1 for item in plan_items if item["needs_manual_review"]),
        "allow_wb_price_fallback": allow_wb_price_fallback,
        "min_price": min_price or "",
        "skip_schema_api": skip_schema_api,
        "artifacts": {
            "run_dir": str(run_dir),
            "plan": str(plan_path),
            "payload_draft": str(payload_path),
            "price_payload_draft": str(price_payload_path),
            "report": str(run_dir / "ozon_card_create_dry_run.md"),
            "summary": str(run_dir / "summary.json"),
        },
    }
    write_json(run_dir / "summary.json", summary)
    manifest = write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=summary,
        task="ozon-card-create-plan",
        mode="dry_run",
        risk="high",
        marketplaces=["ozon"],
        inputs={
            "internal_skus": internal_skus or [],
            "allow_wb_price_fallback": allow_wb_price_fallback,
            "min_price": min_price or "",
            "skip_schema_api": skip_schema_api,
        },
        pending_id=run_id,
        lifecycle_status="pending_review",
    )
    summary["artifacts"]["run_manifest"] = manifest["manifest"]
    write_json(run_dir / "summary.json", summary)
    _write_report(run_dir / "ozon_card_create_dry_run.md", run_id, summary, plan_items)
    return summary


def _write_report(path: Path, run_id: str, summary: dict[str, Any], plan_items: list[dict[str, Any]]) -> None:
    lines = [
        "# Ozon Card Create Dry-Run",
        "",
        f"Run ID: `{run_id}`",
        "",
        "## Summary",
        "",
    ]
    for key in sorted(k for k in summary if k != "artifacts"):
        lines.append(f"- `{key}`: {summary[key]}")
    lines.extend(["", "## Items", ""])
    for item in plan_items:
        target = item.get("target") or {}
        lines.extend(
            [
                f"### {item.get('internal_sku')}",
                "",
                f"- ready: `{item.get('ready')}`",
                f"- manual review: `{item.get('needs_manual_review')}`",
                f"- price source: `{item.get('price_source')}`",
                f"- title: {target.get('title') or ''}",
                f"- category/type: `{target.get('description_category_id')}` / `{target.get('type_id')}`",
                f"- model: {target.get('model_name') or ''}",
                f"- colors: {', '.join(target.get('colors') or [])}",
                f"- photos: `{target.get('photo_count') or 0}`",
            ]
        )
        if item.get("errors"):
            lines.append(f"- errors: `{', '.join(item['errors'])}`")
        if item.get("warnings"):
            lines.append(f"- warnings: `{', '.join(item['warnings'])}`")
        lines.append("")
    lines.extend(
        [
            "## Safety",
            "",
            "- This is a dry-run only.",
            "- No request was sent to `POST /v3/product/import`.",
            "- Apply requires owner approval for this exact plan.",
            "- Prices, stocks, promotions and ads are not changed outside the initial Ozon create payload.",
            "",
            "## Artifacts",
            "",
        ]
    )
    for key, value in sorted((summary.get("artifacts") or {}).items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
