from __future__ import annotations

import csv
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
import re
from pathlib import Path
from typing import Any

from seller_agent.catalog.loader import normalize_sku
from seller_agent.catalog.schema import UNIFIED_CATALOG_FIELDS, UnifiedCatalogProduct
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.reports.writer import ensure_dir, write_json


DEFAULT_MAPPING_PATH = Path("catalog/mapping/ozon_wb_internal_sku_confirmed.csv")
DEFAULT_OZON_CATALOG_PATH = Path("catalog/ozon/processed/ozon_catalog.csv")
DEFAULT_WB_CATALOG_PATH = Path("catalog/wb/processed/wb_catalog.csv")
DEFAULT_OUTPUT_DIR = Path("catalog/unified")
DEFAULT_UNIT_COST_RUB = Decimal("85")


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


def _index_by(rows: list[dict[str, str]], field: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        key = normalize_sku(row.get(field))
        if key and key not in result:
            result[key] = row
    return result


def _duplicate_values(rows: list[dict[str, str]], field: str) -> list[str]:
    values = [normalize_sku(row.get(field)) for row in rows if normalize_sku(row.get(field))]
    return sorted(value for value, count in Counter(values).items() if count > 1)


def parse_pack_qty(internal_sku: str) -> int:
    match = re.search(r"(?:^|_)kit(\d+)(?:_|$)", internal_sku)
    if not match:
        return 1
    try:
        return max(1, int(match.group(1)))
    except ValueError:
        return 1


def product_group_from_internal_sku(internal_sku: str) -> str:
    prefix = internal_sku.split("_", 1)[0]
    return prefix or "unknown"


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("1")))


def _is_active_marketplace_row(row: dict[str, str] | None) -> str:
    if not row:
        return "false"
    status = normalize_sku(row.get("status")).lower()
    if any(marker in status for marker in ("archive", "archived", "deleted", "blocked")):
        return "false"
    return "true"


def _mapping_row_status(row: dict[str, str]) -> str:
    status = normalize_sku(
        row.get("review_status")
        or row.get("owner_decision")
        or row.get("mapping_status")
    )
    return status.lower() or "confirmed"


def _is_confirmed_mapping(row: dict[str, str]) -> bool:
    status = _mapping_row_status(row)
    if status.startswith(("owner_confirmed", "owner_corrected")):
        return True
    return status in {"confirmed", "approved", "да", "yes", "true", "1"}


def _mapped_ozon_offer_ids(mapping_rows: list[dict[str, str]]) -> set[str]:
    return {
        normalize_sku(row.get("ozon_offer_id"))
        for row in mapping_rows
        if normalize_sku(row.get("ozon_offer_id"))
    }


def _mapped_wb_vendor_codes(mapping_rows: list[dict[str, str]]) -> set[str]:
    return {
        normalize_sku(row.get("wb_vendor_code"))
        for row in mapping_rows
        if normalize_sku(row.get("wb_vendor_code"))
    }


def _issue(kind: str, field: str, value: str, details: str = "") -> dict[str, str]:
    return {"kind": kind, "field": field, "value": value, "details": details}


