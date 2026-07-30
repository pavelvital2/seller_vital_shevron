from __future__ import annotations

from collections import Counter
import csv
from datetime import date, datetime, timedelta
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from seller_agent.config import AppCredentials
from seller_agent.core.run_manifest import manifest_from_summary, write_run_manifest
from seller_agent.marketplaces.ozon.adapter import OzonSellerAdapter
from seller_agent.marketplaces.wb.analytics_adapter import WbAnalyticsAdapter
from seller_agent.marketplaces.wb.communications_adapter import WbCommunicationsAdapter
from seller_agent.marketplaces.wb.fbw_supplies_adapter import WbFbwSuppliesAdapter
from seller_agent.marketplaces.wb.finance_adapter import WbFinanceAdapter
from seller_agent.marketplaces.wb.promotion_adapter import WbPromotionAdapter
from seller_agent.marketplaces.wb.statistics_adapter import WbStatisticsAdapter
from seller_agent.reports.writer import ensure_dir, write_json
from seller_agent.sessions.state import combined_session_status
from seller_agent.tasks.approvals import run_approvals_status
from seller_agent.tasks.reviews_questions import _run_ozon_lk_fallback
from seller_agent.tasks.status_preflight import run_status_preflight
from seller_agent.tasks.wb_actions_discount_plan import wb_actions_report_stats


RUN_PREFIXES = [
    "status_preflight_",
    "sessions_status_",
    "catalog_fetch_",
    "actions_apply_",
    "actions_review_",
    "ozon_elastic_plan_",
    "wb_actions_discount_plan_",
    "wb_card_create_plan_",
    "wb_card_create_apply_",
    "restore_ozon_session_",
]

ACTIVE_OZON_SUPPLY_STATES = [
    "DATA_FILLING",
    "READY_TO_SUPPLY",
    "ACCEPTED_AT_SUPPLY_WAREHOUSE",
    "IN_TRANSIT",
    "ACCEPTANCE_AT_STORAGE_WAREHOUSE",
    "REPORTS_CONFIRMATION_AWAITING",
]
ACTIVE_WB_SUPPLY_STATUS_IDS = {1, 2, 3, 4, 6}
TARGET_PRODUCT_GROUPS = {"chev", "nash", "loop"}


def _safe_read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _safe_read_csv(path: Path, *, delimiter: str = ",") -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as file:
            return [dict(row) for row in csv.DictReader(file, delimiter=delimiter)]
    except (OSError, csv.Error):
        return []


def _safe_error(exc: BaseException) -> str:
    raw = str(exc).replace("\n", " ").replace("\r", " ")
    if "failed (429)" in raw or '"status": 429' in raw:
        return "HTTP 429: лимит запросов / слишком много запросов"
    if "failed (401)" in raw or '"status": 401' in raw:
        return "HTTP 401: нет авторизации"
    if "failed (403)" in raw or '"status": 403' in raw:
        return "HTTP 403: доступ запрещен"
    return raw[:800]


