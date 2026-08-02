from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Any

from openpyxl import Workbook

from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.reports.writer import ensure_dir, write_json


@dataclass(frozen=True)
class CpcOptimizationThresholds:
    zero_orders_spend: Decimal = Decimal("50")
    high_drr_percent: Decimal = Decimal("12")
    high_drr_min_spend: Decimal = Decimal("100")
    scale_min_orders: int = 8
    scale_max_drr_percent: Decimal = Decimal("5")
    card_review_reduce_percent: Decimal = Decimal("30")
    max_reduce_percent: Decimal = Decimal("50")
    scale_low_drr_percent: Decimal = Decimal("20")
    scale_mid_drr_percent: Decimal = Decimal("15")
    scale_high_drr_percent: Decimal = Decimal("10")

    def __post_init__(self) -> None:
        object.__setattr__(self, "zero_orders_spend", _decimal(self.zero_orders_spend))
        object.__setattr__(self, "high_drr_percent", _decimal(self.high_drr_percent))
        object.__setattr__(self, "high_drr_min_spend", _decimal(self.high_drr_min_spend))
        object.__setattr__(self, "scale_min_orders", int(self.scale_min_orders))
        object.__setattr__(self, "scale_max_drr_percent", _decimal(self.scale_max_drr_percent))
        object.__setattr__(self, "card_review_reduce_percent", _decimal(self.card_review_reduce_percent))
        object.__setattr__(self, "max_reduce_percent", _decimal(self.max_reduce_percent))
        object.__setattr__(self, "scale_low_drr_percent", _decimal(self.scale_low_drr_percent))
        object.__setattr__(self, "scale_mid_drr_percent", _decimal(self.scale_mid_drr_percent))
        object.__setattr__(self, "scale_high_drr_percent", _decimal(self.scale_high_drr_percent))


@dataclass
class SkuAggregate:
    campaign_id: str
    campaign_title: str
    sku: str
    title: str = ""
    views: int = 0
    clicks: int = 0
    to_cart: int = 0
    orders: int = 0
    spend: Decimal = Decimal("0")
    orders_money: Decimal = Decimal("0")
    spend_days: int = 0
    no_order_spend_days: int = 0
    no_order_spend: Decimal = Decimal("0")


