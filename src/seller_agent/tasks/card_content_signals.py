from __future__ import annotations

import csv
from collections import Counter
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from seller_agent.catalog.loader import normalize_sku
from seller_agent.config import AppCredentials
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.marketplaces.ozon.adapter import OzonSellerAdapter
from seller_agent.marketplaces.wb.statistics_adapter import WbStatisticsAdapter
from seller_agent.reports.writer import ensure_dir, write_json


DEFAULT_CONTENT_MASTER_PATH = Path("catalog/content/content_master.csv")
DEFAULT_OUTPUT_DIR = Path("catalog/content/signals")


@dataclass
class CardSignalRow:
    marketplace: str
    source: str
    source_path: str = ""
    period_from: str = ""
    period_to: str = ""
    internal_product_id: str = ""
    internal_sku: str = ""
    offer_id: str = ""
    ozon_offer_id: str = ""
    ozon_product_id: str = ""
    sku: str = ""
    ozon_sku: str = ""
    vendorCode: str = ""
    wb_vendor_code: str = ""
    nmID: str = ""
    wb_nm_id: str = ""
    sales_units_30d: str = ""
    sales_revenue_30d: str = ""
    stock_total: str = ""
    ozon_stock_total: str = ""
    wb_stock_total: str = ""
    parser_best_position: str = ""
    parser_visible_queries: str = ""
    parser_top30_queries: str = ""
    parser_max_query_popularity_7d: str = ""
    notes: str = ""


SIGNAL_FIELDS = [field.name for field in fields(CardSignalRow)]


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


def _float_value(value: Any) -> float:
    try:
        return float(str(value or "0").replace(",", "."))
    except ValueError:
        return 0.0


def _format_number(value: Any) -> str:
    number = _float_value(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _first_text(*values: Any) -> str:
    for value in values:
        text = normalize_sku(value)
        if text:
            return text
    return ""


def _first_number(row: dict[str, Any], keys: tuple[str, ...]) -> float:
    for key in keys:
        if row.get(key) in (None, ""):
            continue
        value = _float_value(row.get(key))
        if value or str(row.get(key)).strip() in {"0", "0.0"}:
            return value
    return 0.0


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "да"}


def _safe_error(exc: BaseException) -> str:
    raw = str(exc).replace("\n", " ").replace("\r", " ")
    if "failed (429)" in raw or '"status": 429' in raw:
        return "HTTP 429: лимит запросов / слишком много запросов"
    if "failed (401)" in raw or '"status": 401' in raw:
        return "HTTP 401: нет авторизации"
    if "failed (403)" in raw or '"status": 403' in raw:
        return "HTTP 403: доступ запрещен"
    return raw[:800]


def _today_msk() -> date:
    return datetime.now(ZoneInfo("Europe/Moscow")).date()


def _day_bounds_msk(day: date) -> tuple[str, str]:
    value = day.isoformat()
    return f"{value}T00:00:00+03:00", f"{value}T23:59:59+03:00"


def _period(period_days: int, today: date | None = None) -> tuple[date, date]:
    end_day = (today or _today_msk()) - timedelta(days=1)
    start_day = end_day - timedelta(days=max(int(period_days), 1) - 1)
    return start_day, end_day


