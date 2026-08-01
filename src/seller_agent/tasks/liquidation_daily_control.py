from __future__ import annotations

import csv
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from html import escape
import json
from pathlib import Path
from typing import Any

from seller_agent.config import AppCredentials
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.marketplaces.ozon.adapter import OzonSellerAdapter
from seller_agent.marketplaces.ozon.performance_adapter import OzonPerformanceAdapter
from seller_agent.marketplaces.wb.analytics_adapter import WbAnalyticsAdapter
from seller_agent.marketplaces.wb.statistics_adapter import WbStatisticsAdapter
from seller_agent.reports.writer import ensure_dir, write_json
from seller_agent.safety.approvals import action_rows_checksum
from seller_agent.tasks.wb_promotion_report import run_wb_promotion_report


DEFAULT_OZON_COHORT = Path(
    "data/runs/2026-07-30/ozon_dormant_reset_plan_docs_fixed_20260730T1050/"
    "ozon_dormant_reset_plan.csv"
)
DEFAULT_WB_COHORT = Path(
    "data/runs/2026-07-30/wb_dormant_liquidation_fresh_preapply_20260730T1421/"
    "liquidation_plan.csv"
)
OZON_APPLY_STARTED_AT = datetime.fromisoformat("2026-07-30T10:46:33+03:00")
WB_APPLY_STARTED_AT = datetime.fromisoformat("2026-07-30T14:31:53+03:00")


def _decimal(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value).replace(" ", "").replace("\u00a0", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _integer(value: Any) -> int:
    return int(_decimal(value))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, Any]], *, fieldnames: list[str] | None = None) -> None:
    ensure_dir(path.parent)
    columns = fieldnames or (list(rows[0]) if rows else [])
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        if columns:
            writer.writeheader()
            writer.writerows(rows)


def _dates(date_from: date, date_to: date) -> list[date]:
    if date_to < date_from:
        return []
    return [date_from + timedelta(days=offset) for offset in range((date_to - date_from).days + 1)]


def _stock_from_ozon_row(row: dict[str, Any]) -> tuple[int, int]:
    present = 0
    reserved = 0
    for stock in row.get("stocks") or []:
        if isinstance(stock, dict) and str(stock.get("type") or "").lower() == "fbo":
            present += _integer(stock.get("present"))
            reserved += _integer(stock.get("reserved"))
    return present, reserved


def _ozon_min_price(row: dict[str, Any]) -> Decimal:
    price = row.get("price") if isinstance(row.get("price"), dict) else {}
    return _decimal(price.get("min_price") or row.get("min_price"))


def _ozon_elastic_active(row: dict[str, Any]) -> bool:
    actions = row.get("marketing_actions") if isinstance(row.get("marketing_actions"), dict) else {}
    if isinstance(actions.get("actions"), list):
        current = actions["actions"]
    elif isinstance(actions.get("current"), list):
        current = actions["current"]
    else:
        current = []
    return any("эласт" in str(item.get("title") or item.get("name") or "").lower() for item in current if isinstance(item, dict))


def _wb_active_cpc_nm_ids(campaigns_payload: Any) -> set[int]:
    adverts = campaigns_payload.get("adverts") if isinstance(campaigns_payload, dict) else []
    result: set[int] = set()
    for advert in adverts if isinstance(adverts, list) else []:
        if not isinstance(advert, dict):
            continue
        for setting in advert.get("nm_settings") or []:
            if not isinstance(setting, dict):
                continue
            bids = setting.get("bids_kopecks") if isinstance(setting.get("bids_kopecks"), dict) else {}
            if _integer(bids.get("search")) > 0:
                result.add(_integer(setting.get("nm_id")))
    result.discard(0)
    return result


def _ozon_seller_orders(postings: list[dict[str, Any]], cohort_offer_ids: set[str]) -> dict[str, dict[str, Decimal | int]]:
    totals: dict[str, dict[str, Decimal | int]] = {}
    for posting in postings:
        if str(posting.get("status") or "").lower() == "cancelled":
            continue
        for product in posting.get("products") or []:
            if not isinstance(product, dict):
                continue
            offer_id = str(product.get("offer_id") or "").strip()
            if offer_id not in cohort_offer_ids:
                continue
            item = totals.setdefault(offer_id, {"units": 0, "value": Decimal("0")})
            quantity = _integer(product.get("quantity"))
            item["units"] = int(item["units"]) + quantity
            item["value"] = Decimal(item["value"]) + _decimal(product.get("price")) * quantity
    return totals


