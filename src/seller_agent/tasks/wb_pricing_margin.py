from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
import subprocess
from typing import Any
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.styles import Font

from seller_agent.config import AppCredentials
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.marketplaces.wb.analytics_adapter import WbAnalyticsAdapter
from seller_agent.marketplaces.wb.finance_adapter import WbFinanceAdapter
from seller_agent.marketplaces.wb.prices_adapter import WbPricesAdapter
from seller_agent.marketplaces.wb.promotion_adapter import WbPromotionAdapter
from seller_agent.marketplaces.wb.statistics_adapter import WbStatisticsAdapter
from seller_agent.reports.writer import ensure_dir, write_json
from seller_agent.tasks.marketplace_period_report import collect_wb_period_metrics
from seller_agent.tasks.ozon_pricing_margin import (
    _decimal,
    _expense_rows,
    _load_catalog,
    _money,
    _non_negative_decimal,
    _pack_qty,
    _period_snapshot,
    _positive_decimal,
    _price_ladder_row,
)


MOSCOW = ZoneInfo("Europe/Moscow")


def run_wb_pricing_margin(
    *,
    credentials: AppCredentials,
    data_dir: Path,
    unit_cost: Any,
    target_margin: Any,
    period_days: int,
    run_id: str | None = None,
    today: date | None = None,
    statistics: WbStatisticsAdapter | None = None,
    finance: WbFinanceAdapter | None = None,
    promotion: WbPromotionAdapter | None = None,
    prices: WbPricesAdapter | None = None,
    analytics: WbAnalyticsAdapter | None = None,
    action_membership: dict[int, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    cost = _positive_decimal(unit_cost, "unit_cost")
    margin = _non_negative_decimal(target_margin, "target_margin")
    if period_days not in {15, 30}:
        raise ValueError("period_days must be 15 or 30")
    if credentials.wb is None and any(adapter is None for adapter in (statistics, finance, promotion, prices, analytics)):
        raise ValueError("WB API credentials are not configured")

    current_day = today or datetime.now(MOSCOW).date()
    end = current_day - timedelta(days=1)
    start = end - timedelta(days=period_days - 1)
    started_at = datetime.now(MOSCOW)
    run_id = run_id or f"wb_pricing_margin_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.date().isoformat() / run_id)
    catalog_rows = _load_catalog(data_dir / "catalog" / "unified" / "products.csv")
    if not catalog_rows:
        raise ValueError("Unified catalog is empty or missing")

    wb_credentials = credentials.wb
    statistics = statistics or WbStatisticsAdapter(wb_credentials)  # type: ignore[arg-type]
    finance = finance or WbFinanceAdapter(wb_credentials)  # type: ignore[arg-type]
    promotion = promotion or WbPromotionAdapter(wb_credentials)  # type: ignore[arg-type]
    prices = prices or WbPricesAdapter(wb_credentials)  # type: ignore[arg-type]
    analytics = analytics or WbAnalyticsAdapter(wb_credentials)  # type: ignore[arg-type]

    selected_metrics = collect_wb_period_metrics(
        statistics=statistics,
        finance=finance,
        promotion=promotion,
        catalog_rows=catalog_rows,
        start=start,
        end=end,
    )
    period_comparison: dict[str, Any] = {}
    for days in (15, 30):
        comparison_start = end - timedelta(days=days - 1)
        metrics_for_days = selected_metrics if days == period_days else collect_wb_period_metrics(
            statistics=statistics,
            finance=finance,
            promotion=promotion,
            catalog_rows=catalog_rows,
            start=comparison_start,
            end=end,
        )
        period_comparison[str(days)] = _period_snapshot(metrics_for_days, date_from=comparison_start, date_to=end)

    warnings = list(selected_metrics.get("warnings") or [])
    price_rows = prices.fetch_goods_prices()
    stock_rows = analytics.fetch_wb_warehouse_stocks()
    if action_membership is None:
        try:
            action_membership = _collect_action_membership(data_dir=data_dir, run_dir=run_dir, day=current_day)
        except Exception as exc:  # noqa: BLE001 - action gate remains explicit and blocks apply decisions.
            action_membership = {}
            warnings.append(f"WB action gate не подтвержден через ЛК: {exc.__class__.__name__}")
            actions_confirmed = False
        else:
            actions_confirmed = True
    else:
        actions_confirmed = True

    summary_metrics = selected_metrics.get("summary") or {}
    buyout_units = int(summary_metrics.get("buyout_units") or 0)
    physical_pieces = int(summary_metrics.get("physical_pieces") or 0)
    gross = _decimal(summary_metrics.get("gross"))
    total_expenses = _decimal(summary_metrics.get("expenses"))
    expense_rows = _expense_rows(selected_metrics.get("expenses") or {}, buyout_units=buyout_units, physical_pieces=physical_pieces)
    logistics = sum(_decimal(row["total"]) for row in expense_rows if row["category"] == "Логистика")
    variable_rate = max(Decimal("0"), total_expenses - logistics) / gross if gross > 0 else Decimal("0")
    fixed_logistics = logistics / buyout_units if buyout_units else Decimal("0")
    calculation_ready = buyout_units > 0 and physical_pieces > 0 and gross > 0 and Decimal("0") < variable_rate < Decimal("1")
    if not calculation_ready:
        warnings.append("Недостаточно завершённых продаж WB для надёжного расчёта цен.")

    observed_pack = {_pack_qty(row) for row in catalog_rows if _active_wb(row)}
    ladder = [
        _price_ladder_row(
            pack_qty=pack_qty,
            unit_cost=cost,
            target_margin=margin,
            fixed_logistics=fixed_logistics,
            variable_rate=variable_rate,
        )
        for pack_qty in sorted(set(range(1, 6)).union(observed_pack))
    ] if calculation_ready else []
    ladder_by_pack = {int(row["pack_qty"]): row for row in ladder}
    products = _product_rows(
        catalog_rows=catalog_rows,
        price_rows=price_rows,
        stock_rows=stock_rows,
        ladder_by_pack=ladder_by_pack,
        action_membership=action_membership,
        actions_confirmed=actions_confirmed,
    )

    metrics = {
        "period_days": period_days,
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "buyout_units": buyout_units,
        "physical_pieces": physical_pieces,
        "gross": _money(gross),
        "total_expenses": _money(total_expenses),
        "expense_per_sold_product": _money(total_expenses / buyout_units) if buyout_units else None,
        "expense_per_physical_item": _money(total_expenses / physical_pieces) if physical_pieces else None,
        "fixed_logistics_per_sold_product": _money(fixed_logistics),
        "model_variable_rate_pct": _money(variable_rate * 100),
        "calculation_ready": calculation_ready,
        "actions_confirmed": actions_confirmed,
        "products_with_stock": sum(row["stock_gate"] == "pass" for row in products),
        "products_without_stock": sum(row["stock_gate"] == "blocked_no_stock" for row in products),
        "products_in_actions": sum(row["in_action"] is True for row in products),
    }
    artifacts = {
        "report": str(run_dir / "wb_pricing_margin.xlsx"),
        "markdown": str(run_dir / "wb_pricing_margin.md"),
        "json": str(run_dir / "wb_pricing_margin.json"),
        "csv": str(run_dir / "wb_pricing_margin_products.csv"),
    }
    payload = {
        "metrics": metrics,
        "inputs": {"unit_cost": _money(cost), "target_margin": _money(margin), "period_days": period_days},
        "period_comparison": period_comparison,
        "expenses": expense_rows,
        "price_ladder": ladder,
        "products": products,
        "warnings": warnings,
        "sources": selected_metrics.get("sources") or [],
    }
    write_json(Path(artifacts["json"]), payload)
    _write_products_csv(Path(artifacts["csv"]), products)
    _write_markdown(Path(artifacts["markdown"]), payload)
    _write_xlsx(Path(artifacts["report"]), payload)
    summary = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "overall_status": "warning" if warnings else "ok",
        "marketplace": "wb",
        "mode": "dry_run",
        **payload,
        "artifacts": artifacts,
    }
    write_json(run_dir / "summary.json", summary)
    manifest = write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=summary,
        task="wb-pricing-margin",
        mode="dry_run",
        risk="low",
        marketplaces=["wb"],
        inputs=payload["inputs"],
        lifecycle_status="pending_review",
        closed=False,
    )
    summary["artifacts"].update(manifest)
    write_json(run_dir / "summary.json", summary)
    return summary


