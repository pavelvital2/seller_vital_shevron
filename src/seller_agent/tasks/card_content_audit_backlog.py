from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Any

from seller_agent.catalog.loader import normalize_sku
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.reports.writer import ensure_dir, write_json


DEFAULT_CONTENT_MASTER_PATH = Path("catalog/content/content_master.csv")
DEFAULT_OUTPUT_DIR = Path("catalog/content")


@dataclass
class CardContentBacklogRow:
    backlog_rank: str
    audit_priority: str
    audit_focus: str
    score: str
    internal_product_id: str
    internal_sku: str = ""
    product_name: str = ""
    mapping_status: str = ""
    marketplace_presence: str = ""
    title_alignment_status: str = ""
    full_snapshot_status: str = ""
    transfer_direction: str = ""
    ozon_offer_id: str = ""
    wb_vendor_code: str = ""
    ozon_photo_count: str = ""
    wb_photo_count: str = ""
    ozon_description_present: str = ""
    wb_description_present: str = ""
    ozon_attribute_count: str = ""
    wb_attribute_count: str = ""
    ozon_hashtags_count: str = ""
    wb_tags_count: str = ""
    cost_total: str = ""
    sales_units_30d: str = ""
    sales_revenue_30d: str = ""
    stock_total: str = ""
    ozon_stock_total: str = ""
    wb_stock_total: str = ""
    parser_best_position: str = ""
    parser_visible_queries: str = ""
    parser_top30_queries: str = ""
    parser_max_query_popularity_7d: str = ""
    business_priority: str = ""
    business_reasons: str = ""
    reasons: str = ""
    next_step: str = ""
    visual_audit_required: str = "true"
    notes: str = ""


BACKLOG_FIELDS = [field.name for field in fields(CardContentBacklogRow)]


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [
            {key: (value or "").strip() for key, value in row.items()}
            for row in csv.DictReader(handle)
        ]


def _write_dict_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def _int_value(value: Any) -> int:
    try:
        return int(float(str(value or "0").replace(",", ".")))
    except ValueError:
        return 0


def _float_value(value: Any) -> float | None:
    text = str(value or "").replace(",", ".").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _format_number(value: Any) -> str:
    number = _float_value(value)
    if number is None:
        return ""
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _is_true(value: Any) -> bool:
    return str(value or "").strip().lower() in {"true", "1", "yes", "да"}


def _count_tokens(value: str) -> int:
    text = normalize_sku(value)
    if not text:
        return 0
    return len([part for part in text.replace(";", " ").split() if part.strip()])


def _has_ozon(row: dict[str, str]) -> bool:
    return normalize_sku(row.get("ozon_offer_id")) != ""


def _has_wb(row: dict[str, str]) -> bool:
    return normalize_sku(row.get("wb_vendor_code")) != ""


def _first_number(row: dict[str, str], fields: tuple[str, ...]) -> float | None:
    for field in fields:
        number = _float_value(row.get(field))
        if number is not None:
            return number
    return None


def _key(namespace: str, value: Any) -> str:
    text = normalize_sku(value)
    return f"{namespace}:{text.lower()}" if text else ""


def _add_key(keys: list[str], namespace: str, value: Any) -> None:
    key = _key(namespace, value)
    if key and key not in keys:
        keys.append(key)


def _product_signal_keys(row: dict[str, str]) -> list[str]:
    keys: list[str] = []
    _add_key(keys, "internal_product_id", row.get("internal_product_id"))
    _add_key(keys, "internal_sku", row.get("internal_sku"))
    _add_key(keys, "ozon_offer_id", row.get("ozon_offer_id"))
    _add_key(keys, "ozon_product_id", row.get("ozon_product_id"))
    _add_key(keys, "ozon_sku", row.get("ozon_sku"))
    _add_key(keys, "wb_vendor_code", row.get("wb_vendor_code"))
    _add_key(keys, "wb_nm_id", row.get("wb_nm_id"))
    return keys


def _signal_row_keys(row: dict[str, str]) -> list[str]:
    keys: list[str] = []
    _add_key(keys, "internal_product_id", row.get("internal_product_id"))
    _add_key(keys, "internal_sku", row.get("internal_sku"))
    _add_key(keys, "ozon_offer_id", row.get("ozon_offer_id") or row.get("offer_id"))
    _add_key(keys, "ozon_product_id", row.get("ozon_product_id") or row.get("api_product_id") or row.get("product_id"))
    _add_key(keys, "ozon_sku", row.get("ozon_sku") or row.get("sku"))
    _add_key(keys, "wb_vendor_code", row.get("wb_vendor_code") or row.get("vendorCode") or row.get("vendor_code"))
    _add_key(keys, "wb_nm_id", row.get("wb_nm_id") or row.get("nmID") or row.get("nm_id") or row.get("nmId"))
    return keys


