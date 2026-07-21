#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


SELLING_STATUSES = {"продается", "готов к продаже"}
PATCH_GROUPS = {"chev", "nash", "loop"}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [{key: (value or "").strip() for key, value in row.items()} for row in csv.DictReader(handle)]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def number(value: Any) -> float:
    try:
        return float(str(value or "0").replace(" ", "").replace(",", "."))
    except ValueError:
        return 0.0


def integer(value: Any) -> int:
    return int(number(value))


def norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower().replace("ё", "е")


def has_ozon(row: dict[str, str]) -> bool:
    presence = norm(row.get("marketplace_presence"))
    return bool(row.get("ozon_sku") or row.get("ozon_product_id") or row.get("ozon_offer_id")) and "ozon" in presence


def approved_skus(passports_dir: Path) -> set[str]:
    result: set[str] = set()
    for path in passports_dir.rglob("*.json"):
        result.add(path.stem)
        try:
            data = read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            for key in ("internal_sku", "internal_product_id"):
                if data.get(key):
                    result.add(str(data[key]).strip())
            identity = data.get("identity")
            if isinstance(identity, dict):
                for key in ("internal_sku", "internal_product_id"):
                    if identity.get(key):
                        result.add(str(identity[key]).strip())
    return result


def signal_totals(paths: list[Path], field: str) -> dict[str, float]:
    result: dict[str, float] = defaultdict(float)
    for path in paths:
        for row in read_csv(path):
            sku = row.get("internal_sku") or row.get("internal_product_id")
            if sku:
                result[sku] += number(row.get(field))
    return dict(result)


def parser_by_sku(comparison: dict[str, Any], content_rows: list[dict[str, str]]) -> dict[str, list[dict[str, Any]]]:
    sku_by_ozon_id: dict[str, str] = {}
    for row in content_rows:
        internal_sku = row.get("internal_sku")
        if not internal_sku:
            continue
        for key in ("ozon_sku", "ozon_product_id"):
            value = re.sub(r"\D", "", row.get(key, ""))
            if value:
                sku_by_ozon_id[value] = internal_sku

    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in comparison.get("details") or []:
        product_id = re.sub(r"\D", "", str(item.get("product_id") or item.get("normalized_sku") or ""))
        internal_sku = sku_by_ozon_id.get(product_id)
        if internal_sku:
            result[internal_sku].append(item)
    return dict(result)