def _active_wb(row: dict[str, str]) -> bool:
    return str(row.get("active_wb") or "").strip().lower() in {"1", "true", "yes"}


def _product_rows(
    *,
    catalog_rows: list[dict[str, str]],
    price_rows: list[dict[str, Any]],
    stock_rows: list[dict[str, Any]],
    ladder_by_pack: dict[int, dict[str, Any]],
    action_membership: dict[int, list[dict[str, Any]]],
    actions_confirmed: bool,
) -> list[dict[str, Any]]:
    price_map: dict[str, dict[str, Any]] = {}
    for row in price_rows:
        for value in (row.get("nmID"), row.get("nmId"), row.get("vendorCode")):
            if value not in (None, ""):
                price_map[str(value)] = row
    stock_map: dict[int, int] = {}
    for row in stock_rows:
        nm_id = int(row.get("nmId") or row.get("nmID") or 0)
        stock_map[nm_id] = stock_map.get(nm_id, 0) + int(row.get("quantity") or row.get("stockCount") or 0)
    result: list[dict[str, Any]] = []
    for source in catalog_rows:
        if not _active_wb(source):
            continue
        nm_id = int(source.get("wb_nm_id") or 0)
        current = price_map.get(str(nm_id)) or price_map.get(str(source.get("wb_vendor_code") or "")) or {}
        prices = current.get("prices") if isinstance(current.get("prices"), list) else []
        discounted = current.get("discountedPrices") if isinstance(current.get("discountedPrices"), list) else []
        pack_qty = _pack_qty(source)
        offers = action_membership.get(nm_id, [])
        current_participation = any(bool(row.get("currently_participates")) for row in offers)
        stock = stock_map.get(nm_id, 0)
        target = ladder_by_pack.get(pack_qty) or {}
        result.append(
            {
                "internal_sku": source.get("internal_sku") or "",
                "product_name": source.get("product_name") or "",
                "pack_qty": pack_qty,
                "wb_nm_id": nm_id,
                "wb_vendor_code": source.get("wb_vendor_code") or "",
                "current_base_price": prices[0] if prices else None,
                "current_discounted_price": discounted[0] if discounted else None,
                "current_discount_pct": current.get("discount"),
                "target_minimum_price": target.get("minimum_price"),
                "target_discounted_price": target.get("discounted_price"),
                "target_base_price": target.get("base_price"),
                "stock_qty": stock,
                "stock_gate": "pass" if stock > 0 else "blocked_no_stock",
                "in_action": current_participation if actions_confirmed else None,
                "available_action_count": len(offers) if actions_confirmed else None,
                "action_gate": "review_current_action_price" if current_participation else "pass_no_current_action" if actions_confirmed else "not_confirmed",
                "current_minimum_price": None,
                "minimum_price_source": "not_available_in_official_wb_prices_api",
            }
        )
    return result


