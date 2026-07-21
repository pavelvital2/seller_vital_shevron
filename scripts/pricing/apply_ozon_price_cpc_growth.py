#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import time
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from seller_agent.config import load_credentials
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.marketplaces.ozon.adapter import OzonSellerAdapter
from seller_agent.marketplaces.ozon.performance_adapter import OzonPerformanceAdapter
from seller_agent.reports.writer import ensure_dir, write_json
from seller_agent.safety.approvals import (
    apply_marker_for,
    assert_apply_not_repeated,
    canonical_checksum,
    mark_approved_applied,
)
from seller_agent.tasks.ozon_cpc_bids_apply import _current_bid_map, _current_bid_rows
from seller_agent.tasks.pricing_status import normalize_ozon_price_item
from seller_agent.tasks.status_preflight import run_status_preflight


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
CATALOG_PATH = DATA_DIR / "catalog/unified/products.csv"
MOSCOW = ZoneInfo("Europe/Moscow")
MICRO_RUB = Decimal("1000000")


def _decimal(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value).replace(" ", "").replace("\u00a0", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _money(value: Any) -> str:
    return str(_decimal(value).quantize(Decimal("0.01")))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, Any]], *, empty_header: str) -> None:
    headers = list(rows[0]) if rows else [empty_header]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _plan_dir(data_dir: Path, run_id: str) -> Path:
    matches = sorted((data_dir / "runs").glob(f"*/{run_id}"))
    if not matches:
        raise FileNotFoundError(f"approved plan not found: {run_id}")
    return matches[-1]


def load_approved_package(plan_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, str]], list[dict[str, str]]]:
    summary = json.loads((plan_dir / "summary.json").read_text(encoding="utf-8"))
    processed = plan_dir / "processed"
    price_rows: list[dict[str, Any]] = _read_csv(processed / "price_changes.csv")
    for row in price_rows:
        row["pack_qty"] = int(str(row["pack_qty"]))
    bid_rows = _read_csv(processed / "bid_changes.csv")
    pause_rows = _read_csv(processed / "pause_candidates.csv")
    actual_checksum = canonical_checksum(
        {
            "price_changes": price_rows,
            "bid_changes": bid_rows,
            "pause_candidates": pause_rows,
        }
    )
    expected_checksum = str(summary.get("actions_checksum") or "")
    if not expected_checksum or actual_checksum != expected_checksum:
        raise RuntimeError(
            f"approved package checksum mismatch: expected {expected_checksum}, got {actual_checksum}"
        )
    expected_counts = {
        "price_change_rows": len(price_rows),
        "bid_change_rows": len(bid_rows),
        "pause_rows": len(pause_rows),
    }
    for key, actual in expected_counts.items():
        if int(summary.get(key) or -1) != actual:
            raise RuntimeError(f"approved package row count mismatch for {key}")
    return summary, price_rows, bid_rows, pause_rows


def classify_price_rows(
    approved_rows: list[dict[str, Any]],
    fresh_by_offer: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    apply_rows: list[dict[str, Any]] = []
    already_target: list[dict[str, Any]] = []
    drifted: list[dict[str, Any]] = []
    for row in approved_rows:
        offer_id = str(row.get("offer_id") or "")
        fresh = fresh_by_offer.get(offer_id)
        if not fresh:
            drifted.append({"offer_id": offer_id, "reason": "missing_in_fresh_snapshot"})
            continue
        identity_ok = str(fresh.get("product_id") or "") == str(row.get("product_id") or "")
        current_matches = (
            identity_ok
            and _decimal(fresh.get("min_price")) == _decimal(row.get("current_min_price"))
            and _decimal(fresh.get("price")) == _decimal(row.get("current_price"))
            and _decimal(fresh.get("old_price")) == _decimal(row.get("current_old_price"))
        )
        target_matches = (
            identity_ok
            and _decimal(fresh.get("min_price")) == _decimal(row.get("target_min_price"))
            and _decimal(fresh.get("price")) == _decimal(row.get("target_price"))
            and _decimal(fresh.get("old_price")) == _decimal(row.get("target_old_price"))
        )
        if target_matches:
            already_target.append({**row, "fresh_state": "already_target"})
        elif current_matches:
            apply_rows.append({**row, "fresh_state": "approved_current"})
        else:
            drifted.append(
                {
                    "offer_id": offer_id,
                    "reason": "price_state_drift",
                    "approved_current": {
                        "min_price": row.get("current_min_price"),
                        "price": row.get("current_price"),
                        "old_price": row.get("current_old_price"),
                    },
                    "fresh": {
                        "product_id": fresh.get("product_id"),
                        "min_price": _money(fresh.get("min_price")),
                        "price": _money(fresh.get("price")),
                        "old_price": _money(fresh.get("old_price")),
                    },
                }
            )
    return apply_rows, already_target, drifted


def _price_payload(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "offer_id": str(row["offer_id"]),
            "price": _money(row["target_price"]),
            "old_price": _money(row["target_old_price"]),
            "min_price": _money(row["target_min_price"]),
            "currency_code": str(row.get("currency_code") or "RUB"),
            "min_price_for_auto_actions_enabled": True,
        }
        for row in rows
    ]


