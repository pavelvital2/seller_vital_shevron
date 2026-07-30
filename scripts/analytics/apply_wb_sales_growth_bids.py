#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import time
from typing import Any

from seller_agent.config import load_credentials
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.marketplaces.wb.analytics_adapter import WbAnalyticsAdapter
from seller_agent.marketplaces.wb.prices_adapter import WbPricesAdapter
from seller_agent.marketplaces.wb.promotion_adapter import WbPromotionAdapter
from seller_agent.reports.writer import ensure_dir, write_json
from seller_agent.safety.approvals import (
    apply_marker_for,
    assert_apply_not_repeated,
    canonical_checksum,
    mark_approved_applied,
)
from seller_agent.tasks.wb_promotion_bids_apply import (
    _current_bids_from_campaigns,
    _group_bid_payload,
    _verify_applied_rows,
)


APPROVED_ID = "owner-chat-20260729-wb-sales-growth-bids-6-scale-30-focus"
EXPECTED_SCALE = 6
EXPECTED_FOCUS = 30
UNIT_COST = Decimal("85")
TARGET_MARGIN = Decimal("50")
SALE_RETENTION = Decimal("0.8755")
GENERAL_EXPENSE = Decimal("116.86")
BUYOUT_FACTOR = Decimal("0.8902")
MIN_STOCK = 8
MAX_TEST_CLICKS_PER_ORDER = Decimal("20")


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0").strip().replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(rows[0]) if rows else ["empty"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _find_run(data_dir: Path, run_id: str) -> Path:
    matches = sorted((data_dir / "runs").glob(f"*/{run_id}"))
    if not matches:
        raise RuntimeError(f"source run not found: {run_id}")
    return matches[-1]


def _catalog(data_dir: Path) -> dict[str, dict[str, Any]]:
    rows = json.loads((data_dir / "catalog/unified/products.json").read_text(encoding="utf-8"))
    return {
        str(row.get("wb_nm_id") or ""): row
        for row in rows
        if str(row.get("wb_nm_id") or "")
    }


