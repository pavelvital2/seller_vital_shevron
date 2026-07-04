from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, fields
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from seller_agent.catalog.loader import normalize_sku
from seller_agent.config import AppCredentials
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.marketplaces.ozon.adapter import OzonSellerAdapter
from seller_agent.marketplaces.wb.adapter import WbContentAdapter
from seller_agent.reports.writer import ensure_dir, write_json


DEFAULT_UNIFIED_PRODUCTS_PATH = Path("catalog/unified/products.csv")
DEFAULT_OUTPUT_DIR = Path("catalog/content")
OZON_HASHTAGS_ATTR_ID = 23171


@dataclass
class CardContentIndexRow:
    marketplace: str
    internal_product_id: str
    internal_sku: str = ""
    mapping_status: str = ""
    native_id: str = ""
    secondary_id: str = ""
    title: str = ""
    description_present: str = "false"
    description_length: str = "0"
    photo_count: str = "0"
    primary_image_present: str = "false"
    attribute_count: str = "0"
    hashtags_or_tags: str = ""
    subject_or_category: str = ""
    dimensions: str = ""
    raw_snapshot_status: str = ""
    notes: str = ""


CARD_CONTENT_INDEX_FIELDS = [field.name for field in fields(CardContentIndexRow)]


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [
            {key: (value or "").strip() for key, value in row.items()}
            for row in csv.DictReader(handle)
        ]


def _write_dict_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def _first_text(*values: Any) -> str:
    for value in values:
        text = normalize_sku(value)
        if text:
            return text
    return ""


def _attr_values(item: dict[str, Any], attr_id: int) -> list[str]:
    values: list[str] = []
    for attr in item.get("attributes") or []:
        if attr.get("id") != attr_id:
            continue
        for raw in attr.get("values") or []:
            value = raw.get("value") if isinstance(raw, dict) else raw
            text = normalize_sku(value)
            if text:
                values.append(text)
    return values


def _attr_value(item: dict[str, Any], attr_id: int) -> str:
    values = _attr_values(item, attr_id)
    return values[0] if values else ""


def _unique_images(*groups: Any) -> list[str]:
    result: list[str] = []
    for group in groups:
        values = group if isinstance(group, list) else [group]
        for value in values:
            if isinstance(value, dict):
                candidate = _first_text(
                    value.get("big"),
                    value.get("c246x328"),
                    value.get("tm"),
                    value.get("url"),
                    value.get("photo"),
                )
            else:
                candidate = normalize_sku(value)
            if candidate and candidate not in result:
                result.append(candidate)
    return result


def _description_text(*items: dict[str, Any] | None) -> str:
    for item in items:
        if not item:
            continue
        text = _first_text(
            item.get("description"),
            item.get("annotation"),
            item.get("rich_content"),
            item.get("name"),
        )
        if text:
            return text
    return ""


def _ozon_dimensions(item: dict[str, Any]) -> str:
    parts = []
    for key in ("width", "height", "depth", "weight"):
        value = normalize_sku(item.get(key))
        if value:
            parts.append(f"{key}={value}")
    return ";".join(parts)


def _wb_dimensions(card: dict[str, Any]) -> str:
    dimensions = card.get("dimensions")
    if not isinstance(dimensions, dict):
        return ""
    parts = []
    for key in ("length", "width", "height", "weightBrutto"):
        value = normalize_sku(dimensions.get(key))
        if value:
            parts.append(f"{key}={value}")
    return ";".join(parts)


