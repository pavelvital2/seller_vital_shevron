from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from openpyxl import Workbook

from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.reports.writer import ensure_dir, write_json


DEFAULT_SIGNALS_DIR = Path("catalog/content/signals")


def _decimal(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value).replace(" ", "").replace("\u00a0", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _int(value: Any) -> int:
    try:
        return int(_decimal(value))
    except (InvalidOperation, ValueError):
        return 0


def _round2(value: Decimal | None) -> str:
    if value is None:
        return ""
    return str(value.quantize(Decimal("0.01")))


def is_default_wb_parser_enriched_apply_payload_row(
    row: dict[str, Any],
    *,
    min_bid: Decimal,
) -> bool:
    try:
        current_bid = Decimal(
            str(row.get("current_bid") or "").replace(" ", "").replace(",", ".")
        )
        target_bid = Decimal(
            str(row.get("final_target_bid") or "").replace(" ", "").replace(",", ".")
        )
    except (InvalidOperation, ValueError):
        return False
    return bool(
        row.get("parser_enriched_action") == "apply_ready"
        and row.get("current_bid_source") == "current_bid_api"
        and target_bid >= min_bid
        and target_bid != current_bid
        and str(row.get("current_bid_place") or "").strip()
        in {"search", "recommendations", "combined"}
    )


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [{key: (value or "").strip() for key, value in row.items()} for row in csv.DictReader(handle)]


def _write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    headers: list[str] = []
    for row in rows:
        for key in row:
            if key not in headers:
                headers.append(key)
    if not headers:
        headers = ["empty"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_xlsx(rows: list[dict[str, Any]], path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "WB Parser Enriched Bids"
    headers: list[str] = []
    for row in rows:
        for key in row:
            if key not in headers:
                headers.append(key)
    if not headers:
        headers = ["empty"]
    sheet.append(headers)
    for row in rows:
        sheet.append([row.get(header, "") for header in headers])
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for column in sheet.columns:
        max_len = max(len(str(cell.value or "")) for cell in column)
        sheet.column_dimensions[column[0].column_letter].width = min(max(max_len + 2, 12), 70)
    workbook.save(path)


def _latest_base_plan_dir(data_dir: Path) -> Path:
    candidates = sorted((data_dir / "runs").glob("*/wb_promotion_bid_plan_*"))
    for candidate in reversed(candidates):
        if (candidate / "wb_promotion_bid_plan.csv").exists():
            return candidate
    raise FileNotFoundError("No wb_promotion_bid_plan_* run with wb_promotion_bid_plan.csv found")


def _base_plan_dir(data_dir: Path, base_plan_run_id: str | None) -> Path:
    if base_plan_run_id:
        matches = sorted((data_dir / "runs").glob(f"*/{base_plan_run_id}"))
        if not matches:
            raise FileNotFoundError(f"WB promotion bid plan run not found: {base_plan_run_id}")
        return matches[-1]
    return _latest_base_plan_dir(data_dir)


def _default_signal_paths(data_dir: Path) -> list[Path]:
    all_signals = data_dir / DEFAULT_SIGNALS_DIR / "all_signals.csv"
    if all_signals.exists():
        return [all_signals]
    paths = [
        data_dir / DEFAULT_SIGNALS_DIR / "parser_signals.csv",
        data_dir / DEFAULT_SIGNALS_DIR / "stock_signals.csv",
        data_dir / DEFAULT_SIGNALS_DIR / "sales_signals.csv",
    ]
    return [path for path in paths if path.exists()]


def _is_api_sales_source(source: str) -> bool:
    text = source.lower()
    return "/api/v1/supplier/sales" in text or "wb_sales" in text or "supplier/sales" in text


def _is_api_stock_source(source: str) -> bool:
    text = source.lower()
    return "/api/v1/supplier/stocks" in text or "wb_stocks" in text or "supplier/stocks" in text


def _is_parser_source(source: str) -> bool:
    return "parser" in source.lower()


def _signal_keys(row: dict[str, str]) -> list[str]:
    keys: list[str] = []
    for namespace, value in (
        ("nm", row.get("wb_nm_id") or row.get("nmID") or row.get("nm_id")),
        ("vendor", row.get("wb_vendor_code") or row.get("vendorCode") or row.get("vendor_code")),
        ("internal_sku", row.get("internal_sku")),
        ("internal_product_id", row.get("internal_product_id")),
    ):
        text = str(value or "").strip().lower()
        if text:
            keys.append(f"{namespace}:{text}")
    return keys


def _merge_signal(target: dict[str, Any], row: dict[str, str]) -> None:
    source = row.get("source") or ""
    target["signal_sources"].add(source)
    if _is_api_sales_source(source):
        target["has_wb_statistics_sales_signal"] = True
    if _is_api_stock_source(source):
        target["has_api_stock_signal"] = True
    if _is_parser_source(source):
        target["has_parser_signal"] = True
    for field in ("internal_sku", "internal_product_id", "wb_vendor_code", "wb_nm_id"):
        if not target.get(field):
            target[field] = _first_text(row.get(field), row.get(field.replace("wb_", "")))

    if _is_api_sales_source(source):
        for field in ("sales_units_30d", "sales_revenue_30d"):
            value = _decimal(row.get(field))
            if value:
                target[field] = target.get(field, Decimal("0")) + value
    for field in ("stock_total", "wb_stock_total"):
        value = _decimal(row.get(field))
        if value:
            target[field] = max(target.get(field, Decimal("0")), value)

    best = _decimal(row.get("parser_best_position"))
    if best:
        current = target.get("parser_best_position", Decimal("0"))
        target["parser_best_position"] = best if not current else min(current, best)
    for field in ("parser_visible_queries", "parser_top30_queries", "parser_max_query_popularity_7d"):
        value = _decimal(row.get(field))
        if value:
            target[field] = max(target.get(field, Decimal("0")), value)


def build_signal_index(signal_rows: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    for row in signal_rows:
        keys = _signal_keys(row)
        if not keys:
            continue
        signal: dict[str, Any] = {"signal_sources": set()}
        for key in keys:
            if key in by_key:
                signal = by_key[key]
                break
        _merge_signal(signal, row)
        for key in keys:
            by_key[key] = signal
    return by_key


def _lookup_signal(row: dict[str, str], signal_index: dict[str, dict[str, Any]]) -> dict[str, Any]:
    keys = [
        f"nm:{str(row.get('nm_id') or '').strip().lower()}",
        f"vendor:{str(row.get('wb_vendor_code') or row.get('vendorCode') or '').strip().lower()}",
    ]
    for key in keys:
        if key in signal_index:
            return signal_index[key]
    return {"signal_sources": set()}


def _target_bid(current_bid: Decimal, change_percent: Decimal, min_bid: Decimal) -> tuple[Decimal, str]:
    target = (current_bid * (Decimal("1") + change_percent / Decimal("100"))).quantize(Decimal("0.01"))
    if target < min_bid:
        return min_bid, "min_bid_floor"
    return target, ""


def _classify_row(
    row: dict[str, str],
    signal: dict[str, Any],
    *,
    min_stock: int,
    parser_test_increase_percent: Decimal,
    min_bid: Decimal,
) -> dict[str, Any]:
    base_action = row.get("recommended_action") or "keep_monitor"
    current_bid = _decimal(row.get("current_bid"))
    base_target = _decimal(row.get("target_bid"))
    orders = _int(row.get("orders"))
    spend = _decimal(row.get("spend"))
    stock = signal.get("wb_stock_total") or signal.get("stock_total") or Decimal("0")
    sales_units = signal.get("sales_units_30d", Decimal("0"))
    has_wb_statistics_sales_signal = bool(signal.get("has_wb_statistics_sales_signal"))
    has_api_stock_signal = bool(signal.get("has_api_stock_signal"))
    has_parser_signal = bool(signal.get("has_parser_signal"))
    best_position = signal.get("parser_best_position", Decimal("0"))
    visible_queries = signal.get("parser_visible_queries", Decimal("0"))
    top30_queries = signal.get("parser_top30_queries", Decimal("0"))
    parser_weak = bool(best_position and best_position > 30)
    parser_close = bool(best_position and best_position <= 100)
    parser_visible = bool(visible_queries or best_position)
    has_stock = stock > 0
    has_current_bid = current_bid > 0

    final_action = "keep_monitor"
    final_reason = "нет parser/sales/stock основания для изменения"
    target_bid = base_target if base_target else current_bid
    requested_change = _decimal(row.get("requested_bid_change_percent"))
    apply_allowed = False
    risk_flags: list[str] = []
    if not has_wb_statistics_sales_signal:
        risk_flags.append("missing_wb_statistics_sales_signal")

    if not has_current_bid:
        final_action = "blocked"
        final_reason = "нет текущей ставки из WB Promotion API"
        risk_flags.append("missing_current_bid")
    elif not has_stock:
        final_action = "blocked"
        final_reason = "нет подтвержденного остатка"
        risk_flags.append("missing_or_zero_stock")
    elif stock < Decimal(min_stock):
        final_action = "review_only"
        final_reason = f"низкий остаток `{int(stock)}` меньше порога `{min_stock}`"
        risk_flags.append("low_stock")
    elif base_action == "scale_candidate":
        final_action = "apply_ready"
        final_reason = "базовый план рекомендует повышение, остаток подтвержден"
        apply_allowed = True
    elif base_action.startswith("reduce") or base_action == "review_card_then_reduce":
        final_action = "reduce_or_stop_review"
        final_reason = f"базовый план `{base_action}` из-за эффективности рекламы"
        risk_flags.append("base_reduce_signal")
    elif parser_visible and parser_weak and spend == 0:
        final_action = "review_only"
        final_reason = "есть parser-видимость и остаток, но нет рекламных расходов в периоде"
        target_bid, note = _target_bid(current_bid, parser_test_increase_percent, min_bid)
        requested_change = parser_test_increase_percent
        if note:
            risk_flags.append(note)
    elif parser_visible and parser_weak and orders == 0:
        final_action = "review_only"
        final_reason = "есть parser-видимость и остаток, но реклама пока без заказов"
        target_bid, note = _target_bid(current_bid, parser_test_increase_percent, min_bid)
        requested_change = parser_test_increase_percent
        if note:
            risk_flags.append(note)
    elif parser_visible and parser_close and top30_queries == 0 and orders > 0:
        final_action = "watch"
        final_reason = "есть заказы и позиция в top-100, но не top-30; наблюдать перед повышением"
    elif parser_visible and top30_queries:
        final_action = "watch"
        final_reason = "товар уже виден в top-30; без доп. эффективности ставку не повышать"

    bid_change = target_bid - current_bid if target_bid and current_bid else Decimal("0")
    return {
        **row,
        "parser_enriched_action": final_action,
        "parser_enriched_reason": final_reason,
        "parser_enriched_apply_allowed": str(apply_allowed).lower(),
        "final_target_bid": _round2(target_bid),
        "final_bid_change_amount": _round2(bid_change),
        "final_requested_bid_change_percent": _round2(requested_change),
        "internal_sku": signal.get("internal_sku", ""),
        "internal_product_id": signal.get("internal_product_id", ""),
        "wb_vendor_code": signal.get("wb_vendor_code", ""),
        "wb_stock_total": _round2(stock) if stock else "",
        "sales_units_30d": _round2(sales_units) if sales_units else "",
        "wb_statistics_sales_signal": str(has_wb_statistics_sales_signal).lower(),
        "api_stock_signal": str(has_api_stock_signal).lower(),
        "parser_signal": str(has_parser_signal).lower(),
        "parser_best_position": _round2(best_position) if best_position else "",
        "parser_visible_queries": _round2(visible_queries) if visible_queries else "",
        "parser_top30_queries": _round2(top30_queries) if top30_queries else "",
        "signal_sources": ";".join(sorted(signal.get("signal_sources", set()))),
        "risk_flags": ";".join(risk_flags),
    }


def build_wb_promotion_parser_enriched_rows(
    base_rows: list[dict[str, str]],
    signal_rows: list[dict[str, str]],
    *,
    min_stock: int = 4,
    parser_test_increase_percent: Decimal = Decimal("10"),
    min_bid: Decimal = Decimal("1.00"),
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    signal_index = build_signal_index(signal_rows)
    rows = [
        _classify_row(
            row,
            _lookup_signal(row, signal_index),
            min_stock=min_stock,
            parser_test_increase_percent=parser_test_increase_percent,
            min_bid=min_bid,
        )
        for row in base_rows
    ]
    rows.sort(
        key=lambda row: (
            {"apply_ready": 0, "review_only": 1, "watch": 2, "reduce_or_stop_review": 3, "blocked": 4}.get(
                row.get("parser_enriched_action"), 5
            ),
            -_decimal(row.get("orders")),
            _decimal(row.get("parser_best_position") or "999999"),
            row.get("nm_id", ""),
        )
    )
    action_counts: dict[str, int] = {}
    for row in rows:
        action = str(row.get("parser_enriched_action") or "unknown")
        action_counts[action] = action_counts.get(action, 0) + 1
    candidates = [row for row in rows if row.get("parser_enriched_action") in {"apply_ready", "review_only"}]
    summary = {
        "base_rows": len(base_rows),
        "signal_rows": len(signal_rows),
        "enriched_rows": len(rows),
        "candidate_rows": len(candidates),
        "apply_ready_rows": action_counts.get("apply_ready", 0),
        "apply_payload_rows": sum(
            1
            for row in rows
            if is_default_wb_parser_enriched_apply_payload_row(
                row,
                min_bid=min_bid,
            )
        ),
        "review_only_rows": action_counts.get("review_only", 0),
        "watch_rows": action_counts.get("watch", 0),
        "reduce_or_stop_review_rows": action_counts.get("reduce_or_stop_review", 0),
        "blocked_rows": action_counts.get("blocked", 0),
        "rows_with_parser_signal": sum(1 for row in rows if row.get("parser_best_position") or row.get("parser_visible_queries")),
        "rows_with_stock_signal": sum(1 for row in rows if row.get("wb_stock_total")),
        "rows_with_wb_statistics_sales_signal": sum(1 for row in rows if row.get("wb_statistics_sales_signal") == "true"),
        "rows_missing_wb_statistics_sales_signal": sum(1 for row in rows if "missing_wb_statistics_sales_signal" in str(row.get("risk_flags", ""))),
        "action_counts": action_counts,
        "min_stock": min_stock,
        "parser_test_increase_percent": _round2(parser_test_increase_percent),
        "min_bid": _round2(min_bid),
    }
    return rows, summary


def _write_report(path: Path, *, result: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    summary = result["summary"]
    lines = [
        "# WB Promotion Parser-Enriched Bid Plan",
        "",
        "Mode: read-only/dry-run. No bids, budgets, campaign statuses or products changed.",
        "",
        f"Run ID: `{result['run_id']}`",
        f"Base plan run: `{result['base_plan_run_id']}`",
        "",
        "## Summary",
        "",
    ]
    for key in (
        "base_rows",
        "signal_rows",
        "enriched_rows",
        "candidate_rows",
        "apply_ready_rows",
        "apply_payload_rows",
        "review_only_rows",
        "watch_rows",
        "reduce_or_stop_review_rows",
        "blocked_rows",
        "rows_with_parser_signal",
        "rows_with_stock_signal",
        "rows_with_wb_statistics_sales_signal",
        "rows_missing_wb_statistics_sales_signal",
    ):
        lines.append(f"- `{key}`: {summary.get(key)}")

    lines.extend(["", "## Candidates", ""])
    candidates = [row for row in rows if row.get("parser_enriched_action") in {"apply_ready", "review_only"}]
    if not candidates:
        lines.append("- none")
    for row in candidates[:40]:
        lines.append(
            "- "
            f"`{row.get('nm_id')}` {row.get('name')}: `{row.get('parser_enriched_action')}`, "
            f"bid `{row.get('current_bid')}` -> `{row.get('final_target_bid')}`, "
            f"orders `{row.get('orders')}`, spend `{row.get('spend')}`, "
            f"stock `{row.get('wb_stock_total') or 'н/д'}`, "
            f"parser best `{row.get('parser_best_position') or 'н/д'}`, "
            f"reason: {row.get('parser_enriched_reason')}"
        )

    lines.extend(["", "## Risks", ""])
    lines.append("- Apply is forbidden without explicit owner approval, fresh WB promotion report, fresh enriched plan and drift-check.")
    lines.append("- Parser visibility is a scaling signal, not proof of profitable promotion.")
    lines.append(
        "- Sales signals must come from WB Statistics API, not parser. Rows without matched WB Statistics sales are marked `missing_wb_statistics_sales_signal`."
    )
    lines.extend(["", "## Artifacts", ""])
    for key, value in sorted(result["artifacts"].items()):
        lines.append(f"- `{key}`: `{value}`")
    path.write_text("\n".join(lines), encoding="utf-8")


def run_wb_promotion_bid_parser_enriched_plan(
    *,
    data_dir: Path = Path("data"),
    run_id: str | None = None,
    base_plan_run_id: str | None = None,
    base_plan_csv: Path | None = None,
    signals_csv: list[Path] | None = None,
    min_stock: int = 4,
    parser_test_increase_percent: Decimal = Decimal("10"),
    min_bid: Decimal = Decimal("1.00"),
) -> dict[str, Any]:
    started_at = datetime.now()
    run_id = run_id or f"wb_promotion_bid_parser_enriched_plan_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    processed_dir = ensure_dir(run_dir / "processed")

    base_dir = None
    if base_plan_csv is None:
        base_dir = _base_plan_dir(data_dir, base_plan_run_id)
        base_plan_csv = base_dir / "wb_promotion_bid_plan.csv"
    if not base_plan_csv.exists():
        raise FileNotFoundError(f"WB promotion base plan CSV not found: {base_plan_csv}")

    signal_paths = signals_csv or _default_signal_paths(data_dir)
    base_rows = _read_csv(base_plan_csv)
    signal_rows: list[dict[str, str]] = []
    for path in signal_paths:
        signal_rows.extend(_read_csv(path))

    rows, summary = build_wb_promotion_parser_enriched_rows(
        base_rows,
        signal_rows,
        min_stock=min_stock,
        parser_test_increase_percent=parser_test_increase_percent,
        min_bid=min_bid,
    )
    candidates = [row for row in rows if row.get("parser_enriched_action") in {"apply_ready", "review_only"}]
    apply_preview = [row for row in candidates if row.get("parser_enriched_action") == "apply_ready"]

    plan_csv = run_dir / "wb_promotion_bid_parser_enriched_plan.csv"
    candidates_csv = run_dir / "wb_promotion_bid_parser_enriched_candidates.csv"
    apply_preview_csv = run_dir / "wb_promotion_bid_parser_enriched_apply_preview.csv"
    xlsx = run_dir / "wb_promotion_bid_parser_enriched_plan.xlsx"
    report = run_dir / "wb_promotion_bid_parser_enriched_plan.md"
    rows_json = processed_dir / "calculation_rows.json"

    _write_csv(rows, plan_csv)
    _write_csv(candidates, candidates_csv)
    _write_csv(apply_preview, apply_preview_csv)
    _write_xlsx(rows, xlsx)
    write_json(rows_json, rows)

    artifacts = {
        "run_dir": str(run_dir),
        "report": str(report),
        "csv": str(plan_csv),
        "candidates_csv": str(candidates_csv),
        "apply_preview_csv": str(apply_preview_csv),
        "xlsx": str(xlsx),
        "calculation_rows": str(rows_json),
        "summary": str(run_dir / "summary.json"),
        "source_base_plan_csv": str(base_plan_csv),
        "signal_csvs": [str(path) for path in signal_paths],
        "run_manifest": str(run_dir / "manifest.json"),
    }
    result = {
        "run_id": run_id,
        "base_plan_run_id": base_plan_run_id or (base_dir.name if base_dir else ""),
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "dry-run",
        "overall_status": "ok",
        "pending_id": f"{run_id}_pending",
        "summary": summary,
        "artifacts": artifacts,
        "apply_performed": False,
    }
    write_json(run_dir / "summary.json", result)
    _write_report(report, result=result, rows=rows)
    write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=result,
        task="wb-promotion-bids-parser-enriched-plan",
        mode="dry_run",
        risk="normal",
        marketplaces=["wb"],
        inputs={
            "base_plan_run_id": base_plan_run_id,
            "base_plan_csv": str(base_plan_csv),
            "signals_csv": [str(path) for path in signal_paths],
            "min_stock": min_stock,
            "parser_test_increase_percent": _round2(parser_test_increase_percent),
            "min_bid": _round2(min_bid),
        },
    )
    return result
