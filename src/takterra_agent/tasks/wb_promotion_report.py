from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from openpyxl import Workbook

from takterra_agent.config import AppCredentials
from takterra_agent.marketplaces.wb.promotion_adapter import WbPromotionAdapter
from takterra_agent.reports.writer import ensure_dir, write_json
from takterra_agent.tasks.status_preflight import run_status_preflight


STATUS_NAMES = {
    -1: "deleted",
    4: "ready",
    7: "completed",
    8: "declined",
    9: "active",
    11: "paused",
}

TYPE_NAMES = {
    4: "catalog",
    5: "card",
    6: "search",
    7: "recommendations",
    8: "auto",
    9: "auction",
}


@dataclass
class WbPromotionTotals:
    views: int = 0
    clicks: int = 0
    atbs: int = 0
    orders: int = 0
    canceled: int = 0
    shks: int = 0
    spend: Decimal = Decimal("0")
    revenue: Decimal = Decimal("0")


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


def _ratio(part: Decimal, total: Decimal) -> Decimal | None:
    if total == 0:
        return None
    return (part / total).quantize(Decimal("0.01"))


def _percent(part: Decimal | int, total: Decimal | int) -> Decimal | None:
    if total == 0:
        return None
    return (Decimal(part) / Decimal(total) * Decimal("100")).quantize(Decimal("0.01"))


def _default_period() -> tuple[str, str]:
    today = date.today()
    return (today - timedelta(days=14)).isoformat(), today.isoformat()


def _flatten_campaign_ids(count_data: dict[str, Any]) -> tuple[list[int], dict[int, dict[str, Any]]]:
    ids: list[int] = []
    meta: dict[int, dict[str, Any]] = {}
    for group in count_data.get("adverts") or []:
        campaign_type = _int(group.get("type"))
        status = _int(group.get("status"))
        for item in group.get("advert_list") or []:
            advert_id = _int(item.get("advertId"))
            if not advert_id:
                continue
            ids.append(advert_id)
            meta[advert_id] = {
                "type": campaign_type,
                "type_name": TYPE_NAMES.get(campaign_type, str(campaign_type)),
                "status": status,
                "status_name": STATUS_NAMES.get(status, str(status)),
                "change_time": str(item.get("changeTime") or ""),
            }
    return sorted(set(ids)), meta


def _campaign_bid_summary(campaign: dict[str, Any]) -> dict[str, Any]:
    search_values: list[Decimal] = []
    recommendation_values: list[Decimal] = []
    nm_settings = campaign.get("nm_settings") or []
    for nm in nm_settings:
        bids = nm.get("bids_kopecks") or {}
        search = _decimal(bids.get("search"))
        recommendation = _decimal(bids.get("recommendations"))
        if search > 0:
            search_values.append(search / Decimal("100"))
        if recommendation > 0:
            recommendation_values.append(recommendation / Decimal("100"))
    return {
        "nm_count": len(nm_settings),
        "search_bid_min": _round2(min(search_values) if search_values else None),
        "search_bid_max": _round2(max(search_values) if search_values else None),
        "recommendation_bid_min": _round2(min(recommendation_values) if recommendation_values else None),
        "recommendation_bid_max": _round2(max(recommendation_values) if recommendation_values else None),
    }


def _totals_from_stat(row: dict[str, Any]) -> WbPromotionTotals:
    return WbPromotionTotals(
        views=_int(row.get("views")),
        clicks=_int(row.get("clicks")),
        atbs=_int(row.get("atbs")),
        orders=_int(row.get("orders")),
        canceled=_int(row.get("canceled")),
        shks=_int(row.get("shks")),
        spend=_decimal(row.get("sum")),
        revenue=_decimal(row.get("sum_price")),
    )


def _row_metrics(totals: WbPromotionTotals) -> dict[str, str]:
    return {
        "avg_cpc": _round2(_ratio(totals.spend, Decimal(totals.clicks))),
        "ctr_percent": _round2(_percent(totals.clicks, totals.views)),
        "cart_rate_percent": _round2(_percent(totals.atbs, totals.clicks)),
        "order_cr_percent": _round2(_percent(totals.orders, totals.clicks)),
        "drr_percent": _round2(_percent(totals.spend, totals.revenue)),
        "roas": _round2(_ratio(totals.revenue, totals.spend)),
    }


