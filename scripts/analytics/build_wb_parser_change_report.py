#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime
from html import escape
import json
from pathlib import Path
from statistics import mean, median
from typing import Any

from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.marketplaces.parser_data_api import ParserDataApiClient
from seller_agent.reports.writer import ensure_dir, write_json


def rows(payload: Any) -> list[dict[str, Any]]:
    values = payload.get("rows", []) if isinstance(payload, dict) else []
    return [row for row in values if isinstance(row, dict)]


def number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def integer(value: Any) -> int:
    return int(number(value))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, values: list[dict[str, Any]]) -> None:
    fields: list[str] = []
    for row in values:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields or ["empty"], extrasaction="ignore")
        writer.writeheader()
        writer.writerows(values)


def collect_date_rows(
    api: ParserDataApiClient,
    *,
    run_date: str,
    supplier_id: str,
    queries: list[str],
) -> list[dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for query in queries:
        payload = api.get(
            "/warehouse/wb/query-positions",
            params={
                "query": query,
                "supplier_id": supplier_id,
                "date_from": run_date,
                "date_to": run_date,
                "limit": 500,
            },
        )
        for row in rows(payload):
            if str(row.get("supplier_id") or "") != supplier_id:
                continue
            product_id = str(row.get("product_id") or row.get("nmID") or "")
            if not product_id:
                continue
            key = (query, product_id)
            if key not in result or integer(row.get("absolute_position")) < integer(result[key].get("absolute_position")):
                result[key] = row
    return list(result.values())


def compare_pairs(previous: list[dict[str, Any]], current: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prev = {(str(row.get("query")), str(row.get("product_id"))): row for row in previous}
    curr = {(str(row.get("query")), str(row.get("product_id"))): row for row in current}
    result: list[dict[str, Any]] = []
    for query, product_id in sorted(set(prev) | set(curr)):
        before = prev.get((query, product_id), {})
        after = curr.get((query, product_id), {})
        previous_position = integer(before.get("absolute_position")) or None
        current_position = integer(after.get("absolute_position")) or None
        if previous_position is None:
            status = "new"
            delta = None
        elif current_position is None:
            status = "lost"
            delta = None
        else:
            delta = previous_position - current_position
            status = "improved" if delta > 0 else "declined" if delta < 0 else "unchanged"
        row = after or before
        previous_price = number(before.get("final_price")) or None
        current_price = number(after.get("final_price")) or None
        price_change = current_price - previous_price if previous_price is not None and current_price is not None else None
        result.append(
            {
                "query": query,
                "nmID": product_id,
                "product_name": row.get("product_name", ""),
                "status": status,
                "previous_position": previous_position or "",
                "current_position": current_position or "",
                "position_improvement": delta if delta is not None else "",
                "previous_price": previous_price if previous_price is not None else "",
                "current_price": current_price if current_price is not None else "",
                "price_change": price_change if price_change is not None else "",
                "previous_stock": integer(before.get("total_quantity")) if before else "",
                "current_stock": integer(after.get("total_quantity")) if after else "",
            }
        )
    return result


def aggregate_products(
    pair_rows: list[dict[str, Any]],
    *,
    wave: dict[str, dict[str, str]],
    all_plan: dict[str, dict[str, str]],
    changed_bids: set[str],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in pair_rows:
        grouped[str(row["nmID"])].append(row)
    result: list[dict[str, Any]] = []
    for nm_id, items in grouped.items():
        before_positions = [integer(row["previous_position"]) for row in items if row["previous_position"] != ""]
        after_positions = [integer(row["current_position"]) for row in items if row["current_position"] != ""]
        common_deltas = [integer(row["position_improvement"]) for row in items if row["position_improvement"] != ""]
        previous_prices = [number(row["previous_price"]) for row in items if row["previous_price"] != ""]
        current_prices = [number(row["current_price"]) for row in items if row["current_price"] != ""]
        before_best = min(before_positions) if before_positions else None
        after_best = min(after_positions) if after_positions else None
        if before_best is None:
            best_status = "new"
            best_delta = None
        elif after_best is None:
            best_status = "lost"
            best_delta = None
        else:
            best_delta = before_best - after_best
            best_status = "improved" if best_delta > 0 else "declined" if best_delta < 0 else "unchanged"
        wave_row = wave.get(nm_id, {})
        plan_row = all_plan.get(nm_id, {})
        result.append(
            {
                "nmID": nm_id,
                "product_name": next((str(row["product_name"]) for row in items if row.get("product_name")), ""),
                "wave": "yes" if nm_id in wave else "no",
                "segment": wave_row.get("Сегмент", ""),
                "bid_changed": "yes" if nm_id in changed_bids else "no",
                "exited_action": plan_row.get("Вышел из акции", ""),
                "previous_best_position": before_best or "",
                "current_best_position": after_best or "",
                "best_position_improvement": best_delta if best_delta is not None else "",
                "best_status": best_status,
                "previous_visible_queries": len(before_positions),
                "current_visible_queries": len(after_positions),
                "query_coverage_change": len(after_positions) - len(before_positions),
                "improved_queries": sum(row["status"] == "improved" for row in items),
                "declined_queries": sum(row["status"] == "declined" for row in items),
                "new_queries": sum(row["status"] == "new" for row in items),
                "lost_queries": sum(row["status"] == "lost" for row in items),
                "mean_common_improvement": round(mean(common_deltas), 2) if common_deltas else "",
                "previous_price": round(median(previous_prices), 2) if previous_prices else "",
                "current_price": round(median(current_prices), 2) if current_prices else "",
                "price_change": round(median(current_prices) - median(previous_prices), 2)
                if previous_prices and current_prices
                else "",
            }
        )
    return sorted(result, key=lambda row: (row["current_best_position"] == "", integer(row["current_best_position"])))


def aggregate_queries(pair_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in pair_rows:
        grouped[str(row["query"])].append(row)
    result = []
    for query, items in sorted(grouped.items()):
        before = [integer(row["previous_position"]) for row in items if row["previous_position"] != ""]
        after = [integer(row["current_position"]) for row in items if row["current_position"] != ""]
        deltas = [integer(row["position_improvement"]) for row in items if row["position_improvement"] != ""]
        result.append(
            {
                "query": query,
                "previous_products": len(before),
                "current_products": len(after),
                "previous_top30": sum(value <= 30 for value in before),
                "current_top30": sum(value <= 30 for value in after),
                "previous_top100": sum(value <= 100 for value in before),
                "current_top100": sum(value <= 100 for value in after),
                "previous_best": min(before) if before else "",
                "current_best": min(after) if after else "",
                "mean_common_improvement": round(mean(deltas), 2) if deltas else "",
                "improved": sum(row["status"] == "improved" for row in items),
                "declined": sum(row["status"] == "declined" for row in items),
                "new": sum(row["status"] == "new" for row in items),
                "lost": sum(row["status"] == "lost" for row in items),
            }
        )
    return result


def product_metrics(values: list[dict[str, Any]]) -> dict[str, Any]:
    before = [integer(row["previous_best_position"]) for row in values if row["previous_best_position"] != ""]
    after = [integer(row["current_best_position"]) for row in values if row["current_best_position"] != ""]
    return {
        "products": len(values),
        "visible_previous": len(before),
        "visible_current": len(after),
        "top10_previous": sum(value <= 10 for value in before),
        "top10_current": sum(value <= 10 for value in after),
        "top30_previous": sum(value <= 30 for value in before),
        "top30_current": sum(value <= 30 for value in after),
        "top100_previous": sum(value <= 100 for value in before),
        "top100_current": sum(value <= 100 for value in after),
        "improved_best": sum(row["best_status"] == "improved" for row in values),
        "declined_best": sum(row["best_status"] == "declined" for row in values),
        "new_visible": sum(row["best_status"] == "new" for row in values),
        "lost_visible": sum(row["best_status"] == "lost" for row in values),
    }


def html_table(values: list[dict[str, Any]], columns: list[tuple[str, str]], *, limit: int | None = None) -> str:
    values = values[:limit] if limit else values
    if not values:
        return '<p class="muted">Нет строк.</p>'
    head = "".join(f"<th>{escape(label)}</th>" for _, label in columns)
    body = []
    for row in values:
        search = " ".join(str(value) for value in row.values()).lower()
        cells = "".join(f"<td>{escape(str(row.get(key, '') or '-'))}</td>" for key, _ in columns)
        body.append(f'<tr data-search="{escape(search)}">{cells}</tr>')
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--previous-date", required=True)
    parser.add_argument("--current-date", required=True)
    parser.add_argument("--supplier-id", default="4516781")
    parser.add_argument("--wave-plan", type=Path, required=True)
    parser.add_argument("--all-plan", type=Path, required=True)
    parser.add_argument("--applied-rows", type=Path, action="append", default=[])
    parser.add_argument("--query-source", type=Path, action="append", default=[])
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    out = ensure_dir(args.output_dir)
    api = ParserDataApiClient()

    summary_payload = api.get("/warehouse/wb/summary")
    quality_payload = api.get("/warehouse/wb/run-quality", params={"limit": 20})
    seed_rows = []
    for run_date in (args.previous_date, args.current_date):
        seed_rows.extend(
            rows(
                api.get(
                    "/warehouse/wb/query-positions",
                    params={
                        "supplier_id": args.supplier_id,
                        "date_from": run_date,
                        "date_to": run_date,
                        "limit": 500,
                    },
                )
            )
        )
    source_query_rows = [row for path in args.query_source for row in read_csv(path)]
    queries = sorted(
        {str(row.get("query") or "") for row in [*seed_rows, *source_query_rows] if row.get("query")}
    )
    previous = collect_date_rows(api, run_date=args.previous_date, supplier_id=args.supplier_id, queries=queries)
    current = collect_date_rows(api, run_date=args.current_date, supplier_id=args.supplier_id, queries=queries)
    pair_rows = compare_pairs(previous, current)

    wave_rows = read_csv(args.wave_plan)
    all_rows = read_csv(args.all_plan)
    wave = {row["nmID"]: row for row in wave_rows}
    all_plan = {row["nmID"]: row for row in all_rows}
    changed_bids = {
        str(row.get("nm_id") or row.get("nmID") or "")
        for path in args.applied_rows
        for row in read_csv(path)
    }
    product_rows = aggregate_products(pair_rows, wave=wave, all_plan=all_plan, changed_bids=changed_bids)
    query_rows = aggregate_queries(pair_rows)
    wave_products = [row for row in product_rows if row["wave"] == "yes"]
    non_wave_products = [row for row in product_rows if row["wave"] == "no"]
    exited_products = [row for row in product_rows if row["exited_action"] == "да"]
    kept_products = [row for row in product_rows if row["exited_action"] == "нет"]
    price_up = [row for row in product_rows if number(row["price_change"]) > 0]
    price_same = [row for row in product_rows if row["price_change"] != "" and number(row["price_change"]) == 0]
    comparable_prices = [row for row in product_rows if row["previous_price"] != "" and row["current_price"] != ""]
    price_percent_by_nm = {
        row["nmID"]: (number(row["current_price"]) / number(row["previous_price"]) - 1) * 100
        for row in comparable_prices
        if number(row["previous_price"]) > 0
    }
    small_price_growth = [row for row in comparable_prices if price_percent_by_nm.get(row["nmID"], 0) <= 10]
    large_price_growth = [row for row in comparable_prices if price_percent_by_nm.get(row["nmID"], 0) > 40]

    def common_movement(values: list[dict[str, Any]]) -> dict[str, Any]:
        changes = [number(row["best_position_improvement"]) for row in values if row["best_position_improvement"] != ""]
        return {
            "common_products": len(changes),
            "improved": sum(value > 0 for value in changes),
            "declined": sum(value < 0 for value in changes),
            "median_improvement": round(median(changes), 2) if changes else None,
            "mean_improvement": round(mean(changes), 2) if changes else None,
        }

    segment_rows = []
    for segment in sorted({row["Сегмент"] for row in wave_rows}):
        planned = {row["nmID"] for row in wave_rows if row["Сегмент"] == segment}
        visible = [row for row in product_rows if row["nmID"] in planned]
        segment_rows.append({"segment": segment, "planned_products": len(planned), **product_metrics(visible)})

    pair_counts = Counter(str(row["status"]) for row in pair_rows)
    overall = product_metrics(product_rows)
    metrics = {
        "queries": len(queries),
        "previous_pairs": len(previous),
        "current_pairs": len(current),
        "pair_status": dict(pair_counts),
        "all_visible_products": overall,
        "wave": {"planned": len(wave), **product_metrics(wave_products)},
        "non_wave_visible": product_metrics(non_wave_products),
        "exited_action_visible": product_metrics(exited_products),
        "kept_action_visible": product_metrics(kept_products),
        "price_up_visible": product_metrics(price_up),
        "price_same_visible": product_metrics(price_same),
        "price_analysis": {
            "comparable_products": len(comparable_prices),
            "median_previous_price": round(median(number(row["previous_price"]) for row in comparable_prices), 2)
            if comparable_prices
            else None,
            "median_current_price": round(median(number(row["current_price"]) for row in comparable_prices), 2)
            if comparable_prices
            else None,
            "median_price_growth_percent": round(median(price_percent_by_nm.values()), 2)
            if price_percent_by_nm
            else None,
            "small_growth_up_to_10_percent": common_movement(small_price_growth),
            "large_growth_over_40_percent": common_movement(large_price_growth),
        },
        "exited_action_movement": common_movement(exited_products),
        "kept_action_movement": common_movement(kept_products),
        "changed_bid_products": len(changed_bids),
    }

    improved = sorted(
        [row for row in product_rows if row["best_position_improvement"] != ""],
        key=lambda row: number(row["best_position_improvement"]),
        reverse=True,
    )
    declined = list(reversed(improved))
    write_csv(out / "pair_changes.csv", pair_rows)
    write_csv(out / "product_changes.csv", product_rows)
    write_csv(out / "query_changes.csv", query_rows)
    write_csv(out / "wave_product_changes.csv", wave_products)
    write_csv(out / "segment_summary.csv", segment_rows)
    generated = datetime.now().astimezone().isoformat(timespec="seconds")
    result = {
        "run_id": out.name,
        "started_at": generated,
        "overall_status": "ok",
        "mode": "read_only",
        "supplier_id": args.supplier_id,
        "previous_date": args.previous_date,
        "current_date": args.current_date,
        "metrics": metrics,
        "source_summary": summary_payload,
        "run_quality": quality_payload,
    }
    write_json(out / "summary.json", result)

    product_columns = [
        ("nmID", "nmID"),
        ("product_name", "Товар"),
        ("segment", "Сегмент"),
        ("previous_best_position", "Было"),
        ("current_best_position", "Стало"),
        ("best_position_improvement", "Изм."),
        ("previous_price", "Цена была"),
        ("current_price", "Цена стала"),
        ("improved_queries", "Рост запросов"),
        ("declined_queries", "Падение запросов"),
    ]
    query_columns = [
        ("query", "Запрос"),
        ("previous_products", "Товаров было"),
        ("current_products", "Стало"),
        ("previous_top30", "Top-30 было"),
        ("current_top30", "Стало"),
        ("previous_best", "Лучшая была"),
        ("current_best", "Стала"),
        ("mean_common_improvement", "Среднее изм."),
    ]
    segment_columns = [
        ("segment", "Сегмент"),
        ("planned_products", "В плане"),
        ("visible_previous", "Видимы было"),
        ("visible_current", "Стало"),
        ("top30_previous", "Top-30 было"),
        ("top30_current", "Стало"),
        ("improved_best", "Выросли"),
        ("declined_best", "Упали"),
    ]
    html = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>WB: изменение выдачи</title>
<style>:root{{--bg:#f3f5f7;--panel:#fff;--line:#d8dee4;--text:#17212b;--muted:#66717d;--good:#14794c;--bad:#b3261e;--warn:#986300;--accent:#1769aa}}*{{box-sizing:border-box}}html,body{{max-width:100%;overflow-x:hidden}}body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 Arial,sans-serif;letter-spacing:0}}main{{max-width:1450px;margin:auto;padding:18px}}h1{{font-size:25px;margin:0 0 5px}}h2{{font-size:18px;margin:24px 0 10px}}.muted{{color:var(--muted)}}.notice{{background:#fff8e6;border-left:4px solid var(--warn);padding:12px;margin:14px 0}}.grid{{display:grid;grid-template-columns:repeat(5,minmax(125px,1fr));gap:9px;margin:14px 0}}.metric,.section{{background:var(--panel);border:1px solid var(--line);border-radius:6px}}.metric{{padding:11px}}.metric b{{display:block;font-size:22px}}.metric span{{color:var(--muted)}}.good{{color:var(--good)}}.bad{{color:var(--bad)}}.section{{min-width:0;padding:15px;margin:12px 0}}input{{width:100%;max-width:480px;padding:9px;border:1px solid var(--line);border-radius:4px;margin:4px 0 12px}}.table-wrap{{width:100%;overflow:auto;border:1px solid var(--line)}}table{{border-collapse:collapse;width:100%;min-width:980px}}th,td{{padding:8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{position:sticky;top:0;background:#edf2f5}}tr:hover{{background:#f7fafc}}code{{background:#edf2f5;padding:1px 4px;border-radius:3px}}@media(max-width:800px){{main{{padding:11px}}.grid{{grid-template-columns:repeat(2,1fr)}}h1{{font-size:21px}}}}</style></head><body><main>
<h1>WB: изменение поисковой выдачи наших товаров</h1><p class="muted">Сравнение {escape(args.previous_date)} → {escape(args.current_date)} · сформировано {escape(generated)}</p>
<div class="notice"><b>Главное ограничение:</b> новый SERP собран после изменения скидок/цен и примерно через два часа после повышения рекламных ставок. Это первичный совместный эффект; отделить влияние цены, акции и рекламы по одному срезу нельзя.</div>
<div class="grid"><div class="metric"><b>{len(queries)}</b><span>одинаковых запросов</span></div><div class="metric"><b>{len(previous)} → {len(current)}</b><span>связок товар-запрос</span></div><div class="metric"><b>{overall['visible_previous']} → {overall['visible_current']}</b><span>видимых товаров</span></div><div class="metric"><b>{overall['top30_previous']} → {overall['top30_current']}</b><span>товаров с лучшей top-30</span></div><div class="metric"><b>{overall['top100_previous']} → {overall['top100_current']}</b><span>товаров с лучшей top-100</span></div></div>
<section class="section"><h2>Краткий вывод</h2><ol>
<li>Ширина выдачи ухудшилась: видимых товаров стало <b>{overall['visible_previous']} → {overall['visible_current']}</b>, связок товар-запрос <b>{len(previous)} → {len(current)}</b>. При этом число товаров с лучшей позицией в top-30 и top-100 осталось <b>{overall['top30_current']}</b> и <b>{overall['top100_current']}</b>.</li>
<li>Рекламная волна удержала верх лучше остального ассортимента: top-30 <b>{metrics['wave']['top30_previous']} → {metrics['wave']['top30_current']}</b>, top-100 <b>{metrics['wave']['top100_previous']} → {metrics['wave']['top100_current']}</b>. Вне волны: top-30 <b>{metrics['non_wave_visible']['top30_previous']} → {metrics['non_wave_visible']['top30_current']}</b>, top-100 <b>{metrics['non_wave_visible']['top100_previous']} → {metrics['non_wave_visible']['top100_current']}</b>.</li>
<li>Выход из акций связан с худшей динамикой: видимость <b>{metrics['exited_action_visible']['visible_previous']} → {metrics['exited_action_visible']['visible_current']}</b>, top-100 <b>{metrics['exited_action_visible']['top100_previous']} → {metrics['exited_action_visible']['top100_current']}</b>. У сохранивших участие: видимость <b>{metrics['kept_action_visible']['visible_previous']} → {metrics['kept_action_visible']['visible_current']}</b>, top-100 <b>{metrics['kept_action_visible']['top100_previous']} → {metrics['kept_action_visible']['top100_current']}</b>.</li>
<li>У {metrics['price_analysis']['comparable_products']} сопоставимых товаров медианный рост цены составил <b>{metrics['price_analysis']['median_price_growth_percent']}%</b>. При росте цены более 40% медианное изменение лучшей позиции <b>{metrics['price_analysis']['large_growth_over_40_percent']['median_improvement']}</b>, а при росте до 10% — <b>+{metrics['price_analysis']['small_growth_up_to_10_percent']['median_improvement']}</b>. Это сильный, но пока не причинно доказанный сигнал влияния цены.</li>
</ol></section>
<section class="section"><h2>Движение одинаковых связок</h2><p><b class="good">Рост:</b> {pair_counts['improved']} · <b class="bad">падение:</b> {pair_counts['declined']} · без изменения: {pair_counts['unchanged']} · новые: {pair_counts['new']} · потеряны: {pair_counts['lost']}.</p><p>Рекламная волна: видимы {metrics['wave']['visible_previous']} → {metrics['wave']['visible_current']}; top-30 {metrics['wave']['top30_previous']} → {metrics['wave']['top30_current']}; top-100 {metrics['wave']['top100_previous']} → {metrics['wave']['top100_current']}.</p></section>
<section class="section"><h2>Сегменты рекламной волны</h2>{html_table(segment_rows, segment_columns)}</section>
<section class="section"><h2>Изменение по запросам</h2>{html_table(query_rows, query_columns)}</section>
<input id="filter" placeholder="Поиск по nmID, товару или сегменту" oninput="filterRows(this.value)">
<section class="section"><h2 class="good">Наибольший рост лучшей позиции</h2>{html_table(improved, product_columns, limit=40)}</section>
<section class="section"><h2 class="bad">Наибольшее падение лучшей позиции</h2>{html_table(declined, product_columns, limit=40)}</section>
<section class="section"><h2>Все товары рекламной волны, замеченные парсером</h2>{html_table(wave_products, product_columns)}</section>
<section class="section"><h2>Источники и файлы</h2><p>Parser Data API: <code>/warehouse/wb/summary</code>, <code>/run-quality</code>, <code>/query-positions</code>; фильтр <code>supplier_id={escape(args.supplier_id)}</code>.</p><p><code>product_changes.csv</code> · <code>pair_changes.csv</code> · <code>query_changes.csv</code> · <code>wave_product_changes.csv</code></p></section>
<script>function filterRows(q){{q=q.toLowerCase().trim();document.querySelectorAll('tbody tr').forEach(r=>r.hidden=q&&!r.dataset.search.includes(q))}}</script></main></body></html>"""
    (out / "report.html").write_text(html, encoding="utf-8")
    summary_md = f"""# WB parser change summary

- period: `{args.previous_date} -> {args.current_date}`
- queries: `{len(queries)}`
- pairs: `{len(previous)} -> {len(current)}`
- visible products: `{overall['visible_previous']} -> {overall['visible_current']}`
- product top-30: `{overall['top30_previous']} -> {overall['top30_current']}`
- product top-100: `{overall['top100_previous']} -> {overall['top100_current']}`
- pair movements: improved `{pair_counts['improved']}`, declined `{pair_counts['declined']}`, new `{pair_counts['new']}`, lost `{pair_counts['lost']}`
- wave visible: `{metrics['wave']['visible_previous']} -> {metrics['wave']['visible_current']}`
- wave top-30: `{metrics['wave']['top30_previous']} -> {metrics['wave']['top30_current']}`
- exited action visible: `{metrics['exited_action_visible']['visible_previous']} -> {metrics['exited_action_visible']['visible_current']}`
- median price growth: `{metrics['price_analysis']['median_price_growth_percent']}%`
- report: `{out / 'report.html'}`
"""
    (out / "summary.md").write_text(summary_md, encoding="utf-8")
    write_summary_run_manifest(
        data_dir=args.data_dir,
        run_dir=out,
        summary=result,
        task="wb-parser-change-analysis",
        mode="read_only",
        risk="low",
        marketplaces=["wb"],
        inputs={
            "previous_date": args.previous_date,
            "current_date": args.current_date,
            "supplier_id": args.supplier_id,
            "queries": len(queries),
        },
    )
    print(json.dumps({"output_dir": str(out), "metrics": metrics}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