def _signal_item(row: dict[str, str], *, source: str) -> dict[str, Any]:
    item: dict[str, Any] = {"source": source}
    sales_units = _first_number(
        row,
        (
            "sales_units_30d",
            "units_30d",
            "ordered_units",
            "orders",
            "sales",
            "sales_rows",
            "buyouts",
            "quantity_sold",
        ),
    )
    revenue = _first_number(row, ("sales_revenue_30d", "revenue_30d", "revenue", "orders_money", "sales_amount"))
    stock = _first_number(
        row,
        (
            "stock_total",
            "fbo_present",
            "fbo_stock_at_apply",
            "present",
            "quantity",
            "stock",
            "available_stock",
            "available_stock_count",
        ),
    )
    ozon_stock = _first_number(row, ("ozon_stock_total", "fbo_present", "fbo_stock_at_apply", "ozon_stock"))
    wb_stock = _first_number(row, ("wb_stock_total", "wb_stock", "quantity", "stock"))
    best_position = _first_number(row, ("parser_best_position", "best_position", "position", "min_position"))
    visible_queries = _first_number(row, ("parser_visible_queries", "queries_found_count", "visible_queries", "query_count"))
    top30_queries = _first_number(row, ("parser_top30_queries", "top30_count", "top30_queries"))
    popularity = _first_number(row, ("parser_max_query_popularity_7d", "max_query_popularity_7d"))
    if sales_units is not None:
        item["sales_units_30d"] = sales_units
    if revenue is not None:
        item["sales_revenue_30d"] = revenue
    if stock is not None:
        item["stock_total"] = stock
    if ozon_stock is not None:
        item["ozon_stock_total"] = ozon_stock
    if wb_stock is not None:
        item["wb_stock_total"] = wb_stock
    if best_position is not None and best_position > 0:
        item["parser_best_position"] = best_position
    if visible_queries is not None:
        item["parser_visible_queries"] = visible_queries
    if top30_queries is not None:
        item["parser_top30_queries"] = top30_queries
    if popularity is not None:
        item["parser_max_query_popularity_7d"] = popularity
    return item


def build_signal_index(signal_sources: dict[str, list[dict[str, str]]] | None = None) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    by_key: dict[str, set[int]] = {}
    source_rows: dict[str, int] = {}
    for source, rows in (signal_sources or {}).items():
        source_rows[source] = len(rows)
        for row in rows:
            item = _signal_item(row, source=source)
            value_keys = set(item) - {"source"}
            keys = _signal_row_keys(row)
            if not value_keys or not keys:
                continue
            item_id = len(items)
            items.append(item)
            for key in keys:
                by_key.setdefault(key, set()).add(item_id)
    return {"items": items, "by_key": by_key, "source_rows": source_rows}


def _lookup_signals(row: dict[str, str], signal_index: dict[str, Any] | None) -> dict[str, Any]:
    if not signal_index:
        return {}
    item_ids: set[int] = set()
    by_key: dict[str, set[int]] = signal_index.get("by_key", {})
    for key in _product_signal_keys(row):
        item_ids.update(by_key.get(key, set()))
    items: list[dict[str, Any]] = signal_index.get("items", [])
    result: dict[str, Any] = {}
    fallback_stock_total = 0.0
    has_fallback_stock = False
    for item_id in item_ids:
        item = items[item_id]
        for field in ("sales_units_30d", "sales_revenue_30d"):
            if field in item:
                result[field] = result.get(field, 0.0) + item[field]
        if "stock_total" in item and "ozon_stock_total" not in item and "wb_stock_total" not in item:
            fallback_stock_total += item["stock_total"]
            has_fallback_stock = True
        for field in (
            "ozon_stock_total",
            "wb_stock_total",
            "parser_visible_queries",
            "parser_top30_queries",
            "parser_max_query_popularity_7d",
        ):
            if field in item:
                result[field] = max(result.get(field, item[field]), item[field])
        if "parser_best_position" in item:
            current = result.get("parser_best_position")
            result["parser_best_position"] = item["parser_best_position"] if current is None else min(current, item["parser_best_position"])
    marketplace_stock_total = result.get("ozon_stock_total", 0.0) + result.get("wb_stock_total", 0.0)
    if marketplace_stock_total or "ozon_stock_total" in result or "wb_stock_total" in result:
        result["stock_total"] = marketplace_stock_total
    elif has_fallback_stock:
        result["stock_total"] = fallback_stock_total
    return result