def _wb_seller_orders(rows: list[dict[str, Any]], cohort_nm_ids: set[int]) -> dict[int, dict[str, Decimal | int]]:
    totals: dict[int, dict[str, Decimal | int]] = {}
    for row in rows:
        nm_id = _integer(row.get("nmId"))
        if nm_id not in cohort_nm_ids or bool(row.get("isCancel")):
            continue
        item = totals.setdefault(nm_id, {"units": 0, "value": Decimal("0")})
        item["units"] = int(item["units"]) + 1
        item["value"] = Decimal(item["value"]) + _decimal(row.get("finishedPrice") or row.get("priceWithDisc"))
    return totals


def _ozon_ad_rows(
    *,
    report: dict[str, Any],
    campaign_ids: set[str],
) -> tuple[dict[str, dict[str, Decimal | int]], dict[tuple[str, str], dict[str, Decimal | int]]]:
    totals: dict[str, dict[str, Decimal | int]] = {}
    daily: dict[tuple[str, str], dict[str, Decimal | int]] = {}
    for campaign_id, payload in report.items():
        if str(campaign_id) not in campaign_ids or not isinstance(payload, dict):
            continue
        body = payload.get("report") if isinstance(payload.get("report"), dict) else {}
        for row in body.get("rows") or []:
            if not isinstance(row, dict):
                continue
            sku = str(row.get("sku") or "").strip()
            day = str(row.get("date") or "").strip()
            if not sku or not day:
                continue
            metrics = {
                "views": _integer(row.get("views")),
                "clicks": _integer(row.get("clicks")),
                "to_cart": _integer(row.get("toCart")),
                "orders": _integer(row.get("orders")),
                "spend": _decimal(row.get("moneySpent")),
                "revenue": _decimal(row.get("ordersMoney")),
            }
            _add_metrics(totals.setdefault(sku, _empty_metrics()), metrics)
            _add_metrics(daily.setdefault((sku, day), _empty_metrics()), metrics)
    return totals, daily


def _wb_ad_rows(stats_rows: list[dict[str, Any]]) -> tuple[dict[int, dict[str, Decimal | int]], dict[tuple[int, str], dict[str, Decimal | int]]]:
    totals: dict[int, dict[str, Decimal | int]] = {}
    daily: dict[tuple[int, str], dict[str, Decimal | int]] = {}
    for campaign in stats_rows:
        for day_row in campaign.get("days") or []:
            day = str(day_row.get("date") or "")[:10]
            for app in day_row.get("apps") or []:
                for row in app.get("nms") or []:
                    if not isinstance(row, dict):
                        continue
                    nm_id = _integer(row.get("nmId"))
                    if not nm_id or not day:
                        continue
                    metrics = {
                        "views": _integer(row.get("views")),
                        "clicks": _integer(row.get("clicks")),
                        "to_cart": _integer(row.get("atbs")),
                        "orders": _integer(row.get("orders")),
                        "spend": _decimal(row.get("sum")),
                        "revenue": _decimal(row.get("sum_price")),
                    }
                    _add_metrics(totals.setdefault(nm_id, _empty_metrics()), metrics)
                    _add_metrics(daily.setdefault((nm_id, day), _empty_metrics()), metrics)
    return totals, daily


def _empty_metrics() -> dict[str, Decimal | int]:
    return {"views": 0, "clicks": 0, "to_cart": 0, "orders": 0, "spend": Decimal("0"), "revenue": Decimal("0")}


def _add_metrics(target: dict[str, Decimal | int], source: dict[str, Decimal | int]) -> None:
    for key in ("views", "clicks", "to_cart", "orders"):
        target[key] = int(target[key]) + int(source[key])
    for key in ("spend", "revenue"):
        target[key] = Decimal(target[key]) + Decimal(source[key])