def _price_target_matches(row: dict[str, Any], fresh: dict[str, Any] | None) -> bool:
    return bool(fresh) and (
        str(fresh.get("product_id") or "") == str(row.get("product_id") or "")
        and _decimal(fresh.get("min_price")) == _decimal(row.get("target_min_price"))
        and _decimal(fresh.get("price")) == _decimal(row.get("target_price"))
        and _decimal(fresh.get("old_price")) == _decimal(row.get("target_old_price"))
    )


def _growth_offer_map(bid_rows: list[dict[str, str]], price_rows: list[dict[str, Any]]) -> dict[str, str]:
    catalog = _read_csv(CATALOG_PATH)
    catalog_by_sku = {str(row.get("ozon_sku") or ""): row for row in catalog if row.get("ozon_sku")}
    price_by_offer = {str(row.get("offer_id") or ""): row for row in price_rows}
    result: dict[str, str] = {}
    for row in bid_rows:
        if row.get("action") != "increase_growth":
            continue
        sku = str(row.get("sku") or "")
        catalog_row = catalog_by_sku.get(sku)
        offer_id = str((catalog_row or {}).get("ozon_offer_id") or "")
        price_row = price_by_offer.get(offer_id)
        if not catalog_row or not price_row:
            raise RuntimeError(f"growth SKU has no approved price mapping: {sku}")
        if str(catalog_row.get("ozon_product_id") or "") != str(price_row.get("product_id") or ""):
            raise RuntimeError(f"growth SKU price mapping product_id mismatch: {sku}")
        result[sku] = offer_id
    return result


