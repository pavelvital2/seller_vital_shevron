#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from html import escape
import json
from pathlib import Path
import time
from typing import Any

from openpyxl import Workbook

from seller_agent.config import load_credentials
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.marketplaces.parser_data_api import ParserDataApiClient
from seller_agent.marketplaces.wb.analytics_adapter import WbAnalyticsAdapter
from seller_agent.marketplaces.wb.promotion_adapter import WbPromotionAdapter
from seller_agent.marketplaces.wb.statistics_adapter import WbStatisticsAdapter
from seller_agent.reports.writer import ensure_dir, write_json
from seller_agent.tasks.wb_promotion_bids_apply import _current_bids_from_campaigns
from seller_agent.tasks.wb_promotion_report import (
    _flatten_campaign_ids,
    _nm_rows_from_stats,
)


SUPPLIER_ID = "4516781"
UNIT_COST = Decimal("85")
TARGET_MARGIN_PER_PIECE = Decimal("50")
SALE_RETENTION_BEFORE_GENERAL = Decimal("0.8755")
GENERAL_EXPENSE_PER_PRODUCT = Decimal("116.86")
BUYOUT_FACTOR = Decimal("0.8902")
LOW_STOCK = 8
PROMOTION_STATS_DELAY_SECONDS = 65


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0").strip().replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _integer(value: Any) -> int:
    return int(_decimal(value))


def _float(value: Any) -> float:
    return float(_decimal(value))


def _round2(value: Decimal | float | int) -> float:
    return round(float(value), 2)


def _ratio(part: Decimal | int, total: Decimal | int, multiplier: int = 1) -> float | None:
    denominator = Decimal(total)
    if denominator == 0:
        return None
    return _round2(Decimal(part) / denominator * Decimal(multiplier))


