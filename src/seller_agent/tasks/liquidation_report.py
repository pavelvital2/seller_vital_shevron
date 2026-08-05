from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from html import escape
from typing import Any

from seller_agent.marketplaces.parser_data_api import ParserDataApiClient


PARSER_QUERY_PACK = "shevron-core"
WB_SUPPLIER_ID = "4516781"
OZON_SELLER_SLUG = "vital-shevron"


def _normalize_text(value: Any) -> str:
    text = str(value or "").lower().replace("ё", "е")
    return " ".join("".join(char if char.isalnum() else " " for char in text).split())


def query_is_relevant(title: str, query: str) -> bool:
    product = _normalize_text(title)
    search = _normalize_text(query)

    # The monitored assortment contains patches and epaulettes. A pure
    # epaulette must not be reported as invisible for patch-only semantics.
    is_patch = any(token in product for token in ("шеврон", "нашивк", "патч"))
    is_epaulette_only = "петлиц" in product and not is_patch
    if is_epaulette_only:
        return False

    exact_groups = (
        (("фсб",), ("фсб",)),
        (("фсин",), ("фсин", "уис")),
        (("мвд",), ("мвд", "полици", "гибдд", "дпс")),
        (("росгвард",), ("росгвард", "омон", "собр")),
        (("бпла",), ("бпла", "оператор бпла")),
        (("вагнер",), ("вагнер", "чвк")),
        (("вдв",), ("вдв", "воздушно десант")),
        (("вкс",), ("вкс", "космические войск")),
    )
    for query_tokens, product_tokens in exact_groups:
        if any(token in search for token in query_tokens):
            return any(token in product for token in product_tokens)

    if "мог" in search:
        return "мох" in product
    if any(token in search for token in ("именной", "позывной", "с позывным")):
        return "позывн" in product or "именной" in product
    if "на заказ" in search:
        return "на заказ" in product
    if "на кепк" in search or search.startswith("кепка с"):
        return "на кепк" in product
    if "прикол" in search:
        return "прикол" in product
    if "аниме" in search:
        return "аниме" in product
    if "рыбал" in search:
        return "рыбал" in product
    if "поло " in search:
        return False
    if "липучка для шеврон" in search:
        return False
    if "россия" in search or "флаг россии" in search:
        return any(token in product for token in ("россия", " рф ", "вс рф", "флаг", "триколор"))

    broad_queries = {
        "шеврон",
        "шевроны",
        "шеврон на липучке",
        "шевроны на липучке",
        "шевроны тактические",
        "шевроны тактические на липучке",
        "шеврон на липучке военные",
        "шевроны на липучке военные",
    }
    return search in broad_queries and is_patch