def _business_score_and_reasons(signals: dict[str, Any]) -> tuple[int, list[str], str]:
    score = 0
    reasons: list[str] = []
    focus_parts: list[str] = []
    sales_units = _float_value(signals.get("sales_units_30d"))
    stock_total = _float_value(signals.get("stock_total"))
    best_position = _float_value(signals.get("parser_best_position"))
    visible_queries = _float_value(signals.get("parser_visible_queries"))

    if sales_units is not None and sales_units > 0:
        score += 25
        reasons.append("sales_positive")
        focus_parts.append("business_priority")

    if stock_total is not None:
        if stock_total > 0:
            score += 10
            reasons.append("stock_positive")
        else:
            reasons.append("stock_zero")
            focus_parts.append("stock_check")

    if best_position is not None:
        if best_position <= 30:
            score += 30
            reasons.append("parser_top30_visible")
        elif best_position <= 100:
            score += 20
            reasons.append("parser_top100_visible")
        else:
            score += 10
            reasons.append("parser_visible")
        focus_parts.append("seo_visibility")
    elif visible_queries is not None and visible_queries > 0:
        score += 10
        reasons.append("parser_visible_queries")
        focus_parts.append("seo_visibility")

    if not reasons:
        return 0, [], ""
    return score, reasons, ";".join(dict.fromkeys(focus_parts))


def _business_priority(signals: dict[str, Any], business_reasons: list[str]) -> str:
    stock_total = _float_value(signals.get("stock_total"))
    best_position = _float_value(signals.get("parser_best_position"))
    sales_units = _float_value(signals.get("sales_units_30d"))
    if stock_total is not None and stock_total <= 0:
        return "blocked_by_stock"
    if (sales_units is not None and sales_units > 0) or (best_position is not None and best_position <= 30):
        return "now"
    if business_reasons:
        return "watch"
    return ""


def _score_and_reasons(row: dict[str, str], signals: dict[str, Any] | None = None) -> tuple[int, list[str], str, list[str]]:
    score = 0
    reasons: list[str] = []
    focus_parts: list[str] = []
    business_reasons: list[str] = []

    if row.get("title_alignment_status") == "mismatch":
        score += 35
        reasons.append("title_mismatch")
        focus_parts.append("content_unification")

    if row.get("marketplace_presence") in {"ozon_only", "wb_only"}:
        score += 30
        reasons.append("marketplace_only")
        focus_parts.append("assortment_transfer_review")

    if not normalize_sku(row.get("cost_total")):
        score += 15
        reasons.append("missing_cost")
        focus_parts.append("margin_data")

    if row.get("full_snapshot_status") not in {"both_found", "ozon_found", "wb_found"}:
        score += 25
        reasons.append("content_snapshot_missing")
        focus_parts.append("data_gap")

    if _has_ozon(row):
        ozon_photo_count = _int_value(row.get("ozon_photo_count"))
        if ozon_photo_count < 5:
            score += 25
            reasons.append("ozon_photo_lt5")
            focus_parts.append("photo_backlog")
        if not _is_true(row.get("ozon_description_present")):
            score += 20
            reasons.append("ozon_description_missing")
            focus_parts.append("text_content")
        if _int_value(row.get("ozon_attribute_count")) == 0:
            score += 15
            reasons.append("ozon_attributes_missing")
            focus_parts.append("attributes")
        if _count_tokens(row.get("ozon_hashtags", "")) == 0:
            score += 10
            reasons.append("ozon_hashtags_missing")
            focus_parts.append("seo_hashtags")

    if _has_wb(row):
        wb_photo_count = _int_value(row.get("wb_photo_count"))
        if wb_photo_count < 5:
            score += 20
            reasons.append("wb_photo_lt5")
            focus_parts.append("photo_backlog")
        if not _is_true(row.get("wb_description_present")):
            score += 20
            reasons.append("wb_description_missing")
            focus_parts.append("text_content")
        if _int_value(row.get("wb_attribute_count")) == 0:
            score += 15
            reasons.append("wb_attributes_missing")
            focus_parts.append("attributes")

    business_score, business_reasons, business_focus = _business_score_and_reasons(signals or {})
    if business_score:
        score += business_score
    if business_reasons:
        reasons.extend(business_reasons)
    if business_focus:
        focus_parts.extend(business_focus.split(";"))

    if not reasons:
        return 0, [], "ready_for_visual_seo_audit", []
    return score, reasons, ";".join(dict.fromkeys(focus_parts)), business_reasons


