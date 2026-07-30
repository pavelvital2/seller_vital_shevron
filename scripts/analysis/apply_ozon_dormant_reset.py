#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import time
from typing import Any

from seller_agent.config import load_credentials
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.marketplaces.ozon.adapter import OzonSellerAdapter
from seller_agent.marketplaces.ozon.performance_adapter import OzonPerformanceAdapter
from seller_agent.reports.writer import ensure_dir, write_json
from seller_agent.safety.approvals import (
    apply_marker_for,
    assert_apply_not_repeated,
    mark_approved_applied,
)
from seller_agent.tasks.ozon_elastic_plan import ACTIVE, CANDIDATE, _fetch_action_group
from seller_agent.tasks.pricing_status import normalize_ozon_price_item
from seller_agent.tasks.status_preflight import run_status_preflight


ACTION_ID = "1977747"
MICRO_RUB = Decimal("1000000")


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0").replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _money(value: Any) -> str:
    return f"{_decimal(value).quantize(Decimal('0.01'))}"


def _rows_checksum(rows: list[dict[str, Any]]) -> str:
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _plan_dir(data_dir: Path, run_id: str) -> Path:
    matches = sorted((data_dir / "runs").glob(f"*/{run_id}"))
    if not matches:
        raise FileNotFoundError(f"plan not found: {run_id}")
    return matches[-1]


