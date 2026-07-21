#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
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
from seller_agent.tasks.status_preflight import run_status_preflight
from seller_agent.tasks.wb_promotion_bids_apply import (
    _current_bids_from_campaigns,
    _group_bid_payload,
    _verify_applied_rows,
)


APPROVED_ACTIONS = {
    "scale_strong",
    "scale_proven",
    "visibility_test",
    "reduce_waste",
}
EXPECTED_INCREASE_ROWS = 88
EXPECTED_REDUCE_ROWS = 3
EXPECTED_CURRENT_BID_SUM = Decimal("108.07")
EXPECTED_TARGET_BID_SUM = Decimal("174.65")
MIN_STOCK_FOR_INCREASE = 8
MIN_PROJECTED_MARGIN_PER_PIECE = Decimal("50")


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value).strip().replace(",", ".")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise RuntimeError(f"invalid decimal value: {value}") from exc


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(rows[0]) if rows else ["nm_id"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _sum(rows: list[dict[str, Any]], field: str) -> Decimal:
    return sum((_decimal(row[field]) for row in rows), Decimal("0")).quantize(Decimal("0.01"))


def _source_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_approved_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in _read_csv(path):
        action = str(source.get("recommended_action") or "").strip()
        if action not in APPROVED_ACTIONS:
            continue
        current_bid = _decimal(source.get("current_bid"))
        target_bid = _decimal(source.get("target_bid"))
        projected_margin = _decimal(source.get("projected_margin_per_piece"))
        stock = int(_decimal(source.get("stock")))
        nm_id = str(source.get("nm_id") or "").strip()
        advert_id = str(source.get("advert_id") or "").strip()
        name = str(source.get("name") or "").strip()
        if not nm_id or not advert_id:
            raise RuntimeError("approved row is missing advert_id or nm_id")
        if target_bid < Decimal("1.00"):
            raise RuntimeError(f"target bid below WB safe minimum: {advert_id}/{nm_id}")
        if action == "reduce_waste" and target_bid >= current_bid:
            raise RuntimeError(f"reduce row does not reduce bid: {advert_id}/{nm_id}")
        if action != "reduce_waste" and target_bid <= current_bid:
            raise RuntimeError(f"increase row does not increase bid: {advert_id}/{nm_id}")
        if action != "reduce_waste" and stock < MIN_STOCK_FOR_INCREASE:
            raise RuntimeError(f"approved increase has stock below guard: {advert_id}/{nm_id}")
        if action != "reduce_waste" and projected_margin < MIN_PROJECTED_MARGIN_PER_PIECE:
            raise RuntimeError(f"approved increase is below margin guard: {advert_id}/{nm_id}")
        if action != "reduce_waste" and "позывн" in name.lower():
            raise RuntimeError(f"callsign product is present in approved increases: {advert_id}/{nm_id}")
        rows.append(
            {
                **source,
                "advert_id": advert_id,
                "nm_id": nm_id,
                "current_bid": str(current_bid),
                "target_bid": str(target_bid),
                "current_bid_place": "search",
                "current_bid_source": "current_bid_api",
                "current_bid_kopecks": int(current_bid * 100),
                "target_bid_kopecks": int(target_bid * 100),
                "approved_action": action,
            }
        )

    identities = {(row["advert_id"], row["nm_id"], row["current_bid_place"]) for row in rows}
    if len(identities) != len(rows):
        raise RuntimeError("approved package contains duplicate campaign/product rows")
    increase = [row for row in rows if row["approved_action"] != "reduce_waste"]
    reduce = [row for row in rows if row["approved_action"] == "reduce_waste"]
    if len(increase) != EXPECTED_INCREASE_ROWS or len(reduce) != EXPECTED_REDUCE_ROWS:
        raise RuntimeError(
            f"approved package count mismatch: increase={len(increase)}, reduce={len(reduce)}"
        )
    if _sum(rows, "current_bid") != EXPECTED_CURRENT_BID_SUM:
        raise RuntimeError("approved package current bid sum mismatch")
    if _sum(rows, "target_bid") != EXPECTED_TARGET_BID_SUM:
        raise RuntimeError("approved package target bid sum mismatch")
    return sorted(rows, key=lambda row: (int(row["advert_id"]), int(row["nm_id"])))


def _fresh_prices(rows: list[dict[str, Any]]) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    for row in rows:
        nm_id = str(row.get("nmID") or row.get("nm_id") or "")
        sizes = row.get("sizes") or []
        size = sizes[0] if sizes and isinstance(sizes[0], dict) else {}
        value = size.get("discountedPrice") or size.get("clubDiscountedPrice")
        if nm_id and value not in (None, ""):
            result[nm_id] = _decimal(value)
    return result


def _write_report(path: Path, result: dict[str, Any]) -> None:
    summary = result["summary"]
    lines = [
        "# Применение WB CPC ставок с маржой 50 рублей",
        "",
        f"- Статус: `{result['overall_status']}`",
        f"- Согласованный план: `{result['approved_plan_run_id']}`",
        f"- Режим: `{result['mode']}`",
        f"- Строк в согласованном пакете: `{summary['approved_rows']}`",
        f"- Строк без drift: `{summary['unchanged_rows']}`",
        f"- Применено: `{summary['applied_rows']}`",
        f"- Пропущено из-за drift: `{summary['drift_rows']}`",
        f"- Сумма ставок: `{summary['current_bid_sum']} -> {summary['target_bid_sum']}`",
        f"- Verify: `{summary['verify_status']}`, расхождений `{summary['verify_mismatches']}`",
        "",
        "Изменившиеся после согласования строки автоматически не применяются.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--plan-run-id", required=True)
    parser.add_argument("--confirmed-by-user", action="store_true")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--wait-seconds", type=int, default=45)
    args = parser.parse_args()
    if not args.preflight_only and not args.confirmed_by_user:
        raise RuntimeError("explicit owner confirmation is required")

    credentials = load_credentials()
    if not credentials.wb:
        raise RuntimeError("missing WB API token")
    matches = sorted((args.data_dir / "runs").glob(f"*/{args.plan_run_id}"))
    if not matches:
        raise RuntimeError(f"approved plan not found: {args.plan_run_id}")
    plan_dir = matches[-1]
    plan_path = plan_dir / "wb_promotion_margin50_recommendations.csv"
    report_path = plan_dir / "wb_promotion_margin50_report.md"
    if not plan_path.exists() or not report_path.exists():
        raise RuntimeError("approved margin-aware package is incomplete")
    approved_rows = parse_approved_rows(plan_path)
    approved_id = f"{args.plan_run_id}:margin50-exact-bids"
    if not args.preflight_only:
        assert_apply_not_repeated(data_dir=args.data_dir, approved_id=approved_id)
    checksum = canonical_checksum(
        {
            "approved_id": approved_id,
            "source_csv_sha256": _source_sha256(plan_path),
            "source_report_sha256": _source_sha256(report_path),
            "rows": approved_rows,
        }
    )

    started = datetime.now()
    prefix = "wb_margin50_bids_preflight" if args.preflight_only else "wb_margin50_bids_apply"
    run_id = f"{prefix}_{started.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(args.data_dir / "runs" / started.strftime("%Y-%m-%d") / run_id)
    raw_dir = ensure_dir(run_dir / "raw")
    processed_dir = ensure_dir(run_dir / "processed")
    _write_csv(processed_dir / "approved_rows.csv", approved_rows)
    write_json(
        processed_dir / "approved_package.json",
        {
            "approved_id": approved_id,
            "approved_checksum": checksum,
            "source_plan_run_id": args.plan_run_id,
            "source_csv_sha256": _source_sha256(plan_path),
            "source_report_sha256": _source_sha256(report_path),
            "rows_count": len(approved_rows),
        },
    )

    preflight = run_status_preflight(credentials=credentials, data_dir=args.data_dir, include_lk=False)
    if preflight["overall_status"] not in {"ok", "warning"}:
        raise RuntimeError(f"preflight is not ok: {preflight['overall_status']}")

    promotion = WbPromotionAdapter(credentials.wb)
    advert_ids = sorted({int(row["advert_id"]) for row in approved_rows})
    campaigns_before = promotion.fetch_campaigns(
        ids=advert_ids, statuses=[4, 9, 11], payment_type="cpc"
    )
    write_json(raw_dir / "campaigns_before.json", {"adverts": campaigns_before})
    current_bids = _current_bids_from_campaigns(campaigns_before)

    nm_ids = sorted({int(row["nm_id"]) for row in approved_rows})
    analytics = WbAnalyticsAdapter(credentials.wb)
    stock_rows = analytics.fetch_wb_warehouse_stocks(nm_ids=nm_ids)
    write_json(raw_dir / "stocks_before.json", {"items": stock_rows})
    stocks: dict[str, int] = {}
    for item in stock_rows:
        nm_id = str(item.get("nmId") or "")
        stocks[nm_id] = stocks.get(nm_id, 0) + int(item.get("quantity") or 0)

    price_rows = WbPricesAdapter(credentials.wb).fetch_goods_prices()
    write_json(raw_dir / "prices_before.json", {"items": price_rows})
    prices = _fresh_prices(price_rows)

    unchanged: list[dict[str, Any]] = []
    drift: list[dict[str, Any]] = []
    for row in approved_rows:
        key = (row["advert_id"], row["nm_id"], row["current_bid_place"])
        actual_bid = current_bids.get(key)
        expected_bid = _decimal(row["current_bid"])
        actual_stock = stocks.get(row["nm_id"], 0)
        actual_price = prices.get(row["nm_id"])
        expected_price = _decimal(row["current_price"])
        output = {
            **row,
            "fresh_current_bid": str(actual_bid or ""),
            "fresh_stock": actual_stock,
            "fresh_price": str(actual_price or ""),
        }
        reason = ""
        if actual_bid != expected_bid:
            reason = "current_bid_changed_or_membership_missing"
        elif actual_price != expected_price:
            reason = "current_price_changed_or_missing"
        elif row["approved_action"] != "reduce_waste" and actual_stock < MIN_STOCK_FOR_INCREASE:
            reason = "fresh_stock_below_8"
        if reason:
            output["drift_reason"] = reason
            drift.append(output)
        else:
            unchanged.append(output)

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

    response: dict[str, Any] = {}
    applied_rows: list[dict[str, Any]] = []
    verify = {"status": "not_run", "checked_rows": 0, "mismatches": []}
    if not args.preflight_only:
        if not unchanged:
            raise RuntimeError("no unchanged approved rows remain after fresh drift-check")
        payload = _group_bid_payload(unchanged)
        write_json(processed_dir / "update_payload.json", {"bids": payload})
        response = promotion.update_bids(payload)
        write_json(raw_dir / "update_response.json", response)
        applied_rows = unchanged
        if args.wait_seconds > 0:
            time.sleep(args.wait_seconds)
        campaigns_after = promotion.fetch_campaigns(
            ids=advert_ids, statuses=[4, 9, 11], payment_type="cpc"
        )
        write_json(raw_dir / "campaigns_after.json", {"adverts": campaigns_after})
        verify = _verify_applied_rows(campaigns=campaigns_after, apply_rows=applied_rows)

    overall_status = "ok"
    if drift or (not args.preflight_only and verify["status"] != "ok"):
        overall_status = "warning"
    if not args.preflight_only and verify["status"] != "ok":
        overall_status = "error"
    summary = {
        "approved_rows": len(approved_rows),
        "unchanged_rows": len(unchanged),
        "drift_rows": len(drift),
        "applied_rows": len(applied_rows),
        "current_bid_sum": str(_sum(applied_rows or unchanged, "current_bid")),
        "target_bid_sum": str(_sum(applied_rows or unchanged, "target_bid")),
        "verify_status": verify["status"],
        "verify_mismatches": len(verify["mismatches"]),
    }
    result = {
        "run_id": run_id,
        "started_at": started.isoformat(timespec="seconds"),
        "mode": "dry_run" if args.preflight_only else "apply",
        "overall_status": overall_status,
        "approved_plan_run_id": args.plan_run_id,
        "approved_id": approved_id,
        "approved_checksum": checksum,
        "preflight": {"run_id": preflight["run_id"], "status": preflight["overall_status"]},
        "summary": summary,
        "update_response": response,
        "verify": verify,
        "artifacts": {
            "run_dir": str(run_dir),
            "report": str(run_dir / "result.md"),
            "summary": str(run_dir / "summary.json"),
            "approved_package": str(processed_dir / "approved_package.json"),
            "drift_check": str(processed_dir / "drift_check.json"),
            "unchanged_rows": str(processed_dir / "unchanged_rows.csv"),
            "drift_rows": str(processed_dir / "drift_rows.csv"),
            "run_manifest": str(run_dir / "manifest.json"),
            "apply_marker": str(apply_marker_for(data_dir=args.data_dir, approved_id=approved_id)),
        },
    }
    write_json(run_dir / "summary.json", result)
    _write_report(run_dir / "result.md", result)
    manifest = write_summary_run_manifest(
        data_dir=args.data_dir,
        run_dir=run_dir,
        summary=result,
        task="wb-margin50-bids-preflight" if args.preflight_only else "wb-margin50-bids-apply",
        mode="dry_run" if args.preflight_only else "apply",
        risk="normal" if args.preflight_only else "high",
        marketplaces=["wb"],
        inputs={
            "plan_run_id": args.plan_run_id,
            "confirmed_by_user": args.confirmed_by_user,
            "preflight_only": args.preflight_only,
            "wait_seconds": args.wait_seconds,
        },
        lifecycle_status="pending_review" if args.preflight_only else (
            "verified" if verify["status"] == "ok" else "needs_attention"
        ),
        closed=not args.preflight_only and verify["status"] == "ok",
    )
    result["artifacts"].update(manifest)
    write_json(run_dir / "summary.json", result)
    if not args.preflight_only:
        mark_approved_applied(
            data_dir=args.data_dir,
            approved_id=approved_id,
            apply_run_id=run_id,
            task="wb-margin50-bids-apply",
            status=overall_status,
            run_manifest_path=manifest["manifest"],
            checksum=checksum,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if overall_status == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