def _source_rows(source_dir: Path, catalog: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    decisions = _read_csv(source_dir / "processed/wb_product_decisions.csv")
    focus = _read_csv(source_dir / "processed/wb_focus_wave_30.csv")
    scale_rows = [
        row for row in decisions if row.get("recommendation") in {"SCALE_20", "SCALE_30"}
    ]
    if len(scale_rows) != EXPECTED_SCALE or len(focus) != EXPECTED_FOCUS:
        raise RuntimeError(
            f"source package count drift: scale={len(scale_rows)}, focus={len(focus)}"
        )
    output: list[dict[str, Any]] = []
    for source, action, target_field in [
        *[(row, row["recommendation"].lower(), "target_bid_dry_run") for row in scale_rows],
        *[(row, "focus_25", "focus_target_bid") for row in focus],
    ]:
        nm_id = str(source.get("nm_id") or "")
        advert_id = str(source.get("advert_id") or "")
        current = _decimal(source.get("current_bid")).quantize(Decimal("0.01"))
        target = _decimal(source.get(target_field)).quantize(Decimal("0.01"))
        catalog_row = catalog.get(nm_id, {})
        name = str(source.get("name") or catalog_row.get("product_name") or "")
        sku = str(source.get("internal_sku") or catalog_row.get("internal_sku") or "")
        if not nm_id or not advert_id or target <= current:
            raise RuntimeError(f"invalid approved growth row: {advert_id}/{nm_id}")
        if "позывн" in f"{name} {sku}".lower() or "_pz_" in sku.lower():
            raise RuntimeError(f"callsign present in growth package: {advert_id}/{nm_id}")
        output.append(
            {
                "advert_id": advert_id,
                "campaign_name": source.get("campaign", ""),
                "nm_id": nm_id,
                "internal_sku": sku,
                "name": name,
                "pack_qty": max(1, int(_decimal(catalog_row.get("pack_qty") or 1))),
                "approved_action": action,
                "approved_current_bid": str(current),
                "target_bid": str(target),
                "target_bid_kopecks": int(target * 100),
                "current_bid_place": "search",
            }
        )
    identities = {(row["advert_id"], row["nm_id"], row["current_bid_place"]) for row in output}
    if len(identities) != len(output):
        raise RuntimeError("duplicate campaign/product rows in approved growth package")
    return sorted(output, key=lambda row: (int(row["advert_id"]), int(row["nm_id"])))


def _prices(rows: list[dict[str, Any]]) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    for row in rows:
        nm_id = str(row.get("nmID") or row.get("nm_id") or "")
        sizes = row.get("sizes") or []
        size = sizes[0] if sizes and isinstance(sizes[0], dict) else {}
        value = size.get("discountedPrice") or size.get("clubDiscountedPrice")
        if nm_id and value not in (None, ""):
            result[nm_id] = _decimal(value).quantize(Decimal("0.01"))
    return result


def _stocks(rows: list[dict[str, Any]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in rows:
        nm_id = str(row.get("nmId") or "")
        result[nm_id] = result.get(nm_id, 0) + int(_decimal(row.get("quantity")))
    return result


def _margin_headroom(price: Decimal, pack_qty: int) -> Decimal:
    return (
        price * SALE_RETENTION
        - GENERAL_EXPENSE
        - (UNIT_COST + TARGET_MARGIN) * pack_qty
    ).quantize(Decimal("0.01"))


def _fresh_check(
    approved_rows: list[dict[str, Any]],
    *,
    campaigns: list[dict[str, Any]],
    prices: dict[str, Decimal],
    stocks: dict[str, int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    current_bids = _current_bids_from_campaigns(campaigns)
    unchanged: list[dict[str, Any]] = []
    drift: list[dict[str, Any]] = []
    for source in approved_rows:
        row = dict(source)
        key = (row["advert_id"], row["nm_id"], row["current_bid_place"])
        current_bid = current_bids.get(key)
        approved_current = _decimal(row["approved_current_bid"]).quantize(Decimal("0.01"))
        price = prices.get(row["nm_id"])
        stock = stocks.get(row["nm_id"], 0)
        target_bid = _decimal(row["target_bid"])
        headroom = _margin_headroom(price, row["pack_qty"]) if price is not None else Decimal("-1")
        conservative_test_cost = (
            target_bid * MAX_TEST_CLICKS_PER_ORDER / BUYOUT_FACTOR
        ).quantize(Decimal("0.01"))
        reasons: list[str] = []
        if current_bid is None:
            reasons.append("campaign_or_bid_missing")
        elif current_bid != approved_current:
            reasons.append(f"bid_drift:{approved_current}->{current_bid}")
        if price is None:
            reasons.append("price_missing")
        if stock < MIN_STOCK:
            reasons.append(f"stock_below_{MIN_STOCK}:{stock}")
        if headroom < conservative_test_cost:
            reasons.append(
                f"margin_guard:{headroom}<{conservative_test_cost}"
            )
        row.update(
            {
                "fresh_current_bid": str(current_bid or ""),
                "fresh_price": str(price or ""),
                "fresh_stock": stock,
                "margin50_ad_headroom": str(headroom),
                "conservative_20_click_cost": str(conservative_test_cost),
                "drift_reasons": ";".join(reasons),
                "current_bid": str(current_bid or ""),
                "current_bid_kopecks": int((current_bid or Decimal("0")) * 100),
            }
        )
        (drift if reasons else unchanged).append(row)
    return unchanged, drift


def _write_report(path: Path, result: dict[str, Any]) -> None:
    summary = result["summary"]
    lines = [
        "# WB: применение ставок для роста продаж",
        "",
        f"- Статус: `{result['overall_status']}`",
        f"- Согласовано: `{summary['approved_rows']}`",
        f"- Без drift: `{summary['unchanged_rows']}`",
        f"- Применено: `{summary['applied_rows']}`",
        f"- Пропущено: `{summary['drift_rows']}`",
        f"- Сумма ставок: `{summary['current_bid_sum']} -> {summary['target_bid_sum']} руб.`",
        f"- Verify: `{summary['verify_status']}`",
        "- Цены, скидки, состав кампаний и бюджеты не изменялись.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--source-run-id", required=True)
    parser.add_argument("--confirmed-by-user", action="store_true")
    parser.add_argument("--verify-wait-seconds", type=int, default=45)
    args = parser.parse_args()
    if not args.confirmed_by_user:
        raise RuntimeError("explicit owner confirmation is required")

    credentials = load_credentials()
    if not credentials.wb:
        raise RuntimeError("missing WB API token")
    started = datetime.now()
    run_id = f"wb_sales_growth_bids_apply_{started.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(args.data_dir / "runs" / started.strftime("%Y-%m-%d") / run_id)
    raw_dir = ensure_dir(run_dir / "raw")
    processed_dir = ensure_dir(run_dir / "processed")

    source_dir = _find_run(args.data_dir, args.source_run_id)
    approved_rows = _source_rows(source_dir, _catalog(args.data_dir))
    package = {
        "approved_id": APPROVED_ID,
        "source_run_id": args.source_run_id,
        "actions": approved_rows,
    }
    checksum = canonical_checksum(package)
    write_json(
        processed_dir / "approved_package.json",
        {**package, "approved_checksum": checksum},
    )
    assert_apply_not_repeated(
        data_dir=args.data_dir,
        approved_id=APPROVED_ID,
        current_run_id=run_id,
    )

    promotion = WbPromotionAdapter(credentials.wb)
    advert_ids = sorted({int(row["advert_id"]) for row in approved_rows})
    campaigns_before = promotion.fetch_campaigns(
        ids=advert_ids,
        statuses=[9],
        payment_type="cpc",
    )
    write_json(raw_dir / "campaigns_before.json", {"adverts": campaigns_before})
    price_rows = WbPricesAdapter(credentials.wb).fetch_goods_prices()
    write_json(raw_dir / "prices_before.json", {"items": price_rows})
    nm_ids = sorted({int(row["nm_id"]) for row in approved_rows})
    stock_rows = WbAnalyticsAdapter(credentials.wb).fetch_wb_warehouse_stocks(nm_ids=nm_ids)
    write_json(raw_dir / "stocks_before.json", {"items": stock_rows})

    unchanged, drift = _fresh_check(
        approved_rows,
        campaigns=campaigns_before,
        prices=_prices(price_rows),
        stocks=_stocks(stock_rows),
    )
    _write_csv(processed_dir / "unchanged_rows.csv", unchanged)
    _write_csv(processed_dir / "drift_rows.csv", drift)
    write_json(
        processed_dir / "drift_check.json",
        {
            "approved_rows": len(approved_rows),
            "unchanged_rows": len(unchanged),
            "drift_rows": len(drift),
        },
    )
    if not unchanged:
        raise RuntimeError("all approved rows drifted; no write performed")

    payload = _group_bid_payload(unchanged)
    write_json(processed_dir / "update_payload.json", payload)
    response = promotion.update_bids(payload)
    write_json(raw_dir / "update_response.json", response)
    time.sleep(max(1, args.verify_wait_seconds))
    campaigns_after = promotion.fetch_campaigns(
        ids=advert_ids,
        statuses=[9],
        payment_type="cpc",
    )
    write_json(raw_dir / "campaigns_after.json", {"adverts": campaigns_after})
    verify = _verify_applied_rows(campaigns=campaigns_after, apply_rows=unchanged)
    current_sum = sum((_decimal(row["current_bid"]) for row in unchanged), Decimal("0"))
    target_sum = sum((_decimal(row["target_bid"]) for row in unchanged), Decimal("0"))
    overall_status = "ok" if verify["status"] == "ok" else "error"
    summary = {
        "approved_rows": len(approved_rows),
        "unchanged_rows": len(unchanged),
        "drift_rows": len(drift),
        "applied_rows": len(unchanged),
        "scale_rows": sum(row["approved_action"].startswith("scale") for row in unchanged),
        "focus_rows": sum(row["approved_action"] == "focus_25" for row in unchanged),
        "current_bid_sum": str(current_sum.quantize(Decimal("0.01"))),
        "target_bid_sum": str(target_sum.quantize(Decimal("0.01"))),
        "verify_status": verify["status"],
        "verify_mismatches": len(verify["mismatches"]),
    }
    result = {
        "run_id": run_id,
        "started_at": started.isoformat(timespec="seconds"),
        "mode": "apply",
        "overall_status": overall_status,
        "approved_id": APPROVED_ID,
        "approved_checksum": checksum,
        "source_run_id": args.source_run_id,
        "summary": summary,
        "update_response": response,
        "verify": verify,
        "apply_performed": True,
        "artifacts": {
            "run_dir": str(run_dir),
            "summary": str(run_dir / "summary.json"),
            "report": str(run_dir / "result.md"),
            "approved_package": str(processed_dir / "approved_package.json"),
            "drift_check": str(processed_dir / "drift_check.json"),
            "unchanged_rows": str(processed_dir / "unchanged_rows.csv"),
            "drift_rows": str(processed_dir / "drift_rows.csv"),
            "run_manifest": str(run_dir / "manifest.json"),
            "apply_marker": str(
                apply_marker_for(data_dir=args.data_dir, approved_id=APPROVED_ID)
            ),
        },
    }
    write_json(run_dir / "summary.json", result)
    _write_report(run_dir / "result.md", result)
    manifest = write_summary_run_manifest(
        data_dir=args.data_dir,
        run_dir=run_dir,
        summary=result,
        task="wb-sales-growth-bids-apply",
        mode="apply",
        risk="high",
        marketplaces=["wb"],
        inputs={
            "source_run_id": args.source_run_id,
            "confirmed_by_user": True,
            "verify_wait_seconds": args.verify_wait_seconds,
        },
        lifecycle_status="verified" if overall_status == "ok" else "needs_attention",
        closed=overall_status == "ok",
    )
    result["artifacts"].update(manifest)
    write_json(run_dir / "summary.json", result)
    mark_approved_applied(
        data_dir=args.data_dir,
        approved_id=APPROVED_ID,
        apply_run_id=run_id,
        task="wb-sales-growth-bids-apply",
        status=overall_status,
        run_manifest_path=manifest["manifest"],
        checksum=checksum,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if overall_status == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