def _content_by_offer(content_rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {
        normalize_sku(row.get("ozon_offer_id")): row
        for row in content_rows
        if normalize_sku(row.get("ozon_offer_id"))
    }


def _content_by_ozon_product_id(content_rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {
        normalize_sku(row.get("ozon_product_id")): row
        for row in content_rows
        if normalize_sku(row.get("ozon_product_id"))
    }


def _content_by_ozon_sku(content_rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {
        normalize_sku(row.get("ozon_sku")): row
        for row in content_rows
        if normalize_sku(row.get("ozon_sku"))
    }


def _content_by_wb_vendor_code(content_rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {
        normalize_sku(row.get("wb_vendor_code")): row
        for row in content_rows
        if normalize_sku(row.get("wb_vendor_code"))
    }


def _content_by_wb_nm_id(content_rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {
        normalize_sku(row.get("wb_nm_id")): row
        for row in content_rows
        if normalize_sku(row.get("wb_nm_id"))
    }


def _base_signal(
    *,
    marketplace: str,
    source: str,
    content_row: dict[str, str] | None = None,
    period_from: str = "",
    period_to: str = "",
    source_path: str = "",
) -> dict[str, str]:
    row = content_row or {}
    return {
        "marketplace": marketplace,
        "source": source,
        "source_path": source_path,
        "period_from": period_from,
        "period_to": period_to,
        "internal_product_id": normalize_sku(row.get("internal_product_id")),
        "internal_sku": normalize_sku(row.get("internal_sku")),
        "offer_id": normalize_sku(row.get("ozon_offer_id")),
        "ozon_offer_id": normalize_sku(row.get("ozon_offer_id")),
        "ozon_product_id": normalize_sku(row.get("ozon_product_id")),
        "sku": normalize_sku(row.get("ozon_sku")),
        "ozon_sku": normalize_sku(row.get("ozon_sku")),
        "vendorCode": normalize_sku(row.get("wb_vendor_code")),
        "wb_vendor_code": normalize_sku(row.get("wb_vendor_code")),
        "nmID": normalize_sku(row.get("wb_nm_id")),
        "wb_nm_id": normalize_sku(row.get("wb_nm_id")),
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
    return (
        _first_number(item, ("present", "stock", "quantity", "available_stock_count", "valid_stock_count")),
        _first_number(item, ("reserved", "reserved_stock_count")),
    )


def build_ozon_stock_signals(
    stock_items: list[dict[str, Any]],
    content_rows: list[dict[str, str]],
    *,
    period_to: str = "",
) -> list[dict[str, str]]:
    by_product = _content_by_ozon_product_id(content_rows)
    by_offer = _content_by_offer(content_rows)
    rows: list[dict[str, str]] = []
    for item in stock_items:
        product_id = _first_text(item.get("product_id"), item.get("id"))
        offer_id = _first_text(item.get("offer_id"), item.get("offerId"))
        content_row = by_product.get(product_id) or by_offer.get(offer_id)
        present, reserved = _ozon_stock_qty(item)
        signal = _base_signal(marketplace="ozon", source="/v4/product/info/stocks", content_row=content_row, period_to=period_to)
        signal.update(
            {
                "offer_id": signal.get("offer_id") or offer_id,
                "ozon_offer_id": signal.get("ozon_offer_id") or offer_id,
                "ozon_product_id": signal.get("ozon_product_id") or product_id,
                "stock_total": _format_number(present),
                "ozon_stock_total": _format_number(present),
                "notes": f"reserved={_format_number(reserved)}",
            }
        )
        rows.append(signal)
    return rows


def _ozon_posting_products(row: dict[str, Any]) -> list[dict[str, Any]]:
    products = row.get("products")
    if isinstance(products, list):
        return [item for item in products if isinstance(item, dict)]
    financial_data = row.get("financial_data") if isinstance(row.get("financial_data"), dict) else {}
    financial_products = financial_data.get("products") if isinstance(financial_data, dict) else []
    return [item for item in financial_products if isinstance(item, dict)] if isinstance(financial_products, list) else []


def build_ozon_sales_signals(
    postings: list[dict[str, Any]],
    content_rows: list[dict[str, str]],
    *,
    period_from: str,
    period_to: str,
) -> list[dict[str, str]]:
    by_offer = _content_by_offer(content_rows)
    by_sku = _content_by_ozon_sku(content_rows)
    aggregate: dict[str, dict[str, Any]] = {}
    for posting in postings:
        status = normalize_sku(posting.get("status")).lower()
        if "cancel" in status:
            continue
        for product in _ozon_posting_products(posting):
            offer_id = _first_text(product.get("offer_id"), product.get("offerId"))
            sku = _first_text(product.get("sku"))
            key = f"offer:{offer_id}" if offer_id else f"sku:{sku}"
            if key == "sku:":
                continue
            quantity = int(_first_number(product, ("quantity", "qty", "count")) or 1)
            price = _first_number(product, ("price", "client_price", "old_price"))
            item = aggregate.setdefault(key, {"offer_id": offer_id, "sku": sku, "units": 0, "revenue": 0.0})
            item["units"] += quantity
            item["revenue"] += quantity * price

    rows: list[dict[str, str]] = []
    for item in aggregate.values():
        content_row = by_offer.get(normalize_sku(item.get("offer_id"))) or by_sku.get(normalize_sku(item.get("sku")))
        signal = _base_signal(
            marketplace="ozon",
            source="/v2/posting/fbo/list",
            content_row=content_row,
            period_from=period_from,
            period_to=period_to,
        )
        signal.update(
            {
                "offer_id": signal.get("offer_id") or normalize_sku(item.get("offer_id")),
                "ozon_offer_id": signal.get("ozon_offer_id") or normalize_sku(item.get("offer_id")),
                "sku": signal.get("sku") or normalize_sku(item.get("sku")),
                "ozon_sku": signal.get("ozon_sku") or normalize_sku(item.get("sku")),
                "sales_units_30d": _format_number(item["units"]),
                "sales_revenue_30d": _format_number(item["revenue"]),
                "notes": "Ozon FBO postings non-cancelled products; signal for prioritization, not final finance.",
            }
        )
        rows.append(signal)
    return rows


def build_wb_stock_signals(
    stock_rows: list[dict[str, Any]],
    content_rows: list[dict[str, str]],
    *,
    period_to: str = "",
) -> list[dict[str, str]]:
    by_code = _content_by_wb_vendor_code(content_rows)
    totals: dict[str, float] = {}
    for row in stock_rows:
        code = _first_text(row.get("supplierArticle"), row.get("vendorCode"), row.get("vendor_code"))
        if not code:
            continue
        totals[code] = totals.get(code, 0.0) + _first_number(row, ("quantity", "qty", "stock"))

    rows: list[dict[str, str]] = []
    for code, quantity in sorted(totals.items()):
        content_row = by_code.get(code)
        signal = _base_signal(marketplace="wb", source="/api/v1/supplier/stocks", content_row=content_row, period_to=period_to)
        signal.update(
            {
                "vendorCode": signal.get("vendorCode") or code,
                "wb_vendor_code": signal.get("wb_vendor_code") or code,
                "stock_total": _format_number(quantity),
                "wb_stock_total": _format_number(quantity),
                "notes": "WB supplier stocks aggregated by supplierArticle.",
            }
        )
        rows.append(signal)
    return rows


def build_wb_sales_signals(
    sales_rows: list[dict[str, Any]],
    content_rows: list[dict[str, str]],
    *,
    period_from: str,
    period_to: str,
) -> list[dict[str, str]]:
    by_code = _content_by_wb_vendor_code(content_rows)
    aggregate: dict[str, dict[str, float]] = {}
    for row in sales_rows:
        sale_id = normalize_sku(row.get("saleID") or row.get("saleId"))
        if sale_id.startswith("R") or _truthy(row.get("isReturn")):
            continue
        code = _first_text(row.get("supplierArticle"), row.get("vendorCode"), row.get("vendor_code"))
        if not code:
            continue
        item = aggregate.setdefault(code, {"units": 0.0, "revenue": 0.0})
        item["units"] += 1
        item["revenue"] += _first_number(row, ("forPay", "finishedPrice", "priceWithDisc", "totalPrice"))

    rows: list[dict[str, str]] = []
    for code, item in sorted(aggregate.items()):
        content_row = by_code.get(code)
        signal = _base_signal(
            marketplace="wb",
            source="/api/v1/supplier/sales",
            content_row=content_row,
            period_from=period_from,
            period_to=period_to,
        )
        signal.update(
            {
                "vendorCode": signal.get("vendorCode") or code,
                "wb_vendor_code": signal.get("wb_vendor_code") or code,
                "sales_units_30d": _format_number(item["units"]),
                "sales_revenue_30d": _format_number(item["revenue"]),
                "notes": "WB supplier sales excluding saleID returns; signal for prioritization.",
            }
        )
        rows.append(signal)
    return rows


def _latest_matching_file(data_dir: Path, patterns: list[str]) -> Path | None:
    candidates: list[Path] = []
    runs_dir = data_dir / "runs"
    if not runs_dir.exists():
        return None
    for pattern in patterns:
        candidates.extend(runs_dir.glob(pattern))
    existing = [path for path in candidates if path.is_file()]
    if not existing:
        return None
    return max(existing, key=lambda path: path.stat().st_mtime)


def _parser_paths(data_dir: Path, explicit_paths: list[Path] | None, parser_source: str) -> list[Path]:
    if explicit_paths:
        return explicit_paths
    if parser_source == "none":
        return []
    paths: list[Path] = []
    for patterns in (
        [
            "*/ozon_parser_price_seo_*/our_products_visibility_price.csv",
            "*/ozon_parser_analytics_*/our_products_visibility.csv",
        ],
        [
            "*/wb_full_seo_audit_*/processed/wb_full_seo_card_audit.csv",
            "*/wb_parser_positions_*/our_products_visibility.csv",
            "*/ozon_wb_seo_audit_*/wb_parser_our_products_visibility.csv",
        ],
    ):
        path = _latest_matching_file(data_dir, patterns)
        if path:
            paths.append(path)
    return paths


def normalize_parser_signal_rows(
    parser_rows: list[dict[str, str]],
    content_rows: list[dict[str, str]],
    *,
    source_path: Path,
) -> list[dict[str, str]]:
    by_offer = _content_by_offer(content_rows)
    by_product = _content_by_ozon_product_id(content_rows)
    by_ozon_sku = _content_by_ozon_sku(content_rows)
    by_code = _content_by_wb_vendor_code(content_rows)
    by_nm = _content_by_wb_nm_id(content_rows)
    source_name = source_path.name
    rows: list[dict[str, str]] = []
    for parser_row in parser_rows:
        offer_id = _first_text(parser_row.get("offer_id"), parser_row.get("ozon_offer_id"))
        ozon_product_id = _first_text(parser_row.get("api_product_id"), parser_row.get("local_api_product_id"), parser_row.get("ozon_product_id"))
        ozon_sku = _first_text(parser_row.get("sku"), parser_row.get("local_ozon_sku"), parser_row.get("ozon_sku"))
        vendor_code = _first_text(parser_row.get("vendor_code"), parser_row.get("vendorCode"), parser_row.get("wb_vendor_code"))
        nm_id = _first_text(parser_row.get("nm_id"), parser_row.get("nmId"), parser_row.get("nmID"), parser_row.get("wb_nm_id"))

        marketplace = "wb" if vendor_code or nm_id else "ozon"
        content_row = (
            by_offer.get(offer_id)
            or by_product.get(ozon_product_id)
            or by_ozon_sku.get(ozon_sku)
            or by_code.get(vendor_code)
            or by_nm.get(nm_id)
        )
        signal = _base_signal(
            marketplace=marketplace,
            source=f"parser:{source_name}",
            source_path=str(source_path),
            content_row=content_row,
        )
        best_position = _first_number(parser_row, ("parser_best_position", "best_position", "position", "min_position"))
        visible_queries = _first_number(parser_row, ("parser_visible_queries", "queries_found_count", "queries_count", "visible_queries"))
        top30 = _first_number(parser_row, ("parser_top30_queries", "top30_count", "top30"))
        popularity = _first_number(parser_row, ("parser_max_query_popularity_7d", "max_query_popularity_7d"))
        signal.update(
            {
                "offer_id": signal.get("offer_id") or offer_id,
                "ozon_offer_id": signal.get("ozon_offer_id") or offer_id,
                "ozon_product_id": signal.get("ozon_product_id") or ozon_product_id,
                "sku": signal.get("sku") or ozon_sku,
                "ozon_sku": signal.get("ozon_sku") or ozon_sku,
                "vendorCode": signal.get("vendorCode") or vendor_code,
                "wb_vendor_code": signal.get("wb_vendor_code") or vendor_code,
                "nmID": signal.get("nmID") or nm_id,
                "wb_nm_id": signal.get("wb_nm_id") or nm_id,
                "parser_best_position": _format_number(best_position) if best_position else "",
                "parser_visible_queries": _format_number(visible_queries) if visible_queries else "",
                "parser_top30_queries": _format_number(top30) if top30 else "",
                "parser_max_query_popularity_7d": _format_number(popularity) if popularity else "",
                "notes": "Derived parser visibility signal; check parser freshness before final SEO decisions.",
            }
        )
        if signal["parser_best_position"] or signal["parser_visible_queries"]:
            rows.append(signal)
    return rows


def _write_report(path: Path, *, run_id: str, started_at: datetime, result: dict[str, Any]) -> None:
    lines = [
        "# Card Content Signals Report",
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
    lines.extend(["", "## Sources", ""])
    for key, value in sorted(result.get("sources", {}).items()):
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "## Errors", ""])
    if result.get("errors"):
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
            "- Read-only signal collector only. It does not change Ozon/WB data.",
            "- Signals are for prioritizing card audit backlog, not for final card, price or ad decisions.",
            "- Parser source `latest` uses the latest derived parser CSV already present in project runs.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run_collect_card_signals(
    *,
    credentials: AppCredentials | None,
    data_dir: Path = Path("data"),
    content_master_path: Path | None = None,
    output_dir: Path | None = None,
    run_id: str | None = None,
    marketplace: str = "all",
    period_days: int = 30,
    skip_api: bool = False,
    parser_source: str = "latest",
    parser_paths: list[Path] | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    started_at = datetime.now()
    run_id = run_id or f"card_content_signals_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    raw_dir = ensure_dir(run_dir / "raw")
    content_master_path = content_master_path or data_dir / DEFAULT_CONTENT_MASTER_PATH
    output_dir = ensure_dir(output_dir or data_dir / DEFAULT_OUTPUT_DIR)
    start_day, end_day = _period(period_days, today=today)
    period_from = start_day.isoformat()
    period_to = end_day.isoformat()

    errors: dict[str, str] = {}
    sources: dict[str, str] = {}
    try:
        content_rows = _read_csv(content_master_path)
    except Exception as exc:  # noqa: BLE001
        content_rows = []
        errors["content_master"] = _safe_error(exc)

    sales_rows: list[dict[str, str]] = []
    stock_rows: list[dict[str, str]] = []
    parser_signal_rows: list[dict[str, str]] = []

    scope_ozon = marketplace in {"all", "ozon"}
    scope_wb = marketplace in {"all", "wb"}
    credentials = credentials or AppCredentials(ozon_seller=None, ozon_performance=None, wb=None)

    if not errors and not skip_api and scope_ozon:
        if credentials.ozon_seller:
            ozon = OzonSellerAdapter(credentials.ozon_seller)
            try:
                product_ids = sorted({row.get("ozon_product_id", "").strip() for row in content_rows if row.get("ozon_product_id", "").strip()})
                stock_items = ozon.fetch_product_stocks(product_ids)
                write_json(raw_dir / "ozon_product_stocks.json", stock_items)
                stock_rows.extend(build_ozon_stock_signals(stock_items, content_rows, period_to=period_to))
                sources["ozon_stocks"] = "/v4/product/info/stocks"
            except Exception as exc:  # noqa: BLE001
                errors["ozon_stocks"] = _safe_error(exc)
            try:
                since, to = _day_bounds_msk(start_day)
                _, end_to = _day_bounds_msk(end_day)
                postings = ozon.fetch_fbo_postings(since=since, to=end_to)
                write_json(raw_dir / "ozon_fbo_postings.json", postings)
                sales_rows.extend(build_ozon_sales_signals(postings, content_rows, period_from=period_from, period_to=period_to))
                sources["ozon_sales"] = "/v2/posting/fbo/list"
            except Exception as exc:  # noqa: BLE001
                errors["ozon_sales"] = _safe_error(exc)
        else:
            errors["ozon_api"] = "missing credentials"

    if not errors.get("content_master") and not skip_api and scope_wb:
        if credentials.wb:
            wb = WbStatisticsAdapter(credentials.wb)
            try:
                wb_stock_rows = wb.fetch_stocks_legacy(date_from=period_from)
                write_json(raw_dir / "wb_stocks.json", wb_stock_rows)
                stock_rows.extend(build_wb_stock_signals(wb_stock_rows, content_rows, period_to=period_to))
                sources["wb_stocks"] = "/api/v1/supplier/stocks"
            except Exception as exc:  # noqa: BLE001
                errors["wb_stocks"] = _safe_error(exc)
            try:
                wb_sales_rows = wb.fetch_sales(date_from=period_from, flag=1)
                write_json(raw_dir / "wb_sales.json", wb_sales_rows)
                sales_rows.extend(build_wb_sales_signals(wb_sales_rows, content_rows, period_from=period_from, period_to=period_to))
                sources["wb_sales"] = "/api/v1/supplier/sales"
            except Exception as exc:  # noqa: BLE001
                errors["wb_sales"] = _safe_error(exc)
        else:
            errors["wb_api"] = "missing credentials"

    if not errors.get("content_master"):
        for parser_path in _parser_paths(data_dir, parser_paths, parser_source):
            try:
                rows = _read_csv(parser_path)
                parser_signal_rows.extend(normalize_parser_signal_rows(rows, content_rows, source_path=parser_path))
                sources[f"parser:{parser_path.name}"] = str(parser_path)
            except Exception as exc:  # noqa: BLE001
                errors[f"parser:{parser_path}"] = _safe_error(exc)

    sales_path = output_dir / "sales_signals.csv"
    stock_path = output_dir / "stock_signals.csv"
    parser_path = output_dir / "parser_signals.csv"
    combined_path = output_dir / "all_signals.csv"
    report_path = run_dir / "card_content_signals_report.md"
    summary_path = run_dir / "summary.json"

    _write_dict_csv(sales_path, sales_rows, SIGNAL_FIELDS)
    _write_dict_csv(stock_path, stock_rows, SIGNAL_FIELDS)
    _write_dict_csv(parser_path, parser_signal_rows, SIGNAL_FIELDS)
    all_rows = sales_rows + stock_rows + parser_signal_rows
    _write_dict_csv(combined_path, all_rows, SIGNAL_FIELDS)

    marketplace_counts = Counter(row.get("marketplace", "unknown") for row in all_rows)
    summary = {
        "content_rows": len(content_rows),
        "period_from": period_from,
        "period_to": period_to,
        "period_days": period_days,
        "sales_signal_rows": len(sales_rows),
        "stock_signal_rows": len(stock_rows),
        "parser_signal_rows": len(parser_signal_rows),
        "all_signal_rows": len(all_rows),
        "marketplace_counts": dict(sorted(marketplace_counts.items())),
        "skip_api": skip_api,
        "parser_source": parser_source,
    }
    overall_status = "error" if errors.get("content_master") else "warning" if errors else "ok"
    artifacts = {
        "run_dir": str(run_dir),
        "sales_signals_csv": str(sales_path),
        "stock_signals_csv": str(stock_path),
        "parser_signals_csv": str(parser_path),
        "all_signals_csv": str(combined_path),
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
        "sources": sources,
        "errors": errors,
        "inputs": {
            "content_master_path": str(content_master_path),
            "output_dir": str(output_dir),
            "marketplace": marketplace,
            "period_days": period_days,
            "skip_api": skip_api,
            "parser_source": parser_source,
            "parser_paths": [str(path) for path in parser_paths or []],
        },
        "artifacts": artifacts,
    }
    write_json(summary_path, result)
    _write_report(report_path, run_id=run_id, started_at=started_at, result=result)
    write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=result,
        task="collect-card-signals",
        mode="read_only",
        risk="low",
        marketplaces=["ozon", "wb"],
        inputs=result["inputs"],
    )
    return result