def _run_dirs(data_dir: Path) -> list[Path]:
    runs_dir = data_dir / "runs"
    if not runs_dir.exists():
        return []
    return sorted(
        (path for path in runs_dir.glob("*/*") if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def latest_run_dirs(data_dir: Path, *, prefixes: list[str] | None = None, limit: int = 20) -> list[dict[str, Any]]:
    selected_prefixes = prefixes or RUN_PREFIXES
    rows: list[dict[str, Any]] = []
    for run_dir in _run_dirs(data_dir):
        run_id = run_dir.name
        if not any(run_id.startswith(prefix) for prefix in selected_prefixes):
            continue
        summary_path = run_dir / "summary.json"
        summary = _safe_read_json(summary_path) if summary_path.exists() else None
        rows.append(
            {
                "run_id": run_id,
                "path": str(run_dir),
                "summary_path": str(summary_path) if summary_path.exists() else "",
                "started_at": summary.get("started_at") if isinstance(summary, dict) else "",
                "overall_status": summary.get("overall_status") if isinstance(summary, dict) else "",
                "mode": summary.get("mode") if isinstance(summary, dict) else "",
            }
        )
        if len(rows) >= limit:
            break
    return rows


def latest_run(data_dir: Path, prefix: str) -> dict[str, Any] | None:
    rows = latest_run_dirs(data_dir, prefixes=[prefix], limit=1)
    if not rows:
        return None
    row = rows[0]
    summary_path = Path(row["summary_path"]) if row["summary_path"] else None
    summary = _safe_read_json(summary_path) if summary_path else None
    row["summary"] = summary if isinstance(summary, dict) else {}
    return row


def _latest_preflight(
    *,
    credentials: AppCredentials,
    data_dir: Path,
    refresh_preflight: bool,
) -> dict[str, Any] | None:
    if refresh_preflight:
        return run_status_preflight(credentials=credentials, data_dir=data_dir)
    latest = latest_run(data_dir, "status_preflight_")
    if not latest:
        return None
    summary = latest.get("summary")
    return summary if isinstance(summary, dict) else None


def _pending_packages(data_dir: Path) -> list[dict[str, Any]]:
    status = run_approvals_status(data_dir=data_dir, include_closed=False, limit=10000)
    return [
        {
            "pending_id": str(row.get("id") or ""),
            "kind": str(row.get("kind") or ""),
            "path": str(row.get("path") or ""),
            "manifest": str(row.get("manifest") or ""),
            "status": str(row.get("lifecycle_status") or "unknown"),
            "created_at": str(row.get("created_at") or ""),
            "applied_run_id": str(row.get("apply_run_id") or ""),
        }
        for row in status.get("rows", [])
    ]


def _recommendations_summary(data_dir: Path) -> dict[str, Any]:
    path = data_dir / "planning" / "recommendations_index.md"
    if not path.exists():
        return {"path": str(path), "status_counts": {}, "open_items": []}

    counts: Counter[str] = Counter()
    open_items: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not (line.startswith("| REC-") or line.startswith("| VS-REC-")):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 5:
            continue
        rec_id, topic, status, details, next_step = cells[:5]
        counts[status] += 1
        if status in {"proposed", "accepted", "in_progress"}:
            open_items.append(
                {
                    "id": rec_id,
                    "topic": topic,
                    "status": status,
                    "details": details,
                    "next_step": next_step,
                }
            )
    return {"path": str(path), "status_counts": dict(sorted(counts.items())), "open_items": open_items}


def _catalog_section(preflight: dict[str, Any] | None) -> dict[str, Any]:
    checks = preflight.get("checks", {}) if isinstance(preflight, dict) else {}
    catalog = checks.get("master_catalog", {}) if isinstance(checks, dict) else {}
    return catalog if isinstance(catalog, dict) else {}


def _unified_catalog_rows(data_dir: Path) -> list[dict[str, Any]]:
    rows = _safe_read_json(data_dir / "catalog" / "unified" / "products.json")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _unified_catalog_section(data_dir: Path) -> dict[str, Any]:
    path = data_dir / "catalog" / "unified" / "products.json"
    if not path.exists():
        return {
            "status": "missing",
            "path": str(path),
            "error": "products.json missing",
        }

    rows = _unified_catalog_rows(data_dir)
    if not rows:
        return {
            "status": "error",
            "path": str(path),
            "error": "unified catalog is empty or not a list",
        }

    mapping_counts = Counter(str(row.get("mapping_status") or "unknown").strip() or "unknown" for row in rows)
    active_ozon = sum(1 for row in rows if _truthy(row.get("active_ozon")))
    active_wb = sum(1 for row in rows if _truthy(row.get("active_wb")))
    with_internal_sku = sum(1 for row in rows if str(row.get("internal_sku") or "").strip())
    target_rows = [
        row
        for row in rows
        if str(row.get("product_group") or "").strip().lower() in TARGET_PRODUCT_GROUPS
    ]
    target_with_internal_sku = sum(
        1 for row in target_rows if str(row.get("internal_sku") or "").strip()
    )
    both_marketplaces = sum(
        1 for row in rows if _truthy(row.get("active_ozon")) and _truthy(row.get("active_wb"))
    )
    stat = path.stat()
    return {
        "status": "ok",
        "path": str(path),
        "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        "products": len(rows),
        "with_internal_sku": with_internal_sku,
        "target_products": len(target_rows),
        "target_with_internal_sku": target_with_internal_sku,
        "target_identification_complete": bool(target_rows) and target_with_internal_sku == len(target_rows),
        "non_target_or_deferred_products": len(rows) - len(target_rows),
        # Compatibility only. Do not use this legacy status as mapping completeness.
        "confirmed_products": mapping_counts.get("confirmed", 0),
        "legacy_confirmed_status_products": mapping_counts.get("confirmed", 0),
        "ozon_only_products": mapping_counts.get("ozon_only", 0),
        "wb_only_products": mapping_counts.get("wb_only", 0),
        "mapping_status_counts": dict(sorted(mapping_counts.items())),
        "active_ozon_products": active_ozon,
        "active_wb_products": active_wb,
        "both_marketplaces_products": both_marketplaces,
        "active_ozon_only_products": active_ozon - both_marketplaces,
        "active_wb_only_products": active_wb - both_marketplaces,
    }


def _master_catalog_rows(data_dir: Path) -> list[dict[str, Any]]:
    rows = _safe_read_json(data_dir / "catalog" / "processed" / "master_catalog.json")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _catalog_title(row: dict[str, Any]) -> str:
    return str(row.get("title") or row.get("name") or row.get("product_name") or "").strip()


def _catalog_internal_sku(row: dict[str, Any]) -> str:
    return str(row.get("internal_sku") or row.get("master_sku") or row.get("internal_product_id") or "").strip()


def _first_number(source: dict[str, Any], keys: tuple[str, ...]) -> float:
    for key in keys:
        value = source.get(key)
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "да"}


def _money(value: float | int | None) -> float:
    if value is None:
        return 0.0
    return round(float(value), 2)


def _format_money(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "н/д"
    return f"{number:,.0f}".replace(",", " ") + " ₽"


def _format_money_precise(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "н/д"
    whole, decimals = f"{number:,.2f}".split(".")
    return f"{whole.replace(',', ' ')},{decimals} ₽"


def _format_int(value: Any) -> str:
    if value is None:
        return "н/д"
    try:
        return f"{int(float(value)):,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(value)


def _not_confirmed(reason: str, source: str = "") -> dict[str, Any]:
    result = {"status": "not_confirmed", "reason": reason}
    if source:
        result["source"] = source
    return result


def _source_error(source: str, exc: BaseException) -> dict[str, Any]:
    return {"status": "error", "source": source, "error": _safe_error(exc)}


def _moscow_today() -> date:
    return datetime.now(ZoneInfo("Europe/Moscow")).date()


def _day_bounds_msk(day: str) -> tuple[str, str]:
    return f"{day}T00:00:00+03:00", f"{day}T23:59:59+03:00"


def _day_bounds_utc(day: str) -> tuple[str, str]:
    return f"{day}T00:00:00Z", f"{day}T23:59:59Z"


def _row_date_value(row: dict[str, Any]) -> str:
    for key in (
        "date",
        "createdDate",
        "created_at",
        "createdAt",
        "lastChangeDate",
        "last_change_date",
        "updatedAt",
        "published_at",
        "publishedAt",
    ):
        value = row.get(key)
        if value:
            return str(value)
    return ""


def _filter_rows_for_day(rows: list[dict[str, Any]], day: str) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    rows_without_dates: list[dict[str, Any]] = []
    for row in rows:
        row_date = _row_date_value(row)
        if not row_date:
            rows_without_dates.append(row)
            continue
        if row_date[:10] == day:
            filtered.append(row)
    if filtered or not rows_without_dates:
        return filtered
    return rows_without_dates


def _extract_ozon_analytics(response: dict[str, Any], metrics: list[str]) -> dict[str, Any]:
    result = response.get("result") if isinstance(response, dict) else {}
    result = result if isinstance(result, dict) else {}
    totals = result.get("totals")
    values: dict[str, float] = {}

    if isinstance(totals, list):
        for index, metric in enumerate(metrics):
            if index < len(totals):
                try:
                    values[metric] = float(totals[index])
                except (TypeError, ValueError):
                    values[metric] = 0.0

    if not values:
        for row in result.get("data") or []:
            if not isinstance(row, dict) or not isinstance(row.get("metrics"), list):
                continue
            for index, metric in enumerate(metrics):
                if index >= len(row["metrics"]):
                    continue
                try:
                    values[metric] = values.get(metric, 0.0) + float(row["metrics"][index])
                except (TypeError, ValueError):
                    values.setdefault(metric, 0.0)

    return {
        "revenue": _money(values.get("revenue", 0.0)),
        "ordered_units": int(values.get("ordered_units", 0.0)),
        "rows": len(result.get("data") or []) if isinstance(result.get("data"), list) else 0,
    }


def _ozon_stock_qty(item: dict[str, Any]) -> tuple[float, float]:
    stocks = item.get("stocks")
    present = 0.0
    reserved = 0.0
    if isinstance(stocks, list):
        for stock in stocks:
            if not isinstance(stock, dict):
                continue
            present += _first_number(
                stock,
                ("present", "stock", "quantity", "available_stock_count", "valid_stock_count"),
            )
            reserved += _first_number(stock, ("reserved", "reserved_stock_count"))
        return present, reserved

    present = _first_number(item, ("present", "stock", "quantity", "available_stock_count", "valid_stock_count"))
    reserved = _first_number(item, ("reserved", "reserved_stock_count"))
    return present, reserved


def _summarize_ozon_stocks(
    *,
    stock_items: list[dict[str, Any]],
    catalog_rows: list[dict[str, Any]],
    low_stock_threshold: int = 3,
) -> dict[str, Any]:
    product_to_catalog = {str(row.get("ozon_product_id")): row for row in catalog_rows if row.get("ozon_product_id")}
    offer_to_catalog = {str(row.get("ozon_offer_id")): row for row in catalog_rows if row.get("ozon_offer_id")}
    low_stock_sample: list[dict[str, Any]] = []
    out_of_stock_count = 0
    low_stock_count = 0
    present_total = 0.0
    reserved_total = 0.0

    for item in stock_items:
        product_id = str(item.get("product_id") or item.get("id") or "")
        offer_id = str(item.get("offer_id") or item.get("offerId") or "")
        present, reserved = _ozon_stock_qty(item)
        present_total += present
        reserved_total += reserved
        if present <= 0:
            out_of_stock_count += 1
        elif present <= low_stock_threshold:
            low_stock_count += 1
            catalog_row = product_to_catalog.get(product_id) or offer_to_catalog.get(offer_id) or {}
            low_stock_sample.append(
                {
                    "sku": offer_id or catalog_row.get("ozon_offer_id") or catalog_row.get("master_sku") or product_id,
                    "internal_sku": _catalog_internal_sku(catalog_row),
                    "title": _catalog_title(catalog_row),
                    "present": int(present),
                }
            )

    low_stock_sample = sorted(low_stock_sample, key=lambda row: (row["present"], row["sku"]))[:10]
    return {
        "status": "ok",
        "source": "/v4/product/info/stocks",
        "products_checked": len(stock_items),
        "present_total": int(present_total),
        "reserved_total": int(reserved_total),
        "out_of_stock_count": out_of_stock_count,
        "low_stock_threshold": low_stock_threshold,
        "low_stock_count": low_stock_count,
        "low_stock_sample": low_stock_sample,
    }


def _ozon_posting_products(row: dict[str, Any]) -> list[dict[str, Any]]:
    products = row.get("products")
    if isinstance(products, list):
        return [item for item in products if isinstance(item, dict)]
    financial_data = row.get("financial_data") if isinstance(row.get("financial_data"), dict) else {}
    financial_products = financial_data.get("products") if isinstance(financial_data, dict) else []
    return [item for item in financial_products if isinstance(item, dict)] if isinstance(financial_products, list) else []


def _ozon_posting_units_and_amount(rows: list[dict[str, Any]]) -> tuple[int, float]:
    units = 0
    amount = 0.0
    for row in rows:
        for product in _ozon_posting_products(row):
            quantity = int(_first_number(product, ("quantity", "qty", "count")) or 0)
            price = _first_number(product, ("price", "client_price", "old_price"))
            units += quantity
            amount += quantity * price
    return units, _money(amount)


def _summarize_ozon_fbo_postings(rows: list[dict[str, Any]]) -> dict[str, Any]:
    cancelled_rows: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()

    for row in rows:
        status = str(row.get("status") or "").strip().lower()
        status_counts[status or "unknown"] += 1
        if "cancel" in status:
            cancelled_rows.append(row)

    cancelled_units, cancelled_amount = _ozon_posting_units_and_amount(cancelled_rows)
    return {
        "status": "warning",
        "source": "/v2/posting/fbo/list",
        "source_note": (
            "Ozon FBO отмены рассчитаны по отправлениям FBO, которые API вернул за период отчета; "
            "для строгой отмены по дате отмены нужен отдельный источник."
        ),
        "postings_total": len(rows),
        "status_counts": dict(sorted(status_counts.items())),
        "cancelled_postings": len(cancelled_rows),
        "cancelled_units": cancelled_units,
        "cancelled_amount": cancelled_amount,
    }


def _summarize_ozon_finance_buyouts(
    *,
    operations: list[dict[str, Any]],
    day: str,
) -> dict[str, Any]:
    delivered_rows = [
        row
        for row in operations
        if str(row.get("operation_type") or "") == "OperationAgentDeliveredToCustomer"
        and str(row.get("operation_date") or "")[:10] == day
    ]
    units = 0
    gross_amount = 0.0
    net_amount = 0.0
    top_skus: Counter[str] = Counter()
    for row in delivered_rows:
        items = row.get("items") if isinstance(row.get("items"), list) else []
        item_units = len(items) or 1
        units += item_units
        gross_amount += _first_number(row, ("accruals_for_sale",))
        net_amount += _first_number(row, ("amount",))
        for item in items:
            if not isinstance(item, dict):
                continue
            sku = str(item.get("sku") or "").strip()
            if sku:
                top_skus[sku] += 1

    return {
        "status": "ok",
        "source": "/v3/finance/transaction/list",
        "source_note": "Ozon выкупы рассчитаны по финансовым операциям `OperationAgentDeliveredToCustomer` за дату операции.",
        "operation_type": "OperationAgentDeliveredToCustomer",
        "operations_checked": len(operations),
        "buyout_operations": len(delivered_rows),
        "buyout_units": units,
        "buyout_amount": _money(gross_amount),
        "net_amount": _money(net_amount),
        "top_skus": [{"sku": sku, "units": count} for sku, count in top_skus.most_common(5)],
    }


def _positive_expense(value: float | int) -> float:
    return max(0.0, -float(value))


def _ozon_operation_amount(row: dict[str, Any]) -> float:
    return _first_number(row, ("amount",))


def _ozon_services_amount(row: dict[str, Any]) -> float:
    services = row.get("services") if isinstance(row.get("services"), list) else []
    return sum(_first_number(service, ("price",)) for service in services if isinstance(service, dict))


def _summarize_ozon_finance_expenses(
    *,
    operations: list[dict[str, Any]],
    day: str,
) -> dict[str, Any]:
    rows = [
        row
        for row in operations
        if str(row.get("operation_date") or "")[:10] == day
    ]
    gross_amount = sum(_first_number(row, ("accruals_for_sale",)) for row in rows)
    net_amount = sum(_ozon_operation_amount(row) for row in rows)
    commission = _positive_expense(sum(_first_number(row, ("sale_commission",)) for row in rows))

    expenses = {
        "commission": commission,
        "logistics": 0.0,
        "returns_processing": 0.0,
        "acquiring": 0.0,
        "advertising": 0.0,
        "storage": 0.0,
        "crossdocking": 0.0,
        "stars_membership": 0.0,
        "other": 0.0,
    }
    operation_counts: Counter[str] = Counter()
    for row in rows:
        operation_type = str(row.get("operation_type") or "")
        operation_counts[operation_type or "unknown"] += 1
        amount = _ozon_operation_amount(row)
        services_amount = _ozon_services_amount(row)
        service_expense = _positive_expense(services_amount)
        amount_expense = _positive_expense(amount)

        if operation_type == "OperationAgentDeliveredToCustomer":
            expenses["logistics"] += service_expense
        elif operation_type == "OperationItemReturn":
            expenses["returns_processing"] += amount_expense
        elif operation_type == "MarketplaceRedistributionOfAcquiringOperation":
            expenses["acquiring"] += amount_expense
        elif operation_type == "OperationMarketplaceCostPerClick":
            expenses["advertising"] += amount_expense
        elif operation_type == "OperationMarketplaceServiceStorage":
            expenses["storage"] += amount_expense
        elif operation_type == "MarketplaceServiceItemCrossdocking":
            expenses["crossdocking"] += amount_expense
        elif operation_type == "StarsMembership":
            expenses["stars_membership"] += amount_expense
        elif amount < 0 or services_amount < 0:
            expenses["other"] += max(amount_expense, service_expense)

    total_expenses = max(0.0, gross_amount - net_amount)
    known_expenses = sum(expenses.values())
    reconciliation_delta = total_expenses - known_expenses
    if abs(reconciliation_delta) >= 0.01:
        expenses["other"] += reconciliation_delta

    return {
        "status": "ok",
        "source": "/v3/finance/transaction/list",
        "source_note": "Ozon расходы рассчитаны по всем финансовым операциям за дату операции; реклама CPC входит отдельной операцией.",
        "operations_checked": len(operations),
        "operations_in_day": len(rows),
        "operation_counts": dict(sorted(operation_counts.items())),
        "gross_amount": _money(gross_amount),
        "net_amount": _money(net_amount),
        "total_expenses": _money(total_expenses),
        "expenses": {key: _money(value) for key, value in expenses.items()},
    }


def _summarize_wb_orders(rows: list[dict[str, Any]]) -> dict[str, Any]:
    active_rows = [row for row in rows if not _truthy(row.get("isCancel"))]
    cancelled_rows = [row for row in rows if _truthy(row.get("isCancel"))]
    price_keys = ("priceWithDisc", "finishedPrice", "totalPrice", "forPay")
    amount = sum(_first_number(row, price_keys) for row in rows)
    active_amount = sum(_first_number(row, price_keys) for row in active_rows)
    cancelled_amount = sum(_first_number(row, price_keys) for row in cancelled_rows)
    top_skus = Counter(str(row.get("supplierArticle") or row.get("vendorCode") or "").strip() for row in rows)
    top_skus.pop("", None)
    return {
        "total_rows": len(rows),
        "total_orders": len(rows),
        "active_orders": len(active_rows),
        "cancelled_orders": len(cancelled_rows),
        "amount": _money(amount),
        "active_amount": _money(active_amount),
        "cancelled_amount": _money(cancelled_amount),
        "price_basis": "priceWithDisc",
        "amount_fields": {
            "all_rows": {
                key: _money(sum(_first_number(row, (key,)) for row in rows))
                for key in ("totalPrice", "priceWithDisc", "finishedPrice", "forPay")
            },
            "active_rows": {
                key: _money(sum(_first_number(row, (key,)) for row in active_rows))
                for key in ("totalPrice", "priceWithDisc", "finishedPrice", "forPay")
            },
        },
        "top_skus": [{"sku": sku, "orders": count} for sku, count in top_skus.most_common(5)],
    }


def _summarize_wb_sales(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return_rows: list[dict[str, Any]] = []
    sale_rows: list[dict[str, Any]] = []
    for row in rows:
        sale_id = str(row.get("saleID") or row.get("saleId") or "")
        if sale_id.startswith("R") or _truthy(row.get("isReturn")):
            return_rows.append(row)
        else:
            sale_rows.append(row)

    price_keys = ("priceWithDisc", "finishedPrice", "totalPrice", "forPay")
    sales_amount = sum(_first_number(row, price_keys) for row in sale_rows)
    returns_amount = sum(_first_number(row, price_keys) for row in return_rows)
    for_pay_amount = sum(_first_number(row, ("forPay",)) for row in sale_rows)
    returns_for_pay_amount = sum(_first_number(row, ("forPay",)) for row in return_rows)
    return {
        "status": "ok",
        "source": "/api/v1/supplier/sales",
        "source_note": "Оперативный предварительный отчет WB: продажа/возврат, 1 строка = 1 товар; сумма для витрины рассчитана по priceWithDisc.",
        "total_rows": len(rows),
        "sales_rows": len(sale_rows),
        "return_rows": len(return_rows),
        "sales_amount": _money(sales_amount),
        "returns_amount": _money(returns_amount),
        "net_amount_estimate": _money(sales_amount - returns_amount),
        "for_pay_amount": _money(for_pay_amount),
        "returns_for_pay_amount": _money(returns_for_pay_amount),
        "net_for_pay_estimate": _money(for_pay_amount - returns_for_pay_amount),
        "price_basis": "priceWithDisc",
        "amount_fields": {
            "sale_rows": {
                key: _money(sum(_first_number(row, (key,)) for row in sale_rows))
                for key in ("totalPrice", "priceWithDisc", "finishedPrice", "forPay")
            },
            "return_rows": {
                key: _money(sum(_first_number(row, (key,)) for row in return_rows))
                for key in ("totalPrice", "priceWithDisc", "finishedPrice", "forPay")
            },
        },
    }


def _summarize_wb_finance_expenses(
    *,
    reports: list[dict[str, Any]],
    acquiring_reports: list[dict[str, Any]] | None = None,
    ad_spend: float | int | None = None,
) -> dict[str, Any]:
    if not reports:
        return _not_confirmed(
            "WB Finance API не вернул ежедневный финансовый отчет за дату",
            "/api/finance/v1/sales-reports/list",
        )

    gross_amount = sum(_first_number(row, ("retailAmountSum",)) for row in reports)
    for_pay = sum(_first_number(row, ("forPaySum",)) for row in reports)
    bank_payment = sum(_first_number(row, ("bankPaymentSum",)) for row in reports)
    advertising = float(ad_spend or 0)
    acquiring_reports = acquiring_reports or []
    acquiring = sum(
        _first_number(row, ("acquiringFeeSum",))
        + _first_number(row, ("acquiringFeeVatSum",))
        for row in acquiring_reports
    )
    marketplace_deductions = max(0.0, gross_amount - for_pay)
    signed_components = {
        "marketplace_deductions_before_logistics": marketplace_deductions,
        "logistics": sum(_first_number(row, ("deliveryServiceSum",)) for row in reports),
        "storage": sum(_first_number(row, ("paidStorageSum",)) for row in reports),
        "acceptance": sum(_first_number(row, ("paidAcceptanceSum",)) for row in reports),
        "deductions": sum(_first_number(row, ("deductionSum",)) for row in reports),
        "penalties": sum(_first_number(row, ("penaltySum",)) for row in reports),
        "cashback": sum(
            _first_number(row, ("cashbackAmountSum",))
            + _first_number(row, ("cashbackDiscountSum",))
            + _first_number(row, ("cashbackCommissionChangeSum",))
            for row in reports
        ),
        "payment_schedule": sum(_first_number(row, ("paymentSchedule",)) for row in reports),
    }
    additional_payments = sum(_first_number(row, ("additionalPaymentSum",)) for row in reports)
    expenses = {
        key: max(0.0, value)
        for key, value in signed_components.items()
    }
    if additional_payments < 0:
        expenses["negative_additional_payments"] = abs(additional_payments)
    expenses["acquiring"] = acquiring
    expenses["advertising"] = advertising

    credits = {
        f"{key}_credit": abs(value)
        for key, value in signed_components.items()
        if value < 0
    }
    if additional_payments > 0:
        credits["additional_payments"] = additional_payments

    sales_report_expenses = sum(max(0.0, value) for value in signed_components.values())
    if additional_payments < 0:
        sales_report_expenses += abs(additional_payments)
    marketplace_expenses = sum(value for key, value in expenses.items() if key != "advertising")
    total_with_ads = marketplace_expenses + advertising
    total_credits = sum(credits.values())
    expected_bank_payment = gross_amount - sales_report_expenses + total_credits
    reconciliation_delta = bank_payment - expected_bank_payment
    if abs(reconciliation_delta) >= 0.01:
        credits["unclassified_reconciliation"] = reconciliation_delta
        total_credits += reconciliation_delta

    return {
        "status": "ok",
        "source": (
            "/api/finance/v1/sales-reports/list + "
            "/api/finance/v1/acquiring/list + /adv/v3/fullstats"
        ),
        "source_note": (
            "Расходы WB рассчитаны по положительным статьям ежедневного отчета реализации; "
            "отрицательные удержания и другие зачисления показаны отдельно как корректировки. "
            "Реклама добавлена из WB Promotion и не входит в bankPaymentSum."
        ),
        "reports_count": len(reports),
        "acquiring_reports_count": len(acquiring_reports),
        "report_ids": [row.get("reportId") for row in reports if row.get("reportId")],
        "created_dates": sorted({str(row.get("createDate") or "") for row in reports if row.get("createDate")}),
        "gross_amount": _money(gross_amount),
        "for_pay": _money(for_pay),
        "bank_payment": _money(bank_payment),
        "total_expenses": _money(total_with_ads),
        "marketplace_expenses": _money(marketplace_expenses),
        "additional_payments": _money(additional_payments),
        "net_after_expenses": _money(gross_amount - total_with_ads),
        "total_credits_and_adjustments": _money(total_credits),
        "cash_after_adjustments_and_ads": _money(bank_payment - acquiring - advertising),
        "bank_payment_reconciliation_delta": _money(reconciliation_delta),
        "expenses": {key: _money(value) for key, value in expenses.items()},
        "credits_and_adjustments": {key: _money(value) for key, value in credits.items()},
    }


def _summarize_wb_ad_spend(
    *,
    adapter: WbPromotionAdapter,
    day: str,
) -> dict[str, Any]:
    count_data = adapter.fetch_campaign_count()
    campaign_ids: list[int] = []
    for group in count_data.get("adverts") or []:
        for item in group.get("advert_list") or []:
            advert_id = int(_first_number(item, ("advertId",)))
            if advert_id:
                campaign_ids.append(advert_id)

    campaign_ids = sorted(set(campaign_ids))
    stats_rows = adapter.fetch_fullstats(ids=campaign_ids, date_from=day, date_to=day) if campaign_ids else []
    spend = 0.0
    for campaign in stats_rows:
        days = campaign.get("days") if isinstance(campaign.get("days"), list) else []
        if days:
            spend += sum(_first_number(row, ("sum",)) for row in days if isinstance(row, dict))
        else:
            spend += _first_number(campaign, ("sum",))

    return {
        "status": "ok",
        "source": "/adv/v3/fullstats",
        "campaigns_total": len(campaign_ids),
        "campaigns_with_stats": len(stats_rows),
        "spend": _money(spend),
    }


def _unwrap_wb_list(response: dict[str, Any], key: str) -> list[dict[str, Any]]:
    data = response.get("data") if isinstance(response, dict) else response
    if isinstance(data, dict):
        rows = data.get(key) or data.get("items") or data.get("data") or []
    else:
        rows = data
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _count_wb_period_items(
    adapter: WbCommunicationsAdapter,
    *,
    item_type: str,
    day: str,
) -> dict[str, Any]:
    if item_type == "feedbacks":
        fetch = adapter.fetch_feedbacks
        list_key = "feedbacks"
        take = 5000
    else:
        fetch = adapter.fetch_questions
        list_key = "questions"
        take = 10000

    rows: list[dict[str, Any]] = []
    for is_answered in (False, True):
        response = fetch(is_answered=is_answered, take=take, skip=0, order="dateDesc")
        rows.extend(_unwrap_wb_list(response, list_key))

    return {
        "status": "ok",
        "source": f"feedbacks-api.wildberries.ru/{item_type}",
        "count": len(_filter_rows_for_day(rows, day)),
        "rows_checked": len(rows),
    }


def _summarize_wb_stocks(
    *,
    stock_rows: list[dict[str, Any]],
    catalog_rows: list[dict[str, Any]],
    low_stock_threshold: int = 3,
) -> dict[str, Any]:
    qty_by_sku: dict[str, float] = {}
    for row in stock_rows:
        sku = str(row.get("supplierArticle") or row.get("vendorCode") or "").strip()
        if not sku:
            continue
        qty_by_sku[sku] = qty_by_sku.get(sku, 0.0) + _first_number(row, ("quantity", "qty", "stock"))

    master_wb_codes = {
        str(row.get("wb_vendor_code") or "").strip()
        for row in catalog_rows
        if str(row.get("wb_vendor_code") or "").strip()
    }
    title_by_sku = {
        str(row.get("wb_vendor_code") or "").strip(): _catalog_title(row)
        for row in catalog_rows
        if str(row.get("wb_vendor_code") or "").strip()
    }
    internal_by_sku = {
        str(row.get("wb_vendor_code") or "").strip(): _catalog_internal_sku(row)
        for row in catalog_rows
        if str(row.get("wb_vendor_code") or "").strip()
    }
    possible_missing = sorted(master_wb_codes - set(qty_by_sku))
    zero_stock_rows = [
        {"sku": sku, "internal_sku": internal_by_sku.get(sku, ""), "title": title_by_sku.get(sku, ""), "quantity": int(qty)}
        for sku, qty in sorted(qty_by_sku.items(), key=lambda item: (item[1], item[0]))
        if sku in master_wb_codes and qty <= 0
    ]
    low_stock_sample = [
        {"sku": sku, "internal_sku": internal_by_sku.get(sku, ""), "title": title_by_sku.get(sku, ""), "quantity": int(qty)}
        for sku, qty in sorted(qty_by_sku.items(), key=lambda item: (item[1], item[0]))
        if sku in master_wb_codes and 0 < qty <= low_stock_threshold
    ][:10]
    missing_sample = [
        {"sku": sku, "internal_sku": internal_by_sku.get(sku, ""), "title": title_by_sku.get(sku, "")}
        for sku in possible_missing[:10]
    ]
    return {
        "status": "warning",
        "source": "/api/v1/supplier/stocks",
        "source_note": "Устаревший WB endpoint остатков запланирован к отключению 2026-06-23.",
        "warehouse_rows": len(stock_rows),
        "sku_rows": len(qty_by_sku),
        "wb_catalog_codes_count": len(master_wb_codes),
        "quantity_total": int(sum(qty_by_sku.values())),
        "zero_stock_count": len(zero_stock_rows),
        "zero_stock_sample": zero_stock_rows[:10],
        "missing_in_stock_source_count": len(possible_missing),
        "missing_in_stock_source_sample": missing_sample,
        "low_stock_threshold": low_stock_threshold,
        "low_stock_count": sum(1 for sku, qty in qty_by_sku.items() if sku in master_wb_codes and 0 < qty <= low_stock_threshold),
        "low_stock_sample": low_stock_sample,
    }


def _summarize_wb_analytics_stocks(
    *,
    stock_rows: list[dict[str, Any]],
    catalog_rows: list[dict[str, Any]],
    low_stock_threshold: int = 3,
) -> dict[str, Any]:
    qty_by_nm: dict[str, float] = {}
    in_way_to_client = 0.0
    in_way_from_client = 0.0
    for row in stock_rows:
        nm_id = str(row.get("nmId") or row.get("nm_id") or "").strip()
        if not nm_id:
            continue
        qty_by_nm[nm_id] = qty_by_nm.get(nm_id, 0.0) + _first_number(row, ("quantity", "qty", "stock"))
        in_way_to_client += _first_number(row, ("inWayToClient", "in_way_to_client"))
        in_way_from_client += _first_number(row, ("inWayFromClient", "in_way_from_client"))

    catalog_by_nm = {
        str(row.get("wb_nm_id") or "").strip(): row
        for row in catalog_rows
        if str(row.get("wb_nm_id") or "").strip()
    }
    catalog_nm_ids = set(catalog_by_nm)
    missing_nm_ids = sorted(catalog_nm_ids - set(qty_by_nm))

    def product_row(nm_id: str, quantity: float | None = None) -> dict[str, Any]:
        catalog_row = catalog_by_nm.get(nm_id, {})
        result = {
            "nm_id": nm_id,
            "sku": str(catalog_row.get("wb_vendor_code") or "").strip(),
            "internal_sku": _catalog_internal_sku(catalog_row),
            "title": _catalog_title(catalog_row),
        }
        if quantity is not None:
            result["quantity"] = int(quantity)
        return result

    zero_stock = [
        product_row(nm_id, qty)
        for nm_id, qty in sorted(qty_by_nm.items(), key=lambda item: (item[1], item[0]))
        if nm_id in catalog_nm_ids and qty <= 0
    ]
    low_stock = [
        product_row(nm_id, qty)
        for nm_id, qty in sorted(qty_by_nm.items(), key=lambda item: (item[1], item[0]))
        if nm_id in catalog_nm_ids and 0 < qty <= low_stock_threshold
    ]
    return {
        "status": "ok",
        "source": "/api/analytics/v1/stocks-report/wb-warehouses",
        "source_note": "Актуальный WB Analytics endpoint; строки агрегированы по nmId по всем складам.",
        "warehouse_rows": len(stock_rows),
        "nm_rows": len(qty_by_nm),
        "wb_catalog_nm_count": len(catalog_nm_ids),
        "quantity_total": int(sum(qty_by_nm.values())),
        "in_way_to_client": int(in_way_to_client),
        "in_way_from_client": int(in_way_from_client),
        "zero_stock_count": len(zero_stock),
        "zero_stock_sample": zero_stock[:10],
        "missing_in_stock_source_count": len(missing_nm_ids),
        "missing_in_stock_source_sample": [product_row(nm_id) for nm_id in missing_nm_ids[:10]],
        "low_stock_threshold": low_stock_threshold,
        "low_stock_count": len(low_stock),
        "low_stock_sample": low_stock[:10],
    }


def _extract_count(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, dict):
        for key in (
            "countUnanswered",
            "count_unanswered",
            "unanswered",
            "count",
            "total",
            "all",
            "today",
        ):
            if key in value:
                count = _extract_count(value[key])
                if count is not None:
                    return count
        for nested_key in ("data", "result"):
            if nested_key in value:
                count = _extract_count(value[nested_key])
                if count is not None:
                    return count
    return None


def _extract_ozon_rows(response: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(response, list):
        return [row for row in response if isinstance(row, dict)]
    if not isinstance(response, dict):
        return []
    result = response.get("result")
    candidates: list[Any] = [result, response]
    for candidate in candidates:
        if isinstance(candidate, list):
            return [row for row in candidate if isinstance(row, dict)]
        if not isinstance(candidate, dict):
            continue
        for key in keys:
            rows = candidate.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
    return []


def _extract_ozon_attention_count(value: Any) -> int | None:
    if isinstance(value, dict):
        for key in (
            "UNPROCESSED",
            "NOT_VIEWED",
            "NEW",
            "unprocessed",
            "not_viewed",
            "new",
            "todo",
        ):
            if key in value:
                count = _extract_count(value[key])
                if count is not None:
                    return count
        for nested_key in ("result", "data"):
            nested = value.get(nested_key)
            if isinstance(nested, dict):
                count = _extract_ozon_attention_count(nested)
                if count is not None:
                    return count
    return _extract_count(value)


def _count_ozon_rows_for_day(rows: list[dict[str, Any]], day: str) -> dict[str, Any]:
    return {
        "status": "ok",
        "count": len(_filter_rows_for_day(rows, day)),
        "rows_checked": len(rows),
    }


def _collect_ozon_communications(
    *,
    adapter: OzonSellerAdapter,
    run_dir: Path | None,
    day: str,
    limit: int = 100,
) -> dict[str, Any]:
    official: dict[str, Any] = {"status": "unknown", "source": "Ozon Seller API", "methods": {}}
    reviews: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    review_count: Any = None
    question_count: Any = None

    for name, call, rows_key in (
        ("review_count", adapter.fetch_review_count, ""),
        ("review_list", lambda: adapter.fetch_review_list(status="ALL", limit=limit), "reviews"),
        ("question_count", adapter.fetch_question_count, ""),
        ("question_list", lambda: adapter.fetch_question_list(status="ALL", limit=limit), "questions"),
    ):
        try:
            data = call()
            official["methods"][name] = {"status": "ok"}
            if name == "review_count":
                review_count = data
            elif name == "question_count":
                question_count = data
            elif name == "review_list":
                reviews = _extract_ozon_rows(data, rows_key, "items", "data")
            elif name == "question_list":
                questions = _extract_ozon_rows(data, rows_key, "items", "data")
        except Exception as exc:  # noqa: BLE001
            official["methods"][name] = {"status": "error", "error": _safe_error(exc)}

    official_ok = any(item.get("status") == "ok" for item in official["methods"].values())
    if official_ok:
        official["status"] = "ok"
        return {
            "status": "ok",
            "source": "Ozon Seller API",
            "official": official,
            "yesterday_feedbacks": {
                **_count_ozon_rows_for_day(reviews, day),
                "source": "/v1/review/list",
                "rows_checked": len(reviews),
            },
            "yesterday_questions": {
                **_count_ozon_rows_for_day(questions, day),
                "source": "/v1/question/list",
                "rows_checked": len(questions),
            },
            "unanswered_feedbacks": _extract_ozon_attention_count(review_count),
            "unanswered_questions": _extract_ozon_attention_count(question_count),
        }

    official["status"] = "error"
    if not run_dir:
        return {
            "status": "error",
            "source": "Ozon Seller API",
            "official": official,
            "error": "official Ozon review/question API failed and LK fallback run_dir is not available",
        }

    try:
        fallback_reviews, fallback_questions, fallback_summary = _run_ozon_lk_fallback(run_dir=run_dir, limit=limit)
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "source": "Ozon Seller API + LK/CDP fallback",
            "official": official,
            "error": _safe_error(exc),
        }

    if not fallback_summary.get("ok"):
        return {
            "status": "error",
            "source": "Ozon Seller API + LK/CDP fallback",
            "official": official,
            "fallback": fallback_summary,
            "error": fallback_summary.get("blocker") or "Ozon LK fallback failed",
        }

    return {
        "status": "warning",
        "source": "Ozon LK/CDP fallback",
        "source_note": (
            "Официальный Ozon API отзывов/вопросов недоступен для текущего ключа; LK/CDP fallback считает только "
            "видимые непросмотренные отзывы и новые вопросы, поэтому итоги за период могут быть ниже фактических."
        ),
        "official": official,
        "fallback": fallback_summary,
        "yesterday_feedbacks": {
            **_count_ozon_rows_for_day(fallback_reviews, day),
            "source": "ozon_lk_cdp_internal_api/reviews",
        },
        "yesterday_questions": {
            **_count_ozon_rows_for_day(fallback_questions, day),
            "source": "ozon_lk_cdp_internal_api/questions",
        },
        "unanswered_feedbacks": len(fallback_reviews),
        "unanswered_questions": len(fallback_questions),
    }


def _business_status(business: dict[str, Any]) -> str:
    statuses: list[str] = []
    for marketplace in ("ozon", "wb"):
        data = business.get(marketplace, {})
        if not isinstance(data, dict):
            continue
        for section in data.values():
            if isinstance(section, dict) and section.get("status"):
                statuses.append(str(section["status"]))
    if any(status == "error" for status in statuses):
        return "warning"
    if any(status in {"warning", "skipped"} for status in statuses):
        return "warning"
    return "ok"


def _collect_business_snapshot(
    *,
    credentials: AppCredentials,
    data_dir: Path,
    today: date | None = None,
    include_period_communications: bool = False,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    report_day = today or _moscow_today()
    periods = {
        "yesterday": (report_day - timedelta(days=1)).isoformat(),
        "today": report_day.isoformat(),
    }
    unified_rows = _unified_catalog_rows(data_dir)
    catalog_rows = unified_rows if unified_rows else _master_catalog_rows(data_dir)
    business: dict[str, Any] = {
        "periods": periods,
        "catalog_rows": len(catalog_rows),
        "catalog_source": "unified_catalog" if unified_rows else "master_catalog",
        "ozon": {},
        "wb": {},
    }

    if credentials.ozon_seller:
        ozon = OzonSellerAdapter(credentials.ozon_seller)
        metrics = ["revenue", "ordered_units"]
        ozon_orders: dict[str, Any] = {
            "status": "ok",
            "source": "/v1/analytics/data",
        }
        try:
            ozon_orders["yesterday"] = _extract_ozon_analytics(
                ozon.fetch_analytics_data(
                    date_from=periods["yesterday"],
                    date_to=periods["yesterday"],
                    metrics=metrics,
                    dimensions=["day"],
                ),
                metrics,
            )
        except Exception as exc:  # noqa: BLE001 - report must survive API permission/schema errors
            ozon_orders.update(_source_error("/v1/analytics/data", exc))

        if include_period_communications:
            ozon_orders["today"] = _not_confirmed(
                "v3 строится за завершенный вчерашний день и не запрашивает неполный сегодняшний период"
            )
        else:
            try:
                ozon_orders["today"] = _extract_ozon_analytics(
                    ozon.fetch_analytics_data(
                        date_from=periods["today"],
                        date_to=periods["today"],
                        metrics=metrics,
                        dimensions=["day"],
                    ),
                    metrics,
                )
            except Exception as exc:  # noqa: BLE001
                ozon_orders["today"] = _source_error("/v1/analytics/data", exc)
                if ozon_orders.get("status") == "ok":
                    ozon_orders["status"] = "warning"
        business["ozon"]["orders"] = ozon_orders

        try:
            product_ids = sorted(
                {
                    str(row.get("ozon_product_id")).strip()
                    for row in catalog_rows
                    if str(row.get("ozon_product_id") or "").strip()
                }
            )
            business["ozon"]["stocks"] = _summarize_ozon_stocks(
                stock_items=ozon.fetch_product_stocks(product_ids),
                catalog_rows=catalog_rows,
            )
        except Exception as exc:  # noqa: BLE001
            business["ozon"]["stocks"] = _source_error("/v4/product/info/stocks", exc)

        if include_period_communications:
            try:
                since, to = _day_bounds_utc(periods["yesterday"])
                ozon_finance_operations = ozon.fetch_finance_transactions(
                    date_from=since,
                    date_to=to,
                )
                business["ozon"]["finance_buyouts"] = _summarize_ozon_finance_buyouts(
                    operations=ozon_finance_operations,
                    day=periods["yesterday"],
                )
                business["ozon"]["finance_expenses"] = _summarize_ozon_finance_expenses(
                    operations=ozon_finance_operations,
                    day=periods["yesterday"],
                )
            except Exception as exc:  # noqa: BLE001
                business["ozon"]["finance_buyouts"] = _source_error("/v3/finance/transaction/list", exc)
                business["ozon"]["finance_expenses"] = _source_error("/v3/finance/transaction/list", exc)

            try:
                since, to = _day_bounds_msk(periods["yesterday"])
                business["ozon"]["fbo_postings"] = _summarize_ozon_fbo_postings(
                    ozon.fetch_fbo_postings(since=since, to=to)
                )
            except Exception as exc:  # noqa: BLE001
                business["ozon"]["fbo_postings"] = _source_error("/v2/posting/fbo/list", exc)

            business["ozon"]["communications"] = _collect_ozon_communications(
                adapter=ozon,
                run_dir=run_dir,
                day=periods["yesterday"],
            )
    else:
        business["ozon"]["orders"] = {"status": "skipped", "source": "/v1/analytics/data", "error": "missing credentials"}
        business["ozon"]["stocks"] = {"status": "skipped", "source": "/v4/product/info/stocks", "error": "missing credentials"}
        if include_period_communications:
            business["ozon"]["finance_buyouts"] = {
                "status": "skipped",
                "source": "/v3/finance/transaction/list",
                "error": "missing credentials",
            }
            business["ozon"]["finance_expenses"] = {
                "status": "skipped",
                "source": "/v3/finance/transaction/list",
                "error": "missing credentials",
            }
            business["ozon"]["fbo_postings"] = {"status": "skipped", "source": "/v2/posting/fbo/list", "error": "missing credentials"}
            business["ozon"]["communications"] = {
                "status": "skipped",
                "source": "Ozon Seller API",
                "error": "missing credentials",
            }

    if credentials.wb:
        wb_stats = WbStatisticsAdapter(credentials.wb)
        wb_communications = WbCommunicationsAdapter(credentials.wb)
        wb_finance = WbFinanceAdapter(credentials.wb)
        wb_promotion = WbPromotionAdapter(credentials.wb)
        try:
            yesterday_orders = _summarize_wb_orders(
                _filter_rows_for_day(wb_stats.fetch_orders(date_from=periods["yesterday"], flag=1), periods["yesterday"])
            )
            today_orders = (
                _not_confirmed("v3 не запрашивает сегодняшний период, чтобы не превышать лимит WB Statistics API")
                if include_period_communications
                else _summarize_wb_orders(
                    _filter_rows_for_day(wb_stats.fetch_orders(date_from=periods["today"], flag=1), periods["today"])
                )
            )
            business["wb"]["orders"] = {
                "status": "ok",
                "source": "/api/v1/supplier/orders",
                "source_note": "WB updates this data every 30 minutes.",
                "yesterday": yesterday_orders,
                "today": today_orders,
            }
        except Exception as exc:  # noqa: BLE001
            business["wb"]["orders"] = _source_error("/api/v1/supplier/orders", exc)

        try:
            yesterday_sales = _summarize_wb_sales(
                _filter_rows_for_day(wb_stats.fetch_sales(date_from=periods["yesterday"], flag=1), periods["yesterday"])
            )
            today_sales = (
                _not_confirmed("v3 не запрашивает сегодняшний период, чтобы не превышать лимит WB Statistics API")
                if include_period_communications
                else _summarize_wb_sales(
                    _filter_rows_for_day(wb_stats.fetch_sales(date_from=periods["today"], flag=1), periods["today"])
                )
            )
            business["wb"]["sales"] = {
                "status": "ok",
                "source": "/api/v1/supplier/sales",
                "source_note": "Предварительный оперативный отчет WB; данные обновляются раз в 30 минут.",
                "yesterday": yesterday_sales,
                "today": today_sales,
            }
        except Exception as exc:  # noqa: BLE001
            business["wb"]["sales"] = _source_error("/api/v1/supplier/sales", exc)

        if include_period_communications:
            wb_ad_spend: dict[str, Any]
            try:
                wb_ad_spend = _summarize_wb_ad_spend(
                    adapter=wb_promotion,
                    day=periods["yesterday"],
                )
                business["wb"]["ad_spend"] = wb_ad_spend
            except Exception as exc:  # noqa: BLE001
                wb_ad_spend = _source_error("/adv/v3/fullstats", exc)
                business["wb"]["ad_spend"] = wb_ad_spend

            try:
                reports = wb_finance.fetch_sales_reports(
                    date_from=periods["yesterday"],
                    date_to=periods["yesterday"],
                    period="daily",
                )
                acquiring_reports = wb_finance.fetch_acquiring_reports(
                    date_from=periods["yesterday"],
                    date_to=periods["yesterday"],
                )
                ad_spend = wb_ad_spend.get("spend") if wb_ad_spend.get("status") == "ok" else 0
                business["wb"]["finance_expenses"] = _summarize_wb_finance_expenses(
                    reports=reports,
                    acquiring_reports=acquiring_reports,
                    ad_spend=ad_spend,
                )
            except Exception as exc:  # noqa: BLE001
                business["wb"]["finance_expenses"] = _source_error("/api/finance/v1/sales-reports/list", exc)

        try:
            business["wb"]["stocks"] = _summarize_wb_analytics_stocks(
                stock_rows=WbAnalyticsAdapter(credentials.wb).fetch_wb_warehouse_stocks(),
                catalog_rows=catalog_rows,
            )
        except Exception as exc:  # noqa: BLE001
            business["wb"]["stocks"] = _source_error(
                "/api/analytics/v1/stocks-report/wb-warehouses",
                exc,
            )

        try:
            feedbacks = wb_communications.fetch_unanswered_feedbacks_count()
            questions = wb_communications.fetch_unanswered_questions_count()
            business["wb"]["communications"] = {
                "status": "ok",
                "source": "feedbacks-api.wildberries.ru",
                "unanswered_feedbacks": _extract_count(feedbacks),
                "unanswered_questions": _extract_count(questions),
                "feedbacks_response_keys": sorted(feedbacks.keys()) if isinstance(feedbacks, dict) else [],
                "questions_response_keys": sorted(questions.keys()) if isinstance(questions, dict) else [],
            }
            if include_period_communications:
                business["wb"]["communications"]["yesterday_feedbacks"] = _count_wb_period_items(
                    wb_communications,
                    item_type="feedbacks",
                    day=periods["yesterday"],
                )
                business["wb"]["communications"]["yesterday_questions"] = _count_wb_period_items(
                    wb_communications,
                    item_type="questions",
                    day=periods["yesterday"],
                )
        except Exception as exc:  # noqa: BLE001
            business["wb"]["communications"] = _source_error("feedbacks-api.wildberries.ru", exc)
    else:
        business["wb"]["orders"] = {"status": "skipped", "source": "/api/v1/supplier/orders", "error": "missing credentials"}
        business["wb"]["sales"] = {"status": "skipped", "source": "/api/v1/supplier/sales", "error": "missing credentials"}
        business["wb"]["finance_expenses"] = {
            "status": "skipped",
            "source": "/api/finance/v1/sales-reports/list",
            "error": "missing credentials",
        }
        business["wb"]["ad_spend"] = {"status": "skipped", "source": "/adv/v3/fullstats", "error": "missing credentials"}
        business["wb"]["stocks"] = {
            "status": "skipped",
            "source": "/api/analytics/v1/stocks-report/wb-warehouses",
            "error": "missing credentials",
        }
        business["wb"]["communications"] = {
            "status": "skipped",
            "source": "feedbacks-api.wildberries.ru",
            "error": "missing credentials",
        }

    business["business_status"] = _business_status(business)
    return business


def _artifact_path(run: dict[str, Any], key: str) -> Path | None:
    artifacts = ((run.get("summary") or {}).get("artifacts") or {})
    value = artifacts.get(key)
    if not value:
        return None
    return Path(str(value))


def _action_sample_row(row: dict[str, Any], *, sku_key: str, title_key: str, reason_key: str = "reason_code") -> dict[str, str]:
    return {
        "sku": str(row.get(sku_key) or "").strip(),
        "title": str(row.get(title_key) or "").strip(),
        "reason": str(row.get(reason_key) or "").strip(),
    }


def _ozon_action_reason_details(run: dict[str, Any]) -> dict[str, Any]:
    csv_path = _artifact_path(run, "csv")
    rows = _safe_read_csv(csv_path) if csv_path else []
    skipped = [row for row in rows if row.get("planned_action") == "skip_candidate"]
    no_stock = [row for row in skipped if row.get("reason_code") == "no_stock"]
    over_threshold = [row for row in skipped if row.get("reason_code") == "below_min_price_threshold"]
    other = [
        row
        for row in skipped
        if row.get("reason_code") not in {"no_stock", "below_min_price_threshold"}
    ]
    return {
        "zero_stock": len(no_stock),
        "above_threshold": len(over_threshold),
        "no_actions_for_product": 0,
        "other": len(other),
        "source_csv": str(csv_path) if csv_path else "",
        "samples": {
            "zero_stock": [_action_sample_row(row, sku_key="offer_id", title_key="name") for row in no_stock[:5]],
            "above_threshold": [_action_sample_row(row, sku_key="offer_id", title_key="name") for row in over_threshold[:5]],
            "other": [_action_sample_row(row, sku_key="offer_id", title_key="name") for row in other[:5]],
        },
    }


def _wb_action_reason_details(run: dict[str, Any], business: dict[str, Any] | None = None) -> dict[str, Any]:
    csv_path = _artifact_path(run, "csv")
    rows = _safe_read_csv(csv_path, delimiter=";") if csv_path else []
    zero_skus = {
        str(row.get("sku") or "").strip()
        for row in (((business or {}).get("wb", {}).get("stocks", {}) or {}).get("zero_stock_sample") or [])
        if str(row.get("sku") or "").strip()
    }
    missing_count = (((business or {}).get("wb", {}).get("stocks", {}) or {}).get("missing_in_stock_source_count"))

    outside = [
        row
        for row in rows
        if str(row.get("Акций") or "").strip() in {"", "0"}
    ]
    zero_stock = [
        row
        for row in outside
        if str(row.get("Артикул поставщика") or "").strip() in zero_skus
    ]
    no_actions = [
        row
        for row in outside
        if str(row.get("Артикул поставщика") or "").strip() not in zero_skus
    ]
    over_threshold = [
        row
        for row in rows
        if "порога >" in str(row.get("Причина") or "")
    ]
    return {
        "zero_stock": len(zero_stock),
        "above_threshold": len(over_threshold),
        "no_actions_for_product": len(no_actions),
        "missing_stock_source_count": missing_count,
        "source_csv": str(csv_path) if csv_path else "",
        "samples": {
            "zero_stock": [
                _action_sample_row(row, sku_key="Артикул поставщика", title_key="Наименование", reason_key="Причина")
                for row in zero_stock[:5]
            ],
            "above_threshold": [
                _action_sample_row(row, sku_key="Артикул поставщика", title_key="Наименование", reason_key="Причина")
                for row in over_threshold[:5]
            ],
            "no_actions_for_product": [
                _action_sample_row(row, sku_key="Артикул поставщика", title_key="Наименование", reason_key="Причина")
                for row in no_actions[:5]
            ],
        },
    }


def _format_action_reason_counts(details: dict[str, Any]) -> str:
    if not details:
        return "не подтверждено"
    parts = [
        f"нулевой остаток: {_metric_int(details.get('zero_stock'))}",
        f"участие выше порога: {_metric_int(details.get('above_threshold'))}",
        f"нет акций для товара: {_metric_int(details.get('no_actions_for_product'))}",
    ]
    if details.get("other"):
        parts.append(f"прочее: {_metric_int(details.get('other'))}")
    return "; ".join(parts)


def _actions_v3(actions: dict[str, Any], *, business: dict[str, Any] | None = None) -> dict[str, Any]:
    ozon_run = actions.get("latest_ozon_elastic") or {}
    wb_run = actions.get("latest_wb_actions") or {}
    ozon_summary = ((actions.get("latest_ozon_elastic") or {}).get("summary") or {}).get("summary") or {}
    wb_summary = ((actions.get("latest_wb_actions") or {}).get("summary") or {}).get("summary") or {}
    ozon_reason_details = _ozon_action_reason_details(ozon_run) if ozon_summary else {}
    wb_reason_details = _wb_action_reason_details(wb_run, business=business) if wb_summary else {}
    wb_stats = wb_actions_report_stats(_artifact_path(wb_run, "csv")) if wb_summary else {"available": False}

    ozon_total = ozon_summary.get("merged_unique_products")
    ozon_active = ozon_summary.get("active_rows")
    ozon_not_active = None
    if ozon_total is not None and ozon_active is not None:
        try:
            ozon_not_active = max(0, int(ozon_total) - int(ozon_active))
        except (TypeError, ValueError):
            ozon_not_active = None
    ozon_add = ozon_summary.get("add_to_action")
    ozon_remove = ozon_summary.get("deactivate_from_action")
    ozon_after = None
    if ozon_active is not None and ozon_add is not None and ozon_remove is not None:
        try:
            ozon_after = int(ozon_active) + int(ozon_add) - int(ozon_remove)
        except (TypeError, ValueError):
            ozon_after = None

    wb_products_in_actions = (
        wb_stats.get("current_participating")
        if wb_stats.get("available")
        else wb_summary.get("in_promos")
    )
    wb_products_not_in_actions = (
        wb_stats.get("current_not_participating")
        if wb_stats.get("available")
        else wb_summary.get("outside_promos")
    )
    wb_not_participating_reason = (
        "акция доступна, но текущая скидка ниже требования: "
        f"{wb_stats.get('offered_not_participating')}; "
        f"нет доступной активной акции: {wb_stats.get('outside_active_promos')}"
        if wb_stats.get("available")
        else _format_action_reason_counts(wb_reason_details)
    )
    ozon_not_participating_reason = (
        f"нулевой остаток: {_metric_int(ozon_reason_details.get('zero_stock'))}; "
        f"цена акции ниже минимального порога: {_metric_int(ozon_reason_details.get('above_threshold'))}; "
        f"кандидаты на добавление: {_metric_int(ozon_add)}"
        if ozon_summary
        else "нет свежего dry-run"
    )

    return {
        "ozon": {
            "status": "ok" if ozon_summary else "not_confirmed",
            "source": (actions.get("latest_ozon_elastic") or {}).get("run_id") or "",
            "active_actions": 1 if ozon_summary else None,
            "participating_actions": 1 if ozon_summary else None,
            "products_in_actions": ozon_active,
            "products_not_in_actions": ozon_not_active,
            "available_but_not_participating": ozon_add,
            "outside_active_promos": ozon_summary.get("skip_candidate"),
            "planned_to_add": ozon_add,
            "planned_to_remove": ozon_remove,
            "products_after_plan": ozon_after,
            "products_not_in_actions_after_plan": (
                int(ozon_total) - int(ozon_after)
                if ozon_total is not None and ozon_after is not None
                else None
            ),
            "not_in_action_reasons": ozon_not_participating_reason,
            "reason_details": ozon_reason_details,
            "action_name": ozon_summary.get("action_name"),
        },
        "wb": {
            "status": "ok" if wb_summary else "not_confirmed",
            "source": (actions.get("latest_wb_actions") or {}).get("run_id") or "",
            "active_actions": wb_summary.get("active_promos"),
            "participating_actions": wb_summary.get("active_promos"),
            "products_total": wb_stats.get("total") if wb_stats.get("available") else wb_summary.get("total_goods"),
            "products_available_in_actions": (
                wb_stats.get("offered_in_active_promos")
                if wb_stats.get("available")
                else wb_summary.get("in_promos")
            ),
            "products_in_actions": wb_products_in_actions,
            "products_not_in_actions": wb_products_not_in_actions,
            "available_but_not_participating": wb_stats.get("offered_not_participating"),
            "outside_active_promos": (
                wb_stats.get("outside_active_promos")
                if wb_stats.get("available")
                else wb_summary.get("outside_promos")
            ),
            "products_after_plan": wb_stats.get("eligible_after"),
            "products_not_in_actions_after_plan": wb_stats.get("not_participating_after"),
            "current_discount_distribution": wb_stats.get("current_participating_discount_distribution"),
            "not_in_action_reasons": wb_not_participating_reason if wb_summary else "нет свежего dry-run",
            "reason_details": wb_reason_details,
            "future_actions": wb_summary.get("future_promos"),
        },
    }


def _supply_summary(*, source: str, status_counts: Counter[str], current_states: set[str]) -> dict[str, Any]:
    current_total = sum(count for status, count in status_counts.items() if status in current_states)
    return {
        "status": "ok",
        "source": source,
        "has_current": current_total > 0,
        "current_total": current_total,
        "status_counts": dict(sorted(status_counts.items())),
    }


def _supplies_v3(credentials: AppCredentials) -> dict[str, Any]:
    result: dict[str, Any] = {}

    if credentials.ozon_seller:
        try:
            adapter = OzonSellerAdapter(credentials.ozon_seller)
            order_ids = adapter.fetch_supply_order_ids(states=ACTIVE_OZON_SUPPLY_STATES)
            orders = adapter.fetch_supply_orders(order_ids) if order_ids else []
            counts = Counter(str(row.get("state") or "").strip() for row in orders)
            ozon = _supply_summary(
                source="/v3/supply-order/list + /v3/supply-order/get",
                status_counts=counts,
                current_states=set(ACTIVE_OZON_SUPPLY_STATES),
            )
            ozon.update(
                {
                    "forming": counts["DATA_FILLING"],
                    "ready_to_ship": counts["READY_TO_SUPPLY"],
                    "at_dropoff": counts["ACCEPTED_AT_SUPPLY_WAREHOUSE"],
                    "in_transit": counts["IN_TRANSIT"],
                    "at_acceptance_warehouse": counts["ACCEPTANCE_AT_STORAGE_WAREHOUSE"],
                    "acceptance": counts["REPORTS_CONFIRMATION_AWAITING"],
                }
            )
            result["ozon"] = ozon
        except Exception as exc:  # noqa: BLE001
            result["ozon"] = _source_error("Ozon supply-order API", exc)
    else:
        result["ozon"] = {"status": "skipped", "source": "Ozon supply-order API", "error": "missing credentials"}

    if credentials.wb:
        try:
            supplies = WbFbwSuppliesAdapter(credentials.wb).fetch_supplies()
            counts = Counter(str(row.get("statusID") or row.get("statusId") or "").strip() for row in supplies)
            wb = _supply_summary(
                source="supplies-api.wildberries.ru/api/v1/supplies",
                status_counts=counts,
                current_states={str(value) for value in ACTIVE_WB_SUPPLY_STATUS_IDS},
            )
            wb.update(
                {
                    "forming": counts["1"],
                    "ready_to_ship": counts["2"] + counts["3"],
                    "at_dropoff": counts["6"],
                    "in_transit": 0,
                    "at_acceptance_warehouse": 0,
                    "acceptance": counts["4"],
                    "source_note": (
                        "WB API дает статусы: не запланировано, запланировано, отгрузка разрешена, "
                        "выгружено на воротах, приемка и принято; отдельного статуса 'в пути' нет."
                    ),
                }
            )
            result["wb"] = wb
        except Exception as exc:  # noqa: BLE001
            result["wb"] = _source_error("WB FBW supplies API", exc)
    else:
        result["wb"] = {"status": "skipped", "source": "WB FBW supplies API", "error": "missing credentials"}

    return result


def _metric_money(value: Any) -> str:
    if value in (None, "не подтверждено"):
        return "не подтверждено"
    return _format_money(value)


def _metric_int(value: Any) -> str:
    if value in (None, "не подтверждено"):
        return "не подтверждено"
    return _format_int(value)


def _metric_bool(value: Any) -> str:
    if value is True:
        return "да"
    if value is False:
        return "нет"
    return "не подтверждено"


def _discount_distribution(value: Any) -> str:
    if not isinstance(value, dict) or not value:
        return "не подтверждено"
    rows: list[tuple[int, int]] = []
    for discount, count in value.items():
        try:
            rows.append((int(discount), int(count)))
        except (TypeError, ValueError):
            continue
    if not rows:
        return "не подтверждено"
    return "; ".join(f"{discount}%: {count}" for discount, count in sorted(rows, reverse=True))


def _project_health(preflight: dict[str, Any] | None, sessions: dict[str, Any]) -> dict[str, Any]:
    checks = preflight.get("checks", {}) if isinstance(preflight, dict) else {}
    return {
        "preflight_run_id": preflight.get("run_id") if isinstance(preflight, dict) else "",
        "preflight_status": preflight.get("overall_status") if isinstance(preflight, dict) else "missing",
        "ozon_api": (checks.get("ozon_api") or {}).get("status") if isinstance(checks, dict) else "",
        "ozon_performance_api": (checks.get("ozon_performance_api") or {}).get("status") if isinstance(checks, dict) else "",
        "wb_api": (checks.get("wb_api") or {}).get("status") if isinstance(checks, dict) else "",
        "ozon_refresh": checks.get("ozon_refresh_state", {}) if isinstance(checks, dict) else {},
        "wb_refresh": checks.get("wb_refresh_state", {}) if isinstance(checks, dict) else {},
        "sessions_status": sessions.get("overall_status"),
    }


def _actions_section(data_dir: Path) -> dict[str, Any]:
    latest_apply = latest_run(data_dir, "actions_apply_")
    latest_ozon = latest_run(data_dir, "ozon_elastic_plan_")
    latest_wb = latest_run(data_dir, "wb_actions_discount_plan_")
    section: dict[str, Any] = {
        "latest_apply": latest_apply,
        "latest_ozon_elastic": latest_ozon,
        "latest_wb_actions": latest_wb,
    }
    apply_summary = latest_apply.get("summary", {}) if latest_apply else {}
    if isinstance(apply_summary, dict):
        section["last_apply_summary"] = {
            "run_id": apply_summary.get("run_id"),
            "overall_status": apply_summary.get("overall_status"),
            "pending_id": apply_summary.get("pending_id"),
            "ozon_activate_rows": (apply_summary.get("ozon") or {}).get("activate_rows_count"),
            "ozon_deactivate_rows": (apply_summary.get("ozon") or {}).get("deactivate_rows_count"),
            "wb_payload_rows": (apply_summary.get("wb") or {}).get("payload_rows_count"),
            "wb_upload_id": (apply_summary.get("wb") or {}).get("upload_id"),
        }
    return section


def _decision_items(
    *,
    preflight: dict[str, Any] | None,
    pending_packages: list[dict[str, Any]],
    recommendations: dict[str, Any],
) -> list[str]:
    items: list[str] = []
    preflight_status = preflight.get("overall_status") if isinstance(preflight, dict) else None
    if preflight_status != "ok":
        items.append(f"Разобрать preflight status: {preflight_status or 'missing'}.")

    open_pending = pending_packages
    if open_pending:
        items.append(f"Проверить pending-пакеты: {len(open_pending)} шт.")

    open_recs = recommendations.get("open_items", [])
    if open_recs:
        first = open_recs[0]
        items.append(f"Рассмотреть рекомендацию {first['id']}: {first['topic']}.")

    if not items:
        items.append("Критичных решений по текущему MVP-отчету нет.")
    return items


def _write_report(path: Path, result: dict[str, Any]) -> None:
    ensure_dir(path.parent)
    health = result["project_health"]
    catalog = result["catalog"]
    unified_catalog = result.get("unified_catalog", {})
    actions = result["actions"]
    sessions = result["sessions"]
    lines = [
        "# Vital Shevron Утренний Отчет",
        "",
        f"Run ID: `{result['run_id']}`",
        f"Время запуска: `{result['started_at']}`",
        f"Общий статус: `{result['overall_status']}`",
        "",
        "## Краткая Сводка",
        "",
    ]
    for item in result["executive_summary"]:
        lines.append(f"- {item}")

    lines.extend(["", "## Состояние Проекта", ""])
    for key in ("preflight_run_id", "preflight_status", "ozon_api", "ozon_performance_api", "wb_api", "sessions_status"):
        lines.append(f"- `{key}`: `{health.get(key)}`")
    for marketplace, refresh in (("ozon", health.get("ozon_refresh", {})), ("wb", health.get("wb_refresh", {}))):
        if isinstance(refresh, dict):
            lines.append(
                f"- `{marketplace}_refresh`: status `{refresh.get('status')}`, age `{refresh.get('age_seconds')}`, "
                f"interval `{refresh.get('interval_seconds')}`, source `{refresh.get('watchdog_source')}`"
            )

    lines.extend(["", "## Каталог", ""])
    if catalog:
        for key in ("status", "rows", "matched_rows", "ozon_only_rows", "wb_only_rows", "barcode_mismatch_rows", "modified_at"):
            if key in catalog:
                lines.append(f"- `{key}`: `{catalog[key]}`")
    else:
        lines.append("- catalog summary missing")
    if unified_catalog:
        lines.extend(["", "### Единый Каталог", ""])
        for key in (
            "status",
            "products",
            "with_internal_sku",
            "target_products",
            "target_with_internal_sku",
            "target_identification_complete",
            "both_marketplaces_products",
            "active_ozon_only_products",
            "active_wb_only_products",
            "active_ozon_products",
            "active_wb_products",
            "modified_at",
        ):
            if key in unified_catalog:
                lines.append(f"- `{key}`: `{unified_catalog[key]}`")

    lines.extend(["", "## Операции Маркетплейсов", ""])
    last_apply = actions.get("last_apply_summary", {})
    if last_apply:
        for key, value in last_apply.items():
            lines.append(f"- `{key}`: `{value}`")
    else:
        lines.append("- no apply summary found")

    lines.extend(["", "## Сессии", ""])
    for marketplace, snapshot in (sessions.get("sessions") or {}).items():
        refresh = snapshot.get("refresh", {}) if isinstance(snapshot, dict) else {}
        lines.append(
            f"- `{marketplace}`: refresh `{refresh.get('status')}`, age `{refresh.get('age_seconds')}`, "
            f"source `{refresh.get('watchdog_source')}`"
        )

    lines.extend(["", "## Pending-Пакеты", ""])
    if result["pending_packages"]:
        for package in result["pending_packages"]:
            line = f"- `{package['pending_id']}`: `{package['status']}`"
            if package.get("applied_run_id"):
                line += f", applied by `{package['applied_run_id']}`"
            lines.append(line)
    else:
        lines.append("- none")

    lines.extend(["", "## Действия На Сегодня", ""])
    for item in result["decision_items"]:
        lines.append(f"- {item}")

    lines.extend(["", "## Последние Запуски", ""])
    for run in result["recent_runs"]:
        status = run.get("overall_status") or run.get("mode") or "-"
        lines.append(f"- `{run['run_id']}`: `{status}`")

    lines.extend(["", "## Артефакты", ""])
    for key, value in sorted(result["artifacts"].items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _orders_line(marketplace: str, orders: dict[str, Any], period: str) -> str:
    if orders.get("status") != "ok":
        return f"{marketplace}: нет данных ({orders.get('status')}: {orders.get('error', orders.get('source'))})"
    data = orders.get(period, {})
    if marketplace == "Ozon":
        return (
            f"{marketplace}: {_format_int(data.get('ordered_units'))} шт., "
            f"{_format_money(data.get('revenue'))}"
        )
    return (
        f"{marketplace}: {_format_int(data.get('total_orders', data.get('active_orders')))} заказов, "
        f"{_format_money(data.get('amount'))}, отмены {_format_int(data.get('cancelled_orders'))}"
    )


def _sales_line(sales: dict[str, Any], period: str) -> str:
    if sales.get("status") != "ok":
        return f"WB выкупы: нет данных ({sales.get('status')}: {sales.get('error', sales.get('source'))})"
    data = sales.get(period, {})
    return (
        f"WB выкупы: {_format_int(data.get('sales_rows'))} продаж, "
        f"{_format_money(data.get('sales_amount'))}; возвраты {_format_int(data.get('return_rows'))}"
    )


def _stock_line(marketplace: str, stocks: dict[str, Any]) -> str:
    if stocks.get("status") not in {"ok", "warning"}:
        return f"{marketplace}: нет данных ({stocks.get('status')}: {stocks.get('error', stocks.get('source'))})"
    if marketplace == "Ozon":
        return (
            f"{marketplace}: остаток {_format_int(stocks.get('present_total'))} шт., "
            f"ноль {_format_int(stocks.get('out_of_stock_count'))}, "
            f"низкий остаток {_format_int(stocks.get('low_stock_count'))}"
        )
    return (
        f"{marketplace}: остаток {_format_int(stocks.get('quantity_total'))} шт. по строкам WB, "
        f"низкий остаток {_format_int(stocks.get('low_stock_count'))}, "
        f"нет строки в источнике остатков {_format_int(stocks.get('missing_in_stock_source_count'))}"
    )


def _append_top_skus(lines: list[str], title: str, rows: list[dict[str, Any]], count_key: str) -> None:
    if not rows:
        return
    lines.extend(["", title, ""])
    for row in rows[:5]:
        lines.append(f"- `{row.get('sku')}`: {_format_int(row.get(count_key))}")


def _append_low_stock(lines: list[str], marketplace: str, rows: list[dict[str, Any]], qty_key: str) -> None:
    if not rows:
        return
    lines.extend(["", f"### Низкий Остаток {marketplace}", ""])
    for row in rows[:10]:
        title = f" - {row.get('title')}" if row.get("title") else ""
        lines.append(f"- `{row.get('sku')}`: {_format_int(row.get(qty_key))} шт.{title}")


def _write_seller_v2_report(path: Path, result: dict[str, Any]) -> None:
    ensure_dir(path.parent)
    business = result["business"]
    ozon = business.get("ozon", {})
    wb = business.get("wb", {})
    periods = business.get("periods", {})
    actions = result["actions"]
    health = result["project_health"]
    catalog = result["catalog"]
    unified_catalog = result.get("unified_catalog", {})

    ozon_orders = ozon.get("orders", {})
    wb_orders = wb.get("orders", {})
    wb_sales = wb.get("sales", {})
    ozon_stocks = ozon.get("stocks", {})
    wb_stocks = wb.get("stocks", {})
    communications = wb.get("communications", {})
    last_apply = actions.get("last_apply_summary", {})

    lines = [
        "# Vital Shevron Утренний Отчет v2",
        "",
        f"Дата отчета: `{periods.get('today', '')}`",
        f"Время запуска: `{result['started_at']}`",
        f"Run ID: `{result['run_id']}`",
        f"Статус: `{result['overall_status']}`",
        "",
        "## Что Важно Сегодня",
        "",
        f"- Вчера ({periods.get('yesterday')}): {_orders_line('Ozon', ozon_orders, 'yesterday')}; {_orders_line('WB', wb_orders, 'yesterday')}.",
        f"- Сегодня ({periods.get('today')}): {_orders_line('Ozon', ozon_orders, 'today')}; {_orders_line('WB', wb_orders, 'today')}.",
        f"- {_sales_line(wb_sales, 'yesterday')} за вчера.",
        f"- Остатки: {_stock_line('Ozon', ozon_stocks)}; {_stock_line('WB', wb_stocks)}.",
    ]

    if communications.get("status") == "ok":
        lines.append(
            "- WB обращения: "
            f"неотвеченные отзывы {_format_int(communications.get('unanswered_feedbacks'))}, "
            f"неотвеченные вопросы {_format_int(communications.get('unanswered_questions'))}."
        )
    else:
        lines.append(
            "- WB обращения: нет данных "
            f"({communications.get('status')}: {communications.get('error', communications.get('source'))})."
        )

    if last_apply:
        lines.append(
            "- Последнее применение акций: "
            f"`{last_apply.get('run_id')}` статус `{last_apply.get('overall_status')}`, "
            f"Ozon активировано {_format_int(last_apply.get('ozon_activate_rows'))}, "
            f"WB строк {_format_int(last_apply.get('wb_payload_rows'))}."
        )
    else:
        lines.append("- Последнее применение акций: данных нет.")

    lines.extend(["", "## Действия На Сегодня", ""])
    for item in result["decision_items"]:
        lines.append(f"- {item}")
    if wb_stocks.get("status") == "warning":
        lines.append("- Перевести WB-остатки с legacy endpoint на warehouse remains report до 2026-06-23.")

    lines.extend(["", "## Продажи И Заказы", ""])
    lines.append(f"- Ozon вчера: {_orders_line('Ozon', ozon_orders, 'yesterday')}.")
    lines.append(f"- Ozon сегодня: {_orders_line('Ozon', ozon_orders, 'today')}.")
    lines.append(f"- WB заказы вчера: {_orders_line('WB', wb_orders, 'yesterday')}.")
    lines.append(f"- WB заказы сегодня: {_orders_line('WB', wb_orders, 'today')}.")
    lines.append(f"- {_sales_line(wb_sales, 'yesterday')} за вчера.")
    lines.append(f"- {_sales_line(wb_sales, 'today')} сегодня.")

    if wb_orders.get("status") == "ok":
        _append_top_skus(lines, "### Топ WB Артикулов По Заказам Сегодня", wb_orders.get("today", {}).get("top_skus", []), "orders")

    lines.extend(["", "## Остатки", ""])
    lines.append(f"- {_stock_line('Ozon', ozon_stocks)}.")
    lines.append(f"- {_stock_line('WB', wb_stocks)}.")
    _append_low_stock(lines, "Ozon", ozon_stocks.get("low_stock_sample", []), "present")
    _append_low_stock(lines, "WB", wb_stocks.get("low_stock_sample", []), "quantity")

    if wb_stocks.get("missing_in_stock_source_sample"):
        lines.extend(["", "### WB Артикулы Без Строки В Текущем Источнике Остатков", ""])
        for sku in wb_stocks["missing_in_stock_source_sample"][:10]:
            lines.append(f"- `{sku}`")

    lines.extend(["", "## Покупатели", ""])
    if communications.get("status") == "ok":
        lines.append(f"- WB неотвеченные отзывы: {_format_int(communications.get('unanswered_feedbacks'))}.")
        lines.append(f"- WB неотвеченные вопросы: {_format_int(communications.get('unanswered_questions'))}.")
    else:
        lines.append(f"- WB обращения: {communications.get('status')} - {communications.get('error', communications.get('source'))}.")
    lines.append("- Ozon отзывы/вопросы: отдельный read-only счетчик через ЛК/API еще не подключен в v2.")

    lines.extend(["", "## Акции И Продвижение", ""])
    if last_apply:
        for key, value in last_apply.items():
            lines.append(f"- `{key}`: `{value}`")
    else:
        lines.append("- Последний apply не найден.")
    lines.append("- Расходы и эффективность рекламы пока не включены: нужен отдельный read-only блок Ozon Performance/WB ads.")

    lines.extend(["", "## Техническое Приложение", ""])
    for key in ("preflight_run_id", "preflight_status", "ozon_api", "ozon_performance_api", "wb_api", "sessions_status"):
        lines.append(f"- `{key}`: `{health.get(key)}`")
    lines.append(
        f"- Каталог: строк {_format_int(catalog.get('rows'))}, "
        f"matched {_format_int(catalog.get('matched_rows'))}, "
        f"Ozon-only {_format_int(catalog.get('ozon_only_rows'))}, WB-only {_format_int(catalog.get('wb_only_rows'))}."
    )
    lines.append(
        f"- Единый каталог: товаров {_format_int(unified_catalog.get('products'))}, "
        f"целевой ассортимент с internal_sku "
        f"{_format_int(unified_catalog.get('target_with_internal_sku'))}/"
        f"{_format_int(unified_catalog.get('target_products'))}, "
        f"пары Ozon+WB {_format_int(unified_catalog.get('both_marketplaces_products'))}, "
        f"только Ozon {_format_int(unified_catalog.get('active_ozon_only_products'))}, "
        f"только WB {_format_int(unified_catalog.get('active_wb_only_products'))}; "
        f"источник `{unified_catalog.get('path', 'не подтверждено')}`."
    )
    lines.append(f"- Business status: `{business.get('business_status')}`.")

    lines.extend(["", "## Артефакты", ""])
    for key, value in sorted(result["artifacts"].items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _source_issue(section: dict[str, Any]) -> str:
    status = str(section.get("status") or "")
    if status in {"ok", "warning"}:
        return "нет" if status == "ok" else str(section.get("source_note") or "warning")
    return str(section.get("error") or section.get("reason") or section.get("source") or "не подтверждено")


EXPENSE_LABELS = {
    "commission": "Комиссия/вознаграждение",
    "logistics": "Логистика",
    "returns_processing": "Возвраты/отмены/невыкупы",
    "acquiring": "Эквайринг",
    "advertising": "Реклама",
    "storage": "Хранение/размещение",
    "crossdocking": "Кросс-докинг",
    "stars_membership": "Звездные товары/промо",
    "marketplace_deductions_before_logistics": "Комиссия/удержание до логистики",
    "acceptance": "Приемка",
    "deductions": "Удержания",
    "penalties": "Штрафы",
    "cashback": "Кешбэк/корректировки",
    "payment_schedule": "Удержание по графику платежей",
    "acquiring": "Издержки на приём платежей",
    "negative_additional_payments": "Отрицательные дополнительные выплаты",
    "other_reconciliation": "Прочая сверка",
    "other": "Прочая сверка/корректировка",
}


def _expense_value(section: dict[str, Any], key: str) -> Any:
    expenses = section.get("expenses") if isinstance(section.get("expenses"), dict) else {}
    return expenses.get(key)


def _expense_breakdown_lines(label: str, section: dict[str, Any], keys: list[str]) -> list[str]:
    if section.get("status") != "ok":
        return [f"- {label}: не подтверждено ({_source_issue(section)})"]
    lines = [f"- {label}: всего расходов `{_format_money_precise(section.get('total_expenses'))}`"]
    for key in keys:
        value = _expense_value(section, key)
        if value in (None, "", 0, 0.0):
            continue
        lines.append(f"  - {EXPENSE_LABELS.get(key, key)}: `{_format_money_precise(value)}`")
    return lines


def _sample_sku_title(row: Any) -> str:
    if isinstance(row, dict):
        sku = str(row.get("sku") or "").strip()
        internal_sku = str(row.get("internal_sku") or "").strip()
        title = str(row.get("title") or "").strip()
        sku_label = f"`{sku}`"
        if internal_sku and internal_sku != sku:
            sku_label += f" / `{internal_sku}`"
        if title:
            return f"{sku_label} - {title}"
        return sku_label
    return f"`{row}`"


def _append_wb_stock_details(lines: list[str], wb_stocks: dict[str, Any]) -> None:
    if wb_stocks.get("status") not in {"ok", "warning"}:
        return

    zero_count = int(wb_stocks.get("zero_stock_count") or 0)
    zero_sample = wb_stocks.get("zero_stock_sample") or []
    missing_count = int(wb_stocks.get("missing_in_stock_source_count") or 0)
    missing_sample = wb_stocks.get("missing_in_stock_source_sample") or []

    lines.extend(["", "WB товары с нулевым или неподтвержденным остатком:", ""])
    lines.append(f"- Подтвержденный нулевой остаток в источнике WB: `{zero_count}` товаров.")
    if zero_sample:
        lines.append("- Примеры подтвержденного нуля:")
        for row in zero_sample[:10]:
            lines.append(f"  - {_sample_sku_title(row)}")
    lines.append(f"- Нет строки в источнике остатков WB: `{missing_count}` товаров. Это не доказанный ноль, но требует проверки.")
    if missing_sample:
        lines.append("- Примеры без строки в источнике:")
        for row in missing_sample[:10]:
            lines.append(f"  - {_sample_sku_title(row)}")


def _action_reason_line(label: str, details: dict[str, Any]) -> list[str]:
    if not details:
        return [f"- {label}: не подтверждено"]
    lines = [
        f"- {label}:",
        f"  - нулевой остаток: `{_metric_int(details.get('zero_stock'))}`",
        f"  - участие в акции выше порога: `{_metric_int(details.get('above_threshold'))}`",
        f"  - нет акций для этого товара: `{_metric_int(details.get('no_actions_for_product'))}`",
    ]
    if details.get("other"):
        lines.append(f"  - прочее: `{_metric_int(details.get('other'))}`")
    if details.get("missing_stock_source_count") is not None:
        lines.append(
            f"  - нет строки в источнике остатков WB: `{_metric_int(details.get('missing_stock_source_count'))}`; "
            "это вынесено отдельно и не приравнено к нулевому остатку"
        )
    return lines


def _append_action_samples(lines: list[str], label: str, details: dict[str, Any]) -> None:
    samples = details.get("samples") if isinstance(details.get("samples"), dict) else {}
    sample_labels = {
        "zero_stock": "нулевой остаток",
        "above_threshold": "выше порога",
        "no_actions_for_product": "нет акций для товара",
        "other": "прочее",
    }
    for key, rows in samples.items():
        if not rows:
            continue
        lines.append(f"- Примеры {label}, {sample_labels.get(key, key)}:")
        for row in rows[:5]:
            reason = str(row.get("reason") or "").strip()
            suffix = f" ({reason})" if reason else ""
            lines.append(f"  - {_sample_sku_title(row)}{suffix}")


def _write_seller_v3_report(path: Path, result: dict[str, Any]) -> None:
    ensure_dir(path.parent)
    business = result["business"]
    ozon = business.get("ozon", {})
    wb = business.get("wb", {})
    periods = business.get("periods", {})
    health = result["project_health"]
    unified_catalog = result.get("unified_catalog", {})
    actions_v3 = result.get("actions_v3", {})
    supplies_v3 = result.get("supplies_v3", {})

    ozon_orders = ozon.get("orders", {})
    ozon_buyouts = ozon.get("finance_buyouts", {})
    ozon_expenses = ozon.get("finance_expenses", {})
    ozon_fbo = ozon.get("fbo_postings", {})
    wb_orders = wb.get("orders", {})
    wb_sales = wb.get("sales", {})
    wb_expenses = wb.get("finance_expenses", {})
    ozon_stocks = ozon.get("stocks", {})
    wb_stocks = wb.get("stocks", {})
    ozon_communications = ozon.get("communications", {})
    wb_communications = wb.get("communications", {})
    yesterday = periods.get("yesterday", "")

    ozon_orders_day = ozon_orders.get("yesterday", {}) if ozon_orders.get("status") == "ok" else {}
    ozon_feedbacks_day = ozon_communications.get("yesterday_feedbacks", {}) if ozon_communications.get("status") in {"ok", "warning"} else {}
    ozon_questions_day = ozon_communications.get("yesterday_questions", {}) if ozon_communications.get("status") in {"ok", "warning"} else {}
    wb_orders_day = wb_orders.get("yesterday", {}) if wb_orders.get("status") == "ok" else {}
    wb_sales_day = wb_sales.get("yesterday", {}) if wb_sales.get("status") == "ok" else {}
    wb_feedbacks_day = wb_communications.get("yesterday_feedbacks", {}) if wb_communications.get("status") == "ok" else {}
    wb_questions_day = wb_communications.get("yesterday_questions", {}) if wb_communications.get("status") == "ok" else {}

    ozon_actions = actions_v3.get("ozon", {})
    wb_actions = actions_v3.get("wb", {})

    source_issues = []
    for label, section in (
        ("Ozon заказы", ozon_orders),
        ("Ozon выкупы", ozon_buyouts),
        ("Ozon расходы", ozon_expenses),
        ("Ozon FBO отмены", ozon_fbo),
        ("Ozon отзывы/вопросы", ozon_communications),
        ("Ozon остатки", ozon_stocks),
        ("WB заказы", wb_orders),
        ("WB выкупы", wb_sales),
        ("WB расходы", wb_expenses),
        ("WB остатки", wb_stocks),
        ("Ozon поставки", supplies_v3.get("ozon", {})),
        ("WB поставки", supplies_v3.get("wb", {})),
    ):
        issue = _source_issue(section)
        if issue != "нет":
            source_issues.append(f"{label}: {issue}")

    lines = [
        "# Vital Shevron Ежедневный Отчет v3",
        "",
        f"Итог: статус `{result['overall_status']}`. Отчет построен за одну дату `{yesterday}`; изменения в магазинах не выполнялись.",
        "",
        f"Дата отчета: `{periods.get('today', '')}`",
        f"Период данных: `{yesterday}`, 00:00-23:59 MSK",
        "Режим: `read-only`",
        f"Run ID: `{result['run_id']}`",
        "",
        "## Статус систем",
        "",
        f"- Ozon API: `{health.get('ozon_api')}`",
        f"- WB API: `{health.get('wb_api')}`",
        f"- Ozon ЛК: `{(health.get('ozon_refresh') or {}).get('status', 'not_checked')}`",
        f"- WB ЛК: `{(health.get('wb_refresh') or {}).get('status', 'not_checked')}`",
        "",
        "## Единый Каталог",
        "",
        "| Метрика | Значение |",
        "| --- | ---: |",
        f"| Статус | {unified_catalog.get('status', 'не подтверждено')} |",
        f"| Товаров всего | {_metric_int(unified_catalog.get('products'))} |",
        f"| Целевой ассортимент идентифицирован | {_metric_bool(unified_catalog.get('target_identification_complete'))} |",
        f"| Целевых товарных записей с внутренним артикулом | {_metric_int(unified_catalog.get('target_with_internal_sku'))} из {_metric_int(unified_catalog.get('target_products'))} |",
        f"| Связанные пары Ozon+WB | {_metric_int(unified_catalog.get('both_marketplaces_products'))} |",
        f"| Только Ozon, активные | {_metric_int(unified_catalog.get('active_ozon_only_products'))} |",
        f"| Только WB, активные | {_metric_int(unified_catalog.get('active_wb_only_products'))} |",
        f"| Из них вне целевого ассортимента | {_metric_int(unified_catalog.get('non_target_or_deferred_products'))} |",
        f"| Активны на Ozon | {_metric_int(unified_catalog.get('active_ozon_products'))} |",
        f"| Активны на WB | {_metric_int(unified_catalog.get('active_wb_products'))} |",
        f"| Обновлен | {unified_catalog.get('modified_at', 'не подтверждено')} |",
        "",
        "## Заказы, Выкупы, Отмены За Период",
        "",
        "| Метрика | Ozon | WB |",
        "| --- | ---: | ---: |",
        f"| Заказы, шт. | {_metric_int(ozon_orders_day.get('ordered_units'))} | {_metric_int(wb_orders_day.get('total_orders', wb_orders_day.get('active_orders')))} |",
        f"| Заказы, ₽ | {_metric_money(ozon_orders_day.get('revenue'))} | {_metric_money(wb_orders_day.get('amount'))} |",
        f"| Выкупы, шт. | {_metric_int(ozon_buyouts.get('buyout_units')) if ozon_buyouts.get('status') == 'ok' else 'не подтверждено'} | {_metric_int(wb_sales_day.get('sales_rows')) if wb_sales.get('status') == 'ok' else 'не подтверждено'} |",
        f"| Выкупы, ₽ | {_metric_money(ozon_buyouts.get('buyout_amount')) if ozon_buyouts.get('status') == 'ok' else 'не подтверждено'} | {_metric_money(wb_sales_day.get('sales_amount')) if wb_sales.get('status') == 'ok' else 'не подтверждено'} |",
        f"| Отмены, шт. | {_metric_int(ozon_fbo.get('cancelled_units')) if ozon_fbo.get('status') in {'ok', 'warning'} else 'не подтверждено'} | {_metric_int(wb_orders_day.get('cancelled_orders'))} |",
        "",
        "## Деньги И Расходы За Период",
        "",
        "| Метрика | Ozon | WB |",
        "| --- | ---: | ---: |",
        f"| Выкуплено/реализовано, ₽ | {_metric_money(ozon_expenses.get('gross_amount')) if ozon_expenses.get('status') == 'ok' else 'не подтверждено'} | {_metric_money(wb_expenses.get('gross_amount')) if wb_expenses.get('status') == 'ok' else 'не подтверждено'} |",
        f"| Расходы всего, ₽ | {_metric_money(ozon_expenses.get('total_expenses')) if ozon_expenses.get('status') == 'ok' else 'не подтверждено'} | {_metric_money(wb_expenses.get('total_expenses')) if wb_expenses.get('status') == 'ok' else 'не подтверждено'} |",
        f"| После текущих расходов, ₽ | {_metric_money(ozon_expenses.get('net_amount')) if ozon_expenses.get('status') == 'ok' else 'не подтверждено'} | {_metric_money(wb_expenses.get('net_after_expenses')) if wb_expenses.get('status') == 'ok' else 'не подтверждено'} |",
        f"| WB: корректировки/зачисления, ₽ | — | {_metric_money(wb_expenses.get('total_credits_and_adjustments')) if wb_expenses.get('status') == 'ok' else 'не подтверждено'} |",
        f"| WB: начисление с корректировками после рекламы, ₽ | — | {_metric_money(wb_expenses.get('cash_after_adjustments_and_ads')) if wb_expenses.get('status') == 'ok' else 'не подтверждено'} |",
        f"| Источник | {ozon_expenses.get('source', 'не подтверждено')} | {wb_expenses.get('source', 'не подтверждено')} |",
        "",
        "Примечания к денежным показателям:",
        "",
        "- Заказы и выкупы относятся к разным событиям за выбранную дату: заказ мог быть создан раньше, а выкуплен в отчетный день.",
        "- Для WB сумма выкупов в операционном блоке рассчитана по `priceWithDisc` (цена покупателя), "
        "а реализовано в финансовом блоке — по `retailAmountSum`/`finishedPrice` из финансового отчета. "
        "Эти суммы имеют разную расчетную базу и не должны совпадать.",
        "",
        "Расшифровка расходов:",
        "",
        *_expense_breakdown_lines(
            "Ozon",
            ozon_expenses,
            ["commission", "logistics", "returns_processing", "advertising", "storage", "crossdocking", "acquiring", "stars_membership", "other"],
        ),
        *_expense_breakdown_lines(
            "WB",
            wb_expenses,
            [
                "marketplace_deductions_before_logistics",
                "logistics",
                "storage",
                "acceptance",
                "deductions",
                "penalties",
                "cashback",
                "payment_schedule",
                "acquiring",
                "negative_additional_payments",
                "advertising",
            ],
        ),
        "",
        "## Отзывы И Вопросы",
        "",
        "| Метрика | Ozon | WB |",
        "| --- | ---: | ---: |",
        f"| Отзывы за период 00:00-23:59 | {_metric_int(ozon_feedbacks_day.get('count')) if ozon_feedbacks_day.get('status') == 'ok' else 'не подтверждено'} | {_metric_int(wb_feedbacks_day.get('count')) if wb_feedbacks_day.get('status') == 'ok' else 'не подтверждено'} |",
        f"| Вопросы за период 00:00-23:59 | {_metric_int(ozon_questions_day.get('count')) if ozon_questions_day.get('status') == 'ok' else 'не подтверждено'} | {_metric_int(wb_questions_day.get('count')) if wb_questions_day.get('status') == 'ok' else 'не подтверждено'} |",
        f"| Требуют внимания сейчас: отзывы | {_metric_int(ozon_communications.get('unanswered_feedbacks')) if ozon_communications.get('status') in {'ok', 'warning'} else 'не подтверждено'} | {_metric_int(wb_communications.get('unanswered_feedbacks')) if wb_communications.get('status') == 'ok' else 'не подтверждено'} |",
        f"| Требуют внимания сейчас: вопросы | {_metric_int(ozon_communications.get('unanswered_questions')) if ozon_communications.get('status') in {'ok', 'warning'} else 'не подтверждено'} | {_metric_int(wb_communications.get('unanswered_questions')) if wb_communications.get('status') == 'ok' else 'не подтверждено'} |",
        "",
        *(
            [
                "Ограничение Ozon: официальный API отзывов и вопросов вернул ошибку; "
                "показаны только видимые непросмотренные отзывы и новые вопросы из LK/CDP. "
                "Количество за период может быть ниже фактического.",
                "",
            ]
            if ozon_communications.get("status") == "warning"
            else []
        ),
        "## Остатки На Текущий Момент",
        "",
        "| Метрика | Ozon | WB |",
        "| --- | ---: | ---: |",
        f"| Остаток всего, шт. | {_metric_int(ozon_stocks.get('present_total'))} | {_metric_int(wb_stocks.get('quantity_total'))} |",
        f"| Нулевой остаток, товаров | {_metric_int(ozon_stocks.get('out_of_stock_count'))} | {_metric_int(wb_stocks.get('zero_stock_count'))} |",
        f"| Низкий остаток, товаров | {_metric_int(ozon_stocks.get('low_stock_count'))} | {_metric_int(wb_stocks.get('low_stock_count'))} |",
        f"| WB-артикулы без строки в источнике остатков | - | {_metric_int(wb_stocks.get('missing_in_stock_source_count'))} |",
        f"| Проблемы источника | {_source_issue(ozon_stocks)} | {_source_issue(wb_stocks)} |",
    ]

    _append_wb_stock_details(lines, wb_stocks)

    lines.extend([
        "",
        "## Акции На Текущий Момент",
        "",
        "| Метрика | Ozon | WB |",
        "| --- | ---: | ---: |",
        f"| Активные акции, шт. | {_metric_int(ozon_actions.get('active_actions'))} | {_metric_int(wb_actions.get('active_actions'))} |",
        f"| Товаров в расчете | {_metric_int((ozon_actions.get('products_in_actions') or 0) + (ozon_actions.get('products_not_in_actions') or 0)) if ozon_actions.get('products_in_actions') is not None and ozon_actions.get('products_not_in_actions') is not None else 'не подтверждено'} | {_metric_int(wb_actions.get('products_total'))} |",
        f"| Фактически участвует сейчас | {_metric_int(ozon_actions.get('products_in_actions'))} | {_metric_int(wb_actions.get('products_in_actions'))} |",
        f"| Фактически не участвует сейчас | {_metric_int(ozon_actions.get('products_not_in_actions'))} | {_metric_int(wb_actions.get('products_not_in_actions'))} |",
        f"| Акция доступна, но товар не участвует | {_metric_int(ozon_actions.get('available_but_not_participating'))} | {_metric_int(wb_actions.get('available_but_not_participating'))} |",
        f"| Нет доступной активной акции / остается вне | {_metric_int(ozon_actions.get('outside_active_promos'))} | {_metric_int(wb_actions.get('outside_active_promos'))} |",
        f"| После применения текущего dry-run участвовало бы | {_metric_int(ozon_actions.get('products_after_plan'))} | {_metric_int(wb_actions.get('products_after_plan'))} |",
        f"| После применения текущего dry-run не участвовало бы | {_metric_int(ozon_actions.get('products_not_in_actions_after_plan'))} | {_metric_int(wb_actions.get('products_not_in_actions_after_plan'))} |",
        f"| Основные причины неучастия | {ozon_actions.get('not_in_action_reasons', 'не подтверждено')} | {wb_actions.get('not_in_action_reasons', 'не подтверждено')} |",
        "",
        f"- WB, скидки фактически участвующих товаров: {_discount_distribution(wb_actions.get('current_discount_distribution'))}.",
        "- Строки «после применения dry-run» являются расчетом; этот отчет ничего не меняет в магазинах.",
        "",
        "Расшифровка причин по акциям:",
        "",
        *_action_reason_line("Ozon", ozon_actions.get("reason_details", {})),
        *_action_reason_line("WB", wb_actions.get("reason_details", {})),
    ])
    _append_action_samples(lines, "Ozon", ozon_actions.get("reason_details", {}))
    _append_action_samples(lines, "WB", wb_actions.get("reason_details", {}))

    lines.extend([
        "",
        "## Поставки На Текущий Момент",
        "",
        "| Метрика | Ozon | WB |",
        "| --- | ---: | ---: |",
        f"| Есть текущие поставки | {_metric_bool((supplies_v3.get('ozon') or {}).get('has_current'))} | {_metric_bool((supplies_v3.get('wb') or {}).get('has_current'))} |",
        f"| Всего текущих | {_metric_int((supplies_v3.get('ozon') or {}).get('current_total'))} | {_metric_int((supplies_v3.get('wb') or {}).get('current_total'))} |",
        f"| Формируется | {_metric_int((supplies_v3.get('ozon') or {}).get('forming'))} | {_metric_int((supplies_v3.get('wb') or {}).get('forming'))} |",
        f"| Готова к отгрузке | {_metric_int((supplies_v3.get('ozon') or {}).get('ready_to_ship'))} | {_metric_int((supplies_v3.get('wb') or {}).get('ready_to_ship'))} |",
        f"| На точке отгрузки | {_metric_int((supplies_v3.get('ozon') or {}).get('at_dropoff'))} | {_metric_int((supplies_v3.get('wb') or {}).get('at_dropoff'))} |",
        f"| В пути | {_metric_int((supplies_v3.get('ozon') or {}).get('in_transit'))} | {_metric_int((supplies_v3.get('wb') or {}).get('in_transit'))} |",
        f"| На складе приемки | {_metric_int((supplies_v3.get('ozon') or {}).get('at_acceptance_warehouse'))} | {_metric_int((supplies_v3.get('wb') or {}).get('at_acceptance_warehouse'))} |",
        f"| Приемка/подтверждение | {_metric_int((supplies_v3.get('ozon') or {}).get('acceptance'))} | {_metric_int((supplies_v3.get('wb') or {}).get('acceptance'))} |",
        "",
        "## Операционные Риски",
        "",
        f"- открытые пакеты на согласование: `{len(result['pending_packages'])}`",
        f"- ошибки/блокировки: `{len(source_issues)}` источников без полных данных",
    ])

    if source_issues:
        lines.append("- ограничения источников:")
        for issue in source_issues:
            lines.append(f"  - {issue}")

    lines.extend(["", "## Что Важно Сегодня", ""])
    for item in result["executive_summary"]:
        lines.append(f"- {item}")

    lines.extend(["", "## Следующий Шаг", ""])
    for item in result["decision_items"]:
        lines.append(f"- {item}")
    if not result["decision_items"]:
        lines.append("- Критичных действий по отчету нет.")

    lines.extend(["", "## Артефакты", ""])
    for key, value in sorted(result["artifacts"].items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def run_daily_morning_report(
    *,
    credentials: AppCredentials,
    data_dir: Path = Path("data"),
    run_id: str | None = None,
    refresh_preflight: bool = True,
    seller_v2: bool = False,
    seller_v3: bool = False,
) -> dict[str, Any]:
    started_at = datetime.now()
    default_prefix = "daily_morning_report_v3" if seller_v3 else "daily_morning_report_v2" if seller_v2 else "daily_morning_report"
    run_id = run_id or f"{default_prefix}_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)

    preflight = _latest_preflight(credentials=credentials, data_dir=data_dir, refresh_preflight=refresh_preflight)
    sessions = combined_session_status()
    catalog = _catalog_section(preflight)
    unified_catalog = _unified_catalog_section(data_dir)
    pending_packages = _pending_packages(data_dir)
    recommendations = _recommendations_summary(data_dir)
    actions = _actions_section(data_dir)
    recent_runs = latest_run_dirs(data_dir, limit=12)

    project_health = _project_health(preflight, sessions)
    business = (
        _collect_business_snapshot(
            credentials=credentials,
            data_dir=data_dir,
            include_period_communications=seller_v3,
            run_dir=run_dir,
        )
        if seller_v2 or seller_v3
        else {}
    )
    actions_v3 = _actions_v3(actions, business=business) if seller_v3 else {}
    supplies_v3 = _supplies_v3(credentials) if seller_v3 else {}
    business_status = business.get("business_status") if seller_v2 or seller_v3 else "ok"
    decision_items = _decision_items(
        preflight=preflight,
        pending_packages=pending_packages,
        recommendations=recommendations,
    )
    preflight_status = project_health["preflight_status"]
    overall_status = "ok" if preflight_status == "ok" and sessions["overall_status"] == "ok" else "warning"
    if preflight_status == "error" or sessions["overall_status"] == "error":
        overall_status = "error"
    if (seller_v2 or seller_v3) and overall_status == "ok" and business_status != "ok":
        overall_status = "warning"
    if seller_v3 and overall_status == "ok":
        supply_statuses = {
            str(section.get("status") or "")
            for section in supplies_v3.values()
            if isinstance(section, dict)
        }
        if supply_statuses - {"ok"}:
            overall_status = "warning"
    if seller_v3:
        periods = business.get("periods", {})
        ozon_orders = business.get("ozon", {}).get("orders", {})
        wb_orders = business.get("wb", {}).get("orders", {})
        executive_summary = [
            f"Период отчета: {periods.get('yesterday')} 00:00-23:59 MSK.",
            f"Заказы за период: {_orders_line('Ozon', ozon_orders, 'yesterday')}; {_orders_line('WB', wb_orders, 'yesterday')}.",
            (
                "Единый каталог: "
                f"{unified_catalog.get('products', 'н/д')} товаров, "
                f"целевой ассортимент с internal_sku "
                f"{unified_catalog.get('target_with_internal_sku', 'н/д')}/"
                f"{unified_catalog.get('target_products', 'н/д')}, "
                f"пары Ozon+WB {unified_catalog.get('both_marketplaces_products', 'н/д')}, "
                f"только Ozon {unified_catalog.get('active_ozon_only_products', 'н/д')}, "
                f"только WB {unified_catalog.get('active_wb_only_products', 'н/д')}."
            ),
            "V3 уже показывает Ozon/WB рядом; неподключенные источники отмечены как `не подтверждено`.",
        ]
    elif seller_v2:
        ozon_orders = business.get("ozon", {}).get("orders", {})
        wb_orders = business.get("wb", {}).get("orders", {})
        executive_summary = [
            f"Селлерский статус: {overall_status}.",
            f"Заказы вчера: {_orders_line('Ozon', ozon_orders, 'yesterday')}; {_orders_line('WB', wb_orders, 'yesterday')}.",
            f"Заказы сегодня: {_orders_line('Ozon', ozon_orders, 'today')}; {_orders_line('WB', wb_orders, 'today')}.",
            f"Каталог: {catalog.get('rows', 'unknown')} строк, matched {catalog.get('matched_rows', 'unknown')}.",
            f"Единый каталог: {unified_catalog.get('products', 'unknown')} товаров, пары Ozon+WB {unified_catalog.get('both_marketplaces_products', 'unknown')}.",
        ]
    else:
        executive_summary = [
            f"Состояние проекта: {overall_status}.",
            f"Preflight: {preflight_status}.",
            f"Сессии: {sessions['overall_status']}.",
            f"Каталог: {catalog.get('rows', 'unknown')} строк, matched {catalog.get('matched_rows', 'unknown')}.",
            f"Единый каталог: {unified_catalog.get('products', 'unknown')} товаров, пары Ozon+WB {unified_catalog.get('both_marketplaces_products', 'unknown')}.",
            f"Открытые рекомендации: {len(recommendations.get('open_items', []))}.",
        ]
        if actions.get("last_apply_summary"):
            last_apply = actions["last_apply_summary"]
            executive_summary.append(
                f"Последний apply акций: {last_apply.get('run_id')} статус {last_apply.get('overall_status')}."
            )

    report_name = "daily_morning_report_v3.md" if seller_v3 else "daily_morning_report_v2.md" if seller_v2 else "daily_morning_report.md"
    artifacts = {
        "run_dir": str(run_dir),
        "summary": str(run_dir / "summary.json"),
        "report": str(run_dir / report_name),
        "run_manifest": str(run_dir / "manifest.json"),
    }
    result = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "read_only",
        "report_version": "seller_v3" if seller_v3 else "seller_v2" if seller_v2 else "technical_v1",
        "refresh_preflight": refresh_preflight,
        "overall_status": overall_status,
        "executive_summary": executive_summary,
        "business": business,
        "project_health": project_health,
        "catalog": catalog,
        "unified_catalog": unified_catalog,
        "actions": actions,
        "actions_v3": actions_v3,
        "supplies_v3": supplies_v3,
        "sessions": sessions,
        "pending_packages": pending_packages,
        "recommendations": recommendations,
        "decision_items": decision_items,
        "recent_runs": recent_runs,
        "artifacts": artifacts,
    }
    write_json(run_dir / "summary.json", result)
    if seller_v3:
        _write_seller_v3_report(run_dir / report_name, result)
    elif seller_v2:
        _write_seller_v2_report(run_dir / report_name, result)
    else:
        _write_report(run_dir / report_name, result)
    write_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        manifest=manifest_from_summary(
            summary=result,
            task="daily-morning-report",
            mode="read_only",
            risk="low",
            marketplaces=["ozon", "wb"],
            inputs={
                "refresh_preflight": refresh_preflight,
                "seller_v2": seller_v2,
                "seller_v3": seller_v3,
            },
            source_run_ids=[str(preflight.get("run_id"))] if isinstance(preflight, dict) and preflight.get("run_id") else [],
        ),
    )
    return result
