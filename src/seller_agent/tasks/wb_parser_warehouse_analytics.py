from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.marketplaces.parser_data_api import ParserDataApiClient
from seller_agent.reports.writer import ensure_dir, write_json


DEFAULT_SUPPLIER_ID = "4516781"
MAX_API_LIMIT = 500


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _int_value(value: Any) -> int:
    if value in (None, ""):
        return 0
    try:
        return int(float(str(value).replace(",", ".")))
    except ValueError:
        return 0


def _float_value(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(str(value).replace(",", "."))
    except ValueError:
        return 0.0


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
        return [row for row in payload["rows"] if isinstance(row, dict)]
    return []


def _supplier_rows(rows: list[dict[str, Any]], supplier_id: str) -> list[dict[str, Any]]:
    return [row for row in rows if _safe_text(row.get("supplier_id")) == supplier_id]


def _position(row: dict[str, Any], *, current: bool = False) -> int:
    keys = ("current_position", "absolute_position") if current else ("absolute_position", "current_position")
    for key in keys:
        value = _int_value(row.get(key))
        if value:
            return value
    return 0


def _quantity(row: dict[str, Any], *, current: bool = False) -> int:
    keys = ("current_total_quantity", "total_quantity") if current else ("total_quantity", "current_total_quantity")
    for key in keys:
        value = _int_value(row.get(key))
        if value:
            return value
    return 0


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    ensure_dir(path.parent)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    if not fields:
        fields = ["empty"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _query_position_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    positions = [_position(row) for row in rows if _position(row)]
    products = {_safe_text(row.get("product_id")) for row in rows if _safe_text(row.get("product_id"))}
    queries = {_safe_text(row.get("query")) for row in rows if _safe_text(row.get("query"))}
    stock_rows = [row for row in rows if _quantity(row) > 0]
    return {
        "visible_rows": len(rows),
        "unique_products": len(products),
        "unique_queries": len(queries),
        "best_position": min(positions) if positions else 0,
        "top10_rows": sum(1 for pos in positions if pos <= 10),
        "top30_rows": sum(1 for pos in positions if pos <= 30),
        "top100_rows": sum(1 for pos in positions if pos <= 100),
        "stock_visible_rows": len(stock_rows),
        "zero_stock_visible_rows": len(rows) - len(stock_rows),
    }


def _change_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = Counter(_safe_text(row.get("change_status")) or "unknown" for row in rows)
    improved = 0
    declined = 0
    for row in rows:
        delta = _float_value(row.get("position_delta"))
        if delta > 0:
            improved += 1
        elif delta < 0:
            declined += 1
    return {
        "daily_change_rows": len(rows),
        "change_status_counts": dict(sorted(statuses.items())),
        "improved_rows": improved,
        "declined_rows": declined,
        "missing_rows": statuses.get("missing", 0),
        "new_rows": statuses.get("new", 0),
    }


def _weak_visible_candidates(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in rows:
        position = _position(row)
        quantity = _quantity(row)
        if not position or position <= 30 or quantity <= 0:
            continue
        candidates.append(
            {
                "query": _safe_text(row.get("query")),
                "product_id": _safe_text(row.get("product_id")),
                "product_name": _safe_text(row.get("product_name")),
                "absolute_position": position,
                "final_price": row.get("final_price", ""),
                "rating": row.get("rating", ""),
                "feedbacks": row.get("feedbacks", ""),
                "total_quantity": quantity,
            }
        )
    return sorted(candidates, key=lambda item: (_int_value(item["absolute_position"]), item["query"], item["product_id"]))[
        :limit
    ]


def build_wb_parser_signal_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    aggregate: dict[str, dict[str, Any]] = {}
    for row in rows:
        product_id = _safe_text(row.get("product_id"))
        if not product_id:
            continue
        item = aggregate.setdefault(
            product_id,
            {
                "marketplace": "wb",
                "source": "parser_data_api:/warehouse/wb/query-positions",
                "nmID": product_id,
                "wb_nm_id": product_id,
                "product_name": _safe_text(row.get("product_name")),
                "parser_best_position": 0,
                "parser_visible_queries": 0,
                "parser_top30_queries": 0,
                "stock_total": 0,
                "wb_stock_total": 0,
                "query_samples": [],
                "notes": "WB warehouse parser visibility signal; use with sales/stocks before business decisions.",
            },
        )
        position = _position(row)
        if position:
            current_best = _int_value(item.get("parser_best_position"))
            item["parser_best_position"] = position if current_best == 0 else min(current_best, position)
            item["parser_visible_queries"] = _int_value(item.get("parser_visible_queries")) + 1
            if position <= 30:
                item["parser_top30_queries"] = _int_value(item.get("parser_top30_queries")) + 1
        quantity = _quantity(row)
        if quantity:
            item["stock_total"] = max(_int_value(item.get("stock_total")), quantity)
            item["wb_stock_total"] = max(_int_value(item.get("wb_stock_total")), quantity)
        query = _safe_text(row.get("query"))
        samples = item["query_samples"]
        if query and query not in samples and len(samples) < 5:
            samples.append(query)

    result: list[dict[str, Any]] = []
    for item in aggregate.values():
        item = dict(item)
        item["query_samples"] = "; ".join(item.pop("query_samples", []))
        result.append(item)
    return sorted(result, key=lambda item: (_int_value(item.get("parser_best_position")) or 999999, _safe_text(item.get("wb_nm_id"))))


def _strong_declines(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for row in rows:
        if _float_value(row.get("position_delta")) >= 0 and _safe_text(row.get("change_status")) != "missing":
            continue
        filtered.append(row)
    return sorted(
        filtered,
        key=lambda item: (_float_value(item.get("position_delta")), _safe_text(item.get("query")), _safe_text(item.get("product_id"))),
    )[:limit]


def _top_rows(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    return rows[:limit]


def _write_report(
    path: Path,
    *,
    summary: dict[str, Any],
    weak_candidates: list[dict[str, Any]],
    daily_changes: list[dict[str, Any]],
    seller_changes: list[dict[str, Any]],
) -> None:
    source = summary.get("source") if isinstance(summary.get("source"), dict) else {}
    metrics = summary.get("metrics") if isinstance(summary.get("metrics"), dict) else {}
    artifacts = summary.get("artifacts") if isinstance(summary.get("artifacts"), dict) else {}
    lines = [
        "# WB Parser Warehouse Analytics",
        "",
        f"Run ID: `{summary.get('run_id')}`",
        f"Status: `{summary.get('overall_status')}`",
        "",
        "## Источник",
        "",
        f"- Parser Data API: `{source.get('base_url')}`",
        f"- warehouse built_at UTC: `{source.get('built_at_utc') or 'н/д'}`",
        f"- период данных: `{source.get('min_run_date') or 'н/д'}` - `{source.get('max_run_date') or 'н/д'}`",
        f"- supplier_id: `{summary.get('supplier_id')}`",
        f"- query rows: `{source.get('query_position_rows') or 0}`",
        f"- products: `{source.get('products') or 0}`",
        f"- suppliers: `{source.get('suppliers') or 0}`",
        "",
        "## Сводка Vital Shevron",
        "",
        f"- строк видимости: `{metrics.get('visible_rows')}`",
        f"- товаров в выдаче: `{metrics.get('unique_products')}`",
        f"- запросов с нашими товарами: `{metrics.get('unique_queries')}`",
        f"- лучшая позиция: `{metrics.get('best_position') or 'н/д'}`",
        f"- строк top-10/top-30/top-100: `{metrics.get('top10_rows')}` / `{metrics.get('top30_rows')}` / `{metrics.get('top100_rows')}`",
        f"- видимых строк с остатком: `{metrics.get('stock_visible_rows')}`",
        f"- видимых строк без остатка: `{metrics.get('zero_stock_visible_rows')}`",
        f"- daily changes строк: `{metrics.get('daily_change_rows')}`",
        f"- улучшений/ухудшений: `{metrics.get('improved_rows')}` / `{metrics.get('declined_rows')}`",
        f"- выпало из выдачи: `{metrics.get('missing_rows')}`",
        "",
        "## Кандидаты для SEO/карточек",
        "",
    ]
    if weak_candidates:
        for row in weak_candidates[:20]:
            lines.append(
                "- "
                f"`{row.get('product_id')}` | `{row.get('absolute_position')}` | "
                f"{row.get('query')} | {row.get('product_name')} | остаток `{row.get('total_quantity')}`"
            )
    else:
        lines.append("- кандидатов с остатком вне top-30 в текущей ограниченной выборке нет")

    lines.extend(["", "## Сильные падения / missing", ""])
    for row in daily_changes[:20]:
        lines.append(
            "- "
            f"`{row.get('product_id')}` | {row.get('query')} | "
            f"{row.get('previous_position')} -> {row.get('current_position')} | "
            f"`{row.get('change_status')}` | {row.get('product_name')}"
        )
    if not daily_changes:
        lines.append("- нет строк в ограниченной выборке")

    lines.extend(["", "## Seller changes", ""])
    if seller_changes:
        for row in seller_changes:
            lines.append(
                "- "
                f"products `{row.get('previous_product_count')}` -> `{row.get('current_product_count')}`, "
                f"queries `{row.get('previous_query_count')}` -> `{row.get('current_query_count')}`, "
                f"feedbacks `{row.get('previous_feedbacks_count')}` -> `{row.get('current_feedbacks_count')}`"
            )
    else:
        lines.append("- данных по seller changes нет")

    lines.extend(
        [
            "",
            "## Ограничения",
            "",
            "- Parser показывает видимость в выдаче WB, цену покупателя и SERP-сигналы, но не является отчетом продаж.",
            "- Для решений по карточкам и продвижению эти данные нужно соединять с остатками, продажами, акциями и ставками.",
            "- Полные parser datasets в seller project не копируются; сохранены только производные CSV/summary/report.",
            "",
            "## Файлы",
            "",
        ]
    )
    for key, value in artifacts.items():
        lines.append(f"- `{key}`: `{value}`")
    path.write_text("\n".join(lines), encoding="utf-8")


def run_wb_parser_warehouse_analytics(
    *,
    data_dir: Path = Path("data"),
    run_id: str | None = None,
    supplier_id: str = DEFAULT_SUPPLIER_ID,
    limit: int = MAX_API_LIMIT,
    report_limit: int = 50,
    client: ParserDataApiClient | None = None,
) -> dict[str, Any]:
    started_at = datetime.now()
    run_id = run_id or f"wb_parser_warehouse_analytics_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    api = client or ParserDataApiClient()
    limit = max(1, min(int(limit), MAX_API_LIMIT))
    report_limit = max(1, int(report_limit))

    summary_payload = api.get("/warehouse/wb/summary")
    quality_payload = api.get("/warehouse/wb/run-quality", params={"limit": 20})
    query_payload = api.get("/warehouse/wb/query-positions", params={"supplier_id": supplier_id, "limit": limit})
    daily_payload = api.get("/warehouse/wb/daily-changes", params={"supplier_id": supplier_id, "limit": limit})
    top_payload = api.get("/warehouse/wb/top-movers", params={"supplier_id": supplier_id, "limit": limit})
    seller_payload = api.get("/warehouse/wb/seller-changes", params={"supplier_id": supplier_id, "limit": 100})

    query_rows = _supplier_rows(_rows(query_payload), supplier_id)
    daily_rows = _supplier_rows(_rows(daily_payload), supplier_id)
    top_rows = _supplier_rows(_rows(top_payload), supplier_id)
    seller_rows = _supplier_rows(_rows(seller_payload), supplier_id)
    weak_candidates = _weak_visible_candidates(query_rows, limit=report_limit)
    parser_signal_rows = build_wb_parser_signal_rows(query_rows)
    declines = _strong_declines(daily_rows, limit=report_limit)

    raw_dir = ensure_dir(run_dir / "raw")
    artifacts = {
        "summary": str(run_dir / "summary.json"),
        "report": str(run_dir / "wb_parser_warehouse_analytics_report.md"),
        "query_positions_csv": str(run_dir / "wb_query_positions.csv"),
        "daily_changes_csv": str(run_dir / "wb_daily_changes.csv"),
        "top_movers_csv": str(run_dir / "wb_top_movers.csv"),
        "seller_changes_csv": str(run_dir / "wb_seller_changes.csv"),
        "weak_candidates_csv": str(run_dir / "wb_weak_visible_candidates.csv"),
        "parser_signals_csv": str(run_dir / "wb_parser_signals.csv"),
        "raw_summary_json": str(raw_dir / "summary.json"),
        "raw_run_quality_json": str(raw_dir / "run_quality.json"),
    }

    _write_csv(run_dir / "wb_query_positions.csv", query_rows)
    _write_csv(run_dir / "wb_daily_changes.csv", daily_rows)
    _write_csv(run_dir / "wb_top_movers.csv", top_rows)
    _write_csv(run_dir / "wb_seller_changes.csv", seller_rows)
    _write_csv(run_dir / "wb_weak_visible_candidates.csv", weak_candidates)
    _write_csv(run_dir / "wb_parser_signals.csv", parser_signal_rows)
    write_json(raw_dir / "summary.json", summary_payload)
    write_json(raw_dir / "run_quality.json", quality_payload)
    write_json(raw_dir / "query_positions_sample.json", {"rows": _top_rows(query_rows, limit=report_limit)})
    write_json(raw_dir / "daily_changes_sample.json", {"rows": _top_rows(daily_rows, limit=report_limit)})
    write_json(raw_dir / "top_movers_sample.json", {"rows": _top_rows(top_rows, limit=report_limit)})
    write_json(raw_dir / "seller_changes.json", {"rows": seller_rows})

    manifest = summary_payload.get("manifest") if isinstance(summary_payload, dict) else {}
    metrics_source = summary_payload.get("metrics") if isinstance(summary_payload, dict) else {}
    source = {
        "base_url": api.config.base_url,
        "built_at_utc": manifest.get("built_at_utc") if isinstance(manifest, dict) else "",
        "min_run_date": metrics_source.get("min_run_date") if isinstance(metrics_source, dict) else "",
        "max_run_date": metrics_source.get("max_run_date") if isinstance(metrics_source, dict) else "",
        "query_position_rows": metrics_source.get("query_position_rows") if isinstance(metrics_source, dict) else "",
        "products": metrics_source.get("products") if isinstance(metrics_source, dict) else "",
        "suppliers": metrics_source.get("suppliers") if isinstance(metrics_source, dict) else "",
    }
    metrics = {}
    metrics.update(_query_position_metrics(query_rows))
    metrics.update(_change_metrics(daily_rows))
    metrics.update(
        {
            "top_movers_rows": len(top_rows),
            "seller_changes_rows": len(seller_rows),
            "weak_visible_candidates": len(weak_candidates),
            "parser_signal_products": len(parser_signal_rows),
        }
    )
    quality_rows = _rows(quality_payload)
    quality_status_counts = Counter(_safe_text(row.get("status")) or "unknown" for row in quality_rows)
    latest_quality_status = _safe_text(quality_rows[0].get("status")) if quality_rows else ""
    overall_status = "ok" if latest_quality_status in {"success", ""} else "warning"
    if not query_rows:
        overall_status = "warning"

    result = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "supplier_id": supplier_id,
        "source": source,
        "metrics": metrics,
        "run_quality": {
            "latest_status": latest_quality_status,
            "status_counts": dict(sorted(quality_status_counts.items())),
            "rows": len(quality_rows),
        },
        "limits": {
            "api_limit": limit,
            "report_limit": report_limit,
        },
        "artifacts": artifacts,
    }
    _write_report(
        run_dir / "wb_parser_warehouse_analytics_report.md",
        summary=result,
        weak_candidates=weak_candidates,
        daily_changes=declines,
        seller_changes=seller_rows,
    )
    write_json(run_dir / "summary.json", result)
    write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=result,
        task="wb-parser-warehouse-analytics",
        mode="read_only",
        risk="low",
        marketplaces=["wb"],
        inputs={"supplier_id": supplier_id, "limit": limit, "report_limit": report_limit},
    )
    return result