def build_liquidation_rows(
    *,
    ozon_cohort: list[dict[str, str]],
    wb_cohort: list[dict[str, str]],
    ozon_stocks: list[dict[str, Any]],
    ozon_prices: list[dict[str, Any]],
    ozon_current_cpc_skus: set[str],
    ozon_ad_totals: dict[str, dict[str, Decimal | int]],
    ozon_orders: dict[str, dict[str, Decimal | int]],
    wb_stocks: list[dict[str, Any]],
    wb_active_cpc_nm_ids: set[int],
    wb_ad_totals: dict[int, dict[str, Decimal | int]],
    wb_orders: dict[int, dict[str, Decimal | int]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    ozon_stock_map = {str(row.get("offer_id") or ""): _stock_from_ozon_row(row) for row in ozon_stocks}
    ozon_price_map = {str(row.get("offer_id") or ""): row for row in ozon_prices}
    wb_stock_map: dict[int, dict[str, int]] = {}
    for row in wb_stocks:
        nm_id = _integer(row.get("nmId"))
        item = wb_stock_map.setdefault(nm_id, {"quantity": 0, "to": 0, "from": 0})
        item["quantity"] += _integer(row.get("quantity"))
        item["to"] += _integer(row.get("inWayToClient"))
        item["from"] += _integer(row.get("inWayFromClient"))

    ozon_rows: list[dict[str, Any]] = []
    stop_rows: list[dict[str, Any]] = []
    for source in ozon_cohort:
        offer_id = str(source.get("offer_id") or "").strip()
        sku = str(source.get("ozon_sku") or "").strip()
        pack_qty = max(_integer(source.get("pack_qty")), 1)
        ad = ozon_ad_totals.get(sku, _empty_metrics())
        seller = ozon_orders.get(offer_id, {"units": 0, "value": Decimal("0")})
        stop_spend = Decimal("50") * pack_qty
        hard_stop = int(seller["units"]) == 0 and Decimal(ad["spend"]) >= stop_spend
        stock, reserved = ozon_stock_map.get(offer_id, (0, 0))
        price_row = ozon_price_map.get(offer_id, {})
        row = {
            "decision_group": source.get("decision_group", ""),
            "offer_id": offer_id,
            "product_id": source.get("product_id", ""),
            "ozon_sku": sku,
            "title": source.get("title", ""),
            "pack_qty": pack_qty,
            "stock_fbo": stock,
            "reserved_fbo": reserved,
            "elastic_active": _ozon_elastic_active(price_row),
            "cpc_current": sku in ozon_current_cpc_skus,
            "callsign": "позывн" in str(source.get("title") or "").lower(),
            "views": int(ad["views"]),
            "clicks": int(ad["clicks"]),
            "to_cart": int(ad["to_cart"]),
            "ad_orders": int(ad["orders"]),
            "ad_spend": float(Decimal(ad["spend"])),
            "ad_revenue": float(Decimal(ad["revenue"])),
            "seller_order_units": int(seller["units"]),
            "seller_order_value": float(Decimal(seller["value"])),
            "stop_spend": float(stop_spend),
            "hard_stop_reached": hard_stop,
            "live_min_price": float(_ozon_min_price(price_row)),
            "documented_target_min_price": float(_decimal(source.get("target_min_price"))),
            "min_price_document_mismatch": _ozon_min_price(price_row) != _decimal(source.get("target_min_price")),
            "decision": "review_stop" if hard_stop else "keep_monitoring",
        }
        ozon_rows.append(row)
        if hard_stop:
            stop_rows.append({"marketplace": "ozon", "product_id": sku, "offer_id": offer_id, "reason": "spend_limit_without_seller_order", "threshold": float(stop_spend), "observed": row["ad_spend"]})

    wb_rows: list[dict[str, Any]] = []
    for source in wb_cohort:
        nm_id = _integer(source.get("nm_id"))
        ad = wb_ad_totals.get(nm_id, _empty_metrics())
        seller = wb_orders.get(nm_id, {"units": 0, "value": Decimal("0")})
        hard_stop = int(seller["units"]) == 0 and (
            int(ad["clicks"]) >= _integer(source.get("post_apply_click_stop") or 10)
            or Decimal(ad["spend"]) >= _decimal(source.get("post_apply_spend_stop") or 20)
        )
        stock = wb_stock_map.get(nm_id, {"quantity": 0, "to": 0, "from": 0})
        row = {
            "group": source.get("group", ""),
            "nm_id": nm_id,
            "vendor_code": source.get("vendor_code", ""),
            "internal_sku": source.get("internal_sku", ""),
            "title": source.get("title", ""),
            "pack_qty": max(_integer(source.get("pack_qty")), 1),
            "stock_goods": stock["quantity"],
            "in_way_to_client": stock["to"],
            "in_way_from_client": stock["from"],
            "price_mode": source.get("price_mode", ""),
            "action_state": "documented_stage1",
            "active_cpc": nm_id in wb_active_cpc_nm_ids,
            "recommended_bid": float(_decimal(source.get("target_bid_recalc") or source.get("recommended_bid"))),
            "requires_second_price_stage": str(source.get("requires_second_price_stage") or "").lower() == "true",
            "views": int(ad["views"]),
            "clicks": int(ad["clicks"]),
            "to_cart": int(ad["to_cart"]),
            "ad_orders": int(ad["orders"]),
            "ad_spend": float(Decimal(ad["spend"])),
            "ad_revenue": float(Decimal(ad["revenue"])),
            "seller_order_units": int(seller["units"]),
            "seller_order_value": float(Decimal(seller["value"])),
            "stop_clicks": _integer(source.get("post_apply_click_stop") or 10),
            "stop_spend": float(_decimal(source.get("post_apply_spend_stop") or 20)),
            "hard_stop_reached": hard_stop,
            "decision": "review_stop" if hard_stop else "keep_monitoring",
        }
        wb_rows.append(row)
        if hard_stop:
            stop_rows.append({"marketplace": "wb", "product_id": nm_id, "offer_id": source.get("internal_sku", ""), "reason": "click_or_spend_limit_without_seller_order", "threshold": f"{row['stop_clicks']} clicks / {row['stop_spend']} RUB", "observed": f"{row['clicks']} clicks / {row['ad_spend']} RUB"})
    return ozon_rows, wb_rows, stop_rows


def _render_markdown(summary: dict[str, Any]) -> str:
    ozon = summary["ozon"]
    wb = summary["wb"]
    return "\n".join(
        [
            "# Ежедневный контроль распродажи Ozon и WB",
            "",
            f"Run ID: `{summary['run_id']}`",
            f"Период: `{summary['period']['date_from']} - {summary['period']['date_to']}`; только завершённые дни.",
            "",
            "## Ozon",
            f"- когорта: **{ozon['cohort']} SKU**; остаток: **{ozon['stock_products']} товаров / {ozon['stock_physical_items']} изделий**;",
            f"- Elastic: **{ozon['elastic_active']}/{ozon['cohort']}**; CPC: **{ozon['cpc_active']}/{ozon['cohort']}**;",
            f"- Seller API заказы: **{ozon['seller_order_units']}**; рекламная атрибуция: **{ozon['ad_orders']}**; расход CPC: **{ozon['ad_spend']:.2f} руб.**;",
            f"- hard-stop review: **{ozon['hard_stop_candidates']}**; расхождения minimum с документом: **{ozon['min_price_mismatches']}**.",
            "",
            "## Wildberries",
            f"- когорта: **{wb['cohort']} nmID**; остаток: **{wb['stock_goods']} товаров / {wb['stock_physical_items']} изделий**;",
            f"- CPC: **{wb['cpc_active']}/{wb['cohort']}**;",
            f"- Statistics API заказы: **{wb['seller_order_units']}**; рекламная атрибуция: **{wb['ad_orders']}**; расход CPC: **{wb['ad_spend']:.2f} руб.**;",
            f"- hard-stop review: **{wb['hard_stop_candidates']}**; второй ценовой шаг: **{wb['second_price_stage_pending']}**.",
            "",
            "## Решение",
            "Hard-stop строки являются только review-кандидатами. Цены, акции, ставки и состав кампаний не изменялись.",
        ]
    )


def _render_html(summary: dict[str, Any], ozon_rows: list[dict[str, Any]], wb_rows: list[dict[str, Any]]) -> str:
    def table(rows: list[dict[str, Any]], id_key: str) -> str:
        body = "".join(
            f"<tr><td>{escape(str(row[id_key]))}</td><td>{escape(str(row['title']))}</td><td>{row.get('pack_qty')}</td><td>{row.get('stock_fbo', row.get('stock_goods', 0))}</td><td>{row.get('clicks')}</td><td>{row.get('ad_spend')}</td><td>{row.get('seller_order_units')}</td><td>{escape(str(row.get('decision')))}</td></tr>"
            for row in rows
        )
        return f"<div class='wrap'><table><thead><tr><th>ID</th><th>Товар</th><th>Изделий</th><th>Остаток</th><th>Клики</th><th>Расход</th><th>Заказы</th><th>Решение</th></tr></thead><tbody>{body}</tbody></table></div>"

    return f"""<!doctype html><html lang='ru'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Контроль распродажи</title><style>body{{margin:0;background:#f3f5f7;color:#17212b;font:14px/1.45 Arial,sans-serif;letter-spacing:0}}main{{max-width:1400px;margin:auto;padding:20px}}section{{background:#fff;border:1px solid #d8dee6;border-radius:6px;padding:16px;margin:12px 0}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}}.m{{border-left:4px solid #176b55;padding:10px;background:#f7faf9}}.m b{{display:block;font-size:22px}}.wrap{{overflow:auto;max-height:620px}}table{{border-collapse:collapse;width:100%;min-width:980px}}th,td{{padding:8px;border-bottom:1px solid #e1e5ea;text-align:right}}th:nth-child(2),td:nth-child(2){{text-align:left}}th{{position:sticky;top:0;background:#eef2f5}}@media(max-width:800px){{.grid{{grid-template-columns:repeat(2,1fr)}}}}</style></head><body><main><h1>Ежедневный контроль распродажи</h1><p>Run ID: {escape(summary['run_id'])}. Read-only, завершённые дни {summary['period']['date_from']}–{summary['period']['date_to']}.</p><section><h2>Сводка</h2><div class='grid'><div class='m'>Ozon когорта<b>{summary['ozon']['cohort']}</b></div><div class='m'>Ozon stop-review<b>{summary['ozon']['hard_stop_candidates']}</b></div><div class='m'>WB когорта<b>{summary['wb']['cohort']}</b></div><div class='m'>WB stop-review<b>{summary['wb']['hard_stop_candidates']}</b></div></div></section><section><h2>Ozon</h2>{table(ozon_rows, 'ozon_sku')}</section><section><h2>Wildberries</h2>{table(wb_rows, 'nm_id')}</section></main></body></html>"""


def run_liquidation_daily_control(
    *,
    credentials: AppCredentials,
    data_dir: Path = Path("data"),
    date_to: str | None = None,
    ozon_cohort_path: Path = DEFAULT_OZON_COHORT,
    wb_cohort_path: Path = DEFAULT_WB_COHORT,
    run_id: str | None = None,
) -> dict[str, Any]:
    if not credentials.ozon_seller or not credentials.ozon_performance or not credentials.wb:
        raise RuntimeError("Ozon Seller, Ozon Performance and WB credentials are required")
    completed_to = date.fromisoformat(date_to) if date_to else date.today() - timedelta(days=1)
    started_at = datetime.now().astimezone()
    run_id = run_id or f"liquidation_daily_control_{started_at:%Y%m%dT%H%M%S}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    raw_dir = ensure_dir(run_dir / "raw")
    processed_dir = ensure_dir(run_dir / "processed")
    ozon_cohort = _read_csv(ozon_cohort_path)
    wb_cohort = _read_csv(wb_cohort_path)

    ozon_offer_ids = [str(row.get("offer_id") or "") for row in ozon_cohort]
    ozon_product_ids = [str(row.get("product_id") or "") for row in ozon_cohort]
    ozon_skus = {str(row.get("ozon_sku") or "") for row in ozon_cohort}
    wb_nm_ids = {_integer(row.get("nm_id")) for row in wb_cohort}

    ozon_seller = OzonSellerAdapter(credentials.ozon_seller)
    ozon_performance = OzonPerformanceAdapter(credentials.ozon_performance)
    wb_analytics = WbAnalyticsAdapter(credentials.wb)
    wb_statistics = WbStatisticsAdapter(credentials.wb)

    ozon_prices = ozon_seller.fetch_product_info_prices_by_offer_ids(ozon_offer_ids)
    ozon_stocks = ozon_seller.fetch_product_stocks(ozon_product_ids)
    period_to = datetime.combine(completed_to + timedelta(days=1), time.min, tzinfo=OZON_APPLY_STARTED_AT.tzinfo)
    ozon_postings = ozon_seller.fetch_fbo_postings(since=OZON_APPLY_STARTED_AT.isoformat(), to=period_to.isoformat())

    campaigns_payload = ozon_performance.get("/api/client/campaign")
    campaigns = [row for row in campaigns_payload.get("list", []) if isinstance(row, dict) and row.get("PaymentType") == "CPC"]
    campaign_ids = [str(row.get("id")) for row in campaigns if row.get("id")]
    current_ozon_cpc: set[str] = set()
    for campaign in campaigns:
        if campaign.get("state") == "CAMPAIGN_STATE_RUNNING":
            current_ozon_cpc.update(str(row.get("sku") or "") for row in ozon_performance.fetch_campaign_products(str(campaign["id"])))
    report_response = ozon_performance.post("/api/client/statistics/json", {"campaigns": campaign_ids, "dateFrom": OZON_APPLY_STARTED_AT.date().isoformat(), "dateTo": completed_to.isoformat(), "groupBy": "DATE"})
    from scripts.analytics.ozon_cpc_efficiency_report import wait_for_report

    ozon_report = wait_for_report(ozon_performance, str(report_response.get("UUID") or ""), timeout_seconds=900, poll_seconds=10)
    ozon_ad_totals, ozon_daily = _ozon_ad_rows(report=ozon_report, campaign_ids=set(campaign_ids))

    wb_stocks = wb_analytics.fetch_wb_warehouse_stocks(nm_ids=sorted(wb_nm_ids))
    wb_orders_raw = wb_statistics.fetch_orders(date_from=WB_APPLY_STARTED_AT.isoformat(), flag=0)
    wb_promotion = run_wb_promotion_report(credentials=credentials, data_dir=data_dir, run_id=f"wb_promotion_report_{run_id}", date_from=WB_APPLY_STARTED_AT.date().isoformat(), date_to=completed_to.isoformat(), payment_type="cpc")
    wb_stats_raw = json.loads(Path(wb_promotion["artifacts"]["fullstats_raw"]).read_text(encoding="utf-8"))
    wb_campaigns_raw = json.loads(Path(wb_promotion["artifacts"]["campaigns_raw"]).read_text(encoding="utf-8"))
    current_wb_cpc = _wb_active_cpc_nm_ids(wb_campaigns_raw)
    wb_ad_totals, wb_daily = _wb_ad_rows(wb_stats_raw)

    ozon_orders = _ozon_seller_orders(ozon_postings, set(ozon_offer_ids))
    wb_orders = _wb_seller_orders(wb_orders_raw, wb_nm_ids)
    ozon_rows, wb_rows, stop_rows = build_liquidation_rows(
        ozon_cohort=ozon_cohort,
        wb_cohort=wb_cohort,
        ozon_stocks=ozon_stocks,
        ozon_prices=ozon_prices,
        ozon_current_cpc_skus=current_ozon_cpc,
        ozon_ad_totals=ozon_ad_totals,
        ozon_orders=ozon_orders,
        wb_stocks=wb_stocks,
        wb_active_cpc_nm_ids=current_wb_cpc,
        wb_ad_totals=wb_ad_totals,
        wb_orders=wb_orders,
    )

    daily_rows: list[dict[str, Any]] = []
    for day in _dates(OZON_APPLY_STARTED_AT.date(), completed_to):
        for row in ozon_cohort:
            sku = str(row.get("ozon_sku") or "")
            metrics = ozon_daily.get((sku, day.isoformat()), _empty_metrics())
            daily_rows.append({"marketplace": "ozon", "date": day.isoformat(), "product_id": sku, **{key: float(value) if isinstance(value, Decimal) else value for key, value in metrics.items()}})
    for day in _dates(WB_APPLY_STARTED_AT.date(), completed_to):
        for row in wb_cohort:
            nm_id = _integer(row.get("nm_id"))
            metrics = wb_daily.get((nm_id, day.isoformat()), _empty_metrics())
            daily_rows.append({"marketplace": "wb", "date": day.isoformat(), "product_id": nm_id, **{key: float(value) if isinstance(value, Decimal) else value for key, value in metrics.items()}})

    stop_review = {
        "schema": "liquidation_stop_review.v1",
        "run_id": run_id,
        "created_at": started_at.isoformat(timespec="seconds"),
        "mode": "review_only",
        "apply_allowed": False,
        "actions": stop_rows,
        "actions_checksum": action_rows_checksum(stop_rows),
    }
    artifacts = {
        "report": str(run_dir / "report.md"),
        "report_html": str(run_dir / "report.html"),
        "summary": str(run_dir / "summary.json"),
        "ozon_by_product": str(processed_dir / "ozon_by_product.csv"),
        "wb_by_product": str(processed_dir / "wb_by_product.csv"),
        "daily_by_product": str(processed_dir / "daily_by_product.csv"),
        "stop_review": str(processed_dir / "stop_review.json"),
    }
    summary = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "overall_status": "warning" if stop_rows else "ok",
        "mode": "read_only",
        "period": {"date_from": min(OZON_APPLY_STARTED_AT.date(), WB_APPLY_STARTED_AT.date()).isoformat(), "date_to": completed_to.isoformat(), "current_partial_day_excluded": True},
        "ozon": {
            "cohort": len(ozon_rows),
            "stock_products": sum(row["stock_fbo"] for row in ozon_rows),
            "stock_physical_items": sum(row["stock_fbo"] * row["pack_qty"] for row in ozon_rows),
            "seller_order_units": sum(row["seller_order_units"] for row in ozon_rows),
            "ad_orders": sum(row["ad_orders"] for row in ozon_rows),
            "ad_spend": sum(row["ad_spend"] for row in ozon_rows),
            "elastic_active": sum(bool(row["elastic_active"]) for row in ozon_rows),
            "cpc_active": sum(bool(row["cpc_current"]) for row in ozon_rows),
            "hard_stop_candidates": sum(bool(row["hard_stop_reached"]) for row in ozon_rows),
            "min_price_mismatches": sum(bool(row["min_price_document_mismatch"]) for row in ozon_rows),
        },
        "wb": {
            "cohort": len(wb_rows),
            "stock_goods": sum(row["stock_goods"] for row in wb_rows),
            "stock_physical_items": sum(row["stock_goods"] * row["pack_qty"] for row in wb_rows),
            "seller_order_units": sum(row["seller_order_units"] for row in wb_rows),
            "ad_orders": sum(row["ad_orders"] for row in wb_rows),
            "ad_spend": sum(row["ad_spend"] for row in wb_rows),
            "cpc_active": sum(bool(row["active_cpc"]) for row in wb_rows),
            "hard_stop_candidates": sum(bool(row["hard_stop_reached"]) for row in wb_rows),
            "second_price_stage_pending": sum(bool(row["requires_second_price_stage"]) for row in wb_rows),
        },
        "stop_review_checksum": stop_review["actions_checksum"],
        "apply_performed": False,
        "sources": [str(ozon_cohort_path), str(wb_cohort_path), "Ozon Seller API", "Ozon Performance API", "WB Statistics API", "WB Promotion API", "WB Analytics API"],
        "artifacts": artifacts,
    }
    _write_csv(Path(artifacts["ozon_by_product"]), ozon_rows)
    _write_csv(Path(artifacts["wb_by_product"]), wb_rows)
    _write_csv(Path(artifacts["daily_by_product"]), daily_rows)
    write_json(Path(artifacts["stop_review"]), stop_review)
    write_json(raw_dir / "ozon_prices.json", ozon_prices)
    write_json(raw_dir / "ozon_stocks.json", ozon_stocks)
    write_json(raw_dir / "ozon_postings.json", ozon_postings)
    write_json(raw_dir / "ozon_statistics_report.json", ozon_report)
    write_json(raw_dir / "wb_orders.json", wb_orders_raw)
    write_json(raw_dir / "wb_stocks.json", wb_stocks)
    Path(artifacts["report"]).write_text(_render_markdown(summary), encoding="utf-8")
    Path(artifacts["report_html"]).write_text(_render_html(summary, ozon_rows, wb_rows), encoding="utf-8")
    write_json(Path(artifacts["summary"]), summary)
    summary["artifacts"].update(write_summary_run_manifest(data_dir=data_dir, run_dir=run_dir, summary=summary, task="liquidation-daily-control", mode="read_only", risk="low", marketplaces=["ozon", "wb"], inputs={"date_to": completed_to.isoformat(), "ozon_cohort": str(ozon_cohort_path), "wb_cohort": str(wb_cohort_path)}, lifecycle_status="closed", closed=True))
    write_json(Path(artifacts["summary"]), summary)
    return summary
