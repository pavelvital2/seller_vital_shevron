#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from html import escape
import json
from pathlib import Path
import time
from typing import Any

from seller_agent.config import load_credentials
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.http import ApiError
from seller_agent.marketplaces.ozon.performance_adapter import OzonPerformanceAdapter


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_PORTFOLIO = (
    DATA_DIR
    / "runs/2026-07-19/ozon_non_elastic_parser_review_20260719T081214/ozon_non_elastic_products.csv"
)


def decimal_value(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value).replace(" ", "").replace("\u00a0", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def int_value(value: Any) -> int:
    return int(decimal_value(value))


def date_value(value: str) -> date:
    value = value.strip()
    for pattern in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, pattern).date()
        except ValueError:
            pass
    raise ValueError(f"Unsupported report date: {value}")


def money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'))}"


def ratio(part: Decimal | int, total: Decimal | int, multiplier: Decimal = Decimal("1")) -> Decimal:
    if not total:
        return Decimal("0")
    return (Decimal(part) / Decimal(total) * multiplier).quantize(Decimal("0.01"))


def read_portfolio(path: Path | None) -> dict[str, dict[str, str]]:
    if path is None or not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return {
            str(row.get("ozon_sku") or "").strip(): row
            for row in csv.DictReader(handle)
            if str(row.get("ozon_sku") or "").strip()
        }


