#!/usr/bin/env python3
from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
from html import escape
import importlib.util
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from seller_agent.config import load_credentials
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.marketplaces.ozon.adapter import OzonSellerAdapter
from seller_agent.marketplaces.ozon.performance_adapter import OzonPerformanceAdapter
from seller_agent.reports.writer import ensure_dir, write_json
from seller_agent.safety.approvals import canonical_checksum
from seller_agent.tasks.pricing_status import normalize_ozon_price_item


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
MOSCOW = ZoneInfo("Europe/Moscow")
CAMPAIGN_ID = "20233460"
ELASTIC_CSV = DATA_DIR / "runs/2026-07-18/ozon_elastic_plan_20260718T193451/ozon_elastic_dry_run.csv"
CPC_CSV = DATA_DIR / "runs/2026-07-19/ozon_cpc_efficiency_20260719T083057/processed/by_sku_30d.csv"
PORTFOLIO_CSV = DATA_DIR / "runs/2026-07-19/ozon_non_elastic_parser_review_20260719T081214/ozon_non_elastic_products.csv"
CATALOG_CSV = DATA_DIR / "catalog/unified/products.csv"
PRICE_GRID = {
    1: (Decimal("530"), Decimal("650"), Decimal("1300")),
    2: (Decimal("860"), Decimal("1100"), Decimal("2200")),
    3: (Decimal("1180"), Decimal("1450"), Decimal("2900")),
    4: (Decimal("1500"), Decimal("1800"), Decimal("3600")),
    5: (Decimal("1820"), Decimal("2200"), Decimal("4400")),
}
KNOWN_PACK_QTY = {"pzol0006": 1}


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = list(rows[0]) if rows else ["offer_id"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def _decimal(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value).replace(" ", "").replace("\u00a0", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _integer(value: Any) -> int:
    return int(_decimal(value))


def _money(value: Any) -> str:
    return str(_decimal(value).quantize(Decimal("0.01")))


def growth_target_bid(current_bid: Any) -> Decimal:
    current = _decimal(current_bid)
    return min(Decimal("6"), max(current * Decimal("1.5"), current + Decimal("1"))).quantize(
        Decimal("0.1")
    )


def reduce_target_bid(sku: str, current_bid: Any) -> Decimal:
    current = _decimal(current_bid)
    if sku == "2400299708":
        return Decimal("1.0")
    return max(Decimal("1.0"), current * Decimal("0.5")).quantize(Decimal("0.1"))


def price_targets(pack_qty: int, current_price: Any, current_old_price: Any) -> tuple[Decimal, Decimal, Decimal]:
    target_min, grid_price, grid_old = PRICE_GRID[pack_qty]
    return target_min, max(_decimal(current_price), grid_price), max(_decimal(current_old_price), grid_old)


def _load_callsign_detector():
    path = PROJECT_ROOT / "scripts/analytics/ozon_non_elastic_parser_review.py"
    spec = importlib.util.spec_from_file_location("ozon_non_elastic_parser_review", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load callsign detector")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._is_callsign


def _scope_rows() -> tuple[list[dict[str, Any]], list[dict[str, str]], list[dict[str, str]]]:
    elastic = _read_csv(ELASTIC_CSV)
    catalog = _read_csv(CATALOG_CSV)
    cpc = _read_csv(CPC_CSV)
    portfolio = _read_csv(PORTFOLIO_CSV)
    by_product = {row.get("ozon_product_id", ""): row for row in catalog if row.get("ozon_product_id")}
    by_offer = {row.get("ozon_offer_id", ""): row for row in catalog if row.get("ozon_offer_id")}
    portfolio_by_sku = {row.get("ozon_sku", ""): row for row in portfolio if row.get("ozon_sku")}
    is_callsign = _load_callsign_detector()

    price_scope: dict[str, dict[str, Any]] = {}
    for action in elastic:
        product = by_product.get(action.get("product_id", "")) or by_offer.get(action.get("offer_id", "")) or {}
        roles: list[str] = []
        if action.get("source_group") == "active":
            roles.append("elastic_active")
        if is_callsign(product, action):
            roles.append("callsign")
        if roles:
            price_scope[action["offer_id"]] = {"action": action, "catalog": product, "roles": roles}

    growth: list[dict[str, str]] = []
    high_drr: list[dict[str, str]] = []
    pause: list[dict[str, str]] = []
    for row in cpc:
        stock = _integer(row.get("stock"))
        orders = _integer(row.get("orders"))
        spend = _decimal(row.get("spend"))
        drr = _decimal(row.get("drr_percent"))
        if (
            row.get("portfolio_code") in {"recovery_a", "test_b"}
            and stock > 0
            and not (orders == 0 and spend >= Decimal("50"))
            and not (spend >= Decimal("100") and drr >= Decimal("12"))
        ):
            growth.append(row)
            source = portfolio_by_sku[row["sku"]]
            offer_id = source["offer_id"]
            product = by_offer.get(offer_id, {})
            current = price_scope.setdefault(
                offer_id,
                {"action": {"offer_id": offer_id, "product_id": source["product_id"]}, "catalog": product, "roles": []},
            )
            if "growth_cpc" not in current["roles"]:
                current["roles"].append("growth_cpc")
        elif row.get("signal") == "высокая ДРР":
            high_drr.append(row)
        elif row.get("signal") == "расход без заказов":
            pause.append(row)
    return list(price_scope.values()), growth + high_drr, pause


def _render_html(summary: dict[str, Any], price_rows: list[dict[str, Any]], bid_rows: list[dict[str, Any]], pause_rows: list[dict[str, Any]]) -> str:
    def table(headers: list[str], rows: list[dict[str, Any]]) -> str:
        head = "".join(f"<th>{escape(label)}</th>" for label in headers)
        body = "".join(
            "<tr>" + "".join(f"<td>{escape(str(row.get(key, '')))}</td>" for key in headers) + "</tr>"
            for row in rows
        )
        return f'<div class="table"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'

    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Ozon цены + CPC dry-run</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#f4f6f8;color:#17202a;font:14px/1.45 Arial,sans-serif;letter-spacing:0}}main{{max-width:1440px;margin:auto;padding:22px}}h1{{font-size:27px;margin:0 0 8px}}h2{{font-size:19px}}.meta{{color:#667085}}.band{{background:#fff;border:1px solid #d8dee6;border-radius:6px;padding:17px;margin:15px 0}}.grid{{display:grid;grid-template-columns:repeat(5,1fr);gap:10px}}.metric{{border-left:4px solid #126b55;background:#f8fafb;padding:11px}}.metric b{{display:block;font-size:22px}}.warn{{border-left:4px solid #9a3412;padding-left:10px}}.table{{overflow:auto;max-height:650px;border:1px solid #d8dee6}}table{{border-collapse:collapse;width:100%;min-width:1100px}}th,td{{padding:7px;border-bottom:1px solid #d8dee6;text-align:left;white-space:nowrap}}th{{position:sticky;top:0;background:#eef2f5}}@media(max-width:800px){{main{{padding:11px}}h1{{font-size:22px}}.grid{{grid-template-columns:repeat(2,1fr)}}}}
</style></head><body><main><h1>Ozon: цены + CPC, точный dry-run</h1><div class="meta">Run ID: {escape(summary['run_id'])} · apply не выполнялся</div>
<section class="band"><div class="grid"><div class="metric">Ценовой scope<b>{summary['price_scope_rows']}</b></div><div class="metric">Повысить цены<b>{summary['price_change_rows']}</b></div><div class="metric">Изменить ставки<b>{summary['bid_change_rows']}</b></div><div class="metric">Growth SKU<b>{summary['growth_bid_rows']}</b></div><div class="metric">Исключить из CPC<b>{summary['pause_rows']}</b></div></div><p class="warn">Это точный пакет на review. Технический порядок apply: цены → verify → числовые ставки → verify. Исключение 5 SKU требует отдельного подтверждённого API-маршрута удаления из кампании.</p></section>
<section class="band"><h2>Изменения цен</h2>{table(['offer_id','ozon_sku','name','roles','pack_qty','current_min_price','current_price','target_price','current_old_price','target_old_price'], price_rows)}</section>
<section class="band"><h2>Числовые ставки</h2>{table(['sku','title','action','current_bid','target_bid','change_percent','spend_30d','orders_30d','drr_percent','portfolio_code'], bid_rows)}</section>
<section class="band"><h2>Кандидаты на исключение из CPC</h2>{table(['sku','title','current_bid','spend_30d','orders_30d','reason'], pause_rows)}</section>
</main></body></html>"""


def main() -> int:
    started = datetime.now(MOSCOW)
    run_id = f"ozon_price_cpc_growth_plan_{started:%Y%m%dT%H%M%S}"
    run_dir = ensure_dir(DATA_DIR / "runs" / started.date().isoformat() / run_id)
    processed = ensure_dir(run_dir / "processed")
    raw = ensure_dir(run_dir / "raw")
    credentials = load_credentials()
    if credentials.ozon_seller is None or credentials.ozon_performance is None:
        raise RuntimeError("Ozon Seller/Performance credentials are missing")

    price_scope, bid_source_rows, pause_source_rows = _scope_rows()
    offer_ids = [row["action"]["offer_id"] for row in price_scope]
    seller = OzonSellerAdapter(credentials.ozon_seller)
    prices_raw = seller.fetch_product_info_prices_by_offer_ids(offer_ids)
    write_json(raw / "fresh_prices.json", prices_raw)
    prices = {str(row.get("offer_id") or ""): normalize_ozon_price_item(row) for row in prices_raw}
    if len(prices) != len(offer_ids):
        raise RuntimeError(f"Fresh price coverage mismatch: {len(prices)} != {len(offer_ids)}")

    performance = OzonPerformanceAdapter(credentials.ozon_performance)
    campaign_products = performance.fetch_campaign_products(CAMPAIGN_ID)
    write_json(raw / "fresh_campaign_products.json", campaign_products)
    campaign_by_sku = {str(row.get("sku") or ""): row for row in campaign_products}

    price_rows: list[dict[str, Any]] = []
    price_change_rows: list[dict[str, Any]] = []
    for scope in price_scope:
        action = scope["action"]
        offer_id = action["offer_id"]
        catalog = scope["catalog"]
        current = prices[offer_id]
        pack_qty = _integer(catalog.get("pack_qty")) or KNOWN_PACK_QTY.get(offer_id, 0)
        if pack_qty not in PRICE_GRID:
            raise RuntimeError(f"Missing pack_qty for {offer_id}")
        target_min, target_price, target_old = price_targets(pack_qty, current.get("price"), current.get("old_price"))
        row = {
            "offer_id": offer_id,
            "product_id": str(action.get("product_id") or current.get("product_id") or ""),
            "ozon_sku": str(current.get("sku") or ""),
            "name": str(action.get("name") or catalog.get("product_name") or ""),
            "roles": ",".join(sorted(scope["roles"])),
            "pack_qty": pack_qty,
            "current_min_price": _money(current.get("min_price")),
            "target_min_price": _money(target_min),
            "current_price": _money(current.get("price")),
            "target_price": _money(target_price),
            "current_old_price": _money(current.get("old_price")),
            "target_old_price": _money(target_old),
            "currency_code": str(current.get("currency_code") or "RUB"),
            "action": "increase_price" if target_price > _decimal(current.get("price")) or target_old > _decimal(current.get("old_price")) else "keep_at_or_above_grid",
        }
        price_rows.append(row)
        if row["action"] == "increase_price":
            price_change_rows.append(row)

    bid_rows: list[dict[str, Any]] = []
    for source in bid_source_rows:
        sku = source["sku"]
        current_product = campaign_by_sku.get(sku)
        if current_product is None:
            raise RuntimeError(f"SKU is missing from active CPC campaign: {sku}")
        current_bid = _decimal(current_product.get("bid")) / Decimal("1000000")
        action = "reduce_high_drr" if source.get("signal") == "высокая ДРР" else "increase_growth"
        target_bid = growth_target_bid(current_bid) if action == "increase_growth" else reduce_target_bid(sku, current_bid)
        bid_rows.append(
            {
                "campaign_id": CAMPAIGN_ID,
                "sku": sku,
                "title": source.get("title", ""),
                "action": action,
                "current_bid": _money(current_bid),
                "target_bid": _money(target_bid),
                "change_percent": _money((target_bid / current_bid - Decimal("1")) * Decimal("100")),
                "spend_30d": source.get("spend", ""),
                "orders_30d": source.get("orders", ""),
                "drr_percent": source.get("drr_percent", ""),
                "portfolio_code": source.get("portfolio_code", ""),
            }
        )

    pause_rows: list[dict[str, Any]] = []
    for source in pause_source_rows:
        current = campaign_by_sku.get(source["sku"])
        if current is None:
            raise RuntimeError(f"Pause SKU is missing from active CPC campaign: {source['sku']}")
        pause_rows.append(
            {
                "campaign_id": CAMPAIGN_ID,
                "sku": source["sku"],
                "title": source.get("title", ""),
                "current_bid": _money(_decimal(current.get("bid")) / Decimal("1000000")),
                "spend_30d": source.get("spend", ""),
                "orders_30d": source.get("orders", ""),
                "reason": "spend >= 50 RUB and zero CPC-attributed orders",
                "action": "exclude_from_campaign_separate_route",
            }
        )

    price_rows.sort(key=lambda row: (row["action"] != "increase_price", row["pack_qty"], row["offer_id"]))
    bid_rows.sort(key=lambda row: (row["action"], -_decimal(row["spend_30d"]), row["sku"]))
    pause_rows.sort(key=lambda row: (-_decimal(row["spend_30d"]), row["sku"]))
    _write_csv(processed / "price_scope.csv", price_rows)
    _write_csv(processed / "price_changes.csv", price_change_rows)
    _write_csv(processed / "bid_changes.csv", bid_rows)
    _write_csv(processed / "pause_candidates.csv", pause_rows)

    checksum_payload = {
        "price_changes": price_change_rows,
        "bid_changes": bid_rows,
        "pause_candidates": pause_rows,
    }
    checksum = canonical_checksum(checksum_payload)
    summary = {
        "run_id": run_id,
        "started_at": started.isoformat(timespec="seconds"),
        "overall_status": "warning",
        "mode": "dry_run",
        "apply_performed": False,
        "campaign_id": CAMPAIGN_ID,
        "price_scope_rows": len(price_rows),
        "price_change_rows": len(price_change_rows),
        "price_keep_rows": len(price_rows) - len(price_change_rows),
        "growth_bid_rows": sum(row["action"] == "increase_growth" for row in bid_rows),
        "reduce_bid_rows": sum(row["action"] == "reduce_high_drr" for row in bid_rows),
        "bid_change_rows": len(bid_rows),
        "pause_rows": len(pause_rows),
        "bid_target_distribution": dict(Counter(f"{row['current_bid']}->{row['target_bid']}" for row in bid_rows)),
        "price_pack_distribution": dict(Counter(str(row["pack_qty"]) for row in price_change_rows)),
        "actions_checksum": checksum,
        "limitations": [
            "Pause/exclude rows require a separately verified Ozon Performance API removal route.",
            "Price and bid apply must use a fresh snapshot and partial drift-check against this checksum.",
            "Apply sequence is prices -> price verify -> numeric bids -> bid verify; failed price rows must not receive growth bids.",
        ],
        "artifacts": {
            "run_dir": str(run_dir),
            "report": str(run_dir / "report.html"),
            "summary": str(run_dir / "summary.json"),
            "price_scope": str(processed / "price_scope.csv"),
            "price_changes": str(processed / "price_changes.csv"),
            "bid_changes": str(processed / "bid_changes.csv"),
            "pause_candidates": str(processed / "pause_candidates.csv"),
            "fresh_prices": str(raw / "fresh_prices.json"),
            "fresh_campaign_products": str(raw / "fresh_campaign_products.json"),
        },
    }
    write_json(run_dir / "summary.json", summary)
    (run_dir / "report.html").write_text(
        _render_html(summary, price_rows, bid_rows, pause_rows), encoding="utf-8"
    )
    manifest = write_summary_run_manifest(
        data_dir=DATA_DIR,
        run_dir=run_dir,
        summary=summary,
        task="ozon-price-cpc-growth-plan",
        mode="dry_run",
        risk="high",
        marketplaces=["ozon"],
        inputs={
            "elastic_source_run_id": ELASTIC_CSV.parent.name,
            "cpc_source_run_id": CPC_CSV.parents[1].name,
            "portfolio_source_run_id": PORTFOLIO_CSV.parent.name,
        },
        lifecycle_status="pending_review",
        closed=False,
    )
    summary["artifacts"].update(manifest)
    write_json(run_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
