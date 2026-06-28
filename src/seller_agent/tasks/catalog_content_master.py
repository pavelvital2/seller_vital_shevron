from __future__ import annotations

import csv
from collections import Counter
from dataclasses import asdict, dataclass, fields
from datetime import datetime
import re
from pathlib import Path
from typing import Any

from seller_agent.catalog.internal_sku_owner_review import (
    apply_owner_internal_sku_assignment,
    build_owner_internal_sku_assignments,
    owner_assignment_note,
)
from seller_agent.catalog.loader import normalize_sku
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.reports.writer import ensure_dir, write_json


DEFAULT_UNIFIED_PRODUCTS_PATH = Path("catalog/unified/products.csv")
DEFAULT_OWNER_REVIEW_PATH = Path("catalog/unified/internal_sku_assignment_owner_review.csv")
DEFAULT_OZON_CATALOG_PATH = Path("catalog/ozon/processed/ozon_catalog.csv")
DEFAULT_WB_CATALOG_PATH = Path("catalog/wb/processed/wb_catalog.csv")
DEFAULT_PRICING_STATUS_PATH = Path("pricing/pricing_status.csv")
DEFAULT_CARD_CONTENT_INDEX_PATH = Path("catalog/content/card_content_index.csv")
DEFAULT_OUTPUT_DIR = Path("catalog/content")


@dataclass
class ContentMasterRow:
    internal_product_id: str
    internal_sku: str = ""
    product_name: str = ""
    mapping_status: str = ""
    product_group: str = ""
    pack_qty: str = ""
    cost_total: str = ""
    cost_per_unit: str = ""
    canonical_title: str = ""
    ozon_offer_id: str = ""
    ozon_product_id: str = ""
    ozon_sku: str = ""
    ozon_current_title: str = ""
    ozon_status: str = ""
    ozon_action_price: str = ""
    ozon_description_present: str = ""
    ozon_description_length: str = ""
    ozon_photo_count: str = ""
    ozon_attribute_count: str = ""
    ozon_hashtags: str = ""
    ozon_content_snapshot_status: str = ""
    wb_vendor_code: str = ""
    wb_nm_id: str = ""
    wb_current_title: str = ""
    wb_status: str = ""
    wb_subject: str = ""
    wb_action_price: str = ""
    wb_description_present: str = ""
    wb_description_length: str = ""
    wb_photo_count: str = ""
    wb_attribute_count: str = ""
    wb_tags: str = ""
    wb_content_snapshot_status: str = ""
    title_alignment_status: str = ""
    marketplace_presence: str = ""
    full_snapshot_status: str = ""
    transfer_direction: str = ""
    content_review_priority: str = ""
    content_review_reasons: str = ""
    next_content_step: str = ""
    seo_ready_status: str = ""
    photo_audit_status: str = ""
    notes: str = ""


CONTENT_MASTER_FIELDS = [field.name for field in fields(ContentMasterRow)]

AUDIT_FIELDS = [
    "priority",
    "issue",
    "internal_product_id",
    "internal_sku",
    "mapping_status",
    "product_name",
    "ozon_offer_id",
    "ozon_current_title",
    "wb_vendor_code",
    "wb_current_title",
    "details",
    "next_step",
]


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