def _collect_action_membership(*, data_dir: Path, run_dir: Path, day: date) -> dict[int, list[dict[str, Any]]]:
    from seller_agent.tasks.wb_actions_discount_plan import WbActionsSnapshotLock, _lock_path
    from scripts.actions.wb_best_price_action_plan import read_promo_rows

    raw_dir = ensure_dir(run_dir / "raw" / "actions")
    prices_dir = ensure_dir(run_dir / "raw" / "prices")
    with WbActionsSnapshotLock(_lock_path(data_dir)):
        completed = subprocess.run(
            [
                "node",
                "scripts/actions/wb_download_active_actions.js",
                "--date",
                day.isoformat(),
                "--out-dir",
                str(raw_dir),
                "--prices-dir",
                str(prices_dir),
            ],
            cwd=Path(__file__).resolve().parents[3],
            text=True,
            capture_output=True,
            timeout=300,
            check=False,
        )
    if completed.returncode != 0:
        raise RuntimeError("wb_action_snapshot_failed")
    _promos, rows = read_promo_rows(raw_dir / "cabinet-actions-snapshot.json")
    return rows


def _write_products_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(rows[0]) if rows else ["internal_sku"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_markdown(path: Path, payload: dict[str, Any]) -> None:
    metrics = payload["metrics"]
    lines = [
        "# WB: цены и маржа",
        "",
        "Режим: `dry-run`. Цены, скидки и минимальные цены WB не изменялись.",
        "",
        f"- Основной период: `{metrics['date_from']} - {metrics['date_to']}`.",
        f"- Выкупы: `{metrics['buyout_units']}` товаров / `{metrics['physical_pieces']}` изделий.",
        f"- Расходы: `{metrics['total_expenses']}` руб.",
        f"- Расход на проданный товар/комплект: `{metrics['expense_per_sold_product']}` руб.",
        f"- Средняя логистика на товар/комплект: `{metrics['fixed_logistics_per_sold_product']}` руб.",
        f"- Stock gate: с остатком `{metrics['products_with_stock']}`, без остатка `{metrics['products_without_stock']}`.",
        f"- В действующих акциях: `{metrics['products_in_actions']}`.",
        "",
        "## Ценовая сетка",
        "",
        "| Изделий | Минимальная | Со скидкой | Базовая |",
        "| ---: | ---: | ---: | ---: |",
    ]
    for row in payload["price_ladder"]:
        lines.append(f"| {row['pack_qty']} | {row['minimum_price']} | {row['discounted_price']} | {row['base_price']} |")
    if payload["warnings"]:
        lines.extend(["", "## Ограничения", "", *[f"- {item}" for item in payload["warnings"]]])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_xlsx(path: Path, payload: dict[str, Any]) -> None:
    workbook = Workbook()
    ladder = workbook.active
    ladder.title = "Ценовая сетка"
    ladder.append(["pack_qty", "minimum_price", "discounted_price", "base_price", "expected_margin_per_item"])
    for row in payload["price_ladder"]:
        ladder.append([row.get(key) for key in ("pack_qty", "minimum_price", "discounted_price", "base_price", "expected_margin_per_item")])
    for cell in ladder[1]:
        cell.font = Font(bold=True)
    products = workbook.create_sheet("Товары")
    fields = list(payload["products"][0]) if payload["products"] else ["internal_sku"]
    products.append(fields)
    for row in payload["products"]:
        products.append([row.get(field) for field in fields])
    for cell in products[1]:
        cell.font = Font(bold=True)
    workbook.save(path)
