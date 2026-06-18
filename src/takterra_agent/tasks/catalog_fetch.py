from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from takterra_agent.catalog.loader import (
    build_master_catalog,
    extract_ozon_items,
    extract_wb_items,
    rows_as_dicts,
)
from takterra_agent.config import AppCredentials
from takterra_agent.core.run_manifest import write_summary_run_manifest
from takterra_agent.marketplaces.ozon.adapter import OzonSellerAdapter
from takterra_agent.marketplaces.wb.adapter import WbContentAdapter
from takterra_agent.reports.writer import (
    ensure_dir,
    write_json,
    write_markdown_report,
    write_master_catalog_csv,
)


def _write_dict_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    ensure_dir(path.parent)
    fields = sorted({field for row in rows for field in row})
    if not fields:
        path.write_text("", encoding="utf-8")
        return
    import csv

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _product_id_from_ozon_list_item(item: dict[str, Any]) -> str:
    value = item.get("product_id") or item.get("id") or ""
    return str(value).strip()


def run_catalog_fetch(
    *,
    credentials: AppCredentials,
    data_dir: Path = Path("data"),
    run_id: str | None = None,
) -> dict[str, Any]:
    started_at = datetime.now()
    run_id = run_id or f"catalog_fetch_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    processed_dir = ensure_dir(data_dir / "catalog" / "processed")
    ozon_raw_dir = ensure_dir(data_dir / "catalog" / "ozon" / "raw" / started_at.strftime("%Y-%m-%d") / run_id)
    wb_raw_dir = ensure_dir(data_dir / "catalog" / "wb" / "raw" / started_at.strftime("%Y-%m-%d") / run_id)
    ozon_processed_dir = ensure_dir(data_dir / "catalog" / "ozon" / "processed")
    wb_processed_dir = ensure_dir(data_dir / "catalog" / "wb" / "processed")

    errors: dict[str, str] = {}
    ozon_product_list: list[dict[str, Any]] = []
    ozon_product_info: list[dict[str, Any]] = []
    wb_cards: list[dict[str, Any]] = []

    if credentials.ozon_seller:
        try:
            ozon = OzonSellerAdapter(credentials.ozon_seller)
            ozon_product_list = ozon.fetch_product_list()
            product_ids = [
                product_id
                for product_id in (_product_id_from_ozon_list_item(item) for item in ozon_product_list)
                if product_id
            ]
            ozon_product_info = ozon.fetch_product_info(product_ids)
        except Exception as exc:  # noqa: BLE001 - task report must capture external API failures
            errors["ozon"] = str(exc)
    else:
        errors["ozon"] = "missing Ozon Seller API credentials"

    if credentials.wb:
        try:
            wb = WbContentAdapter(credentials.wb)
            wb_cards = wb.fetch_cards()
        except Exception as exc:  # noqa: BLE001
            errors["wb"] = str(exc)
    else:
        errors["wb"] = "missing Wildberries API token"

    write_json(ozon_raw_dir / "ozon_product_list.json", ozon_product_list)
    write_json(ozon_raw_dir / "ozon_product_info.json", ozon_product_info)
    write_json(wb_raw_dir / "wb_cards.json", wb_cards)

    rows, catalog_summary = build_master_catalog(
        ozon_product_list,
        ozon_product_info,
        wb_cards,
    )
    ozon_items, _ozon_stats = extract_ozon_items(ozon_product_list, ozon_product_info)
    wb_items, _wb_stats = extract_wb_items(wb_cards)
    ozon_catalog_rows = sorted(ozon_items.values(), key=lambda row: row.get("offer_id", "").lower())
    wb_catalog_rows = sorted(wb_items.values(), key=lambda row: row.get("vendor_code", "").lower())

    ozon_catalog_json_path = ozon_processed_dir / "ozon_catalog.json"
    ozon_catalog_csv_path = ozon_processed_dir / "ozon_catalog.csv"
    wb_catalog_json_path = wb_processed_dir / "wb_catalog.json"
    wb_catalog_csv_path = wb_processed_dir / "wb_catalog.csv"
    write_json(ozon_catalog_json_path, ozon_catalog_rows)
    _write_dict_csv(ozon_catalog_csv_path, ozon_catalog_rows)
    write_json(wb_catalog_json_path, wb_catalog_rows)
    _write_dict_csv(wb_catalog_csv_path, wb_catalog_rows)

    master_json_path = processed_dir / "master_catalog.json"
    master_csv_path = processed_dir / "master_catalog.csv"
    write_json(master_json_path, rows_as_dicts(rows))
    write_master_catalog_csv(master_csv_path, rows)

    summary = {
        "ozon_product_list_count": len(ozon_product_list),
        "ozon_product_info_count": len(ozon_product_info),
        "wb_cards_count": len(wb_cards),
        "ozon_catalog_rows": len(ozon_catalog_rows),
        "wb_catalog_rows": len(wb_catalog_rows),
        **catalog_summary,
    }

    artifacts = {
        "run_dir": str(run_dir),
        "ozon_raw_dir": str(ozon_raw_dir),
        "wb_raw_dir": str(wb_raw_dir),
        "ozon_catalog_json": str(ozon_catalog_json_path),
        "ozon_catalog_csv": str(ozon_catalog_csv_path),
        "wb_catalog_json": str(wb_catalog_json_path),
        "wb_catalog_csv": str(wb_catalog_csv_path),
        "master_catalog_json": str(master_json_path),
        "master_catalog_csv": str(master_csv_path),
        "report": str(run_dir / "catalog_check_report.md"),
        "summary": str(run_dir / "summary.json"),
        "run_manifest": str(run_dir / "manifest.json"),
    }

    result = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "read_only",
        "overall_status": "warning" if errors else "ok",
        "summary": summary,
        "errors": errors,
        "artifacts": artifacts,
    }

    write_json(run_dir / "summary.json", result)
    write_markdown_report(
        run_dir / "catalog_check_report.md",
        run_id=run_id,
        started_at=started_at,
        summary=summary,
        errors=errors,
        artifacts=artifacts,
    )
    write_json(run_dir / "master_catalog_preview.json", [asdict(row) for row in rows[:50]])
    write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=result,
        task="catalog-fetch",
        mode="read_only",
        risk="low",
        marketplaces=["ozon", "wb"],
        inputs={},
    )
    return result
