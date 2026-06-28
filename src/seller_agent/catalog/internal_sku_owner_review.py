from __future__ import annotations

from typing import Any

from seller_agent.catalog.loader import normalize_sku


OWNER_APPROVED_INTERNAL_SKU_STATUSES = {
    "owner_confirmed_internal_sku",
    "owner_corrected_internal_sku",
}


def _fallback_internal_product_id(row: dict[str, str]) -> str:
    marketplace = normalize_sku(row.get("source_marketplace")).lower()
    source_id = normalize_sku(row.get("source_id"))
    if marketplace in {"ozon", "wb"} and source_id:
        return f"{marketplace}:{source_id}"
    return ""


def build_owner_internal_sku_assignments(
    rows: list[dict[str, str]],
) -> tuple[dict[str, str], list[dict[str, str]], dict[str, Any]]:
    """Return owner-approved internal SKU assignments keyed by internal product id.

    The owner-review CSV is an approval layer for marketplace-only products.
    It does not rename marketplace seller SKUs; it only supplies the internal
    SKU that derived catalogs and card-audit packages should use.
    """

    assignments: dict[str, str] = {}
    issues: list[dict[str, str]] = []
    approved_rows = 0
    skipped_rows = 0

    for row in rows:
        status = normalize_sku(row.get("review_status")).lower()
        approved_internal_sku = normalize_sku(row.get("approved_internal_sku"))
        if status not in OWNER_APPROVED_INTERNAL_SKU_STATUSES or not approved_internal_sku:
            skipped_rows += 1
            continue

        approved_rows += 1
        internal_product_id = normalize_sku(row.get("current_internal_product_id")) or _fallback_internal_product_id(row)
        if not internal_product_id:
            issues.append(
                {
                    "kind": "owner_review_missing_internal_product_id",
                    "field": "current_internal_product_id",
                    "value": "",
                    "details": approved_internal_sku,
                }
            )
            continue

        existing = assignments.get(internal_product_id)
        if existing and existing != approved_internal_sku:
            issues.append(
                {
                    "kind": "owner_review_conflicting_internal_sku",
                    "field": "current_internal_product_id",
                    "value": internal_product_id,
                    "details": f"{existing} != {approved_internal_sku}",
                }
            )
            continue
        assignments[internal_product_id] = approved_internal_sku

    by_sku: dict[str, set[str]] = {}
    for internal_product_id, internal_sku in assignments.items():
        by_sku.setdefault(internal_sku, set()).add(internal_product_id)
    for internal_sku, product_ids in sorted(by_sku.items()):
        if len(product_ids) > 1:
            issues.append(
                {
                    "kind": "owner_review_duplicate_internal_sku",
                    "field": "approved_internal_sku",
                    "value": internal_sku,
                    "details": ";".join(sorted(product_ids)),
                }
            )

    summary = {
        "owner_review_rows": len(rows),
        "owner_review_approved_rows": approved_rows,
        "owner_review_skipped_rows": skipped_rows,
        "owner_review_assignments": len(assignments),
        "owner_review_issue_count": len(issues),
    }
    return assignments, issues, summary


def apply_owner_internal_sku_assignment(
    *,
    internal_product_id: str,
    current_internal_sku: str,
    assignments: dict[str, str],
) -> tuple[str, str]:
    approved_internal_sku = assignments.get(normalize_sku(internal_product_id))
    current_internal_sku = normalize_sku(current_internal_sku)
    if not approved_internal_sku:
        return current_internal_sku, ""
    if not current_internal_sku:
        return approved_internal_sku, "owner_review"
    if current_internal_sku == approved_internal_sku:
        return current_internal_sku, "already_aligned"
    return current_internal_sku, "conflict"


def owner_assignment_note(existing_notes: str, source: str) -> str:
    notes = [part for part in normalize_sku(existing_notes).split(";") if part]
    if source == "owner_review":
        notes = [part for part in notes if part != "not_confirmed_in_mapping"]
        if "owner_approved_internal_sku" not in notes:
            notes.append("owner_approved_internal_sku")
        if "not_cross_marketplace_mapped" not in notes:
            notes.append("not_cross_marketplace_mapped")
    elif source == "conflict" and "owner_review_internal_sku_conflict" not in notes:
        notes.append("owner_review_internal_sku_conflict")
    return ";".join(notes)
