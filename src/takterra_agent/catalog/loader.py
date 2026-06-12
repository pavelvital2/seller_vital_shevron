from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from typing import Any

from .schema import MasterCatalogRow


def normalize_sku(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if value is not None and not isinstance(value, (dict, list, tuple, set)):
            text = str(value).strip()
            if text:
                return text
    return ""


def _first_barcode(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        for item in value:
            text = _first_barcode(item)
            if text:
                return text
    if isinstance(value, dict):
        for key in ("barcode", "barcodes", "sku", "skus"):
            text = _first_barcode(value.get(key))
            if text:
                return text
    return ""


def _is_ozon_platform_barcode(value: str) -> bool:
    return value.upper().startswith("OZN")


def _ozon_status(info: dict[str, Any], base: dict[str, Any]) -> str:
    status = info.get("statuses") or info.get("status")
    if isinstance(status, dict):
        return _first_text(
            status.get("status_name"),
            status.get("status_description"),
            status.get("state_name"),
            status.get("state"),
            status.get("status"),
        )
    return _first_text(
        info.get("status_name"),
        info.get("status_description"),
        base.get("visibility"),
        status,
    )


def _ozon_product_id(item: dict[str, Any]) -> str:
    return _first_text(item.get("product_id"), item.get("id"))


def extract_ozon_items(
    product_list: list[dict[str, Any]],
    product_info: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, str]], dict[str, int]]:
    info_by_product_id = {
        _ozon_product_id(item): item for item in product_info if _ozon_product_id(item)
    }
    info_by_offer_id = {
        normalize_sku(item.get("offer_id")): item
        for item in product_info
        if normalize_sku(item.get("offer_id"))
    }

    merged: dict[str, dict[str, str]] = {}
    seen_offers: list[str] = []

    for base in product_list:
        offer_id = normalize_sku(base.get("offer_id"))
        product_id = _ozon_product_id(base)
        info = info_by_product_id.get(product_id) or info_by_offer_id.get(offer_id) or {}
        if not offer_id:
            offer_id = normalize_sku(info.get("offer_id"))
        if not offer_id:
            continue
        seen_offers.append(offer_id)
        merged[offer_id] = {
            "offer_id": offer_id,
            "product_id": _first_text(product_id, _ozon_product_id(info)),
            "sku": _first_text(info.get("sku"), info.get("fbo_sku"), info.get("fbs_sku")),
            "barcode": _first_barcode(info.get("barcodes")) or _first_barcode(info.get("barcode")),
            "title": _first_text(info.get("name"), base.get("name")),
            "status": _ozon_status(info, base),
        }

    for info in product_info:
        offer_id = normalize_sku(info.get("offer_id"))
        if not offer_id or offer_id in merged:
            continue
        seen_offers.append(offer_id)
        merged[offer_id] = {
            "offer_id": offer_id,
            "product_id": _ozon_product_id(info),
            "sku": _first_text(info.get("sku"), info.get("fbo_sku"), info.get("fbs_sku")),
            "barcode": _first_barcode(info.get("barcodes")) or _first_barcode(info.get("barcode")),
            "title": _first_text(info.get("name")),
            "status": _ozon_status(info, {}),
        }

    duplicate_count = sum(count - 1 for count in Counter(seen_offers).values() if count > 1)
    return merged, {"duplicate_offer_id_count": duplicate_count}


def extract_wb_items(cards: list[dict[str, Any]]) -> tuple[dict[str, dict[str, str]], dict[str, int]]:
    merged: dict[str, dict[str, str]] = {}
    seen_vendor_codes: list[str] = []

    for card in cards:
        vendor_code = normalize_sku(card.get("vendorCode"))
        if not vendor_code:
            continue
        seen_vendor_codes.append(vendor_code)
        merged[vendor_code] = {
            "vendor_code": vendor_code,
            "nm_id": _first_text(card.get("nmID"), card.get("nmId")),
            "barcode": _first_barcode(card.get("sizes")),
            "title": _first_text(card.get("title"), card.get("subjectName")),
            "subject": _first_text(card.get("subjectName"), card.get("object")),
            "brand": _first_text(card.get("brand")),
            "status": "present",
        }

    duplicate_count = sum(
        count - 1 for count in Counter(seen_vendor_codes).values() if count > 1
    )
    return merged, {"duplicate_vendor_code_count": duplicate_count}


def build_master_catalog(
    product_list: list[dict[str, Any]],
    product_info: list[dict[str, Any]],
    wb_cards: list[dict[str, Any]],
) -> tuple[list[MasterCatalogRow], dict[str, Any]]:
    ozon_items, ozon_stats = extract_ozon_items(product_list, product_info)
    wb_items, wb_stats = extract_wb_items(wb_cards)

    master_skus = sorted(set(ozon_items) | set(wb_items), key=lambda value: value.lower())
    rows: list[MasterCatalogRow] = []

    for master_sku in master_skus:
        ozon = ozon_items.get(master_sku, {})
        wb = wb_items.get(master_sku, {})
        notes: list[str] = []
        if ozon and wb:
            match_status = "matched"
        elif ozon:
            match_status = "ozon_only"
            notes.append("not_found_in_wb")
        else:
            match_status = "wb_only"
            notes.append("not_found_in_ozon")

        ozon_barcode = ozon.get("barcode", "")
        wb_barcode = wb.get("barcode", "")
        if ozon_barcode and wb_barcode and ozon_barcode != wb_barcode:
            if _is_ozon_platform_barcode(ozon_barcode):
                notes.append("ozon_platform_barcode")
            else:
                notes.append("barcode_mismatch")

        rows.append(
            MasterCatalogRow(
                master_sku=master_sku,
                title=ozon.get("title") or wb.get("title", ""),
                product_group=wb.get("subject", ""),
                ozon_offer_id=ozon.get("offer_id", ""),
                ozon_product_id=ozon.get("product_id", ""),
                ozon_sku=ozon.get("sku", ""),
                ozon_barcode=ozon_barcode,
                wb_vendor_code=wb.get("vendor_code", ""),
                wb_nm_id=wb.get("nm_id", ""),
                wb_barcode=wb_barcode,
                status_ozon=ozon.get("status", ""),
                status_wb=wb.get("status", ""),
                match_status=match_status,
                notes=";".join(notes),
            )
        )

    summary = {
        "ozon_unique_offer_ids": len(ozon_items),
        "wb_unique_vendor_codes": len(wb_items),
        "master_catalog_rows": len(rows),
        "matched_rows": sum(1 for row in rows if row.match_status == "matched"),
        "ozon_only_rows": sum(1 for row in rows if row.match_status == "ozon_only"),
        "wb_only_rows": sum(1 for row in rows if row.match_status == "wb_only"),
        "barcode_mismatch_rows": sum(1 for row in rows if "barcode_mismatch" in row.notes),
        "ozon_platform_barcode_rows": sum(
            1 for row in rows if "ozon_platform_barcode" in row.notes
        ),
        **ozon_stats,
        **wb_stats,
    }
    return rows, summary


def rows_as_dicts(rows: list[MasterCatalogRow]) -> list[dict[str, str]]:
    return [asdict(row) for row in rows]