def _fetch_all(client: ParserDataApiClient, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        payload = client.get(path, params={**params, "cursor": cursor})
        batch = payload.get("rows") if isinstance(payload, dict) else []
        rows.extend(row for row in (batch or []) if isinstance(row, dict))
        cursor = str(payload.get("next_cursor") or "") if isinstance(payload, dict) else ""
        if not cursor or not bool(payload.get("has_more")):
            break
    return rows


def collect_parser_visibility(
    *,
    ozon_rows: list[dict[str, Any]],
    wb_rows: list[dict[str, Any]],
    client: ParserDataApiClient | None = None,
) -> dict[str, Any]:
    parser = client or ParserDataApiClient()
    result: dict[str, Any] = {"status": "ok", "marketplaces": {}}
    for marketplace, products, ownership in (
        ("ozon", ozon_rows, {"seller_slug": OZON_SELLER_SLUG}),
        ("wb", wb_rows, {"supplier_id": WB_SUPPLIER_ID}),
    ):
        try:
            summary = parser.get(f"/warehouse/{marketplace}/summary")
            metrics = summary.get("metrics") if isinstance(summary, dict) else {}
            warehouse_date = str((metrics or {}).get("max_run_date") or "")
            built_at = str(((summary.get("manifest") or {}).get("built_at_utc")) or "")
            base_params = {
                "date": warehouse_date,
                "query_pack_id": PARSER_QUERY_PACK,
                "limit": 500,
                **ownership,
            }
            positions = _fetch_all(
                parser,
                f"/warehouse/{marketplace}/query-positions",
                base_params,
            )
            coverage = _fetch_all(
                parser,
                f"/warehouse/{marketplace}/aggregates/query-coverage",
                {**base_params, "top_n": 100},
            )
            result["marketplaces"][marketplace] = build_visibility_index(
                marketplace=marketplace,
                products=products,
                positions=positions,
                coverage=coverage,
                warehouse_date=warehouse_date,
                built_at=built_at,
            )
        except Exception as exc:  # report must remain available if parser is down
            result["status"] = "warning"
            result["marketplaces"][marketplace] = {
                "status": "unavailable",
                "reason": type(exc).__name__,
                "by_product": {},
                "warehouse_date": "",
                "regions": [],
                "queries": [],
            }
    return result


def build_visibility_index(
    *,
    marketplace: str,
    products: list[dict[str, Any]],
    positions: list[dict[str, Any]],
    coverage: list[dict[str, Any]],
    warehouse_date: str,
    built_at: str,
) -> dict[str, Any]:
    coverage_pairs = {
        (str(row.get("region_id") or ""), str(row.get("query") or ""))
        for row in coverage
        if row.get("region_id") and row.get("query")
    }
    regions = sorted(
        {
            (str(row.get("region_id") or ""), str(row.get("region_name") or row.get("region_id") or ""))
            for row in coverage
            if row.get("region_id")
        }
    )
    queries = sorted({query for _, query in coverage_pairs})

    position_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in positions:
        key = str(row.get("normalized_sku") or "") if marketplace == "ozon" else str(row.get("product_id") or "")
        if key:
            position_map[key].append(row)

    by_product: dict[str, dict[str, Any]] = {}
    for product in products:
        product_key = (
            f"OZN{str(product.get('ozon_sku') or '').removeprefix('OZN')}"
            if marketplace == "ozon"
            else str(product.get("nm_id") or "")
        )
        title = str(product.get("title") or "")
        relevant_queries = {query for query in queries if query_is_relevant(title, query)}
        expected_pairs = {(region_id, query) for region_id, query in coverage_pairs if query in relevant_queries}
        observed: dict[tuple[str, str], dict[str, Any]] = {}
        for row in position_map.get(product_key, []):
            query = str(row.get("query") or "")
            pair = (str(row.get("region_id") or ""), query)
            if query not in relevant_queries or pair not in expected_pairs:
                continue
            position = int(row.get("absolute_position") or 0)
            previous = observed.get(pair)
            if previous is None or position < int(previous.get("absolute_position") or 0):
                observed[pair] = row

        positions_found = [int(row.get("absolute_position") or 0) for row in observed.values()]
        best_by_query: dict[str, int] = {}
        for (_, query), row in observed.items():
            value = int(row.get("absolute_position") or 0)
            best_by_query[query] = min(best_by_query.get(query, value), value)
        best_queries = sorted(best_by_query.items(), key=lambda item: (item[1], item[0]))[:5]

        if not relevant_queries:
            status = "query_pack_gap"
        elif not observed:
            status = "not_visible"
        elif any(position <= 30 for position in positions_found):
            status = "top30"
        elif any(position <= 100 for position in positions_found):
            status = "top100"
        else:
            status = "below_top100"
        by_product[product_key] = {
            "status": status,
            "relevant_query_count": len(relevant_queries),
            "relevant_queries": sorted(relevant_queries),
            "expected_query_region_pairs": len(expected_pairs),
            "visible_query_region_pairs": len(observed),
            "visible_query_count": len({query for _, query in observed}),
            "visible_region_count": len({region for region, _ in observed}),
            "visible_regions": sorted(
                {
                    str(row.get("region_name") or row.get("region_id") or "")
                    for row in observed.values()
                    if row.get("region_name") or row.get("region_id")
                }
            ),
            "best_position": min(positions_found) if positions_found else None,
            "top30_pairs": sum(position <= 30 for position in positions_found),
            "top100_pairs": sum(position <= 100 for position in positions_found),
            "best_queries": [{"query": query, "position": position} for query, position in best_queries],
        }

    return {
        "status": "ok",
        "warehouse_date": warehouse_date,
        "built_at_utc": built_at,
        "query_pack_id": PARSER_QUERY_PACK,
        "ownership_filter": "seller_slug=vital-shevron" if marketplace == "ozon" else "supplier_id=4516781",
        "regions": [{"region_id": region_id, "region_name": name} for region_id, name in regions],
        "queries": queries,
        "position_rows": len(positions),
        "coverage_rows": len(coverage),
        "by_product": by_product,
    }


def enrich_products_with_visibility(
    rows: list[dict[str, Any]],
    *,
    marketplace: str,
    visibility: dict[str, Any],
) -> list[dict[str, Any]]:
    index = visibility.get("by_product") if isinstance(visibility, dict) else {}
    enriched: list[dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        key = (
            f"OZN{str(row.get('ozon_sku') or '').removeprefix('OZN')}"
            if marketplace == "ozon"
            else str(row.get("nm_id") or "")
        )
        visible = (index or {}).get(key, {})
        row.update(
            {
                "visibility_status": visible.get("status", "unavailable"),
                "relevant_query_count": visible.get("relevant_query_count", 0),
                "expected_query_region_pairs": visible.get("expected_query_region_pairs", 0),
                "visible_query_region_pairs": visible.get("visible_query_region_pairs", 0),
                "visible_query_count": visible.get("visible_query_count", 0),
                "visible_region_count": visible.get("visible_region_count", 0),
                "visible_regions": " | ".join(visible.get("visible_regions") or []),
                "best_position": visible.get("best_position"),
                "top30_pairs": visible.get("top30_pairs", 0),
                "top100_pairs": visible.get("top100_pairs", 0),
                "relevant_queries": " | ".join(visible.get("relevant_queries") or []),
                "best_queries": " | ".join(
                    f"{item['query']}: {item['position']}"
                    for item in (visible.get("best_queries") or [])
                ),
            }
        )
        enriched.append(row)
    return enriched


def classify_product_action(row: dict[str, Any], marketplace: str) -> tuple[str, str]:
    stock = int(row.get("stock_fbo") or row.get("stock_goods") or 0)
    orders = int(row.get("seller_order_units") or 0)
    clicks = int(row.get("clicks") or 0)
    views = int(row.get("views") or 0)
    spend = float(row.get("ad_spend") or 0)
    hard_stop = bool(row.get("stop_action_required", row.get("hard_stop_reached")))
    hard_stop_already_inactive = bool(row.get("hard_stop_reached")) and not hard_stop
    visibility = str(row.get("visibility_status") or "unavailable")

    if stock <= 0:
        return "no_stock", "Нет остатка: не расходовать продвижение; отдельно проверить поставку или закрытие остатка."
    if orders > 0:
        return "sales_exist", "Есть заказ: сохранить текущую распродажную схему и продолжить контроль."
    if marketplace == "ozon" and row.get("cohort_status") in {
        "new_candidate_not_applied",
        "new_candidate_not_in_previous_cohort",
    }:
        return "new_candidate_review", "Новый кандидат вне прежней когорты: отдельно проверить текущую цену, акцию и CPC, затем вести новый контрольный период."
    if hard_stop:
        return "stop_review", "Достигнут лимит без заказа: подготовить отключение CPC на отдельное согласование."
    if hard_stop_already_inactive:
        return "hard_stop_already_inactive", "Стоп-порог достигнут ранее, CPC уже отключено; повторное действие не требуется."
    if marketplace == "wb" and bool(row.get("second_price_stage_applied")) and int(row.get("monitoring_full_days") or 0) < 2:
        return "stage2_too_early", "Второй ценовой этап начался 1 августа: решение только после двух полных дней, утром 4 августа."
    if clicks > 0:
        return "clicks_no_sales", "Клики есть, заказа нет: проверить цену, первое фото и карточку; ставку не повышать до появления конверсии."
    if views >= 30:
        return "views_no_clicks", "Показы есть, кликов нет: приоритетно переработать главное фото и заголовок."
    if visibility == "query_pack_gap":
        return "query_pack_gap", "В пакете парсера нет релевантного запроса: расширить семантику, видимость пока не оценивать."
    if visibility == "not_visible":
        return "not_visible", "Не найден по релевантным запросам: проверить региональный остаток и SEO карточки; ставку вслепую не повышать."
    if spend > 0:
        return "spend_no_sales", "Есть расход без заказа: продолжить только до согласованного лимита, ставку не повышать."
    return "insufficient_data", "Данных мало: сохранить тест до следующей контрольной даты без повышения ставки."


def build_business_analysis(
    *,
    ozon_rows: list[dict[str, Any]],
    wb_rows: list[dict[str, Any]],
    daily_rows: list[dict[str, Any]],
    visibility: dict[str, Any],
) -> dict[str, Any]:
    for marketplace, rows in (("ozon", ozon_rows), ("wb", wb_rows)):
        for row in rows:
            action, recommendation = classify_product_action(row, marketplace)
            row["action_group"] = action
            row["recommendation"] = recommendation

    def marketplace_summary(rows: list[dict[str, Any]], marketplace: str) -> dict[str, Any]:
        cohort = len(rows)
        sold_rows = [row for row in rows if int(row.get("seller_order_units") or 0) > 0]
        orders = sum(int(row.get("seller_order_units") or 0) for row in rows)
        revenue = sum(float(row.get("seller_order_value") or 0) for row in rows)
        spend = sum(float(row.get("ad_spend") or 0) for row in rows)
        views = sum(int(row.get("views") or 0) for row in rows)
        clicks = sum(int(row.get("clicks") or 0) for row in rows)
        to_cart = sum(int(row.get("to_cart") or 0) for row in rows)
        ad_orders = sum(int(row.get("ad_orders") or 0) for row in rows)
        ad_revenue = sum(float(row.get("ad_revenue") or 0) for row in rows)
        physical_orders = sum(int(row.get("seller_order_units") or 0) * int(row.get("pack_qty") or 1) for row in rows)
        action_counts = Counter(str(row.get("action_group") or "") for row in rows)
        visibility_counts = Counter(str(row.get("visibility_status") or "") for row in rows)
        return {
            "cohort": cohort,
            "sold_skus": len(sold_rows),
            "unsold_skus": cohort - len(sold_rows),
            "sold_sku_share_pct": round(100 * len(sold_rows) / cohort, 1) if cohort else 0,
            "seller_order_units": orders,
            "physical_order_units": physical_orders,
            "seller_order_value": round(revenue, 2),
            "ad_spend": round(spend, 2),
            "spend_per_seller_order": round(spend / orders, 2) if orders else None,
            "views": views,
            "clicks": clicks,
            "to_cart": to_cart,
            "ad_orders": ad_orders,
            "ad_revenue": round(ad_revenue, 2),
            "ctr_pct": round(100 * clicks / views, 2) if views else 0,
            "seller_orders_per_click_pct": round(100 * orders / clicks, 2) if clicks else 0,
            "hard_stop_candidates": sum(bool(row.get("stop_action_required", row.get("hard_stop_reached"))) for row in rows),
            "action_counts": dict(action_counts),
            "visibility_counts": dict(visibility_counts),
            "top_sellers": sorted(
                sold_rows,
                key=lambda row: (-int(row.get("seller_order_units") or 0), -float(row.get("seller_order_value") or 0)),
            ),
            "marketplace": marketplace,
        }

    daily: dict[str, list[dict[str, Any]]] = {"ozon": [], "wb": []}
    grouped: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in daily_rows:
        key = (str(row.get("marketplace") or ""), str(row.get("date") or ""))
        for metric in ("views", "clicks", "to_cart", "orders", "spend", "revenue"):
            grouped[key][metric] += float(row.get(metric) or 0)
    for (marketplace, day), metrics in sorted(grouped.items()):
        daily.setdefault(marketplace, []).append({"date": day, **{key: round(value, 2) for key, value in metrics.items()}})

    return {
        "ozon": marketplace_summary(ozon_rows, "ozon"),
        "wb": marketplace_summary(wb_rows, "wb"),
        "daily": daily,
        "visibility": visibility,
    }


ACTION_LABELS = {
    "sales_exist": "Есть продажи — оставить",
    "stop_review": "Достигнут лимит — остановить после согласования",
    "hard_stop_already_inactive": "Стоп-порог достигнут, CPC уже отключено",
    "stage2_too_early": "Рано оценивать второй этап",
    "not_visible": "Нет видимости по релевантным запросам",
    "views_no_clicks": "Есть показы, нет кликов",
    "clicks_no_sales": "Есть клики, нет продаж",
    "query_pack_gap": "Нет релевантного запроса в парсере",
    "spend_no_sales": "Есть расход, мало данных",
    "insufficient_data": "Недостаточно данных",
    "no_stock": "Нет остатка",
    "new_candidate_review": "Новые кандидаты — отдельное решение",
}

VISIBILITY_LABELS = {
    "top30": "Есть в top-30",
    "top100": "Есть в top-100",
    "below_top100": "Виден ниже top-100",
    "not_visible": "Не найден по релевантным запросам",
    "query_pack_gap": "Нет релевантного запроса в пакете",
    "unavailable": "Данные недоступны",
}


def render_management_markdown(summary: dict[str, Any], analysis: dict[str, Any]) -> str:
    period_days = (
        date.fromisoformat(summary["period"]["date_to"])
        - date.fromisoformat(summary["period"]["date_from"])
    ).days + 1
    lines = [
        "# Контроль и рекомендации по распродаже",
        "",
        f"Run ID: `{summary['run_id']}`",
        f"Продажи и реклама: `{summary['period']['date_from']} — {summary['period']['date_to']}`, только завершённые дни.",
        "",
        "## Главный вывод",
        f"- Ozon: продажи получили **{analysis['ozon']['sold_skus']} из {analysis['ozon']['cohort']} SKU** "
        f"({analysis['ozon']['sold_sku_share_pct']}%), заказано **{analysis['ozon']['seller_order_units']} товаров / "
        f"{analysis['ozon']['physical_order_units']} изделий**, CPC-расход **{analysis['ozon']['ad_spend']:.2f} руб.**",
        f"- WB: продажи получили **{analysis['wb']['sold_skus']} из {analysis['wb']['cohort']} SKU** "
        f"({analysis['wb']['sold_sku_share_pct']}%), заказано **{analysis['wb']['seller_order_units']} товаров / "
        f"{analysis['wb']['physical_order_units']} изделий**, CPC-расход **{analysis['wb']['ad_spend']:.2f} руб.**",
        f"- Порог остановки сейчас достигнут: Ozon **{analysis['ozon']['hard_stop_candidates']}**, WB **{analysis['wb']['hard_stop_candidates']}**.",
        f"- Вывод после {period_days} завершённых дней: распродажа дала первые продажи на Ozon, "
        "на WB результат пока слабый; повышать ставки всей когорте нельзя.",
        "",
    ]
    for marketplace, title in (("ozon", "Ozon"), ("wb", "Wildberries")):
        data = analysis[marketplace]
        visibility = analysis["visibility"].get("marketplaces", {}).get(marketplace, {})
        lines.extend(
            [
                f"## {title}",
                f"- Оборот заказов когорты: **{data['seller_order_value']:.2f} руб.**; расход CPC: **{data['ad_spend']:.2f} руб.**; "
                f"расход CPC на один seller-заказ: **{data['spend_per_seller_order'] if data['spend_per_seller_order'] is not None else 'нет заказов'} руб.**",
                f"- Рекламная воронка: **{data['views']} показов → {data['clicks']} кликов → {data['to_cart']} корзин**; "
                f"атрибутированные рекламой заказы: **{data['ad_orders']}**; CTR **{data['ctr_pct']}%**. "
                f"Отдельно заказы Seller API: **{data['seller_order_units']}**.",
                f"- Видимость: срез **{visibility.get('warehouse_date') or 'недоступен'}**, пакет `{visibility.get('query_pack_id') or PARSER_QUERY_PACK}`, "
                f"регионов **{len(visibility.get('regions') or [])}**, точный фильтр `{visibility.get('ownership_filter') or 'недоступен'}`.",
                "",
                "### Что делать",
            ]
        )
        for action, count in sorted(data["action_counts"].items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"- **{ACTION_LABELS.get(action, action)}:** {count} SKU.")
        lines.extend(["", "### Товары с заказами"])
        if not data["top_sellers"]:
            lines.append("- Заказов нет.")
        else:
            for row in data["top_sellers"][:15]:
                product_id = row.get("ozon_sku") or row.get("nm_id")
                lines.append(
                    f"- `{product_id}` — {row.get('title')}: {row.get('seller_order_units')} заказ., "
                    f"{float(row.get('seller_order_value') or 0):.2f} руб.; лучшая релевантная позиция "
                    f"{row.get('best_position') or 'не найден'}; CPC {float(row.get('ad_spend') or 0):.2f} руб."
                )
        lines.append("")
    lines.extend(
        [
            "## Ограничения",
            "- Parser Data API показывает видимость на дату warehouse-среза, а не текущую позицию в момент отчёта.",
            "- Видимость считается только по запросам, релевантность которых подтверждается назначением товара в названии. Нерелевантные запросы не учитываются.",
            "- Наличие товара в регионе является возможной причиной видимости, но не включается в сам показатель видимости.",
            "- Изменений цен, акций, ставок и кампаний не выполнялось.",
        ]
    )
    return "\n".join(lines)


def render_management_html(
    summary: dict[str, Any],
    analysis: dict[str, Any],
    ozon_rows: list[dict[str, Any]],
    wb_rows: list[dict[str, Any]],
) -> str:
    def money(value: Any) -> str:
        return f"{float(value or 0):,.2f}".replace(",", " ")

    def action_rows(data: dict[str, Any]) -> str:
        return "".join(
            f"<tr><td>{escape(ACTION_LABELS.get(action, action))}</td><td>{count}</td></tr>"
            for action, count in sorted(data["action_counts"].items(), key=lambda item: (-item[1], item[0]))
        )

    def visibility_rows(data: dict[str, Any]) -> str:
        return "".join(
            f"<tr><td>{escape(VISIBILITY_LABELS.get(status, status))}</td><td>{count}</td></tr>"
            for status, count in sorted(data["visibility_counts"].items(), key=lambda item: (-item[1], item[0]))
        )

    def daily_rows(marketplace: str) -> str:
        return "".join(
            f"<tr><td>{escape(str(row['date']))}</td><td>{int(row['views'])}</td><td>{int(row['clicks'])}</td>"
            f"<td>{int(row['to_cart'])}</td><td>{int(row['orders'])}</td><td>{money(row['spend'])}</td></tr>"
            for row in analysis["daily"].get(marketplace, [])
        )

    def product_rows(rows: list[dict[str, Any]], marketplace: str) -> str:
        sorted_rows = sorted(
            rows,
            key=lambda row: (
                0 if int(row.get("seller_order_units") or 0) > 0 else 1,
                list(ACTION_LABELS).index(str(row.get("action_group"))) if str(row.get("action_group")) in ACTION_LABELS else 99,
                -float(row.get("ad_spend") or 0),
            ),
        )
        result = []
        for row in sorted_rows:
            product_id = row.get("ozon_sku") or row.get("nm_id")
            best = row.get("best_position") or "—"
            coverage = f"{int(row.get('visible_query_count') or 0)} / {int(row.get('relevant_query_count') or 0)}"
            query_evidence = str(row.get("best_queries") or "") or "—"
            result.append(
                f"<tr data-action='{escape(str(row.get('action_group') or ''))}' data-title='{escape(_normalize_text(row.get('title')))}'>"
                f"<td><code>{escape(str(product_id))}</code></td><td>{escape(str(row.get('title') or ''))}</td>"
                f"<td>{int(row.get('stock_fbo') or row.get('stock_goods') or 0)}</td>"
                f"<td>{int(row.get('views') or 0)}</td><td>{int(row.get('clicks') or 0)}</td>"
                f"<td>{money(row.get('ad_spend'))}</td><td>{int(row.get('seller_order_units') or 0)}</td>"
                f"<td>{escape(VISIBILITY_LABELS.get(str(row.get('visibility_status')), str(row.get('visibility_status'))))}</td>"
                f"<td>{best}</td><td>{coverage}</td><td>{escape(query_evidence)}</td><td>{escape(str(row.get('visible_regions') or '—'))}</td>"
                f"<td>{escape(ACTION_LABELS.get(str(row.get('action_group')), str(row.get('action_group'))))}</td>"
                f"<td>{escape(str(row.get('recommendation') or ''))}</td></tr>"
            )
        return "".join(result)

    def marketplace_section(marketplace: str, label: str, rows: list[dict[str, Any]]) -> str:
        data = analysis[marketplace]
        visible = analysis["visibility"].get("marketplaces", {}).get(marketplace, {})
        action_options = "".join(
            f"<option value='{escape(action)}'>{escape(ACTION_LABELS.get(action, action))}</option>"
            for action in data["action_counts"]
        )
        return f"""
        <section id='{marketplace}'>
          <div class='inner'>
            <h2>{label}</h2>
            <div class='metrics'>
              <div class='metric'><span>SKU с продажами</span><b>{data['sold_skus']} / {data['cohort']}</b><small>{data['sold_sku_share_pct']}% когорты</small></div>
              <div class='metric'><span>Заказано</span><b>{data['seller_order_units']} товаров</b><small>{data['physical_order_units']} физических изделий</small></div>
              <div class='metric'><span>Оборот заказов</span><b>{money(data['seller_order_value'])} ₽</b><small>не выплата и не прибыль</small></div>
              <div class='metric'><span>CPC-расход</span><b>{money(data['ad_spend'])} ₽</b><small>{money(data['spend_per_seller_order']) if data['spend_per_seller_order'] is not None else 'нет'} ₽ на seller-заказ</small></div>
              <div class='metric'><span>Рекламная воронка</span><b>{data['views']} → {data['clicks']}</b><small>{data['to_cart']} корзин, CTR {data['ctr_pct']}%</small></div>
              <div class='metric'><span>Hard stop</span><b>{data['hard_stop_candidates']}</b><small>кандидатов на согласование</small></div>
            </div>
            <div class='triple'>
              <div><h3>Группы решений</h3><table class='compact'><thead><tr><th>Решение</th><th>SKU</th></tr></thead><tbody>{action_rows(data)}</tbody></table></div>
              <div><h3>Видимость</h3><table class='compact'><thead><tr><th>Статус</th><th>SKU</th></tr></thead><tbody>{visibility_rows(data)}</tbody></table></div>
              <div><h3>По дням</h3><div class='compact-wrap'><table class='compact daily'><thead><tr><th>Дата</th><th>Показы</th><th>Клики</th><th>Корзина</th><th>Рекл. заказы</th><th>Расход ₽</th></tr></thead><tbody>{daily_rows(marketplace)}</tbody></table></div></div>
            </div>
            <div class='source'>Видимость: warehouse <b>{escape(str(visible.get('warehouse_date') or 'недоступен'))}</b>, пакет <code>{escape(str(visible.get('query_pack_id') or PARSER_QUERY_PACK))}</code>, регионов {len(visible.get('regions') or [])}, фильтр <code>{escape(str(visible.get('ownership_filter') or 'недоступен'))}</code>.</div>
            <div class='toolbar'><input class='search' data-target='{marketplace}-rows' placeholder='Найти SKU или товар'><select class='filter' data-target='{marketplace}-rows'><option value=''>Все решения</option>{action_options}</select></div>
            <div class='table-wrap'><table class='products'><thead><tr><th>ID</th><th>Товар</th><th>Остаток</th><th>Показы</th><th>Клики</th><th>CPC ₽</th><th>Заказы</th><th>Статус видимости</th><th>Лучшая позиция</th><th>Запросов видно / релевантно</th><th>Лучшие релевантные запросы</th><th>Регионы видимости</th><th>Группа</th><th>Что делать</th></tr></thead><tbody id='{marketplace}-rows'>{product_rows(rows, marketplace)}</tbody></table></div>
          </div>
        </section>"""

    return f"""<!doctype html>
<html lang='ru'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Контроль распродажи</title>
<style>
:root{{--ink:#182026;--muted:#66727c;--line:#d8dee3;--paper:#fff;--bg:#f2f4f5;--green:#176b55;--amber:#a55b00;--red:#a32929}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 Arial,sans-serif;letter-spacing:0}}
header,section{{border-bottom:1px solid var(--line)}} header{{background:#163c35;color:#fff}} .inner{{max-width:1500px;margin:auto;padding:22px}}
h1{{font-size:30px;margin:0 0 8px}} h2{{font-size:23px;margin:0 0 16px}} h3{{font-size:16px;margin:0 0 10px}} p{{margin:6px 0}}
.lead{{font-size:17px;max-width:1050px}} .status{{display:inline-block;background:#fff;color:#163c35;padding:4px 8px;border-radius:4px;font-weight:700}}
.headline{{background:#fff}} .headline-grid,.metrics{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:1px;background:var(--line);border:1px solid var(--line)}}
.headline-item,.metric{{background:#fff;padding:14px;min-height:90px}} .headline-item b,.metric b{{display:block;font-size:22px;margin:4px 0}} .headline-item span,.metric span,.metric small{{display:block;color:var(--muted)}}
.verdict{{border-left:5px solid var(--amber);padding:12px 14px;background:#fff8ed;margin-top:14px}} section{{background:#fff}} section:nth-of-type(even){{background:#f8f9fa}}
.metrics{{grid-template-columns:repeat(6,minmax(0,1fr));margin-bottom:16px}} .triple{{display:grid;grid-template-columns:1fr 1fr 1.5fr;gap:24px;margin:18px 0}} .triple>div{{min-width:0}}
table{{border-collapse:collapse;width:100%}} th,td{{border-bottom:1px solid var(--line);padding:8px;text-align:right;vertical-align:top}} th{{background:#edf1f2;position:sticky;top:0;z-index:1}} th:nth-child(2),td:nth-child(2),th:last-child,td:last-child{{text-align:left}}
.compact th:first-child,.compact td:first-child{{text-align:left}} .source{{padding:10px 12px;background:#eef5f3;border-left:4px solid var(--green);margin:14px 0}}
.toolbar{{display:flex;gap:8px;margin:12px 0}} input,select{{font:inherit;padding:9px 10px;border:1px solid #aeb8c0;border-radius:4px;background:#fff}} input{{flex:1;min-width:220px}}
.table-wrap{{overflow:auto;max-height:720px;border:1px solid var(--line)}} .products{{min-width:2050px}} code{{font-size:12px;overflow-wrap:anywhere;word-break:break-word}} .compact-wrap{{overflow:auto}} .daily{{min-width:560px}}
.notes{{background:#f8f9fa}} ul{{margin:8px 0;padding-left:20px}}
@media(max-width:900px){{.headline-grid,.metrics{{grid-template-columns:repeat(2,minmax(0,1fr))}}.triple{{grid-template-columns:1fr}}.toolbar{{flex-direction:column}}h1{{font-size:25px}}}}
</style></head><body>
<header><div class='inner'><span class='status'>READ-ONLY</span><h1>Контроль и рекомендации по распродаже</h1><p class='lead'>Период продаж и рекламы: {escape(summary['period']['date_from'])} — {escape(summary['period']['date_to'])}. Текущий неполный день исключён.</p><p>Run ID: <code>{escape(summary['run_id'])}</code></p></div></header>
<div class='headline'><div class='inner'><h2>Главный вывод</h2><div class='headline-grid'>
<div class='headline-item'><span>Ozon: SKU с продажами</span><b>{analysis['ozon']['sold_skus']} / {analysis['ozon']['cohort']}</b><small>{analysis['ozon']['seller_order_units']} заказанных товаров</small></div>
<div class='headline-item'><span>Ozon CPC</span><b>{money(analysis['ozon']['ad_spend'])} ₽</b><small>{analysis['ozon']['hard_stop_candidates']} достигли стоп-порога</small></div>
<div class='headline-item'><span>WB: SKU с продажами</span><b>{analysis['wb']['sold_skus']} / {analysis['wb']['cohort']}</b><small>{analysis['wb']['seller_order_units']} заказанных товаров</small></div>
<div class='headline-item'><span>WB CPC</span><b>{money(analysis['wb']['ad_spend'])} ₽</b><small>{analysis['wb']['hard_stop_candidates']} достигли стоп-порога</small></div>
</div><div class='verdict'><b>Решение сейчас:</b> Ozon дал первые продажи, WB пока слабый. Массово повышать ставки нельзя. Оставить товары с продажами, отдельно работать с отсутствием видимости и конверсии; второй ценовой этап WB оценивать утром 4 августа.</div></div></div>
{marketplace_section('ozon','Ozon',ozon_rows)}
{marketplace_section('wb','Wildberries',wb_rows)}
<section class='notes'><div class='inner'><h2>Как читать видимость</h2><ul><li>Учитываются только запросы, релевантные назначению конкретного товара.</li><li>Parser warehouse имеет собственную дату: это не позиция в реальном времени.</li><li>Наличие в регионе — одна из причин видимости, но не часть метрики видимости.</li><li>Для товаров без подходящего запроса показан «нет релевантного запроса», а не ложный ноль.</li></ul><p>Цены, акции, ставки и кампании не изменялись.</p></div></section>
<script>
document.querySelectorAll('.search,.filter').forEach(el=>el.addEventListener('input',()=>{{const target=el.dataset.target;const body=document.getElementById(target);const root=el.closest('.inner');const q=(root.querySelector('.search').value||'').toLowerCase();const a=root.querySelector('.filter').value;body.querySelectorAll('tr').forEach(row=>{{row.hidden=!!((q&&!row.innerText.toLowerCase().includes(q))||(a&&row.dataset.action!==a))}})}}));
</script></body></html>"""
