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

from seller_agent.config import load_credentials
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.marketplaces.ozon.adapter import OzonSellerAdapter
from seller_agent.reports.writer import ensure_dir, write_json
from seller_agent.safety.approvals import (
    apply_marker_for,
    assert_apply_not_repeated,
    canonical_checksum,
    mark_approved_applied,
)
from seller_agent.tasks.pricing_status import normalize_ozon_price_item


def _money(value: object) -> Decimal:
    try:
        return Decimal(str(value or "0")).quantize(Decimal("0.01"))
    except InvalidOperation:
        return Decimal("0.00")


def _load_plan(plan_dir: Path) -> tuple[list[dict[str, object]], dict[str, object]]:
    summary = json.loads((plan_dir / "summary.json").read_text(encoding="utf-8"))
    with (plan_dir / "ozon_min_price_change_plan.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows: list[dict[str, object]] = list(csv.DictReader(handle))
    for row in rows:
        row["pack_qty"] = int(str(row["pack_qty"]))
    expected = str(summary.get("actions_checksum") or "")
    actual = canonical_checksum(rows)
    if actual != expected:
        raise RuntimeError(f"plan checksum mismatch: expected {expected}, got {actual}")
    return rows, summary


def _price_map(adapter: OzonSellerAdapter) -> tuple[list[dict[str, object]], dict[str, dict[str, object]]]:
    rows = [normalize_ozon_price_item(item) for item in adapter.fetch_product_info_prices()]
    by_offer = {
        str(row.get("offer_id") or "").strip(): row
        for row in rows
        if str(row.get("offer_id") or "").strip()
    }
    return rows, by_offer


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan-run-id", required=True)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--confirmed-by-user", action="store_true")
    args = parser.parse_args()
    if not args.confirmed_by_user:
        raise RuntimeError("apply requires --confirmed-by-user")

    data_dir = Path(args.data_dir)
    plan_dirs = sorted((data_dir / "runs").glob(f"*/{args.plan_run_id}"))
    if not plan_dirs:
        raise FileNotFoundError(f"plan not found: {args.plan_run_id}")
    plan_dir = plan_dirs[-1]
    plan_rows, plan_summary = _load_plan(plan_dir)
    if len(plan_rows) != int(plan_summary.get("rows") or 0):
        raise RuntimeError("plan row count mismatch")

    started = datetime.now()
    run_id = f"ozon_min_price_apply_pack_{started.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started.strftime("%Y-%m-%d") / run_id)
    approved_id = args.plan_run_id
    assert_apply_not_repeated(data_dir=data_dir, approved_id=approved_id, current_run_id=run_id)

    credentials = load_credentials()
    if not credentials.ozon_seller:
        raise RuntimeError("Ozon Seller API credentials are unavailable")
    ozon = OzonSellerAdapter(credentials.ozon_seller)
    before, by_offer = _price_map(ozon)
    write_json(run_dir / "before_prices.json", before)

    unchanged: list[tuple[dict[str, object], dict[str, object]]] = []
    drifted: list[dict[str, object]] = []
    for row in plan_rows:
        offer_id = str(row["offer_id"])
        current = by_offer.get(offer_id)
        if not current:
            drifted.append({"offer_id": offer_id, "reason": "missing_in_fresh_snapshot"})
            continue
        checks = {
            "product_id": str(current.get("product_id") or "") == str(row["product_id"]),
            "price": _money(current.get("price")) == _money(row["current_seller_price"]),
            "old_price": _money(current.get("old_price")) == _money(row["current_old_price"]),
            "min_price": _money(current.get("min_price")) == _money(row["current_min_price"]),
        }
        if all(checks.values()):
            unchanged.append((row, current))
        else:
            drifted.append({
                "offer_id": offer_id,
                "reason": "price_state_drift",
                "checks": checks,
                "approved": {
                    "price": row["current_seller_price"],
                    "old_price": row["current_old_price"],
                    "min_price": row["current_min_price"],
                },
                "fresh": {
                    "price": current.get("price"),
                    "old_price": current.get("old_price"),
                    "min_price": current.get("min_price"),
                },
            })
    checksum = str(plan_summary["actions_checksum"])
    write_json(run_dir / "drift_check.json", {
        "approved_rows": len(plan_rows),
        "fresh_rows": len(before),
        "unchanged_rows": len(unchanged),
        "drifted_rows": len(drifted),
        "drifted": drifted,
        "plan_checksum": checksum,
    })
    if not unchanged:
        raise RuntimeError("no unchanged approved rows to apply")

    payload = [{
        "offer_id": str(row["offer_id"]),
        "price": f"{_money(current.get('price')):.2f}",
        "old_price": f"{_money(current.get('old_price')):.2f}",
        "min_price": f"{_money(row['target_min_price']):.2f}",
        "currency_code": str(current.get("currency_code") or "RUB"),
        "min_price_for_auto_actions_enabled": True,
    } for row, current in unchanged]
    write_json(run_dir / "ozon_product_import_prices_request.json", {"prices": payload})
    response = ozon.import_product_prices(payload)
    write_json(run_dir / "ozon_product_import_prices_response.json", response)
    response_rows = response.get("result") if isinstance(response, dict) else []
    response_rows = response_rows if isinstance(response_rows, list) else []
    response_by_offer = {
        str(item.get("offer_id") or ""): item
        for item in response_rows
        if isinstance(item, dict)
    }
    accepted: list[str] = []
    rejected: list[dict[str, object]] = []
    for item in payload:
        result = response_by_offer.get(item["offer_id"])
        if result and result.get("updated") is True and not result.get("errors"):
            accepted.append(item["offer_id"])
        else:
            rejected.append({"offer_id": item["offer_id"], "response": result or {"reason": "missing_response_row"}})
    write_json(run_dir / "response_classification.json", {
        "submitted": len(payload), "accepted": len(accepted), "rejected": len(rejected), "rejected_rows": rejected,
    })

    accepted_set = set(accepted)
    targets = {item["offer_id"]: item for item in payload if item["offer_id"] in accepted_set}
    verify_rows: list[dict[str, object]] = []
    after: list[dict[str, object]] = []
    for attempt in range(1, 7):
        time.sleep(5 if attempt == 1 else 10)
        after, after_by_offer = _price_map(ozon)
        verify_rows = []
        for offer_id, target in targets.items():
            current = after_by_offer.get(offer_id)
            checks = {
                "found": bool(current),
                "min_price": bool(current) and _money(current.get("min_price")) == _money(target["min_price"]),
                "price_unchanged": bool(current) and _money(current.get("price")) == _money(target["price"]),
                "old_price_unchanged": bool(current) and _money(current.get("old_price")) == _money(target["old_price"]),
            }
            verify_rows.append({
                "offer_id": offer_id,
                "status": "ok" if all(checks.values()) else "warning",
                "checks": checks,
                "actual": {
                    "price": current.get("price") if current else None,
                    "old_price": current.get("old_price") if current else None,
                    "min_price": current.get("min_price") if current else None,
                },
                "target": {"price": target["price"], "old_price": target["old_price"], "min_price": target["min_price"]},
            })
        write_json(run_dir / f"verify_attempt_{attempt:02d}.json", {"attempt": attempt, "rows": verify_rows})
        if verify_rows and all(row["status"] == "ok" for row in verify_rows):
            break
    write_json(run_dir / "after_prices.json", after)
    verify_failed = [row for row in verify_rows if row["status"] != "ok"]
    verified = len(verify_rows) - len(verify_failed)
    status = "ok" if not drifted and not rejected and not verify_failed and verified == len(plan_rows) else "warning"
    write_json(run_dir / "verify_summary.json", {
        "status": status, "submitted": len(payload), "accepted": len(accepted), "rejected": len(rejected),
        "verified": verified, "verify_failed": len(verify_failed), "verify_failed_rows": verify_failed,
    })

    result = {
        "run_id": run_id,
        "started_at": started.isoformat(timespec="seconds"),
        "mode": "apply",
        "overall_status": status,
        "approved_plan_run_id": approved_id,
        "approved_id": approved_id,
        "plan_checksum": checksum,
        "summary": {
            "approved_rows": len(plan_rows), "fresh_rows": len(before), "unchanged_rows": len(unchanged),
            "drifted_rows": len(drifted), "submitted_rows": len(payload), "accepted_rows": len(accepted),
            "rejected_rows": len(rejected), "verified_rows": verified, "verify_failed_rows": len(verify_failed),
            "targets": dict(Counter(f"pack{row['pack_qty']}:{row['target_min_price']}" for row, _ in unchanged)),
            "elastic_or_actions_changed": False,
        },
        "artifacts": {
            "run_dir": str(run_dir), "before_prices": str(run_dir / "before_prices.json"),
            "drift_check": str(run_dir / "drift_check.json"), "request": str(run_dir / "ozon_product_import_prices_request.json"),
            "response": str(run_dir / "ozon_product_import_prices_response.json"), "after_prices": str(run_dir / "after_prices.json"),
            "verify": str(run_dir / "verify_summary.json"), "report": str(run_dir / "ozon_min_price_apply_report.md"),
            "summary": str(run_dir / "summary.json"), "apply_marker": str(apply_marker_for(data_dir=data_dir, approved_id=approved_id)),
        },
    }
    report = [
        "# Ozon minimum price apply", "", f"Run ID: `{run_id}`.", "",
        f"- approved plan: `{approved_id}`;", f"- approved rows: `{len(plan_rows)}`;",
        f"- drifted/skipped: `{len(drifted)}`;", f"- submitted: `{len(payload)}`;",
        f"- API accepted: `{len(accepted)}`;", f"- API rejected: `{len(rejected)}`;",
        f"- verified: `{verified}`;", f"- verify failed: `{len(verify_failed)}`;",
        "- Elastic/actions changed: `false`.", "",
        "Only approved min_price targets changed. Current price and old_price were preserved and verified.", "",
    ]
    (run_dir / "ozon_min_price_apply_report.md").write_text("\n".join(report), encoding="utf-8")
    write_json(run_dir / "summary.json", result)
    manifest = write_summary_run_manifest(
        data_dir=data_dir, run_dir=run_dir, summary=result, task="ozon-min-price-apply", mode="apply", risk="high",
        marketplaces=["ozon"], inputs={"plan_run_id": approved_id, "confirmed_by_user": True, "changes_scope": "min_price_only"},
        source_run_ids=[approved_id], approved_id=approved_id,
        lifecycle_status="verified" if status == "ok" else "needs_attention", closed=status == "ok",
    )
    result["artifacts"]["run_manifest"] = manifest["manifest"]
    write_json(run_dir / "summary.json", result)
    mark_approved_applied(
        data_dir=data_dir, approved_id=approved_id, apply_run_id=run_id, task="ozon-min-price-apply", status=status,
        run_manifest_path=manifest["manifest"], checksum=checksum,
    )
    print(json.dumps({"run_id": run_id, "status": status, **result["summary"], "report": result["artifacts"]["report"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