def _priority(score: int) -> str:
    if score >= 60:
        return "high"
    if score >= 30:
        return "normal"
    return "low"


def _next_step(row: dict[str, str], reasons: list[str]) -> str:
    if "stock_zero" in reasons:
        return "Проверить актуальный остаток и не начинать SEO/перенос до решения по наличию."
    if "sales_positive" in reasons or "parser_top30_visible" in reasons:
        return "Поставить в ближайший визуальный SEO-аудит: есть продажи или видимость в parser."
    if "content_snapshot_missing" in reasons:
        return "Повторить fetch-card-content и проверить native IDs."
    if "marketplace_only" in reasons:
        return "Проверить продажи/маржу/остатки и решить, переносить ли товар на вторую площадку."
    if "title_mismatch" in reasons:
        return "Провести визуальный аудит фото и подготовить единый SEO/content draft Ozon/WB."
    if any(reason.endswith("photo_lt5") for reason in reasons):
        return "Открыть все фото, сделать коллаж и поставить задачи дизайнеру по недостающим изображениям."
    if any("description_missing" in reason or "attributes_missing" in reason for reason in reasons):
        return "Проверить карточку и подготовить draft описания/характеристик."
    if "ozon_hashtags_missing" in reasons:
        return "Подобрать релевантные Ozon-хештеги после проверки запросов и фото."
    return normalize_sku(row.get("next_content_step")) or "Провести карточный аудит по product_card_work_runbook."


def build_card_content_audit_backlog(
    content_rows: list[dict[str, str]],
    *,
    include_low: bool = False,
    signal_index: dict[str, Any] | None = None,
) -> tuple[list[CardContentBacklogRow], dict[str, Any]]:
    backlog: list[CardContentBacklogRow] = []

    for row in content_rows:
        signals = _lookup_signals(row, signal_index)
        score, reasons, focus, business_reasons = _score_and_reasons(row, signals)
        if score == 0 and not include_low:
            continue
        priority = _priority(score)
        backlog.append(
            CardContentBacklogRow(
                backlog_rank="",
                audit_priority=priority,
                audit_focus=focus,
                score=str(score),
                internal_product_id=normalize_sku(row.get("internal_product_id")),
                internal_sku=normalize_sku(row.get("internal_sku")),
                product_name=normalize_sku(row.get("product_name")),
                mapping_status=normalize_sku(row.get("mapping_status")),
                marketplace_presence=normalize_sku(row.get("marketplace_presence")),
                title_alignment_status=normalize_sku(row.get("title_alignment_status")),
                full_snapshot_status=normalize_sku(row.get("full_snapshot_status")),
                transfer_direction=normalize_sku(row.get("transfer_direction")),
                ozon_offer_id=normalize_sku(row.get("ozon_offer_id")),
                wb_vendor_code=normalize_sku(row.get("wb_vendor_code")),
                ozon_photo_count=normalize_sku(row.get("ozon_photo_count")),
                wb_photo_count=normalize_sku(row.get("wb_photo_count")),
                ozon_description_present=normalize_sku(row.get("ozon_description_present")),
                wb_description_present=normalize_sku(row.get("wb_description_present")),
                ozon_attribute_count=normalize_sku(row.get("ozon_attribute_count")),
                wb_attribute_count=normalize_sku(row.get("wb_attribute_count")),
                ozon_hashtags_count=str(_count_tokens(row.get("ozon_hashtags", ""))),
                wb_tags_count=str(_count_tokens(row.get("wb_tags", ""))),
                cost_total=normalize_sku(row.get("cost_total")),
                sales_units_30d=_format_number(signals.get("sales_units_30d")),
                sales_revenue_30d=_format_number(signals.get("sales_revenue_30d")),
                stock_total=_format_number(signals.get("stock_total")),
                ozon_stock_total=_format_number(signals.get("ozon_stock_total")),
                wb_stock_total=_format_number(signals.get("wb_stock_total")),
                parser_best_position=_format_number(signals.get("parser_best_position")),
                parser_visible_queries=_format_number(signals.get("parser_visible_queries")),
                parser_top30_queries=_format_number(signals.get("parser_top30_queries")),
                parser_max_query_popularity_7d=_format_number(signals.get("parser_max_query_popularity_7d")),
                business_priority=_business_priority(signals, business_reasons),
                business_reasons=";".join(business_reasons),
                reasons=";".join(reasons) if reasons else "ready_for_visual_seo_audit",
                next_step=_next_step(row, reasons),
                notes=normalize_sku(row.get("notes")),
            )
        )

    backlog.sort(
        key=lambda item: (
            -_int_value(item.score),
            item.mapping_status != "confirmed",
            item.internal_sku or item.internal_product_id,
        )
    )
    for index, row in enumerate(backlog, start=1):
        row.backlog_rank = str(index)

    summary = {
        "input_rows": len(content_rows),
        "backlog_rows": len(backlog),
        "high_priority_rows": sum(1 for row in backlog if row.audit_priority == "high"),
        "normal_priority_rows": sum(1 for row in backlog if row.audit_priority == "normal"),
        "low_priority_rows": sum(1 for row in backlog if row.audit_priority == "low"),
        "title_mismatch_rows": sum(1 for row in backlog if "title_mismatch" in row.reasons),
        "marketplace_only_rows": sum(1 for row in backlog if "marketplace_only" in row.reasons),
        "photo_lt5_rows": sum(1 for row in backlog if "photo_lt5" in row.reasons),
        "missing_cost_rows": sum(1 for row in backlog if "missing_cost" in row.reasons),
        "ozon_hashtags_missing_rows": sum(1 for row in backlog if "ozon_hashtags_missing" in row.reasons),
        "sales_signal_rows": sum(1 for row in backlog if row.sales_units_30d or row.sales_revenue_30d),
        "sales_positive_rows": sum(1 for row in backlog if "sales_positive" in row.business_reasons),
        "stock_signal_rows": sum(1 for row in backlog if row.stock_total or row.ozon_stock_total or row.wb_stock_total),
        "stock_zero_rows": sum(1 for row in backlog if "stock_zero" in row.business_reasons),
        "parser_signal_rows": sum(1 for row in backlog if row.parser_best_position or row.parser_visible_queries),
        "parser_visible_rows": sum(1 for row in backlog if "parser_" in row.business_reasons),
        "business_priority_now_rows": sum(1 for row in backlog if row.business_priority == "now"),
        "business_priority_blocked_by_stock_rows": sum(1 for row in backlog if row.business_priority == "blocked_by_stock"),
        "include_low": include_low,
    }
    return backlog, summary