def wait_for_report(
    adapter: OzonPerformanceAdapter,
    report_uuid: str,
    *,
    timeout_seconds: int,
    poll_seconds: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        try:
            report = adapter.get(f"/api/client/statistics/report?UUID={report_uuid}")
        except ApiError as exc:
            if exc.status != 404:
                raise
            last_error = exc.message
            time.sleep(poll_seconds)
            continue
        if not isinstance(report, dict):
            raise RuntimeError("Ozon Performance API returned an unexpected report payload")
        return report
    raise TimeoutError(f"Ozon CPC report was not ready after {timeout_seconds}s: {last_error[:200]}")


def normalize_rows(
    report: dict[str, Any],
    campaign_meta: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for campaign_id, item in report.items():
        if not isinstance(item, dict):
            continue
        report_body = item.get("report") if isinstance(item.get("report"), dict) else {}
        rows = report_body.get("rows") if isinstance(report_body.get("rows"), list) else []
        meta = campaign_meta.get(str(campaign_id), {})
        for source in rows:
            if not isinstance(source, dict) or not source.get("date"):
                continue
            spend = decimal_value(source.get("moneySpent"))
            orders_money = decimal_value(source.get("ordersMoney"))
            clicks = int_value(source.get("clicks"))
            result.append(
                {
                    "campaign_id": str(campaign_id),
                    "campaign_title": str(meta.get("title") or item.get("title") or ""),
                    "date": date_value(str(source.get("date"))),
                    "sku": str(source.get("sku") or "").strip(),
                    "title": str(source.get("title") or "").strip(),
                    "views": int_value(source.get("views")),
                    "clicks": clicks,
                    "to_cart": int_value(source.get("toCart")),
                    "orders": int_value(source.get("orders")),
                    "spend": spend,
                    "orders_money": orders_money,
                    "avg_bid_report": decimal_value(source.get("avgBid")),
                    "product_gmv": decimal_value(source.get("product_gmv")),
                    "cpc": ratio(spend, clicks),
                }
            )
    return result


def metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    views = sum(int(row["views"]) for row in rows)
    clicks = sum(int(row["clicks"]) for row in rows)
    to_cart = sum(int(row["to_cart"]) for row in rows)
    orders = sum(int(row["orders"]) for row in rows)
    spend = sum((Decimal(row["spend"]) for row in rows), Decimal("0"))
    orders_money = sum((Decimal(row["orders_money"]) for row in rows), Decimal("0"))
    return {
        "views": views,
        "clicks": clicks,
        "to_cart": to_cart,
        "orders": orders,
        "spend": spend,
        "orders_money": orders_money,
        "ctr_percent": ratio(clicks, views, Decimal("100")),
        "avg_cpc": ratio(spend, clicks),
        "cart_rate_percent": ratio(to_cart, clicks, Decimal("100")),
        "order_cr_percent": ratio(orders, clicks, Decimal("100")),
        "cpa": ratio(spend, orders),
        "drr_percent": ratio(spend, orders_money, Decimal("100")),
        "roas": ratio(orders_money, spend),
    }


def aggregate_skus(
    rows: list[dict[str, Any]],
    *,
    date_from: date,
    date_to: date,
    current_products: dict[str, dict[str, Any]],
    portfolio: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if date_from <= row["date"] <= date_to and row["sku"]:
            grouped.setdefault(row["sku"], []).append(row)

    result: list[dict[str, Any]] = []
    for sku, sku_rows in grouped.items():
        totals = metrics(sku_rows)
        current = current_products.get(sku, {})
        parser = portfolio.get(sku, {})
        portfolio_code = parser.get("recommendation_code", "")
        if portfolio_code == "block_no_stock":
            signal = "нет остатка"
        elif portfolio_code == "restock_then_recover":
            signal = "сначала пополнить"
        elif totals["orders"] == 0 and totals["spend"] >= Decimal("50"):
            signal = "расход без заказов"
        elif totals["spend"] >= Decimal("100") and totals["drr_percent"] >= Decimal("12"):
            signal = "высокая ДРР"
        elif totals["orders"] >= 8 and totals["drr_percent"] <= Decimal("5"):
            signal = "эффективный кандидат"
        else:
            signal = "наблюдать"
        result.append(
            {
                "sku": sku,
                "title": next((row["title"] for row in sku_rows if row["title"]), ""),
                "campaign_id": sku_rows[0]["campaign_id"],
                "current_bid": ratio(decimal_value(current.get("bid")), Decimal("1000000")),
                "in_current_campaign": bool(current),
                "portfolio_code": portfolio_code,
                "portfolio": parser.get("recommendation", ""),
                "stock": int_value(parser.get("stock")),
                "parser_best_position": int_value(parser.get("best_current_position")),
                "signal": signal,
                **totals,
            }
        )
    return sorted(result, key=lambda row: (row["spend"], row["orders_money"]), reverse=True)


def json_metrics(values: dict[str, Any]) -> dict[str, Any]:
    return {key: float(value) if isinstance(value, Decimal) else value for key, value in values.items()}


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    fieldnames = list(rows[0])
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: money(value) if isinstance(value, Decimal) else value
                    for key, value in row.items()
                }
            )


def metric_card(label: str, value: str, note: str = "") -> str:
    return (
        '<div class="metric"><span>'
        + escape(label)
        + "</span><strong>"
        + escape(value)
        + "</strong><small>"
        + escape(note)
        + "</small></div>"
    )


def format_change(current: Decimal, previous: Decimal, suffix: str = "") -> str:
    if previous == 0:
        return "нет базы сравнения"
    change = ratio(current - previous, previous, Decimal("100"))
    sign = "+" if change > 0 else ""
    return f"{sign}{change}%{suffix}"


def render_html(
    *,
    run_id: str,
    generated_at: str,
    windows: dict[str, dict[str, Any]],
    window_periods: dict[str, tuple[date, date]],
    sku_rows: list[dict[str, Any]],
    campaign_count: int,
    current_product_count: int,
) -> str:
    current = windows["current_15d"]
    previous = windows["previous_15d"]
    thirty = windows["full_30d"]
    problem = [row for row in sku_rows if row["signal"] in {"расход без заказов", "высокая ДРР"}]
    effective = [row for row in sku_rows if row["signal"] == "эффективный кандидат"]
    portfolio = [row for row in sku_rows if row["portfolio_code"] in {"recovery_a", "test_b"}]

    def row_html(row: dict[str, Any]) -> str:
        return "".join(
            [
                '<tr data-search="',
                escape(f"{row['sku']} {row['title']} {row['portfolio']} {row['signal']}".lower()),
                '"><td><code>',
                escape(row["sku"]),
                "</code></td><td>",
                escape(row["title"]),
                "</td><td>",
                escape(row["portfolio_code"] or "-"),
                "</td><td>",
                money(row["current_bid"]),
                "</td><td>",
                str(row["views"]),
                "</td><td>",
                str(row["clicks"]),
                "</td><td>",
                money(row["ctr_percent"]),
                "%</td><td>",
                money(row["spend"]),
                "</td><td>",
                str(row["orders"]),
                "</td><td>",
                money(row["cpa"]),
                "</td><td>",
                money(row["orders_money"]),
                "</td><td>",
                money(row["drr_percent"]),
                "%</td><td><span class=\"tag\">",
                escape(row["signal"]),
                "</span></td></tr>",
            ]
        )

    start_15, end_15 = window_periods["current_15d"]
    start_30, end_30 = window_periods["full_30d"]
    cards = "".join(
        [
            metric_card("Расход, 15 дней", f"{money(current['spend'])} ₽", format_change(current["spend"], previous["spend"])),
            metric_card("CTR, 15 дней", f"{money(current['ctr_percent'])}%", f"было {money(previous['ctr_percent'])}%"),
            metric_card("ДРР, 15 дней", f"{money(current['drr_percent'])}%", f"было {money(previous['drr_percent'])}%"),
            metric_card("Заказы из CPC", str(current["orders"]), format_change(Decimal(current["orders"]), Decimal(previous["orders"]))),
            metric_card("Средний CPC", f"{money(current['avg_cpc'])} ₽", f"CPA {money(current['cpa'])} ₽"),
            metric_card("Выручка из CPC", f"{money(current['orders_money'])} ₽", f"ROAS {money(current['roas'])}"),
        ]
    )
    table_rows = "".join(row_html(row) for row in sku_rows)
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ozon CPC: spend / CTR / DRR</title>
<style>
:root{{--bg:#f4f6f8;--panel:#fff;--ink:#17202a;--muted:#667085;--line:#d8dee6;--accent:#126b55;--warn:#9a3412}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 Arial,sans-serif;letter-spacing:0}}
main{{max-width:1440px;margin:auto;padding:24px}} h1{{font-size:28px;margin:0 0 6px}} h2{{font-size:19px;margin:0 0 14px}} p{{margin:6px 0}}
.meta{{color:var(--muted);margin-bottom:18px}} .band{{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:18px;margin:0 0 16px}}
.grid{{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px}} .metric{{min-height:104px;border-left:4px solid var(--accent);background:#f8fafb;padding:12px}}
.metric span,.metric small{{display:block;color:var(--muted)}} .metric strong{{display:block;font-size:23px;margin:5px 0}} .summary{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}
.summary b{{display:block;font-size:20px}} input{{width:100%;max-width:460px;padding:10px;border:1px solid var(--line);border-radius:4px;margin-bottom:10px}}
.table-wrap{{overflow:auto;max-height:720px;border:1px solid var(--line)}} table{{border-collapse:collapse;width:100%;min-width:1280px}} th,td{{padding:8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}}
th{{position:sticky;top:0;background:#eef2f5;z-index:1}} th:nth-child(2),td:nth-child(2){{text-align:left;white-space:normal;min-width:300px}} td:first-child,th:first-child,td:nth-child(3),th:nth-child(3),td:last-child,th:last-child{{text-align:left}}
.tag{{display:inline-block;border:1px solid var(--line);border-radius:3px;padding:2px 6px}} code{{font-size:12px}} .note{{border-left:4px solid var(--warn);padding-left:10px;color:#59321f}}
@media(max-width:900px){{main{{padding:12px}}h1{{font-size:23px}}.grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}.summary{{grid-template-columns:repeat(2,1fr)}}.metric strong{{font-size:20px}}}}
</style></head><body><main>
<h1>Ozon CPC: spend / CTR / DRR</h1>
<div class="meta">Run ID: {escape(run_id)} · сформирован {escape(generated_at)} · режим read-only</div>
<section class="band"><h2>Последние 15 завершённых дней: {start_15:%d.%m.%Y}–{end_15:%d.%m.%Y}</h2><div class="grid">{cards}</div></section>
<section class="band"><h2>Контроль охвата</h2><div class="summary">
<div><span>Кампаний CPC</span><b>{campaign_count}</b></div><div><span>Товаров в активной кампании</span><b>{current_product_count}</b></div>
<div><span>SKU со статистикой за 30 дней</span><b>{len(sku_rows)}</b></div><div><span>Расход за 30 дней</span><b>{money(thirty['spend'])} ₽</b></div>
</div><p>Период 30 дней: {start_30:%d.%m.%Y}–{end_30:%d.%m.%Y}; показы {thirty['views']}, клики {thirty['clicks']}, CTR {money(thirty['ctr_percent'])}%, заказы {thirty['orders']}, ДРР {money(thirty['drr_percent'])}%.</p></section>
<section class="band"><h2>Сигналы по SKU за 30 дней</h2><div class="summary">
<div><span>Эффективные кандидаты</span><b>{len(effective)}</b></div><div><span>Проблемные строки</span><b>{len(problem)}</b></div><div><span>Портфель A/B в статистике</span><b>{len(portfolio)}</b></div><div><span>Всего строк</span><b>{len(sku_rows)}</b></div>
</div><p class="note">Сигналы являются read-only классификацией. Они не меняют ставки и не являются разрешением на apply.</p></section>
<section class="band"><h2>Детализация по SKU за 30 дней</h2><input id="search" type="search" placeholder="Поиск по SKU, названию, портфелю или сигналу"><div class="table-wrap"><table><thead><tr>
<th>SKU</th><th>Товар</th><th>Портфель</th><th>Ставка, ₽</th><th>Показы</th><th>Клики</th><th>CTR</th><th>Расход, ₽</th><th>Заказы</th><th>CPA, ₽</th><th>Выручка, ₽</th><th>ДРР</th><th>Сигнал</th>
</tr></thead><tbody id="rows">{table_rows}</tbody></table></div></section>
<section class="band"><h2>Источники и ограничения</h2><p>Ozon Performance API: список CPC-кампаний, статистический отчёт с группировкой DATE и текущий состав активной кампании.</p><p>Метрики рассчитаны из сумм: CTR = клики / показы; ДРР = расход / выручка по заказам; CPC = расход / клики; CPA = расход / заказы. Атрибуция соответствует отчёту Ozon.</p></section>
</main><script>const q=document.getElementById('search');q.addEventListener('input',()=>{{const v=q.value.toLowerCase();document.querySelectorAll('#rows tr').forEach(r=>r.hidden=!r.dataset.search.includes(v))}});</script></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a fresh read-only Ozon CPC report")
    parser.add_argument("--date-to", help="Last completed day, YYYY-MM-DD")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--report-uuid", help="Resume an already requested Ozon report")
    parser.add_argument("--portfolio-csv", type=Path, default=DEFAULT_PORTFOLIO)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--poll-seconds", type=int, default=10)
    args = parser.parse_args()

    if args.days != 30:
        raise ValueError("This report currently requires exactly 30 days for two comparable 15-day windows")
    date_to = date.fromisoformat(args.date_to) if args.date_to else date.today() - timedelta(days=1)
    date_from = date_to - timedelta(days=args.days - 1)
    split = date_to - timedelta(days=14)
    previous_to = split - timedelta(days=1)

    credentials = load_credentials().ozon_performance
    if credentials is None:
        raise RuntimeError("Ozon Performance credentials are not configured")
    adapter = OzonPerformanceAdapter(credentials)

    campaigns_payload = adapter.get("/api/client/campaign")
    campaigns = [
        row
        for row in campaigns_payload.get("list", [])
        if isinstance(row, dict) and row.get("PaymentType") == "CPC"
    ]
    campaign_ids = [str(row.get("id")) for row in campaigns if row.get("id")]
    if not campaign_ids:
        raise RuntimeError("No CPC campaigns were returned by Ozon Performance API")
    report_uuid = args.report_uuid
    if not report_uuid:
        response = adapter.post(
            "/api/client/statistics/json",
            {
                "campaigns": campaign_ids,
                "dateFrom": date_from.isoformat(),
                "dateTo": date_to.isoformat(),
                "groupBy": "DATE",
            },
        )
        report_uuid = str(response.get("UUID") or "")
        if not report_uuid:
            raise RuntimeError("Ozon did not return a statistics report UUID")

    report = wait_for_report(
        adapter,
        report_uuid,
        timeout_seconds=args.timeout_seconds,
        poll_seconds=args.poll_seconds,
    )
    campaign_meta = {str(row.get("id")): row for row in campaigns}
    daily_rows = normalize_rows(report, campaign_meta)

    current_products: dict[str, dict[str, Any]] = {}
    for campaign in campaigns:
        if campaign.get("state") != "CAMPAIGN_STATE_RUNNING":
            continue
        for product in adapter.fetch_campaign_products(str(campaign["id"])):
            sku = str(product.get("sku") or "").strip()
            if sku:
                current_products[sku] = product

    portfolio = read_portfolio(args.portfolio_csv)
    window_periods = {
        "current_15d": (split, date_to),
        "previous_15d": (date_from, previous_to),
        "full_30d": (date_from, date_to),
    }
    windows: dict[str, dict[str, Any]] = {}
    for key, (start, end) in window_periods.items():
        windows[key] = metrics([row for row in daily_rows if start <= row["date"] <= end])
    sku_rows = aggregate_skus(
        daily_rows,
        date_from=date_from,
        date_to=date_to,
        current_products=current_products,
        portfolio=portfolio,
    )

    started = datetime.now()
    run_id = f"ozon_cpc_efficiency_{started:%Y%m%dT%H%M%S}"
    run_dir = DATA_DIR / "runs" / started.strftime("%Y-%m-%d") / run_id
    raw_dir = run_dir / "raw"
    processed_dir = run_dir / "processed"
    raw_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "campaigns.json").write_text(
        json.dumps(campaigns_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (raw_dir / "statistics_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (raw_dir / "current_campaign_products.json").write_text(
        json.dumps(list(current_products.values()), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    daily_csv_rows = [
        {**row, "date": row["date"].isoformat()}
        for row in daily_rows
    ]
    write_csv(processed_dir / "daily_rows.csv", daily_csv_rows)
    write_csv(processed_dir / "by_sku_30d.csv", sku_rows)

    serialized_windows = {key: json_metrics(value) for key, value in windows.items()}
    summary = {
        "run_id": run_id,
        "started_at": started.isoformat(timespec="seconds"),
        "status": "ok",
        "overall_status": "ok",
        "mode": "read_only",
        "source": "Ozon Performance API",
        "report_uuid": report_uuid,
        "periods": {
            key: {"date_from": start.isoformat(), "date_to": end.isoformat()}
            for key, (start, end) in window_periods.items()
        },
        "campaigns_cpc": len(campaigns),
        "active_campaign_products": len(current_products),
        "daily_rows": len(daily_rows),
        "sku_rows_30d": len(sku_rows),
        "windows": serialized_windows,
        "signals": {
            "effective_candidates": sum(row["signal"] == "эффективный кандидат" for row in sku_rows),
            "spend_without_orders": sum(row["signal"] == "расход без заказов" for row in sku_rows),
            "high_drr": sum(row["signal"] == "высокая ДРР" for row in sku_rows),
            "portfolio_a_b": sum(row["portfolio_code"] in {"recovery_a", "test_b"} for row in sku_rows),
            "no_stock_or_restock_first": sum(
                row["signal"] in {"нет остатка", "сначала пополнить"} for row in sku_rows
            ),
        },
        "artifacts": {
            "report_html": str(run_dir / "report.html"),
            "report_markdown": str(run_dir / "ozon_cpc_efficiency_report.md"),
            "summary": str(run_dir / "summary.json"),
            "daily_rows_csv": str(processed_dir / "daily_rows.csv"),
            "by_sku_csv": str(processed_dir / "by_sku_30d.csv"),
            "raw_campaigns": str(raw_dir / "campaigns.json"),
            "raw_statistics_report": str(raw_dir / "statistics_report.json"),
            "current_campaign_products": str(raw_dir / "current_campaign_products.json"),
        },
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    current = windows["current_15d"]
    previous = windows["previous_15d"]
    markdown = f"""# Ozon CPC: spend / CTR / DRR

Run ID: `{run_id}`
Режим: `read_only`
Период: `{split.isoformat()}` - `{date_to.isoformat()}`; сравнение с `{date_from.isoformat()}` - `{previous_to.isoformat()}`.

## Последние 15 дней

- расход: `{money(current['spend'])}` руб.;
- показы: `{current['views']}`;
- клики: `{current['clicks']}`;
- CTR: `{money(current['ctr_percent'])}%`;
- средний CPC: `{money(current['avg_cpc'])}` руб.;
- заказы: `{current['orders']}`;
- выручка из отчета Ozon: `{money(current['orders_money'])}` руб.;
- CPA: `{money(current['cpa'])}` руб.;
- ДРР: `{money(current['drr_percent'])}%`;
- ROAS: `{money(current['roas'])}`.

## Предыдущие 15 дней

- расход: `{money(previous['spend'])}` руб.;
- CTR: `{money(previous['ctr_percent'])}%`;
- заказы: `{previous['orders']}`;
- ДРР: `{money(previous['drr_percent'])}%`.

## Контроль

- CPC-кампаний: `{len(campaigns)}`;
- товаров в активной CPC-кампании: `{len(current_products)}`;
- SKU со статистикой за 30 дней: `{len(sku_rows)}`;
- режим read-only, ставки, бюджеты и цены не менялись.

Полная детализация: `report.html` и `processed/by_sku_30d.csv`.
"""
    (run_dir / "ozon_cpc_efficiency_report.md").write_text(markdown, encoding="utf-8")
    (run_dir / "report.html").write_text(
        render_html(
            run_id=run_id,
            generated_at=started.isoformat(timespec="seconds"),
            windows=windows,
            window_periods=window_periods,
            sku_rows=sku_rows,
            campaign_count=len(campaigns),
            current_product_count=len(current_products),
        ),
        encoding="utf-8",
    )
    manifest_artifacts = write_summary_run_manifest(
        data_dir=DATA_DIR,
        run_dir=run_dir,
        summary=summary,
        task="ozon-cpc-efficiency",
        mode="read_only",
        risk="low",
        marketplaces=["ozon"],
        inputs={"date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "days": args.days},
        lifecycle_status="closed",
        closed=True,
    )
    summary["artifacts"].update(manifest_artifacts)
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
