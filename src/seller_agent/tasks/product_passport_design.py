from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any

from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.reports.writer import ensure_dir, write_json


DEFAULT_PARAMETER_INVENTORY_DIR = Path("catalog/content/parameter_inventory")
DEFAULT_OUTPUT_DIR = Path("catalog/content/product_passport")


PASSPORT_FIELD_FIELDS = [
    "section",
    "field",
    "type",
    "required",
    "description",
    "source_priority",
    "owner_rule",
]

ATTRIBUTE_MAPPING_FIELDS = [
    "section",
    "internal_field",
    "value_type",
    "required_in_passport",
    "ozon_target",
    "ozon_attribute_id",
    "ozon_attribute_name",
    "ozon_value_strategy",
    "wb_target",
    "wb_subject_id",
    "wb_characteristic_id",
    "wb_characteristic_name",
    "wb_value_strategy",
    "notes",
]


PASSPORT_FIELDS: list[dict[str, str]] = [
    {
        "section": "identity",
        "field": "internal_product_id",
        "type": "string",
        "required": "true",
        "description": "Stable internal product key in the project.",
        "source_priority": "unified catalog -> mapping",
        "owner_rule": "Do not use marketplace seller SKU as the only identity.",
    },
    {
        "section": "identity",
        "field": "internal_sku",
        "type": "string",
        "required": "true",
        "description": "Owner-approved internal SKU for the product.",
        "source_priority": "seller_sku_rules -> owner review -> unified catalog",
        "owner_rule": "Seller SKU changes on marketplaces are a separate dangerous operation.",
    },
    {
        "section": "identity",
        "field": "marketplace_presence",
        "type": "enum",
        "required": "true",
        "description": "Product presence: ozon_wb, ozon_only, wb_only, inactive_or_missing.",
        "source_priority": "unified catalog",
        "owner_rule": "Marketplace-local scenarios must work even without full Ozon/WB mapping.",
    },
    {
        "section": "core",
        "field": "product_type",
        "type": "enum",
        "required": "true",
        "description": "chevron, patch, loop, kit or other confirmed product type.",
        "source_priority": "internal_sku -> title -> owner review -> photo audit",
        "owner_rule": "Current target assortment is chevrons, patches, loops and kits made from them.",
    },
    {
        "section": "core",
        "field": "wear_position",
        "type": "enum",
        "required": "false",
        "description": "nr, ng, kp, back or empty for kits where position should not be fixed.",
        "source_priority": "internal_sku -> title -> photo audit -> owner review",
        "owner_rule": "For kits with 3+ items do not force one wear position.",
    },
    {
        "section": "core",
        "field": "theme_group",
        "type": "string",
        "required": "true",
        "description": "Internal theme/group: svo, bpla, form, mvd, fsb, fsin, rg, voisk, etc.",
        "source_priority": "internal_sku -> owner mapping decisions -> title",
        "owner_rule": "Use owner-approved topic dictionaries from seller_sku_rules and followups.",
    },
    {
        "section": "core",
        "field": "content_kind",
        "type": "enum",
        "required": "false",
        "description": "text, pict or empty when not applicable.",
        "source_priority": "internal_sku -> photo audit -> owner review",
        "owner_rule": "Many force-structure sleeve chevrons and force kits are pict, not text.",
    },
    {
        "section": "core",
        "field": "pack_qty",
        "type": "integer",
        "required": "true",
        "description": "Number of physical items in one marketplace product unit.",
        "source_priority": "internal_sku kitN -> title -> characteristics -> owner review",
        "owner_rule": "Cost, weight and package depth depend on pack_qty.",
    },
    {
        "section": "content",
        "field": "canonical_title",
        "type": "string",
        "required": "true",
        "description": "Unified owner-approved product title before marketplace adaptation.",
        "source_priority": "card audit recommendation -> owner approval",
        "owner_rule": "Ozon and WB titles may differ only because of marketplace limits.",
    },
    {
        "section": "content",
        "field": "canonical_description",
        "type": "string",
        "required": "true",
        "description": "Unified product description blocks before marketplace adaptation.",
        "source_priority": "photo audit -> current descriptions -> owner rules",
        "owner_rule": "Description should cover image meaning, characteristics/use, and short store block.",
    },
    {
        "section": "content",
        "field": "image_subject",
        "type": "string",
        "required": "true",
        "description": "What is shown on the chevron/patch/loops.",
        "source_priority": "photo audit -> title -> owner review",
        "owner_rule": "Do not invent image meaning when it is not confirmed.",
    },
    {
        "section": "physical",
        "field": "product_width_mm",
        "type": "integer",
        "required": "true",
        "description": "Product width in millimeters.",
        "source_priority": "photo/dimensions -> current attributes -> owner size rules",
        "owner_rule": "Product size is written as width*height.",
    },
    {
        "section": "physical",
        "field": "product_height_mm",
        "type": "integer",
        "required": "true",
        "description": "Product height in millimeters.",
        "source_priority": "photo/dimensions -> current attributes -> owner size rules",
        "owner_rule": "Product size is written as width*height.",
    },
    {
        "section": "physical",
        "field": "product_depth_mm",
        "type": "integer",
        "required": "false",
        "description": "Product thickness/depth in millimeters, default 6 for flat items when confirmed.",
        "source_priority": "owner package rules -> current dimensions -> audit",
        "owner_rule": "Flat item package thickness is 6 mm; kit package thickness is pack_qty * 6 mm.",
    },
    {
        "section": "physical",
        "field": "package_width_mm",
        "type": "integer",
        "required": "true",
        "description": "Package width in millimeters.",
        "source_priority": "owner package rules -> current dimensions -> audit",
        "owner_rule": "Package size is written as width*height*thickness.",
    },
    {
        "section": "physical",
        "field": "package_height_mm",
        "type": "integer",
        "required": "true",
        "description": "Package height in millimeters.",
        "source_priority": "owner package rules -> current dimensions -> audit",
        "owner_rule": "Package size is written as width*height*thickness.",
    },
    {
        "section": "physical",
        "field": "package_depth_mm",
        "type": "integer",
        "required": "true",
        "description": "Package thickness/depth in millimeters.",
        "source_priority": "owner package rules -> current dimensions -> audit",
        "owner_rule": "For kits: package_depth_mm = pack_qty * 6 mm.",
    },
    {
        "section": "physical",
        "field": "item_weight_g",
        "type": "number",
        "required": "true",
        "description": "Weight of one physical item in grams.",
        "source_priority": "owner weight rules -> audit",
        "owner_rule": "Regular chevron/patch/loops item is 10 g; back chevron is 30 g.",
    },
    {
        "section": "physical",
        "field": "package_weight_g",
        "type": "number",
        "required": "true",
        "description": "Total package weight in grams.",
        "source_priority": "item_weight_g * pack_qty -> current marketplace value -> audit",
        "owner_rule": "For kits sum physical item weights.",
    },
    {
        "section": "materials",
        "field": "material",
        "type": "string",
        "required": "true",
        "description": "Main material shown as marketplace material when supported.",
        "source_priority": "owner material rule -> current attributes -> audit",
        "owner_rule": "Material is Gabardine when confirmed for current assortment.",
    },
    {
        "section": "materials",
        "field": "composition",
        "type": "array[string]",
        "required": "true",
        "description": "Material composition for marketplace composition fields.",
        "source_priority": "owner composition rule -> current attributes -> audit",
        "owner_rule": "Composition: polyester and nylon when confirmed for item type.",
    },
    {
        "section": "materials",
        "field": "attachment_type",
        "type": "enum",
        "required": "true",
        "description": "velcro_hook_loop, sew_on or other confirmed attachment.",
        "source_priority": "product_type -> owner attachment rules -> photo audit",
        "owner_rule": "Chevrons and loops have velcro; patches are sew-on without velcro.",
    },
    {
        "section": "classification",
        "field": "tnved",
        "type": "string",
        "required": "true",
        "description": "TN VED code.",
        "source_priority": "owner rule -> current attributes -> compliance review",
        "owner_rule": "Use 5810999000 for chevrons, patches, loops and kits unless exception is confirmed.",
    },
    {
        "section": "classification",
        "field": "country_of_origin",
        "type": "string",
        "required": "true",
        "description": "Country of production/origin.",
        "source_priority": "current attributes -> owner confirmation",
        "owner_rule": "Do not invent if current card/source does not confirm it.",
    },
    {
        "section": "pricing",
        "field": "cost_total",
        "type": "number",
        "required": "true",
        "description": "Total cost of one marketplace product unit.",
        "source_priority": "pricing runbook -> pack_qty * current cost per item",
        "owner_rule": "Current base production cost: 85 rub per physical chevron-like item.",
    },
    {
        "section": "seo",
        "field": "search_queries",
        "type": "array[string]",
        "required": "false",
        "description": "Relevant search queries where the product is visible or should be visible.",
        "source_priority": "parser visibility -> LK query exports -> card audit",
        "owner_rule": "Keep query visibility in reports; do not add irrelevant keys.",
    },
    {
        "section": "seo",
        "field": "ozon_hashtags",
        "type": "array[string]",
        "required": "false",
        "description": "Ozon hashtags selected for this product.",
        "source_priority": "current Ozon hashtags -> parser queries -> hashtag frequency table",
        "owner_rule": "Up to 30; do not use BPLA in Ozon hashtags.",
    },
    {
        "section": "media",
        "field": "media_assets",
        "type": "array[object]",
        "required": "true",
        "description": "Ordered current/target photos with source, role, status and audit notes.",
        "source_priority": "card snapshots -> visual audit -> designer backlog",
        "owner_rule": "Inspect all photos and send a collage in card reports.",
    },
    {
        "section": "grouping",
        "field": "target_group_key",
        "type": "string",
        "required": "false",
        "description": "Stable key for target Ozon/WB grouping review.",
        "source_priority": "card grouping audit -> owner approval",
        "owner_rule": "Grouping is a separate dangerous operation.",
    },
]