def _index_by_offer(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = normalize_sku(row.get("offer_id"))
        if key and key not in result:
            result[key] = row
    return result


def _index_by_vendor(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = normalize_sku(row.get("vendorCode"))
        if key and key not in result:
            result[key] = row
    return result


def _merge_rows_by_key(
    existing: list[dict[str, Any]],
    updates: list[dict[str, Any]],
    *,
    keys: tuple[str, ...],
) -> list[dict[str, Any]]:
    merged: dict[tuple[str, ...], dict[str, Any]] = {}
    order: list[tuple[str, ...]] = []
    for row in existing + updates:
        key = tuple(normalize_sku(row.get(field)) for field in keys)
        if not all(key):
            continue
        if key not in merged:
            order.append(key)
        merged[key] = row
    return [merged[key] for key in order]


def _merge_ozon_content(
    *,
    existing_path: Path,
    new_attributes: list[dict[str, Any]],
    new_descriptions: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    existing: dict[str, Any] = {}
    if existing_path.exists():
        existing = json.loads(existing_path.read_text(encoding="utf-8"))
    return {
        "attributes": _merge_rows_by_key(
            existing.get("attributes") or [],
            new_attributes,
            keys=("offer_id",),
        ),
        "descriptions": _merge_rows_by_key(
            existing.get("descriptions") or [],
            new_descriptions,
            keys=("offer_id",),
        ),
    }


def _merge_wb_content(*, existing_path: Path, new_cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    existing: list[dict[str, Any]] = []
    if existing_path.exists():
        raw = json.loads(existing_path.read_text(encoding="utf-8"))
        existing = raw if isinstance(raw, list) else []
    return _merge_rows_by_key(existing, new_cards, keys=("vendorCode",))


def normalize_ozon_card_content(
    *,
    product: dict[str, str],
    attributes: dict[str, Any] | None,
    description: dict[str, Any] | None,
) -> CardContentIndexRow:
    attributes = attributes or {}
    description = description or {}
    images = _unique_images(attributes.get("primary_image"), attributes.get("images"))
    text = _description_text(description, attributes)
    title = _first_text(description.get("name"), _attr_value(attributes, 4180), product.get("product_name"))
    offer_id = normalize_sku(product.get("ozon_offer_id"))
    raw_status = "found" if attributes or description else "missing"

    return CardContentIndexRow(
        marketplace="ozon",
        internal_product_id=normalize_sku(product.get("internal_product_id")),
        internal_sku=normalize_sku(product.get("internal_sku")),
        mapping_status=normalize_sku(product.get("mapping_status")),
        native_id=offer_id,
        secondary_id=_first_text(product.get("ozon_product_id"), attributes.get("id"), attributes.get("product_id")),
        title=title,
        description_present=str(bool(text)).lower(),
        description_length=str(len(text)),
        photo_count=str(len(images)),
        primary_image_present=str(bool(attributes.get("primary_image"))).lower(),
        attribute_count=str(len(attributes.get("attributes") or [])),
        hashtags_or_tags=" ".join(_attr_values(attributes, OZON_HASHTAGS_ATTR_ID)),
        subject_or_category=";".join(
            value
            for value in (
                normalize_sku(attributes.get("description_category_id")),
                normalize_sku(attributes.get("type_id")),
            )
            if value
        ),
        dimensions=_ozon_dimensions(attributes),
        raw_snapshot_status=raw_status,
    )


def normalize_wb_card_content(
    *,
    product: dict[str, str],
    card: dict[str, Any] | None,
) -> CardContentIndexRow:
    card = card or {}
    images = _unique_images(card.get("photos"), card.get("mediaFiles"))
    description = normalize_sku(card.get("description"))
    vendor_code = normalize_sku(product.get("wb_vendor_code"))
    raw_status = "found" if card else "missing"

    return CardContentIndexRow(
        marketplace="wb",
        internal_product_id=normalize_sku(product.get("internal_product_id")),
        internal_sku=normalize_sku(product.get("internal_sku")),
        mapping_status=normalize_sku(product.get("mapping_status")),
        native_id=vendor_code,
        secondary_id=_first_text(product.get("wb_nm_id"), card.get("nmID"), card.get("nmId")),
        title=_first_text(card.get("title"), product.get("product_name")),
        description_present=str(bool(description)).lower(),
        description_length=str(len(description)),
        photo_count=str(len(images)),
        primary_image_present=str(bool(images)).lower(),
        attribute_count=str(len(card.get("characteristics") or [])),
        hashtags_or_tags=";".join(str(value) for value in card.get("tags") or []),
        subject_or_category=_first_text(card.get("subjectName"), card.get("subjectID")),
        dimensions=_wb_dimensions(card),
        raw_snapshot_status=raw_status,
    )


def build_card_content_index(
    *,
    products: list[dict[str, str]],
    ozon_attributes: list[dict[str, Any]] | None = None,
    ozon_descriptions: list[dict[str, Any]] | None = None,
    wb_cards: list[dict[str, Any]] | None = None,
    marketplace: str = "all",
) -> tuple[list[CardContentIndexRow], dict[str, Any]]:
    ozon_attrs_by_offer = _index_by_offer(ozon_attributes or [])
    ozon_desc_by_offer = _index_by_offer(ozon_descriptions or [])
    wb_by_vendor = _index_by_vendor(wb_cards or [])
    rows: list[CardContentIndexRow] = []

    for product in products:
        if marketplace in {"all", "ozon"} and normalize_sku(product.get("ozon_offer_id")):
            offer_id = normalize_sku(product.get("ozon_offer_id"))
            rows.append(
                normalize_ozon_card_content(
                    product=product,
                    attributes=ozon_attrs_by_offer.get(offer_id),
                    description=ozon_desc_by_offer.get(offer_id),
                )
            )
        if marketplace in {"all", "wb"} and normalize_sku(product.get("wb_vendor_code")):
            vendor_code = normalize_sku(product.get("wb_vendor_code"))
            rows.append(normalize_wb_card_content(product=product, card=wb_by_vendor.get(vendor_code)))

    summary = {
        "products": len(products),
        "content_index_rows": len(rows),
        "ozon_rows": sum(1 for row in rows if row.marketplace == "ozon"),
        "wb_rows": sum(1 for row in rows if row.marketplace == "wb"),
        "found_rows": sum(1 for row in rows if row.raw_snapshot_status == "found"),
        "missing_rows": sum(1 for row in rows if row.raw_snapshot_status == "missing"),
        "description_present_rows": sum(1 for row in rows if row.description_present == "true"),
        "photo_lt5_rows": sum(
            1 for row in rows if row.raw_snapshot_status == "found" and int(row.photo_count or "0") < 5
        ),
    }
    return rows, summary


def _write_report(path: Path, *, run_id: str, started_at: datetime, result: dict[str, Any]) -> None:
    lines = [
        "# Card Content Snapshot Report",
        "",
        f"Run ID: `{run_id}`",
        f"Started at: `{started_at.isoformat(timespec='seconds')}`",
        f"Status: `{result['overall_status']}`",
        "",
        "## Summary",
        "",
    ]
    for key in sorted(result["summary"]):
        lines.append(f"- `{key}`: {result['summary'][key]}")

    lines.extend(["", "## Errors", ""])
    if result["errors"]:
        for key, value in sorted(result["errors"].items()):
            lines.append(f"- `{key}`: {value}")
    else:
        lines.append("- none")

    lines.extend(["", "## Artifacts", ""])
    for key, value in sorted(result["artifacts"].items()):
        lines.append(f"- `{key}`: `{value}`")

    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Read-only snapshot only. No card, photo, price, stock or seller SKU writes.",
            "- Raw API payloads are runtime/generated artifacts and must not be committed.",
            "- Photo count is not visual inspection. Card recommendations still require full photo audit.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run_card_content_snapshot(
    *,
    credentials: AppCredentials,
    data_dir: Path = Path("data"),
    products_path: Path | None = None,
    output_dir: Path | None = None,
    run_id: str | None = None,
    marketplace: str = "all",
    limit_products: int | None = None,
    internal_skus: list[str] | None = None,
    merge_existing: bool = False,
    ozon_adapter: OzonSellerAdapter | None = None,
    wb_adapter: WbContentAdapter | None = None,
) -> dict[str, Any]:
    started_at = datetime.now()
    run_id = run_id or f"card_content_snapshot_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    raw_dir = ensure_dir(run_dir / "raw")
    products_path = products_path or data_dir / DEFAULT_UNIFIED_PRODUCTS_PATH
    output_dir = ensure_dir(output_dir or data_dir / DEFAULT_OUTPUT_DIR)

    products = _read_csv(products_path)
    requested_skus = {normalize_sku(item) for item in internal_skus or [] if normalize_sku(item)}
    if requested_skus:
        products = [row for row in products if normalize_sku(row.get("internal_sku")) in requested_skus]
    if limit_products is not None and limit_products > 0:
        products = products[:limit_products]

    errors: dict[str, str] = {}
    ozon_attributes: list[dict[str, Any]] = []
    ozon_descriptions: list[dict[str, Any]] = []
    wb_cards: list[dict[str, Any]] = []

    if marketplace in {"all", "ozon"}:
        offer_ids = [normalize_sku(row.get("ozon_offer_id")) for row in products if normalize_sku(row.get("ozon_offer_id"))]
        if not credentials.ozon_seller and ozon_adapter is None:
            errors["ozon"] = "missing Ozon Seller API credentials"
        else:
            try:
                ozon = ozon_adapter or OzonSellerAdapter(credentials.ozon_seller)  # type: ignore[arg-type]
                ozon_attributes = ozon.fetch_product_attributes(offer_ids)
                ozon_descriptions = ozon.fetch_product_descriptions(offer_ids)
            except Exception as exc:  # noqa: BLE001 - report external API/read errors
                errors["ozon"] = str(exc)

    if marketplace in {"all", "wb"}:
        if not credentials.wb and wb_adapter is None:
            errors["wb"] = "missing Wildberries API token"
        else:
            try:
                wb = wb_adapter or WbContentAdapter(credentials.wb)  # type: ignore[arg-type]
                wb_cards = wb.fetch_cards()
            except Exception as exc:  # noqa: BLE001
                errors["wb"] = str(exc)

    rows, summary = build_card_content_index(
        products=products,
        ozon_attributes=ozon_attributes,
        ozon_descriptions=ozon_descriptions,
        wb_cards=wb_cards,
        marketplace=marketplace,
    )
    summary.update(
        {
            "input_products": len(products),
            "marketplace": marketplace,
            "ozon_attributes": len(ozon_attributes),
            "ozon_descriptions": len(ozon_descriptions),
            "wb_cards": len(wb_cards),
        }
    )

    index_dicts = [asdict(row) for row in rows]
    index_csv_path = output_dir / "card_content_index.csv"
    index_json_path = output_dir / "card_content_index.json"
    ozon_content_path = output_dir / "ozon_card_content.json"
    wb_content_path = output_dir / "wb_card_content.json"
    report_path = run_dir / "card_content_snapshot_report.md"
    summary_path = run_dir / "summary.json"

    final_index_dicts = index_dicts
    final_ozon_content: dict[str, Any] = {"attributes": ozon_attributes, "descriptions": ozon_descriptions}
    final_wb_cards = wb_cards
    if merge_existing:
        existing_index = _read_csv(index_csv_path) if index_csv_path.exists() else []
        final_index_dicts = _merge_rows_by_key(
            existing_index,
            index_dicts,
            keys=("marketplace", "native_id"),
        )
        final_ozon_content = _merge_ozon_content(
            existing_path=ozon_content_path,
            new_attributes=ozon_attributes,
            new_descriptions=ozon_descriptions,
        )
        final_wb_cards = _merge_wb_content(existing_path=wb_content_path, new_cards=wb_cards)

    _write_dict_csv(index_csv_path, final_index_dicts, CARD_CONTENT_INDEX_FIELDS)
    write_json(index_json_path, final_index_dicts)
    write_json(ozon_content_path, final_ozon_content)
    write_json(wb_content_path, final_wb_cards)
    write_json(raw_dir / "ozon_product_attributes.json", ozon_attributes)
    write_json(raw_dir / "ozon_product_descriptions.json", ozon_descriptions)
    write_json(raw_dir / "wb_cards.json", wb_cards)

    overall_status = "warning" if errors or summary["missing_rows"] else "ok"
    artifacts = {
        "run_dir": str(run_dir),
        "raw_dir": str(raw_dir),
        "card_content_index_csv": str(index_csv_path),
        "card_content_index_json": str(index_json_path),
        "ozon_card_content_json": str(ozon_content_path),
        "wb_card_content_json": str(wb_content_path),
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
        "inputs": {
            "products_path": str(products_path),
            "marketplace": marketplace,
            "limit_products": limit_products or "",
            "internal_skus": sorted(requested_skus),
            "merge_existing": merge_existing,
        },
        "artifacts": artifacts,
    }

    write_json(summary_path, result)
    _write_report(report_path, run_id=run_id, started_at=started_at, result=result)
    write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=result,
        task="card-content-snapshot",
        mode="read_only",
        risk="low",
        marketplaces=["ozon", "wb"] if marketplace == "all" else [marketplace],
        inputs=result["inputs"],
    )
    return result
