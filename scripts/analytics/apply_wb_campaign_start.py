#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import time
from typing import Any

from seller_agent.config import load_credentials
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.marketplaces.wb.promotion_adapter import WbPromotionAdapter
from seller_agent.reports.writer import ensure_dir, write_json
from seller_agent.safety.approvals import (
    apply_marker_for,
    assert_apply_not_repeated,
    canonical_checksum,
    mark_approved_applied,
)


EXPECTED_CAMPAIGN_ID = 36269574
EXPECTED_NAME = "СВО за клик от 01.05.2026"
EXPECTED_PRODUCTS = 50
APPROVED_ID = "owner-chat-20260729-wb-campaign-36269574-start-all50-unchanged"


def _campaign_snapshot(campaign: dict[str, Any]) -> dict[str, Any]:
    settings = campaign.get("settings") or {}
    products = []
    for row in campaign.get("nm_settings") or []:
        bids = row.get("bids_kopecks") or {}
        products.append(
            {
                "nm_id": int(row.get("nm_id") or row.get("nmId") or 0),
                "search_bid_kopecks": int(bids.get("search") or 0),
                "recommendations_bid_kopecks": int(bids.get("recommendations") or 0),
            }
        )
    products.sort(key=lambda row: row["nm_id"])
    return {
        "campaign_id": int(campaign.get("id") or 0),
        "name": str(settings.get("name") or ""),
        "status": int(campaign.get("status") or 0),
        "payment_type": str(settings.get("payment_type") or ""),
        "bid_type": str(campaign.get("bid_type") or ""),
        "placements": settings.get("placements") or {},
        "products": products,
    }


def _load_campaign(adapter: WbPromotionAdapter) -> dict[str, Any]:
    rows = adapter.fetch_campaigns(
        ids=[EXPECTED_CAMPAIGN_ID],
        statuses=[9, 11],
        payment_type="cpc",
    )
    matches = [row for row in rows if int(row.get("id") or 0) == EXPECTED_CAMPAIGN_ID]
    if len(matches) != 1:
        raise RuntimeError(f"expected one campaign {EXPECTED_CAMPAIGN_ID}, got {len(matches)}")
    return matches[0]


def _assert_before(snapshot: dict[str, Any]) -> None:
    if snapshot["campaign_id"] != EXPECTED_CAMPAIGN_ID:
        raise RuntimeError("campaign identity drift")
    if snapshot["name"] != EXPECTED_NAME:
        raise RuntimeError(f"campaign name drift: {snapshot['name']!r}")
    if snapshot["status"] != 11:
        raise RuntimeError(f"campaign is not paused: status={snapshot['status']}")
    if snapshot["payment_type"] != "cpc":
        raise RuntimeError(f"campaign payment type drift: {snapshot['payment_type']}")
    if len(snapshot["products"]) != EXPECTED_PRODUCTS:
        raise RuntimeError(
            f"campaign product count drift: {len(snapshot['products'])} != {EXPECTED_PRODUCTS}"
        )


def _verify(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "status_active": after["status"] == 9,
        "campaign_id_unchanged": after["campaign_id"] == before["campaign_id"],
        "name_unchanged": after["name"] == before["name"],
        "payment_type_unchanged": after["payment_type"] == before["payment_type"],
        "bid_type_unchanged": after["bid_type"] == before["bid_type"],
        "placements_unchanged": after["placements"] == before["placements"],
        "products_and_bids_unchanged": after["products"] == before["products"],
        "product_count_50": len(after["products"]) == EXPECTED_PRODUCTS,
    }
    return {
        "status": "ok" if all(checks.values()) else "error",
        "checks": checks,
        "before_status": before["status"],
        "after_status": after["status"],
        "products_before": len(before["products"]),
        "products_after": len(after["products"]),
    }


def _write_report(path: Path, result: dict[str, Any]) -> None:
    verify = result["verify"]
    lines = [
        "# Запуск WB CPC-кампании",
        "",
        f"- Кампания: `{EXPECTED_CAMPAIGN_ID}` / {EXPECTED_NAME}",
        f"- Статус: `{verify['before_status']} -> {verify['after_status']}`",
        f"- Товаров: `{verify['products_before']} -> {verify['products_after']}`",
        f"- Verify: `{verify['status']}`",
        "- Ставки, состав, тип оплаты и размещения не изменялись.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--confirmed-by-user", action="store_true")
    parser.add_argument("--verify-wait-seconds", type=int, default=5)
    args = parser.parse_args()
    if not args.confirmed_by_user:
        raise RuntimeError("explicit owner confirmation is required")

    credentials = load_credentials()
    if not credentials.wb:
        raise RuntimeError("missing WB API token")

    started = datetime.now()
    run_id = f"wb_campaign_start_apply_{started.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(args.data_dir / "runs" / started.strftime("%Y-%m-%d") / run_id)
    raw_dir = ensure_dir(run_dir / "raw")
    processed_dir = ensure_dir(run_dir / "processed")
    assert_apply_not_repeated(
        data_dir=args.data_dir,
        approved_id=APPROVED_ID,
        current_run_id=run_id,
    )

    adapter = WbPromotionAdapter(credentials.wb)
    balance_before = adapter.fetch_balance()
    campaign_before_raw = _load_campaign(adapter)
    campaign_before = _campaign_snapshot(campaign_before_raw)
    _assert_before(campaign_before)
    package = {
        "approved_id": APPROVED_ID,
        "owner_approval": "Запустить paused-кампанию целиком без изменения ставок и состава",
        "action": {
            "marketplace": "wb",
            "operation": "start_campaign",
            "campaign_id": EXPECTED_CAMPAIGN_ID,
            "expected_status_before": 11,
            "expected_status_after": 9,
            "preserve_products_and_bids": True,
        },
        "before_snapshot": campaign_before,
    }
    checksum = canonical_checksum(package)
    write_json(raw_dir / "balance_before.json", balance_before)
    write_json(raw_dir / "campaign_before.json", campaign_before_raw)
    write_json(
        processed_dir / "approved_package.json",
        {**package, "approved_checksum": checksum},
    )

    response = adapter.start_campaign(advert_id=EXPECTED_CAMPAIGN_ID)
    write_json(raw_dir / "start_response.json", response)
    time.sleep(max(1, args.verify_wait_seconds))
    campaign_after_raw = _load_campaign(adapter)
    campaign_after = _campaign_snapshot(campaign_after_raw)
    write_json(raw_dir / "campaign_after.json", campaign_after_raw)
    verify = _verify(campaign_before, campaign_after)
    overall_status = "ok" if verify["status"] == "ok" else "error"
    result = {
        "run_id": run_id,
        "started_at": started.isoformat(timespec="seconds"),
        "mode": "apply",
        "overall_status": overall_status,
        "approved_id": APPROVED_ID,
        "approved_checksum": checksum,
        "campaign_id": EXPECTED_CAMPAIGN_ID,
        "campaign_name": EXPECTED_NAME,
        "update_response": response,
        "verify": verify,
        "apply_performed": True,
        "artifacts": {
            "run_dir": str(run_dir),
            "summary": str(run_dir / "summary.json"),
            "report": str(run_dir / "result.md"),
            "approved_package": str(processed_dir / "approved_package.json"),
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
        task="wb-campaign-start-apply",
        mode="apply",
        risk="high",
        marketplaces=["wb"],
        inputs={
            "campaign_id": EXPECTED_CAMPAIGN_ID,
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
        task="wb-campaign-start-apply",
        status=overall_status,
        run_manifest_path=manifest["manifest"],
        checksum=checksum,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if overall_status == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