def _nm_rows_from_stats(stats_rows: list[dict[str, Any]], campaign_by_id: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    rows_by_key: dict[tuple[int, int], dict[str, Any]] = {}
    for campaign_stat in stats_rows:
        advert_id = _int(campaign_stat.get("advertId"))
        campaign = campaign_by_id.get(advert_id, {})
        for day in campaign_stat.get("days") or []:
            for app in day.get("apps") or []:
                for nm in app.get("nms") or []:
                    nm_id = _int(nm.get("nmId"))
                    if not nm_id:
                        continue
                    key = (advert_id, nm_id)
                    row = rows_by_key.setdefault(
                        key,
                        {
                            "marketplace": "WB",
                            "advert_id": advert_id,
                            "campaign_name": campaign.get("name", ""),
                            "status": campaign.get("status_name", ""),
                            "type": campaign.get("type_name", ""),
                            "payment_type": campaign.get("payment_type", ""),
                            "bid_type": campaign.get("bid_type", ""),
                            "nm_id": nm_id,
                            "name": str(nm.get("name") or ""),
                            "views": 0,
                            "clicks": 0,
                            "atbs": 0,
                            "orders": 0,
                            "canceled": 0,
                            "shks": 0,
                            "spend": Decimal("0"),
                            "revenue": Decimal("0"),
                        },
                    )
                    row["name"] = row["name"] or str(nm.get("name") or "")
                    row["views"] += _int(nm.get("views"))
                    row["clicks"] += _int(nm.get("clicks"))
                    row["atbs"] += _int(nm.get("atbs"))
                    row["orders"] += _int(nm.get("orders"))
                    row["canceled"] += _int(nm.get("canceled"))
                    row["shks"] += _int(nm.get("shks"))
                    row["spend"] += _decimal(nm.get("sum"))
                    row["revenue"] += _decimal(nm.get("sum_price"))

    output: list[dict[str, Any]] = []
    for row in rows_by_key.values():
        totals = WbPromotionTotals(
            views=row["views"],
            clicks=row["clicks"],
            atbs=row["atbs"],
            orders=row["orders"],
            canceled=row["canceled"],
            shks=row["shks"],
            spend=row["spend"],
            revenue=row["revenue"],
        )
        row = dict(row)
        row["spend"] = _round2(totals.spend)
        row["revenue"] = _round2(totals.revenue)
        row.update(_row_metrics(totals))
        output.append(row)
    return sorted(output, key=lambda item: (-_decimal(item["spend"]), str(item["advert_id"]), str(item["nm_id"])))


def build_wb_promotion_rows(
    *,
    count_data: dict[str, Any],
    campaigns: list[dict[str, Any]],
    stats_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    campaign_ids, count_meta = _flatten_campaign_ids(count_data)
    stats_by_id = {_int(row.get("advertId")): row for row in stats_rows}
    campaign_by_id: dict[int, dict[str, Any]] = {}
    for campaign in campaigns:
        advert_id = _int(campaign.get("id"))
        meta = count_meta.get(advert_id, {})
        settings = campaign.get("settings") or {}
        row = {
            "advert_id": advert_id,
            "name": str(settings.get("name") or ""),
            "status": _int(campaign.get("status")) or meta.get("status", ""),
            "status_name": STATUS_NAMES.get(_int(campaign.get("status")), meta.get("status_name", "")),
            "type": meta.get("type", ""),
            "type_name": meta.get("type_name", ""),
            "payment_type": str(settings.get("payment_type") or ""),
            "bid_type": str(campaign.get("bid_type") or ""),
            "change_time": meta.get("change_time", ""),
        }
        row.update(_campaign_bid_summary(campaign))
        campaign_by_id[advert_id] = row

    campaign_rows: list[dict[str, Any]] = []
    totals = WbPromotionTotals()
    campaigns_with_stats = 0
    for advert_id in campaign_ids:
        campaign = campaign_by_id.get(advert_id) or {
            "advert_id": advert_id,
            "name": "",
            "status": count_meta.get(advert_id, {}).get("status", ""),
            "status_name": count_meta.get(advert_id, {}).get("status_name", ""),
            "type": count_meta.get(advert_id, {}).get("type", ""),
            "type_name": count_meta.get(advert_id, {}).get("type_name", ""),
            "payment_type": "",
            "bid_type": "",
            "change_time": count_meta.get(advert_id, {}).get("change_time", ""),
            "nm_count": "",
            "search_bid_min": "",
            "search_bid_max": "",
            "recommendation_bid_min": "",
            "recommendation_bid_max": "",
        }
        stat = stats_by_id.get(advert_id, {})
        row_totals = _totals_from_stat(stat)
        if stat:
            campaigns_with_stats += 1
        totals.views += row_totals.views
        totals.clicks += row_totals.clicks
        totals.atbs += row_totals.atbs
        totals.orders += row_totals.orders
        totals.canceled += row_totals.canceled
        totals.shks += row_totals.shks
        totals.spend += row_totals.spend
        totals.revenue += row_totals.revenue
        output = dict(campaign)
        output.update(
            {
                "views": row_totals.views,
                "clicks": row_totals.clicks,
                "atbs": row_totals.atbs,
                "orders": row_totals.orders,
                "canceled": row_totals.canceled,
                "shks": row_totals.shks,
                "spend": _round2(row_totals.spend),
                "revenue": _round2(row_totals.revenue),
            }
        )
        output.update(_row_metrics(row_totals))
        campaign_rows.append(output)

    campaign_rows.sort(key=lambda row: (-_decimal(row.get("spend")), str(row.get("advert_id"))))
    nm_rows = _nm_rows_from_stats(stats_rows, campaign_by_id)
    summary = {
        "campaigns_total": len(campaign_ids),
        "campaigns_info_loaded": len(campaigns),
        "campaigns_with_stats": campaigns_with_stats,
        "nm_rows": len(nm_rows),
        "views": totals.views,
        "clicks": totals.clicks,
        "atbs": totals.atbs,
        "orders": totals.orders,
        "canceled": totals.canceled,
        "shks": totals.shks,
        "spend": _round2(totals.spend),
        "revenue": _round2(totals.revenue),
    }
    summary.update(_row_metrics(totals))
    return campaign_rows, nm_rows, summary


def _write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    headers = list(rows[0].keys()) if rows else ["empty"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_xlsx(campaign_rows: list[dict[str, Any]], nm_rows: list[dict[str, Any]], path: Path) -> None:
    workbook = Workbook()
    for sheet_index, (title, rows) in enumerate((("Campaigns", campaign_rows), ("Products", nm_rows))):
        sheet = workbook.active if sheet_index == 0 else workbook.create_sheet()
        sheet.title = title
        headers = list(rows[0].keys()) if rows else ["empty"]
        sheet.append(headers)
        for row in rows:
            sheet.append([row.get(header, "") for header in headers])
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for column in sheet.columns:
            max_len = max(len(str(cell.value or "")) for cell in column)
            sheet.column_dimensions[column[0].column_letter].width = min(max(max_len + 2, 12), 70)
    workbook.save(path)


def _write_report(path: Path, *, result: dict[str, Any], campaign_rows: list[dict[str, Any]], nm_rows: list[dict[str, Any]]) -> None:
    summary = result["summary"]
    lines = [
        "# WB Promotion Read-only Report",
        "",
        "Mode: read-only. No bids, budgets, campaign statuses or product sets changed.",
        "",
        f"Run ID: `{result['run_id']}`",
        f"Period: `{result['period']['date_from']}` - `{result['period']['date_to']}`",
        "Source: WB Promotion API",
        "",
        "## Summary",
        "",
    ]
    for key in (
        "campaigns_total",
        "campaigns_info_loaded",
        "campaigns_with_stats",
        "nm_rows",
        "spend",
        "views",
        "clicks",
        "avg_cpc",
        "atbs",
        "orders",
        "revenue",
        "drr_percent",
        "roas",
    ):
        lines.append(f"- `{key}`: {summary.get(key)}")

    lines.extend(["", "## Top Campaigns By Spend", ""])
    for row in campaign_rows[:10]:
        lines.append(
            "- "
            f"`{row['advert_id']}` {row.get('name') or '(no name)'}: status `{row.get('status_name')}`, "
            f"payment `{row.get('payment_type')}`, spend `{row.get('spend')}`, orders `{row.get('orders')}`, "
            f"revenue `{row.get('revenue')}`, DRR `{row.get('drr_percent') or 'n/a'}%`, "
            f"ROAS `{row.get('roas') or 'n/a'}`"
        )

    lines.extend(["", "## Products With Spend And No Orders", ""])
    problem_rows = [row for row in nm_rows if _decimal(row.get("spend")) > 0 and _int(row.get("orders")) == 0]
    for row in problem_rows[:15]:
        lines.append(
            "- "
            f"`{row['nm_id']}` {row.get('name')}: campaign `{row.get('advert_id')}`, "
            f"spend `{row.get('spend')}`, clicks `{row.get('clicks')}`, atbs `{row.get('atbs')}`"
        )
    if not problem_rows:
        lines.append("- none")

    lines.extend(["", "## Top Products By Orders", ""])
    top_orders = sorted(nm_rows, key=lambda row: (-_int(row.get("orders")), -_decimal(row.get("revenue"))))
    for row in top_orders[:15]:
        if _int(row.get("orders")) <= 0:
            continue
        lines.append(
            "- "
            f"`{row['nm_id']}` {row.get('name')}: campaign `{row.get('advert_id')}`, "
            f"orders `{row.get('orders')}`, revenue `{row.get('revenue')}`, spend `{row.get('spend')}`, "
            f"DRR `{row.get('drr_percent') or 'n/a'}%`"
        )

    lines.extend(["", "## Artifacts", ""])
    for key, value in sorted(result["artifacts"].items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def run_wb_promotion_report(
    *,
    credentials: AppCredentials,
    data_dir: Path = Path("data"),
    run_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    payment_type: str | None = None,
) -> dict[str, Any]:
    if not credentials.wb:
        raise RuntimeError("missing WB API token")

    if not date_from or not date_to:
        default_from, default_to = _default_period()
        date_from = date_from or default_from
        date_to = date_to or default_to

    started_at = datetime.now()
    run_id = run_id or f"wb_promotion_report_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    raw_dir = ensure_dir(run_dir / "raw")
    processed_dir = ensure_dir(run_dir / "processed")

    preflight = run_status_preflight(credentials=credentials, data_dir=data_dir, include_lk=False)
    wb = WbPromotionAdapter(credentials.wb)

    count_data = wb.fetch_campaign_count()
    campaign_ids, _ = _flatten_campaign_ids(count_data)
    campaigns = wb.fetch_campaigns(
        ids=campaign_ids,
        statuses=[7, 9, 11],
        payment_type=payment_type,
    ) if campaign_ids else []
    stats_rows = wb.fetch_fullstats(ids=campaign_ids, date_from=date_from, date_to=date_to) if campaign_ids else []
    try:
        balance = wb.fetch_balance()
    except Exception as exc:  # noqa: BLE001
        balance = {"status": "error", "error": str(exc)}

    write_json(raw_dir / "campaign_count.json", count_data)
    write_json(raw_dir / "campaigns.json", {"adverts": campaigns})
    write_json(raw_dir / "fullstats.json", stats_rows)
    write_json(raw_dir / "balance.json", balance)

    campaign_rows, nm_rows, summary = build_wb_promotion_rows(
        count_data=count_data,
        campaigns=campaigns,
        stats_rows=stats_rows,
    )
    campaign_csv = run_dir / "wb_promotion_campaigns.csv"
    nm_csv = run_dir / "wb_promotion_products.csv"
    xlsx = run_dir / "wb_promotion_report.xlsx"
    report = run_dir / "wb_promotion_report.md"
    _write_csv(campaign_rows, campaign_csv)
    _write_csv(nm_rows, nm_csv)
    _write_xlsx(campaign_rows, nm_rows, xlsx)

    artifacts = {
        "run_dir": str(run_dir),
        "summary": str(run_dir / "summary.json"),
        "report": str(report),
        "campaigns_csv": str(campaign_csv),
        "products_csv": str(nm_csv),
        "xlsx": str(xlsx),
        "campaign_count_raw": str(raw_dir / "campaign_count.json"),
        "campaigns_raw": str(raw_dir / "campaigns.json"),
        "fullstats_raw": str(raw_dir / "fullstats.json"),
        "balance_raw": str(raw_dir / "balance.json"),
    }
    result = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "read_only",
        "source": "WB Promotion API",
        "period": {"date_from": date_from, "date_to": date_to},
        "preflight": {
            "run_id": preflight["run_id"],
            "overall_status": preflight["overall_status"],
            "artifacts": preflight["artifacts"],
        },
        "summary": summary,
        "balance": balance,
        "artifacts": artifacts,
        "apply_performed": False,
    }
    write_json(run_dir / "summary.json", result)
    _write_report(report, result=result, campaign_rows=campaign_rows, nm_rows=nm_rows)
    return result
