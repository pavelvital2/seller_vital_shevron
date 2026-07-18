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
from seller_agent.marketplaces.wb.promotion_adapter import WbPromotionAdapter
from seller_agent.reports.writer import ensure_dir, write_json
from seller_agent.safety.approvals import (
    apply_marker_for,
    assert_apply_not_repeated,
    canonical_checksum,
    mark_approved_applied,
)
from seller_agent.tasks.status_preflight import run_status_preflight
from seller_agent.tasks.wb_promotion_bids_apply import (
    _current_bids_from_campaigns,
    _group_bid_payload,
    _verify_applied_rows,
)


APPROVED_SEGMENTS = {
    "scale_strong",
    "scale_proven",
    "visibility_test",
    "launch_priority",
    "launch_discovery",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def decimal_value(value: Any) -> Decimal:
    try:
        return Decimal(str(value).strip().replace(",", ".")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise RuntimeError(f"invalid decimal value: {value}") from exc


def parse_approved_rows(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        if row.get("Первая волна") != "да" or row.get("Сегмент") not in APPROVED_SEGMENTS:
            continue
        nm_id = str(row.get("nmID") or "").strip()
        raw_entries = [item.strip() for item in str(row.get("План ставок") or "").split(";") if item.strip()]
        entries = [item for item in raw_entries if "->" in item]
        if row.get("Сегмент") in {"launch_priority", "launch_discovery"} and not entries:
            continue
        if not nm_id or not entries:
            raise RuntimeError(f"approved existing-bid row has no exact bid plan: nmID={nm_id}")
        for entry in entries:
            advert_id, values = entry.split(":", 1)
            current_text, target_text = values.split("->", 1)
            current_bid = decimal_value(current_text)
            target_bid = decimal_value(target_text)
            if target_bid <= current_bid:
                raise RuntimeError(f"target bid must be greater than current bid: {entry}")
            result.append(
                {
                    "advert_id": advert_id,
                    "nm_id": nm_id,
                    "name": row.get("Товар", ""),
                    "segment": row.get("Сегмент", ""),
                    "current_bid": str(current_bid),
                    "target_bid": str(target_bid),
                    "current_bid_place": "search",
                    "current_bid_source": "current_bid_api",
                    "target_bid_kopecks": int(target_bid * 100),
                    "current_bid_kopecks": int(current_bid * 100),
                }
            )
    identities = {(row["advert_id"], row["nm_id"], row["current_bid_place"]) for row in result}
    if len(identities) != len(result):
        raise RuntimeError("approved existing-bid package contains duplicate campaign/product rows")
    return sorted(result, key=lambda row: (int(row["advert_id"]), int(row["nm_id"])))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(rows[0]) if rows else ["nm_id"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def sum_field(rows: list[dict[str, Any]], field: str) -> str:
    return str(sum((decimal_value(row[field]) for row in rows), Decimal("0")).quantize(Decimal("0.01")))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--plan-run-id", required=True)
    parser.add_argument("--confirmed-by-user", action="store_true")
    parser.add_argument("--wait-seconds", type=int, default=45)
    args = parser.parse_args()
    if not args.confirmed_by_user:
        raise RuntimeError("explicit owner confirmation is required")

    credentials = load_credentials()
    if not credentials.wb:
        raise RuntimeError("missing WB API token")
    plan_matches = sorted((args.data_dir / "runs").glob(f"*/{args.plan_run_id}"))
    if not plan_matches:
        raise RuntimeError(f"portfolio plan not found: {args.plan_run_id}")
    plan_dir = plan_matches[-1]
    plan_path = plan_dir / "wb_portfolio_growth_plan_wave1.csv"
    report_path = plan_dir / "report.html"
    if not plan_path.exists() or not report_path.exists():
        raise RuntimeError("approved portfolio CSV/HTML package is incomplete")
    approved_rows = parse_approved_rows(read_csv(plan_path))
    approved_id = f"{args.plan_run_id}:existing-bids"
    assert_apply_not_repeated(data_dir=args.data_dir, approved_id=approved_id)
    approved_checksum = canonical_checksum(
        {
            "approved_id": approved_id,
            "rows": approved_rows,
            "source_csv_sha": canonical_checksum(plan_path.read_bytes().hex()),
            "source_report_sha": canonical_checksum(report_path.read_bytes().hex()),
        }
    )

    started_at = datetime.now()
    run_id = f"wb_portfolio_existing_bids_apply_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(args.data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    raw_dir = ensure_dir(run_dir / "raw")
    processed_dir = ensure_dir(run_dir / "processed")
    write_csv(processed_dir / "approved_rows.csv", approved_rows)
    write_json(
        processed_dir / "approved_package.json",
        {
            "approved_id": approved_id,
            "approved_checksum": approved_checksum,
            "source_plan_run_id": args.plan_run_id,
            "rows_count": len(approved_rows),
        },
    )

    preflight = run_status_preflight(credentials=credentials, data_dir=args.data_dir, include_lk=False)
    if preflight["overall_status"] not in {"ok", "warning"}:
        raise RuntimeError(f"preflight is not ok: {preflight['overall_status']}")

    wb = WbPromotionAdapter(credentials.wb)
    advert_ids = sorted({int(row["advert_id"]) for row in approved_rows})
    fresh_campaigns = wb.fetch_campaigns(ids=advert_ids, statuses=[4, 9, 11], payment_type="cpc")
    write_json(raw_dir / "campaigns_before_apply.json", {"adverts": fresh_campaigns})
    current_bids = _current_bids_from_campaigns(fresh_campaigns)

    analytics = WbAnalyticsAdapter(credentials.wb)
    nm_ids = sorted({int(row["nm_id"]) for row in approved_rows})
    fresh_stock_rows = analytics.fetch_wb_warehouse_stocks(nm_ids=nm_ids)
    write_json(raw_dir / "stocks_before_apply.json", {"items": fresh_stock_rows})
    stocks: dict[str, int] = {}
    for item in fresh_stock_rows:
        nm_id = str(item.get("nmId") or "")
        stocks[nm_id] = stocks.get(nm_id, 0) + int(item.get("quantity") or 0)

    unchanged: list[dict[str, Any]] = []
    drift: list[dict[str, Any]] = []
    for row in approved_rows:
        key = (row["advert_id"], row["nm_id"], row["current_bid_place"])
        actual_bid = current_bids.get(key)
        expected_bid = decimal_value(row["current_bid"])
        stock = stocks.get(row["nm_id"], 0)
        output = {**row, "fresh_current_bid": str(actual_bid or ""), "fresh_stock": stock}
        if actual_bid != expected_bid:
            output["drift_reason"] = "current_bid_changed_or_membership_missing"
            drift.append(output)
        elif stock < 4:
            output["drift_reason"] = "fresh_stock_below_4"
            drift.append(output)
        else:
            unchanged.append(output)
    write_csv(processed_dir / "unchanged_rows.csv", unchanged)
    write_csv(processed_dir / "drift_rows.csv", drift)
    write_json(
        processed_dir / "drift_check.json",
        {
            "approved_rows": len(approved_rows),
            "unchanged_rows": len(unchanged),
            "drift_rows": len(drift),
        },
    )
    if not unchanged:
        raise RuntimeError("no unchanged approved rows remain after fresh drift-check")

    payload = _group_bid_payload(unchanged)
    write_json(processed_dir / "update_payload.json", {"bids": payload})
    response = wb.update_bids(payload)
    write_json(raw_dir / "update_response.json", response)
    if args.wait_seconds > 0:
        time.sleep(args.wait_seconds)

    campaigns_after = wb.fetch_campaigns(ids=advert_ids, statuses=[4, 9, 11], payment_type="cpc")
    write_json(raw_dir / "campaigns_after_apply.json", {"adverts": campaigns_after})
    verify = _verify_applied_rows(campaigns=campaigns_after, apply_rows=unchanged)
    result_status = "ok" if verify["status"] == "ok" and not drift else "warning"
    if verify["status"] != "ok":
        result_status = "error"

    summary = {
        "approved_rows": len(approved_rows),
        "applied_rows": len(unchanged),
        "drift_rows": len(drift),
        "current_bid_sum": sum_field(unchanged, "current_bid"),
        "target_bid_sum": sum_field(unchanged, "target_bid"),
        "verify_status": verify["status"],
        "verify_mismatches": len(verify["mismatches"]),
    }
    artifacts = {
        "run_dir": str(run_dir),
        "report": str(run_dir / "result.md"),
        "summary": str(run_dir / "summary.json"),
        "approved_package": str(processed_dir / "approved_package.json"),
        "drift_check": str(processed_dir / "drift_check.json"),
        "applied_rows": str(processed_dir / "unchanged_rows.csv"),
        "drift_rows": str(processed_dir / "drift_rows.csv"),
        "update_payload": str(processed_dir / "update_payload.json"),
        "campaigns_after": str(raw_dir / "campaigns_after_apply.json"),
        "apply_marker": str(apply_marker_for(data_dir=args.data_dir, approved_id=approved_id)),
        "run_manifest": str(run_dir / "manifest.json"),
    }
    result = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "apply",
        "overall_status": result_status,
        "approved_id": approved_id,
        "approved_checksum": approved_checksum,
        "preflight": {"run_id": preflight["run_id"], "status": preflight["overall_status"]},
        "summary": summary,
        "verify": verify,
        "artifacts": artifacts,
    }
    write_json(run_dir / "summary.json", result)
    lines = [
        "# WB Portfolio Existing Bids Apply",
        "",
        f"- status: `{result_status}`",
        f"- approved: `{len(approved_rows)}`",
        f"- applied: `{len(unchanged)}`",
        f"- drift: `{len(drift)}`",
        f"- bids sum: `{summary['current_bid_sum']} -> {summary['target_bid_sum']}`",
        f"- verify: `{verify['status']}`, mismatches `{len(verify['mismatches'])}`",
    ]
    (run_dir / "result.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest_paths = write_summary_run_manifest(
        data_dir=args.data_dir,
        run_dir=run_dir,
        summary=result,
        task="wb-portfolio-existing-bids-apply",
        mode="apply",
        risk="high",
        marketplaces=["wb"],
        inputs={"plan_run_id": args.plan_run_id, "confirmed_by_user": True},
        lifecycle_status="verified" if verify["status"] == "ok" else "needs_attention",
        closed=verify["status"] == "ok",
    )
    mark_approved_applied(
        data_dir=args.data_dir,
        approved_id=approved_id,
        apply_run_id=run_id,
        task="wb-portfolio-existing-bids-apply",
        status=result_status,
        run_manifest_path=manifest_paths["manifest"],
        checksum=approved_checksum,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if verify["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