def _write_report(path: Path, *, run_id: str, started_at: datetime, result: dict[str, Any]) -> None:
    lines = [
        "# Card Content Audit Backlog Report",
        "",
        f"Run ID: `{run_id}`",
        f"Started at: `{started_at.isoformat(timespec='seconds')}`",
        f"Status: `{result['overall_status']}`",
        "",
        "## Summary",
        "",
    ]
    for key in sorted(result["summary"]):
        lines.append(f"- `{key}`: {result['summary'][key]}")

    lines.extend(["", "## Top Backlog Rows", ""])
    for row in result.get("backlog_sample", [])[:30]:
        lines.append(
            f"- `{row['backlog_rank']}` `{row['audit_priority']}` score `{row['score']}` "
            f"`{row['internal_product_id']}`: {row['product_name']} "
            f"({row['reasons']})"
        )
    if result.get("backlog_sample_truncated"):
        lines.append(f"- truncated: {result['backlog_sample_truncated']} more rows")

    lines.extend(["", "## Artifacts", ""])
    for key, value in sorted(result["artifacts"].items()):
        lines.append(f"- `{key}`: `{value}`")

    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Read-only backlog only. It does not recommend final card text or apply changes.",
            "- `photo_lt5` uses API photo count and still requires visual inspection and collage.",
            "- Optional sales, stock and parser signals only adjust audit priority; they do not replace visual card audit.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _read_optional_signal_csvs(paths: list[Path] | None, errors: dict[str, str], label: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in paths or []:
        try:
            rows.extend(_read_csv(path))
        except Exception as exc:  # noqa: BLE001 - task report must capture local input errors
            errors[f"{label}:{path}"] = str(exc)
    return rows