def _load_plan(plan_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = json.loads((plan_dir / "plan.json").read_text(encoding="utf-8"))
    summary = json.loads((plan_dir / "summary.json").read_text(encoding="utf-8"))
    if not isinstance(rows, list) or len(rows) != 138:
        raise RuntimeError("approved plan must contain exactly 138 rows")
    actual = _rows_checksum(rows)
    expected = str(summary.get("rows_checksum_sha256") or "")
    if actual != expected:
        raise RuntimeError(f"plan checksum mismatch: expected {expected}, got {actual}")
    groups_12 = [row for row in rows if str(row.get("decision_group")) in {"1", "2"}]
    if len(groups_12) != 108:
        raise RuntimeError("approved plan must contain exactly 108 group 1-2 rows")
    if any(
        _decimal(row.get("target_min_price")) != _decimal(row.get("current_min_price"))
        or str(row.get("target_min_price_for_auto_actions_enabled")) != "keep"
        for row in groups_12
    ):
        raise RuntimeError("group 1-2 minimum-price policy must preserve the approved floor")
    return rows, summary


def _prices(adapter: OzonSellerAdapter) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    rows = [normalize_ozon_price_item(row) for row in adapter.fetch_product_info_prices()]
    return rows, {
        str(row.get("offer_id") or ""): row
        for row in rows
        if str(row.get("offer_id") or "")
    }


def _campaign_bid(row: dict[str, Any]) -> Decimal:
    return _decimal(row.get("bid")) / MICRO_RUB


def _elastic_range(row: dict[str, Any]) -> tuple[Decimal, Decimal] | None:
    values = [
        _decimal(row.get(key))
        for key in ("price_min_elastic", "price_max_elastic")
        if _decimal(row.get(key)) > 0
    ]
    if not values:
        return None
    return min(values), max(values)


def _verify_action(
    adapter: OzonSellerAdapter,
    *,
    rows: list[dict[str, Any]],
    raw_dir: Path,
) -> list[dict[str, Any]]:
    active = _fetch_action_group(
        adapter,
        action_id=ACTION_ID,
        source_group=ACTIVE,
        raw_dir=raw_dir,
    )
    active_by_id = {str(row.get("product_id") or ""): row for row in active}
    result = []
    for row in rows:
        current = active_by_id.get(str(row["product_id"]))
        actual = _decimal((current or {}).get("current_action_price"))
        target = _decimal(row["target_action_price"])
        result.append(
            {
                "offer_id": row["offer_id"],
                "product_id": row["product_id"],
                "target_action_price": _money(target),
                "actual_action_price": _money(actual),
                "status": "ok" if current and actual == target else "warning",
            }
        )
    return result


def _pending_action_payload(
    rows: list[dict[str, Any]],
    active: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    active_by_id = {
        str(row.get("product_id") or ""): row
        for row in active
        if str(row.get("product_id") or "")
    }
    return [
        {
            "product_id": int(row["product_id"]),
            "action_price": float(_decimal(row["target_action_price"])),
        }
        for row in rows
        if _decimal((active_by_id.get(str(row["product_id"])) or {}).get("current_action_price"))
        != _decimal(row["target_action_price"])
    ]


def _safe_error(exc: Exception) -> str:
    return str(exc).replace("\n", " ")[:1000]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan-run-id", required=True)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--confirmed-by-user", action="store_true")
    args = parser.parse_args()
    if not args.confirmed_by_user:
        raise RuntimeError("apply requires --confirmed-by-user")

    data_dir = Path(args.data_dir)
    plan_dir = _plan_dir(data_dir, args.plan_run_id)
    rows, approved_summary = _load_plan(plan_dir)
    started = datetime.now()
    run_id = f"ozon_dormant_reset_apply_{started:%Y%m%dT%H%M%S}"
    run_dir = ensure_dir(data_dir / "runs" / started.date().isoformat() / run_id)
    raw_dir = ensure_dir(run_dir / "raw")
    processed_dir = ensure_dir(run_dir / "processed")
    assert_apply_not_repeated(
        data_dir=data_dir,
        approved_id=args.plan_run_id,
        current_run_id=run_id,
    )

    credentials = load_credentials()
    if not credentials.ozon_seller or not credentials.ozon_performance:
        raise RuntimeError("Ozon Seller/Performance credentials are unavailable")
    seller = OzonSellerAdapter(credentials.ozon_seller)
    performance = OzonPerformanceAdapter(credentials.ozon_performance)
    preflight = run_status_preflight(
        credentials=credentials,
        data_dir=data_dir,
        include_lk=False,
        marketplaces=("ozon",),
        include_catalog=False,
        include_ozon_performance=True,
    )
    if preflight["overall_status"] not in {"ok", "warning"}:
        raise RuntimeError(f"Ozon preflight failed: {preflight['overall_status']}")

    prices_before, price_by_offer = _prices(seller)
    write_json(raw_dir / "prices_before.json", prices_before)
    action_rows = [row for row in rows if str(row.get("target_action_id")) == ACTION_ID]
    min_rows = [row for row in rows if str(row.get("decision_group")) in {"1", "2"}]

    fresh_action_dir = ensure_dir(raw_dir / "fresh_action")
    active = _fetch_action_group(
        seller,
        action_id=ACTION_ID,
        source_group=ACTIVE,
        raw_dir=fresh_action_dir,
    )
    candidates = _fetch_action_group(
        seller,
        action_id=ACTION_ID,
        source_group=CANDIDATE,
        raw_dir=fresh_action_dir,
    )
    action_options: dict[str, list[tuple[Decimal, Decimal]]] = {}
    for item in [*active, *candidates]:
        value = _elastic_range(item)
        if value:
            action_options.setdefault(str(item.get("product_id") or ""), []).append(value)

    campaign_before = performance.fetch_campaign_products(str(rows[0]["cpc_campaign_id"]))
    write_json(raw_dir / "campaign_before.json", campaign_before)
    campaign_by_sku = {str(row.get("sku") or ""): row for row in campaign_before}

    drift: list[dict[str, Any]] = []
    for row in rows:
        current = price_by_offer.get(str(row["offer_id"]))
        if not current:
            drift.append({"offer_id": row["offer_id"], "reason": "missing_price_row"})
            continue
        if str(current.get("product_id") or "") != str(row["product_id"]):
            drift.append({"offer_id": row["offer_id"], "reason": "product_id_drift"})
            continue
        current_min = _decimal(current.get("min_price"))
        approved_min = _decimal(row["current_min_price"])
        target_min = _decimal(row.get("target_min_price") or approved_min)
        if (
            _decimal(current.get("price")) != _decimal(row["current_price"])
            or current_min not in {approved_min, target_min}
        ):
            drift.append(
                {
                    "offer_id": row["offer_id"],
                    "reason": "price_drift",
                    "approved_price": row["current_price"],
                    "fresh_price": _money(current.get("price")),
                    "approved_min_price": row["current_min_price"],
                    "fresh_min_price": _money(current.get("min_price")),
                }
            )
        target_action = _decimal(row["target_action_price"])
        if row in action_rows and not any(
            lower <= target_action <= upper
            for lower, upper in action_options.get(str(row["product_id"]), [])
        ):
            drift.append({"offer_id": row["offer_id"], "reason": "elastic_target_drift"})
        sku = str(row["ozon_sku"])
        campaign_row = campaign_by_sku.get(sku)
        approved_bid = _decimal(row["current_cpc_bid"])
        if campaign_row and _campaign_bid(campaign_row) != approved_bid:
            drift.append({"offer_id": row["offer_id"], "sku": sku, "reason": "cpc_bid_drift"})
        if not campaign_row and approved_bid != 0:
            drift.append({"offer_id": row["offer_id"], "sku": sku, "reason": "cpc_membership_drift"})
    write_json(
        processed_dir / "full_scope_drift.json",
        {"approved_rows": len(rows), "drift_count": len(drift), "drift": drift},
    )
    if drift:
        raise RuntimeError(f"full-scope drift-check failed for {len(drift)} rows")

    min_payload = []
    for row in min_rows:
        current = price_by_offer[str(row["offer_id"])]
        if _decimal(current.get("min_price")) == _decimal(row["target_min_price"]):
            continue
        min_payload.append(
            {
                "offer_id": str(row["offer_id"]),
                "price": _money(current.get("price")),
                "old_price": _money(current.get("old_price")),
                "min_price": _money(row["target_min_price"]),
                "currency_code": str(current.get("currency_code") or "RUB"),
                "min_price_for_auto_actions_enabled": True,
            }
        )
    write_json(raw_dir / "min_price_request.json", {"prices": min_payload})
    min_response = seller.import_product_prices(min_payload) if min_payload else {"result": []}
    write_json(raw_dir / "min_price_response.json", min_response)

    price_verify: list[dict[str, Any]] = []
    for attempt in range(1, 9):
        time.sleep(4 if attempt == 1 else 7)
        prices_after, after_by_offer = _prices(seller)
        price_verify = []
        for row in min_rows:
            current = after_by_offer.get(str(row["offer_id"]))
            before = price_by_offer[str(row["offer_id"])]
            checks = {
                "min_price": bool(current)
                and _decimal(current.get("min_price")) == _decimal(row["target_min_price"]),
                "price_unchanged": bool(current)
                and _decimal(current.get("price")) == _decimal(before.get("price")),
                "old_price_unchanged": bool(current)
                and _decimal(current.get("old_price")) == _decimal(before.get("old_price")),
            }
            price_verify.append(
                {
                    "offer_id": row["offer_id"],
                    "target_min_price": row["target_min_price"],
                    "actual_min_price": _money((current or {}).get("min_price")),
                    "status": "ok" if all(checks.values()) else "warning",
                    "checks": checks,
                }
            )
        write_json(processed_dir / f"min_price_verify_{attempt:02d}.json", price_verify)
        if all(row["status"] == "ok" for row in price_verify):
            write_json(raw_dir / "prices_after.json", prices_after)
            break
    if not price_verify or any(row["status"] != "ok" for row in price_verify):
        raise RuntimeError("minimum-price verify failed; Elastic and CPC were not changed")

    action_payload = _pending_action_payload(action_rows, active)
    write_json(raw_dir / "elastic_request.json", {"action_id": int(ACTION_ID), "products": action_payload})
    action_response = (
        seller.post(
            "/v1/actions/products/activate",
            {"action_id": int(ACTION_ID), "products": action_payload},
        )
        if action_payload
        else {"result": []}
    )
    write_json(raw_dir / "elastic_response.json", action_response)
    action_verify: list[dict[str, Any]] = []
    for attempt in range(1, 7):
        time.sleep(4 if attempt == 1 else 7)
        action_verify = _verify_action(
            seller,
            rows=action_rows,
            raw_dir=ensure_dir(raw_dir / f"elastic_verify_{attempt:02d}"),
        )
        write_json(processed_dir / f"elastic_verify_{attempt:02d}.json", action_verify)
        if all(row["status"] == "ok" for row in action_verify):
            break
    if not action_verify or any(row["status"] != "ok" for row in action_verify):
        raise RuntimeError("Elastic verify failed; CPC was not changed")

    changed_bid_rows = [
        row
        for row in rows
        if str(row["ozon_sku"]) in campaign_by_sku
        and _decimal(row["current_cpc_bid"]) != _decimal(row["target_cpc_bid"])
    ]
    bid_response: Any = None
    if changed_bid_rows:
        bid_response = performance.update_campaign_product_bids(
            str(rows[0]["cpc_campaign_id"]),
            [
                {
                    "sku": str(row["ozon_sku"]),
                    "bid": str(int(_decimal(row["target_cpc_bid"]) * MICRO_RUB)),
                }
                for row in changed_bid_rows
            ],
        )
    write_json(raw_dir / "cpc_existing_response.json", bid_response)

    missing_rows = [row for row in rows if str(row["ozon_sku"]) not in campaign_by_sku]
    missing_add_error = ""
    missing_add_response: Any = None
    if missing_rows:
        try:
            missing_add_response = performance.add_campaign_products(
                str(rows[0]["cpc_campaign_id"]),
                [
                    {
                        "sku": str(row["ozon_sku"]),
                        "bid": str(int(_decimal(row["target_cpc_bid"]) * MICRO_RUB)),
                    }
                    for row in missing_rows
                ],
            )
        except Exception as exc:  # noqa: BLE001 - classify once; do not retry writes.
            missing_add_error = _safe_error(exc)
    write_json(
        raw_dir / "cpc_missing_add_response.json",
        {"response": missing_add_response, "error": missing_add_error},
    )

    campaign_after = performance.fetch_campaign_products(str(rows[0]["cpc_campaign_id"]))
    write_json(raw_dir / "campaign_after.json", campaign_after)
    after_campaign_by_sku = {str(row.get("sku") or ""): row for row in campaign_after}
    cpc_verify = []
    for row in rows:
        current = after_campaign_by_sku.get(str(row["ozon_sku"]))
        actual = _campaign_bid(current) if current else Decimal("0")
        target = _decimal(row["target_cpc_bid"])
        cpc_verify.append(
            {
                "offer_id": row["offer_id"],
                "sku": row["ozon_sku"],
                "target_bid": _money(target),
                "actual_bid": _money(actual),
                "status": "ok" if current and actual == target else "warning",
                "was_missing_before": str(row["ozon_sku"]) not in campaign_by_sku,
            }
        )
    write_json(processed_dir / "cpc_verify.json", cpc_verify)

    cpc_failed = [row for row in cpc_verify if row["status"] != "ok"]
    overall_status = "ok" if not cpc_failed else "warning"
    summary = {
        "run_id": run_id,
        "started_at": started.isoformat(timespec="seconds"),
        "mode": "apply",
        "overall_status": overall_status,
        "approved_plan_run_id": args.plan_run_id,
        "approved_checksum": approved_summary["rows_checksum_sha256"],
        "min_price_verified": sum(row["status"] == "ok" for row in price_verify),
        "elastic_verified": sum(row["status"] == "ok" for row in action_verify),
        "cpc_verified": sum(row["status"] == "ok" for row in cpc_verify),
        "cpc_failed": len(cpc_failed),
        "cpc_added": sum(
            row["status"] == "ok" and row["was_missing_before"] for row in cpc_verify
        ),
        "cpc_distribution": dict(Counter(row["actual_bid"] for row in cpc_verify if row["status"] == "ok")),
        "artifacts": {
            "run_dir": str(run_dir),
            "report": str(run_dir / "report.md"),
            "summary": str(run_dir / "summary.json"),
            "drift": str(processed_dir / "full_scope_drift.json"),
            "min_price_verify": str(processed_dir / "min_price_verify_01.json"),
            "elastic_verify": str(processed_dir / "elastic_verify_01.json"),
            "cpc_verify": str(processed_dir / "cpc_verify.json"),
            "apply_marker": str(apply_marker_for(data_dir=data_dir, approved_id=args.plan_run_id)),
        },
    }
    report = [
        "# Ozon dormant inventory reset apply",
        "",
        f"- approved plan: `{args.plan_run_id}`;",
        f"- minimum prices verified: `{summary['min_price_verified']}/108`;",
        f"- Elastic verified: `{summary['elastic_verified']}/120`;",
        f"- CPC verified: `{summary['cpc_verified']}/138`;",
        f"- CPC newly added: `{summary['cpc_added']}/27`;",
        f"- CPC unresolved: `{summary['cpc_failed']}`;",
        f"- status: `{overall_status}`.",
        "",
        "Regular price and old_price were preserved for the 108 minimum-price updates.",
        "",
    ]
    (run_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    write_json(run_dir / "summary.json", summary)
    manifest = write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=summary,
        task="ozon-dormant-reset-apply",
        mode="apply",
        risk="high",
        marketplaces=["ozon"],
        inputs={"plan_run_id": args.plan_run_id, "confirmed_by_user": True},
        source_run_ids=[args.plan_run_id],
        approved_id=args.plan_run_id,
        lifecycle_status="verified" if overall_status == "ok" else "needs_attention",
        closed=overall_status == "ok",
    )
    summary["artifacts"]["manifest"] = manifest["manifest"]
    write_json(run_dir / "summary.json", summary)
    mark_approved_applied(
        data_dir=data_dir,
        approved_id=args.plan_run_id,
        apply_run_id=run_id,
        task="ozon-dormant-reset-apply",
        status=overall_status,
        run_manifest_path=manifest["manifest"],
        checksum=approved_summary["rows_checksum_sha256"],
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if overall_status == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
