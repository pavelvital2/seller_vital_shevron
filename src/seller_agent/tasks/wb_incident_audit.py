from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path
from typing import Any

from seller_agent.config import AppCredentials
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.http import ApiError
from seller_agent.marketplaces.wb.analytics_adapter import WbAnalyticsAdapter
from seller_agent.marketplaces.wb.documents_adapter import WbDocumentsAdapter
from seller_agent.marketplaces.wb.finance_adapter import WbFinanceAdapter
from seller_agent.marketplaces.wb.statistics_adapter import WbStatisticsAdapter
from seller_agent.reports.writer import ensure_dir, write_json
from seller_agent.tasks.liquidation_daily_control import _decimal, _integer, _read_csv, _write_csv


BASELINE_PATH = Path("data/runs/2026-07-18/wb_production_work_plan_20260718T171948/raw/wb_warehouse_stocks.json")
INCIDENT_DATE = date(2026, 7, 19)
ACCEPTED_SUPPLIES_AFTER_BASELINE = 566
ACCEPTED_SUPPLY_PHYSICAL_ITEMS = 670


def _pack_map(data_dir: Path) -> dict[int, int]:
    path = data_dir / "catalog/unified/products.csv"
    if not path.exists():
        return {}
    return {_integer(row.get("wb_nm_id")): max(_integer(row.get("pack_qty")), 1) for row in _read_csv(path) if _integer(row.get("wb_nm_id"))}


def _state_totals(rows: list[dict[str, Any]], pack_qty: dict[int, int]) -> tuple[int, int]:
    units = 0
    pieces = 0
    for row in rows:
        quantity = _integer(row.get("quantity")) + _integer(row.get("inWayToClient")) + _integer(row.get("inWayFromClient"))
        units += quantity
        pieces += quantity * pack_qty.get(_integer(row.get("nmId")), 1)
    return units, pieces


def _net_sales(rows: list[dict[str, Any]], pack_qty: dict[int, int]) -> tuple[int, int]:
    units = 0
    pieces = 0
    for row in rows:
        sign = -1 if str(row.get("saleID") or "").startswith("R") or bool(row.get("isReturn")) else 1
        units += sign
        pieces += sign * pack_qty.get(_integer(row.get("nmId")), 1)
    return units, pieces


def _compensation_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        text = " ".join(str(row.get(key) or "") for key in ("supplier_oper_name", "bonus_type_name", "deduction"))
        if "компен" in text.lower() or "возмещ" in text.lower():
            result.append(row)
    return result


