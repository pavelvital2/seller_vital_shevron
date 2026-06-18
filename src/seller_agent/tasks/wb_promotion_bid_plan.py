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
class WbPromotionBidThresholds:
    zero_orders_spend: Decimal = Decimal("10")
    high_drr_percent: Decimal = Decimal("5")
    high_drr_min_spend: Decimal = Decimal("30")
    scale_min_orders: int = 3
    scale_max_drr_percent: Decimal = Decimal("1")
    card_review_reduce_percent: Decimal = Decimal("20")
    zero_no_cart_reduce_percent: Decimal = Decimal("30")
    max_reduce_percent: Decimal = Decimal("30")
    scale_low_drr_percent: Decimal = Decimal("20")
    scale_mid_drr_percent: Decimal = Decimal("15")
    scale_high_drr_percent: Decimal = Decimal("10")
    min_bid: Decimal = Decimal("1.00")

    def __post_init__(self) -> None:
        object.__setattr__(self, "zero_orders_spend", _decimal(self.zero_orders_spend))
        object.__setattr__(self, "high_drr_percent", _decimal(self.high_drr_percent))
        object.__setattr__(self, "high_drr_min_spend", _decimal(self.high_drr_min_spend))
        object.__setattr__(self, "scale_min_orders", int(self.scale_min_orders))
        object.__setattr__(self, "scale_max_drr_percent", _decimal(self.scale_max_drr_percent))
        object.__setattr__(self, "card_review_reduce_percent", _decimal(self.card_review_reduce_percent))
        object.__setattr__(self, "zero_no_cart_reduce_percent", _decimal(self.zero_no_cart_reduce_percent))
        object.__setattr__(self, "max_reduce_percent", _decimal(self.max_reduce_percent))
        object.__setattr__(self, "scale_low_drr_percent", _decimal(self.scale_low_drr_percent))
        object.__setattr__(self, "scale_mid_drr_percent", _decimal(self.scale_mid_drr_percent))
        object.__setattr__(self, "scale_high_drr_percent", _decimal(self.scale_high_drr_percent))
        object.__setattr__(self, "min_bid", _decimal(self.min_bid))


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


def _percent(part: int | Decimal, total: int | Decimal) -> Decimal | None:
    if total == 0:
        return None
    return (Decimal(part) / Decimal(total) * Decimal("100")).quantize(Decimal("0.01"))


def _latest_wb_promotion_report_dir(data_dir: Path) -> Path:
    run_root = data_dir / "runs"
    candidates = sorted(run_root.glob("*/wb_promotion_report_*"))
    for candidate in reversed(candidates):
        if (candidate / "wb_promotion_products.csv").exists() and (candidate / "raw" / "campaigns.json").exists():
            return candidate
    raise FileNotFoundError("No wb_promotion_report_* run with products CSV and raw/campaigns.json found")


def _source_run_dir(data_dir: Path, source_run_id: str | None) -> Path:
    if source_run_id:
        matches = sorted((data_dir / "runs").glob(f"*/{source_run_id}"))
        if not matches:
            raise FileNotFoundError(f"Source run not found: {source_run_id}")
        return matches[-1]
    return _latest_wb_promotion_report_dir(data_dir)


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [row for row in csv.DictReader(handle)]