ATTRIBUTE_MAPPINGS: list[dict[str, str]] = [
    {
        "section": "identity",
        "internal_field": "brand",
        "value_type": "string",
        "required_in_passport": "true",
        "ozon_target": "attribute",
        "ozon_attribute_id": "85",
        "ozon_attribute_name": "Бренд",
        "ozon_value_strategy": "Use marketplace/current brand or owner-approved no-brand/brand value.",
        "wb_target": "characteristic",
        "wb_subject_id": "2367",
        "wb_characteristic_id": "14177446",
        "wb_characteristic_name": "Бренд",
        "wb_value_strategy": "Use marketplace/current brand or owner-approved no-brand/brand value.",
        "notes": "Brand value must be verified before apply.",
    },
    {
        "section": "core",
        "internal_field": "product_type",
        "value_type": "enum",
        "required_in_passport": "true",
        "ozon_target": "attribute",
        "ozon_attribute_id": "8229",
        "ozon_attribute_name": "Тип",
        "ozon_value_strategy": "Map internal type to Ozon category type value.",
        "wb_target": "characteristic",
        "wb_subject_id": "2367",
        "wb_characteristic_id": "384944",
        "wb_characteristic_name": "Вид декора для одежды",
        "wb_value_strategy": "Map chevron/patch/loop to WB decor type values.",
        "notes": "Use owner product type, not only marketplace title.",
    },
    {
        "section": "content",
        "internal_field": "canonical_title",
        "value_type": "string",
        "required_in_passport": "true",
        "ozon_target": "attribute",
        "ozon_attribute_id": "4180",
        "ozon_attribute_name": "Название",
        "ozon_value_strategy": "Marketplace-adapted title, no keyword stuffing, no caps lock.",
        "wb_target": "field_or_characteristic",
        "wb_subject_id": "2367",
        "wb_characteristic_id": "15000000",
        "wb_characteristic_name": "Наименование",
        "wb_value_strategy": "Marketplace-adapted title up to 60 characters.",
        "notes": "Unified title is source; marketplace titles may differ by limits.",
    },
    {
        "section": "content",
        "internal_field": "canonical_description",
        "value_type": "string",
        "required_in_passport": "true",
        "ozon_target": "attribute",
        "ozon_attribute_id": "4191",
        "ozon_attribute_name": "Аннотация",
        "ozon_value_strategy": "Use three-block description adapted to Ozon.",
        "wb_target": "field_or_characteristic",
        "wb_subject_id": "2367",
        "wb_characteristic_id": "14177452",
        "wb_characteristic_name": "Описание",
        "wb_value_strategy": "Plain, truthful, concise description without forbidden symbols/irrelevant keys.",
        "notes": "Description does not replace characteristics.",
    },
    {
        "section": "physical",
        "internal_field": "product_size_mm",
        "value_type": "object",
        "required_in_passport": "true",
        "ozon_target": "attribute",
        "ozon_attribute_id": "4382",
        "ozon_attribute_name": "Размеры, мм",
        "ozon_value_strategy": "Build from product_width_mm/product_height_mm/product_depth_mm; confirm Ozon D/W/H order before apply.",
        "wb_target": "none",
        "wb_subject_id": "",
        "wb_characteristic_id": "",
        "wb_characteristic_name": "",
        "wb_value_strategy": "WB uses package dimensions, not product size characteristic for current flow.",
        "notes": "Owner-facing size remains width*height.",
    },
    {
        "section": "physical",
        "internal_field": "package_dimensions",
        "value_type": "object",
        "required_in_passport": "true",
        "ozon_target": "top_level_dimensions",
        "ozon_attribute_id": "",
        "ozon_attribute_name": "",
        "ozon_value_strategy": "Use package_width_mm/package_height_mm/package_depth_mm in Ozon payload dimensions.",
        "wb_target": "dimensions",
        "wb_subject_id": "2367",
        "wb_characteristic_id": "",
        "wb_characteristic_name": "",
        "wb_value_strategy": "Convert millimeters to centimeters for WB dimensions length/width/height.",
        "notes": "Do not duplicate WB package dimensions as characteristics in apply payload.",
    },
    {
        "section": "physical",
        "internal_field": "package_weight_g",
        "value_type": "number",
        "required_in_passport": "true",
        "ozon_target": "attribute",
        "ozon_attribute_id": "4497",
        "ozon_attribute_name": "Вес с упаковкой, г",
        "ozon_value_strategy": "Use total package weight in grams.",
        "wb_target": "dimensions",
        "wb_subject_id": "2367",
        "wb_characteristic_id": "",
        "wb_characteristic_name": "",
        "wb_value_strategy": "Convert grams to kilograms for WB dimensions.weightBrutto.",
        "notes": "WB rejects weight in characteristics in known flow.",
    },
    {
        "section": "materials",
        "internal_field": "material",
        "value_type": "string",
        "required_in_passport": "true",
        "ozon_target": "attribute",
        "ozon_attribute_id": "7405",
        "ozon_attribute_name": "Материал",
        "ozon_value_strategy": "Use confirmed material, usually Габардин for current assortment.",
        "wb_target": "none",
        "wb_subject_id": "2367",
        "wb_characteristic_id": "",
        "wb_characteristic_name": "",
        "wb_value_strategy": "WB current key field is composition; material may stay in description if no field.",
        "notes": "Do not invent material when product/photo contradicts default.",
    },
    {
        "section": "materials",
        "internal_field": "composition",
        "value_type": "array[string]",
        "required_in_passport": "true",
        "ozon_target": "description_if_no_attribute",
        "ozon_attribute_id": "",
        "ozon_attribute_name": "",
        "ozon_value_strategy": "Add composition to annotation unless Ozon category gets a confirmed composition attribute.",
        "wb_target": "characteristic",
        "wb_subject_id": "2367",
        "wb_characteristic_id": "14177450",
        "wb_characteristic_name": "Состав",
        "wb_value_strategy": "Use confirmed composition values, usually полиэстер and нейлон.",
        "notes": "Ozon current category does not confirm separate composition attribute.",
    },
    {
        "section": "color",
        "internal_field": "color_values",
        "value_type": "array[string]",
        "required_in_passport": "true",
        "ozon_target": "attribute",
        "ozon_attribute_id": "10096",
        "ozon_attribute_name": "Цвет товара",
        "ozon_value_strategy": "All visible colors ordered by fill volume; first usually background.",
        "wb_target": "characteristic",
        "wb_subject_id": "2367",
        "wb_characteristic_id": "14177449",
        "wb_characteristic_name": "Цвет",
        "wb_value_strategy": "Up to 5 WB color values matching the concrete product.",
        "notes": "Must be checked visually.",
    },
    {
        "section": "color",
        "internal_field": "variant_color_name",
        "value_type": "string",
        "required_in_passport": "false",
        "ozon_target": "attribute",
        "ozon_attribute_id": "10097",
        "ozon_attribute_name": "Название цвета",
        "ozon_value_strategy": "Short variant name shown inside Ozon group.",
        "wb_target": "grouping_variant",
        "wb_subject_id": "2367",
        "wb_characteristic_id": "",
        "wb_characteristic_name": "",
        "wb_value_strategy": "Use as grouping/variant label only if WB supports current changing characteristic.",
        "notes": "Owner usually uses short chevron name here.",
    },
    {
        "section": "classification",
        "internal_field": "tnved",
        "value_type": "string",
        "required_in_passport": "true",
        "ozon_target": "attribute",
        "ozon_attribute_id": "22232",
        "ozon_attribute_name": "ТН ВЭД коды ЕАЭС",
        "ozon_value_strategy": "Use confirmed TN VED value.",
        "wb_target": "characteristic",
        "wb_subject_id": "2367",
        "wb_characteristic_id": "15000001",
        "wb_characteristic_name": "ТНВЭД",
        "wb_value_strategy": "Use confirmed TN VED value.",
        "notes": "Owner rule currently: 5810999000 for chevrons/patches/loops/kits.",
    },
    {
        "section": "classification",
        "internal_field": "country_of_origin",
        "value_type": "string",
        "required_in_passport": "true",
        "ozon_target": "none",
        "ozon_attribute_id": "",
        "ozon_attribute_name": "",
        "ozon_value_strategy": "No confirmed Ozon field in current schema; keep in passport unless field appears.",
        "wb_target": "characteristic",
        "wb_subject_id": "2367",
        "wb_characteristic_id": "14177451",
        "wb_characteristic_name": "Страна производства",
        "wb_value_strategy": "Use confirmed country from current card/owner.",
        "notes": "Do not invent country.",
    },
    {
        "section": "kit",
        "internal_field": "pack_qty",
        "value_type": "integer",
        "required_in_passport": "true",
        "ozon_target": "title_or_description",
        "ozon_attribute_id": "",
        "ozon_attribute_name": "",
        "ozon_value_strategy": "Reflect pack quantity in title/description where relevant.",
        "wb_target": "characteristic",
        "wb_subject_id": "2367",
        "wb_characteristic_id": "179792",
        "wb_characteristic_name": "Количество предметов в упаковке",
        "wb_value_strategy": "Use concrete count of physical items in kit.",
        "notes": "Petlitcy one product unit is two uncut loops, but pack_qty business meaning must be explicit.",
    },
    {
        "section": "kit",
        "internal_field": "kit_contents",
        "value_type": "string",
        "required_in_passport": "true",
        "ozon_target": "description",
        "ozon_attribute_id": "",
        "ozon_attribute_name": "",
        "ozon_value_strategy": "Describe concrete kit composition in annotation.",
        "wb_target": "characteristic",
        "wb_subject_id": "2367",
        "wb_characteristic_id": "378533",
        "wb_characteristic_name": "Комплектация",
        "wb_value_strategy": "Use concrete kit/velcro/sew-on contents.",
        "notes": "For kits, first photo must show full composition on WB.",
    },
    {
        "section": "seo",
        "internal_field": "ozon_hashtags",
        "value_type": "array[string]",
        "required_in_passport": "false",
        "ozon_target": "attribute",
        "ozon_attribute_id": "23171",
        "ozon_attribute_name": "#Хештеги",
        "ozon_value_strategy": "Up to 30 relevant hashtags; prefer visible relevant queries and frequency table.",
        "wb_target": "none",
        "wb_subject_id": "",
        "wb_characteristic_id": "",
        "wb_characteristic_name": "",
        "wb_value_strategy": "No equivalent confirmed WB hashtag field for current subject.",
        "notes": "Do not use BPLA in Ozon hashtags.",
    },
    {
        "section": "grouping",
        "internal_field": "ozon_model_group_key",
        "value_type": "string",
        "required_in_passport": "false",
        "ozon_target": "attribute",
        "ozon_attribute_id": "9048",
        "ozon_attribute_name": "Название модели (для объединения в одну карточку)",
        "ozon_value_strategy": "Same value for variants intended to be one Ozon model group.",
        "wb_target": "imtID_or_moveNm",
        "wb_subject_id": "",
        "wb_characteristic_id": "",
        "wb_characteristic_name": "",
        "wb_value_strategy": "WB grouping is handled by imtID/moveNm/upload-add after separate review.",
        "notes": "Grouping is a separate dangerous operation.",
    },
    {
        "section": "grouping",
        "internal_field": "ozon_similar_group_key",
        "value_type": "string",
        "required_in_passport": "false",
        "ozon_target": "attribute",
        "ozon_attribute_id": "22390",
        "ozon_attribute_name": "Объединить в похожие товары",
        "ozon_value_strategy": "Same value for Ozon similar-products group when approved.",
        "wb_target": "none",
        "wb_subject_id": "",
        "wb_characteristic_id": "",
        "wb_characteristic_name": "",
        "wb_value_strategy": "No direct WB equivalent.",
        "notes": "Do not mix with model grouping without review.",
    },
]


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [{key: (value or "").strip() for key, value in row.items()} for row in csv.DictReader(handle)]


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def _schema_property(field: dict[str, str]) -> dict[str, Any]:
    raw_type = field["type"]
    if raw_type == "integer":
        schema_type: dict[str, Any] = {"type": "integer"}
    elif raw_type == "number":
        schema_type = {"type": "number"}
    elif raw_type.startswith("array"):
        schema_type = {"type": "array", "items": {"type": "string"}}
    elif raw_type == "object":
        schema_type = {"type": "object"}
    elif raw_type == "enum":
        schema_type = {"type": "string"}
    else:
        schema_type = {"type": "string"}
    schema_type["description"] = field["description"]
    schema_type["x-section"] = field["section"]
    schema_type["x-source-priority"] = field["source_priority"]
    schema_type["x-owner-rule"] = field["owner_rule"]
    return schema_type


