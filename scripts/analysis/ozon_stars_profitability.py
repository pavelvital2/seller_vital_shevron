#!/usr/bin/env python3
from __future__ import annotations

import argparse
import calendar
from collections import defaultdict
import csv
from datetime import date, datetime, timedelta
from html import escape
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from seller_agent.config import load_credentials
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.marketplaces.ozon.adapter import OzonSellerAdapter


MOSCOW = ZoneInfo("Europe/Moscow")
DELIVERED = "OperationAgentDeliveredToCustomer"
STARS = "StarsMembership"


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _money(value: Any) -> float:
    return round(_number(value) + 1e-9, 2)


def _percent(value: Any) -> float:
    return round(_number(value) * 100, 2)


def _rub(value: Any) -> str:
    return f"{_number(value):,.2f}".replace(",", " ").replace(".", ",") + " ₽"


def _pct(value: Any) -> str:
    return f"{_number(value):.2f}".replace(".", ",") + "%"


def _count(value: Any) -> str:
    return f"{_integer(value):,}".replace(",", " ")


def _month_chunks(start: date, end: date) -> list[tuple[date, date]]:
    chunks: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        month_end = date(
            cursor.year,
            cursor.month,
            calendar.monthrange(cursor.year, cursor.month)[1],
        )
        chunk_end = min(end, month_end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def _catalog_index(path: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8", newline="") as source:
        for row in csv.DictReader(source):
            sku = str(row.get("ozon_sku") or "").strip()
            if sku:
                result[sku] = row
    return result


def _fetch_transactions(
    adapter: OzonSellerAdapter,
    *,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for chunk_start, chunk_end in _month_chunks(start, end):
        rows.extend(
            adapter.fetch_finance_transactions(
                date_from=f"{chunk_start.isoformat()}T00:00:00.000Z",
                date_to=f"{chunk_end.isoformat()}T23:59:59.999Z",
                operation_type=[DELIVERED, STARS],
            )
        )
    return rows


def _item(row: dict[str, Any]) -> dict[str, Any]:
    items = row.get("items") if isinstance(row.get("items"), list) else []
    return items[0] if items and isinstance(items[0], dict) else {}


def _transaction_key(row: dict[str, Any]) -> tuple[str, str]:
    posting = row.get("posting") if isinstance(row.get("posting"), dict) else {}
    return (
        str(posting.get("posting_number") or ""),
        str(_item(row).get("sku") or ""),
    )


def _normalize_transactions(
    rows: list[dict[str, Any]],
    catalog: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    pairs: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        operation_type = str(row.get("operation_type") or "")
        if operation_type in {DELIVERED, STARS}:
            pairs[_transaction_key(row)][operation_type] = row

    result: list[dict[str, Any]] = []
    for (_, sku), pair in pairs.items():
        delivered = pair.get(DELIVERED)
        stars = pair.get(STARS)
        if delivered is None:
            continue
        item = _item(delivered)
        posting = delivered.get("posting") if isinstance(delivered.get("posting"), dict) else {}
        catalog_row = catalog.get(sku) or {}
        quantity = max(1, _integer(item.get("quantity"), 1))
        pack_qty = max(1, _integer(catalog_row.get("pack_qty"), 1))
        gross = _number(delivered.get("accruals_for_sale"))
        stars_fee = abs(_number((stars or {}).get("amount")))
        result.append(
            {
                "delivery_date": str(delivered.get("operation_date") or "")[:10],
                "order_date": str(posting.get("order_date") or "")[:10],
                "sku": sku,
                "internal_sku": str(catalog_row.get("internal_sku") or ""),
                "name": str(catalog_row.get("product_name") or item.get("name") or ""),
                "quantity": quantity,
                "pack_qty": pack_qty,
                "physical_pieces": quantity * pack_qty,
                "gross": gross,
                "stars_fee": stars_fee,
                "stars_matched": stars is not None,
                "stars_rate_pct": stars_fee / gross * 100 if gross > 0 and stars_fee else 0,
                "catalog_mapped": bool(catalog_row),
            }
        )
    return result


def _window(
    transactions: list[dict[str, Any]],
    *,
    start: date,
    end: date,
) -> dict[str, Any]:
    selected = [
        row
        for row in transactions
        if start <= _parse_date(row["delivery_date"]) <= end
    ]
    days = (end - start).days + 1
    units = sum(_integer(row["quantity"]) for row in selected)
    pieces = sum(_integer(row["physical_pieces"]) for row in selected)
    gross = sum(_number(row["gross"]) for row in selected)
    stars_fee = sum(_number(row["stars_fee"]) for row in selected)
    matched = sum(bool(row["stars_matched"]) for row in selected)
    return {
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "days": days,
        "delivered_rows": len(selected),
        "buyout_units": units,
        "physical_pieces": pieces,
        "gross": _money(gross),
        "stars_fee": _money(stars_fee),
        "stars_fee_per_day": _money(stars_fee / days if days else 0),
        "stars_fee_per_product": _money(stars_fee / units if units else 0),
        "stars_fee_per_piece": _money(stars_fee / pieces if pieces else 0),
        "stars_coverage_pct": _percent(matched / len(selected) if selected else 0),
        "units_per_day": round(units / days, 2) if days else 0,
        "pieces_per_day": round(pieces / days, 2) if days else 0,
        "gross_per_day": _money(gross / days if days else 0),
        "mapped_rows_pct": _percent(
            sum(bool(row["catalog_mapped"]) for row in selected) / len(selected)
            if selected
            else 0
        ),
    }


def _daily_rows(
    transactions: list[dict[str, Any]],
    *,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "buyout_units": 0,
            "physical_pieces": 0,
            "gross": 0.0,
            "stars_fee": 0.0,
            "delivered_rows": 0,
            "stars_rows": 0,
        }
    )
    for row in transactions:
        day = row["delivery_date"]
        parsed = _parse_date(day)
        if not start <= parsed <= end:
            continue
        target = grouped[day]
        target["buyout_units"] += _integer(row["quantity"])
        target["physical_pieces"] += _integer(row["physical_pieces"])
        target["gross"] += _number(row["gross"])
        target["stars_fee"] += _number(row["stars_fee"])
        target["delivered_rows"] += 1
        target["stars_rows"] += int(bool(row["stars_matched"]))

    result: list[dict[str, Any]] = []
    cursor = start
    while cursor <= end:
        day = cursor.isoformat()
        values = grouped[day]
        result.append(
            {
                "date": day,
                "buyout_units": values["buyout_units"],
                "physical_pieces": values["physical_pieces"],
                "gross": _money(values["gross"]),
                "stars_fee": _money(values["stars_fee"]),
                "stars_coverage_pct": _percent(
                    values["stars_rows"] / values["delivered_rows"]
                    if values["delivered_rows"]
                    else 0
                ),
            }
        )
        cursor += timedelta(days=1)
    return result


def _monthly_rows(
    transactions: list[dict[str, Any]],
    *,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    result = []
    for chunk_start, chunk_end in _month_chunks(start, end):
        row = _window(transactions, start=chunk_start, end=chunk_end)
        row["month"] = chunk_start.strftime("%Y-%m")
        result.append(row)
    return result


def _product_rows(
    transactions: list[dict[str, Any]],
    *,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in transactions:
        if not start <= _parse_date(row["delivery_date"]) <= end:
            continue
        sku = row["sku"]
        target = grouped.setdefault(
            sku,
            {
                "sku": sku,
                "internal_sku": row["internal_sku"],
                "name": row["name"],
                "pack_qty": row["pack_qty"],
                "buyout_units": 0,
                "physical_pieces": 0,
                "gross": 0.0,
                "stars_fee": 0.0,
            },
        )
        target["buyout_units"] += _integer(row["quantity"])
        target["physical_pieces"] += _integer(row["physical_pieces"])
        target["gross"] += _number(row["gross"])
        target["stars_fee"] += _number(row["stars_fee"])
    rows = []
    for target in grouped.values():
        target["gross"] = _money(target["gross"])
        target["stars_fee"] = _money(target["stars_fee"])
        target["stars_fee_per_piece"] = _money(
            target["stars_fee"] / target["physical_pieces"]
            if target["physical_pieces"]
            else 0
        )
        rows.append(target)
    return sorted(rows, key=lambda row: row["stars_fee"], reverse=True)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _economics(
    *,
    current: dict[str, Any],
    pricing_summary: dict[str, Any],
    unit_cost: float,
    target_margin: float,
) -> dict[str, Any]:
    metrics = pricing_summary.get("metrics") or {}
    net = _number(metrics.get("net"))
    pieces = _integer(metrics.get("physical_pieces"))
    units = _integer(metrics.get("buyout_units"))
    gross = _number(metrics.get("gross"))
    stars_fee = _number(current.get("stars_fee"))
    cogs = pieces * unit_cost
    profit_after_stars = net - cogs
    profit_before_stars = profit_after_stars + stars_fee
    contribution_after_per_product = profit_after_stars / units if units else 0
    contribution_before_per_product = profit_before_stars / units if units else 0
    contribution_after_per_piece = profit_after_stars / pieces if pieces else 0
    expense_by_category = {
        str(row.get("category") or ""): _number(row.get("total"))
        for row in pricing_summary.get("expenses") or []
        if isinstance(row, dict)
    }
    non_incremental_costs = (
        expense_by_category.get("Реклама", 0)
        + expense_by_category.get("Хранение", 0)
    )
    generous_incremental_profit = profit_after_stars + non_incremental_costs
    generous_incremental_margin = (
        generous_incremental_profit / gross if gross > 0 else 0
    )
    actual_break_even_uplift = (
        stars_fee / profit_after_stars if profit_after_stars > 0 else None
    )
    target_profit = pieces * target_margin
    target_break_even_uplift = stars_fee / target_profit if target_profit > 0 else None
    return {
        "unit_cost": _money(unit_cost),
        "target_margin_per_piece": _money(target_margin),
        "cogs": _money(cogs),
        "net_after_marketplace_expenses_before_cogs": _money(net),
        "profit_after_marketplace_expenses_and_cogs": _money(profit_after_stars),
        "profit_without_stars_fee_counterfactual": _money(profit_before_stars),
        "profit_after_stars_per_product": _money(contribution_after_per_product),
        "profit_before_stars_per_product": _money(contribution_before_per_product),
        "profit_after_stars_per_piece": _money(contribution_after_per_piece),
        "stars_share_of_profit_before_stars_pct": _percent(
            stars_fee / profit_before_stars if profit_before_stars > 0 else 0
        ),
        "break_even_extra_products_actual_margin": (
            int(round(stars_fee / contribution_after_per_product))
            if contribution_after_per_product > 0
            else None
        ),
        "break_even_extra_pieces_actual_margin": (
            int(round(stars_fee / contribution_after_per_piece))
            if contribution_after_per_piece > 0
            else None
        ),
        "break_even_sales_uplift_actual_margin_pct": (
            _percent(actual_break_even_uplift)
            if actual_break_even_uplift is not None
            else None
        ),
        "break_even_extra_pieces_target_margin": int(round(stars_fee / target_margin)),
        "break_even_sales_uplift_target_margin_pct": (
            _percent(target_break_even_uplift)
            if target_break_even_uplift is not None
            else None
        ),
        "actual_net_margin_after_stars_pct": _percent(
            profit_after_stars / gross if gross > 0 else 0
        ),
        "generous_incremental_margin_pct": _percent(generous_incremental_margin),
        "generous_incremental_margin_excludes": [
            "Реклама",
            "Хранение",
        ],
    }


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    current = report["windows"]["recent_30_days"]
    last14 = report["windows"]["last_14_days"]
    pre = report["windows"]["before_connection"]
    post = report["windows"]["after_connection"]
    economics = report["economics"]
    loyalty = report["loyalty_lk"]
    post_units_delta = (
        post["units_per_day"] / pre["units_per_day"] - 1
        if pre["units_per_day"]
        else 0
    )
    lines = [
        "# Ozon: прибыльность «Звёздных товаров»",
        "",
        "## Итог",
        "",
        (
            "**Подтверждённой дополнительной прибыли от программы нет. "
            "При текущей экономике её продолжение не обосновано без контрольного отключения.**"
        ),
        "",
        (
            f"- За последние 30 завершённых дней списано **{_rub(current['stars_fee'])}**, "
            f"в среднем **{_rub(current['stars_fee_per_day'])} в день**."
        ),
        (
            f"- После всех расходов Ozon и себестоимости 85 ₽ фактическая прибыль составила "
            f"**{_rub(economics['profit_after_stars_per_piece'])} на физическое изделие**."
        ),
        (
            f"- Чтобы услуга окупилась при этой фактической марже, она должна увеличить продажи "
            f"минимум на **{_pct(economics['break_even_sales_uplift_actual_margin_pct'])}**, "
            f"или примерно на **{economics['break_even_extra_products_actual_margin']} товаров "
            f"за 30 дней**."
        ),
        (
            f"- Даже при целевой марже 50 ₽ требуется прирост минимум "
            f"**{_pct(economics['break_even_sales_uplift_target_margin_pct'])}**."
        ),
        "",
        "## Что подтверждено Ozon",
        "",
        f"- Программа подключена: `{report['program']['activation_date']}`.",
        f"- Текущая комиссия ЛК: **{_pct(report['program']['commission_pct'])} от реализации**.",
        (
            f"- За последние 14 дней ЛК показывает оборот пользователей программы "
            f"**{_rub(loyalty['program_user_turnover'])}** и "
            f"**{_count(loyalty['program_views'])} просмотров**."
        ),
        (
            f"- Этот оборот равен **{_pct(loyalty['share_of_total_gross_pct'])}** "
            "от всех завершённых продаж за сопоставимые 14 дней."
        ),
        (
            "- Ozon не показывает, какая часть этого оборота является приростом относительно "
            "обычных продаж. Поэтому весь оборот пользователей программы нельзя считать эффектом услуги."
        ),
        (
            f"- Даже в щедром сценарии без распределения рекламы и хранения на дополнительные продажи "
            f"для окупаемости нужно, чтобы **{_pct(loyalty['required_incremental_share_generous_pct'])}** "
            "указанного Ozon оборота было полностью новым, а не обычными покупками тех же клиентов."
        ),
        "",
        "## Фактическая экономика",
        "",
        "| Показатель | Значение |",
        "| --- | ---: |",
        f"| Выкупы за 30 дней | {current['buyout_units']} товаров / {current['physical_pieces']} изделий |",
        f"| Оборот завершённых выкупов | {_rub(current['gross'])} |",
        f"| Списания «Звёздных товаров» | {_rub(current['stars_fee'])} |",
        f"| Себестоимость | {_rub(economics['cogs'])} |",
        f"| Прибыль после расходов и себестоимости | {_rub(economics['profit_after_marketplace_expenses_and_cogs'])} |",
        f"| Расчётная прибыль без списания программы | {_rub(economics['profit_without_stars_fee_counterfactual'])} |",
        f"| Программа забирает от прибыли до своего списания | {_pct(economics['stars_share_of_profit_before_stars_pct'])} |",
        "",
        "## Сравнение до и после подключения",
        "",
        "| Период | Товаров в день | Изделий в день | Продажи в день |",
        "| --- | ---: | ---: | ---: |",
        f"| До подключения: {pre['date_from']} - {pre['date_to']} | {pre['units_per_day']} | {pre['pieces_per_day']} | {_rub(pre['gross_per_day'])} |",
        f"| После подключения: {post['date_from']} - {post['date_to']} | {post['units_per_day']} | {post['pieces_per_day']} | {_rub(post['gross_per_day'])} |",
        "",
        (
            f"После подключения продажи товаров в день изменились на **{_pct(post_units_delta * 100)}**. "
            "Это только корреляция: периоды отличаются сезоном, ассортиментом, ценами, остатками и рекламой."
        ),
        "",
        "## Решение",
        "",
        (
            "Рекомендация: **отключить программу на 14 полных дней как контролируемый тест**. "
            "При неизменных ценах, рекламе и наличии экономия составит примерно текущие ежедневные списания."
        ),
        (
            f"Если выкупы снизятся менее чем на **{_pct(economics['break_even_sales_uplift_actual_margin_pct'])}**, "
            "программа была убыточной и её не следует подключать обратно. Если снижение будет выше порога, "
            "нужно повторно сравнить прибыль, а не только количество заказов."
        ),
        "",
        "## Ограничения",
        "",
        "- Программа подключена непрерывно с 24.11.2025, поэтому одновременной контрольной группы без неё нет.",
        "- Сравнение до/после не доказывает причинный эффект.",
        "- Текущая прибыль рассчитана по завершённым финансовым операциям Ozon; текущий день исключён.",
        "- Налог не учтён. Себестоимость физического изделия: 85 ₽.",
        "",
        "## Источники",
        "",
        "- Ozon Seller API `/v3/finance/transaction/list`: выкупы и операции `StarsMembership`.",
        "- Ozon LK `Продвижение -> Лояльность -> Звёздные товары`: ставка, дата подключения и аналитика 14 дней.",
        f"- Модель расходов: `{report['sources']['pricing_summary_path']}`.",
        f"- Собрано: `{report['collected_at']}`.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_html(path: Path, report: dict[str, Any], products: list[dict[str, Any]]) -> None:
    current = report["windows"]["recent_30_days"]
    economics = report["economics"]
    loyalty = report["loyalty_lk"]
    rows = "\n".join(
        "<tr>"
        f"<td><code>{escape(row['internal_sku'] or row['sku'])}</code><br>"
        f"<span>{escape(row['name'])}</span></td>"
        f"<td>{row['pack_qty']}</td>"
        f"<td>{row['buyout_units']}</td>"
        f"<td>{row['physical_pieces']}</td>"
        f"<td>{_rub(row['gross'])}</td>"
        f"<td>{_rub(row['stars_fee'])}</td>"
        "</tr>"
        for row in products[:40]
    )
    html = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Ozon — прибыльность «Звёздных товаров»</title>
  <style>
    :root {{ color-scheme: light; --ink:#17202a; --muted:#65717e; --line:#dce2e7;
      --danger:#a12622; --danger-bg:#fff0ee; --warn:#765a00; --warn-bg:#fff8dc;
      --ok:#21653f; --ok-bg:#eaf7ef; --accent:#005b96; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font:15px/1.5 Arial,sans-serif; color:var(--ink); background:#f5f7f8; }}
    main {{ max-width:1120px; margin:0 auto; padding:24px; }}
    h1 {{ font-size:28px; margin:0 0 8px; letter-spacing:0; }}
    h2 {{ font-size:20px; margin:28px 0 12px; letter-spacing:0; }}
    p {{ margin:8px 0; }}
    .meta {{ color:var(--muted); font-size:13px; }}
    .decision {{ border-left:5px solid var(--danger); background:var(--danger-bg); padding:16px; margin:20px 0; }}
    .decision strong {{ color:var(--danger); }}
    .grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px; }}
    .metric {{ background:#fff; border:1px solid var(--line); border-radius:6px; padding:14px; min-height:96px; }}
    .metric b {{ display:block; font-size:21px; margin-top:5px; }}
    .metric span {{ color:var(--muted); font-size:13px; }}
    .band {{ background:#fff; border:1px solid var(--line); padding:16px; margin-top:14px; }}
    .warning {{ background:var(--warn-bg); border-left:4px solid var(--warn); padding:12px; }}
    table {{ width:100%; min-width:860px; border-collapse:collapse; background:#fff; }}
    th,td {{ text-align:left; padding:9px; border-bottom:1px solid var(--line); vertical-align:top; }}
    th {{ font-size:12px; color:var(--muted); text-transform:uppercase; }}
    td:nth-child(n+2), th:nth-child(n+2) {{ text-align:right; white-space:nowrap; }}
    td:first-child span {{ color:var(--muted); }}
    .table-wrap {{ overflow:auto; border:1px solid var(--line); }}
    code {{ font-size:12px; }}
    ul {{ padding-left:22px; }}
    @media (max-width:760px) {{
      main {{ padding:16px; }}
      h1 {{ font-size:23px; }}
      .grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
    }}
  </style>
</head>
<body>
<main>
  <h1>Ozon: «Звёздные товары»</h1>
  <div class="meta">Read-only анализ · период до {current['date_to']} · собрано {escape(report['collected_at'])}</div>
  <section class="decision">
    <strong>Решение: текущая прибыльность программы не подтверждена.</strong>
    <p>Рекомендован контрольный тест с отключением на 14 полных дней. Продолжать платить без такого теста экономически не обосновано.</p>
  </section>
  <section class="grid">
    <div class="metric"><span>Списано за 30 дней</span><b>{_rub(current['stars_fee'])}</b></div>
    <div class="metric"><span>Среднее списание в день</span><b>{_rub(current['stars_fee_per_day'])}</b></div>
    <div class="metric"><span>Прибыль на изделие после всех расходов</span><b>{_rub(economics['profit_after_stars_per_piece'])}</b></div>
    <div class="metric"><span>Нужный прирост выкупов для окупаемости</span><b>{_pct(economics['break_even_sales_uplift_actual_margin_pct'])}</b></div>
  </section>
  <h2>Что показывает Ozon</h2>
  <section class="band">
    <p>Подключено <b>{escape(report['program']['activation_date'])}</b>; текущая ставка <b>{_pct(report['program']['commission_pct'])}</b>.</p>
    <p>За 14 дней: оборот пользователей программы <b>{_rub(loyalty['program_user_turnover'])}</b>,
      просмотры <b>{_count(loyalty['program_views'])}</b>, доля активных покупателей <b>{_pct(loyalty['active_buyer_share_pct'])}</b>.</p>
    <p class="warning">Это атрибутированный оборот пользователей программы, а не доказанный дополнительный оборот. Ozon не показывает контрфактический результат без услуги.</p>
    <p>Даже в щедром сценарии без распределения рекламы и хранения на новые продажи
      <b>{_pct(loyalty['required_incremental_share_generous_pct'])}</b> этого оборота должно быть полностью добавочным, чтобы окупить услугу.</p>
  </section>
  <h2>Экономика 30 дней</h2>
  <section class="grid">
    <div class="metric"><span>Выкуплено товаров</span><b>{current['buyout_units']}</b></div>
    <div class="metric"><span>Физических изделий</span><b>{current['physical_pieces']}</b></div>
    <div class="metric"><span>Прибыль после Ozon и себестоимости</span><b>{_rub(economics['profit_after_marketplace_expenses_and_cogs'])}</b></div>
    <div class="metric"><span>Доля списания от прибыли до услуги</span><b>{_pct(economics['stars_share_of_profit_before_stars_pct'])}</b></div>
  </section>
  <h2>Практический порог</h2>
  <section class="band">
    <p>При фактической марже услуга должна добавить примерно <b>{economics['break_even_extra_products_actual_margin']} товаров</b>
      за 30 дней, то есть <b>{_pct(economics['break_even_sales_uplift_actual_margin_pct'])}</b> к продажам.</p>
    <p>При целевой марже 50 ₽ на изделие нижняя оценка требуемого прироста:
      <b>{_pct(economics['break_even_sales_uplift_target_margin_pct'])}</b>.</p>
  </section>
  <h2>Товары с наибольшими списаниями за 30 дней</h2>
  <div class="table-wrap">
    <table>
      <thead><tr><th>Товар</th><th>В комплекте</th><th>Товаров</th><th>Изделий</th><th>Продажи</th><th>Списано</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
  <h2>Как принять окончательное решение</h2>
  <section class="band">
    <ol>
      <li>Зафиксировать цены, рекламу и остатки.</li>
      <li>Отключить программу на 14 полных дней отдельным подтверждённым действием.</li>
      <li>Сравнить одинаковые дни недели до и после по выкупам, физическим изделиям и прибыли.</li>
      <li>Не подключать обратно, если снижение выкупов меньше { _pct(economics['break_even_sales_uplift_actual_margin_pct']) }.</li>
    </ol>
  </section>
  <h2>Источники и ограничения</h2>
  <ul>
    <li>Ozon Seller API <code>/v3/finance/transaction/list</code>, операции <code>{DELIVERED}</code> и <code>{STARS}</code>.</li>
    <li>ЛК Vital Shevron: <code>Продвижение → Лояльность → Звёздные товары</code>.</li>
    <li>Себестоимость: 85 ₽ за физическое изделие; налог не учитывался.</li>
    <li>До контрольного отключения причинный прирост продаж подтвердить нельзя.</li>
  </ul>
</main>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--date-to", default="2026-07-28")
    parser.add_argument("--activation-date", default="2025-11-24")
    parser.add_argument("--unit-cost", type=float, default=85)
    parser.add_argument("--target-margin", type=float, default=50)
    parser.add_argument("--program-turnover-14d", type=float, required=True)
    parser.add_argument("--program-views-14d", type=int, required=True)
    parser.add_argument("--active-buyer-share-pct", type=float, required=True)
    parser.add_argument("--commission-pct", type=float, required=True)
    parser.add_argument("--lk-collected-at", required=True)
    parser.add_argument("--pricing-summary", required=True)
    parser.add_argument("--run-id", default="")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    end = _parse_date(args.date_to)
    activation = _parse_date(args.activation_date)
    start = activation - timedelta(days=30)
    recent_start = end - timedelta(days=29)
    last14_start = end - timedelta(days=13)
    run_started = datetime.now(MOSCOW)
    run_id = args.run_id or f"ozon_stars_profitability_{run_started.strftime('%Y%m%dT%H%M%S')}"
    run_dir = data_dir / "runs" / run_started.date().isoformat() / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    credentials = load_credentials()
    if credentials.ozon_seller is None:
        raise RuntimeError("Ozon Seller API credentials are not configured")
    adapter = OzonSellerAdapter(credentials.ozon_seller)
    catalog = _catalog_index(data_dir / "catalog" / "unified" / "products.csv")
    operations = _fetch_transactions(adapter, start=start, end=end)
    transactions = _normalize_transactions(operations, catalog)

    before = _window(
        transactions,
        start=activation - timedelta(days=30),
        end=activation - timedelta(days=1),
    )
    after = _window(
        transactions,
        start=activation,
        end=activation + timedelta(days=29),
    )
    recent = _window(transactions, start=recent_start, end=end)
    last14 = _window(transactions, start=last14_start, end=end)
    pricing_summary = _read_json(Path(args.pricing_summary))
    economics = _economics(
        current=recent,
        pricing_summary=pricing_summary,
        unit_cost=args.unit_cost,
        target_margin=args.target_margin,
    )
    actual_margin = _number(economics["actual_net_margin_after_stars_pct"]) / 100
    generous_margin = _number(economics["generous_incremental_margin_pct"]) / 100
    full_cost_profit_from_program_turnover = args.program_turnover_14d * actual_margin
    generous_profit_from_program_turnover = args.program_turnover_14d * generous_margin
    required_incremental_share_full = (
        _number(last14["stars_fee"]) / full_cost_profit_from_program_turnover
        if full_cost_profit_from_program_turnover > 0
        else 0
    )
    required_incremental_share_generous = (
        _number(last14["stars_fee"]) / generous_profit_from_program_turnover
        if generous_profit_from_program_turnover > 0
        else 0
    )
    program_turnover_share = (
        args.program_turnover_14d / _number(last14["gross"])
        if _number(last14["gross"]) > 0
        else 0
    )
    report = {
        "run_id": run_id,
        "collected_at": run_started.isoformat(timespec="seconds"),
        "mode": "read_only",
        "program": {
            "activation_date": activation.isoformat(),
            "commission_pct": args.commission_pct,
            "scope": "all realized goods while the program is active",
            "status": "active",
        },
        "loyalty_lk": {
            "period": "last_14_days",
            "active_buyer_share_pct": args.active_buyer_share_pct,
            "program_user_turnover": _money(args.program_turnover_14d),
            "program_views": args.program_views_14d,
            "share_of_total_gross_pct": _percent(program_turnover_share),
            "profit_if_all_turnover_incremental_full_cost": _money(
                full_cost_profit_from_program_turnover
            ),
            "profit_if_all_turnover_incremental_generous": _money(
                generous_profit_from_program_turnover
            ),
            "required_incremental_share_full_cost_pct": _percent(
                required_incremental_share_full
            ),
            "required_incremental_share_generous_pct": _percent(
                required_incremental_share_generous
            ),
            "collected_at": args.lk_collected_at,
            "source": "Ozon LK /app/loyalty/sellerpoints",
        },
        "windows": {
            "before_connection": before,
            "after_connection": after,
            "recent_30_days": recent,
            "last_14_days": last14,
        },
        "economics": economics,
        "monthly": _monthly_rows(transactions, start=start, end=end),
        "sources": {
            "finance": "Ozon Seller API /v3/finance/transaction/list",
            "loyalty": "Ozon LK /app/loyalty/sellerpoints",
            "catalog": "data/catalog/unified/products.csv",
            "pricing_summary_path": args.pricing_summary,
        },
        "limitations": [
            "No simultaneous control group without the program.",
            "Before/after comparison is confounded by seasonality, assortment, prices, stock and ads.",
            "Ozon LK program turnover is attributed user turnover, not proven incremental turnover.",
            "Tax is excluded.",
        ],
    }
    products = _product_rows(transactions, start=recent_start, end=end)
    daily = _daily_rows(transactions, start=start, end=end)

    _write_json(run_dir / "summary.json", report)
    _write_json(run_dir / "by_product.json", products)
    _write_csv(run_dir / "daily.csv", daily)
    _write_csv(run_dir / "by_product.csv", products)
    _write_markdown(run_dir / "report.md", report)
    _write_html(run_dir / "report.html", report, products)

    artifacts = {
        "report": str(run_dir / "report.md"),
        "html": str(run_dir / "report.html"),
        "summary": str(run_dir / "summary.json"),
        "daily": str(run_dir / "daily.csv"),
        "by_product": str(run_dir / "by_product.csv"),
    }
    run_summary = {
        "run_id": run_id,
        "started_at": run_started.isoformat(timespec="seconds"),
        "overall_status": "warning",
        "mode": "read_only",
        "decision": "controlled_disable_test_recommended",
        "metrics": {
            "stars_fee_30d": recent["stars_fee"],
            "stars_fee_per_day_30d": recent["stars_fee_per_day"],
            "break_even_sales_uplift_actual_margin_pct": economics[
                "break_even_sales_uplift_actual_margin_pct"
            ],
            "profit_after_stars_per_piece": economics["profit_after_stars_per_piece"],
        },
        "warnings": report["limitations"],
        "artifacts": artifacts,
    }
    manifest_artifacts = write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=run_summary,
        task="ozon-stars-profitability",
        mode="read_only",
        risk="low",
        marketplaces=["ozon"],
        inputs={
            "date_to": end.isoformat(),
            "activation_date": activation.isoformat(),
            "unit_cost": args.unit_cost,
            "target_margin": args.target_margin,
        },
        lifecycle_status="closed",
        closed=True,
    )
    run_summary["artifacts"].update(manifest_artifacts)
    _write_json(run_dir / "run_summary.json", run_summary)
    print(json.dumps(run_summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