def _decimal(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value).replace(" ", "").replace("\u00a0", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _int(value: Any) -> int:
    return int(_decimal(value))


def _round2(value: Decimal | None) -> str:
    if value is None:
        return ""
    return str(value.quantize(Decimal("0.01")))


def _display_percent(value: Any) -> str:
    return f"{value}%" if value not in (None, "") else "n/a"


def _percent(part: int | Decimal, total: int | Decimal) -> Decimal | None:
    if total == 0:
        return None
    return (Decimal(part) / Decimal(total) * Decimal("100")).quantize(Decimal("0.01"))


def _ratio(part: Decimal, total: Decimal) -> Decimal | None:
    if total == 0:
        return None
    return (part / total).quantize(Decimal("0.01"))


def _target_bid(reference_bid: Decimal | None, change_percent: Decimal | None) -> Decimal | None:
    if reference_bid is None or change_percent is None:
        return None
    multiplier = Decimal("1") + (change_percent / Decimal("100"))
    target = reference_bid * multiplier
    if target < 0:
        return Decimal("0.00")
    return target.quantize(Decimal("0.01"))


def is_default_cpc_apply_payload_row(row: dict[str, Any]) -> bool:
    current_raw = row.get("current_bid")
    target_raw = row.get("target_bid")
    if current_raw in (None, "") or target_raw in (None, ""):
        return False
    try:
        current_bid = Decimal(str(current_raw).replace(" ", "").replace(",", "."))
        target_bid = Decimal(str(target_raw).replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return False
    return bool(
        row.get("bid_reference_type") == "current_bid_api"
        and target_bid >= Decimal("1.00")
        and target_bid != current_bid
    )


def _read_current_bids(path: Path | None) -> dict[str, Decimal]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Current bids file must be a list: {path}")
    result: dict[str, Decimal] = {}
    for row in data:
        if not isinstance(row, dict):
            continue
        sku = str(row.get("sku") or "").strip()
        if not sku:
            continue
        bid = _decimal(row.get("current_bid"))
        result[sku] = bid
    return result


def _scale_change_percent(drr: Decimal | None, thresholds: CpcOptimizationThresholds) -> Decimal:
    if drr is not None and drr <= Decimal("2"):
        return thresholds.scale_low_drr_percent
    if drr is not None and drr <= Decimal("3.5"):
        return thresholds.scale_mid_drr_percent
    return thresholds.scale_high_drr_percent


def _reduce_change_percent(drr: Decimal | None, thresholds: CpcOptimizationThresholds) -> Decimal:
    if drr is None or drr <= 0:
        return -thresholds.max_reduce_percent
    needed = (Decimal("1") - (thresholds.high_drr_percent / drr)) * Decimal("100")
    needed = max(Decimal("15"), needed)
    needed = min(thresholds.max_reduce_percent, needed)
    return -needed.quantize(Decimal("0.01"))


def _latest_cpc_run_dir(data_dir: Path) -> Path:
    run_root = data_dir / "runs"
    candidates = sorted(run_root.glob("*/ozon_cpc_efficiency_*"))
    for candidate in reversed(candidates):
        if (candidate / "processed" / "rows.csv").exists():
            return candidate
    raise FileNotFoundError("No ozon_cpc_efficiency_* run with processed/rows.csv found")


def _source_run_dir(data_dir: Path, source_run_id: str | None) -> Path:
    if source_run_id:
        matches = sorted((data_dir / "runs").glob(f"*/{source_run_id}"))
        if not matches:
            raise FileNotFoundError(f"Source run not found: {source_run_id}")
        return matches[-1]
    return _latest_cpc_run_dir(data_dir)


def _read_source_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [row for row in csv.DictReader(handle)]


def build_cpc_optimization_rows(
    source_rows: list[dict[str, Any]],
    *,
    thresholds: CpcOptimizationThresholds = CpcOptimizationThresholds(),
    current_bids: dict[str, Decimal] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    aggregates: dict[tuple[str, str], SkuAggregate] = {}
    totals = {
        "source_rows": len(source_rows),
        "views": 0,
        "clicks": 0,
        "to_cart": 0,
        "orders": 0,
        "spend": Decimal("0"),
        "orders_money": Decimal("0"),
    }

    for row in source_rows:
        campaign_id = str(row.get("campaign_id") or "").strip()
        sku = str(row.get("sku") or "").strip()
        if not campaign_id or not sku:
            continue
        key = (campaign_id, sku)
        item = aggregates.setdefault(
            key,
            SkuAggregate(
                campaign_id=campaign_id,
                campaign_title=str(row.get("campaign_title") or "").strip(),
                sku=sku,
                title=str(row.get("title") or "").strip(),
            ),
        )
        item.campaign_title = item.campaign_title or str(row.get("campaign_title") or "").strip()
        item.title = item.title or str(row.get("title") or "").strip()
        views = _int(row.get("views"))
        clicks = _int(row.get("clicks"))
        to_cart = _int(row.get("to_cart"))
        orders = _int(row.get("orders"))
        spend = _decimal(row.get("spend"))
        orders_money = _decimal(row.get("orders_money"))

        item.views += views
        item.clicks += clicks
        item.to_cart += to_cart
        item.orders += orders
        item.spend += spend
        item.orders_money += orders_money
        if spend > 0:
            item.spend_days += 1
            if orders == 0:
                item.no_order_spend_days += 1
                item.no_order_spend += spend

        totals["views"] += views
        totals["clicks"] += clicks
        totals["to_cart"] += to_cart
        totals["orders"] += orders
        totals["spend"] += spend
        totals["orders_money"] += orders_money

    rows: list[dict[str, Any]] = []
    action_counts: dict[str, int] = {}
    potential_cut_spend = Decimal("0")
    scale_candidates = 0
    current_bids = current_bids or {}
    current_bid_matches = 0

    for item in aggregates.values():
        avg_cpc = _ratio(item.spend, Decimal(item.clicks))
        ctr = _percent(item.clicks, item.views)
        cart_rate = _percent(item.to_cart, item.clicks)
        order_cr = _percent(item.orders, item.clicks)
        drr = _percent(item.spend, item.orders_money)
        roas = _ratio(item.orders_money, item.spend)

        if item.orders == 0 and item.spend >= thresholds.zero_orders_spend:
            if item.to_cart > 0:
                action = "review_card_then_reduce_or_pause"
                reason = "расход без заказов, но есть добавления в корзину"
                priority = 10
                proposed_bid_change_percent = -thresholds.card_review_reduce_percent
            else:
                action = "pause_or_exclude"
                reason = "расход без заказов и без корзин"
                priority = 20
                proposed_bid_change_percent = Decimal("-100")
            potential_cut_spend += item.spend
        elif (
            item.orders > 0
            and item.spend >= thresholds.high_drr_min_spend
            and drr is not None
            and drr >= thresholds.high_drr_percent
        ):
            action = "reduce_bid_or_review"
            reason = f"ДРР >= {thresholds.high_drr_percent}% при расходе >= {thresholds.high_drr_min_spend}"
            priority = 30
            proposed_bid_change_percent = _reduce_change_percent(drr, thresholds)
            potential_cut_spend += item.spend
        elif item.orders >= thresholds.scale_min_orders and drr is not None and drr <= thresholds.scale_max_drr_percent:
            action = "scale_candidate"
            reason = f"заказы >= {thresholds.scale_min_orders}, ДРР <= {thresholds.scale_max_drr_percent}%"
            priority = 80
            proposed_bid_change_percent = _scale_change_percent(drr, thresholds)
            scale_candidates += 1
        else:
            action = "keep_monitor"
            reason = "нет явного сигнала для изменения"
            priority = 100
            proposed_bid_change_percent = Decimal("0")

        action_counts[action] = action_counts.get(action, 0) + 1
        current_bid = current_bids.get(item.sku)
        if current_bid is not None:
            current_bid_matches += 1
            bid_reference_type = "current_bid_api"
            bid_reference = current_bid
        else:
            bid_reference_type = "avg_cpc_from_report"
            bid_reference = avg_cpc
        target_bid = _target_bid(bid_reference, proposed_bid_change_percent)
        bid_change_amount = target_bid - bid_reference if target_bid is not None and bid_reference is not None else None
        rows.append(
            {
                "marketplace": "OZON",
                "mode": "dry-run",
                "campaign_id": item.campaign_id,
                "campaign_title": item.campaign_title,
                "sku": item.sku,
                "title": item.title,
                "recommended_action": action,
                "reason": reason,
                "current_bid": _round2(current_bid),
                "bid_reference_type": bid_reference_type,
                "bid_reference": _round2(bid_reference),
                "proposed_bid_change_percent": _round2(proposed_bid_change_percent),
                "target_bid": _round2(target_bid),
                "bid_change_amount": _round2(bid_change_amount),
                "priority": priority,
                "views": item.views,
                "clicks": item.clicks,
                "to_cart": item.to_cart,
                "orders": item.orders,
                "spend": _round2(item.spend),
                "orders_money": _round2(item.orders_money),
                "avg_cpc": _round2(avg_cpc),
                "ctr_percent": _round2(ctr),
                "cart_rate_percent": _round2(cart_rate),
                "order_cr_percent": _round2(order_cr),
                "drr_percent": _round2(drr),
                "roas": _round2(roas),
                "spend_days": item.spend_days,
                "no_order_spend_days": item.no_order_spend_days,
                "no_order_spend": _round2(item.no_order_spend),
                "apply_allowed": False,
            }
        )

    rows.sort(key=lambda row: (int(row["priority"]), -_decimal(row["spend"]), row["sku"]))
    summary = {
        "source_rows": totals["source_rows"],
        "sku_rows": len(rows),
        "views": totals["views"],
        "clicks": totals["clicks"],
        "to_cart": totals["to_cart"],
        "orders": totals["orders"],
        "spend": _round2(totals["spend"]),
        "orders_money": _round2(totals["orders_money"]),
        "avg_cpc": _round2(_ratio(totals["spend"], Decimal(totals["clicks"]))),
        "drr_percent": _round2(_percent(totals["spend"], totals["orders_money"])),
        "roas": _round2(_ratio(totals["orders_money"], totals["spend"])),
        "potential_cut_spend": _round2(potential_cut_spend),
        "scale_candidates": scale_candidates,
        "current_bids_loaded": len(current_bids),
        "current_bid_matches": current_bid_matches,
        "current_bid_missing": len(rows) - current_bid_matches,
        "action_rows": sum(count for action, count in action_counts.items() if action != "keep_monitor"),
        "action_rows_with_current_bid": sum(
            1 for row in rows if row["recommended_action"] != "keep_monitor" and row["current_bid"]
        ),
        "apply_payload_rows": sum(
            1 for row in rows if is_default_cpc_apply_payload_row(row)
        ),
        "action_counts": action_counts,
        "thresholds": {
            "zero_orders_spend": _round2(thresholds.zero_orders_spend),
            "high_drr_percent": _round2(thresholds.high_drr_percent),
            "high_drr_min_spend": _round2(thresholds.high_drr_min_spend),
            "scale_min_orders": thresholds.scale_min_orders,
            "scale_max_drr_percent": _round2(thresholds.scale_max_drr_percent),
            "card_review_reduce_percent": _round2(thresholds.card_review_reduce_percent),
            "max_reduce_percent": _round2(thresholds.max_reduce_percent),
            "scale_low_drr_percent": _round2(thresholds.scale_low_drr_percent),
            "scale_mid_drr_percent": _round2(thresholds.scale_mid_drr_percent),
            "scale_high_drr_percent": _round2(thresholds.scale_high_drr_percent),
        },
    }
    return rows, summary


def _write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    headers = list(rows[0].keys()) if rows else ["recommended_action"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_xlsx(rows: list[dict[str, Any]], path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Ozon CPC Dry Run"
    headers = list(rows[0].keys()) if rows else ["recommended_action"]
    sheet.append(headers)
    for row in rows:
        sheet.append([row.get(header, "") for header in headers])
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for column in sheet.columns:
        max_len = max(len(str(cell.value or "")) for cell in column)
        sheet.column_dimensions[column[0].column_letter].width = min(max(max_len + 2, 12), 70)
    workbook.save(path)


def _top(rows: list[dict[str, Any]], action: str, limit: int | None = None) -> list[dict[str, Any]]:
    selected = [row for row in rows if row["recommended_action"] == action]
    if limit is None:
        return selected
    return [row for row in rows if row["recommended_action"] == action][:limit]


def _write_report(path: Path, *, result: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    summary = result["summary"]
    lines = [
        "# Ozon CPC Optimization Dry Run",
        "",
        "Mode: read-only/dry-run. No bids, budgets, campaign statuses or products changed.",
        "",
        f"Run ID: `{result['run_id']}`",
        f"Source run: `{result['source_run_id']}`",
        "",
        "## Summary",
        "",
    ]
    for key in (
        "sku_rows",
        "spend",
        "orders_money",
        "orders",
        "drr_percent",
        "roas",
        "potential_cut_spend",
        "scale_candidates",
        "current_bids_loaded",
        "current_bid_matches",
        "current_bid_missing",
        "action_rows",
        "action_rows_with_current_bid",
    ):
        lines.append(f"- `{key}`: {summary.get(key)}")

    lines.extend(["", "## Action Counts", ""])
    for action, count in sorted(summary["action_counts"].items()):
        lines.append(f"- `{action}`: {count}")

    sections = (
        ("## Pause Or Exclude", "pause_or_exclude"),
        ("## Review Card Then Reduce Or Pause", "review_card_then_reduce_or_pause"),
        ("## Reduce Bid Or Review", "reduce_bid_or_review"),
        ("## Scale Candidates", "scale_candidate"),
    )
    for title, action in sections:
        lines.extend(["", title, ""])
        selected = _top(rows, action)
        if not selected:
            lines.append("- none")
            continue
        for row in selected:
            lines.append(
                "- "
                f"`{row['sku']}` {row['title']}: spend `{row['spend']}`, "
                f"orders `{row['orders']}`, revenue `{row['orders_money']}`, "
                f"DRR `{_display_percent(row['drr_percent'])}`, action `{row['recommended_action']}`, "
                f"bid `{row['current_bid'] or row['bid_reference']}` -> `{row['target_bid']}` "
                f"({row['proposed_bid_change_percent']}%, amount `{row['bid_change_amount']}`)"
            )

    lines.extend(["", "## Artifacts", ""])
    for key, value in sorted(result["artifacts"].items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "Apply is forbidden without explicit owner approval and fresh drift-check.", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def run_ozon_cpc_optimization_plan(
    *,
    data_dir: Path = Path("data"),
    run_id: str | None = None,
    source_run_id: str | None = None,
    rows_csv: Path | None = None,
    current_bids_json: Path | None = None,
    thresholds: CpcOptimizationThresholds = CpcOptimizationThresholds(),
) -> dict[str, Any]:
    started_at = datetime.now()
    run_id = run_id or f"ozon_cpc_optimization_plan_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    processed_dir = ensure_dir(run_dir / "processed")

    source_dir = None
    if rows_csv is None:
        source_dir = _source_run_dir(data_dir, source_run_id)
        rows_csv = source_dir / "processed" / "rows.csv"
    if not rows_csv.exists():
        raise FileNotFoundError(f"Source rows CSV not found: {rows_csv}")

    source_rows = _read_source_rows(rows_csv)
    current_bids = _read_current_bids(current_bids_json)
    rows, summary = build_cpc_optimization_rows(source_rows, thresholds=thresholds, current_bids=current_bids)

    plan_csv = run_dir / "ozon_cpc_optimization_plan.csv"
    bid_changes_csv = run_dir / "ozon_cpc_bid_changes.csv"
    plan_xlsx = run_dir / "ozon_cpc_optimization_plan.xlsx"
    report_path = run_dir / "ozon_cpc_optimization_plan.md"
    rows_json = processed_dir / "calculation_rows.json"
    write_json(rows_json, rows)
    _write_csv(rows, plan_csv)
    _write_csv([row for row in rows if row["recommended_action"] != "keep_monitor"], bid_changes_csv)
    _write_xlsx(rows, plan_xlsx)

    artifacts = {
        "run_dir": str(run_dir),
        "report": str(report_path),
        "csv": str(plan_csv),
        "bid_changes_csv": str(bid_changes_csv),
        "xlsx": str(plan_xlsx),
        "calculation_rows": str(rows_json),
        "summary": str(run_dir / "summary.json"),
        "source_rows_csv": str(rows_csv),
        "current_bids_json": str(current_bids_json) if current_bids_json else "",
        "run_manifest": str(run_dir / "manifest.json"),
    }
    result = {
        "run_id": run_id,
        "source_run_id": source_run_id or (source_dir.name if source_dir else ""),
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "dry-run",
        "overall_status": "ok",
        "pending_id": f"{run_id}_pending",
        "summary": summary,
        "artifacts": artifacts,
        "apply_performed": False,
    }
    write_json(run_dir / "summary.json", result)
    _write_report(report_path, result=result, rows=rows)
    write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=result,
        task="ozon-cpc-optimization-plan",
        mode="dry_run",
        risk="normal",
        marketplaces=["ozon"],
        inputs={
            "source_run_id": source_run_id,
            "rows_csv": str(rows_csv),
            "current_bids_json": str(current_bids_json) if current_bids_json else "",
        },
    )
    return result