def build_product_passport_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://seller-vital-shevron.local/schemas/master_product_passport.schema.json",
        "title": "MasterProductPassport",
        "type": "object",
        "additionalProperties": True,
        "required": [field["field"] for field in PASSPORT_FIELDS if field["required"] == "true"],
        "properties": {field["field"]: _schema_property(field) for field in PASSPORT_FIELDS},
    }


def _validate_mapping(
    *,
    mapping_rows: list[dict[str, str]],
    ozon_schema_rows: list[dict[str, str]],
    wb_schema_rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    ozon_ids = {row.get("attribute_id", "") for row in ozon_schema_rows if row.get("attribute_id")}
    wb_ids_by_subject = {
        (row.get("subject_id", ""), row.get("characteristic_id", ""))
        for row in wb_schema_rows
        if row.get("subject_id") and row.get("characteristic_id")
    }
    issues: list[dict[str, str]] = []
    for row in mapping_rows:
        ozon_attr_id = row.get("ozon_attribute_id", "")
        if ozon_attr_id and ozon_schema_rows and ozon_attr_id not in ozon_ids:
            issues.append(
                {
                    "marketplace": "ozon",
                    "internal_field": row["internal_field"],
                    "target_id": ozon_attr_id,
                    "issue": "attribute_id_not_found_in_schema",
                }
            )
        wb_subject_id = row.get("wb_subject_id", "")
        wb_char_id = row.get("wb_characteristic_id", "")
        if wb_subject_id and wb_char_id and wb_schema_rows and (wb_subject_id, wb_char_id) not in wb_ids_by_subject:
            issues.append(
                {
                    "marketplace": "wb",
                    "internal_field": row["internal_field"],
                    "target_id": f"{wb_subject_id}:{wb_char_id}",
                    "issue": "characteristic_id_not_found_in_schema",
                }
            )
    return issues


def _write_report(path: Path, *, run_id: str, started_at: datetime, result: dict[str, Any]) -> None:
    lines = [
        "# Master Product Passport Design",
        "",
        f"Run ID: `{run_id}`",
        f"Started at: `{started_at.isoformat(timespec='seconds')}`",
        f"Status: `{result['overall_status']}`",
        "",
        "## Итог",
        "",
        "- Это read-only проектирование структуры полноценного master product passport.",
        "- Задача не меняет карточки, фото, цены, остатки, группировки или seller SKU.",
        "- Результат нужен как контракт данных для будущего массового карточного аудита и dry-run загрузок.",
        "",
        "## Summary",
        "",
    ]
    for key in sorted(result["summary"]):
        lines.append(f"- `{key}`: {result['summary'][key]}")

    lines.extend(["", "## Passport Sections", ""])
    sections = sorted({row["section"] for row in PASSPORT_FIELDS})
    for section in sections:
        count = sum(1 for row in PASSPORT_FIELDS if row["section"] == section)
        lines.append(f"- `{section}`: `{count}` fields")

    lines.extend(["", "## Mapping Coverage", ""])
    lines.append("- Ozon attributes are stored separately from WB characteristics.")
    lines.append("- Package dimensions and WB `weightBrutto` are marketplace-specific targets, not generic characteristics.")
    lines.append("- Grouping fields are part of the passport, but applying grouping remains a separate dangerous operation.")

    lines.extend(["", "## Validation Issues", ""])
    if result["validation_issues"]:
        for issue in result["validation_issues"][:50]:
            lines.append(
                f"- `{issue['marketplace']}` `{issue['internal_field']}` "
                f"`{issue['target_id']}`: {issue['issue']}"
            )
        if len(result["validation_issues"]) > 50:
            lines.append(f"- truncated: {len(result['validation_issues']) - 50} more issues")
    else:
        lines.append("- none")

    lines.extend(["", "## Artifacts", ""])
    for key, value in sorted(result["artifacts"].items()):
        lines.append(f"- `{key}`: `{value}`")

    lines.extend(
        [
            "",
            "## Next Step",
            "",
            "1. Use this schema as the write target for saved card-audit packages.",
            "2. Build a read-only generator that creates one product passport draft per backlog item.",
            "3. Only after owner review prepare marketplace-specific dry-run payloads.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run_product_passport_design(
    *,
    data_dir: Path = Path("data"),
    parameter_inventory_dir: Path | None = None,
    output_dir: Path | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    started_at = datetime.now()
    run_id = run_id or f"product_passport_design_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    parameter_inventory_dir = parameter_inventory_dir or data_dir / DEFAULT_PARAMETER_INVENTORY_DIR
    output_dir = ensure_dir(output_dir or data_dir / DEFAULT_OUTPUT_DIR)

    errors: dict[str, str] = {}
    ozon_schema_rows: list[dict[str, str]] = []
    wb_schema_rows: list[dict[str, str]] = []
    try:
        ozon_schema_rows = _read_csv(parameter_inventory_dir / "ozon_schema_attributes.csv")
    except Exception as exc:  # noqa: BLE001
        errors["ozon_schema_attributes"] = str(exc)
    try:
        wb_schema_rows = _read_csv(parameter_inventory_dir / "wb_schema_characteristics.csv")
    except Exception as exc:  # noqa: BLE001
        errors["wb_schema_characteristics"] = str(exc)

    schema = build_product_passport_schema()
    validation_issues = _validate_mapping(
        mapping_rows=ATTRIBUTE_MAPPINGS,
        ozon_schema_rows=ozon_schema_rows,
        wb_schema_rows=wb_schema_rows,
    )

    passport_fields_path = output_dir / "passport_fields.csv"
    mapping_path = output_dir / "passport_attribute_mapping.csv"
    schema_path = output_dir / "master_product_passport.schema.json"
    report_path = run_dir / "master_product_passport_design_report.md"
    summary_path = run_dir / "summary.json"

    _write_csv(passport_fields_path, PASSPORT_FIELDS, PASSPORT_FIELD_FIELDS)
    _write_csv(mapping_path, ATTRIBUTE_MAPPINGS, ATTRIBUTE_MAPPING_FIELDS)
    write_json(schema_path, schema)

    overall_status = "warning" if errors or validation_issues else "ok"
    summary = {
        "passport_fields": len(PASSPORT_FIELDS),
        "required_passport_fields": sum(1 for row in PASSPORT_FIELDS if row["required"] == "true"),
        "attribute_mapping_rows": len(ATTRIBUTE_MAPPINGS),
        "ozon_mapped_attributes": sum(1 for row in ATTRIBUTE_MAPPINGS if row.get("ozon_attribute_id")),
        "wb_mapped_characteristics": sum(1 for row in ATTRIBUTE_MAPPINGS if row.get("wb_characteristic_id")),
        "ozon_schema_rows": len(ozon_schema_rows),
        "wb_schema_rows": len(wb_schema_rows),
        "validation_issues": len(validation_issues),
    }
    artifacts = {
        "run_dir": str(run_dir),
        "report": str(report_path),
        "summary": str(summary_path),
        "passport_fields_csv": str(passport_fields_path),
        "attribute_mapping_csv": str(mapping_path),
        "json_schema": str(schema_path),
        "run_manifest": str(run_dir / "manifest.json"),
    }
    result = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "read_only",
        "overall_status": overall_status,
        "summary": summary,
        "errors": errors,
        "validation_issues": validation_issues,
        "inputs": {
            "parameter_inventory_dir": str(parameter_inventory_dir),
        },
        "artifacts": artifacts,
    }

    write_json(summary_path, result)
    _write_report(report_path, run_id=run_id, started_at=started_at, result=result)
    write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=result,
        task="product-passport-design",
        mode="read_only",
        risk="low",
        marketplaces=["ozon", "wb"],
        inputs=result["inputs"],
    )
    return result
