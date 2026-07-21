#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from datetime import datetime
from html import escape
import json
import os
from pathlib import Path
import re
from typing import Any
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from seller_agent.config import load_credentials
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.marketplaces.ozon.adapter import OzonSellerAdapter
from seller_agent.marketplaces.ozon.performance_adapter import OzonPerformanceAdapter
from seller_agent.reports.writer import ensure_dir, write_json


MOSCOW = ZoneInfo("Europe/Moscow")
SELLER_SLUG = "vital-shevron"
CAMPAIGN_ID = "20233460"
PRICE_GRID = {
    1: (530, 650, 1300),
    2: (860, 1100, 2200),
    3: (1180, 1450, 2900),
    4: (1500, 1800, 3600),
    5: (1820, 2200, 4400),
}
RETAINED_SHARE = 0.4645

LABELS = {
    "recovery_a": "Приоритет A: восстановление CPC",
    "test_b": "Приоритет B: контролируемый CPC-тест",
    "restock_then_recover": "Сначала пополнить остаток",
    "monitor_strong": "Сильная позиция: наблюдать",
    "block_no_stock": "Блок: нет остатка",
    "seo_or_low_data": "SEO/контент или мало данных",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--elastic-csv", type=Path, required=True)
    parser.add_argument("--previous-date", required=True)
    parser.add_argument("--current-date", required=True)
    parser.add_argument("--sales-from", required=True)
    parser.add_argument("--sales-to", required=True)
    parser.add_argument("--run-id")
    args = parser.parse_args()

    _load_parser_env(Path("/home/pavel/.parser-data-api.env"))
    started = datetime.now(MOSCOW)
    run_id = args.run_id or f"ozon_non_elastic_parser_review_{started.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(args.data_dir / "runs" / started.date().isoformat() / run_id)

    catalog = _read_csv(args.data_dir / "catalog" / "unified" / "products.csv")
    elastic = _read_csv(args.elastic_csv)
    by_product_id = {row["ozon_product_id"]: row for row in catalog if row.get("ozon_product_id")}
    by_offer_id = {row["ozon_offer_id"]: row for row in catalog if row.get("ozon_offer_id")}

    parser_payload, parser_rows = _parser_comparison(args.previous_date, args.current_date)
    parser_by_sku: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in parser_rows:
        parser_by_sku[str(row.get("normalized_sku") or "")].append(row)

    credentials = load_credentials()
    if credentials.ozon_seller is None:
        raise RuntimeError("Ozon Seller API credentials are not configured")
    if credentials.ozon_performance is None:
        raise RuntimeError("Ozon Performance API credentials are not configured")
    orders = _sales_by_sku(
        OzonSellerAdapter(credentials.ozon_seller),
        date_from=args.sales_from,
        date_to=args.sales_to,
    )
    campaign_products = OzonPerformanceAdapter(credentials.ozon_performance).fetch_campaign_products(
        CAMPAIGN_ID
    )
    campaign_by_sku = {str(row.get("sku") or ""): row for row in campaign_products}

    all_classified: list[tuple[dict[str, str], dict[str, str], bool]] = []
    for action_row in elastic:
        catalog_row = by_product_id.get(action_row.get("product_id", "")) or by_offer_id.get(
            action_row.get("offer_id", "")
        )
        all_classified.append((action_row, catalog_row or {}, _is_callsign(catalog_row or {}, action_row)))

    candidates = [row for row in all_classified if row[0].get("source_group") == "candidate"]
    target = [row for row in candidates if not row[2]]
    result_rows = [
        _build_row(action, product, parser_by_sku, orders, campaign_by_sku)
        for action, product, _ in target
    ]
    result_rows.sort(key=_sort_key)

    group_comparison = {
        group: _visibility_group(
            [row for row in all_classified if row[0].get("source_group") == group and not row[2]],
            parser_by_sku,
        )
        for group in ("active", "candidate")
    }
    recommendation_counts = Counter(row["recommendation_code"] for row in result_rows)
    summary_metrics = {
        "elastic_rows": len(elastic),
        "elastic_active": sum(row[0].get("source_group") == "active" for row in all_classified),
        "elastic_candidates": len(candidates),
        "excluded_callsigns": sum(row[2] for row in candidates),
        "target_non_callsign": len(result_rows),
        "target_with_stock": sum(row["stock"] > 0 for row in result_rows),
        "target_without_stock": sum(row["stock"] <= 0 for row in result_rows),
        "target_in_cpc_campaign": sum(row["in_cpc_campaign"] for row in result_rows),
        "target_not_in_cpc_campaign": sum(not row["in_cpc_campaign"] for row in result_rows),
        "target_with_parser_history": sum(row["parser_pairs"] > 0 for row in result_rows),
        "target_currently_visible": sum(row["best_current_position"] is not None for row in result_rows),
        "orders_30d": sum(row["orders_30d"] for row in result_rows),
        "revenue_30d": round(sum(row["revenue_30d"] for row in result_rows), 2),
        "recommendations": dict(recommendation_counts),
    }

    csv_path = run_dir / "ozon_non_elastic_products.csv"
    _write_csv(csv_path, result_rows)
    html_path = run_dir / "report.html"
    md_path = run_dir / "summary.md"
    json_path = run_dir / "summary.json"
    _write_html(
        html_path,
        run_id=run_id,
        generated_at=started.isoformat(timespec="seconds"),
        previous_date=args.previous_date,
        current_date=args.current_date,
        sales_from=args.sales_from,
        sales_to=args.sales_to,
        elastic_source=str(args.elastic_csv),
        parser_payload=parser_payload,
        metrics=summary_metrics,
        group_comparison=group_comparison,
        rows=result_rows,
    )
    _write_markdown(
        md_path,
        run_id=run_id,
        metrics=summary_metrics,
        previous_date=args.previous_date,
        current_date=args.current_date,
        group_comparison=group_comparison,
        rows=result_rows,
        html_path=html_path,
        csv_path=csv_path,
    )
    summary = {
        "run_id": run_id,
        "started_at": started.isoformat(timespec="seconds"),
        "overall_status": "warning",
        "mode": "read_only",
        "apply_performed": False,
        "period": {
            "parser_previous_date": args.previous_date,
            "parser_current_date": args.current_date,
            "sales_from": args.sales_from,
            "sales_to": args.sales_to,
        },
        "metrics": summary_metrics,
        "group_comparison": group_comparison,
        "limitations": [
            f"Parser comparison uses the exact available dates {args.previous_date} and {args.current_date}.",
            "Заказы Ozon Seller API не являются заказами, атрибутированными CPC-рекламе.",
            "Текущие ставки и состав кампании прочитаны, но свежие spend/CTR/DRR по SKU не получены.",
            "Снижение позиций нельзя приписать только выходу из Elastic: контрольная активная группа тоже снижалась.",
        ],
        "artifacts": {
            "report": str(html_path),
            "summary_markdown": str(md_path),
            "csv": str(csv_path),
            "summary": str(json_path),
            "run_dir": str(run_dir),
        },
    }
    manifest = write_summary_run_manifest(
        data_dir=args.data_dir,
        run_dir=run_dir,
        summary=summary,
        task="ozon-non-elastic-parser-review",
        mode="read_only",
        risk="low",
        marketplaces=["ozon"],
        inputs={
            "elastic_csv": str(args.elastic_csv),
            "previous_date": args.previous_date,
            "current_date": args.current_date,
            "sales_from": args.sales_from,
            "sales_to": args.sales_to,
            "seller_slug": SELLER_SLUG,
            "campaign_id": CAMPAIGN_ID,
        },
        source_run_ids=[args.elastic_csv.parent.name],
        lifecycle_status="closed",
        closed=True,
    )
    summary["artifacts"].update(manifest)
    write_json(json_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _load_parser_env(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(path)
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _is_callsign(product: dict[str, str], action: dict[str, str]) -> bool:
    text = " ".join(
        (
            product.get("internal_sku", ""),
            product.get("product_name", ""),
            action.get("offer_id", ""),
            action.get("name", ""),
        )
    ).lower()
    internal_sku = product.get("internal_sku", "").lower()
    offer_id = action.get("offer_id", "").lower()
    return (
        "позывн" in text
        or internal_sku.startswith(("chev_pz_", "chev_kit2_pz_"))
        or offer_id.startswith(("pzol", "pzmh", "pzkit"))
        or bool(re.search(r"(^|_)pz(_|$)", internal_sku))
    )


def _parser_comparison(previous_date: str, current_date: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    base_url = os.environ["PARSER_DATA_API_BASE_URL"].rstrip("/")
    token = os.environ["PARSER_DATA_API_TOKEN"]
    endpoint = f"{base_url}/warehouse/ozon/aggregates/store-period-comparison"
    common = [
        ("previous_date", previous_date),
        ("current_date", current_date),
        ("seller_slug", SELLER_SLUG),
        ("query_scope", "union"),
        ("page_size", "500"),
    ]
    rows: list[dict[str, Any]] = []
    cursor = ""
    first_payload: dict[str, Any] = {}
    while True:
        params = common + ([("cursor", cursor)] if cursor else [])
        request = urllib.request.Request(
            f"{endpoint}?{urllib.parse.urlencode(params)}",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.load(response)
        if not first_payload:
            first_payload = payload
        rows.extend(payload.get("rows") or [])
        cursor = str(payload.get("next_cursor") or "")
        if not cursor:
            break
    return first_payload, rows


def _sales_by_sku(adapter: OzonSellerAdapter, *, date_from: str, date_to: str) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for offset in range(0, 5000, 1000):
        payload = adapter.fetch_analytics_data(
            date_from=date_from,
            date_to=date_to,
            metrics=["ordered_units", "revenue"],
            dimensions=["sku"],
            limit=1000,
            offset=offset,
        )
        rows = ((payload.get("result") or {}).get("data") or []) if isinstance(payload, dict) else []
        for row in rows:
            dimensions = row.get("dimensions") or []
            metrics = row.get("metrics") or []
            if not dimensions:
                continue
            sku = str(dimensions[0].get("id") or "")
            result[sku] = {
                "orders": _integer(metrics[0] if metrics else 0),
                "revenue": _number(metrics[1] if len(metrics) > 1 else 0),
            }
        if len(rows) < 1000:
            break
    return result


def _build_row(
    action: dict[str, str],
    product: dict[str, str],
    parser_by_sku: dict[str, list[dict[str, Any]]],
    orders: dict[str, dict[str, float]],
    campaign_by_sku: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    sku = str(product.get("ozon_sku") or "")
    normalized_sku = sku if sku.startswith("OZN") else f"OZN{sku}"
    parser_rows = parser_by_sku.get(normalized_sku, [])
    previous = [_integer(row["previous_position"]) for row in parser_rows if row.get("previous_position") is not None]
    current = [_integer(row["current_position"]) for row in parser_rows if row.get("current_position") is not None]
    best_previous = min(previous) if previous else None
    best_current = min(current) if current else None
    best_delta = best_current - best_previous if best_previous is not None and best_current is not None else None
    lost = [row for row in parser_rows if row.get("movement_status") == "lost"]
    declined = [row for row in parser_rows if row.get("movement_status") == "declined"]
    improved = [row for row in parser_rows if row.get("movement_status") == "improved"]
    new = [row for row in parser_rows if row.get("movement_status") == "new"]
    stock = _integer(action.get("R_stock_present"))
    sales = orders.get(sku, {})
    order_count = _integer(sales.get("orders"))
    campaign = campaign_by_sku.get(sku)
    in_campaign = campaign is not None
    bid = _number((campaign or {}).get("bid")) / 1_000_000
    severe_decline = (
        (best_previous is not None and best_current is None)
        or (best_delta is not None and best_delta >= 20 and best_current > 30)
        or (len(lost) >= 2 and (best_current is None or best_current > 30))
    )
    weak_visibility = best_current is None or best_current > 30

    if stock <= 0:
        recommendation = "block_no_stock"
    elif stock < 10 and order_count >= 5 and (severe_decline or weak_visibility):
        recommendation = "restock_then_recover"
    elif stock >= 10 and order_count >= 5 and severe_decline and in_campaign:
        recommendation = "recovery_a"
    elif stock >= 10 and order_count >= 3 and weak_visibility and in_campaign:
        recommendation = "test_b"
    elif best_current is not None and best_current <= 30 and order_count >= 3:
        recommendation = "monitor_strong"
    else:
        recommendation = "seo_or_low_data"

    pack_qty = max(1, _integer(product.get("pack_qty"), 1))
    min_price, discounted_price, base_price = PRICE_GRID.get(pack_qty, (0, 0, 0))
    cpa_headroom = round(max(0, discounted_price - min_price) * RETAINED_SHARE, 2)
    return {
        "recommendation_code": recommendation,
        "recommendation": LABELS[recommendation],
        "internal_sku": product.get("internal_sku", ""),
        "offer_id": action.get("offer_id", ""),
        "product_id": action.get("product_id", ""),
        "ozon_sku": sku,
        "product_name": product.get("product_name") or action.get("name", ""),
        "pack_qty": pack_qty,
        "stock": stock,
        "elastic_reason": action.get("reason_code", ""),
        "orders_30d": order_count,
        "revenue_30d": round(_number(sales.get("revenue")), 2),
        "in_cpc_campaign": in_campaign,
        "current_bid": round(bid, 2),
        "parser_pairs": len(parser_rows),
        "best_previous_position": best_previous,
        "best_current_position": best_current,
        "best_position_delta": best_delta,
        "lost_pairs": len(lost),
        "declined_pairs": len(declined),
        "improved_pairs": len(improved),
        "new_pairs": len(new),
        "current_queries": ", ".join(
            f"{row.get('query')} ({row.get('current_position')})"
            for row in sorted(
                (row for row in parser_rows if row.get("current_position") is not None),
                key=lambda row: _integer(row.get("current_position")),
            )[:8]
        ),
        "lost_queries": ", ".join(str(row.get("query") or "") for row in lost[:8]),
        "target_min_price": min_price,
        "target_discounted_price": discounted_price,
        "target_base_price": base_price,
        "max_incremental_cpc_per_order": cpa_headroom,
        "not_in_cpc_campaign": not in_campaign,
    }


def _visibility_group(
    products: list[tuple[dict[str, str], dict[str, str], bool]],
    parser_by_sku: dict[str, list[dict[str, Any]]],
) -> dict[str, int]:
    metrics = Counter()
    metrics["products"] = len(products)
    for _, product, _ in products:
        sku = str(product.get("ozon_sku") or "")
        rows = parser_by_sku.get(sku if sku.startswith("OZN") else f"OZN{sku}", [])
        previous = [_integer(row["previous_position"]) for row in rows if row.get("previous_position") is not None]
        current = [_integer(row["current_position"]) for row in rows if row.get("current_position") is not None]
        if previous:
            metrics["previous_visible"] += 1
        if current:
            metrics["current_visible"] += 1
        if previous and not current:
            metrics["lost_all"] += 1
        if previous and current and min(current) > min(previous):
            metrics["best_declined"] += 1
        if previous and current and min(current) < min(previous):
            metrics["best_improved"] += 1
        for row in rows:
            metrics[f"pair_{row.get('movement_status')}"] += 1
    return dict(metrics)


def _sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    order = {
        "recovery_a": 0,
        "test_b": 1,
        "restock_then_recover": 2,
        "monitor_strong": 3,
        "block_no_stock": 4,
        "seo_or_low_data": 5,
    }
    return (order[row["recommendation_code"]], -row["orders_30d"], -row["stock"], row["internal_sku"])


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["internal_sku"])
        writer.writeheader()
        writer.writerows(rows)


def _write_markdown(
    path: Path,
    *,
    run_id: str,
    metrics: dict[str, Any],
    previous_date: str,
    current_date: str,
    group_comparison: dict[str, Any],
    rows: list[dict[str, Any]],
    html_path: Path,
    csv_path: Path,
) -> None:
    counts = metrics["recommendations"]
    top = [row for row in rows if row["recommendation_code"] == "recovery_a"][:15]
    lines = [
        "# Ozon: товары вне Elastic без позывных",
        "",
        "## Итог",
        "",
        "Анализ read-only. Изменения цен, акций и CPC не выполнялись.",
        "",
        f"- Вне Elastic: **{metrics['elastic_candidates']}**; исключено позывных: **{metrics['excluded_callsigns']}**.",
        f"- Целевых товаров: **{metrics['target_non_callsign']}**; с остатком: **{metrics['target_with_stock']}**.",
        f"- Приоритет A: **{counts.get('recovery_a', 0)}**; приоритет B: **{counts.get('test_b', 0)}**.",
        f"- Сначала пополнить: **{counts.get('restock_then_recover', 0)}**; без остатка: **{counts.get('block_no_stock', 0)}**.",
        f"- Parser-сравнение: **{previous_date} -> {current_date}**. Это не день-к-дню.",
        "",
        "## Контрольная группа",
        "",
        f"- Вне Elastic, не позывные: {group_comparison['candidate']}.",
        f"- В Elastic, не позывные: {group_comparison['active']}.",
        "- Обе группы снижались, поэтому выход из Elastic нельзя считать единственной причиной падения.",
        "",
        "## Первые кандидаты A",
        "",
    ]
    for row in top:
        lines.append(
            f"- `{row['internal_sku']}`: {row['orders_30d']} заказов, остаток {row['stock']}, "
            f"позиция {row['best_previous_position']} -> {row['best_current_position']}, ставка {row['current_bid']} руб."
        )
    lines.extend(
        [
            "",
            "## Ограничения",
            "",
            "- Заказы Seller API не являются CPC-атрибутированными заказами.",
            "- Без свежих spend/CTR/DRR по SKU точную новую ставку назначать нельзя.",
            "- Полная таблица и фильтры находятся в HTML; машинная детализация - в CSV.",
            "",
            f"- HTML: `{html_path}`",
            f"- CSV: `{csv_path}`",
            f"- Run ID: `{run_id}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_html(
    path: Path,
    *,
    run_id: str,
    generated_at: str,
    previous_date: str,
    current_date: str,
    sales_from: str,
    sales_to: str,
    elastic_source: str,
    parser_payload: dict[str, Any],
    metrics: dict[str, Any],
    group_comparison: dict[str, Any],
    rows: list[dict[str, Any]],
) -> None:
    counts = metrics["recommendations"]
    row_html = []
    for row in rows:
        search = " ".join(str(value) for value in row.values()).lower()
        previous = row["best_previous_position"] if row["best_previous_position"] is not None else "-"
        current = row["best_current_position"] if row["best_current_position"] is not None else "-"
        row_html.append(
            f"<tr data-rec='{escape(row['recommendation_code'])}' data-search='{escape(search)}'>"
            f"<td><span class='tag {escape(row['recommendation_code'])}'>{escape(row['recommendation'])}</span></td>"
            f"<td><strong>{escape(row['internal_sku'])}</strong><small>{escape(row['product_name'])}</small></td>"
            f"<td>{escape(row['ozon_sku'])}<small>product {escape(row['product_id'])}<br>offer {escape(row['offer_id'])}</small></td>"
            f"<td>{row['orders_30d']}<small>{row['revenue_30d']:.0f} руб.</small></td>"
            f"<td>{row['stock']}</td>"
            f"<td>{previous} -> {current}<small>lost {row['lost_pairs']}; declined {row['declined_pairs']}</small></td>"
            f"<td>{'да' if row['in_cpc_campaign'] else 'нет'}<small>{row['current_bid']:.2f} руб.</small></td>"
            f"<td>{row['target_min_price']} / {row['target_discounted_price']} / {row['target_base_price']}<small>CPA-резерв до {row['max_incremental_cpc_per_order']:.2f} руб.</small></td>"
            f"<td>{escape(row['current_queries'] or 'нет текущей видимости')}<small>Потеряно: {escape(row['lost_queries'] or '-')}</small></td>"
            "</tr>"
        )
    active = group_comparison["active"]
    candidate = group_comparison["candidate"]
    built_at = ((parser_payload.get("metadata") or {}).get("warehouse_built_at_utc") or "")
    html = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ozon вне Elastic: parser-анализ</title>
<style>
:root{{--bg:#f5f6f7;--panel:#fff;--text:#17202a;--muted:#61707f;--line:#d8dee4;--red:#a12828;--amber:#8a5a00;--green:#176b42;--blue:#205b8f}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 Arial,sans-serif;letter-spacing:0}}
main{{max-width:1500px;margin:auto;padding:20px}}h1{{font-size:25px;margin:0 0 6px}}h2{{font-size:18px;margin:0 0 12px}}p{{margin:6px 0}}.muted,small{{color:var(--muted)}}
.notice{{border-left:4px solid var(--amber);background:#fff7e4;padding:12px 14px;margin:16px 0}}.grid{{display:grid;grid-template-columns:repeat(6,minmax(150px,1fr));gap:10px;margin:16px 0}}
.metric,.section{{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:14px;min-width:0}}.metric strong{{display:block;font-size:24px}}.section{{margin:14px 0}}.split{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
.controls{{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}}input,select{{min-height:38px;border:1px solid var(--line);border-radius:4px;background:#fff;padding:7px 9px;font:inherit}}input{{flex:1;min-width:240px}}
.table-wrap{{overflow:auto;border:1px solid var(--line);border-radius:5px}}table{{width:100%;border-collapse:collapse;background:#fff;min-width:1450px}}th,td{{padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top;text-align:left}}th{{position:sticky;top:0;background:#eef1f3;z-index:1;font-size:12px}}td small{{display:block;margin-top:4px;max-width:300px}}
.tag{{display:inline-block;font-size:12px;font-weight:700;padding:3px 6px;border-radius:4px;background:#eef1f3}}.recovery_a{{color:#fff;background:var(--red)}}.test_b{{color:#fff;background:var(--blue)}}.restock_then_recover,.block_no_stock{{color:#fff;background:var(--amber)}}.monitor_strong{{color:#fff;background:var(--green)}}
code{{font-family:monospace;word-break:break-all}}p,li{{overflow-wrap:anywhere}}ul{{padding-left:20px}}@media(max-width:900px){{main{{padding:12px}}.grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}.split{{grid-template-columns:1fr}}}}
</style></head><body><main>
<h1>Ozon: товары вне Elastic без позывных</h1><p class="muted">Read-only отчет. Цены, акции и ставки не менялись.</p>
<div class="notice"><strong>Главный вывод.</strong> Для массового безусловного повышения ставок оснований нет. Есть портфель из <strong>{counts.get('recovery_a',0)}</strong> товаров приоритета A и <strong>{counts.get('test_b',0)}</strong> товаров приоритета B. Еще <strong>{counts.get('restock_then_recover',0)}</strong> продающихся карточек сначала требуют пополнения. Точные ставки блокированы до свежего CPC-отчета по spend/CTR/DRR.</div>
<div class="grid">
<div class="metric"><strong>{metrics['elastic_candidates']}</strong>вне Elastic</div><div class="metric"><strong>{metrics['excluded_callsigns']}</strong>позывных исключено</div><div class="metric"><strong>{metrics['target_non_callsign']}</strong>целевых SKU</div><div class="metric"><strong>{metrics['target_with_stock']}</strong>с остатком</div><div class="metric"><strong>{metrics['target_in_cpc_campaign']}</strong>уже в CPC</div><div class="metric"><strong>{metrics['orders_30d']}</strong>заказов за 30 дней</div>
</div>
<section class="section"><h2>Как читать рекомендации</h2><div class="split"><div><p><strong>Приоритет A</strong>: есть продажи, достаточный остаток и подтвержденное падение релевантной parser-видимости.</p><p><strong>Приоритет B</strong>: есть спрос и остаток, но parser-покрытие слабое; нужен ограниченный тест после проверки рекламы.</p><p><strong>SEO/контент</strong>: низкий сигнал продаж или нет релевантной видимости; увеличение CPC без доработки карточки не обосновано.</p></div><div><p><strong>Финансовый предел.</strong> Для одиночного товара сетка 530/650/1300 создает только около <strong>55,74 руб.</strong> дополнительного удерживаемого дохода на заказ при retained share 46,45%, а не 120 руб. Это верхний CPA-резерв до уровня экономики при цене 530, не рекомендуемая ставка за клик.</p><p>Рабочий CPC определяется как допустимый CPA x фактическая конверсия клика в заказ. Без свежей конверсии по SKU точную ставку назначать нельзя.</p></div></div></section>
<section class="section"><h2>Elastic как фактор, но не единственная причина</h2><div class="split"><div><strong>Вне Elastic, не позывные</strong><p>{candidate.get('previous_visible',0)} -> {candidate.get('current_visible',0)} видимых товаров; полностью потеряны {candidate.get('lost_all',0)}; best-position ухудшилась у {candidate.get('best_declined',0)}.</p></div><div><strong>В Elastic, не позывные</strong><p>{active.get('previous_visible',0)} -> {active.get('current_visible',0)} видимых товаров; полностью потеряны {active.get('lost_all',0)}; best-position ухудшилась у {active.get('best_declined',0)}.</p></div></div><p class="muted">Обе группы снижались. Поэтому снижение нельзя автоматически объяснять только выходом из Elastic; одновременно изменились цены и общая выдача.</p></section>
<section class="section"><h2>Все 320 товаров</h2><div class="controls"><input id="search" placeholder="Поиск по SKU, названию, запросу"><select id="filter"><option value="">Все рекомендации</option>{''.join(f'<option value="{escape(code)}">{escape(LABELS[code])} ({counts.get(code,0)})</option>' for code in LABELS)}</select></div>
<div class="table-wrap"><table><thead><tr><th>Рекомендация</th><th>Товар</th><th>Ozon IDs</th><th>Заказы 30д</th><th>Остаток</th><th>Parser {previous_date} -> {current_date}</th><th>CPC</th><th>Цены min/price/old</th><th>Запросы</th></tr></thead><tbody>{''.join(row_html)}</tbody></table></div></section>
<section class="section"><h2>Источники и ограничения</h2><ul><li>Parser Data API: <code>/warehouse/ozon/aggregates/store-period-comparison</code>, seller_slug <code>{SELLER_SLUG}</code>, warehouse built <code>{escape(str(built_at))}</code>.</li><li>Parser-период: {previous_date} -> {current_date}.</li><li>Ozon Seller API: заказы и выручка по SKU, {sales_from} -> {sales_to}.</li><li>Ozon Performance API: актуальный состав кампании <code>{CAMPAIGN_ID}</code> и текущие ставки; свежих spend/CTR/DRR по SKU в этом отчете нет.</li><li>Elastic source: <code>{escape(elastic_source)}</code>.</li><li>Run: <code>{escape(run_id)}</code>; создан {escape(generated_at)}.</li></ul></section>
</main><script>
const search=document.getElementById('search'), filter=document.getElementById('filter');function apply(){{const q=search.value.toLowerCase().trim(),f=filter.value;document.querySelectorAll('tbody tr').forEach(r=>{{r.style.display=(!f||r.dataset.rec===f)&&(!q||r.dataset.search.includes(q))?'':'none'}})}}search.addEventListener('input',apply);filter.addEventListener('change',apply);
</script></body></html>"""
    path.write_text(html, encoding="utf-8")


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


if __name__ == "__main__":
    raise SystemExit(main())
