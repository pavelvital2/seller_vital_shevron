#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal
from html import escape
import json
from pathlib import Path
import shutil
from typing import Any

from seller_agent.core.run_manifest import write_summary_run_manifest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"


def decimal_value(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    return Decimal(str(value).replace(",", "."))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def metrics(rows: list[dict[str, str]]) -> dict[str, Decimal]:
    result = {
        key: sum((decimal_value(row.get(key)) for row in rows), Decimal("0"))
        for key in ("views", "clicks", "to_cart", "orders", "spend", "orders_money")
    }
    result.update(
        {
            "ctr_percent": result["clicks"] / result["views"] * 100 if result["views"] else Decimal("0"),
            "avg_cpc": result["spend"] / result["clicks"] if result["clicks"] else Decimal("0"),
            "order_cr_percent": result["orders"] / result["clicks"] * 100 if result["clicks"] else Decimal("0"),
            "cpa": result["spend"] / result["orders"] if result["orders"] else Decimal("0"),
            "drr_percent": result["spend"] / result["orders_money"] * 100
            if result["orders_money"]
            else Decimal("0"),
        }
    )
    return result


def serialize_metrics(values: dict[str, Decimal]) -> dict[str, float | int]:
    integer_keys = {"views", "clicks", "to_cart", "orders"}
    return {
        key: int(value) if key in integer_keys else round(float(value), 2)
        for key, value in values.items()
    }


def percentage_change(previous: Decimal, current: Decimal) -> float | None:
    if not previous:
        return None
    return round(float((current / previous - 1) * 100), 1)


def movement_for_product(rows: list[dict[str, Any]]) -> dict[str, Any]:
    previous = [int(row["previous_position"]) for row in rows if row.get("previous_position") is not None]
    current = [int(row["current_position"]) for row in rows if row.get("current_position") is not None]
    best_previous = min(previous) if previous else None
    best_current = min(current) if current else None
    if best_previous is None and best_current is None:
        status = "not_visible"
    elif best_previous is None:
        status = "new_visible"
    elif best_current is None:
        status = "lost_all"
    elif best_current < best_previous:
        status = "best_improved"
    elif best_current > best_previous:
        status = "best_declined"
    else:
        status = "best_unchanged"
    return {
        "status": status,
        "best_previous": best_previous,
        "best_current": best_current,
        "best_delta": best_current - best_previous
        if best_previous is not None and best_current is not None
        else None,
        "pair_counts": dict(Counter(str(row.get("movement_status") or "") for row in rows)),
    }


def parser_group_metrics(scope: set[str], parser_by_sku: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    products = []
    pair_counts: Counter[str] = Counter()
    intersection: list[dict[str, Any]] = []
    for sku in scope:
        rows = parser_by_sku.get(sku, [])
        movement = movement_for_product(rows)
        products.append(movement)
        pair_counts.update(movement["pair_counts"])
        intersection.extend(
            row
            for row in rows
            if row.get("previous_position") is not None and row.get("current_position") is not None
        )
    previous_average = (
        sum(int(row["previous_position"]) for row in intersection) / len(intersection)
        if intersection
        else None
    )
    current_average = (
        sum(int(row["current_position"]) for row in intersection) / len(intersection)
        if intersection
        else None
    )
    return {
        "scope_products": len(scope),
        "matched_products": sum(bool(parser_by_sku.get(sku)) for sku in scope),
        "previous_visible_products": sum(item["best_previous"] is not None for item in products),
        "current_visible_products": sum(item["best_current"] is not None for item in products),
        "product_status_counts": dict(Counter(item["status"] for item in products)),
        "pair_status_counts": dict(pair_counts),
        "intersection_pairs": len(intersection),
        "average_previous_position": round(previous_average, 2) if previous_average is not None else None,
        "average_current_position": round(current_average, 2) if current_average is not None else None,
        "average_position_change": round(current_average - previous_average, 2)
        if previous_average is not None and current_average is not None
        else None,
    }


def format_number(value: Any, digits: int = 0) -> str:
    if value is None:
        return "-"
    number = float(value)
    return f"{number:,.{digits}f}".replace(",", " ").replace(".", ",")


def metric_table(previous: dict[str, float | int], current: dict[str, float | int]) -> str:
    rows = []
    definitions = (
        ("Показы", "views", 0),
        ("Клики", "clicks", 0),
        ("Расход, руб.", "spend", 2),
        ("Заказы", "orders", 0),
        ("Выручка Ozon, руб.", "orders_money", 2),
        ("CTR, %", "ctr_percent", 2),
        ("Средний CPC, руб.", "avg_cpc", 2),
        ("Конверсия клик -> заказ, %", "order_cr_percent", 2),
        ("CPA, руб.", "cpa", 2),
        ("ДРР, %", "drr_percent", 2),
    )
    for label, key, digits in definitions:
        change = percentage_change(decimal_value(previous[key]), decimal_value(current[key]))
        change_text = "-" if change is None else f"{change:+.1f}%"
        tone = "bad" if key in {"spend", "avg_cpc", "cpa", "drr_percent"} and change and change > 0 else ""
        rows.append(
            f"<tr><th>{escape(label)}</th><td>{format_number(previous[key], digits)}</td>"
            f"<td>{format_number(current[key], digits)}</td><td class='{tone}'>{change_text}</td></tr>"
        )
    return "".join(rows)


def format_period(dates: list[str]) -> str:
    if not dates:
        return "-"
    parsed = sorted(date.fromisoformat(value) for value in dates)
    if len(parsed) == 1:
        return parsed[0].strftime("%d.%m.%Y")
    return f"{parsed[0]:%d.%m.%Y}-{parsed[-1]:%d.%m.%Y}"


def summarize_result(summary: dict[str, Any]) -> tuple[str, str]:
    growth = summary["growth"]
    changes = growth["changes_percent"]
    parser_growth = summary["parser"]["growth"]
    order_change = changes.get("orders")
    spend_change = changes.get("spend")
    drr = decimal_value(growth["post"]["drr_percent"])
    position_change = parser_growth.get("average_position_change")
    decisions = summary.get("guardrails", {}).get("growth_decision_counts", {})
    keep_count = decisions.get("оставить текущую ставку", 0)
    day_watch_count = decisions.get("контроль 24 часа", 0)

    visibility = "Сопоставимая средняя позиция не рассчитана."
    if position_change is not None:
        if position_change < 0:
            visibility = (
                f"Средняя позиция сопоставимых пар улучшилась на "
                f"{format_number(abs(position_change), 1)} места."
            )
        elif position_change > 0:
            visibility = (
                f"Средняя позиция сопоставимых пар ухудшилась на "
                f"{format_number(position_change, 1)} места."
            )
        else:
            visibility = "Средняя позиция сопоставимых пар не изменилась."

    if order_change is not None and order_change > 5 and drr <= Decimal("8"):
        conclusion = "Рост заказов подтверждён при приемлемой ДРР."
        recommendation = (
            "Сохранить текущие ставки у эффективных SKU. Точечно вынести в dry-run "
            "только товары с расходом без заказов или ДРР выше контрольного порога."
        )
    elif spend_change is not None and spend_change > 5 and (order_change or 0) <= 5:
        conclusion = "Дополнительный расход пока не дал сопоставимого роста заказов."
        recommendation = (
            f"Не повышать ставки повторно. Сохранить текущие ставки у {keep_count} "
            f"эффективных SKU; {day_watch_count} товара проверить через 24 часа и "
            "выносить в dry-run снижения только при расходе от 50 руб. без заказа."
        )
    elif drr <= Decimal("8"):
        conclusion = "Когорта остаётся экономически приемлемой, но рост продаж слабый."
        recommendation = (
            "Сохранить текущие ставки и продолжить точечный контроль; массовое "
            "изменение по этой выборке не обосновано."
        )
    else:
        conclusion = "ДРР когорты вышла за контрольный порог."
        recommendation = (
            "Подготовить отдельный dry-run снижения только для убыточных SKU, "
            "сохранив товары с заказами и приемлемой ДРР."
        )
    return f"{conclusion} {visibility}", recommendation


def render_html(
    summary: dict[str, Any],
    products: list[dict[str, Any]],
    reductions: list[dict[str, Any]],
) -> str:
    growth = summary["growth"]
    reduction = summary["reductions"]
    parser_growth = summary["parser"]["growth"]
    parser_control = summary["parser"]["control"]
    conclusion, recommendation = summarize_result(summary)
    pre_period = format_period(summary["periods"]["pre"])
    post_period = format_period(summary["periods"]["post"])
    matched_pre_period = format_period(summary["periods"].get("matched_pre", []))
    position_change = parser_growth.get("average_position_change")
    position_tone = "good" if position_change is not None and position_change < 0 else "bad"
    position_value = (
        f"{format_number(parser_growth['average_previous_position'], 1)} -> "
        f"{format_number(parser_growth['average_current_position'], 1)}"
    )
    product_rows = []
    for row in products:
        product_rows.append(
            "<tr>"
            f"<td><code>{escape(row['sku'])}</code><br><small>{escape(row['title'])}</small></td>"
            f"<td>{format_number(row['old_bid'], 2)} -> {format_number(row['new_bid'], 2)}</td>"
            f"<td>{format_number(row['post_spend'], 2)}</td>"
            f"<td>{format_number(row['post_orders'])}</td>"
            f"<td>{format_number(row['post_drr_percent'], 2)}</td>"
            f"<td>{format_number(row['best_previous'])} -> {format_number(row['best_current'])}</td>"
            f"<td>{escape(row['decision'])}</td>"
            "</tr>"
        )
    reduction_rows = []
    for row in reductions:
        reduction_rows.append(
            "<tr>"
            f"<td><code>{escape(row['sku'])}</code><br><small>{escape(row['title'])}</small></td>"
            f"<td>{format_number(row['old_bid'], 2)} -> {format_number(row['new_bid'], 2)}</td>"
            f"<td>{format_number(row['post_spend'], 2)}</td>"
            f"<td>{format_number(row['post_orders'])}</td>"
            f"<td>{format_number(row['post_drr_percent'], 2)}</td>"
            f"<td>{format_number(row['best_previous'])} -> {format_number(row['best_current'])}</td>"
            f"<td>{escape(row['decision'])}</td>"
            "</tr>"
        )
    decision_counts = summary["guardrails"]["growth_decision_counts"]
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ozon CPC: контроль после изменения ставок</title>
<style>
:root{{--bg:#f4f6f8;--panel:#fff;--ink:#17202a;--muted:#5f6b76;--line:#d9e0e6;--good:#17653a;--bad:#a12b25;--warn:#8a5a00}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 Arial,sans-serif}}
main{{max-width:1180px;margin:auto;padding:18px}}h1{{font-size:25px;margin:0 0 6px}}h2{{font-size:18px;margin:0 0 12px}}
.muted{{color:var(--muted)}}.band{{background:#17202a;color:#fff;padding:16px;margin-bottom:14px;border-radius:6px}}
.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:14px 0}}
.kpi,.section{{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:14px}}
.kpi b{{display:block;font-size:23px;margin-top:4px}}.section{{margin:12px 0}}.good{{color:var(--good)}}.bad{{color:var(--bad)}}.warn{{color:var(--warn)}}
table{{width:100%;border-collapse:collapse}}th,td{{padding:8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{background:#f7f9fa}}
.scroll{{overflow-x:auto}}code{{font-size:12px}}ul{{margin:8px 0;padding-left:20px}}
@media(max-width:760px){{main{{padding:10px}}.grid{{grid-template-columns:1fr 1fr}}h1{{font-size:21px}}table{{min-width:760px}}}}
</style></head><body><main>
<div class="band"><h1>Ozon CPC: контроль после изменения ставок</h1>
<div>Run: <code>{escape(summary['run_id'])}</code> · read-only · сформирован {escape(summary['generated_at'])}</div></div>
<section class="section"><h2>Итог</h2><p><b>{escape(conclusion)}</b></p><p>{escape(recommendation)}</p></section>
<div class="grid">
<div class="kpi">Усилено товаров<b>{growth['products']}</b><span class="muted">точная когорта apply</span></div>
<div class="kpi">Видимость<b>{parser_growth['previous_visible_products']} -> {parser_growth['current_visible_products']}</b><span class="muted">товаров в {summary['parser']['query_count']} запросах</span></div>
<div class="kpi">Средняя позиция<b class="{position_tone}">{position_value}</b><span class="muted">{parser_growth['intersection_pairs']} сопоставимых пар</span></div>
<div class="kpi">ДРР после<b>{format_number(growth['post']['drr_percent'], 2)}%</b><span class="muted">цель портфеля не выше 8%</span></div>
</div>
<div class="grid">
<div class="kpi">Оставить ставку<b class="good">{decision_counts.get('оставить текущую ставку', 0)}</b><span class="muted">есть заказы, ДРР до 8%</span></div>
<div class="kpi">Контроль 24 часа<b class="warn">{decision_counts.get('контроль 24 часа', 0)}</b><span class="muted">расход 30-50 руб. без заказа</span></div>
<div class="kpi">Позиция и конверсия<b>{decision_counts.get('контроль позиции и конверсии', 0)}</b><span class="muted">позиция ухудшилась, заказов нет</span></div>
<div class="kpi">Недостаточно данных<b>{decision_counts.get('наблюдать', 0)}</b><span class="muted">без немедленного изменения</span></div>
</div>
<section class="section"><h2>Эффективность {growth['products']} повышенных ставок</h2><p class="muted">До: {pre_period}. После: {post_period}. День apply исключён, оба окна состоят из полных календарных дней. Одновременно изменялись цены, поэтому причинность CPC отдельно не доказана.</p><div class="scroll"><table><thead><tr><th>Метрика</th><th>До</th><th>После</th><th>Изменение</th></tr></thead><tbody>{metric_table(growth['pre'], growth['post'])}</tbody></table></div></section>
<section class="section"><h2>Контроль по тем же дням недели</h2><p class="muted">{matched_pre_period} против {post_period}: понедельник-среда предыдущей и текущей недели.</p><div class="scroll"><table><thead><tr><th>Метрика</th><th>Предыдущая неделя</th><th>После</th><th>Изменение</th></tr></thead><tbody>{metric_table(growth['matched_pre'], growth['post'])}</tbody></table></div></section>
<section class="section"><h2>Позиции {escape(summary['parser']['previous_date'])} -> {escape(summary['parser']['current_date'])}</h2>
<ul><li>Группа повышенных ставок: улучшились лучшие позиции у <b>{parser_growth['product_status_counts'].get('best_improved',0)}</b> товаров, ухудшились у <b>{parser_growth['product_status_counts'].get('best_declined',0)}</b>, впервые появились <b>{parser_growth['product_status_counts'].get('new_visible',0)}</b>, полностью потеряны у <b>{parser_growth['product_status_counts'].get('lost_all',0)}</b>.</li>
<li>По парам товар + запрос: <b>{parser_growth['pair_status_counts'].get('improved', 0)}</b> улучшений и <b>{parser_growth['pair_status_counts'].get('new', 0)}</b> новых против <b>{parser_growth['pair_status_counts'].get('declined', 0)}</b> ухудшений и <b>{parser_growth['pair_status_counts'].get('lost', 0)}</b> потерь.</li>
<li>Контрольная группа без повышения: видимых товаров стало <b>{parser_control['previous_visible_products']} -> {parser_control['current_visible_products']}</b>, средняя сопоставимая позиция ухудшилась <b>{format_number(parser_control['average_previous_position'],1)} -> {format_number(parser_control['average_current_position'],1)}</b>.</li></ul></section>
<section class="section"><h2>Что делать</h2><ol><li><b>{escape(recommendation)}</b></li><li>Любое снижение или исключение оформить отдельным dry-run; этот отчёт ничего в Ozon не меняет.</li><li>Через семь полных дней после apply повторить устойчивый контроль, чтобы отделить краткосрочную волатильность выдачи.</li></ol></section>
<section class="section"><h2>{growth['products']} повышенных ставок: результат по SKU</h2><div class="scroll"><table><thead><tr><th>SKU / товар</th><th>Ставка</th><th>Расход после</th><th>Заказы</th><th>ДРР</th><th>Лучшая позиция</th><th>Решение</th></tr></thead><tbody>{''.join(product_rows)}</tbody></table></div></section>
<section class="section"><h2>{reduction['products']} сниженных ставок: проверка экономии</h2><div class="scroll"><table><thead><tr><th>SKU / товар</th><th>Ставка</th><th>Расход после</th><th>Заказы</th><th>ДРР</th><th>Лучшая позиция</th><th>Решение</th></tr></thead><tbody>{''.join(reduction_rows)}</tbody></table></div></section>
<section class="section"><h2>Источники и ограничения</h2><ul><li>Ozon Performance API: статистика по полным дням до {escape(summary['performance_date_to'])}, источник <code>{escape(summary['performance_run_id'])}</code>.</li><li>Apply: <code>{escape(summary['source_apply_run_id'])}</code>, {growth['products']} повышения и {reduction['products']} снижений ставок.</li><li>Parser Data API: <code>/warehouse/ozon/aggregates/store-period-comparison</code>, seller_slug <code>vital-shevron</code>, даты {escape(summary['parser']['previous_date'])} -> {escape(summary['parser']['current_date'])}, warehouse {escape(summary['parser']['warehouse_built_at_utc'])}, {summary['parser']['returned_rows']} строк, complete={str(summary['parser']['complete']).lower()}.</li><li>Парсер охватывает {summary['parser']['query_count']} запросов и глубину до 500 позиций, а не всю поисковую выдачу Ozon. Одновременно менялись цены и CPC, поэтому результат является совместным эффектом.</li></ul></section>
</main></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Review Ozon CPC results after an exact bid apply")
    parser.add_argument("--daily-csv", type=Path, required=True)
    parser.add_argument("--bid-apply-csv", type=Path, required=True)
    parser.add_argument("--parser-json", type=Path, required=True)
    parser.add_argument("--pre-dates", required=True)
    parser.add_argument("--matched-pre-dates", required=True)
    parser.add_argument("--post-dates", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--performance-run-id", required=True)
    parser.add_argument("--source-apply-run-id", required=True)
    parser.add_argument("--output-date", help="Run directory date, YYYY-MM-DD")
    args = parser.parse_args()

    pre_dates = {item.strip() for item in args.pre_dates.split(",") if item.strip()}
    matched_pre_dates = {
        item.strip() for item in args.matched_pre_dates.split(",") if item.strip()
    }
    post_dates = {item.strip() for item in args.post_dates.split(",") if item.strip()}
    daily_rows = read_csv(args.daily_csv)
    bid_rows = read_csv(args.bid_apply_csv)
    parser_payload = json.loads(args.parser_json.read_text(encoding="utf-8"))
    growth_rows = [row for row in bid_rows if row.get("action") == "increase_growth"]
    reduction_rows = [row for row in bid_rows if row.get("action") == "reduce_high_drr"]
    growth_skus = {row["sku"] for row in growth_rows}
    reduction_skus = {row["sku"] for row in reduction_rows}

    parser_by_sku: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in parser_payload.get("rows") or []:
        sku = str(row.get("normalized_sku") or "")
        parser_by_sku[sku.removeprefix("OZN")].append(row)

    def cohort_metrics(scope: set[str], dates: set[str]) -> dict[str, Decimal]:
        return metrics([row for row in daily_rows if row.get("sku") in scope and row.get("date") in dates])

    growth_pre = cohort_metrics(growth_skus, pre_dates)
    growth_matched_pre = cohort_metrics(growth_skus, matched_pre_dates)
    growth_post = cohort_metrics(growth_skus, post_dates)
    reduction_pre = cohort_metrics(reduction_skus, pre_dates)
    reduction_matched_pre = cohort_metrics(reduction_skus, matched_pre_dates)
    reduction_post = cohort_metrics(reduction_skus, post_dates)

    post_by_sku: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in daily_rows:
        if row.get("sku") in growth_skus | reduction_skus and row.get("date") in post_dates:
            post_by_sku[row["sku"]].append(row)

    def product_result(bid: dict[str, str], *, increased: bool) -> dict[str, Any]:
        sku = bid["sku"]
        post = metrics(post_by_sku.get(sku, []))
        movement = movement_for_product(parser_by_sku.get(sku, []))
        if not increased and post["orders"] >= 1 and post["drr_percent"] <= Decimal("8"):
            decision = "снижение эффективно: оставить"
        elif not increased and post["orders"] == 0 and post["spend"] < Decimal("20"):
            decision = "экономия подтверждена, наблюдать продажи"
        elif post["orders"] == 0 and post["spend"] >= Decimal("50"):
            decision = "кандидат на dry-run снижения/исключения"
        elif post["spend"] >= Decimal("50") and post["drr_percent"] > Decimal("12"):
            decision = "высокая ДРР: кандидат на dry-run снижения"
        elif post["orders"] == 0 and post["spend"] >= Decimal("30"):
            decision = "контроль 24 часа"
        elif post["orders"] >= 1 and post["drr_percent"] <= Decimal("8"):
            decision = "оставить текущую ставку"
        elif post["orders"] == 0 and movement["status"] == "best_declined":
            decision = "контроль позиции и конверсии"
        else:
            decision = "наблюдать"
        return {
            "sku": sku,
            "title": bid.get("title") or "",
            "old_bid": float(decimal_value(bid.get("current_bid"))),
            "new_bid": float(decimal_value(bid.get("target_bid"))),
            "post_spend": round(float(post["spend"]), 2),
            "post_orders": int(post["orders"]),
            "post_revenue": round(float(post["orders_money"]), 2),
            "post_drr_percent": round(float(post["drr_percent"]), 2),
            "best_previous": movement["best_previous"],
            "best_current": movement["best_current"],
            "best_delta": movement["best_delta"],
            "movement_status": movement["status"],
            "decision": decision,
        }

    products = [product_result(bid, increased=True) for bid in growth_rows]
    reductions = [product_result(bid, increased=False) for bid in reduction_rows]
    products.sort(key=lambda row: (-row["post_spend"], row["sku"]))
    reductions.sort(key=lambda row: (-row["post_spend"], row["sku"]))

    growth_parser = parser_group_metrics(growth_skus, parser_by_sku)
    control_parser = parser_group_metrics(
        set(parser_by_sku) - growth_skus - reduction_skus,
        parser_by_sku,
    )
    reduction_parser = parser_group_metrics(reduction_skus, parser_by_sku)
    zero_order_products = [row for row in products if row["post_orders"] == 0]
    near_stop = max(zero_order_products, key=lambda row: row["post_spend"]) if zero_order_products else None
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    output_date = args.output_date or datetime.now().astimezone().date().isoformat()
    summary = {
        "run_id": args.run_id,
        "generated_at": generated_at,
        "mode": "read_only",
        "overall_status": "warning",
        "apply_performed": False,
        "source_apply_run_id": args.source_apply_run_id,
        "performance_run_id": args.performance_run_id,
        "performance_date_to": max(post_dates),
        "periods": {
            "pre": sorted(pre_dates),
            "matched_pre": sorted(matched_pre_dates),
            "post": sorted(post_dates),
        },
        "growth": {
            "products": len(growth_skus),
            "pre": serialize_metrics(growth_pre),
            "matched_pre": serialize_metrics(growth_matched_pre),
            "post": serialize_metrics(growth_post),
            "changes_percent": {
                key: percentage_change(growth_pre[key], growth_post[key])
                for key in growth_pre
            },
            "matched_changes_percent": {
                key: percentage_change(growth_matched_pre[key], growth_post[key])
                for key in growth_matched_pre
            },
        },
        "reductions": {
            "products": len(reduction_skus),
            "pre": serialize_metrics(reduction_pre),
            "matched_pre": serialize_metrics(reduction_matched_pre),
            "post": serialize_metrics(reduction_post),
            "changes_percent": {
                key: percentage_change(reduction_pre[key], reduction_post[key])
                for key in reduction_pre
            },
        },
        "parser": {
            "previous_date": parser_payload.get("previous_date"),
            "current_date": parser_payload.get("current_date"),
            "warehouse_built_at_utc": (parser_payload.get("metadata") or {}).get("warehouse_built_at_utc"),
            "query_count": parser_payload.get("query_count"),
            "returned_rows": parser_payload.get("returned_rows"),
            "complete": parser_payload.get("complete"),
            "growth": growth_parser,
            "control": control_parser,
            "reductions": reduction_parser,
        },
        "guardrails": {
            "near_stop_sku": near_stop["sku"] if near_stop else None,
            "near_stop_spend": near_stop["post_spend"] if near_stop else 0,
            "near_stop_orders": near_stop["post_orders"] if near_stop else 0,
            "growth_decision_counts": dict(Counter(row["decision"] for row in products)),
            "reduction_decision_counts": dict(Counter(row["decision"] for row in reductions)),
        },
        "limitations": [
            "The apply day and the current partial day are excluded from the primary comparison.",
            "Prices and CPC bids changed in one sequence, so the isolated causal effect of bids cannot be confirmed.",
            "Parser visibility covers 30 collected queries and top 500 positions, not the complete Ozon search universe.",
        ],
    }

    run_dir = DATA_DIR / "runs" / output_date / args.run_id
    processed_dir = run_dir / "processed"
    raw_dir = run_dir / "raw"
    processed_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    with (processed_dir / "growth_products.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(products[0]))
        writer.writeheader()
        writer.writerows(products)
    with (processed_dir / "reduced_products.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(reductions[0]))
        writer.writeheader()
        writer.writerows(reductions)
    shutil.copy2(args.parser_json, raw_dir / "parser_store_period_comparison.json")
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "report.html").write_text(
        render_html(summary, products, reductions),
        encoding="utf-8",
    )
    conclusion, recommendation = summarize_result(summary)
    growth_change = summary["growth"]["changes_percent"]
    markdown = f"""# Ozon CPC: контроль после изменения ставок

Итог: {conclusion}

- период до: `{format_period(sorted(pre_dates))}`;
- те же дни недели до: `{format_period(sorted(matched_pre_dates))}`;
- период после: `{format_period(sorted(post_dates))}`;
- расход: `{growth_pre['spend']:.2f} -> {growth_post['spend']:.2f}` руб. (`{growth_change['spend']:+.1f}%`);
- заказы: `{int(growth_pre['orders'])} -> {int(growth_post['orders'])}` (`{growth_change['orders']:+.1f}%`);
- средний CPC: `{growth_pre['avg_cpc']:.2f} -> {growth_post['avg_cpc']:.2f}` руб.;
- ДРР: `{growth_pre['drr_percent']:.2f}% -> {growth_post['drr_percent']:.2f}%`;
- видимые товары: `{growth_parser['previous_visible_products']} -> {growth_parser['current_visible_products']}`;
- средняя позиция сопоставимых пар: `{growth_parser['average_previous_position']} -> {growth_parser['average_current_position']}`.

Рекомендация: {recommendation}

Режим read-only. Цены, ставки, кампании и карточки не изменялись.
"""
    (run_dir / "summary.md").write_text(markdown, encoding="utf-8")
    summary["artifacts"] = {
        "report": str(run_dir / "report.html"),
        "summary_markdown": str(run_dir / "summary.md"),
        "summary": str(run_dir / "summary.json"),
        "products": str(processed_dir / "growth_products.csv"),
        "reductions": str(processed_dir / "reduced_products.csv"),
        "parser_snapshot": str(raw_dir / "parser_store_period_comparison.json"),
    }
    summary["artifacts"].update(
        write_summary_run_manifest(
            data_dir=DATA_DIR,
            run_dir=run_dir,
            summary=summary,
            task="ozon-cpc-post-apply-review",
            mode="read_only",
            risk="low",
            marketplaces=["ozon"],
            inputs={
                "source_apply_run_id": summary["source_apply_run_id"],
                "pre_dates": sorted(pre_dates),
                "matched_pre_dates": sorted(matched_pre_dates),
                "post_dates": sorted(post_dates),
                "parser_previous_date": parser_payload.get("previous_date"),
                "parser_current_date": parser_payload.get("current_date"),
            },
            lifecycle_status="closed",
            closed=True,
        )
    )
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