def _index_by(rows: list[dict[str, str]], field: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        key = normalize_sku(row.get(field))
        if key and key not in result:
            result[key] = row
    return result


def _normalize_title(value: str) -> str:
    text = normalize_sku(value).lower().replace("ё", "е")
    text = re.sub(r"""["'`«».,:;!?()\[\]{}]+""", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _first_text(*values: Any) -> str:
    for value in values:
        text = normalize_sku(value)
        if text:
            return text
    return ""


def _presence(product: dict[str, str], ozon: dict[str, str] | None, wb: dict[str, str] | None) -> str:
    active_ozon = product.get("active_ozon") == "true" or bool(ozon)
    active_wb = product.get("active_wb") == "true" or bool(wb)
    if active_ozon and active_wb:
        return "ozon_wb"
    if active_ozon:
        return "ozon_only"
    if active_wb:
        return "wb_only"
    return "inactive_or_missing"


def _title_status(
    *,
    canonical_title: str,
    ozon_title: str,
    wb_title: str,
    presence: str,
) -> str:
    if presence == "ozon_wb":
        if not ozon_title or not wb_title:
            return "missing_marketplace_title"
        if _normalize_title(ozon_title) == _normalize_title(wb_title):
            return "aligned"
        return "mismatch"
    if not canonical_title:
        return "missing_canonical_title"
    return "single_marketplace"


def _priority(reasons: list[str], mapping_status: str) -> str:
    if "marketplace_only" in reasons:
        return "high"
    if "title_mismatch" in reasons and mapping_status == "confirmed":
        return "high"
    if "missing_cost" in reasons or "needs_photo_content_snapshot" in reasons:
        return "normal"
    return "low"


def _next_step(reasons: list[str], presence: str) -> str:
    if "marketplace_only" in reasons and presence == "ozon_only":
        return "Проверить продажи/маржу Ozon-only и подготовить dry-run переноса на WB."
    if "marketplace_only" in reasons and presence == "wb_only":
        return "Проверить продажи/маржу WB-only и подготовить dry-run переноса на Ozon."
    if "title_mismatch" in reasons:
        return "Подтянуть полный контент и фото карточек, затем подготовить unified SEO draft."
    if "missing_cost" in reasons:
        return "Уточнить pack_qty/себестоимость перед расчетом минимальных цен."
    return "Подтянуть описание, характеристики, хештеги/теги и фото для карточного аудита."


def _pricing_by_product(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        key = normalize_sku(row.get("internal_product_id")) or normalize_sku(row.get("internal_sku"))
        if key and key not in result:
            result[key] = row
    return result


def _content_index_by_product(rows: list[dict[str, str]]) -> dict[tuple[str, str], dict[str, str]]:
    result: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        marketplace = normalize_sku(row.get("marketplace")).lower()
        internal_product_id = normalize_sku(row.get("internal_product_id"))
        if marketplace and internal_product_id:
            result[(marketplace, internal_product_id)] = row
    return result


def _content_snapshot_status(
    *,
    presence: str,
    ozon_content: dict[str, str] | None,
    wb_content: dict[str, str] | None,
) -> str:
    ozon_found = bool(ozon_content and ozon_content.get("raw_snapshot_status") == "found")
    wb_found = bool(wb_content and wb_content.get("raw_snapshot_status") == "found")
    if presence == "ozon_wb":
        if ozon_found and wb_found:
            return "both_found"
        if ozon_found:
            return "missing_wb"
        if wb_found:
            return "missing_ozon"
        return "missing_both"
    if presence == "ozon_only":
        return "ozon_found" if ozon_found else "missing_ozon"
    if presence == "wb_only":
        return "wb_found" if wb_found else "missing_wb"
    return "not_applicable"


def _photo_audit_status_from_content(
    *,
    full_snapshot_status: str,
    ozon_content: dict[str, str] | None,
    wb_content: dict[str, str] | None,
) -> str:
    if full_snapshot_status in {"missing_both", "missing_ozon", "missing_wb"}:
        return "snapshot_missing"
    counts: list[int] = []
    for content in (ozon_content, wb_content):
        if not content or content.get("raw_snapshot_status") != "found":
            continue
        try:
            counts.append(int(content.get("photo_count") or 0))
        except ValueError:
            counts.append(0)
    if not counts:
        return "not_checked"
    if any(count < 5 for count in counts):
        return "photo_count_lt5_not_inspected"
    return "photo_count_ok_not_inspected"


def build_content_master(
    *,
    products: list[dict[str, str]],
    ozon_rows: list[dict[str, str]],
    wb_rows: list[dict[str, str]],
    pricing_rows: list[dict[str, str]] | None = None,
    card_content_rows: list[dict[str, str]] | None = None,
    owner_review_rows: list[dict[str, str]] | None = None,
) -> tuple[list[ContentMasterRow], list[dict[str, str]], dict[str, Any]]:
    owner_assignments, owner_issues, owner_summary = build_owner_internal_sku_assignments(owner_review_rows or [])
    ozon_by_offer = _index_by(ozon_rows, "offer_id")
    wb_by_vendor = _index_by(wb_rows, "vendor_code")
    pricing_by_id = _pricing_by_product(pricing_rows or [])
    card_content_by_product = _content_index_by_product(card_content_rows or [])
    content_rows: list[ContentMasterRow] = []
    audit_rows: list[dict[str, str]] = []

    for product in products:
        internal_product_id = normalize_sku(product.get("internal_product_id"))
        ozon_offer_id = normalize_sku(product.get("ozon_offer_id"))
        wb_vendor_code = normalize_sku(product.get("wb_vendor_code"))
        ozon = ozon_by_offer.get(ozon_offer_id)
        wb = wb_by_vendor.get(wb_vendor_code)
        pricing = pricing_by_id.get(internal_product_id) or pricing_by_id.get(normalize_sku(product.get("internal_sku"))) or {}
        ozon_content = card_content_by_product.get(("ozon", internal_product_id))
        wb_content = card_content_by_product.get(("wb", internal_product_id))

        ozon_title = _first_text(ozon.get("title") if ozon else "", product.get("product_name") if ozon_offer_id else "")
        wb_title = _first_text(wb.get("title") if wb else "", product.get("product_name") if wb_vendor_code else "")
        canonical_title = _first_text(product.get("product_name"), ozon_title, wb_title)
        mapping_status = normalize_sku(product.get("mapping_status"))
        presence = _presence(product, ozon, wb)
        title_status = _title_status(
            canonical_title=canonical_title,
            ozon_title=ozon_title,
            wb_title=wb_title,
            presence=presence,
        )

        reasons: list[str] = []
        if presence in {"ozon_only", "wb_only"}:
            reasons.append("marketplace_only")
        if title_status == "mismatch":
            reasons.append("title_mismatch")
        if not normalize_sku(product.get("cost_total")):
            reasons.append("missing_cost")
        full_snapshot_status = _content_snapshot_status(
            presence=presence,
            ozon_content=ozon_content,
            wb_content=wb_content,
        )
        if full_snapshot_status not in {"both_found", "ozon_found", "wb_found"}:
            reasons.append("needs_photo_content_snapshot")
        priority = _priority(reasons, mapping_status)

        transfer_direction = ""
        if presence == "ozon_only":
            transfer_direction = "create_on_wb_candidate"
        elif presence == "wb_only":
            transfer_direction = "create_on_ozon_candidate"
        internal_sku, assignment_source = apply_owner_internal_sku_assignment(
            internal_product_id=internal_product_id,
            current_internal_sku=product.get("internal_sku", ""),
            assignments=owner_assignments,
        )
        notes = owner_assignment_note(product.get("notes", ""), assignment_source)
        if assignment_source == "conflict":
            audit_rows.append(
                {
                    "priority": "high",
                    "issue": "owner_review_internal_sku_conflict",
                    "internal_product_id": internal_product_id,
                    "internal_sku": normalize_sku(product.get("internal_sku")),
                    "mapping_status": mapping_status,
                    "product_name": normalize_sku(product.get("product_name")),
                    "ozon_offer_id": ozon_offer_id,
                    "ozon_current_title": ozon_title,
                    "wb_vendor_code": wb_vendor_code,
                    "wb_current_title": wb_title,
                    "details": owner_assignments.get(internal_product_id, ""),
                    "next_step": "Разобрать конфликт owner-review и unified catalog до карточных dry-run.",
                }
            )

        row = ContentMasterRow(
            internal_product_id=internal_product_id,
            internal_sku=internal_sku,
            product_name=normalize_sku(product.get("product_name")),
            mapping_status=mapping_status,
            product_group=normalize_sku(product.get("product_group")),
            pack_qty=normalize_sku(product.get("pack_qty")),
            cost_total=normalize_sku(product.get("cost_total")),
            cost_per_unit=normalize_sku(product.get("cost_per_unit")),
            canonical_title=canonical_title,
            ozon_offer_id=ozon_offer_id,
            ozon_product_id=_first_text(product.get("ozon_product_id"), ozon.get("product_id") if ozon else ""),
            ozon_sku=_first_text(product.get("ozon_sku"), ozon.get("sku") if ozon else ""),
            ozon_current_title=ozon_title,
            ozon_status=normalize_sku(ozon.get("status") if ozon else ""),
            ozon_action_price=normalize_sku(pricing.get("ozon_action_price")),
            ozon_description_present=normalize_sku(ozon_content.get("description_present") if ozon_content else ""),
            ozon_description_length=normalize_sku(ozon_content.get("description_length") if ozon_content else ""),
            ozon_photo_count=normalize_sku(ozon_content.get("photo_count") if ozon_content else ""),
            ozon_attribute_count=normalize_sku(ozon_content.get("attribute_count") if ozon_content else ""),
            ozon_hashtags=normalize_sku(ozon_content.get("hashtags_or_tags") if ozon_content else ""),
            ozon_content_snapshot_status=normalize_sku(ozon_content.get("raw_snapshot_status") if ozon_content else ""),
            wb_vendor_code=wb_vendor_code,
            wb_nm_id=_first_text(product.get("wb_nm_id"), wb.get("nm_id") if wb else ""),
            wb_current_title=wb_title,
            wb_status=normalize_sku(wb.get("status") if wb else ""),
            wb_subject=normalize_sku(wb.get("subject") if wb else ""),
            wb_action_price=normalize_sku(pricing.get("wb_action_price")),
            wb_description_present=normalize_sku(wb_content.get("description_present") if wb_content else ""),
            wb_description_length=normalize_sku(wb_content.get("description_length") if wb_content else ""),
            wb_photo_count=normalize_sku(wb_content.get("photo_count") if wb_content else ""),
            wb_attribute_count=normalize_sku(wb_content.get("attribute_count") if wb_content else ""),
            wb_tags=normalize_sku(wb_content.get("hashtags_or_tags") if wb_content else ""),
            wb_content_snapshot_status=normalize_sku(wb_content.get("raw_snapshot_status") if wb_content else ""),
            title_alignment_status=title_status,
            marketplace_presence=presence,
            full_snapshot_status=full_snapshot_status,
            transfer_direction=transfer_direction,
            content_review_priority=priority,
            content_review_reasons=";".join(reasons),
            next_content_step=_next_step(reasons, presence),
            seo_ready_status="ready_for_card_audit" if "needs_photo_content_snapshot" not in reasons else "needs_full_card_snapshot",
            photo_audit_status=_photo_audit_status_from_content(
                full_snapshot_status=full_snapshot_status,
                ozon_content=ozon_content,
                wb_content=wb_content,
            ),
            notes=notes,
        )
        content_rows.append(row)

        for reason in reasons:
            if reason == "needs_photo_content_snapshot":
                continue
            audit_rows.append(
                {
                    "priority": priority,
                    "issue": reason,
                    "internal_product_id": row.internal_product_id,
                    "internal_sku": row.internal_sku,
                    "mapping_status": row.mapping_status,
                    "product_name": row.product_name,
                    "ozon_offer_id": row.ozon_offer_id,
                    "ozon_current_title": row.ozon_current_title,
                    "wb_vendor_code": row.wb_vendor_code,
                    "wb_current_title": row.wb_current_title,
                    "details": row.content_review_reasons,
                    "next_step": row.next_content_step,
                }
            )

    by_presence = Counter(row.marketplace_presence for row in content_rows)
    by_title = Counter(row.title_alignment_status for row in content_rows)
    by_priority = Counter(row.content_review_priority for row in content_rows)
    summary = {
        "input_products": len(products),
        "content_master_rows": len(content_rows),
        "ozon_catalog_rows": len(ozon_rows),
        "wb_catalog_rows": len(wb_rows),
        "pricing_rows": len(pricing_rows or []),
        "card_content_rows": len(card_content_rows or []),
        "confirmed_rows": sum(1 for row in content_rows if row.mapping_status == "confirmed"),
        "ozon_only_rows": by_presence.get("ozon_only", 0),
        "wb_only_rows": by_presence.get("wb_only", 0),
        "both_marketplaces_rows": by_presence.get("ozon_wb", 0),
        "title_mismatch_rows": by_title.get("mismatch", 0),
        "missing_cost_rows": sum(1 for row in content_rows if "missing_cost" in row.content_review_reasons),
        "full_snapshot_found_rows": sum(
            1 for row in content_rows if row.full_snapshot_status in {"both_found", "ozon_found", "wb_found"}
        ),
        "photo_count_lt5_rows": sum(
            1 for row in content_rows if row.photo_audit_status == "photo_count_lt5_not_inspected"
        ),
        "owner_review_rows": owner_summary["owner_review_rows"],
        "owner_review_approved_rows": owner_summary["owner_review_approved_rows"],
        "owner_review_assignments": owner_summary["owner_review_assignments"],
        "owner_review_applied_rows": sum(
            1 for row in content_rows if "owner_approved_internal_sku" in row.notes.split(";")
        ),
        "owner_review_issue_count": len(owner_issues),
        "high_priority_rows": by_priority.get("high", 0),
        "normal_priority_rows": by_priority.get("normal", 0),
        "audit_rows": len(audit_rows) + len(owner_issues),
    }
    for issue in owner_issues:
        audit_rows.append(
            {
                "priority": "high",
                "issue": issue.get("kind", "owner_review_issue"),
                "internal_product_id": issue.get("value", ""),
                "internal_sku": "",
                "mapping_status": "",
                "product_name": "",
                "ozon_offer_id": "",
                "ozon_current_title": "",
                "wb_vendor_code": "",
                "wb_current_title": "",
                "details": issue.get("details", ""),
                "next_step": "Разобрать owner-review конфликт до карточных dry-run.",
            }
        )
    return content_rows, audit_rows, summary


def _write_report(path: Path, *, run_id: str, started_at: datetime, result: dict[str, Any]) -> None:
    lines = [
        "# Catalog Content Master Report",
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

    lines.extend(["", "## Audit Samples", ""])
    audit_rows = result.get("audit_sample", [])
    if audit_rows:
        for row in audit_rows[:30]:
            lines.append(
                f"- `{row['priority']}` `{row['issue']}` "
                f"`{row['internal_product_id']}`: {row['product_name']}"
            )
        if len(audit_rows) > 30:
            lines.append(f"- truncated: {len(audit_rows) - 30} more rows")
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
            "- This is a read-only derived layer.",
            "- It does not update Ozon/WB cards, photos, prices, stocks or seller SKUs.",
            "- `photo_audit_status=not_checked` means photos were not inspected in this run.",
            "- Full card content and photo snapshots must be collected before marketplace card recommendations.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run_catalog_content_master(
    *,
    data_dir: Path = Path("data"),
    products_path: Path | None = None,
    owner_review_path: Path | None = None,
    ozon_catalog_path: Path | None = None,
    wb_catalog_path: Path | None = None,
    pricing_status_path: Path | None = None,
    card_content_index_path: Path | None = None,
    output_dir: Path | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    started_at = datetime.now()
    run_id = run_id or f"catalog_content_master_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)

    products_path = products_path or data_dir / DEFAULT_UNIFIED_PRODUCTS_PATH
    owner_review_path = owner_review_path or data_dir / DEFAULT_OWNER_REVIEW_PATH
    ozon_catalog_path = ozon_catalog_path or data_dir / DEFAULT_OZON_CATALOG_PATH
    wb_catalog_path = wb_catalog_path or data_dir / DEFAULT_WB_CATALOG_PATH
    pricing_status_path = pricing_status_path or data_dir / DEFAULT_PRICING_STATUS_PATH
    card_content_index_path = card_content_index_path or data_dir / DEFAULT_CARD_CONTENT_INDEX_PATH
    output_dir = ensure_dir(output_dir or data_dir / DEFAULT_OUTPUT_DIR)

    errors: dict[str, str] = {}
    products: list[dict[str, str]] = []
    ozon_rows: list[dict[str, str]] = []
    wb_rows: list[dict[str, str]] = []
    pricing_rows: list[dict[str, str]] = []
    card_content_rows: list[dict[str, str]] = []
    owner_review_rows: list[dict[str, str]] = []

    for label, path, required in (
        ("products", products_path, True),
        ("ozon_catalog", ozon_catalog_path, True),
        ("wb_catalog", wb_catalog_path, True),
        ("pricing_status", pricing_status_path, False),
        ("card_content_index", card_content_index_path, False),
    ):
        try:
            rows = _read_csv(path)
            if label == "products":
                products = rows
            elif label == "ozon_catalog":
                ozon_rows = rows
            elif label == "wb_catalog":
                wb_rows = rows
            else:
                if label == "pricing_status":
                    pricing_rows = rows
                else:
                    card_content_rows = rows
        except Exception as exc:  # noqa: BLE001 - task report must capture missing/bad inputs
            if required:
                errors[label] = str(exc)
    if owner_review_path.exists():
        try:
            owner_review_rows = _read_csv(owner_review_path)
        except Exception as exc:  # noqa: BLE001 - optional input errors should be visible
            errors["owner_review"] = str(exc)

    content_rows: list[ContentMasterRow] = []
    audit_rows: list[dict[str, str]] = []
    summary: dict[str, Any] = {
        "input_products": len(products),
        "content_master_rows": 0,
        "ozon_catalog_rows": len(ozon_rows),
        "wb_catalog_rows": len(wb_rows),
        "pricing_rows": len(pricing_rows),
        "card_content_rows": len(card_content_rows),
        "owner_review_rows": len(owner_review_rows),
        "owner_review_approved_rows": 0,
        "owner_review_assignments": 0,
        "owner_review_applied_rows": 0,
        "owner_review_issue_count": 0,
        "confirmed_rows": 0,
        "ozon_only_rows": 0,
        "wb_only_rows": 0,
        "both_marketplaces_rows": 0,
        "title_mismatch_rows": 0,
        "missing_cost_rows": 0,
        "full_snapshot_found_rows": 0,
        "photo_count_lt5_rows": 0,
        "high_priority_rows": 0,
        "normal_priority_rows": 0,
        "audit_rows": 0,
    }
    if not errors:
        content_rows, audit_rows, summary = build_content_master(
            products=products,
            ozon_rows=ozon_rows,
            wb_rows=wb_rows,
            pricing_rows=pricing_rows,
            card_content_rows=card_content_rows,
            owner_review_rows=owner_review_rows,
        )

    content_dicts = [asdict(row) for row in content_rows]
    content_csv_path = output_dir / "content_master.csv"
    content_json_path = output_dir / "content_master.json"
    audit_csv_path = output_dir / "content_audit.csv"
    audit_json_path = output_dir / "content_audit.json"
    report_path = run_dir / "catalog_content_master_report.md"
    summary_path = run_dir / "summary.json"

    if not errors:
        _write_dict_csv(content_csv_path, content_dicts, CONTENT_MASTER_FIELDS)
        write_json(content_json_path, content_dicts)
        _write_dict_csv(audit_csv_path, audit_rows, AUDIT_FIELDS)
        write_json(audit_json_path, audit_rows)

    overall_status = "error" if errors else "warning" if audit_rows else "ok"
    artifacts = {
        "run_dir": str(run_dir),
        "content_master_csv": str(content_csv_path),
        "content_master_json": str(content_json_path),
        "content_audit_csv": str(audit_csv_path),
        "content_audit_json": str(audit_json_path),
        "report": str(report_path),
        "summary": str(summary_path),
        "run_manifest": str(run_dir / "manifest.json"),
    }
    audit_sample = audit_rows[:50]
    result = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "read_only",
        "overall_status": overall_status,
        "summary": summary,
        "errors": errors,
        "audit_sample": audit_sample,
        "audit_sample_truncated": max(0, len(audit_rows) - len(audit_sample)),
        "inputs": {
            "products_path": str(products_path),
            "owner_review_path": str(owner_review_path),
            "ozon_catalog_path": str(ozon_catalog_path),
            "wb_catalog_path": str(wb_catalog_path),
            "pricing_status_path": str(pricing_status_path),
            "card_content_index_path": str(card_content_index_path),
        },
        "artifacts": artifacts,
    }

    write_json(summary_path, result)
    _write_report(report_path, run_id=run_id, started_at=started_at, result=result)
    write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=result,
        task="catalog-content-master",
        mode="read_only",
        risk="low",
        marketplaces=["ozon", "wb"],
        inputs=result["inputs"],
    )
    return result