def score_row(
    row: dict[str, str],
    *,
    sales: float,
    stock: float,
    parser_rows: list[dict[str, Any]],
    seo: dict[str, Any],
) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []

    if stock > 0:
        score += 35 + min(15, math.log1p(stock) * 4)
        reasons.append(f"остаток Ozon {int(stock)} шт.")
    else:
        score -= 35
        reasons.append("остаток Ozon не подтвержден")

    if sales > 0:
        score += 15 + min(20, math.log1p(sales) * 5)
        reasons.append(f"продажи за 30 дней {int(sales)} шт.")
    elif stock > 0:
        score += 25
        reasons.append("при остатке нет продаж за 30 дней")

    positions = [integer(item.get("current_position")) for item in parser_rows if integer(item.get("current_position")) > 0]
    best_position = min(positions) if positions else 0
    if 11 <= best_position <= 100:
        score += 35
        reasons.append(f"лучшая позиция parser {best_position}: зона роста")
    elif best_position > 100:
        score += 30
        reasons.append(f"лучшая позиция parser {best_position}: слабая видимость")
    elif 1 <= best_position <= 10:
        score += 12
        reasons.append(f"лучшая позиция parser {best_position}: сохранить релевантность")
    else:
        score += 20
        reasons.append("не видна в топ-500 по отслеживаемым запросам")

    description_length = integer(row.get("ozon_description_length"))
    if norm(row.get("ozon_description_present")) != "true" or description_length < 500:
        score += 25
        reasons.append("описание отсутствует или слабое")

    hashtags = [item for item in re.split(r"[\s,;]+", row.get("ozon_hashtags", "")) if item.startswith("#")]
    if len(hashtags) < 20:
        score += 22
        reasons.append(f"хештегов {len(hashtags)} из целевых 20-30")

    photo_count = integer(row.get("ozon_photo_count"))
    if photo_count < 5:
        score += 10
        reasons.append(f"фото Ozon {photo_count}: нужен визуальный аудит")

    query_rows = [item for item in (seo.get("confirmed_query_rows") or []) if item.get("marketplace") == "ozon"]
    if query_rows:
        score += min(20, 4 * len(query_rows))
        reasons.append(f"подтверждено SEO-запросов {len(query_rows)}")
    else:
        score -= 20
        reasons.append("нет подтвержденных Ozon SEO-запросов")

    if norm(row.get("content_review_priority")) == "high":
        score += 10
    return score, reasons


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an Ozon-only SEO audit batch without approved passports.")
    parser.add_argument("--content-master", type=Path, required=True)
    parser.add_argument("--backlog", type=Path, required=True)
    parser.add_argument("--passports-dir", type=Path, required=True)
    parser.add_argument("--sales", type=Path, action="append", default=[])
    parser.add_argument("--stocks", type=Path, action="append", default=[])
    parser.add_argument("--parser-comparison", type=Path, required=True)
    parser.add_argument("--seo-targets", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    content_rows = read_csv(args.content_master)
    backlog_rows = read_csv(args.backlog)
    backlog_by_sku = {row.get("internal_sku"): row for row in backlog_rows if row.get("internal_sku")}
    approved = approved_skus(args.passports_dir)
    sales = signal_totals(args.sales, "sales_units_30d")
    stocks = signal_totals(args.stocks, "ozon_stock_total")
    parser_comparison = read_json(args.parser_comparison)
    parser_rows_by_sku = parser_by_sku(parser_comparison, content_rows)
    seo_targets = read_json(args.seo_targets)
    seo_by_sku = {row.get("internal_sku"): row for row in seo_targets if row.get("internal_sku")}

    candidates: list[dict[str, Any]] = []
    excluded = Counter()
    for row in content_rows:
        internal_sku = row.get("internal_sku")
        if not internal_sku:
            excluded["no_internal_sku"] += 1
            continue
        if internal_sku in approved:
            excluded["approved_passport_exists"] += 1
            continue
        if not has_ozon(row):
            excluded["no_ozon_card"] += 1
            continue
        if norm(row.get("ozon_status")) not in SELLING_STATUSES:
            excluded["ozon_not_selling"] += 1
            continue
        product_group = norm(row.get("product_group"))
        if product_group not in PATCH_GROUPS:
            excluded["non_patch_assortment"] += 1
            continue
        seo = seo_by_sku.get(internal_sku, {})
        if seo.get("query_pack_status") not in {"ready", "ready_broad_only"}:
            excluded["seo_not_ready"] += 1
            continue
        parser_rows = parser_rows_by_sku.get(internal_sku, [])
        base_score, reasons = score_row(
            row,
            sales=sales.get(internal_sku, 0),
            stock=stocks.get(internal_sku, 0),
            parser_rows=parser_rows,
            seo=seo,
        )
        query_rows = [item for item in (seo.get("confirmed_query_rows") or []) if item.get("marketplace") == "ozon"]
        candidates.append(
            {
                "internal_sku": internal_sku,
                "internal_product_id": row.get("internal_product_id"),
                "product_name": row.get("product_name"),
                "ozon_offer_id": row.get("ozon_offer_id"),
                "ozon_product_id": row.get("ozon_product_id"),
                "ozon_sku": row.get("ozon_sku"),
                "ozon_status": row.get("ozon_status"),
                "stock_ozon": int(stocks.get(internal_sku, 0)),
                "sales_units_30d": int(sales.get(internal_sku, 0)),
                "parser_best_position": min(
                    [integer(item.get("current_position")) for item in parser_rows if integer(item.get("current_position")) > 0],
                    default=0,
                ),
                "parser_visible_queries": len(parser_rows),
                "theme_code": seo.get("theme_code") or "unknown",
                "primary_target": seo.get("primary_target") or "",
                "query_pack_status": seo.get("query_pack_status") or "",
                "confirmed_query_count": len(query_rows),
                "confirmed_frequency_sum": sum(number(item.get("frequency") or item.get("popularity")) for item in query_rows),
                "base_score": round(base_score, 2),
                "priority_reasons": reasons,
                "content": row,
                "seo": seo,
                "parser_rows": parser_rows,
            }
        )

    # A diversity penalty prevents a large family of near-identical callsigns from
    # consuming the whole batch while retaining the underlying business score.
    selected: list[dict[str, Any]] = []
    remaining = list(candidates)
    theme_counts: Counter[str] = Counter()
    target_counts: Counter[str] = Counter()
    while remaining and len(selected) < args.limit:
        def adjusted(item: dict[str, Any]) -> tuple[float, float, str]:
            theme = item["theme_code"]
            target = norm(item["primary_target"])
            value = item["base_score"] - theme_counts[theme] * 3 - target_counts[target] * 6
            return value, item["base_score"], item["internal_sku"]

        chosen = max(remaining, key=adjusted)
        remaining.remove(chosen)
        chosen["selection_score"] = round(adjusted(chosen)[0], 2)
        chosen["batch_rank"] = len(selected) + 1
        selected.append(chosen)
        theme_counts[chosen["theme_code"]] += 1
        target_counts[norm(chosen["primary_target"])] += 1

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    index_fields = [
        "batch_rank", "internal_sku", "product_name", "ozon_offer_id", "ozon_product_id", "ozon_sku",
        "ozon_status", "stock_ozon", "sales_units_30d", "parser_best_position", "parser_visible_queries",
        "theme_code", "primary_target", "query_pack_status", "confirmed_query_count",
        "confirmed_frequency_sum", "base_score", "selection_score", "priority_reasons",
    ]
    index_rows: list[dict[str, Any]] = []
    filtered_backlog: list[dict[str, Any]] = []
    for item in selected:
        index_row = {key: item.get(key, "") for key in index_fields}
        index_row["priority_reasons"] = "; ".join(item["priority_reasons"])
        index_rows.append(index_row)
        backlog = dict(backlog_by_sku.get(item["internal_sku"], {}))
        backlog["backlog_rank"] = str(item["batch_rank"])
        backlog["business_priority"] = "now"
        backlog["audit_priority"] = "high"
        backlog["score"] = str(item["base_score"])
        backlog["internal_sku"] = item["internal_sku"]
        backlog["internal_product_id"] = item["internal_product_id"]
        backlog["product_name"] = item["product_name"]
        backlog["ozon_offer_id"] = item["ozon_offer_id"]
        backlog["marketplace_presence"] = item["content"].get("marketplace_presence", "")
        backlog["stock_total"] = str(item["stock_ozon"])
        backlog["ozon_stock_total"] = str(item["stock_ozon"])
        backlog["sales_units_30d"] = str(item["sales_units_30d"])
        backlog["parser_best_position"] = str(item["parser_best_position"] or "")
        backlog["parser_visible_queries"] = str(item["parser_visible_queries"])
        backlog["reasons"] = ";".join(item["priority_reasons"])
        backlog["next_step"] = "Одноразовый аудит Ozon по свежему SEO-брифу; без создания паспорта и без apply."
        filtered_backlog.append(backlog)
        write_json(
            output_dir / "briefs" / f"{item['internal_sku']}.json",
            {
                "batch_rank": item["batch_rank"],
                "internal_sku": item["internal_sku"],
                "product_name": item["product_name"],
                "selection": {
                    "base_score": item["base_score"],
                    "selection_score": item["selection_score"],
                    "reasons": item["priority_reasons"],
                    "sales_units_30d": item["sales_units_30d"],
                    "stock_ozon": item["stock_ozon"],
                },
                "parser": {
                    "previous_date": parser_comparison.get("previous_date"),
                    "current_date": parser_comparison.get("current_date"),
                    "rows": item["parser_rows"],
                    "limitations": "Позиции только по 30 отслеживаемым запросам, глубина 500.",
                },
                "seo_query_pack": item["seo"],
                "orchestrator_instructions": [
                    "Использовать только релевантные товару запросы; не переносить широкие ключи механически.",
                    "Название, описание, характеристики и хештеги должны образовывать единый SEO-кластер без переспама.",
                    "Описание состоит из трех тематических блоков и написано естественно от лица производителя.",
                    "Хештеги: 20-30 релевантных фраз, популярность по убыванию, без нерелевантного добора.",
                    "Не выдумывать изображение, материал, размер, цвет и другие характеристики.",
                ],
            },
        )

    backlog_fields = list(backlog_rows[0].keys()) if backlog_rows else []
    for field in ("backlog_rank", "business_priority", "audit_priority", "score", "internal_sku", "internal_product_id", "product_name"):
        if field not in backlog_fields:
            backlog_fields.append(field)
    write_csv(output_dir / "batch_index.csv", index_rows, index_fields)
    write_json(output_dir / "batch_index.json", index_rows)
    write_csv(output_dir / "filtered_backlog.csv", filtered_backlog, backlog_fields)
    (output_dir / "selected_skus.txt").write_text("\n".join(item["internal_sku"] for item in selected) + "\n", encoding="utf-8")
    write_json(
        output_dir / "summary.json",
        {
            "status": "ok" if len(selected) == args.limit else "warning",
            "mode": "read_only",
            "requested": args.limit,
            "selected": len(selected),
            "eligible_candidates": len(candidates),
            "approved_passports_found": len(approved),
            "excluded": dict(excluded),
            "theme_counts": dict(theme_counts),
            "parser": {
                "previous_date": parser_comparison.get("previous_date"),
                "current_date": parser_comparison.get("current_date"),
                "query_scope": parser_comparison.get("query_scope"),
                "complete": parser_comparison.get("complete"),
            },
        },
    )
    print(json.dumps(read_json(output_dir / "summary.json"), ensure_ascii=False, indent=2))
    return 0 if len(selected) == args.limit else 2


if __name__ == "__main__":
    raise SystemExit(main())