def build_unified_products(
    *,
    ozon_rows: list[dict[str, str]],
    wb_rows: list[dict[str, str]],
    mapping_rows: list[dict[str, str]],
    unit_cost_rub: Decimal = DEFAULT_UNIT_COST_RUB,
) -> tuple[list[UnifiedCatalogProduct], list[dict[str, str]], dict[str, Any]]:
    confirmed_mapping_rows = [row for row in mapping_rows if _is_confirmed_mapping(row)]
    ozon_by_offer = _index_by(ozon_rows, "offer_id")
    wb_by_vendor = _index_by(wb_rows, "vendor_code")
    products: list[UnifiedCatalogProduct] = []
    issues: list[dict[str, str]] = []

    for field in ("internal_sku", "ozon_offer_id", "wb_vendor_code"):
        for value in _duplicate_values(confirmed_mapping_rows, field):
            issues.append(_issue("duplicate_mapping_value", field, value))

    for row in confirmed_mapping_rows:
        internal_sku = normalize_sku(row.get("internal_sku"))
        ozon_offer_id = normalize_sku(row.get("ozon_offer_id"))
        wb_vendor_code = normalize_sku(row.get("wb_vendor_code"))
        ozon = ozon_by_offer.get(ozon_offer_id)
        wb = wb_by_vendor.get(wb_vendor_code)
        notes = [normalize_sku(row.get("notes"))] if normalize_sku(row.get("notes")) else []

        if not internal_sku:
            issues.append(
                _issue(
                    "missing_internal_sku",
                    "internal_sku",
                    "",
                    ozon_offer_id or wb_vendor_code,
                )
            )
            notes.append("missing_internal_sku")
        if ozon_offer_id and not ozon:
            issues.append(_issue("missing_ozon_catalog_row", "ozon_offer_id", ozon_offer_id, internal_sku))
            notes.append("missing_ozon_catalog_row")
        if wb_vendor_code and not wb:
            issues.append(_issue("missing_wb_catalog_row", "wb_vendor_code", wb_vendor_code, internal_sku))
            notes.append("missing_wb_catalog_row")

        pack_qty = parse_pack_qty(internal_sku)
        products.append(
            UnifiedCatalogProduct(
                internal_product_id=internal_sku or f"mapping:{ozon_offer_id or wb_vendor_code}",
                internal_sku=internal_sku,
                product_name=_first_text(
                    row.get("product_name"),
                    row.get("ozon_title"),
                    row.get("wb_title"),
                    ozon.get("title") if ozon else "",
                    wb.get("title") if wb else "",
                ),
                product_group=product_group_from_internal_sku(internal_sku),
                pack_qty=str(pack_qty),
                cost_total=_money(unit_cost_rub * pack_qty),
                cost_per_unit=_money(unit_cost_rub),
                ozon_offer_id=ozon_offer_id,
                ozon_product_id=_first_text(
                    row.get("ozon_product_id"),
                    ozon.get("product_id") if ozon else "",
                ),
                ozon_sku=_first_text(row.get("ozon_sku"), ozon.get("sku") if ozon else ""),
                wb_vendor_code=wb_vendor_code,
                wb_nm_id=_first_text(row.get("wb_nm_id"), wb.get("nm_id") if wb else ""),
                mapping_status="confirmed",
                active_ozon=_is_active_marketplace_row(ozon),
                active_wb=_is_active_marketplace_row(wb),
                notes=";".join(note for note in notes if note),
            )
        )

    mapped_ozon = _mapped_ozon_offer_ids(confirmed_mapping_rows)
    mapped_wb = _mapped_wb_vendor_codes(confirmed_mapping_rows)

    for offer_id, row in sorted(ozon_by_offer.items(), key=lambda item: item[0].lower()):
        if offer_id in mapped_ozon:
            continue
        products.append(
            UnifiedCatalogProduct(
                internal_product_id=f"ozon:{offer_id}",
                product_name=_first_text(row.get("title"), offer_id),
                product_group="ozon_only",
                ozon_offer_id=offer_id,
                ozon_product_id=normalize_sku(row.get("product_id")),
                ozon_sku=normalize_sku(row.get("sku")),
                mapping_status="ozon_only",
                active_ozon=_is_active_marketplace_row(row),
                active_wb="false",
                notes="not_confirmed_in_mapping",
            )
        )

    for vendor_code, row in sorted(wb_by_vendor.items(), key=lambda item: item[0].lower()):
        if vendor_code in mapped_wb:
            continue
        products.append(
            UnifiedCatalogProduct(
                internal_product_id=f"wb:{vendor_code}",
                product_name=_first_text(row.get("title"), vendor_code),
                product_group="wb_only",
                wb_vendor_code=vendor_code,
                wb_nm_id=normalize_sku(row.get("nm_id")),
                mapping_status="wb_only",
                active_ozon="false",
                active_wb=_is_active_marketplace_row(row),
                notes="not_confirmed_in_mapping",
            )
        )

    summary = {
        "ozon_catalog_rows": len(ozon_rows),
        "wb_catalog_rows": len(wb_rows),
        "mapping_rows": len(mapping_rows),
        "confirmed_mapping_rows": len(confirmed_mapping_rows),
        "unified_products": len(products),
        "confirmed_products": sum(
            1 for product in products if product.mapping_status == "confirmed"
        ),
        "ozon_only_products": sum(1 for product in products if product.mapping_status == "ozon_only"),
        "wb_only_products": sum(1 for product in products if product.mapping_status == "wb_only"),
        "issue_count": len(issues),
    }
    return products, issues, summary