def classify_bid_rows(
    approved_rows: list[dict[str, str]],
    fresh_bids: dict[str, Decimal],
    growth_offer_by_sku: dict[str, str],
    verified_price_offers: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    apply_rows: list[dict[str, Any]] = []
    already_target: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for row in approved_rows:
        sku = str(row.get("sku") or "")
        fresh = fresh_bids.get(sku)
        current = _decimal(row.get("current_bid"))
        target = _decimal(row.get("target_bid"))
        if fresh is None:
            skipped.append({**row, "skip_reason": "missing_in_fresh_campaign"})
            continue
        if fresh == target:
            already_target.append({**row, "fresh_bid": _money(fresh), "fresh_state": "already_target"})
            continue
        if fresh != current:
            skipped.append({**row, "fresh_bid": _money(fresh), "skip_reason": "current_bid_drift"})
            continue
        if row.get("action") == "increase_growth":
            offer_id = growth_offer_by_sku.get(sku, "")
            if offer_id not in verified_price_offers:
                skipped.append(
                    {**row, "offer_id": offer_id, "skip_reason": "growth_price_not_verified"}
                )
                continue
        apply_rows.append(
            {
                **row,
                "raw_current_bid": str(int((current * MICRO_RUB).to_integral_value())),
                "raw_target_bid": str(int((target * MICRO_RUB).to_integral_value())),
                "fresh_state": "approved_current",
            }
        )
    return apply_rows, already_target, skipped


def _fetch_prices(ozon: OzonSellerAdapter, offer_ids: list[str]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    raw = ozon.fetch_product_info_prices_by_offer_ids(offer_ids)
    normalized = [normalize_ozon_price_item(row) for row in raw]
    return raw, {str(row.get("offer_id") or ""): row for row in normalized}


def _report(result: dict[str, Any]) -> str:
    summary = result["summary"]
    lines = [
        "# Ozon price + CPC apply result",
        "",
        f"Run ID: `{result['run_id']}`",
        f"Approved plan: `{result['approved_plan_run_id']}`",
        f"Status: `{result['overall_status']}`",
        "",
        "## Prices",
        "",
        f"- approved changes: `{summary['price_approved_rows']}`",
        f"- submitted: `{summary['price_submitted_rows']}`",
        f"- already at target: `{summary['price_already_target_rows']}`",
        f"- verified: `{summary['price_verified_rows']}`",
        f"- drifted/skipped: `{summary['price_drifted_rows']}`",
        "",
        "## CPC bids",
        "",
        f"- approved numeric changes: `{summary['bid_approved_rows']}`",
        f"- submitted: `{summary['bid_submitted_rows']}`",
        f"- already at target: `{summary['bid_already_target_rows']}`",
        f"- verified: `{summary['bid_verified_rows']}`",
        f"- skipped: `{summary['bid_skipped_rows']}`",
        f"- exclusion candidates not applied: `{summary['pause_not_applied_rows']}`",
        "",
        "Apply order: prices -> price verify -> numeric CPC bids -> bid verify.",
        "The separately listed CPC exclusions were not applied.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan-run-id", required=True)
    parser.add_argument("--data-dir", default=str(DATA_DIR))
    parser.add_argument("--confirmed-by-user", action="store_true")
    args = parser.parse_args()
    if not args.confirmed_by_user:
        raise RuntimeError("apply requires --confirmed-by-user")

    data_dir = Path(args.data_dir)
    plan_dir = _plan_dir(data_dir, args.plan_run_id)
    plan_summary, price_rows, bid_rows, pause_rows = load_approved_package(plan_dir)
    approved_id = args.plan_run_id
    assert_apply_not_repeated(data_dir=data_dir, approved_id=approved_id)

    credentials = load_credentials()
    if not credentials.ozon_seller or not credentials.ozon_performance:
        raise RuntimeError("Ozon Seller/Performance credentials are unavailable")

    started = datetime.now(MOSCOW)
    run_id = f"ozon_price_cpc_growth_apply_{started:%Y%m%dT%H%M%S}"
    run_dir = ensure_dir(data_dir / "runs" / started.date().isoformat() / run_id)
    raw_dir = ensure_dir(run_dir / "raw")
    processed_dir = ensure_dir(run_dir / "processed")

    preflight = run_status_preflight(credentials=credentials, data_dir=data_dir, include_lk=False)
    if preflight["overall_status"] not in {"ok", "warning"}:
        raise RuntimeError(f"preflight is not safe: {preflight['overall_status']}")

    seller = OzonSellerAdapter(credentials.ozon_seller)
    performance = OzonPerformanceAdapter(credentials.ozon_performance)
    offer_ids = [str(row["offer_id"]) for row in price_rows]
    raw_prices_before, prices_before = _fetch_prices(seller, offer_ids)
    write_json(raw_dir / "prices_before.json", raw_prices_before)
    price_apply_rows, price_already_target, price_drifted = classify_price_rows(price_rows, prices_before)
    write_json(processed_dir / "price_drift_check.json", {
        "approved_rows": len(price_rows),
        "apply_rows": len(price_apply_rows),
        "already_target_rows": len(price_already_target),
        "drifted_rows": len(price_drifted),
        "drifted": price_drifted,
        "actions_checksum": plan_summary["actions_checksum"],
    })

    price_payload = _price_payload(price_apply_rows)
    write_json(raw_dir / "price_request.json", {"prices": price_payload})
    price_response: Any = None
    if price_payload:
        price_response = seller.import_product_prices(price_payload)
    write_json(raw_dir / "price_response.json", price_response)

    approved_price_by_offer = {str(row["offer_id"]): row for row in price_rows}
    price_verify_rows: list[dict[str, Any]] = []
    raw_prices_after: list[dict[str, Any]] = []
    prices_after: dict[str, dict[str, Any]] = {}
    for attempt in range(1, 9):
        if price_payload:
            time.sleep(3 if attempt == 1 else 7)
        raw_prices_after, prices_after = _fetch_prices(seller, offer_ids)
        price_verify_rows = []
        for offer_id, approved in approved_price_by_offer.items():
            fresh = prices_after.get(offer_id)
            ok = _price_target_matches(approved, fresh)
            price_verify_rows.append(
                {
                    "offer_id": offer_id,
                    "status": "ok" if ok else "warning",
                    "actual_min_price": _money((fresh or {}).get("min_price")),
                    "actual_price": _money((fresh or {}).get("price")),
                    "actual_old_price": _money((fresh or {}).get("old_price")),
                    "target_min_price": approved.get("target_min_price"),
                    "target_price": approved.get("target_price"),
                    "target_old_price": approved.get("target_old_price"),
                }
            )
        write_json(processed_dir / f"price_verify_attempt_{attempt:02d}.json", price_verify_rows)
        submitted_offers = {str(row["offer_id"]) for row in price_apply_rows}
        if all(
            row["status"] == "ok" or row["offer_id"] not in submitted_offers
            for row in price_verify_rows
        ):
            break
    write_json(raw_dir / "prices_after.json", raw_prices_after)
    verified_price_offers = {row["offer_id"] for row in price_verify_rows if row["status"] == "ok"}
    _write_csv(processed_dir / "price_verify.csv", price_verify_rows, empty_header="offer_id")

    campaign_id = str(plan_summary["campaign_id"])
    campaign_before = performance.fetch_campaign_products(campaign_id)
    write_json(raw_dir / "campaign_products_before.json", campaign_before)
    fresh_bid_rows = _current_bid_rows(campaign_before)
    fresh_bids = _current_bid_map(fresh_bid_rows)
    growth_offer_by_sku = _growth_offer_map(bid_rows, price_rows)
    bid_apply_rows, bid_already_target, bid_skipped = classify_bid_rows(
        bid_rows,
        fresh_bids,
        growth_offer_by_sku,
        verified_price_offers,
    )
    _write_csv(processed_dir / "bid_apply_rows.csv", bid_apply_rows, empty_header="sku")
    _write_csv(processed_dir / "bid_already_target.csv", bid_already_target, empty_header="sku")
    _write_csv(processed_dir / "bid_skipped.csv", bid_skipped, empty_header="sku")
    bid_response: Any = None
    if bid_apply_rows:
        bid_response = performance.update_campaign_product_bids(
            campaign_id,
            [{"sku": str(row["sku"]), "bid": str(row["raw_target_bid"])} for row in bid_apply_rows],
        )
    write_json(raw_dir / "bid_response.json", bid_response)

    campaign_after = performance.fetch_campaign_products(campaign_id)
    write_json(raw_dir / "campaign_products_after.json", campaign_after)
    bids_after = _current_bid_map(_current_bid_rows(campaign_after))
    bid_verify_rows: list[dict[str, Any]] = []
    for row in [*bid_apply_rows, *bid_already_target]:
        sku = str(row["sku"])
        actual = bids_after.get(sku)
        target = _decimal(row["target_bid"])
        bid_verify_rows.append(
            {
                "sku": sku,
                "action": row.get("action"),
                "status": "ok" if actual == target else "warning",
                "actual_bid": _money(actual),
                "target_bid": _money(target),
            }
        )
    _write_csv(processed_dir / "bid_verify.csv", bid_verify_rows, empty_header="sku")

    price_verified = len(verified_price_offers)
    bid_verified = sum(row["status"] == "ok" for row in bid_verify_rows)
    price_failed = len(price_rows) - price_verified
    bid_failed = len(bid_verify_rows) - bid_verified
    status = "ok" if not price_failed and not bid_skipped and not bid_failed else "warning"
    summary = {
        "price_approved_rows": len(price_rows),
        "price_submitted_rows": len(price_apply_rows),
        "price_already_target_rows": len(price_already_target),
        "price_verified_rows": price_verified,
        "price_drifted_rows": len(price_drifted),
        "bid_approved_rows": len(bid_rows),
        "bid_submitted_rows": len(bid_apply_rows),
        "bid_already_target_rows": len(bid_already_target),
        "bid_verified_rows": bid_verified,
        "bid_skipped_rows": len(bid_skipped),
        "bid_applied_actions": dict(Counter(str(row.get("action")) for row in bid_apply_rows)),
        "pause_not_applied_rows": len(pause_rows),
    }
    result = {
        "run_id": run_id,
        "started_at": started.isoformat(timespec="seconds"),
        "mode": "apply",
        "overall_status": status,
        "approved_plan_run_id": approved_id,
        "approved_id": approved_id,
        "actions_checksum": plan_summary["actions_checksum"],
        "apply_performed": bool(price_apply_rows or bid_apply_rows),
        "preflight": {"run_id": preflight["run_id"], "status": preflight["overall_status"]},
        "summary": summary,
        "artifacts": {
            "run_dir": str(run_dir),
            "report": str(run_dir / "report.md"),
            "summary": str(run_dir / "summary.json"),
            "price_drift": str(processed_dir / "price_drift_check.json"),
            "price_verify": str(processed_dir / "price_verify.csv"),
            "bid_apply_rows": str(processed_dir / "bid_apply_rows.csv"),
            "bid_skipped": str(processed_dir / "bid_skipped.csv"),
            "bid_verify": str(processed_dir / "bid_verify.csv"),
            "apply_marker": str(apply_marker_for(data_dir=data_dir, approved_id=approved_id)),
        },
    }
    (run_dir / "report.md").write_text(_report(result), encoding="utf-8")
    write_json(run_dir / "summary.json", result)
    manifest = write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=result,
        task="ozon-price-cpc-growth-apply",
        mode="apply",
        risk="high",
        marketplaces=["ozon"],
        inputs={
            "plan_run_id": approved_id,
            "confirmed_by_user": True,
            "excluded_cpc_rows_apply": False,
        },
        source_run_ids=[approved_id, preflight["run_id"]],
        approved_id=approved_id,
        lifecycle_status="verified" if status == "ok" else "needs_attention",
        closed=status == "ok",
    )
    result["artifacts"].update(manifest)
    write_json(run_dir / "summary.json", result)
    mark_approved_applied(
        data_dir=data_dir,
        approved_id=approved_id,
        apply_run_id=run_id,
        task="ozon-price-cpc-growth-apply",
        status=status,
        run_manifest_path=manifest["manifest"],
        checksum=str(plan_summary["actions_checksum"]),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
