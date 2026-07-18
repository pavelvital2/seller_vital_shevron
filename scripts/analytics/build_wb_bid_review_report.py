#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from datetime import datetime
from html import escape
from pathlib import Path
import re


PARTICIPATING = {"да", "yes", "true", "1"}


def read_csv(path: Path, *, delimiter: str = ",") -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter=delimiter))


def participating(row: dict[str, str]) -> bool:
    values = {
        item.strip().lower()
        for item in re.split(r"[,;]", row.get("Статусы в файлах акций", ""))
        if item.strip()
    }
    return bool(values & PARTICIPATING)


def number(value: str | None) -> float:
    try:
        return float(str(value or "0").replace(" ", "").replace(",", "."))
    except ValueError:
        return 0.0


def display(value: str | None, *, digits: int = 2) -> str:
    if value in (None, ""):
        return "-"
    result = number(value)
    if result.is_integer():
        return str(int(result))
    return f"{result:.{digits}f}".rstrip("0").rstrip(".")


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def html_table(rows: list[dict[str, str]], columns: list[tuple[str, str]]) -> str:
    if not rows:
        return '<p class="muted">Нет строк.</p>'
    head = "".join(f"<th>{escape(label)}</th>" for _, label in columns)
    body = []
    for row in rows:
        search = " ".join(str(value) for value in row.values()).lower()
        cells = "".join(f"<td>{escape(str(row.get(key, '-') or '-'))}</td>" for key, _ in columns)
        body.append(f'<tr data-search="{escape(search)}">{cells}</tr>')
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before-actions", type=Path, required=True)
    parser.add_argument("--after-actions", type=Path, required=True)
    parser.add_argument("--enriched-plan", type=Path, required=True)
    parser.add_argument("--parser-signals", type=Path, required=True)
    parser.add_argument("--stock-signals", type=Path, required=True)
    parser.add_argument("--sales-signals", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    before = {row["Артикул WB"]: row for row in read_csv(args.before_actions, delimiter=";")}
    after = {row["Артикул WB"]: row for row in read_csv(args.after_actions, delimiter=";")}
    exited = {nm_id for nm_id, row in before.items() if participating(row) and not participating(after.get(nm_id, {}))}
    entered = {nm_id for nm_id, row in before.items() if not participating(row) and participating(after.get(nm_id, {}))}

    parser_by_nm = {row["wb_nm_id"]: row for row in read_csv(args.parser_signals) if row.get("wb_nm_id")}
    stock_by_nm = {row["wb_nm_id"]: row for row in read_csv(args.stock_signals) if row.get("wb_nm_id")}
    sales_by_nm = {row["wb_nm_id"]: row for row in read_csv(args.sales_signals) if row.get("wb_nm_id")}
    plan_rows = read_csv(args.enriched_plan)

    review_rows: list[dict[str, str]] = []
    campaigns_by_nm: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in plan_rows:
        nm_id = row["nm_id"]
        campaigns_by_nm[nm_id].append(row)
        parser_row = parser_by_nm.get(nm_id, {})
        stock_row = stock_by_nm.get(nm_id, {})
        sales_row = sales_by_nm.get(nm_id, {})
        review_rows.append(
            {
                "nmID": nm_id,
                "Название": row.get("name", ""),
                "Кампания": row.get("campaign_name", ""),
                "ID кампании": row.get("advert_id", ""),
                "Вышел из акции": "да" if nm_id in exited else "нет",
                "Текущая скидка": after.get(nm_id, {}).get("Текущая скидка", ""),
                "Текущая цена": after.get(nm_id, {}).get("Текущая цена со скидкой", ""),
                "Ставка сейчас": display(row.get("current_bid")),
                "Ставка рекомендована": display(row.get("final_target_bid")),
                "Действие": row.get("parser_enriched_action", ""),
                "Причина": row.get("parser_enriched_reason", ""),
                "Заказы рекламы 30д": display(row.get("orders")),
                "Расход 30д, руб": display(row.get("spend")),
                "ДРР, %": display(row.get("drr_percent")),
                "Остаток API": display(stock_row.get("wb_stock_total") or row.get("wb_stock_total")),
                "Продажи API 30д": display(sales_row.get("sales_units_30d")),
                "Лучшая позиция парсера": display(parser_row.get("parser_best_position") or row.get("parser_best_position")),
                "Запросов в top-30": display(parser_row.get("parser_top30_queries") or row.get("parser_top30_queries")),
                "Запросы": parser_row.get("notes", ""),
            }
        )

    output_csv = args.output_dir / "wb_bid_product_review.csv"
    write_csv(output_csv, review_rows)
    action_counts = Counter(row["Действие"] for row in review_rows)
    campaign_nm_ids = set(campaigns_by_nm)
    exited_campaign = exited & campaign_nm_ids
    exited_parser = exited & set(parser_by_nm)
    exited_stock = exited & set(stock_by_nm)
    exited_sales = exited & set(sales_by_nm)
    exited_with_orders = {
        nm_id for nm_id in exited_campaign if any(number(row.get("orders")) > 0 for row in campaigns_by_nm[nm_id])
    }
    exited_zero_orders = {
        nm_id for nm_id in exited_campaign if all(number(row.get("orders")) == 0 for row in campaigns_by_nm[nm_id])
    }
    exited_top30 = {
        nm_id for nm_id in exited_parser if 0 < number(parser_by_nm[nm_id].get("parser_best_position")) <= 30
    }
    exited_31_100 = {
        nm_id for nm_id in exited_parser if 30 < number(parser_by_nm[nm_id].get("parser_best_position")) <= 100
    }
    exited_over100 = {
        nm_id for nm_id in exited_parser if number(parser_by_nm[nm_id].get("parser_best_position")) > 100
    }
    exited_low_stock = {
        nm_id for nm_id in exited_stock if number(stock_by_nm[nm_id].get("wb_stock_total")) < 4
    }

    increases = [row for row in review_rows if row["Действие"] == "apply_ready"]
    reductions = [row for row in review_rows if row["Действие"] == "reduce_or_stop_review"]
    watches = [row for row in review_rows if row["Действие"] == "watch"]
    reviews = [row for row in review_rows if row["Действие"] == "review_only"]
    columns = [
        ("nmID", "nmID"),
        ("Название", "Товар"),
        ("Вышел из акции", "Вышел из акции"),
        ("Ставка сейчас", "Сейчас, ₽"),
        ("Ставка рекомендована", "Рекомендую, ₽"),
        ("Заказы рекламы 30д", "Заказы"),
        ("Расход 30д, руб", "Расход, ₽"),
        ("ДРР, %", "ДРР, %"),
        ("Остаток API", "Остаток"),
        ("Лучшая позиция парсера", "Позиция"),
        ("Причина", "Основание"),
    ]

    generated = datetime.now().astimezone().isoformat(timespec="seconds")
    html = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>WB: рекомендации по ставкам</title><style>
:root{{--bg:#f4f6f8;--panel:#fff;--text:#17212b;--muted:#66717d;--line:#d9dee4;--good:#16784b;--warn:#9a6500;--bad:#b3261e;--accent:#1267a5}}
*{{box-sizing:border-box}}html,body{{max-width:100%;overflow-x:hidden}}body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 Arial,sans-serif;letter-spacing:0}}
main{{max-width:1400px;margin:auto;padding:20px}}h1{{font-size:24px;margin:0 0 6px}}h2{{font-size:18px;margin:28px 0 10px}}p{{margin:6px 0}}.muted{{color:var(--muted)}}
.notice{{background:#fff8e6;border-left:4px solid var(--warn);padding:12px;margin:16px 0}}.grid{{display:grid;grid-template-columns:repeat(5,minmax(140px,1fr));gap:10px;margin:16px 0}}
.metric{{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:12px}}.metric b{{display:block;font-size:23px}}.metric span{{color:var(--muted)}}
.section{{min-width:0;max-width:100%;background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:16px;margin:12px 0}}.good{{color:var(--good)}}.bad{{color:var(--bad)}}
input{{width:100%;max-width:440px;padding:9px;border:1px solid var(--line);border-radius:4px;margin:4px 0 12px}}.table-wrap{{width:100%;max-width:100%;overflow:auto;border:1px solid var(--line)}}
table{{border-collapse:collapse;width:100%;min-width:1050px}}th,td{{padding:8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{position:sticky;top:0;background:#eef2f5;z-index:1}}tr:hover{{background:#f7fafc}}
ol{{padding-left:22px}}code{{background:#eef2f5;padding:1px 4px;border-radius:3px}}@media(max-width:800px){{main{{padding:12px}}.grid{{grid-template-columns:repeat(2,1fr)}}h1{{font-size:21px}}}}
</style></head><body><main>
<h1>WB: рекомендации по ставкам после выхода товаров из акций</h1><p class="muted">Сформировано {escape(generated)}. Только read-only анализ, ставки и акции не изменялись.</p>
<div class="notice"><b>Ограничение:</b> позиции парсера актуальны на 16.07.2026 и отражают выдачу до изменения скидок 17.07. Поэтому это исходная точка, а не измерение последствия нового уровня цен.</div>
<div class="grid">
<div class="metric"><b>{len(before)}</b><span>товаров WB</span></div><div class="metric"><b>{sum(map(participating,before.values()))} → {sum(map(participating,after.values()))}</b><span>участвуют в акциях</span></div>
<div class="metric"><b class="bad">{len(exited)}</b><span>вышли из акций</span></div><div class="metric"><b class="good">{len(increases)}</b><span>точечных повышений</span></div><div class="metric"><b>{len(reductions)}</b><span>снижений / review</span></div>
</div>
<section class="section"><h2>Краткий вывод</h2><ol>
<li>Массово повышать ставки не нужно: реклама за 30 дней уже эффективна в целом, но товарные результаты неоднородны.</li>
<li>Для тестового повышения подтверждены только {len(increases)} строк: есть заказы, низкий ДРР и достаточный остаток.</li>
<li>{len(reductions)} строки следует снизить или пересмотреть: расход есть, заказов нет, остаток достаточный.</li>
<li>После изменения цены нужен повтор parser-срез 18.07 и сравнение 16.07 → 18.07. До него нельзя приписывать изменение позиции выходу из акций.</li>
</ol></section>
<section class="section"><h2>Что произошло с акциями</h2><p>До изменения участвовало <b>{sum(map(participating,before.values()))}</b>, после — <b>{sum(map(participating,after.values()))}</b>. Вышло <b>{len(exited)}</b>, вошло <b>{len(entered)}</b>.</p>
<p>Среди вышедших: в CPC-кампаниях {len(exited_campaign)}; с рекламными заказами {len(exited_with_orders)}; без заказов {len(exited_zero_orders)}; видны парсеру {len(exited_parser)}; top-30 {len(exited_top30)}, позиции 31–100 {len(exited_31_100)}, ниже 100 {len(exited_over100)}; остаток менее 4 шт. у {len(exited_low_stock)}.</p></section>
<input id="filter" placeholder="Поиск по nmID, названию или причине" oninput="filterRows(this.value)">
<section class="section"><h2 class="good">Тестово повысить: {len(increases)}</h2>{html_table(increases,columns)}</section>
<section class="section"><h2 class="bad">Снизить / пересмотреть: {len(reductions)}</h2>{html_table(reductions,columns)}</section>
<section class="section"><h2>Оставить ставку и наблюдать: {len(watches)}</h2>{html_table(watches,columns)}</section>
<section class="section"><h2>Требуют ручного review: {len(reviews)}</h2>{html_table(reviews,columns)}</section>
<section class="section"><h2>Источники</h2><p><code>{escape(str(args.enriched_plan))}</code></p><p><code>{escape(str(args.before_actions))}</code></p><p><code>{escape(str(args.after_actions))}</code></p><p><code>{escape(str(args.parser_signals))}</code></p><p><code>{escape(str(args.stock_signals))}</code></p><p><code>{escape(str(args.sales_signals))}</code></p></section>
<script>function filterRows(q){{q=q.toLowerCase().trim();document.querySelectorAll('tbody tr').forEach(r=>r.hidden=q&&!r.dataset.search.includes(q))}}</script>
</main></body></html>"""
    (args.output_dir / "report.html").write_text(html, encoding="utf-8")

    summary = f"""# WB promotion bid review

Сформировано: `{generated}`

## Итог

- товаров WB: `{len(before)}`;
- участие в акциях: `{sum(map(participating,before.values()))} -> {sum(map(participating,after.values()))}`;
- вышли из акций: `{len(exited)}`;
- точечно повысить ставку: `{len(increases)}`;
- снизить или пересмотреть: `{len(reductions)}`;
- оставить и наблюдать: `{len(watches)}`;
- parser baseline: `2026-07-16`, до изменения скидок.

## Артефакты

- HTML: `{args.output_dir / 'report.html'}`
- CSV: `{output_csv}`
"""
    (args.output_dir / "summary.md").write_text(summary, encoding="utf-8")
    print(summary)
    print("action_counts", dict(action_counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
