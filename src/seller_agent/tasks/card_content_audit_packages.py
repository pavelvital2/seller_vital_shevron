from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime
import html
import json
import re
from pathlib import Path
from typing import Any

from seller_agent.catalog.loader import normalize_sku
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.reports.writer import ensure_dir, write_json


DEFAULT_BACKLOG_PATH = Path("catalog/content/card_content_audit_backlog.csv")
DEFAULT_CONTENT_MASTER_PATH = Path("catalog/content/content_master.csv")
DEFAULT_OZON_CONTENT_PATH = Path("catalog/content/ozon_card_content.json")
DEFAULT_WB_CONTENT_PATH = Path("catalog/content/wb_card_content.json")
DEFAULT_PASSPORT_SCHEMA_PATH = Path("catalog/content/product_passport/master_product_passport.schema.json")
DEFAULT_ATTRIBUTE_MAPPING_PATH = Path("catalog/content/product_passport/passport_attribute_mapping.csv")
DEFAULT_SEO_QUERY_PACK_PATH = Path("catalog/content/seo_query_pack/card_seo_targets.json")
DEFAULT_OUTPUT_DIR = Path("catalog/content/card_audit_packages")
DEFAULT_ALLOWED_SEO_STATUSES = {"ready", "ready_broad_only"}

PACKAGE_INDEX_FIELDS = [
    "package_rank",
    "backlog_rank",
    "audit_priority",
    "business_priority",
    "internal_product_id",
    "internal_sku",
    "product_name",
    "marketplace_presence",
    "mapping_status",
    "ozon_offer_id",
    "wb_vendor_code",
    "ozon_photo_count",
    "wb_photo_count",
    "total_photo_count",
    "seo_query_pack_status",
    "seo_manual_review_reason",
    "visual_audit_status",
    "passport_draft_status",
    "package_dir",
    "audit_report",
    "audit_package_json",
    "photos_html",
]

EXCLUDED_PACKAGE_INDEX_FIELDS = [
    "backlog_rank",
    "audit_priority",
    "business_priority",
    "internal_product_id",
    "internal_sku",
    "product_name",
    "marketplace_presence",
    "mapping_status",
    "ozon_offer_id",
    "wb_vendor_code",
    "seo_query_pack_status",
    "seo_manual_review_reason",
    "exclude_reason",
]


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [
            {key: (value or "").strip() for key, value in row.items()}
            for row in csv.DictReader(handle)
        ]


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def _read_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(str(path))
    return json.loads(path.read_text(encoding="utf-8"))