def _write_report(path: Path, *, run_id: str, started_at: datetime, result: dict[str, Any]) -> None:
    lines = [
        "# Unified Catalog Build Report",
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

    lines.extend(["", "## Issues", ""])
    issues = result.get("issues", [])
    if issues:
        for issue in issues[:100]:
            lines.append(
                f"- `{issue.get('kind')}` `{issue.get('field')}`="
                f"`{issue.get('value')}` {issue.get('details', '')}".rstrip()
            )
        if len(issues) > 100:
            lines.append(f"- truncated: {len(issues) - 100} more issues")
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
            "- `products.csv/json` is an internal product-level catalog.",
            "- It does not rename Ozon `offer_id` or WB `vendorCode`.",
            "- Cross-marketplace write operations still require confirmed mapping per row.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run_build_unified_catalog(
    *,
    data_dir: Path = Path("data"),
    mapping_path: Path | None = None,
    ozon_catalog_path: Path | None = None,
    wb_catalog_path: Path | None = None,
    output_dir: Path | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    started_at = datetime.now()
    run_id = run_id or f"catalog_build_unified_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)

    mapping_path = mapping_path or data_dir / DEFAULT_MAPPING_PATH
    ozon_catalog_path = ozon_catalog_path or data_dir / DEFAULT_OZON_CATALOG_PATH
    wb_catalog_path = wb_catalog_path or data_dir / DEFAULT_WB_CATALOG_PATH
    output_dir = output_dir or data_dir / DEFAULT_OUTPUT_DIR
    output_dir = ensure_dir(output_dir)

    errors: dict[str, str] = {}
    ozon_rows: list[dict[str, str]] = []
    wb_rows: list[dict[str, str]] = []
    mapping_rows: list[dict[str, str]] = []

    for label, path in (
        ("mapping", mapping_path),
        ("ozon_catalog", ozon_catalog_path),
        ("wb_catalog", wb_catalog_path),
    ):
        try:
            rows = _read_csv(path)
            if label == "mapping":
                mapping_rows = rows
            elif label == "ozon_catalog":
                ozon_rows = rows
            else:
                wb_rows = rows
        except Exception as exc:  # noqa: BLE001 - task report must capture missing/bad local inputs
            errors[label] = str(exc)

    products: list[UnifiedCatalogProduct] = []
    issues: list[dict[str, str]] = []
    summary: dict[str, Any] = {
        "ozon_catalog_rows": len(ozon_rows),
        "wb_catalog_rows": len(wb_rows),
        "mapping_rows": len(mapping_rows),
        "confirmed_mapping_rows": 0,
        "unified_products": 0,
        "confirmed_products": 0,
        "ozon_only_products": 0,
        "wb_only_products": 0,
        "issue_count": 0,
    }
    if not errors:
        products, issues, summary = build_unified_products(
            ozon_rows=ozon_rows,
            wb_rows=wb_rows,
            mapping_rows=mapping_rows,
        )
        summary["issue_count"] = len(issues)

    products_json_path = output_dir / "products.json"
    products_csv_path = output_dir / "products.csv"
    issues_json_path = run_dir / "unified_catalog_issues.json"
    report_path = run_dir / "unified_catalog_report.md"
    summary_path = run_dir / "summary.json"
    product_dicts = [asdict(product) for product in products]
    if not errors:
        write_json(products_json_path, product_dicts)
        _write_dict_csv(products_csv_path, product_dicts, UNIFIED_CATALOG_FIELDS)
    write_json(issues_json_path, issues)

    overall_status = "error" if errors else "warning" if issues else "ok"
    artifacts = {
        "run_dir": str(run_dir),
        "products_json": str(products_json_path),
        "products_csv": str(products_csv_path),
        "issues_json": str(issues_json_path),
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
        "issues": issues,
        "inputs": {
            "mapping_path": str(mapping_path),
            "ozon_catalog_path": str(ozon_catalog_path),
            "wb_catalog_path": str(wb_catalog_path),
        },
        "artifacts": artifacts,
    }

    write_json(summary_path, result)
    _write_report(report_path, run_id=run_id, started_at=started_at, result=result)
    write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=result,
        task="catalog-build-unified",
        mode="read_only",
        risk="low",
        marketplaces=["ozon", "wb"],
        inputs=result["inputs"],
    )
    return result
