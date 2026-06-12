from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
import json
from pathlib import Path
from typing import Any

from takterra_agent.config import AppCredentials
from takterra_agent.marketplaces.ozon.adapter import OzonSellerAdapter
from takterra_agent.marketplaces.wb.communications_adapter import WbCommunicationsAdapter
from takterra_agent.marketplaces.wb.statistics_adapter import WbStatisticsAdapter
from takterra_agent.reports.writer import ensure_dir, write_json
from takterra_agent.sessions.state import combined_session_status
from takterra_agent.tasks.status_preflight import run_status_preflight


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


def _safe_read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _safe_error(exc: BaseException) -> str:
    raw = str(exc).replace("\n", " ").replace("\r", " ")
    if "failed (429)" in raw or '"status": 429' in raw:
        return "HTTP 429: rate limit / too many requests"
    if "failed (401)" in raw or '"status": 401' in raw:
        return "HTTP 401: unauthorized"
    if "failed (403)" in raw or '"status": 403' in raw:
        return "HTTP 403: forbidden"
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
    pending_dir = data_dir / "pending"
    if not pending_dir.exists():
        return []
    applied_by_pending: dict[str, str] = {}
    for run in latest_run_dirs(data_dir, prefixes=["actions_apply_"], limit=50):
        summary = _safe_read_json(Path(run["summary_path"])) if run["summary_path"] else None
        if isinstance(summary, dict) and summary.get("pending_id") and summary.get("overall_status") == "ok":
            applied_by_pending[str(summary["pending_id"])] = str(summary.get("run_id") or run["run_id"])

    packages: list[dict[str, Any]] = []
    for path in sorted((item for item in pending_dir.iterdir() if item.is_dir()), key=lambda item: item.name):
        manifest_path = path / "manifest.json"
        manifest = _safe_read_json(manifest_path) if manifest_path.exists() else {}
        pending_id = str(manifest.get("pending_id") or path.name) if isinstance(manifest, dict) else path.name
        applied_run_id = applied_by_pending.get(pending_id)
        packages.append(
            {
                "pending_id": pending_id,
                "path": str(path),
                "manifest": str(manifest_path) if manifest_path.exists() else "",
                "status": "applied" if applied_run_id else manifest.get("status", "unknown") if isinstance(manifest, dict) else "unknown",
                "created_at": manifest.get("created_at", "") if isinstance(manifest, dict) else "",
                "applied_run_id": applied_run_id or "",
            }
        )
    return packages


def _recommendations_summary(data_dir: Path) -> dict[str, Any]:
    path = data_dir / "planning" / "recommendations_index.md"
    if not path.exists():
        return {"path": str(path), "status_counts": {}, "open_items": []}

    counts: Counter[str] = Counter()
    open_items: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| REC-"):
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


def _master_catalog_rows(data_dir: Path) -> list[dict[str, Any]]:
    rows = _safe_read_json(data_dir / "catalog" / "processed" / "master_catalog.json")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


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


def _format_int(value: Any) -> str:
    if value is None:
        return "н/д"
    try:
        return f"{int(float(value)):,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(value)


