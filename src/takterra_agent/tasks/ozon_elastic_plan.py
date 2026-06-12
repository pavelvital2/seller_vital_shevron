from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Any

from openpyxl import Workbook

from takterra_agent.config import AppCredentials
from takterra_agent.marketplaces.ozon.adapter import OzonSellerAdapter
from takterra_agent.reports.writer import ensure_dir, write_json


READ_PAGE_SIZE = 100
ELASTIC_ACTION_TYPE = "MARKETPLACE_MULTI_LEVEL_DISCOUNT_ON_AMOUNT"
ELASTIC_TITLE_MARKER = "Эластичный бустинг"

ACTIVE = "active"
CANDIDATE = "candidate"
COLLISION = "candidate_and_active"
DEACTIVATE_REASONS = {
    "missing_min_price",
    "no_stock",
    "no_boost_prices",
    "below_min_price_threshold",
    "insufficient_ozon_input_data",
}


def _first(row: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    for alias in aliases:
        if alias in row and row[alias] not in (None, ""):
            return row[alias]
    return None


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(" ", "").replace("\u00a0", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


def _json_decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.normalize(), "f")


def _product_id(row: dict[str, Any]) -> str:
    return str(_first(row, ("product_id", "id", "sku")) or "").strip()


def _response_rows(response: dict[str, Any], key: str = "products") -> tuple[list[dict[str, Any]], int | None, str]:
    result = response.get("result") or {}
    rows = result.get(key) or result.get("items") or result.get("list") or []
    if not isinstance(rows, list):
        rows = []
    total = result.get("total")
    last_id = str(result.get("last_id") or "")
    return [row for row in rows if isinstance(row, dict)], int(total) if total is not None else None, last_id


def _safe_action(action: dict[str, Any]) -> dict[str, Any]:
    return {
        "action_id": str(action.get("id") or action.get("action_id") or "").strip(),
        "title": str(action.get("title") or action.get("name") or "").strip(),
        "action_type": str(action.get("action_type") or "").strip(),
        "status": action.get("status"),
        "date_start": action.get("date_start") or action.get("start_date"),
        "date_end": action.get("date_end") or action.get("end_date"),
        "active_products_count": action.get("active_products_count"),
        "candidates_count": action.get("candidates_count"),
    }


def _is_elastic(action: dict[str, Any]) -> bool:
    return (
        action.get("action_type") == ELASTIC_ACTION_TYPE
        and ELASTIC_TITLE_MARKER in str(action.get("title") or action.get("name") or "")
    )


def _fetch_actions(ozon: OzonSellerAdapter, raw_dir: Path) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    offset = 0
    page_no = 1
    while True:
        data = ozon.get("/v1/actions", {"limit": READ_PAGE_SIZE, "offset": offset})
        write_json(raw_dir / f"ozon_actions_page_{page_no:03d}.json", data)
        result = data.get("result")
        if isinstance(result, list):
            page_rows = result
        elif isinstance(result, dict):
            page_rows = result.get("actions") or result.get("items") or result.get("list") or []
        else:
            page_rows = []
        page_rows = [row for row in page_rows if isinstance(row, dict)]
        actions.extend(page_rows)
        if len(page_rows) < READ_PAGE_SIZE:
            break
        offset += READ_PAGE_SIZE
        page_no += 1
    return actions


def _pick_elastic_action(actions: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = [_safe_action(action) for action in actions if _is_elastic(action)]
    if not candidates:
        raise RuntimeError("Ozon elastic action not found")
    active_statuses = {"active", "running", "started", "in_progress", "enabled"}
    active = [row for row in candidates if str(row.get("status") or "").lower() in active_statuses]
    return sorted(active or candidates, key=lambda row: str(row.get("date_start") or ""), reverse=True)[0]


def _fetch_action_group(
    ozon: OzonSellerAdapter,
    *,
    action_id: str,
    source_group: str,
    raw_dir: Path,
) -> list[dict[str, Any]]:
    path = "/v1/actions/products" if source_group == ACTIVE else "/v1/actions/candidates"
    prefix = "active" if source_group == ACTIVE else "candidate"
    rows: list[dict[str, Any]] = []
    offset = 0
    last_id = ""
    page_no = 1
    while True:
        payload = {"action_id": str(action_id), "limit": READ_PAGE_SIZE, "offset": offset}
        if last_id:
            payload["last_id"] = last_id
        data = ozon.post(path, payload)
        write_json(raw_dir / f"ozon_elastic_{prefix}_page_{page_no:03d}.json", data)
        page_rows, total, next_last_id = _response_rows(data)
        for row in page_rows:
            product_id = _product_id(row)
            if not product_id:
                continue
            rows.append(
                {
                    "source_group": source_group,
                    "product_id": product_id,
                    "offer_id": _first(row, ("offer_id", "offer")),
                    "name": _first(row, ("name", "title")),
                    "current_action_price": _first(row, ("action_price", "current_action_price")),
                    "price_min_elastic": _first(row, ("price_min_elastic", "min_action_price", "min_boost_price", "min_price_for_action")),
                    "price_max_elastic": _first(row, ("price_max_elastic", "max_action_price", "max_boost_price", "max_price_for_action")),
                }
            )
        if total is not None and len(rows) >= total:
            break
        if total is None and len(page_rows) < READ_PAGE_SIZE:
            break
        if next_last_id:
            if next_last_id == last_id:
                break
            last_id = next_last_id
        else:
            offset += READ_PAGE_SIZE
        page_no += 1
    return rows


def _merge_sources(active_rows: list[dict[str, Any]], candidate_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for rows, group in ((active_rows, ACTIVE), (candidate_rows, CANDIDATE)):
        for row in rows:
            product_id = str(row.get("product_id") or "")
            if not product_id:
                continue
            if product_id not in merged:
                merged[product_id] = {**row, "source_groups": [group]}
                continue
            existing = merged[product_id]
            existing["source_group"] = COLLISION
            existing["source_groups"] = list(dict.fromkeys([*existing.get("source_groups", []), group]))
            if group == ACTIVE:
                existing["active_row"] = row
            else:
                existing["candidate_row"] = row
    return list(merged.values())


def _stock_present(stock_item: dict[str, Any] | None) -> Decimal | None:
    if not stock_item:
        return None
    rows = stock_item.get("stocks") or stock_item.get("stock") or []
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        return None
    total = Decimal("0")
    found = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        present = _decimal(_first(row, ("present",)))
        if present is None:
            continue
        total += present
        found = True
    return total if found else None


def _reason_message(code: str) -> str:
    return {
        "missing_min_price": "Minimum allowed price is missing.",
        "no_stock": "Stock is missing or non-positive.",
        "no_boost_prices": "Both boost prices are missing.",
        "use_max_boost_price": "Max boost price is used as final promo price.",
        "use_min_price": "Minimum allowed price is used as final promo price.",
        "below_min_price_threshold": "Minimum boost price is below minimum allowed price.",
        "insufficient_ozon_input_data": "Input data is insufficient for Ozon decision rules.",
    }[code]


@dataclass(frozen=True)
class Decision:
    reason_code: str
    final_price: Decimal | None

    @property
    def participates(self) -> bool:
        return self.final_price is not None


def _decide(*, min_price: Decimal | None, min_boost: Decimal | None, max_boost: Decimal | None, stock: Decimal | None) -> Decision:
    if min_price is None:
        return Decision("missing_min_price", None)
    if stock is None or stock <= 0:
        return Decision("no_stock", None)
    if min_boost is None and max_boost is None:
        return Decision("no_boost_prices", None)
    if max_boost is not None and max_boost >= min_price:
        return Decision("use_max_boost_price", max_boost)
    if max_boost is not None and min_boost is not None and max_boost < min_price <= min_boost:
        return Decision("use_min_price", min_price)
    if min_boost is not None and min_boost < min_price:
        return Decision("below_min_price_threshold", None)
    return Decision("insufficient_ozon_input_data", None)


def _planned_action(source_group: str, decision: Decision) -> tuple[str, bool]:
    if source_group == CANDIDATE:
        return ("add_to_action", False) if decision.participates else ("skip_candidate", False)
    if source_group in {ACTIVE, COLLISION}:
        if decision.participates:
            return "update_action_price", False
        if decision.reason_code in DEACTIVATE_REASONS:
            return "deactivate_from_action", True
    return "blocked", False


def _write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    headers = [
        "planned_action",
        "product_id",
        "offer_id",
        "name",
        "current_action_price",
        "calculated_action_price",
        "J_min_price",
        "O_price_min_elastic",
        "P_price_max_elastic",
        "R_stock_present",
        "source_group",
        "reason_code",
        "deactivate_required",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_xlsx(rows: list[dict[str, Any]], path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Ozon Elastic Dry Run"
    headers = list(rows[0].keys()) if rows else ["planned_action"]
    sheet.append(headers)
    for row in rows:
        sheet.append([row.get(header, "") for header in headers])
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for column in sheet.columns:
        max_len = max(len(str(cell.value or "")) for cell in column)
        sheet.column_dimensions[column[0].column_letter].width = min(max(max_len + 2, 12), 70)
    workbook.save(path)


def _write_report(path: Path, *, result: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    summary = result["summary"]
    lines = [
        "# Ozon Elastic Boosting Dry Run",
        "",
        "Mode: read-only/dry-run. No changes uploaded to Ozon.",
        "",
        "## Action",
        "",
        f"- `action_id`: `{summary.get('action_id')}`",
        f"- `action_name`: {summary.get('action_name')}",
        "",
        "## Summary",
        "",
    ]
    for key in (
        "active_rows",
        "candidate_rows",
        "merged_unique_products",
        "add_to_action",
        "update_action_price",
        "update_action_price_with_changed_price",
        "deactivate_from_action",
        "skip_candidate",
        "blocked",
    ):
        lines.append(f"- `{key}`: {summary.get(key)}")

    lines.extend(["", "## Reason Codes", ""])
    reason_counts: dict[str, int] = {}
    for row in rows:
        reason = str(row.get("reason_code") or "")
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
    for reason, count in sorted(reason_counts.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"- `{reason}`: {count}")

    lines.extend(["", "## Artifacts", ""])
    for key, value in sorted(result["artifacts"].items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "Apply is forbidden without explicit owner approval and drift-check.", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def run_ozon_elastic_plan(
    *,
    credentials: AppCredentials,
    data_dir: Path = Path("data"),
    run_id: str | None = None,
) -> dict[str, Any]:
    if not credentials.ozon_seller:
        raise RuntimeError("missing Ozon Seller API credentials")

    started_at = datetime.now()
    run_id = run_id or f"ozon_elastic_plan_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    raw_dir = ensure_dir(run_dir / "raw")
    processed_dir = ensure_dir(run_dir / "processed")

    ozon = OzonSellerAdapter(credentials.ozon_seller)
    actions = _fetch_actions(ozon, raw_dir)
    selected_action = _pick_elastic_action(actions)
    write_json(processed_dir / "selected_elastic_action.json", selected_action)
    write_json(processed_dir / "actions_safe_snapshot.json", [_safe_action(action) for action in actions])

    action_id = str(selected_action["action_id"])
    action_name = str(selected_action.get("title") or "")
    active_rows = _fetch_action_group(ozon, action_id=action_id, source_group=ACTIVE, raw_dir=raw_dir)
    candidate_rows = _fetch_action_group(ozon, action_id=action_id, source_group=CANDIDATE, raw_dir=raw_dir)
    source_rows = _merge_sources(active_rows, candidate_rows)
    write_json(processed_dir / "source_rows.json", source_rows)

    product_ids = [str(row["product_id"]) for row in source_rows if row.get("product_id")]
    product_info = {str(row.get("id") or row.get("product_id") or ""): row for row in ozon.fetch_product_info(product_ids)}
    stocks = {str(row.get("product_id") or row.get("id") or ""): row for row in ozon.fetch_product_stocks(product_ids)}
    write_json(processed_dir / "product_info_safe_snapshot.json", product_info)
    write_json(processed_dir / "stocks_safe_snapshot.json", stocks)

    rows: list[dict[str, Any]] = []
    for source in source_rows:
        product_id = str(source.get("product_id") or "")
        info = product_info.get(product_id, {})
        stock_item = stocks.get(product_id, {})
        min_price = _decimal(info.get("min_price"))
        min_boost = _decimal(source.get("price_min_elastic"))
        max_boost = _decimal(source.get("price_max_elastic"))
        stock = _stock_present(stock_item)
        decision = _decide(min_price=min_price, min_boost=min_boost, max_boost=max_boost, stock=stock)
        planned_action, deactivate_required = _planned_action(str(source.get("source_group") or ""), decision)
        current_action_price = source.get("current_action_price")
        calculated_action_price = _json_decimal(decision.final_price)
        rows.append(
            {
                "marketplace": "OZON",
                "store": "Vital Shevron",
                "action_id": action_id,
                "action_name": action_name,
                "source_group": source.get("source_group"),
                "product_id": product_id,
                "offer_id": info.get("offer_id") or source.get("offer_id") or "",
                "name": info.get("name") or source.get("name") or "",
                "current_action_price": current_action_price or "",
                "calculated_action_price": calculated_action_price or "",
                "J_min_price": _json_decimal(min_price) or "",
                "O_price_min_elastic": _json_decimal(min_boost) or "",
                "P_price_max_elastic": _json_decimal(max_boost) or "",
                "R_stock_present": _json_decimal(stock) or "",
                "planned_action": planned_action,
                "reason_code": decision.reason_code,
                "reason": _reason_message(decision.reason_code),
                "upload_ready": decision.participates,
                "deactivate_required": deactivate_required,
            }
        )

    groups: dict[str, list[dict[str, Any]]] = {
        "add_to_action": [],
        "update_action_price": [],
        "deactivate_from_action": [],
        "skip_candidate": [],
        "blocked": [],
    }
    for row in rows:
        groups.setdefault(str(row["planned_action"]), []).append(row)
    price_changes = [
        row
        for row in groups.get("update_action_price", [])
        if str(row.get("current_action_price") or "") != str(row.get("calculated_action_price") or "")
    ]

    payload_preview = {
        "dry_run": True,
        "action_id": action_id,
        "activate_endpoint": "/v1/actions/products/activate",
        "deactivate_endpoint": "/v1/actions/products/deactivate",
        "add_update_products": [
            {
                "product_id": row["product_id"],
                "offer_id": row["offer_id"],
                "action_price": row["calculated_action_price"],
                "planned_action": row["planned_action"],
            }
            for row in rows
            if row["planned_action"] in {"add_to_action", "update_action_price"}
        ],
        "deactivate_products": [
            {"product_id": row["product_id"], "offer_id": row["offer_id"], "reason_code": row["reason_code"]}
            for row in rows
            if row["planned_action"] == "deactivate_from_action"
        ],
        "note": "Preview only. Do not upload without explicit owner confirmation and drift-check.",
    }

    csv_path = run_dir / "ozon_elastic_dry_run.csv"
    xlsx_path = run_dir / "ozon_elastic_dry_run.xlsx"
    report_path = run_dir / "ozon_elastic_dry_run.md"
    payload_path = run_dir / "ozon_elastic_upload_payload_preview.json"
    write_json(processed_dir / "calculation_rows.json", rows)
    _write_csv(rows, csv_path)
    _write_xlsx(rows, xlsx_path)
    write_json(payload_path, payload_preview)

    summary = {
        "action_id": action_id,
        "action_name": action_name,
        "active_rows": len(active_rows),
        "candidate_rows": len(candidate_rows),
        "merged_unique_products": len(source_rows),
        "add_to_action": len(groups.get("add_to_action", [])),
        "update_action_price": len(groups.get("update_action_price", [])),
        "update_action_price_with_changed_price": len(price_changes),
        "deactivate_from_action": len(groups.get("deactivate_from_action", [])),
        "skip_candidate": len(groups.get("skip_candidate", [])),
        "blocked": len(groups.get("blocked", [])),
    }
    artifacts = {
        "run_dir": str(run_dir),
        "report": str(report_path),
        "csv": str(csv_path),
        "xlsx": str(xlsx_path),
        "payload_preview": str(payload_path),
        "summary": str(run_dir / "summary.json"),
    }
    result = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "dry-run",
        "summary": summary,
        "artifacts": artifacts,
        "apply_performed": False,
    }
    write_json(run_dir / "summary.json", result)
    _write_report(report_path, result=result, rows=rows)
    return result