def _read_csv(path: Path, *, delimiter: str = ",") -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter=delimiter))


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(rows[0]) if rows else ["empty"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_xlsx(path: Path, sheets: list[tuple[str, list[dict[str, Any]]]]) -> None:
    workbook = Workbook()
    for index, (title, rows) in enumerate(sheets):
        sheet = workbook.active if index == 0 else workbook.create_sheet()
        sheet.title = title[:31]
        fields = list(rows[0]) if rows else ["empty"]
        sheet.append(fields)
        for row in rows:
            sheet.append([row.get(field, "") for field in fields])
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for column in sheet.columns:
            max_length = max(len(str(cell.value or "")) for cell in column)
            sheet.column_dimensions[column[0].column_letter].width = min(max(max_length + 2, 11), 55)
    workbook.save(path)


def _catalog(data_dir: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    rows = json.loads((data_dir / "catalog/unified/products.json").read_text(encoding="utf-8"))
    by_nm: dict[str, dict[str, Any]] = {}
    by_sku: dict[str, dict[str, Any]] = {}
    for row in rows:
        nm_id = str(row.get("wb_nm_id") or "").strip()
        internal_sku = str(row.get("internal_sku") or "").strip()
        if nm_id:
            by_nm[nm_id] = row
        if internal_sku:
            by_sku[internal_sku] = row
    return by_nm, by_sku


def _price_action_rows(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    result: dict[str, dict[str, Any]] = {}
    participating_discounts: Counter[int] = Counter()
    for row in _read_csv(path, delimiter=";"):
        nm_id = str(row.get("Артикул WB") or "").strip()
        if not nm_id:
            continue
        current_discount = _integer(row.get("Текущая скидка"))
        min_discount_raw = str(row.get("MIN загружаемая скидка") or "").strip()
        min_discount = _integer(min_discount_raw) if min_discount_raw else None
        action_count = _integer(row.get("Акций"))
        participating = bool(
            action_count > 0
            and min_discount is not None
            and current_discount >= min_discount
        )
        if participating:
            participating_discounts[current_discount] += 1
        result[nm_id] = {
            "base_price": _round2(_decimal(row.get("Базовая цена"))),
            "discount_percent": current_discount,
            "discounted_price": _round2(_decimal(row.get("Текущая цена со скидкой"))),
            "action_count": action_count,
            "action_names": str(row.get("Акции") or ""),
            "action_min_discount": min_discount,
            "action_participating": participating,
        }
    return result, dict(sorted(participating_discounts.items()))


def _action_run_stats(data_dir: Path, run_id: str) -> dict[str, Any]:
    matches = sorted((data_dir / "runs").glob(f"*/{run_id}"))
    if not matches:
        return {"run_id": run_id, "status": "missing"}
    csv_files = sorted(matches[-1].glob("wb-discount-calculation-active-actions-*.csv"))
    if not csv_files:
        return {"run_id": run_id, "status": "missing_csv"}
    rows, discount_counts = _price_action_rows(csv_files[-1])
    return {
        "run_id": run_id,
        "status": "ok",
        "date": matches[-1].parent.name,
        "total_goods": len(rows),
        "available": sum(1 for row in rows.values() if row.get("action_count", 0) > 0),
        "participating": sum(discount_counts.values()),
        "discount_counts": discount_counts,
    }


def _campaign_meta(campaigns: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for campaign in campaigns:
        advert_id = _integer(campaign.get("id"))
        settings = campaign.get("settings") or {}
        result[advert_id] = {
            "advert_id": advert_id,
            "campaign_name": str(settings.get("name") or ""),
            "status": _integer(campaign.get("status")),
            "status_name": {7: "completed", 9: "active", 11: "paused"}.get(
                _integer(campaign.get("status")), str(campaign.get("status") or "")
            ),
            "payment_type": str(settings.get("payment_type") or ""),
            "bid_type": str(campaign.get("bid_type") or ""),
        }
    return result


def _fetch_fullstats_with_retry(
    promotion: WbPromotionAdapter,
    *,
    ids: list[int],
    date_from: str,
    date_to: str,
    attempts: int = 3,
) -> list[dict[str, Any]]:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return promotion.fetch_fullstats(ids=ids, date_from=date_from, date_to=date_to)
        except Exception as exc:  # noqa: BLE001 - WB returns a generic API error for 429.
            last_error = exc
            if "429" not in str(exc) or attempt == attempts - 1:
                raise
            time.sleep(PROMOTION_STATS_DELAY_SECONDS)
    raise RuntimeError("WB fullstats retry loop ended unexpectedly") from last_error


def _stats_by_key(
    stats: list[dict[str, Any]],
    campaigns: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    meta = _campaign_meta(campaigns)
    rows = _nm_rows_from_stats(stats, meta)
    return {
        (str(row.get("advert_id") or ""), str(row.get("nm_id") or "")): row
        for row in rows
    }


def _sum_metrics(
    rows: list[dict[str, Any]],
    *,
    label: str,
) -> dict[str, Any]:
    views = sum(_integer(row.get("views")) for row in rows)
    clicks = sum(_integer(row.get("clicks")) for row in rows)
    atbs = sum(_integer(row.get("atbs")) for row in rows)
    orders = sum(_integer(row.get("orders")) for row in rows)
    spend = sum((_decimal(row.get("spend")) for row in rows), Decimal("0"))
    revenue = sum((_decimal(row.get("revenue")) for row in rows), Decimal("0"))
    return {
        "segment": label,
        "rows": len(rows),
        "views": views,
        "clicks": clicks,
        "ctr_percent": _ratio(clicks, views, 100),
        "atbs": atbs,
        "orders": orders,
        "order_cr_percent": _ratio(orders, clicks, 100),
        "spend": _round2(spend),
        "avg_cpc": _ratio(spend, clicks),
        "cpa": _ratio(spend, orders),
        "revenue": _round2(revenue),
        "drr_percent": _ratio(spend, revenue, 100),
    }


def _change(before: float | int | None, after: float | int | None) -> float | None:
    if before in (None, 0):
        return None
    return _round2((Decimal(str(after or 0)) / Decimal(str(before)) - 1) * 100)


def _period_comparison(
    before_rows: list[dict[str, Any]],
    after_rows: list[dict[str, Any]],
    *,
    label: str,
) -> dict[str, Any]:
    before = _sum_metrics(before_rows, label=label)
    after = _sum_metrics(after_rows, label=label)
    changes = {
        key: _change(before.get(key), after.get(key))
        for key in ("views", "clicks", "ctr_percent", "orders", "spend", "revenue", "drr_percent")
    }
    return {"segment": label, "before": before, "after": after, "change_percent": changes}


def _sale_date(row: dict[str, Any]) -> str:
    for key in ("date", "lastChangeDate"):
        value = str(row.get(key) or "")
        if value:
            return value[:10]
    return ""


def _is_return(row: dict[str, Any]) -> bool:
    return str(row.get("saleID") or row.get("saleId") or "").startswith("R") or bool(row.get("isReturn"))


def _sales_by_nm(
    rows: list[dict[str, Any]],
    *,
    date_from: str,
    date_to: str,
    catalog_by_sku: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "sales": 0,
            "returns": 0,
            "physical_pieces": 0,
            "sales_amount": Decimal("0"),
            "for_pay": Decimal("0"),
        }
    )
    for row in rows:
        day = _sale_date(row)
        if not day or day < date_from or day > date_to:
            continue
        nm_id = str(row.get("nmId") or "").strip()
        sku = str(row.get("supplierArticle") or "").strip()
        if not nm_id and sku:
            nm_id = str(catalog_by_sku.get(sku, {}).get("wb_nm_id") or "")
        if not nm_id:
            continue
        catalog_row = catalog_by_sku.get(sku, {})
        pack_qty = max(1, _integer(catalog_row.get("pack_qty") or 1))
        target = result[nm_id]
        if _is_return(row):
            target["returns"] += 1
            continue
        target["sales"] += 1
        target["physical_pieces"] += pack_qty
        target["sales_amount"] += _decimal(
            row.get("priceWithDisc") or row.get("finishedPrice") or row.get("totalPrice")
        )
        target["for_pay"] += _decimal(row.get("forPay"))
    normalized: dict[str, dict[str, Any]] = {}
    for nm_id, value in result.items():
        normalized[nm_id] = {
            **value,
            "sales_amount": _round2(value["sales_amount"]),
            "for_pay": _round2(value["for_pay"]),
        }
    return normalized


def _sales_summary(rows: list[dict[str, Any]], catalog_by_nm: dict[str, dict[str, Any]], label: str) -> dict[str, Any]:
    sales = sum(_integer(row.get("sales")) for row in rows)
    returns = sum(_integer(row.get("returns")) for row in rows)
    physical = sum(_integer(row.get("physical_pieces")) for row in rows)
    amount = sum((_decimal(row.get("sales_amount")) for row in rows), Decimal("0"))
    for_pay = sum((_decimal(row.get("for_pay")) for row in rows), Decimal("0"))
    return {
        "segment": label,
        "sales": sales,
        "returns": returns,
        "physical_pieces": physical,
        "sales_amount": _round2(amount),
        "for_pay": _round2(for_pay),
        "for_pay_per_piece": _ratio(for_pay, physical),
    }


def _stock_map(rows: list[dict[str, Any]]) -> dict[str, int]:
    result: dict[str, int] = defaultdict(int)
    for row in rows:
        nm_id = str(row.get("nmId") or "")
        if nm_id:
            result[nm_id] += _integer(row.get("quantity"))
    return dict(result)


def _parser_product_map(comparison: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("product_id") or ""): row
        for row in comparison.get("product_aggregates") or []
        if row.get("product_id")
    }


def _is_callsign(name: str, sku: str) -> bool:
    value = f"{name} {sku}".lower()
    return "позывн" in value or "_pz_" in value


def _margin_model(
    *,
    price: Decimal,
    pack_qty: int,
    spend: Decimal,
    orders: int,
) -> tuple[float | None, float]:
    headroom = (
        price * SALE_RETENTION_BEFORE_GENERAL
        - GENERAL_EXPENSE_PER_PRODUCT
        - (UNIT_COST + TARGET_MARGIN_PER_PIECE) * pack_qty
    )
    if orders <= 0:
        return None, _round2(max(Decimal("0"), headroom))
    adjusted_cpa = spend / (Decimal(orders) * BUYOUT_FACTOR)
    margin_product = (
        price * SALE_RETENTION_BEFORE_GENERAL
        - GENERAL_EXPENSE_PER_PRODUCT
        - UNIT_COST * pack_qty
        - adjusted_cpa
    )
    return _round2(margin_product / pack_qty), _round2(max(Decimal("0"), headroom))


def _recommendation(
    *,
    row: dict[str, Any],
    callsign: bool,
    stock: int,
    current_bid: Decimal,
    margin_per_piece: float | None,
    ad_headroom: float,
) -> tuple[str, float | None, str]:
    views = _integer(row.get("views"))
    clicks = _integer(row.get("clicks"))
    orders = _integer(row.get("orders"))
    spend = _decimal(row.get("spend"))
    ctr = _ratio(clicks, views, 100) or 0
    drr = _ratio(spend, _decimal(row.get("revenue")), 100)

    if stock < LOW_STOCK:
        return "RESTOCK_HOLD", None, f"Остаток {stock} шт. ниже порога {LOW_STOCK}; рост рекламы создаст обнуление."
    if callsign:
        return "CALLSIGN_HOLD", None, "Позывной: целевой спрос, согласованное правило — не масштабировать платное продвижение."
    if clicks >= 20 and orders == 0:
        target = max(Decimal("1.00"), current_bid * Decimal("0.70"))
        return "STOP_OR_REDUCE", _round2(target), "20+ кликов без рекламного заказа: снизить на 30% или остановить после проверки карточки."
    if clicks >= 10 and orders == 0:
        return "CARD_FIX_HOLD", None, "10+ кликов без заказа: ставка уже приводит трафик, проблема в конверсии карточки/цены."
    if views >= 500 and ctr < 1.5:
        return "CTR_FIX_HOLD", None, f"CTR {ctr:.2f}% при {views} показах: сначала главное фото, название и релевантность выдачи."
    if orders >= 2 and drr is not None and drr <= 5 and margin_per_piece is not None and margin_per_piece >= 80:
        step = Decimal("1.30")
        target = min(current_bid * step, current_bid + Decimal(str(ad_headroom)) * Decimal("0.25"))
        return "SCALE_30", _round2(max(target, current_bid)), "Есть 2+ заказа, ДРР до 5% и запас маржи: заметное повышение на 30%."
    if orders >= 1 and drr is not None and drr <= 8 and margin_per_piece is not None and margin_per_piece >= 50:
        step = Decimal("1.20")
        target = min(current_bid * step, current_bid + Decimal(str(ad_headroom)) * Decimal("0.20"))
        return "SCALE_20", _round2(max(target, current_bid)), "Есть заказ, ДРР до 8% и защищена маржа 50 руб.: повышение на 20%."
    if clicks < 10:
        return "COLLECT_DATA", None, "Меньше 10 кликов за 7 дней: данных недостаточно для нового изменения ставки."
    return "KEEP", None, "Ставку оставить: нет подтвержденного основания масштабировать или сокращать."


def _product_rows(
    *,
    memberships: list[dict[str, Any]],
    pre_stats: dict[tuple[str, str], dict[str, Any]],
    post_stats: dict[tuple[str, str], dict[str, Any]],
    cohort_keys: set[tuple[str, str]],
    cohort_actions: dict[tuple[str, str], str],
    catalog_by_nm: dict[str, dict[str, Any]],
    stocks: dict[str, int],
    prices: dict[str, dict[str, Any]],
    sales_pre: dict[str, dict[str, Any]],
    sales_post: dict[str, dict[str, Any]],
    parser_products: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for membership in memberships:
        key = (str(membership["advert_id"]), str(membership["nm_id"]))
        nm_id = key[1]
        catalog = catalog_by_nm.get(nm_id, {})
        pre = pre_stats.get(key, {})
        post = post_stats.get(key, {})
        price = prices.get(nm_id, {})
        parser = parser_products.get(nm_id, {})
        post_sales = sales_post.get(nm_id, {})
        pack_qty = max(1, _integer(catalog.get("pack_qty") or 1))
        current_price = _decimal(price.get("discounted_price"))
        current_bid = _decimal(membership.get("current_bid"))
        name = str(catalog.get("product_name") or post.get("name") or pre.get("name") or "")
        internal_sku = str(catalog.get("internal_sku") or "")
        callsign = _is_callsign(name, internal_sku)
        margin, headroom = _margin_model(
            price=current_price,
            pack_qty=pack_qty,
            spend=_decimal(post.get("spend")),
            orders=_integer(post.get("orders")),
        )
        recommendation, target_bid, reason = _recommendation(
            row=post,
            callsign=callsign,
            stock=stocks.get(nm_id, 0),
            current_bid=current_bid,
            margin_per_piece=margin,
            ad_headroom=headroom,
        )
        output.append(
            {
                "advert_id": key[0],
                "campaign": membership.get("campaign_name", ""),
                "nm_id": nm_id,
                "internal_sku": internal_sku,
                "name": name,
                "pack_qty": pack_qty,
                "cohort": "yes" if key in cohort_keys else "control",
                "cohort_action": cohort_actions.get(key, ""),
                "current_bid": _round2(current_bid),
                "current_price": _round2(current_price),
                "discount_percent": price.get("discount_percent", ""),
                "action_available": "yes" if price.get("action_count", 0) else "no",
                "action_participating": "yes" if price.get("action_participating") else "no",
                "stock": stocks.get(nm_id, 0),
                "pre_views": _integer(pre.get("views")),
                "post_views": _integer(post.get("views")),
                "post_clicks": _integer(post.get("clicks")),
                "post_ctr_percent": _ratio(_integer(post.get("clicks")), _integer(post.get("views")), 100),
                "post_orders": _integer(post.get("orders")),
                "post_spend": _round2(_decimal(post.get("spend"))),
                "post_revenue": _round2(_decimal(post.get("revenue"))),
                "post_drr_percent": _ratio(_decimal(post.get("spend")), _decimal(post.get("revenue")), 100),
                "actual_sales_pre": _integer(sales_pre.get(nm_id, {}).get("sales")),
                "actual_sales_post": _integer(post_sales.get("sales")),
                "actual_returns_post": _integer(post_sales.get("returns")),
                "actual_pieces_post": _integer(post_sales.get("physical_pieces")),
                "margin_model_per_piece": margin,
                "ad_headroom_margin50": headroom,
                "parser_prev_best": parser.get("previous_best_position"),
                "parser_current_best": parser.get("current_best_position"),
                "parser_prev_queries": parser.get("previous_visible_queries", 0),
                "parser_current_queries": parser.get("current_visible_queries", 0),
                "recommendation": recommendation,
                "target_bid_dry_run": target_bid,
                "reason": reason,
            }
        )
    return sorted(output, key=lambda row: (row["recommendation"], -_integer(row["post_orders"]), -_float(row["post_spend"])))


def _html_table(rows: list[dict[str, Any]], fields: list[tuple[str, str]], limit: int | None = None) -> str:
    selected = rows if limit is None else rows[:limit]
    head = "".join(f"<th>{escape(label)}</th>" for _, label in fields)
    body = []
    for row in selected:
        cells = "".join(f"<td>{escape(str(row.get(key, '') if row.get(key, '') is not None else ''))}</td>" for key, _ in fields)
        body.append(f"<tr>{cells}</tr>")
    if not body:
        body.append(f"<tr><td colspan='{len(fields)}'>Нет строк</td></tr>")
    return f"<div class='table-wrap'><table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"


def _fmt(value: Any, suffix: str = "") -> str:
    if value is None:
        return "н/д"
    if isinstance(value, float):
        return f"{value:,.2f}".replace(",", " ").replace(".", ",") + suffix
    return f"{value}{suffix}"


def _write_html(
    path: Path,
    *,
    summary: dict[str, Any],
    product_rows: list[dict[str, Any]],
    launch_rows: list[dict[str, Any]],
    reactivation_rows: list[dict[str, Any]],
    test_focus_rows: list[dict[str, Any]],
    parser: dict[str, Any],
    query_coverage: list[dict[str, Any]],
) -> None:
    comparison_rows = []
    for item in summary["promotion_comparison"]:
        before = item["before"]
        after = item["after"]
        comparison_rows.append(
            {
                "segment": item["segment"],
                "views": f"{before['views']} → {after['views']} ({_fmt(item['change_percent']['views'], '%')})",
                "clicks": f"{before['clicks']} → {after['clicks']} ({_fmt(item['change_percent']['clicks'], '%')})",
                "orders": f"{before['orders']} → {after['orders']} ({_fmt(item['change_percent']['orders'], '%')})",
                "spend": f"{_fmt(before['spend'], ' ₽')} → {_fmt(after['spend'], ' ₽')}",
                "revenue": f"{_fmt(before['revenue'], ' ₽')} → {_fmt(after['revenue'], ' ₽')}",
                "drr": f"{_fmt(before['drr_percent'], '%')} → {_fmt(after['drr_percent'], '%')}",
            }
        )
    decision_counts = summary["decision_counts"]
    top_scale = [row for row in product_rows if row["recommendation"] in {"SCALE_30", "SCALE_20"}]
    waste = [row for row in product_rows if row["recommendation"] in {"STOP_OR_REDUCE", "CARD_FIX_HOLD", "CTR_FIX_HOLD"}]
    stock_rows = [row for row in product_rows if row["recommendation"] == "RESTOCK_HOLD"]
    parser_movement = parser.get("movement") or {}
    cards = "".join(
        [
            f"<article><span>Заказы рекламы, 91 ставка</span><strong>{summary['cohort_post']['orders']}</strong><small>до: {summary['cohort_pre']['orders']}</small></article>",
            f"<article><span>Фактические продажи когорты</span><strong>{summary['cohort_sales_post']['sales']}</strong><small>до: {summary['cohort_sales_pre']['sales']}</small></article>",
            f"<article><span>ДРР когорты</span><strong>{_fmt(summary['cohort_post']['drr_percent'], '%')}</strong><small>расход {_fmt(summary['cohort_post']['spend'], ' ₽')}</small></article>",
            f"<article><span>Видимых товаров</span><strong>{parser.get('current', {}).get('visible_products', 0)}</strong><small>до: {parser.get('previous', {}).get('visible_products', 0)}</small></article>",
            f"<article><span>Масштабировать</span><strong>{decision_counts.get('SCALE_30', 0) + decision_counts.get('SCALE_20', 0)}</strong><small>только после dry-run</small></article>",
            f"<article><span>Утечки бюджета</span><strong>{decision_counts.get('STOP_OR_REDUCE', 0)}</strong><small>20+ кликов без заказа</small></article>",
        ]
    )
    decision_list = "".join(
        f"<li><b>{escape(key)}</b><span>{value}</span></li>"
        for key, value in sorted(decision_counts.items(), key=lambda item: (-item[1], item[0]))
    )
    action_history = summary["actions"]["history"]
    content = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:,">
<title>WB: полный контроль продвижения</title>
<style>
:root{{--ink:#17202a;--muted:#65717f;--line:#d9dee5;--soft:#f4f6f8;--good:#18794e;--warn:#a35a00;--bad:#b42318;--accent:#2457a7}}
*{{box-sizing:border-box}} body{{margin:0;background:#eef1f4;color:var(--ink);font:14px/1.45 Arial,sans-serif;letter-spacing:0}}
main{{max-width:1420px;margin:auto;background:white;min-height:100vh;padding:24px}}
h1{{font-size:26px;margin:0 0 6px}} h2{{font-size:19px;margin:0 0 12px}} h3{{font-size:16px;margin:16px 0 8px}}
p{{margin:6px 0}} .meta{{color:var(--muted);margin-bottom:18px}} .notice{{border-left:4px solid var(--warn);background:#fff7e8;padding:10px 12px;margin:14px 0}}
.cards{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:16px 0}}
article{{border:1px solid var(--line);border-radius:6px;padding:12px;min-width:0}} article span,article small{{display:block;color:var(--muted)}} article strong{{display:block;font-size:24px;margin:3px 0}}
section{{border-top:1px solid var(--line);padding:20px 0}} .table-wrap{{overflow-x:auto;border:1px solid var(--line);border-radius:6px}}
table{{border-collapse:collapse;width:100%;min-width:880px}} th,td{{padding:8px 9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;white-space:nowrap}}
th{{background:var(--soft);font-size:12px;position:sticky;top:0}} td:nth-child(4){{white-space:normal;min-width:260px}}
ul.actions{{padding:0;margin:0;list-style:none;max-width:700px}} ul.actions li{{display:flex;justify-content:space-between;border-bottom:1px solid var(--line);padding:6px 0}}
.recommendations li{{margin:8px 0}} code{{background:var(--soft);padding:2px 4px;border-radius:3px}} .good{{color:var(--good)}} .bad{{color:var(--bad)}}
@media(max-width:760px){{main{{padding:14px}}.cards{{grid-template-columns:1fr 1fr}}h1{{font-size:22px}}}}
</style>
</head>
<body><main>
<h1>Wildberries: полный контроль продвижения</h1>
<p class="meta">Период после изменения ставок: 22–28.07.2026; сравнение: 14–20.07.2026. Сформировано {escape(summary['generated_at'])}.</p>
<div class="notice"><b>Read-only.</b> Ставки, кампании, цены и скидки не менялись. Все целевые ставки ниже являются проектом следующего dry-run и требуют отдельного согласования.</div>
<div class="cards">{cards}</div>
<section><h2>Главный вывод</h2>
<p>{escape(summary['conclusion'])}</p>
<h3>Решения по активным CPC-товарам</h3><ul class="actions">{decision_list}</ul></section>
<section><h2>До и после</h2>{_html_table(comparison_rows,[('segment','Сегмент'),('views','Показы'),('clicks','Клики'),('orders','Заказы'),('spend','Расход'),('revenue','Выручка рекламы'),('drr','ДРР')])}</section>
<section><h2>Товары для заметного масштабирования</h2>
<p>Только позиции с заказами, приемлемым ДРР, запасом маржи и остатком. Предлагается шаг 20–30%, а не символическая корректировка.</p>
{_html_table(top_scale,[('nm_id','nmID'),('internal_sku','Артикул'),('name','Товар'),('stock','Остаток'),('post_orders','Заказы'),('post_spend','Расход'),('post_drr_percent','ДРР, %'),('margin_model_per_piece','Маржа/шт., модель'),('current_bid','Ставка'),('target_bid_dry_run','Проект ставки')],50)}</section>
<section><h2>Фокусная волна для получения продаж</h2>
<p>Из 154 строк с недостаточным трафиком выбраны 30 товаров: есть фактические продажи или видимость в parser, остаток достаточный. Предлагается не увеличивать общий бюджет, а перенести его с менее доказанного хвоста и дать этой волне заметный тест +25% на 7 дней.</p>
{_html_table(test_focus_rows,[('nm_id','nmID'),('internal_sku','Артикул'),('name','Товар'),('stock','Остаток'),('actual_sales_post','Продажи'),('parser_current_best','Позиция'),('parser_current_queries','Запросов'),('current_bid','Ставка'),('focus_target_bid','Проект +25%'),('focus_priority','Приоритет')],30)}</section>
<section><h2>Остановить утечки и исправить конверсию</h2>
{_html_table(waste,[('nm_id','nmID'),('internal_sku','Артикул'),('name','Товар'),('recommendation','Решение'),('post_views','Показы'),('post_clicks','Клики'),('post_orders','Заказы'),('post_spend','Расход'),('current_bid','Ставка'),('target_bid_dry_run','Проект'),('reason','Причина')],80)}</section>
<section><h2>Новые товары для тестового запуска CPC</h2>
<p>Кандидаты вне текущих активных CPC: официальный остаток не ниже 8, не позывные, есть parser-сигнал; приоритет повышается при фактических продажах.</p>
{_html_table(launch_rows,[('nm_id','nmID'),('internal_sku','Артикул'),('name','Товар'),('stock','Остаток'),('actual_sales_post','Продажи 22–28'),('best_position','Лучшая позиция'),('visible_queries','Запросов'),('sample_queries','Примеры запросов'),('launch_priority','Приоритет')],40)}</section>
<section><h2>Приостановленная CPC-кампания</h2>
<p>Кампания <code>36269574</code> «СВО за клик от 01.05.2026» находится в паузе с 28 июля: в ней {summary['paused_cpc_rows']} товаров, включая {summary['inactive_cohort_rows']} строк исходной когорты. Причина: {escape(summary['paused_campaign_reason'])}. Ниже позиции, которые можно рассматривать для возврата только после fresh-проверки кампании, ставки и маржи.</p>
{_html_table(reactivation_rows,[('nm_id','nmID'),('internal_sku','Артикул'),('name','Товар'),('cohort_member','Когорта 91'),('stock','Остаток'),('post_orders','Заказы рекламы'),('actual_sales_post','Факт. продажи'),('parser_current_queries','Запросов'),('status','Решение')],60)}</section>
<section><h2>Остатки, блокирующие рост</h2>
{_html_table(stock_rows,[('nm_id','nmID'),('internal_sku','Артикул'),('name','Товар'),('stock','Остаток'),('post_orders','Заказы рекламы'),('actual_sales_post','Факт. продажи'),('reason','Причина')],80)}</section>
<section><h2>Поисковая выдача</h2>
<p>Видимые товары: {parser.get('previous',{}).get('visible_products',0)} → {parser.get('current',{}).get('visible_products',0)}; связки запрос–товар: {parser.get('previous',{}).get('query_product_pairs',0)} → {parser.get('current',{}).get('query_product_pairs',0)}; новые связки {parser_movement.get('new_pairs',0)}, потерянные {parser_movement.get('lost_pairs',0)}. При этом товаров в top-30: {parser.get('previous',{}).get('top_30_products',0)} → {parser.get('current',{}).get('top_30_products',0)}.</p>
<h3>Пробелы по запросам</h3>
{_html_table(query_coverage,[('query','Запрос'),('matched_products_in_top_n','Наших в top-30'),('matched_products_visible_anywhere','Видимых всего'),('matched_best_position','Лучшая позиция'),('matched_total_stock','Остаток')],30)}</section>
<section><h2>Акции и причина просадки</h2>
<p>Участие в акциях резко сократилось после снижения скидок. Это сильный сопутствующий фактор падения продаж, но не доказательство единственной причины. Возвращать товары в акции с требованием выше 60% без проверки минимальной цены и маржи нельзя.</p>
{_html_table(action_history,[('date','Дата среза'),('total_goods','Товаров'),('available','Доступна акция'),('participating','Участвуют')])}</section>
<section><h2>Расширенный план роста продаж</h2>
<ol class="recommendations">
<li><b>Перераспределить бюджет:</b> увеличить на 20–30% только доказанные товары из блока масштабирования; сократить на 30% или остановить строки с 20+ кликами без заказа. Это даёт заметный перенос бюджета, не изменение на копейки.</li>
<li><b>Сконцентрировать тестовый бюджет:</b> 154 активные строки собрали вместе только 95 кликов и не дали атрибутированных заказов. Не повышать их все. Выбрать 30–50 товаров по фактическим продажам, запасу и parser-сигналу, а слабый хвост временно не масштабировать.</li>
<li><b>Расширить продающий пул:</b> проверить {len(reactivation_rows)} выпавших строк когорты и {len(launch_rows)} новых кандидатов вне CPC. Запускать только непозывные товары с запасом и подтвержденным спросом, отдельными семантическими группами.</li>
<li><b>Не лечить ставкой слабую карточку:</b> для строк с низким CTR сначала главное фото/название; для 10+ кликов без заказа — фото, цена, отзывы, описание и соответствие запросу.</li>
<li><b>Восстановить видимость:</b> парсер показывает потерю {parser_movement.get('lost_pairs',0)} связок при {parser_movement.get('new_pairs',0)} новых. Нужен SEO-пакет по потерянным релевантным запросам и контроль через 3 и 7 полных дней.</li>
<li><b>Акции использовать точечно:</b> сейчас участвуют {summary['actions']['participating']} из {summary['actions']['available']} доступных акциям товаров. Не повышать скидку всем; отдельно проверить маржу и выбрать товары, где эффект акции потенциально дешевле CPC.</li>
<li><b>Снабжение как часть продвижения:</b> не масштабировать {len(stock_rows)} строк с остатком ниже {LOW_STOCK}. Сначала распределить товар ближе к регионам спроса, затем повышать ставку.</li>
</ol></section>
<section><h2>Источники и ограничения</h2>
<ul><li>WB Promotion API: <code>/adv/v3/fullstats</code>, <code>/api/advert/v2/adverts</code>.</li>
<li>WB Statistics API: <code>/api/v1/supplier/sales</code>, оперативные продажи и возвраты.</li>
<li>WB Analytics API: <code>/api/analytics/v1/stocks-report/wb-warehouses</code>, свежий официальный остаток.</li>
<li>Акции/цены: fresh dry-run <code>{escape(summary['actions']['source_run_id'])}</code>, без write.</li>
<li>Parser Data API: <code>/warehouse/wb/aggregates/store-period-comparison</code>, <code>query-coverage</code>, <code>promotion-visibility-candidates</code>; supplier_id <code>{SUPPLIER_ID}</code>; warehouse {escape(str(parser.get('metadata',{}).get('warehouse_built_at','')))}.</li>
<li>Маржа — расчетная модель, а не бухгалтерский факт: себестоимость 85 ₽/изделие, цель 50 ₽/изделие, удержание до общих расходов 87,55%, общие расходы 116,86 ₽/товар, buyout factor 89,02%.</li>
<li>Одновременно на позиции влияли цены, акции, остатки, карточки и конкуренты; причинность только ставок полностью отделить нельзя.</li></ul></section>
</main></body></html>"""
    path.write_text(content, encoding="utf-8")


def _write_markdown(path: Path, summary: dict[str, Any], top_rows: list[dict[str, Any]]) -> None:
    counts = summary["decision_counts"]
    lines = [
        "# WB: полный контроль продвижения",
        "",
        "Режим: `read-only`. Никакие ставки, кампании, цены или скидки не изменялись.",
        "",
        f"Периоды: `{summary['periods']['before']}` и `{summary['periods']['after']}`.",
        "",
        "## Итог",
        "",
        summary["conclusion"],
        "",
        f"- рекламные заказы точной когорты: `{summary['cohort_pre']['orders']} -> {summary['cohort_post']['orders']}`;",
        f"- фактические продажи когорты: `{summary['cohort_sales_pre']['sales']} -> {summary['cohort_sales_post']['sales']}`;",
        f"- ДРР когорты после изменения: `{summary['cohort_post']['drr_percent']}`%;",
        f"- масштабировать на 20–30%: `{counts.get('SCALE_20', 0) + counts.get('SCALE_30', 0)}` строк;",
        f"- сократить/остановить: `{counts.get('STOP_OR_REDUCE', 0)}` строк;",
        f"- сначала исправить карточку/CTR: `{counts.get('CARD_FIX_HOLD', 0) + counts.get('CTR_FIX_HOLD', 0)}` строк;",
        f"- заблокировано низким остатком: `{counts.get('RESTOCK_HOLD', 0)}` строк;",
        f"- новых кандидатов для CPC-теста: `{summary['new_launch_candidates']}`.",
        "",
        "## Первые кандидаты на масштабирование",
        "",
    ]
    for row in top_rows[:15]:
        lines.append(
            f"- `{row['internal_sku']}` / `{row['nm_id']}`: заказов `{row['post_orders']}`, "
            f"ДРР `{row['post_drr_percent']}`%, ставка `{row['current_bid']} -> {row['target_bid_dry_run']}` руб."
        )
    lines.extend(
        [
            "",
            f"- фокусная бюджетно-нейтральная волна: `{summary['focus_wave_products']}` товаров;",
            f"- paused CPC-кампания: `{summary['paused_cpc_rows']}` товаров, из них кандидатов на отдельный возврат `{summary['reactivation_candidates']}`;",
            f"- участие в акциях: `{summary['actions']['participating']}` товаров сейчас против `{summary['actions']['history'][0]['participating']}` на 20 июля;",
            "",
            "Следующий шаг: сформировать отдельный fresh dry-run ставок и запусков, проверить текущую маржу/остатки и только затем передать владельцу на согласование.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--before-from", default="2026-07-14")
    parser.add_argument("--before-to", default="2026-07-20")
    parser.add_argument("--after-from", default="2026-07-22")
    parser.add_argument("--after-to", default="2026-07-28")
    parser.add_argument(
        "--cohort-csv",
        type=Path,
        default=Path("data/runs/2026-07-21/wb_margin50_bids_apply_20260721T074342/processed/approved_rows.csv"),
    )
    parser.add_argument(
        "--actions-run",
        default="wb_actions_discount_plan_60-50-50_20260729T070852",
    )
    parser.add_argument(
        "--reuse-run-dir",
        type=Path,
        help="Reuse raw read-only snapshots from a completed run instead of calling marketplace APIs again.",
    )
    parser.add_argument(
        "--before-actions-run",
        default="wb_actions_discount_plan_60-50-50_20260720T195814",
    )
    parser.add_argument(
        "--paused-campaign-reason",
        default="не подтверждена доступными read-only источниками",
    )
    args = parser.parse_args()

    started = datetime.now()
    run_id = f"wb_sales_growth_control_{started.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(args.data_dir / "runs" / started.strftime("%Y-%m-%d") / run_id)
    raw_dir = ensure_dir(run_dir / "raw")
    processed_dir = ensure_dir(run_dir / "processed")

    credentials = load_credentials()
    if not credentials.wb:
        raise RuntimeError("missing WB API token")

    action_dirs = sorted((args.data_dir / "runs").glob(f"*/{args.actions_run}"))
    if not action_dirs:
        raise RuntimeError(f"actions run not found: {args.actions_run}")
    action_dir = action_dirs[-1]
    action_csv = action_dir / "wb-discount-calculation-active-actions-60-50-50.csv"
    prices, action_discount_counts = _price_action_rows(action_csv)

    catalog_by_nm, catalog_by_sku = _catalog(args.data_dir)
    cohort_rows = _read_csv(args.cohort_csv)
    cohort_keys = {
        (str(row.get("advert_id") or ""), str(row.get("nm_id") or ""))
        for row in cohort_rows
    }
    cohort_actions = {
        (str(row.get("advert_id") or ""), str(row.get("nm_id") or "")): str(row.get("approved_action") or "")
        for row in cohort_rows
    }

    if args.reuse_run_dir:
        source_raw = args.reuse_run_dir / "raw"
        count_data = json.loads((source_raw / "campaign_count.json").read_text(encoding="utf-8"))
        campaigns = json.loads((source_raw / "campaigns.json").read_text(encoding="utf-8"))["adverts"]
        pre_raw = json.loads((source_raw / "fullstats_before.json").read_text(encoding="utf-8"))
        post_raw = json.loads((source_raw / "fullstats_after.json").read_text(encoding="utf-8"))
        stock_rows = json.loads((source_raw / "stocks.json").read_text(encoding="utf-8"))["items"]
        sales_rows = json.loads((source_raw / "sales.json").read_text(encoding="utf-8"))
        parser_summary = json.loads((source_raw / "parser_summary.json").read_text(encoding="utf-8"))
        parser_comparison = json.loads((source_raw / "parser_comparison.json").read_text(encoding="utf-8"))
        query_coverage_response = json.loads((source_raw / "parser_query_coverage.json").read_text(encoding="utf-8"))
        launch_response = json.loads((source_raw / "parser_launch_candidates.json").read_text(encoding="utf-8"))
    else:
        promotion = WbPromotionAdapter(credentials.wb)
        count_data = promotion.fetch_campaign_count()
        campaign_ids, _ = _flatten_campaign_ids(count_data)
        campaigns = promotion.fetch_campaigns(ids=campaign_ids, statuses=[7, 9, 11], payment_type="cpc")
        pre_raw = _fetch_fullstats_with_retry(
            promotion,
            ids=campaign_ids,
            date_from=args.before_from,
            date_to=args.before_to,
        )
        write_json(raw_dir / "fullstats_before.json", pre_raw)
        time.sleep(PROMOTION_STATS_DELAY_SECONDS)
        post_raw = _fetch_fullstats_with_retry(
            promotion,
            ids=campaign_ids,
            date_from=args.after_from,
            date_to=args.after_to,
        )
        target_nm_ids = sorted(
            {
                int(nm_id)
                for nm_id, row in catalog_by_nm.items()
                if str(row.get("active_wb") or "").lower() not in {"false", "0", "no"}
            }
        )
        stock_rows = WbAnalyticsAdapter(credentials.wb).fetch_wb_warehouse_stocks(nm_ids=target_nm_ids)
        sales_rows = WbStatisticsAdapter(credentials.wb).fetch_sales(
            date_from=f"{args.before_from}T00:00:00", flag=0
        )
        parser_api = ParserDataApiClient()
        parser_summary = parser_api.get("/warehouse/wb/summary")
        parser_comparison = parser_api.get(
            "/warehouse/wb/aggregates/store-period-comparison",
            params={
                "previous_date": args.before_to,
                "current_date": args.after_to,
                "supplier_id": SUPPLIER_ID,
                "query_scope": "union",
                "top_n": 30,
                "page_size": 500,
            },
        )
        query_coverage_response = parser_api.get(
            "/warehouse/wb/aggregates/query-coverage",
            params={"date": args.after_to, "supplier_id": SUPPLIER_ID, "top_n": 30, "limit": 100},
        )
        launch_response = parser_api.get(
            "/warehouse/wb/aggregates/promotion-visibility-candidates",
            params={"date": args.after_to, "supplier_id": SUPPLIER_ID, "top_n": 30, "min_stock": LOW_STOCK, "limit": 500},
        )

    write_json(raw_dir / "campaign_count.json", count_data)
    write_json(raw_dir / "campaigns.json", {"adverts": campaigns})
    write_json(raw_dir / "fullstats_before.json", pre_raw)
    write_json(raw_dir / "fullstats_after.json", post_raw)
    write_json(raw_dir / "stocks.json", {"items": stock_rows})
    write_json(raw_dir / "sales.json", sales_rows)
    write_json(raw_dir / "parser_summary.json", parser_summary)
    write_json(raw_dir / "parser_comparison.json", parser_comparison)
    write_json(raw_dir / "parser_query_coverage.json", query_coverage_response)
    write_json(raw_dir / "parser_launch_candidates.json", launch_response)

    pre_stats = _stats_by_key(pre_raw, campaigns)
    post_stats = _stats_by_key(post_raw, campaigns)
    current_bids = _current_bids_from_campaigns(campaigns)
    meta = _campaign_meta(campaigns)
    memberships: list[dict[str, Any]] = []
    paused_memberships: list[dict[str, Any]] = []
    active_nm_ids: set[str] = set()
    paused_nm_ids: set[str] = set()
    for (advert_id, nm_id, placement), bid in current_bids.items():
        if placement != "search":
            continue
        campaign = meta.get(_integer(advert_id), {})
        if campaign.get("payment_type") != "cpc":
            continue
        membership = {
            "advert_id": advert_id,
            "nm_id": nm_id,
            "current_bid": bid,
            "campaign_name": campaign.get("campaign_name", ""),
        }
        if campaign.get("status") == 9:
            memberships.append(membership)
            active_nm_ids.add(nm_id)
        elif campaign.get("status") == 11:
            paused_memberships.append(membership)
            paused_nm_ids.add(nm_id)

    stocks = _stock_map(stock_rows)
    sales_pre = _sales_by_nm(
        sales_rows,
        date_from=args.before_from,
        date_to=args.before_to,
        catalog_by_sku=catalog_by_sku,
    )
    sales_post = _sales_by_nm(
        sales_rows,
        date_from=args.after_from,
        date_to=args.after_to,
        catalog_by_sku=catalog_by_sku,
    )

    parser_products = _parser_product_map(parser_comparison)

    product_rows = _product_rows(
        memberships=memberships,
        pre_stats=pre_stats,
        post_stats=post_stats,
        cohort_keys=cohort_keys,
        cohort_actions=cohort_actions,
        catalog_by_nm=catalog_by_nm,
        stocks=stocks,
        prices=prices,
        sales_pre=sales_pre,
        sales_post=sales_post,
        parser_products=parser_products,
    )
    cohort_current_keys = {
        (str(row["advert_id"]), str(row["nm_id"]))
        for row in memberships
        if (str(row["advert_id"]), str(row["nm_id"])) in cohort_keys
    }
    control_keys = {
        (str(row["advert_id"]), str(row["nm_id"]))
        for row in memberships
        if (str(row["advert_id"]), str(row["nm_id"])) not in cohort_keys
    }
    increase_keys = {
        key for key, action in cohort_actions.items() if action != "reduce_waste"
    }
    reduce_keys = {
        key for key, action in cohort_actions.items() if action == "reduce_waste"
    }

    def selected(stats: dict[tuple[str, str], dict[str, Any]], keys: set[tuple[str, str]]) -> list[dict[str, Any]]:
        return [stats.get(key, {}) for key in keys]

    comparisons = [
        _period_comparison(selected(pre_stats, cohort_keys), selected(post_stats, cohort_keys), label="Точная когорта 91"),
        _period_comparison(selected(pre_stats, increase_keys), selected(post_stats, increase_keys), label="88 повышений"),
        _period_comparison(selected(pre_stats, reduce_keys), selected(post_stats, reduce_keys), label="3 снижения"),
        _period_comparison(selected(pre_stats, control_keys), selected(post_stats, control_keys), label="Активный CPC-контроль"),
    ]

    cohort_nm_ids = {nm_id for _, nm_id in cohort_keys}
    cohort_sales_pre_rows = [sales_pre.get(nm_id, {}) for nm_id in cohort_nm_ids]
    cohort_sales_post_rows = [sales_post.get(nm_id, {}) for nm_id in cohort_nm_ids]
    all_sales_pre_rows = list(sales_pre.values())
    all_sales_post_rows = list(sales_post.values())

    parser_launch_map = {
        str(row.get("product_id") or ""): row
        for row in launch_response.get("rows") or []
    }
    launch_rows: list[dict[str, Any]] = []
    for nm_id, catalog in catalog_by_nm.items():
        parser_row = parser_products.get(nm_id, {})
        parser_candidate = parser_launch_map.get(nm_id, {})
        name = str(catalog.get("product_name") or parser_candidate.get("product_name") or "")
        sku = str(catalog.get("internal_sku") or "")
        stock = stocks.get(nm_id, 0)
        if (
            nm_id in active_nm_ids
            or nm_id in paused_nm_ids
            or nm_id in cohort_nm_ids
            or stock < LOW_STOCK
            or _is_callsign(name, sku)
        ):
            continue
        actual_sales = _integer(sales_post.get(nm_id, {}).get("sales"))
        visible_queries = _integer(parser_row.get("current_visible_queries"))
        if actual_sales <= 0 and visible_queries <= 0:
            continue
        best_position = _integer(parser_row.get("current_best_position") or parser_candidate.get("best_position"))
        priority = actual_sales * 1000 + max(0, 500 - best_position) + visible_queries * 10
        launch_rows.append(
            {
                "nm_id": nm_id,
                "internal_sku": sku,
                "name": name,
                "stock": stock,
                "actual_sales_post": actual_sales,
                "best_position": best_position,
                "visible_queries": visible_queries,
                "sample_queries": ", ".join(parser_candidate.get("sample_queries") or []),
                "launch_priority_score": priority,
                "launch_priority": "A" if actual_sales > 0 or best_position <= 100 else "B",
            }
        )
    launch_rows.sort(key=lambda row: (-row["launch_priority_score"], row["best_position"]))

    focus_candidates = [
        row
        for row in product_rows
        if row["recommendation"] == "COLLECT_DATA"
        and (
            _integer(row.get("actual_sales_post")) > 0
            or _integer(row.get("parser_current_queries")) > 0
            or _integer(row.get("parser_prev_queries")) > 0
        )
    ]
    for row in focus_candidates:
        actual_sales = _integer(row.get("actual_sales_post"))
        current_queries = _integer(row.get("parser_current_queries"))
        current_best = _integer(row.get("parser_current_best")) or 9999
        row["focus_priority_score"] = (
            actual_sales * 10000
            + current_queries * 1000
            + max(0, 500 - current_best)
            + _integer(row.get("stock"))
        )
        row["focus_priority"] = (
            "A" if actual_sales > 0 else "B" if current_best <= 100 else "C"
        )
        row["focus_target_bid"] = _round2(_decimal(row.get("current_bid")) * Decimal("1.25"))
    test_focus_rows = sorted(
        focus_candidates,
        key=lambda row: (-row["focus_priority_score"], row["internal_sku"]),
    )[:30]

    cohort_source_by_key = {
        (str(row.get("advert_id") or ""), str(row.get("nm_id") or "")): row
        for row in cohort_rows
    }
    inactive_keys = cohort_keys - cohort_current_keys
    reactivation_rows: list[dict[str, Any]] = []
    for membership in paused_memberships:
        key = (str(membership["advert_id"]), str(membership["nm_id"]))
        nm_id = key[1]
        catalog = catalog_by_nm.get(nm_id, {})
        source = cohort_source_by_key.get(key, {})
        post = post_stats.get(key, {})
        parser_row = parser_products.get(nm_id, {})
        name = str(catalog.get("product_name") or source.get("name") or "")
        sku = str(catalog.get("internal_sku") or source.get("internal_sku") or "")
        stock = stocks.get(nm_id, 0)
        sales_count = _integer(sales_post.get(nm_id, {}).get("sales"))
        post_orders = _integer(post.get("orders"))
        visible_queries = _integer(parser_row.get("current_visible_queries"))
        if _is_callsign(name, sku):
            status = "CALLSIGN_HOLD"
        elif stock < LOW_STOCK:
            status = "RESTOCK_FIRST"
        elif post_orders > 0 or sales_count > 0 or visible_queries > 0:
            status = "REACTIVATE_REVIEW"
        else:
            status = "NO_DEMAND_HOLD"
        reactivation_rows.append(
            {
                "advert_id": key[0],
                "campaign": membership.get("campaign_name", ""),
                "nm_id": nm_id,
                "internal_sku": sku,
                "name": name,
                "stock": stock,
                "post_orders": post_orders,
                "post_spend": _round2(_decimal(post.get("spend"))),
                "actual_sales_post": sales_count,
                "parser_current_queries": visible_queries,
                "last_approved_bid": source.get("target_bid", ""),
                "current_paused_bid": _round2(_decimal(membership.get("current_bid"))),
                "cohort_member": "yes" if key in cohort_keys else "no",
                "status": status,
            }
        )
    reactivation_rows.sort(
        key=lambda row: (
            row["status"] != "REACTIVATE_REVIEW",
            -row["actual_sales_post"],
            -row["post_orders"],
        )
    )

    decision_counts = dict(Counter(str(row["recommendation"]) for row in product_rows))
    cohort_pre = comparisons[0]["before"]
    cohort_post = comparisons[0]["after"]
    cohort_sales_pre = _sales_summary(cohort_sales_pre_rows, catalog_by_nm, "cohort_before")
    cohort_sales_post = _sales_summary(cohort_sales_post_rows, catalog_by_nm, "cohort_after")
    all_sales_pre = _sales_summary(all_sales_pre_rows, catalog_by_nm, "store_before")
    all_sales_post = _sales_summary(all_sales_post_rows, catalog_by_nm, "store_after")
    participating = sum(action_discount_counts.values())
    available = sum(1 for row in prices.values() if row.get("action_count", 0) > 0)
    action_history_run_ids = [
        args.before_actions_run,
        "wb_actions_discount_plan_60-50-50_20260722T081854",
        "wb_actions_discount_plan_60-50-50_20260724T161408",
        "wb_actions_discount_plan_60-50-50_20260728T124808",
        args.actions_run,
    ]
    action_history = [
        row
        for run in action_history_run_ids
        if (row := _action_run_stats(args.data_dir, run)).get("status") == "ok"
    ]
    scale_count = decision_counts.get("SCALE_20", 0) + decision_counts.get("SCALE_30", 0)
    waste_count = decision_counts.get("STOP_OR_REDUCE", 0)
    sales_change = _change(cohort_sales_pre["sales"], cohort_sales_post["sales"])
    launch_clause = (
        f"Дополнительно подтверждено {len(launch_rows)} новых непозывных кандидатов вне CPC."
        if launch_rows
        else "Подтвержденных новых кандидатов вне текущих и paused CPC нет."
    )
    conclusion = (
        f"После изменения ставок рекламные заказы точной когорты изменились "
        f"{cohort_pre['orders']} → {cohort_post['orders']}, фактические продажи "
        f"{cohort_sales_pre['sales']} → {cohort_sales_post['sales']} "
        f"({_fmt(sales_change, '%')}). Массово повышать все ставки нельзя: "
        f"доказанное масштабирование есть у {scale_count} строк, а {waste_count} строк уже имеют "
        f"20+ кликов без рекламного заказа. Основной потенциал продаж — перераспределить бюджет "
        f"между этими группами и проверить {sum(row['status'] == 'REACTIVATE_REVIEW' for row in reactivation_rows)} "
        f"кандидатов на возврат. {launch_clause} Нужно вернуть релевантную видимость, потому что parser потерял "
        f"{parser_comparison.get('movement', {}).get('lost_pairs', 0)} связок запрос–товар."
    )
    summary = {
        "run_id": run_id,
        "generated_at": started.isoformat(timespec="seconds"),
        "mode": "read_only",
        "periods": {
            "before": f"{args.before_from}..{args.before_to}",
            "after": f"{args.after_from}..{args.after_to}",
            "excluded_apply_day": "2026-07-21",
        },
        "cohort": {
            "approved_rows": len(cohort_rows),
            "current_active_rows": len(cohort_current_keys),
            "increases": len(increase_keys),
            "reductions": len(reduce_keys),
        },
        "promotion_comparison": comparisons,
        "cohort_pre": cohort_pre,
        "cohort_post": cohort_post,
        "cohort_sales_pre": cohort_sales_pre,
        "cohort_sales_post": cohort_sales_post,
        "store_sales_pre": all_sales_pre,
        "store_sales_post": all_sales_post,
        "decision_counts": decision_counts,
        "new_launch_candidates": len(launch_rows),
        "inactive_cohort_rows": len(inactive_keys),
        "paused_cpc_rows": len(paused_memberships),
        "paused_campaign_reason": args.paused_campaign_reason,
        "reactivation_candidates": sum(
            row["status"] == "REACTIVATE_REVIEW" for row in reactivation_rows
        ),
        "actions": {
            "source_run_id": args.actions_run,
            "total_goods": len(prices),
            "available": available,
            "participating": participating,
            "participating_discount_counts": action_discount_counts,
            "history": action_history,
        },
        "parser": {
            "previous": parser_comparison.get("previous"),
            "current": parser_comparison.get("current"),
            "movement": parser_comparison.get("movement"),
            "metadata": parser_comparison.get("metadata"),
            "complete": parser_comparison.get("complete"),
        },
        "economics_assumptions": {
            "unit_cost": float(UNIT_COST),
            "target_margin_per_piece": float(TARGET_MARGIN_PER_PIECE),
            "sale_retention_before_general": float(SALE_RETENTION_BEFORE_GENERAL),
            "general_expense_per_product": float(GENERAL_EXPENSE_PER_PRODUCT),
            "buyout_factor": float(BUYOUT_FACTOR),
        },
        "conclusion": conclusion,
        "focus_wave_products": len(test_focus_rows),
        "apply_performed": False,
    }

    products_csv = processed_dir / "wb_product_decisions.csv"
    launches_csv = processed_dir / "wb_new_cpc_candidates.csv"
    reactivation_csv = processed_dir / "wb_reactivation_candidates.csv"
    focus_csv = processed_dir / "wb_focus_wave_30.csv"
    comparisons_csv = processed_dir / "wb_period_comparison.csv"
    _write_csv(products_csv, product_rows)
    _write_csv(launches_csv, launch_rows)
    _write_csv(reactivation_csv, reactivation_rows)
    _write_csv(focus_csv, test_focus_rows)
    comparison_flat = []
    for item in comparisons:
        comparison_flat.append({"period": "before", **item["before"]})
        comparison_flat.append({"period": "after", **item["after"]})
    _write_csv(comparisons_csv, comparison_flat)

    xlsx = run_dir / "wb_sales_growth_control.xlsx"
    _write_xlsx(
        xlsx,
        [
            ("Решения CPC", product_rows),
            ("Новые запуски", launch_rows),
            ("Возврат в CPC", reactivation_rows),
            ("Фокусная волна 30", test_focus_rows),
            ("Сравнение", comparison_flat),
            ("Пробелы запросов", query_coverage_response.get("rows") or []),
        ],
    )
    html = run_dir / "wb_sales_growth_control.html"
    markdown = run_dir / "wb_sales_growth_control.md"
    _write_html(
        html,
        summary=summary,
        product_rows=product_rows,
        launch_rows=launch_rows,
        reactivation_rows=reactivation_rows,
        test_focus_rows=test_focus_rows,
        parser=parser_comparison,
        query_coverage=query_coverage_response.get("rows") or [],
    )
    top_scale = [
        row for row in product_rows
        if row["recommendation"] in {"SCALE_30", "SCALE_20"}
    ]
    _write_markdown(markdown, summary, top_scale)
    summary["artifacts"] = {
        "run_dir": str(run_dir),
        "html": str(html),
        "markdown": str(markdown),
        "xlsx": str(xlsx),
        "product_decisions_csv": str(products_csv),
        "new_cpc_candidates_csv": str(launches_csv),
        "reactivation_candidates_csv": str(reactivation_csv),
        "focus_wave_csv": str(focus_csv),
        "period_comparison_csv": str(comparisons_csv),
    }
    write_json(run_dir / "summary.json", summary)
    write_summary_run_manifest(
        data_dir=args.data_dir,
        run_dir=run_dir,
        summary=summary,
        task="wb-sales-growth-control",
        mode="read_only",
        risk="low",
        marketplaces=["wb"],
        inputs={
            "before_from": args.before_from,
            "before_to": args.before_to,
            "after_from": args.after_from,
            "after_to": args.after_to,
            "cohort_csv": str(args.cohort_csv),
            "actions_run": args.actions_run,
            "supplier_id": SUPPLIER_ID,
        },
    )
    print(json.dumps({"run_id": run_id, "summary": summary, "artifacts": summary["artifacts"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