def _source_error(source: str, exc: BaseException) -> dict[str, Any]:
    return {"status": "error", "source": source, "error": _safe_error(exc)}


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
            catalog_row = product_to_catalog.get(product_id, {})
            low_stock_sample.append(
                {
                    "sku": offer_id or catalog_row.get("master_sku") or product_id,
                    "title": catalog_row.get("title", ""),
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


def _summarize_wb_orders(rows: list[dict[str, Any]]) -> dict[str, Any]:
    active_rows = [row for row in rows if not _truthy(row.get("isCancel"))]
    amount = sum(_first_number(row, ("finishedPrice", "priceWithDisc", "totalPrice")) for row in active_rows)
    top_skus = Counter(str(row.get("supplierArticle") or row.get("vendorCode") or "").strip() for row in active_rows)
    top_skus.pop("", None)
    return {
        "total_rows": len(rows),
        "active_orders": len(active_rows),
        "cancelled_orders": len(rows) - len(active_rows),
        "amount": _money(amount),
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

    sales_amount = sum(_first_number(row, ("forPay", "finishedPrice", "priceWithDisc", "totalPrice")) for row in sale_rows)
    returns_amount = sum(_first_number(row, ("forPay", "finishedPrice", "priceWithDisc", "totalPrice")) for row in return_rows)
    return {
        "total_rows": len(rows),
        "sales_rows": len(sale_rows),
        "return_rows": len(return_rows),
        "sales_amount": _money(sales_amount),
        "returns_amount": _money(returns_amount),
        "net_amount_estimate": _money(sales_amount - returns_amount),
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
        str(row.get("wb_vendor_code") or row.get("master_sku") or "").strip()
        for row in catalog_rows
        if row.get("wb_vendor_code") or row.get("master_sku")
    }
    possible_missing = sorted(master_wb_codes - set(qty_by_sku))
    low_stock_sample = [
        {"sku": sku, "quantity": int(qty)}
        for sku, qty in sorted(qty_by_sku.items(), key=lambda item: (item[1], item[0]))
        if 0 < qty <= low_stock_threshold
    ][:10]
    return {
        "status": "warning",
        "source": "/api/v1/supplier/stocks",
        "source_note": "Legacy WB stock endpoint is deprecated and scheduled for removal on 2026-06-23.",
        "warehouse_rows": len(stock_rows),
        "sku_rows": len(qty_by_sku),
        "quantity_total": int(sum(qty_by_sku.values())),
        "possible_zero_or_missing_count": len(possible_missing),
        "possible_zero_or_missing_sample": possible_missing[:10],
        "low_stock_threshold": low_stock_threshold,
        "low_stock_count": sum(1 for qty in qty_by_sku.values() if 0 < qty <= low_stock_threshold),
        "low_stock_sample": low_stock_sample,
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
) -> dict[str, Any]:
    report_day = today or date.today()
    periods = {
        "yesterday": (report_day - timedelta(days=1)).isoformat(),
        "today": report_day.isoformat(),
    }
    catalog_rows = _master_catalog_rows(data_dir)
    business: dict[str, Any] = {
        "periods": periods,
        "catalog_rows": len(catalog_rows),
        "ozon": {},
        "wb": {},
    }

    if credentials.ozon_seller:
        ozon = OzonSellerAdapter(credentials.ozon_seller)
        metrics = ["revenue", "ordered_units"]
        try:
            business["ozon"]["orders"] = {
                "status": "ok",
                "source": "/v1/analytics/data",
                "yesterday": _extract_ozon_analytics(
                    ozon.fetch_analytics_data(
                        date_from=periods["yesterday"],
                        date_to=periods["yesterday"],
                        metrics=metrics,
                        dimensions=["day"],
                    ),
                    metrics,
                ),
                "today": _extract_ozon_analytics(
                    ozon.fetch_analytics_data(
                        date_from=periods["today"],
                        date_to=periods["today"],
                        metrics=metrics,
                        dimensions=["day"],
                    ),
                    metrics,
                ),
            }
        except Exception as exc:  # noqa: BLE001 - report must survive API permission/schema errors
            business["ozon"]["orders"] = _source_error("/v1/analytics/data", exc)

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
    else:
        business["ozon"]["orders"] = {"status": "skipped", "source": "/v1/analytics/data", "error": "missing credentials"}
        business["ozon"]["stocks"] = {"status": "skipped", "source": "/v4/product/info/stocks", "error": "missing credentials"}

    if credentials.wb:
        wb_stats = WbStatisticsAdapter(credentials.wb)
        wb_communications = WbCommunicationsAdapter(credentials.wb)
        try:
            business["wb"]["orders"] = {
                "status": "ok",
                "source": "/api/v1/supplier/orders",
                "source_note": "WB updates this data every 30 minutes.",
                "yesterday": _summarize_wb_orders(wb_stats.fetch_orders(date_from=periods["yesterday"], flag=1)),
                "today": _summarize_wb_orders(wb_stats.fetch_orders(date_from=periods["today"], flag=1)),
            }
        except Exception as exc:  # noqa: BLE001
            business["wb"]["orders"] = _source_error("/api/v1/supplier/orders", exc)

        try:
            business["wb"]["sales"] = {
                "status": "ok",
                "source": "/api/v1/supplier/sales",
                "source_note": "Preliminary WB operational sales/returns data.",
                "yesterday": _summarize_wb_sales(wb_stats.fetch_sales(date_from=periods["yesterday"], flag=1)),
                "today": _summarize_wb_sales(wb_stats.fetch_sales(date_from=periods["today"], flag=1)),
            }
        except Exception as exc:  # noqa: BLE001
            business["wb"]["sales"] = _source_error("/api/v1/supplier/sales", exc)

        try:
            business["wb"]["stocks"] = _summarize_wb_stocks(
                stock_rows=wb_stats.fetch_stocks_legacy(date_from="2019-01-01"),
                catalog_rows=catalog_rows,
            )
        except Exception as exc:  # noqa: BLE001
            business["wb"]["stocks"] = _source_error("/api/v1/supplier/stocks", exc)

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
        except Exception as exc:  # noqa: BLE001
            business["wb"]["communications"] = _source_error("feedbacks-api.wildberries.ru", exc)
    else:
        business["wb"]["orders"] = {"status": "skipped", "source": "/api/v1/supplier/orders", "error": "missing credentials"}
        business["wb"]["sales"] = {"status": "skipped", "source": "/api/v1/supplier/sales", "error": "missing credentials"}
        business["wb"]["stocks"] = {"status": "skipped", "source": "/api/v1/supplier/stocks", "error": "missing credentials"}
        business["wb"]["communications"] = {
            "status": "skipped",
            "source": "feedbacks-api.wildberries.ru",
            "error": "missing credentials",
        }

    business["business_status"] = _business_status(business)
    return business


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

    open_pending = [package for package in pending_packages if package.get("status") != "applied"]
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
        f"{marketplace}: {_format_int(data.get('active_orders'))} заказов, "
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
        f"возможно нет остатка/строки {_format_int(stocks.get('possible_zero_or_missing_count'))}"
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

    if wb_stocks.get("possible_zero_or_missing_sample"):
        lines.extend(["", "### WB Возможный Нулевой Остаток Или Нет Строки В Stock API", ""])
        for sku in wb_stocks["possible_zero_or_missing_sample"][:10]:
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
    lines.append(f"- Business status: `{business.get('business_status')}`.")

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
) -> dict[str, Any]:
    started_at = datetime.now()
    default_prefix = "daily_morning_report_v2" if seller_v2 else "daily_morning_report"
    run_id = run_id or f"{default_prefix}_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)

    preflight = _latest_preflight(credentials=credentials, data_dir=data_dir, refresh_preflight=refresh_preflight)
    sessions = combined_session_status()
    catalog = _catalog_section(preflight)
    pending_packages = _pending_packages(data_dir)
    recommendations = _recommendations_summary(data_dir)
    actions = _actions_section(data_dir)
    recent_runs = latest_run_dirs(data_dir, limit=12)

    project_health = _project_health(preflight, sessions)
    business = _collect_business_snapshot(credentials=credentials, data_dir=data_dir) if seller_v2 else {}
    business_status = business.get("business_status") if seller_v2 else "ok"
    decision_items = _decision_items(
        preflight=preflight,
        pending_packages=pending_packages,
        recommendations=recommendations,
    )
    preflight_status = project_health["preflight_status"]
    overall_status = "ok" if preflight_status == "ok" and sessions["overall_status"] == "ok" else "warning"
    if preflight_status == "error" or sessions["overall_status"] == "error":
        overall_status = "error"
    if seller_v2 and overall_status == "ok" and business_status != "ok":
        overall_status = "warning"

    if seller_v2:
        ozon_orders = business.get("ozon", {}).get("orders", {})
        wb_orders = business.get("wb", {}).get("orders", {})
        executive_summary = [
            f"Селлерский статус: {overall_status}.",
            f"Заказы вчера: {_orders_line('Ozon', ozon_orders, 'yesterday')}; {_orders_line('WB', wb_orders, 'yesterday')}.",
            f"Заказы сегодня: {_orders_line('Ozon', ozon_orders, 'today')}; {_orders_line('WB', wb_orders, 'today')}.",
            f"Каталог: {catalog.get('rows', 'unknown')} строк, matched {catalog.get('matched_rows', 'unknown')}.",
        ]
    else:
        executive_summary = [
            f"Состояние проекта: {overall_status}.",
            f"Preflight: {preflight_status}.",
            f"Сессии: {sessions['overall_status']}.",
            f"Каталог: {catalog.get('rows', 'unknown')} строк, matched {catalog.get('matched_rows', 'unknown')}.",
            f"Открытые рекомендации: {len(recommendations.get('open_items', []))}.",
        ]
        if actions.get("last_apply_summary"):
            last_apply = actions["last_apply_summary"]
            executive_summary.append(
                f"Последний apply акций: {last_apply.get('run_id')} статус {last_apply.get('overall_status')}."
            )

    report_name = "daily_morning_report_v2.md" if seller_v2 else "daily_morning_report.md"
    artifacts = {
        "run_dir": str(run_dir),
        "summary": str(run_dir / "summary.json"),
        "report": str(run_dir / report_name),
    }
    result = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "read_only",
        "report_version": "seller_v2" if seller_v2 else "technical_v1",
        "refresh_preflight": refresh_preflight,
        "overall_status": overall_status,
        "executive_summary": executive_summary,
        "business": business,
        "project_health": project_health,
        "catalog": catalog,
        "actions": actions,
        "sessions": sessions,
        "pending_packages": pending_packages,
        "recommendations": recommendations,
        "decision_items": decision_items,
        "recent_runs": recent_runs,
        "artifacts": artifacts,
    }
    write_json(run_dir / "summary.json", result)
    if seller_v2:
        _write_seller_v2_report(run_dir / report_name, result)
    else:
        _write_report(run_dir / report_name, result)
    return result