def _read_campaigns(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    adverts = data.get("adverts") if isinstance(data, dict) else []
    return [row for row in adverts or [] if isinstance(row, dict)]


def _current_bid_rows(campaigns: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for campaign in campaigns:
        advert_id = str(campaign.get("id") or "").strip()
        settings = campaign.get("settings") or {}
        placements = settings.get("placements") or {}
        active_places = [place for place in ("search", "recommendations") if placements.get(place)]
        bid_place = active_places[0] if active_places else "search"
        for nm in campaign.get("nm_settings") or []:
            nm_id = str(nm.get("nm_id") or nm.get("nmId") or "").strip()
            if not advert_id or not nm_id:
                continue
            bids = nm.get("bids_kopecks") or {}
            bid_kopecks = _decimal(bids.get(bid_place))
            result[(advert_id, nm_id)] = {
                "current_bid": (bid_kopecks / Decimal("100")).quantize(Decimal("0.01")) if bid_kopecks else None,
                "current_bid_place": bid_place,
                "current_bid_source": "current_bid_api",
                "subject_id": str((nm.get("subject") or {}).get("id") or ""),
                "subject_name": str((nm.get("subject") or {}).get("name") or ""),
            }
    return result


def _scale_change_percent(drr: Decimal | None, thresholds: WbPromotionBidThresholds) -> Decimal:
    if drr is not None and drr <= Decimal("0.50"):
        return thresholds.scale_low_drr_percent
    if drr is not None and drr <= Decimal("0.75"):
        return thresholds.scale_mid_drr_percent
    return thresholds.scale_high_drr_percent


def _reduce_change_percent(drr: Decimal | None, thresholds: WbPromotionBidThresholds) -> Decimal:
    if drr is None or drr <= 0:
        return -thresholds.max_reduce_percent
    needed = (Decimal("1") - (thresholds.high_drr_percent / drr)) * Decimal("100")
    needed = max(Decimal("10"), needed)
    needed = min(thresholds.max_reduce_percent, needed)
    return -needed.quantize(Decimal("0.01"))


def _target_bid(
    current_bid: Decimal | None,
    change_percent: Decimal,
    thresholds: WbPromotionBidThresholds,
) -> tuple[Decimal | None, str]:
    if current_bid is None:
        return None, "missing_current_bid"
    target = current_bid * (Decimal("1") + change_percent / Decimal("100"))
    target = target.quantize(Decimal("0.01"))
    if target < thresholds.min_bid:
        return thresholds.min_bid, "min_bid_floor"
    return target, ""


def _actual_change_percent(current_bid: Decimal | None, target_bid: Decimal | None) -> Decimal | None:
    if current_bid is None or current_bid == 0 or target_bid is None:
        return None
    return ((target_bid - current_bid) / current_bid * Decimal("100")).quantize(Decimal("0.01"))


def build_wb_promotion_bid_plan_rows(
    product_rows: list[dict[str, Any]],
    *,
    campaigns: list[dict[str, Any]],
    thresholds: WbPromotionBidThresholds = WbPromotionBidThresholds(),
    active_cpc_only: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    bid_rows = _current_bid_rows(campaigns)
    rows: list[dict[str, Any]] = []
    action_counts: dict[str, int] = {}
    current_bid_matches = 0
    current_bid_sum = Decimal("0")
    target_bid_sum = Decimal("0")
    bid_change_sum = Decimal("0")
    action_bid_change_sum = Decimal("0")

    for source in product_rows:
        status = str(source.get("status") or "").strip()
        payment_type = str(source.get("payment_type") or "").strip()
        if active_cpc_only and (status != "active" or payment_type != "cpc"):
            continue

        advert_id = str(source.get("advert_id") or "").strip()
        nm_id = str(source.get("nm_id") or "").strip()
        bid = bid_rows.get((advert_id, nm_id), {})
        current_bid = bid.get("current_bid")
        if current_bid is not None:
            current_bid_matches += 1

        spend = _decimal(source.get("spend"))
        revenue = _decimal(source.get("revenue"))
        orders = _int(source.get("orders"))
        atbs = _int(source.get("atbs"))
        drr = _percent(spend, revenue)

        if orders == 0 and spend >= thresholds.zero_orders_spend:
            if atbs > 0:
                action = "review_card_then_reduce"
                reason = "расход без заказов, но есть добавления в корзину"
                priority = 20
                requested_change = -thresholds.card_review_reduce_percent
            else:
                action = "reduce_bid_or_pause"
                reason = "расход без заказов и без корзин"
                priority = 10
                requested_change = -thresholds.zero_no_cart_reduce_percent
        elif (
            orders > 0
            and spend >= thresholds.high_drr_min_spend
            and drr is not None
            and drr > thresholds.high_drr_percent
        ):
            action = "reduce_bid_or_review"
            reason = f"ДРР > {thresholds.high_drr_percent}% при расходе >= {thresholds.high_drr_min_spend}"
            priority = 30
            requested_change = _reduce_change_percent(drr, thresholds)
        elif orders >= thresholds.scale_min_orders and drr is not None and drr <= thresholds.scale_max_drr_percent:
            action = "scale_candidate"
            reason = f"заказы >= {thresholds.scale_min_orders}, ДРР <= {thresholds.scale_max_drr_percent}%"
            priority = 80
            requested_change = _scale_change_percent(drr, thresholds)
        else:
            action = "keep_monitor"
            reason = "нет явного сигнала для изменения"
            priority = 100
            requested_change = Decimal("0")

        target_bid, note = _target_bid(current_bid, requested_change, thresholds)
        bid_change_amount = target_bid - current_bid if target_bid is not None and current_bid is not None else None
        actual_change = _actual_change_percent(current_bid, target_bid)

        action_counts[action] = action_counts.get(action, 0) + 1
        if current_bid is not None:
            current_bid_sum += current_bid
        if target_bid is not None:
            target_bid_sum += target_bid
        if bid_change_amount is not None:
            bid_change_sum += bid_change_amount
            if action != "keep_monitor":
                action_bid_change_sum += bid_change_amount

        rows.append(
            {
                "marketplace": "WB",
                "mode": "dry-run",
                "advert_id": advert_id,
                "campaign_name": source.get("campaign_name") or "",
                "status": status,
                "type": source.get("type") or "",
                "payment_type": payment_type,
                "bid_type": source.get("bid_type") or "",
                "nm_id": nm_id,
                "name": source.get("name") or "",
                "recommended_action": action,
                "reason": reason,
                "current_bid": _round2(current_bid),
                "current_bid_place": bid.get("current_bid_place") or "",
                "current_bid_source": bid.get("current_bid_source") or "missing_current_bid",
                "requested_bid_change_percent": _round2(requested_change),
                "target_bid": _round2(target_bid),
                "bid_change_amount": _round2(bid_change_amount),
                "actual_bid_change_percent": _round2(actual_change),
                "target_adjustment_note": note,
                "priority": priority,
                "views": _int(source.get("views")),
                "clicks": _int(source.get("clicks")),
                "atbs": atbs,
                "orders": orders,
                "canceled": _int(source.get("canceled")),
                "shks": _int(source.get("shks")),
                "spend": _round2(spend),
                "revenue": _round2(revenue),
                "avg_cpc": source.get("avg_cpc") or "",
                "ctr_percent": source.get("ctr_percent") or "",
                "cart_rate_percent": source.get("cart_rate_percent") or "",
                "order_cr_percent": source.get("order_cr_percent") or "",
                "drr_percent": _round2(drr),
                "roas": source.get("roas") or "",
                "subject_id": bid.get("subject_id") or "",
                "subject_name": bid.get("subject_name") or "",
                "apply_allowed": False,
            }
        )

    rows.sort(key=lambda row: (int(row["priority"]), -_decimal(row["spend"]), row["advert_id"], row["nm_id"]))
    action_rows = [row for row in rows if row["recommended_action"] != "keep_monitor"]
    changed_rows = [row for row in action_rows if _decimal(row["bid_change_amount"]) != 0]
    summary = {
        "source_rows": len(product_rows),
        "eligible_rows": len(rows),
        "action_rows": len(action_rows),
        "changed_rows": len(changed_rows),
        "current_bids_loaded": len(bid_rows),
        "current_bid_matches": current_bid_matches,
        "current_bid_missing": len(rows) - current_bid_matches,
        "current_bid_sum": _round2(current_bid_sum),
        "target_bid_sum": _round2(target_bid_sum),
        "bid_change_sum": _round2(bid_change_sum),
        "action_bid_change_sum": _round2(action_bid_change_sum),
        "action_counts": action_counts,
        "thresholds": {
            "zero_orders_spend": _round2(thresholds.zero_orders_spend),
            "high_drr_percent": _round2(thresholds.high_drr_percent),
            "high_drr_min_spend": _round2(thresholds.high_drr_min_spend),
            "scale_min_orders": thresholds.scale_min_orders,
            "scale_max_drr_percent": _round2(thresholds.scale_max_drr_percent),
            "card_review_reduce_percent": _round2(thresholds.card_review_reduce_percent),
            "zero_no_cart_reduce_percent": _round2(thresholds.zero_no_cart_reduce_percent),
            "max_reduce_percent": _round2(thresholds.max_reduce_percent),
            "scale_low_drr_percent": _round2(thresholds.scale_low_drr_percent),
            "scale_mid_drr_percent": _round2(thresholds.scale_mid_drr_percent),
            "scale_high_drr_percent": _round2(thresholds.scale_high_drr_percent),
            "min_bid": _round2(thresholds.min_bid),
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
    sheet.title = "WB Promotion Bid Dry Run"
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


def _display_percent(value: Any) -> str:
    return f"{value}%" if value not in (None, "") else "n/a"


def _write_report(path: Path, *, result: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    summary = result["summary"]
    lines = [
        "# WB Promotion Bid Optimization Dry Run",
        "",
        "Mode: read-only/dry-run. No bids, budgets, campaign statuses or products changed.",
        "",
        f"Run ID: `{result['run_id']}`",
        f"Source run: `{result['source_run_id']}`",
        f"Active CPC only: `{result['active_cpc_only']}`",
        "",
        "## Summary",
        "",
    ]
    for key in (
        "eligible_rows",
        "action_rows",
        "changed_rows",
        "current_bids_loaded",
        "current_bid_matches",
        "current_bid_missing",
        "current_bid_sum",
        "target_bid_sum",
        "bid_change_sum",
        "action_bid_change_sum",
    ):
        lines.append(f"- `{key}`: {summary.get(key)}")

    lines.extend(["", "## Action Counts", ""])
    for action, count in sorted(summary["action_counts"].items()):
        lines.append(f"- `{action}`: {count}")

    sections = (
        ("## Reduce Bid Or Pause", "reduce_bid_or_pause"),
        ("## Review Card Then Reduce", "review_card_then_reduce"),
        ("## Reduce Bid Or Review", "reduce_bid_or_review"),
        ("## Scale Candidates", "scale_candidate"),
    )
    for title, action in sections:
        lines.extend(["", title, ""])
        selected = [row for row in rows if row["recommended_action"] == action]
        if not selected:
            lines.append("- none")
            continue
        for row in selected:
            lines.append(
                "- "
                f"`{row['nm_id']}` {row['name']}: campaign `{row['advert_id']}`, "
                f"spend `{row['spend']}`, orders `{row['orders']}`, revenue `{row['revenue']}`, "
                f"DRR `{_display_percent(row['drr_percent'])}`, action `{row['recommended_action']}`, "
                f"bid `{row['current_bid']}` -> `{row['target_bid']}` "
                f"(amount `{row['bid_change_amount']}`, actual `{_display_percent(row['actual_bid_change_percent'])}`)"
            )

    lines.extend(["", "## Artifacts", ""])
    for key, value in sorted(result["artifacts"].items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "Apply is forbidden without explicit owner approval and fresh drift-check.", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def run_wb_promotion_bid_plan(
    *,
    data_dir: Path = Path("data"),
    run_id: str | None = None,
    source_run_id: str | None = None,
    products_csv: Path | None = None,
    campaigns_json: Path | None = None,
    thresholds: WbPromotionBidThresholds = WbPromotionBidThresholds(),
    active_cpc_only: bool = True,
) -> dict[str, Any]:
    started_at = datetime.now()
    run_id = run_id or f"wb_promotion_bid_plan_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    processed_dir = ensure_dir(run_dir / "processed")

    source_dir = None
    if products_csv is None or campaigns_json is None:
        source_dir = _source_run_dir(data_dir, source_run_id)
        products_csv = products_csv or (source_dir / "wb_promotion_products.csv")
        campaigns_json = campaigns_json or (source_dir / "raw" / "campaigns.json")
    if not products_csv.exists():
        raise FileNotFoundError(f"Products CSV not found: {products_csv}")
    if not campaigns_json.exists():
        raise FileNotFoundError(f"Campaigns JSON not found: {campaigns_json}")

    product_rows = _read_csv(products_csv)
    campaigns = _read_campaigns(campaigns_json)
    rows, summary = build_wb_promotion_bid_plan_rows(
        product_rows,
        campaigns=campaigns,
        thresholds=thresholds,
        active_cpc_only=active_cpc_only,
    )

    plan_csv = run_dir / "wb_promotion_bid_plan.csv"
    bid_changes_csv = run_dir / "wb_promotion_bid_changes.csv"
    xlsx = run_dir / "wb_promotion_bid_plan.xlsx"
    report = run_dir / "wb_promotion_bid_plan.md"
    rows_json = processed_dir / "calculation_rows.json"
    write_json(rows_json, rows)
    _write_csv(rows, plan_csv)
    _write_csv([row for row in rows if row["recommended_action"] != "keep_monitor"], bid_changes_csv)
    _write_xlsx(rows, xlsx)

    artifacts = {
        "run_dir": str(run_dir),
        "report": str(report),
        "csv": str(plan_csv),
        "bid_changes_csv": str(bid_changes_csv),
        "xlsx": str(xlsx),
        "calculation_rows": str(rows_json),
        "summary": str(run_dir / "summary.json"),
        "source_products_csv": str(products_csv),
        "source_campaigns_json": str(campaigns_json),
        "run_manifest": str(run_dir / "manifest.json"),
    }
    result = {
        "run_id": run_id,
        "source_run_id": source_run_id or (source_dir.name if source_dir else ""),
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "dry-run",
        "overall_status": "ok",
        "pending_id": f"{run_id}_pending",
        "active_cpc_only": active_cpc_only,
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
        task="wb-promotion-bid-plan",
        mode="dry_run",
        risk="normal",
        marketplaces=["wb"],
        inputs={
            "source_run_id": source_run_id,
            "products_csv": str(products_csv),
            "campaigns_json": str(campaigns_json),
            "active_cpc_only": active_cpc_only,
        },
    )
    return result