def run_card_content_audit_backlog(
    *,
    data_dir: Path = Path("data"),
    content_master_path: Path | None = None,
    output_dir: Path | None = None,
    run_id: str | None = None,
    include_low: bool = False,
    sales_signals_paths: list[Path] | None = None,
    stock_signals_paths: list[Path] | None = None,
    parser_signals_paths: list[Path] | None = None,
) -> dict[str, Any]:
    started_at = datetime.now()
    run_id = run_id or f"card_content_audit_backlog_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    content_master_path = content_master_path or data_dir / DEFAULT_CONTENT_MASTER_PATH
    output_dir = ensure_dir(output_dir or data_dir / DEFAULT_OUTPUT_DIR)

    errors: dict[str, str] = {}
    content_rows: list[dict[str, str]] = []
    try:
        content_rows = _read_csv(content_master_path)
    except Exception as exc:  # noqa: BLE001 - task report must capture local input errors
        errors["content_master"] = str(exc)

    sales_signal_rows = _read_optional_signal_csvs(sales_signals_paths, errors, "sales_signals")
    stock_signal_rows = _read_optional_signal_csvs(stock_signals_paths, errors, "stock_signals")
    parser_signal_rows = _read_optional_signal_csvs(parser_signals_paths, errors, "parser_signals")
    signal_index = build_signal_index(
        {
            "sales": sales_signal_rows,
            "stocks": stock_signal_rows,
            "parser": parser_signal_rows,
        }
    )

    backlog: list[CardContentBacklogRow] = []
    summary: dict[str, Any] = {
        "input_rows": len(content_rows),
        "backlog_rows": 0,
        "high_priority_rows": 0,
        "normal_priority_rows": 0,
        "low_priority_rows": 0,
        "title_mismatch_rows": 0,
        "marketplace_only_rows": 0,
        "photo_lt5_rows": 0,
        "missing_cost_rows": 0,
        "ozon_hashtags_missing_rows": 0,
        "sales_input_rows": len(sales_signal_rows),
        "stock_input_rows": len(stock_signal_rows),
        "parser_input_rows": len(parser_signal_rows),
        "sales_signal_rows": 0,
        "sales_positive_rows": 0,
        "stock_signal_rows": 0,
        "stock_zero_rows": 0,
        "parser_signal_rows": 0,
        "parser_visible_rows": 0,
        "business_priority_now_rows": 0,
        "business_priority_blocked_by_stock_rows": 0,
        "include_low": include_low,
    }
    if not errors:
        backlog, summary = build_card_content_audit_backlog(
            content_rows,
            include_low=include_low,
            signal_index=signal_index,
        )
        summary.update(
            {
                "sales_input_rows": len(sales_signal_rows),
                "stock_input_rows": len(stock_signal_rows),
                "parser_input_rows": len(parser_signal_rows),
                "signal_items_indexed": len(signal_index.get("items", [])),
            }
        )

    backlog_dicts = [asdict(row) for row in backlog]
    backlog_csv_path = output_dir / "card_content_audit_backlog.csv"
    backlog_json_path = output_dir / "card_content_audit_backlog.json"
    report_path = run_dir / "card_content_audit_backlog_report.md"
    summary_path = run_dir / "summary.json"
    if not errors:
        _write_dict_csv(backlog_csv_path, backlog_dicts, BACKLOG_FIELDS)
        write_json(backlog_json_path, backlog_dicts)

    overall_status = "error" if errors else "warning" if backlog else "ok"
    artifacts = {
        "run_dir": str(run_dir),
        "backlog_csv": str(backlog_csv_path),
        "backlog_json": str(backlog_json_path),
        "report": str(report_path),
        "summary": str(summary_path),
        "run_manifest": str(run_dir / "manifest.json"),
    }
    backlog_sample = backlog_dicts[:50]
    result = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "read_only",
        "overall_status": overall_status,
        "summary": summary,
        "errors": errors,
        "backlog_sample": backlog_sample,
        "backlog_sample_truncated": max(0, len(backlog_dicts) - len(backlog_sample)),
        "inputs": {
            "content_master_path": str(content_master_path),
            "include_low": include_low,
            "sales_signals_paths": [str(path) for path in sales_signals_paths or []],
            "stock_signals_paths": [str(path) for path in stock_signals_paths or []],
            "parser_signals_paths": [str(path) for path in parser_signals_paths or []],
        },
        "artifacts": artifacts,
    }

    write_json(summary_path, result)
    _write_report(report_path, run_id=run_id, started_at=started_at, result=result)
    write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=result,
        task="card-content-audit-backlog",
        mode="read_only",
        risk="low",
        marketplaces=["ozon", "wb"],
        inputs=result["inputs"],
    )
    return result