def _index_by(rows: list[dict[str, str]], field: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        key = normalize_sku(row.get(field))
        if key and key not in result:
            result[key] = row
    return result


def _load_seo_targets(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict) and isinstance(raw.get("card_targets"), list):
        return [row for row in raw["card_targets"] if isinstance(row, dict)]
    if isinstance(raw, list):
        return [row for row in raw if isinstance(row, dict)]
    return []


def _index_seo_targets(rows: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_id: dict[str, dict[str, Any]] = {}
    by_sku: dict[str, dict[str, Any]] = {}
    for row in rows:
        internal_product_id = normalize_sku(row.get("internal_product_id"))
        internal_sku = normalize_sku(row.get("internal_sku"))
        if internal_product_id and internal_product_id not in by_id:
            by_id[internal_product_id] = row
        if internal_sku and internal_sku not in by_sku:
            by_sku[internal_sku] = row
    return by_id, by_sku


def _index_ozon_content(ozon_content: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    attrs_by_offer: dict[str, dict[str, Any]] = {}
    desc_by_offer: dict[str, dict[str, Any]] = {}
    for item in ozon_content.get("attributes") or []:
        if isinstance(item, dict):
            key = normalize_sku(item.get("offer_id"))
            if key and key not in attrs_by_offer:
                attrs_by_offer[key] = item
    for item in ozon_content.get("descriptions") or []:
        if isinstance(item, dict):
            key = normalize_sku(item.get("offer_id"))
            if key and key not in desc_by_offer:
                desc_by_offer[key] = item
    return attrs_by_offer, desc_by_offer


def _index_wb_content(wb_content: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in wb_content:
        if isinstance(item, dict):
            key = normalize_sku(item.get("vendorCode"))
            if key and key not in result:
                result[key] = item
    return result


def _attribute_names(mapping_rows: list[dict[str, str]]) -> tuple[dict[str, str], dict[str, str]]:
    ozon_names: dict[str, str] = {}
    wb_names: dict[str, str] = {}
    for row in mapping_rows:
        if row.get("ozon_attribute_id") and row.get("ozon_attribute_name"):
            ozon_names[row["ozon_attribute_id"]] = row["ozon_attribute_name"]
        if row.get("wb_characteristic_id") and row.get("wb_characteristic_name"):
            wb_names[row["wb_characteristic_id"]] = row["wb_characteristic_name"]
    return ozon_names, wb_names


def _schema_fields(schema: dict[str, Any]) -> list[str]:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return []
    return sorted(str(key) for key in properties)


def _values(raw_values: Any) -> list[str]:
    result: list[str] = []
    for raw in raw_values or []:
        if isinstance(raw, dict):
            value = raw.get("value")
        else:
            value = raw
        text = normalize_sku(value)
        if text:
            result.append(text)
    return result


def _unique_images(*groups: Any) -> list[str]:
    result: list[str] = []
    for group in groups:
        values = group if isinstance(group, list) else [group]
        for value in values:
            if isinstance(value, dict):
                candidate = normalize_sku(
                    value.get("big")
                    or value.get("hq")
                    or value.get("c516x688")
                    or value.get("c246x328")
                    or value.get("tm")
                    or value.get("url")
                    or value.get("photo")
                )
            else:
                candidate = normalize_sku(value)
            if candidate and candidate not in result:
                result.append(candidate)
    return result


def _slug(value: str, fallback: str) -> str:
    text = normalize_sku(value).lower()
    text = re.sub(r"[^a-z0-9а-яё_-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text[:90] or fallback


def _int_text(value: Any) -> int:
    try:
        return int(float(str(value or "0").replace(",", ".")))
    except ValueError:
        return 0


def _first_text(*values: Any) -> str:
    for value in values:
        text = normalize_sku(value)
        if text:
            return text
    return ""


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = normalize_sku(value)
        if text and text not in result:
            result.append(text)
    return result


def _sku_parts(internal_sku: str) -> dict[str, str]:
    parts = [part for part in normalize_sku(internal_sku).split("_") if part]
    result = {
        "product_type": parts[0] if parts else "",
        "wear_position": "",
        "theme_group": "",
        "content_kind": "",
    }
    if len(parts) > 1:
        result["wear_position"] = parts[1]
    if len(parts) > 2:
        result["theme_group"] = parts[2]
    for part in parts:
        if part.startswith("text") or part == "text":
            result["content_kind"] = "text"
        elif part.startswith("pict") or part == "pict":
            result["content_kind"] = "pict"
    return result


def _ozon_attributes(item: dict[str, Any] | None, names: dict[str, str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for attr in (item or {}).get("attributes") or []:
        attr_id = normalize_sku(attr.get("id"))
        result.append(
            {
                "attribute_id": attr_id,
                "attribute_name": names.get(attr_id, ""),
                "complex_id": attr.get("complex_id", 0),
                "values": _values(attr.get("values")),
                "source_status": "confirmed_from_ozon_snapshot",
            }
        )
    return result


def _wb_attributes(item: dict[str, Any] | None, names: dict[str, str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for char in (item or {}).get("characteristics") or []:
        char_id = normalize_sku(char.get("id"))
        values = char.get("value")
        if not isinstance(values, list):
            values = [values]
        result.append(
            {
                "characteristic_id": char_id,
                "characteristic_name": normalize_sku(char.get("name")) or names.get(char_id, ""),
                "values": [normalize_sku(value) for value in values if normalize_sku(value)],
                "source_status": "confirmed_from_wb_snapshot",
            }
        )
    return result


def _photo_assets(
    *,
    ozon_item: dict[str, Any] | None,
    wb_item: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    for index, url in enumerate(_unique_images((ozon_item or {}).get("primary_image"), (ozon_item or {}).get("images")), start=1):
        assets.append(
            {
                "marketplace": "ozon",
                "position": index,
                "url": url,
                "role": "primary" if index == 1 else "additional",
                "visual_audit_status": "pending_agent_review",
                "notes": "",
            }
        )
    for index, url in enumerate(_unique_images((wb_item or {}).get("photos"), (wb_item or {}).get("mediaFiles")), start=1):
        assets.append(
            {
                "marketplace": "wb",
                "position": index,
                "url": url,
                "role": "primary" if index == 1 else "additional",
                "visual_audit_status": "pending_agent_review",
                "notes": "",
            }
        )
    return assets


def _description(ozon_description: dict[str, Any] | None, ozon_item: dict[str, Any] | None, wb_item: dict[str, Any] | None) -> str:
    return _first_text(
        (ozon_description or {}).get("description"),
        (ozon_item or {}).get("description"),
        (ozon_item or {}).get("annotation"),
        (wb_item or {}).get("description"),
    )


def _passport_draft(
    *,
    backlog_row: dict[str, str],
    content_row: dict[str, str],
    ozon_item: dict[str, Any] | None,
    ozon_description: dict[str, Any] | None,
    wb_item: dict[str, Any] | None,
    media_assets: list[dict[str, Any]],
    seo_target: dict[str, Any] | None,
) -> dict[str, Any]:
    internal_sku = _first_text(backlog_row.get("internal_sku"), content_row.get("internal_sku"))
    sku_parts = _sku_parts(internal_sku)
    pack_qty = _first_text(content_row.get("pack_qty"), "1")
    cost_total = _first_text(content_row.get("cost_total"), backlog_row.get("cost_total"))
    description = _description(ozon_description, ozon_item, wb_item)
    title = _first_text(content_row.get("canonical_title"), backlog_row.get("product_name"))
    query_clusters = (seo_target or {}).get("target_query_clusters") or {}
    search_queries = _unique(
        [
            *query_clusters.get("primary_target", []),
            *query_clusters.get("secondary_target", []),
            *query_clusters.get("broad_identity", []),
            *query_clusters.get("placement", []),
        ]
    )

    values = {
        "internal_product_id": _first_text(backlog_row.get("internal_product_id"), content_row.get("internal_product_id")),
        "internal_sku": internal_sku,
        "marketplace_presence": _first_text(backlog_row.get("marketplace_presence"), content_row.get("marketplace_presence")),
        "product_type": sku_parts["product_type"],
        "wear_position": sku_parts["wear_position"],
        "theme_group": sku_parts["theme_group"],
        "content_kind": sku_parts["content_kind"],
        "pack_qty": _int_text(pack_qty) or 1,
        "canonical_title": title,
        "canonical_description": description,
        "image_subject": "",
        "product_width_mm": None,
        "product_height_mm": None,
        "product_depth_mm": None,
        "package_width_mm": None,
        "package_height_mm": None,
        "package_depth_mm": None,
        "item_weight_g": None,
        "package_weight_g": None,
        "material": "",
        "composition": [],
        "attachment_type": "",
        "tnved": "",
        "country_of_origin": "",
        "cost_total": float(cost_total.replace(",", ".")) if cost_total else None,
        "search_queries": search_queries,
        "ozon_hashtags": [],
        "media_assets": media_assets,
        "target_group_key": "",
    }
    field_status = {
        "internal_product_id": "confirmed_from_unified_catalog",
        "internal_sku": "confirmed_from_unified_catalog",
        "marketplace_presence": "confirmed_from_content_master",
        "product_type": "draft_from_internal_sku_needs_agent_review",
        "wear_position": "draft_from_internal_sku_needs_agent_review",
        "theme_group": "draft_from_internal_sku_needs_agent_review",
        "content_kind": "draft_from_internal_sku_needs_agent_review",
        "pack_qty": "confirmed_from_content_master" if pack_qty else "needs_owner_review",
        "canonical_title": "draft_from_content_master_needs_agent_review",
        "canonical_description": "current_snapshot_needs_agent_review" if description else "not_confirmed",
        "image_subject": "needs_agent_visual_review",
        "product_width_mm": "needs_agent_visual_review",
        "product_height_mm": "needs_agent_visual_review",
        "product_depth_mm": "needs_agent_visual_review",
        "package_width_mm": "needs_agent_visual_review_or_owner_rule",
        "package_height_mm": "needs_agent_visual_review_or_owner_rule",
        "package_depth_mm": "needs_agent_visual_review_or_owner_rule",
        "item_weight_g": "needs_owner_rule_mapping",
        "package_weight_g": "needs_owner_rule_mapping",
        "material": "needs_agent_review",
        "composition": "needs_agent_review",
        "attachment_type": "needs_agent_review",
        "tnved": "needs_agent_review",
        "country_of_origin": "not_confirmed",
        "cost_total": "confirmed_from_content_master" if cost_total else "not_confirmed",
        "search_queries": "loaded_from_seo_query_pack" if seo_target else "not_loaded_in_package",
        "ozon_hashtags": "current_snapshot_or_not_loaded",
        "media_assets": "urls_collected_visual_audit_pending",
        "target_group_key": "needs_grouping_audit",
    }
    return {"values": values, "field_status": field_status}


def build_card_audit_packages(
    *,
    backlog_rows: list[dict[str, str]],
    content_rows: list[dict[str, str]],
    ozon_content: dict[str, Any],
    wb_content: list[dict[str, Any]],
    passport_schema: dict[str, Any] | None = None,
    attribute_mapping_rows: list[dict[str, str]] | None = None,
    seo_target_rows: list[dict[str, Any]] | None = None,
    seo_allowed_statuses: set[str] | None = None,
    limit: int | None = None,
    business_priority: str | None = "now",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    content_by_id = _index_by(content_rows, "internal_product_id")
    ozon_by_offer, ozon_desc_by_offer = _index_ozon_content(ozon_content)
    wb_by_vendor = _index_wb_content(wb_content)
    seo_by_id, seo_by_sku = _index_seo_targets(seo_target_rows or [])
    seo_targets_available = bool(seo_target_rows)
    seo_allowed_statuses = seo_allowed_statuses or DEFAULT_ALLOWED_SEO_STATUSES
    ozon_names, wb_names = _attribute_names(attribute_mapping_rows or [])
    schema_fields = _schema_fields(passport_schema or {})
    selected: list[dict[str, str]] = []
    for row in backlog_rows:
        if business_priority is not None and normalize_sku(row.get("business_priority")) != business_priority:
            continue
        selected.append(row)
        if limit is not None and len(selected) >= limit:
            break

    packages: list[dict[str, Any]] = []
    excluded_rows: list[dict[str, Any]] = []
    for row in selected:
        internal_product_id = normalize_sku(row.get("internal_product_id"))
        content_row = content_by_id.get(internal_product_id, {})
        ozon_offer_id = _first_text(row.get("ozon_offer_id"), content_row.get("ozon_offer_id"))
        wb_vendor_code = _first_text(row.get("wb_vendor_code"), content_row.get("wb_vendor_code"))
        internal_sku = _first_text(row.get("internal_sku"), content_row.get("internal_sku"))
        seo_target = seo_by_id.get(internal_product_id) or seo_by_sku.get(normalize_sku(internal_sku))
        seo_status = (seo_target or {}).get("query_pack_status", "missing")
        if seo_targets_available and seo_status not in seo_allowed_statuses:
            excluded_rows.append(
                {
                    "backlog_rank": row.get("backlog_rank", ""),
                    "audit_priority": row.get("audit_priority", ""),
                    "business_priority": row.get("business_priority", ""),
                    "internal_product_id": row.get("internal_product_id", ""),
                    "internal_sku": row.get("internal_sku", ""),
                    "product_name": row.get("product_name", ""),
                    "marketplace_presence": row.get("marketplace_presence", ""),
                    "mapping_status": row.get("mapping_status", ""),
                    "ozon_offer_id": row.get("ozon_offer_id", ""),
                    "wb_vendor_code": row.get("wb_vendor_code", ""),
                    "seo_query_pack_status": seo_status,
                    "seo_manual_review_reason": (seo_target or {}).get("manual_review_reason", ""),
                    "exclude_reason": "seo_query_pack_status_not_allowed_for_mass_card_audit",
                }
            )
            continue
        ozon_item = ozon_by_offer.get(ozon_offer_id)
        ozon_description = ozon_desc_by_offer.get(ozon_offer_id)
        wb_item = wb_by_vendor.get(wb_vendor_code)
        media_assets = _photo_assets(ozon_item=ozon_item, wb_item=wb_item)
        passport_draft = _passport_draft(
            backlog_row=row,
            content_row=content_row,
            ozon_item=ozon_item,
            ozon_description=ozon_description,
            wb_item=wb_item,
            media_assets=media_assets,
            seo_target=seo_target,
        )
        seo_query_pack = {
            "source_status": "found" if seo_target else "missing",
            "query_pack_status": (seo_target or {}).get("query_pack_status", "missing"),
            "manual_review_reason": (seo_target or {}).get("manual_review_reason", ""),
            "target_query_clusters": (seo_target or {}).get("target_query_clusters") or {},
            "confirmed_query_rows": (seo_target or {}).get("confirmed_query_rows") or [],
            "confirmed_query_rows_by_marketplace": (seo_target or {}).get("confirmed_query_rows_by_marketplace") or {},
            "confirmed_query_rows_by_role": (seo_target or {}).get("confirmed_query_rows_by_role") or {},
            "primary_target": (seo_target or {}).get("primary_target", ""),
            "secondary_targets": (seo_target or {}).get("secondary_targets", ""),
            "broad_identity_terms": (seo_target or {}).get("broad_identity_terms", ""),
            "placement_terms": (seo_target or {}).get("placement_terms", ""),
            "excluded_terms": (seo_target or {}).get("excluded_terms", ""),
            "ozon_confirmed_queries": (seo_target or {}).get("ozon_confirmed_queries", ""),
            "wb_confirmed_queries": (seo_target or {}).get("wb_confirmed_queries", ""),
            "ozon_frequency_sum": (seo_target or {}).get("ozon_frequency_sum", ""),
            "wb_frequency_sum": (seo_target or {}).get("wb_frequency_sum", ""),
            "routing_note": (
                "Use ready targets for SEO recommendations; ready_broad_only needs explicit limitation; "
                "needs_manual_review/excluded_non_patch_assortment must not be treated as complete SEO evidence. "
                "Use confirmed_query_rows as row-level demand evidence; do not replace it with aggregate frequency sums."
            ),
        }
        packages.append(
            {
                "package_rank": len(packages) + 1,
                "source_status": "read_only_generated_package",
                "audit_requires_agent_visual_review": True,
                "visual_audit_status": "pending_agent_review",
                "recommendation_status": "not_prepared",
                "passport_draft_status": "draft_needs_agent_review",
                "schema_fields_available": schema_fields,
                "backlog": row,
                "content_master": content_row,
                "current_marketplace_content": {
                    "ozon": {
                        "offer_id": ozon_offer_id,
                        "product_id": _first_text(content_row.get("ozon_product_id"), (ozon_item or {}).get("id")),
                        "sku": _first_text(content_row.get("ozon_sku"), (ozon_item or {}).get("sku")),
                        "title": _first_text((ozon_description or {}).get("name"), (ozon_item or {}).get("name"), content_row.get("ozon_current_title")),
                        "description": _description(ozon_description, ozon_item, None),
                        "dimensions": {
                            "width": (ozon_item or {}).get("width"),
                            "height": (ozon_item or {}).get("height"),
                            "depth": (ozon_item or {}).get("depth"),
                            "weight": (ozon_item or {}).get("weight"),
                            "dimension_unit": (ozon_item or {}).get("dimension_unit"),
                            "weight_unit": (ozon_item or {}).get("weight_unit"),
                        },
                        "model_info": (ozon_item or {}).get("model_info") or {},
                        "attributes": _ozon_attributes(ozon_item, ozon_names),
                        "snapshot_status": "found" if ozon_item or ozon_description else "missing",
                    },
                    "wb": {
                        "vendor_code": wb_vendor_code,
                        "nm_id": _first_text(content_row.get("wb_nm_id"), (wb_item or {}).get("nmID"), (wb_item or {}).get("nmId")),
                        "title": _first_text((wb_item or {}).get("title"), content_row.get("wb_current_title")),
                        "description": normalize_sku((wb_item or {}).get("description")),
                        "subject_id": (wb_item or {}).get("subjectID"),
                        "subject_name": (wb_item or {}).get("subjectName"),
                        "dimensions": (wb_item or {}).get("dimensions") or {},
                        "imt_id": (wb_item or {}).get("imtID"),
                        "characteristics": _wb_attributes(wb_item, wb_names),
                        "snapshot_status": "found" if wb_item else "missing",
                    },
                },
                "media_assets": media_assets,
                "seo_query_pack": seo_query_pack,
                "master_product_passport_draft": passport_draft,
                "grouping_diagnostics": {
                    "ozon_model_info": (ozon_item or {}).get("model_info") or {},
                    "wb_imt_id": (wb_item or {}).get("imtID"),
                    "target_group_key": "",
                    "grouping_review_status": "manual_review_pending",
                    "notes": "Grouping is a separate dangerous operation; this package only collects current state.",
                },
                "next_agent_actions": [
                    "Open all photos from photos_html or media_assets.",
                    "Describe each photo, colors, background, form and visible size evidence.",
                    "Read current title, description and attributes from this package.",
                    "Only then prepare current -> recommended card changes for owner review.",
                ],
            }
        )

    by_priority = Counter(package["backlog"].get("audit_priority", "") for package in packages)
    summary = {
        "input_backlog_rows": len(backlog_rows),
        "input_content_rows": len(content_rows),
        "selected_rows": len(selected),
        "packages": len(packages),
        "excluded_by_seo_status": len(excluded_rows),
        "excluded_needs_manual_review": sum(1 for row in excluded_rows if row["seo_query_pack_status"] == "needs_manual_review"),
        "excluded_non_patch_assortment": sum(1 for row in excluded_rows if row["seo_query_pack_status"] == "excluded_non_patch_assortment"),
        "business_priority_filter": business_priority if business_priority is not None else "all",
        "limit": limit if limit is not None else "",
        "high_priority_packages": by_priority.get("high", 0),
        "normal_priority_packages": by_priority.get("normal", 0),
        "low_priority_packages": by_priority.get("low", 0),
        "total_media_assets": sum(len(package["media_assets"]) for package in packages),
        "packages_with_ozon_snapshot": sum(1 for package in packages if package["current_marketplace_content"]["ozon"]["snapshot_status"] == "found"),
        "packages_with_wb_snapshot": sum(1 for package in packages if package["current_marketplace_content"]["wb"]["snapshot_status"] == "found"),
        "packages_with_seo_query_pack": sum(1 for package in packages if package["seo_query_pack"]["source_status"] == "found"),
        "packages_seo_ready": sum(1 for package in packages if package["seo_query_pack"]["query_pack_status"] == "ready"),
        "packages_seo_broad_only": sum(1 for package in packages if package["seo_query_pack"]["query_pack_status"] == "ready_broad_only"),
        "packages_seo_needs_manual_review": sum(1 for package in packages if package["seo_query_pack"]["query_pack_status"] == "needs_manual_review"),
        "packages_seo_excluded_non_patch": sum(1 for package in packages if package["seo_query_pack"]["query_pack_status"] == "excluded_non_patch_assortment"),
        "visual_audit_completed": 0,
    }
    return packages, excluded_rows, summary


def _write_photos_html(path: Path, package: dict[str, Any]) -> None:
    ensure_dir(path.parent)
    title = html.escape(
        _first_text(
            package["backlog"].get("product_name"),
            package["backlog"].get("internal_product_id"),
        )
    )
    cards = []
    for item in package.get("media_assets", []):
        url = html.escape(item.get("url", ""))
        label = html.escape(f"{item.get('marketplace')} #{item.get('position')} {item.get('role')}")
        cards.append(
            "<figure>"
            f"<img src=\"{url}\" alt=\"{label}\">"
            f"<figcaption>{label}</figcaption>"
            "</figure>"
        )
    body = "\n".join(cards) if cards else "<p>No photo URLs in snapshots.</p>"
    path.write_text(
        "\n".join(
            [
                "<!doctype html>",
                "<html lang=\"ru\">",
                "<head>",
                "<meta charset=\"utf-8\">",
                f"<title>{title}</title>",
                "<style>",
                "body{font-family:Arial,sans-serif;margin:24px;color:#222}",
                ".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:16px}",
                "figure{margin:0;border:1px solid #ddd;padding:8px;background:#fff}",
                "img{display:block;max-width:100%;height:220px;object-fit:contain;margin:auto;background:#f7f7f7}",
                "figcaption{font-size:13px;margin-top:6px;color:#444}",
                "</style>",
                "</head>",
                "<body>",
                f"<h1>{title}</h1>",
                "<p>Generated contact sheet. Visual audit status: pending_agent_review.</p>",
                f"<div class=\"grid\">{body}</div>",
                "</body>",
                "</html>",
            ]
        ),
        encoding="utf-8",
    )


def _write_package_report(path: Path, package: dict[str, Any]) -> None:
    ensure_dir(path.parent)
    backlog = package["backlog"]
    ozon = package["current_marketplace_content"]["ozon"]
    wb = package["current_marketplace_content"]["wb"]
    seo = package.get("seo_query_pack") or {}
    confirmed_query_rows = seo.get("confirmed_query_rows") or []
    confirmed_query_sample = confirmed_query_rows[:8]
    lines = [
        "# Card Audit Package",
        "",
        f"- Internal product: `{backlog.get('internal_product_id')}`",
        f"- Internal SKU: `{backlog.get('internal_sku')}`",
        f"- Product name: {backlog.get('product_name')}",
        f"- Backlog rank: `{backlog.get('backlog_rank')}`",
        f"- Priority: `{backlog.get('audit_priority')}` / business `{backlog.get('business_priority')}`",
        f"- Visual audit status: `{package['visual_audit_status']}`",
        f"- Recommendation status: `{package['recommendation_status']}`",
        "",
        "## Current Ozon",
        "",
        f"- Offer ID: `{ozon.get('offer_id')}`",
        f"- Product ID: `{ozon.get('product_id')}`",
        f"- Title: {ozon.get('title')}",
        f"- Snapshot: `{ozon.get('snapshot_status')}`",
        f"- Photos: `{sum(1 for item in package['media_assets'] if item.get('marketplace') == 'ozon')}`",
        f"- Attributes: `{len(ozon.get('attributes') or [])}`",
        "",
        "## Current WB",
        "",
        f"- Vendor code: `{wb.get('vendor_code')}`",
        f"- nmID: `{wb.get('nm_id')}`",
        f"- Title: {wb.get('title')}",
        f"- Snapshot: `{wb.get('snapshot_status')}`",
        f"- Photos: `{sum(1 for item in package['media_assets'] if item.get('marketplace') == 'wb')}`",
        f"- Characteristics: `{len(wb.get('characteristics') or [])}`",
        "",
        "## SEO Query Pack",
        "",
        f"- Source: `{seo.get('source_status')}`",
        f"- Status: `{seo.get('query_pack_status')}`",
        f"- Manual review reason: `{seo.get('manual_review_reason')}`",
        f"- Primary target: {seo.get('primary_target')}",
        f"- Secondary targets: {seo.get('secondary_targets')}",
        f"- Broad terms: {seo.get('broad_identity_terms')}",
        f"- Placement terms: {seo.get('placement_terms')}",
        f"- Ozon confirmed: {seo.get('ozon_confirmed_queries')}",
        f"- WB confirmed: {seo.get('wb_confirmed_queries')}",
        f"- Row-level confirmed queries: `{len(confirmed_query_rows)}`",
        "",
    ]
    if confirmed_query_sample:
        lines.extend(["### Row-Level Query Sample", ""])
        for item in confirmed_query_sample:
            lines.append(
                "- "
                f"`{item.get('marketplace')}` `{item.get('role')}` "
                f"{item.get('query')} = `{item.get('frequency')}` "
                f"period `{item.get('period')}` source `{item.get('source')}`"
            )
        lines.append("")
    lines.extend(
        [
            "## Grouping",
            "",
            f"- Ozon model_info: `{json.dumps(package['grouping_diagnostics'].get('ozon_model_info') or {}, ensure_ascii=False)}`",
            f"- WB imtID: `{package['grouping_diagnostics'].get('wb_imt_id')}`",
            f"- Status: `{package['grouping_diagnostics'].get('grouping_review_status')}`",
            "",
            "## Next Agent Actions",
            "",
        ]
    )
    for item in package["next_agent_actions"]:
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "## Important Boundary",
            "",
            "- This package is generated read-only source material.",
            "- It is not a completed SEO/card audit.",
            "- The agent must inspect all photos before writing recommendations.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_run_report(path: Path, *, run_id: str, started_at: datetime, result: dict[str, Any]) -> None:
    lines = [
        "# Card Content Audit Packages",
        "",
        f"Run ID: `{run_id}`",
        f"Started at: `{started_at.isoformat(timespec='seconds')}`",
        f"Status: `{result['overall_status']}`",
        "",
        "## Итог",
        "",
        "- Сформированы read-only пакеты исходных данных для карточного аудита.",
        "- Визуальный аудит фото в этом запуске не выполнялся.",
        "- Рекомендации по карточкам должен подготовить агент после просмотра всех фото.",
        "",
        "## Summary",
        "",
    ]
    for key in sorted(result["summary"]):
        lines.append(f"- `{key}`: {result['summary'][key]}")
    lines.extend(["", "## Package Sample", ""])
    for row in result.get("package_sample", [])[:30]:
        lines.append(
            f"- `{row['package_rank']}` `{row['audit_priority']}` "
            f"`{row['internal_product_id']}`: {row['product_name']}"
        )
    if result.get("package_sample_truncated"):
        lines.append(f"- truncated: {result['package_sample_truncated']} more rows")
    lines.extend(["", "## Excluded Sample", ""])
    for row in result.get("excluded_sample", [])[:30]:
        lines.append(
            f"- `{row.get('seo_query_pack_status')}` `{row.get('internal_product_id')}`: "
            f"{row.get('product_name')} ({row.get('seo_manual_review_reason')})"
        )
    if result.get("excluded_sample_truncated"):
        lines.append(f"- truncated: {result['excluded_sample_truncated']} more rows")
    lines.extend(["", "## Artifacts", ""])
    for key, value in sorted(result["artifacts"].items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- `photos_html` is a contact sheet for agent review, not proof of visual inspection.",
            "- `master_product_passport_draft.field_status` shows which fields need agent or owner review.",
            "- No Ozon/WB card, price, media, grouping or SKU write operation is performed.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run_card_content_audit_packages(
    *,
    data_dir: Path = Path("data"),
    backlog_path: Path | None = None,
    content_master_path: Path | None = None,
    ozon_content_path: Path | None = None,
    wb_content_path: Path | None = None,
    passport_schema_path: Path | None = None,
    attribute_mapping_path: Path | None = None,
    seo_query_pack_path: Path | None = None,
    output_dir: Path | None = None,
    run_id: str | None = None,
    limit: int | None = None,
    business_priority: str | None = "now",
) -> dict[str, Any]:
    started_at = datetime.now()
    run_id = run_id or f"card_content_audit_packages_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)

    backlog_path = backlog_path or data_dir / DEFAULT_BACKLOG_PATH
    content_master_path = content_master_path or data_dir / DEFAULT_CONTENT_MASTER_PATH
    ozon_content_path = ozon_content_path or data_dir / DEFAULT_OZON_CONTENT_PATH
    wb_content_path = wb_content_path or data_dir / DEFAULT_WB_CONTENT_PATH
    passport_schema_path = passport_schema_path or data_dir / DEFAULT_PASSPORT_SCHEMA_PATH
    attribute_mapping_path = attribute_mapping_path or data_dir / DEFAULT_ATTRIBUTE_MAPPING_PATH
    seo_query_pack_path = seo_query_pack_path or data_dir / DEFAULT_SEO_QUERY_PACK_PATH
    output_dir = ensure_dir(output_dir or data_dir / DEFAULT_OUTPUT_DIR / run_id)

    errors: dict[str, str] = {}
    backlog_rows: list[dict[str, str]] = []
    content_rows: list[dict[str, str]] = []
    ozon_content: dict[str, Any] = {"attributes": [], "descriptions": []}
    wb_content: list[dict[str, Any]] = []
    passport_schema: dict[str, Any] = {}
    attribute_mapping_rows: list[dict[str, str]] = []
    seo_target_rows: list[dict[str, Any]] = []
    optional_warnings: dict[str, str] = {}

    for label, path in (
        ("backlog", backlog_path),
        ("content_master", content_master_path),
        ("attribute_mapping", attribute_mapping_path),
    ):
        try:
            rows = _read_csv(path)
            if label == "backlog":
                backlog_rows = rows
            elif label == "content_master":
                content_rows = rows
            else:
                attribute_mapping_rows = rows
        except Exception as exc:  # noqa: BLE001 - task report must capture local input errors
            errors[label] = str(exc)

    try:
        raw = _read_json(ozon_content_path)
        if isinstance(raw, dict):
            ozon_content = raw
        else:
            errors["ozon_content"] = "expected object"
    except Exception as exc:  # noqa: BLE001
        errors["ozon_content"] = str(exc)

    try:
        raw = _read_json(wb_content_path)
        if isinstance(raw, list):
            wb_content = raw
        else:
            errors["wb_content"] = "expected list"
    except Exception as exc:  # noqa: BLE001
        errors["wb_content"] = str(exc)

    try:
        raw = _read_json(passport_schema_path)
        if isinstance(raw, dict):
            passport_schema = raw
        else:
            errors["passport_schema"] = "expected object"
    except Exception as exc:  # noqa: BLE001
        errors["passport_schema"] = str(exc)

    try:
        raw = _read_json(seo_query_pack_path)
        seo_target_rows = _load_seo_targets(raw)
        if not seo_target_rows:
            optional_warnings["seo_query_pack"] = "loaded but no card targets found"
    except FileNotFoundError:
        optional_warnings["seo_query_pack"] = "not found"
    except Exception as exc:  # noqa: BLE001
        optional_warnings["seo_query_pack"] = str(exc)

    packages: list[dict[str, Any]] = []
    summary: dict[str, Any] = {
        "input_backlog_rows": len(backlog_rows),
        "input_content_rows": len(content_rows),
        "selected_rows": 0,
        "packages": 0,
        "excluded_by_seo_status": 0,
        "excluded_needs_manual_review": 0,
        "excluded_non_patch_assortment": 0,
        "business_priority_filter": business_priority if business_priority is not None else "all",
        "limit": limit if limit is not None else "",
        "high_priority_packages": 0,
        "normal_priority_packages": 0,
        "low_priority_packages": 0,
        "total_media_assets": 0,
        "packages_with_ozon_snapshot": 0,
        "packages_with_wb_snapshot": 0,
        "packages_with_seo_query_pack": 0,
        "packages_seo_ready": 0,
        "packages_seo_broad_only": 0,
        "packages_seo_needs_manual_review": 0,
        "packages_seo_excluded_non_patch": 0,
        "visual_audit_completed": 0,
    }
    if not errors:
        packages, excluded_rows, summary = build_card_audit_packages(
            backlog_rows=backlog_rows,
            content_rows=content_rows,
            ozon_content=ozon_content,
            wb_content=wb_content,
            passport_schema=passport_schema,
            attribute_mapping_rows=attribute_mapping_rows,
            seo_target_rows=seo_target_rows,
            limit=limit,
            business_priority=business_priority,
        )

    package_index_rows: list[dict[str, Any]] = []
    excluded_index_rows: list[dict[str, Any]] = []
    if not errors:
        for package in packages:
            backlog = package["backlog"]
            package_dir = ensure_dir(
                output_dir
                / f"{int(package['package_rank']):04d}_{_slug(backlog.get('internal_product_id', ''), 'card')}"
            )
            package_json_path = package_dir / "audit_package.json"
            package_report_path = package_dir / "audit_report.md"
            photos_html_path = package_dir / "photos.html"
            write_json(package_json_path, package)
            _write_package_report(package_report_path, package)
            _write_photos_html(photos_html_path, package)
            ozon_photo_count = sum(1 for item in package["media_assets"] if item.get("marketplace") == "ozon")
            wb_photo_count = sum(1 for item in package["media_assets"] if item.get("marketplace") == "wb")
            package_index_rows.append(
                {
                    "package_rank": package["package_rank"],
                    "backlog_rank": backlog.get("backlog_rank", ""),
                    "audit_priority": backlog.get("audit_priority", ""),
                    "business_priority": backlog.get("business_priority", ""),
                    "internal_product_id": backlog.get("internal_product_id", ""),
                    "internal_sku": backlog.get("internal_sku", ""),
                    "product_name": backlog.get("product_name", ""),
                    "marketplace_presence": backlog.get("marketplace_presence", ""),
                    "mapping_status": backlog.get("mapping_status", ""),
                    "ozon_offer_id": backlog.get("ozon_offer_id", ""),
                    "wb_vendor_code": backlog.get("wb_vendor_code", ""),
                    "ozon_photo_count": ozon_photo_count,
                    "wb_photo_count": wb_photo_count,
                    "total_photo_count": ozon_photo_count + wb_photo_count,
                    "seo_query_pack_status": package["seo_query_pack"]["query_pack_status"],
                    "seo_manual_review_reason": package["seo_query_pack"]["manual_review_reason"],
                    "visual_audit_status": package["visual_audit_status"],
                    "passport_draft_status": package["passport_draft_status"],
                    "package_dir": str(package_dir),
                    "audit_report": str(package_report_path),
                    "audit_package_json": str(package_json_path),
                    "photos_html": str(photos_html_path),
                }
            )
        excluded_index_rows = excluded_rows

    package_index_csv_path = output_dir / "package_index.csv"
    package_index_json_path = output_dir / "package_index.json"
    excluded_index_csv_path = output_dir / "excluded_package_index.csv"
    excluded_index_json_path = output_dir / "excluded_package_index.json"
    report_path = run_dir / "card_content_audit_packages_report.md"
    summary_path = run_dir / "summary.json"
    if not errors:
        _write_csv(package_index_csv_path, package_index_rows, PACKAGE_INDEX_FIELDS)
        write_json(package_index_json_path, package_index_rows)
        _write_csv(excluded_index_csv_path, excluded_index_rows, EXCLUDED_PACKAGE_INDEX_FIELDS)
        write_json(excluded_index_json_path, excluded_index_rows)

    overall_status = "error" if errors else "warning" if packages else "ok"
    artifacts = {
        "run_dir": str(run_dir),
        "output_dir": str(output_dir),
        "package_index_csv": str(package_index_csv_path),
        "package_index_json": str(package_index_json_path),
        "excluded_package_index_csv": str(excluded_index_csv_path),
        "excluded_package_index_json": str(excluded_index_json_path),
        "report": str(report_path),
        "summary": str(summary_path),
        "run_manifest": str(run_dir / "manifest.json"),
    }
    result = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "read_only",
        "overall_status": overall_status,
        "summary": summary,
        "errors": errors,
        "optional_warnings": optional_warnings,
        "package_sample": package_index_rows[:50],
        "package_sample_truncated": max(0, len(package_index_rows) - 50),
        "excluded_sample": excluded_index_rows[:50],
        "excluded_sample_truncated": max(0, len(excluded_index_rows) - 50),
        "inputs": {
            "backlog_path": str(backlog_path),
            "content_master_path": str(content_master_path),
            "ozon_content_path": str(ozon_content_path),
            "wb_content_path": str(wb_content_path),
            "passport_schema_path": str(passport_schema_path),
            "attribute_mapping_path": str(attribute_mapping_path),
            "seo_query_pack_path": str(seo_query_pack_path),
            "business_priority": business_priority if business_priority is not None else "all",
            "limit": limit if limit is not None else "",
        },
        "artifacts": artifacts,
    }

    write_json(summary_path, result)
    _write_run_report(report_path, run_id=run_id, started_at=started_at, result=result)
    write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=result,
        task="card-content-audit-packages",
        mode="read_only",
        risk="low",
        marketplaces=["ozon", "wb"],
        inputs=result["inputs"],
    )
    return result