def run_wb_incident_audit(*, credentials: AppCredentials, data_dir: Path = Path("data"), run_id: str | None = None) -> dict[str, Any]:
    if not credentials.wb:
        raise RuntimeError("WB credentials are required")
    started = datetime.now().astimezone()
    run_id = run_id or f"wb_incident_audit_{started:%Y%m%dT%H%M%S}"
    run_dir = ensure_dir(data_dir / "runs" / started.strftime("%Y-%m-%d") / run_id)
    baseline = __import__("json").loads(BASELINE_PATH.read_text(encoding="utf-8"))
    current = WbAnalyticsAdapter(credentials.wb).fetch_wb_warehouse_stocks()
    sales = WbStatisticsAdapter(credentials.wb).fetch_sales(date_from=INCIDENT_DATE.isoformat(), flag=0)
    finance = WbFinanceAdapter(credentials.wb).fetch_sales_report_details(date_from=INCIDENT_DATE.isoformat(), date_to=date.today().isoformat(), page_delay_seconds=61)
    documents_adapter = WbDocumentsAdapter(credentials.wb)
    documents_api_status = "ok"
    documents_api_http_status: int | None = None
    try:
        document_categories = documents_adapter.fetch_categories()
        documents = documents_adapter.fetch_documents(begin_time=INCIDENT_DATE.isoformat(), end_time=date.today().isoformat())
    except ApiError as exc:
        # A token without the Documents category must not hide the rest of the
        # read-only reconciliation. The missing source remains explicit.
        document_categories = []
        documents = []
        documents_api_status = "unavailable"
        documents_api_http_status = exc.status
    compensation_documents = [
        row
        for row in documents
        if "compensation-report" in str(row.get("serviceName") or "").lower()
        or str(row.get("name") or "").lower() == "compensation-report"
        or "возмещении убыт" in str(row.get("category") or row.get("name") or "").lower()
    ]
    pack_qty = _pack_map(data_dir)
    baseline_units, baseline_pieces = _state_totals(baseline, pack_qty)
    current_units, current_pieces = _state_totals(current, pack_qty)
    net_sales_units, net_sales_pieces = _net_sales(sales, pack_qty)
    expected_units = baseline_units + ACCEPTED_SUPPLIES_AFTER_BASELINE - net_sales_units
    expected_pieces = baseline_pieces + ACCEPTED_SUPPLY_PHYSICAL_ITEMS - net_sales_pieces
    shortage_units = max(expected_units - current_units, 0)
    shortage_pieces = max(expected_pieces - current_pieces, 0)
    compensations = _compensation_rows(finance)
    compensation_rub = sum(float(abs(_decimal(row.get("deduction") or row.get("ppvz_for_pay")))) for row in compensations)
    warehouse_rows: list[dict[str, Any]] = []
    baseline_by: dict[str, int] = {}
    current_by: dict[str, int] = {}
    for row in baseline:
        baseline_by[str(row.get("warehouseName") or "unknown")] = baseline_by.get(str(row.get("warehouseName") or "unknown"), 0) + _integer(row.get("quantity"))
    for row in current:
        current_by[str(row.get("warehouseName") or "unknown")] = current_by.get(str(row.get("warehouseName") or "unknown"), 0) + _integer(row.get("quantity"))
    for warehouse in sorted(set(baseline_by) | set(current_by)):
        warehouse_rows.append({"warehouse": warehouse, "baseline_quantity": baseline_by.get(warehouse, 0), "current_quantity": current_by.get(warehouse, 0), "raw_change_before_internal_movements": current_by.get(warehouse, 0) - baseline_by.get(warehouse, 0), "loss_confirmed": False, "note": "Per-warehouse change is not loss until WB internal movements and compensation are reconciled."})
    summary = {
        "run_id": run_id,
        "started_at": started.isoformat(timespec="seconds"),
        "overall_status": "warning",
        "mode": "read_only",
        "baseline": {"path": str(BASELINE_PATH), "marketplace_units": baseline_units, "physical_items": baseline_pieces},
        "accepted_supplies_after_baseline": {"marketplace_units": ACCEPTED_SUPPLIES_AFTER_BASELINE, "physical_items": ACCEPTED_SUPPLY_PHYSICAL_ITEMS, "source": "verified supplies 40816383, 40816239, 40816029"},
        "net_sales": {"marketplace_units": net_sales_units, "physical_items": net_sales_pieces},
        "expected_without_loss": {"marketplace_units": expected_units, "physical_items": expected_pieces},
        "current_all_states": {"marketplace_units": current_units, "physical_items": current_pieces},
        "balance_shortage_estimate": {"marketplace_units": shortage_units, "physical_items": shortage_pieces},
        "compensation": {"finance_rows": len(compensations), "amount_rub": round(compensation_rub, 2), "documents": len(compensation_documents), "documents_status": "available_for_line_item_review" if compensation_documents else ("not_published" if documents_api_status == "ok" else "source_unavailable"), "documents_api_status": documents_api_status, "documents_api_http_status": documents_api_http_status, "source": "WB financial detailed report plus Documents API compensation-report metadata"},
        "confirmed_destroyed_by_warehouse": None,
        "limitations": ["Current WB ledger may include frozen incident goods.", "Internal movements are unavailable and per-warehouse raw changes are not losses.", "Accepted supply constant covers the three owner-verified post-cutoff supplies; later supplies require reconciliation.", "Document metadata confirms publication only; destroyed quantities require downloading and parsing a compensation-report when one appears."],
        "apply_performed": False,
        "artifacts": {"report": str(run_dir / "report.md"), "summary": str(run_dir / "summary.json"), "warehouse_csv": str(run_dir / "warehouse_reconciliation.csv"), "raw_current_stocks": str(run_dir / "raw_current_stocks.json"), "raw_sales": str(run_dir / "raw_sales.json"), "raw_finance": str(run_dir / "raw_finance.json"), "raw_document_categories": str(run_dir / "raw_document_categories.json"), "raw_documents": str(run_dir / "raw_documents.json"), "compensation_documents": str(run_dir / "compensation_documents.json")},
    }
    _write_csv(run_dir / "warehouse_reconciliation.csv", warehouse_rows)
    write_json(run_dir / "raw_current_stocks.json", current)
    write_json(run_dir / "raw_sales.json", sales)
    write_json(run_dir / "raw_finance.json", finance)
    write_json(run_dir / "raw_document_categories.json", document_categories)
    write_json(run_dir / "raw_documents.json", documents)
    write_json(run_dir / "compensation_documents.json", compensation_documents)
    (run_dir / "report.md").write_text("\n".join(["# WB: контроль складских инцидентов", "", f"Балансовый дефицит: **{shortage_units} товаров / {shortage_pieces} физических изделий**.", f"Компенсационные финансовые строки: **{len(compensations)} / {compensation_rub:.2f} руб.**", f"Documents API `compensation-report`: **{len(compensation_documents)} документов**.", "", "Нулевой балансовый дефицит не доказывает отсутствие физического уничтожения: замороженные товары могут продолжать числиться. Изменения по складам не трактуются как потери без внутренних перемещений WB."]), encoding="utf-8")
    write_json(run_dir / "summary.json", summary)
    summary["artifacts"].update(write_summary_run_manifest(data_dir=data_dir, run_dir=run_dir, summary=summary, task="wb-incident-audit", mode="read_only", risk="low", marketplaces=["wb"], inputs={"incident_date": INCIDENT_DATE.isoformat(), "baseline_path": str(BASELINE_PATH)}, lifecycle_status="closed", closed=True))
    write_json(run_dir / "summary.json", summary)
    return summary
