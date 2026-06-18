from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import time
from typing import Any

from takterra_agent.config import AppCredentials
from takterra_agent.core.run_manifest import write_summary_run_manifest
from takterra_agent.marketplaces.wb.promotion_adapter import WbPromotionAdapter
from takterra_agent.reports.writer import ensure_dir, write_json
from takterra_agent.safety.approvals import (
    apply_marker_for,
    assert_apply_not_repeated,
    canonical_checksum,
    mark_approved_applied,
)
from takterra_agent.tasks.status_preflight import run_status_preflight
from takterra_agent.tasks.wb_promotion_bid_plan import WbPromotionBidThresholds, run_wb_promotion_bid_plan
from takterra_agent.tasks.wb_promotion_report import run_wb_promotion_report


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    headers = list(rows[0].keys()) if rows else ["nm_id"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(" ", "").replace("\u00a0", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


def _round2(value: Decimal | None) -> str:
    if value is None:
        return ""
    return str(value.quantize(Decimal("0.01")))


def _money_to_kopecks(value: Decimal) -> int:
    return int((value * Decimal("100")).to_integral_value())


def _latest_plan_dir(data_dir: Path) -> Path | None:
    runs_dir = data_dir / "runs"
    if not runs_dir.exists():
        return None
    candidates = [
        path
        for path in runs_dir.glob("*/*")
        if path.is_dir()
        and path.name.startswith("wb_promotion_bid_plan_")
        and (path / "summary.json").exists()
        and (path / "wb_promotion_bid_changes.csv").exists()
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True)[0]


def _plan_dir(data_dir: Path, plan_run_id: str | None) -> Path:
    if plan_run_id:
        for path in (data_dir / "runs").glob(f"*/{plan_run_id}"):
            if path.is_dir():
                return path
        raise RuntimeError(f"WB promotion bid plan run not found: {plan_run_id}")

    latest = _latest_plan_dir(data_dir)
    if latest is None:
        raise RuntimeError("WB promotion bid plan run not found")
    return latest


def _thresholds_from_summary(summary: dict[str, Any]) -> WbPromotionBidThresholds:
    values = summary.get("summary", {}).get("thresholds", {})
    return WbPromotionBidThresholds(
        zero_orders_spend=values.get("zero_orders_spend", "10"),
        high_drr_percent=values.get("high_drr_percent", "5"),
        high_drr_min_spend=values.get("high_drr_min_spend", "30"),
        scale_min_orders=int(values.get("scale_min_orders", 2)),
        scale_max_drr_percent=values.get("scale_max_drr_percent", "2"),
        card_review_reduce_percent=values.get("card_review_reduce_percent", "10"),
        zero_no_cart_reduce_percent=values.get("zero_no_cart_reduce_percent", "10"),
        max_reduce_percent=values.get("max_reduce_percent", "15"),
        scale_low_drr_percent=values.get("scale_low_drr_percent", "10"),
        scale_mid_drr_percent=values.get("scale_mid_drr_percent", "7"),
        scale_high_drr_percent=values.get("scale_high_drr_percent", "5"),
        min_bid=values.get("min_bid", "1.00"),
    )


def _action_signature(row: dict[str, str]) -> tuple[str, str, str, str, str]:
    return (
        str(row.get("advert_id") or ""),
        str(row.get("nm_id") or ""),
        str(row.get("recommended_action") or ""),
        str(row.get("current_bid") or ""),
        str(row.get("target_bid") or ""),
    )


def _assert_no_drift(
    *,
    approved_rows: list[dict[str, str]],
    fresh_rows: list[dict[str, str]],
    allowed_actions: set[str],
) -> dict[str, Any]:
    approved_set = {
        _action_signature(row)
        for row in approved_rows
        if str(row.get("recommended_action") or "") in allowed_actions
    }
    fresh_set = {
        _action_signature(row)
        for row in fresh_rows
        if str(row.get("recommended_action") or "") in allowed_actions
    }
    drift = {
        "approved_action_rows": len(approved_set),
        "fresh_action_rows": len(fresh_set),
        "added": sorted(fresh_set - approved_set),
        "removed": sorted(approved_set - fresh_set),
    }
    if drift["added"] or drift["removed"]:
        raise RuntimeError("WB promotion bid drift-check failed")
    return drift


def _split_apply_rows(
    rows: list[dict[str, str]],
    *,
    allowed_actions: set[str],
    min_bid: Decimal,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    apply_rows: list[dict[str, Any]] = []
    skipped_rows: list[dict[str, Any]] = []
    for row in rows:
        output = dict(row)
        action = str(row.get("recommended_action") or "")
        if action not in allowed_actions:
            output["skip_reason"] = "action not selected for this apply"
            skipped_rows.append(output)
            continue
        current_bid = _decimal(row.get("current_bid"))
        target_bid = _decimal(row.get("target_bid"))
        if current_bid is None or target_bid is None:
            output["skip_reason"] = "missing current_bid or target_bid"
            skipped_rows.append(output)
            continue
        if row.get("current_bid_source") != "current_bid_api":
            output["skip_reason"] = "current bid was not loaded from API"
            skipped_rows.append(output)
            continue
        if target_bid < min_bid:
            output["skip_reason"] = f"target_bid below safe minimum {min_bid}"
            skipped_rows.append(output)
            continue
        if target_bid == current_bid:
            output["skip_reason"] = "target_bid equals current_bid"
            skipped_rows.append(output)
            continue
        placement = str(row.get("current_bid_place") or "").strip()
        if placement not in {"search", "recommendations", "combined"}:
            output["skip_reason"] = "missing or unsupported placement"
            skipped_rows.append(output)
            continue
        output["target_bid_kopecks"] = _money_to_kopecks(target_bid)
        output["current_bid_kopecks"] = _money_to_kopecks(current_bid)
        apply_rows.append(output)
    return apply_rows, skipped_rows


def _group_bid_payload(apply_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in apply_rows:
        advert_id = str(row["advert_id"])
        grouped.setdefault(advert_id, []).append(
            {
                "nm_id": int(str(row["nm_id"])),
                "bid_kopecks": int(row["target_bid_kopecks"]),
                "placement": str(row["current_bid_place"]),
            }
        )
    return [
        {"advert_id": int(advert_id), "nm_bids": nm_bids}
        for advert_id, nm_bids in sorted(grouped.items(), key=lambda item: int(item[0]))
    ]


def _current_bids_from_campaigns(campaigns: list[dict[str, Any]]) -> dict[tuple[str, str, str], Decimal]:
    result: dict[tuple[str, str, str], Decimal] = {}
    for campaign in campaigns:
        advert_id = str(campaign.get("id") or "")
        for nm in campaign.get("nm_settings") or []:
            nm_id = str(nm.get("nm_id") or nm.get("nmId") or "")
            bids = nm.get("bids_kopecks") or {}
            for placement in ("search", "recommendations"):
                bid = _decimal(bids.get(placement))
                if advert_id and nm_id and bid is not None and bid > 0:
                    result[(advert_id, nm_id, placement)] = (bid / Decimal("100")).quantize(Decimal("0.01"))
    return result


def _verify_applied_rows(
    *,
    campaigns: list[dict[str, Any]],
    apply_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    current_bids = _current_bids_from_campaigns(campaigns)
    mismatches: list[dict[str, Any]] = []
    for row in apply_rows:
        key = (str(row["advert_id"]), str(row["nm_id"]), str(row["current_bid_place"]))
        expected = _decimal(row.get("target_bid"))
        actual = current_bids.get(key)
        if expected != actual:
            mismatches.append(
                {
                    "advert_id": row["advert_id"],
                    "nm_id": row["nm_id"],
                    "placement": row["current_bid_place"],
                    "expected": _round2(expected),
                    "actual": _round2(actual),
                }
            )
    return {
        "status": "ok" if not mismatches else "error",
        "checked_rows": len(apply_rows),
        "mismatches": mismatches,
    }


def _sum_field(rows: list[dict[str, Any]], field: str) -> str:
    total = Decimal("0")
    for row in rows:
        value = _decimal(row.get(field))
        if value is not None:
            total += value
    return _round2(total)


def _write_report(path: Path, result: dict[str, Any]) -> None:
    lines = [
        "# WB Promotion Bid Apply Result",
        "",
        f"Run ID: `{result['run_id']}`",
        f"Started at: `{result['started_at']}`",
        f"Overall status: `{result['overall_status']}`",
        f"Approved plan: `{result['approved_plan_run_id']}`",
        f"Fresh report: `{result['fresh_report']['run_id']}`",
        f"Fresh plan: `{result['fresh_plan']['run_id']}`",
        "",
        "## Summary",
        "",
    ]
    for key, value in result["summary"].items():
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "## Verify", ""])
    lines.append(f"- `status`: {result['verify']['status']}")
    lines.append(f"- `checked_rows`: {result['verify']['checked_rows']}")
    lines.append(f"- `mismatches`: {len(result['verify']['mismatches'])}")
    lines.extend(["", "## Artifacts", ""])
    for key, value in sorted(result["artifacts"].items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def run_wb_promotion_bids_apply(
    *,
    credentials: AppCredentials,
    data_dir: Path = Path("data"),
    plan_run_id: str | None = None,
    run_id: str | None = None,
    confirmed_by_user: bool = False,
    allowed_actions: set[str] | None = None,
    wait_seconds: int = 45,
) -> dict[str, Any]:
    if not confirmed_by_user:
        raise RuntimeError("explicit owner confirmation is required")
    if not credentials.wb:
        raise RuntimeError("missing WB API token")

    allowed_actions = allowed_actions or {"scale_candidate"}
    approved_plan_dir = _plan_dir(data_dir, plan_run_id)
    approved_id = approved_plan_dir.name
    assert_apply_not_repeated(data_dir=data_dir, approved_id=approved_id)
    approved_summary = json.loads((approved_plan_dir / "summary.json").read_text(encoding="utf-8"))
    approved_rows = _read_csv(approved_plan_dir / "wb_promotion_bid_changes.csv")
    thresholds = _thresholds_from_summary(approved_summary)

    started_at = datetime.now()
    run_id = run_id or f"wb_promotion_bids_apply_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    raw_dir = ensure_dir(run_dir / "raw")
    processed_dir = ensure_dir(run_dir / "processed")

    preflight = run_status_preflight(credentials=credentials, data_dir=data_dir, include_lk=False)
    if preflight["overall_status"] not in {"ok", "warning"}:
        raise RuntimeError(f"preflight is not ok: {preflight['overall_status']}")

    fresh_report = run_wb_promotion_report(credentials=credentials, data_dir=data_dir, payment_type="cpc")
    fresh_plan = run_wb_promotion_bid_plan(
        data_dir=data_dir,
        source_run_id=fresh_report["run_id"],
        thresholds=thresholds,
        active_cpc_only=bool(approved_summary.get("active_cpc_only", True)),
    )
    fresh_rows = _read_csv(Path(fresh_plan["artifacts"]["bid_changes_csv"]))
    drift = _assert_no_drift(approved_rows=approved_rows, fresh_rows=fresh_rows, allowed_actions=allowed_actions)
    write_json(processed_dir / "drift_check.json", drift)

    apply_rows, skipped_rows = _split_apply_rows(
        fresh_rows,
        allowed_actions=allowed_actions,
        min_bid=thresholds.min_bid,
    )
    payload_bids = _group_bid_payload(apply_rows)
    write_json(processed_dir / "apply_rows.json", apply_rows)
    write_json(processed_dir / "skipped_rows.json", skipped_rows)
    write_json(processed_dir / "update_payload.json", {"bids": payload_bids})
    _write_csv(apply_rows, processed_dir / "apply_rows.csv")
    _write_csv(skipped_rows, processed_dir / "skipped_rows.csv")

    wb = WbPromotionAdapter(credentials.wb)
    update_response: Any = {}
    if payload_bids:
        update_response = wb.update_bids(payload_bids)
    write_json(raw_dir / "update_response.json", update_response)

    if wait_seconds > 0 and apply_rows:
        time.sleep(wait_seconds)

    advert_ids = sorted({int(str(row["advert_id"])) for row in apply_rows})
    campaigns_after = wb.fetch_campaigns(ids=advert_ids, statuses=[4, 9, 11], payment_type="cpc") if advert_ids else []
    write_json(raw_dir / "campaigns_after_apply.json", {"adverts": campaigns_after})
    verify = _verify_applied_rows(campaigns=campaigns_after, apply_rows=apply_rows)

    summary = {
        "allowed_actions": ",".join(sorted(allowed_actions)),
        "approved_rows": len(approved_rows),
        "fresh_action_rows": len(fresh_rows),
        "applied_rows": len(apply_rows),
        "skipped_rows": len(skipped_rows),
        "current_bid_sum_applied": _sum_field(apply_rows, "current_bid"),
        "target_bid_sum_applied": _sum_field(apply_rows, "target_bid"),
        "bid_change_sum_applied": _sum_field(apply_rows, "bid_change_amount"),
        "wait_seconds": wait_seconds,
    }
    overall_status = "ok" if verify["status"] == "ok" else "warning"
    artifacts = {
        "run_dir": str(run_dir),
        "summary": str(run_dir / "summary.json"),
        "report": str(run_dir / "wb_promotion_bids_apply_result.md"),
        "drift_check": str(processed_dir / "drift_check.json"),
        "apply_rows": str(processed_dir / "apply_rows.csv"),
        "skipped_rows": str(processed_dir / "skipped_rows.csv"),
        "update_payload": str(processed_dir / "update_payload.json"),
        "update_response": str(raw_dir / "update_response.json"),
        "campaigns_after_apply": str(raw_dir / "campaigns_after_apply.json"),
        "run_manifest": str(run_dir / "manifest.json"),
        "apply_marker": str(apply_marker_for(data_dir=data_dir, approved_id=approved_id)),
    }
    result = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "apply",
        "overall_status": overall_status,
        "approved_plan_run_id": approved_id,
        "approved_id": approved_id,
        "preflight": {
            "run_id": preflight["run_id"],
            "overall_status": preflight["overall_status"],
            "artifacts": preflight["artifacts"],
        },
        "fresh_report": {
            "run_id": fresh_report["run_id"],
            "summary": fresh_report["summary"],
            "artifacts": fresh_report["artifacts"],
        },
        "fresh_plan": {
            "run_id": fresh_plan["run_id"],
            "summary": fresh_plan["summary"],
            "artifacts": fresh_plan["artifacts"],
        },
        "drift": drift,
        "summary": summary,
        "update_response": update_response,
        "verify": verify,
        "artifacts": artifacts,
    }
    write_json(run_dir / "summary.json", result)
    _write_report(run_dir / "wb_promotion_bids_apply_result.md", result)
    manifest_paths = write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=result,
        task="wb-promotion-bids-apply",
        mode="apply",
        risk="high",
        marketplaces=["wb"],
        inputs={
            "plan_run_id": plan_run_id,
            "confirmed_by_user": confirmed_by_user,
            "allowed_actions": sorted(allowed_actions),
            "wait_seconds": wait_seconds,
        },
    )
    mark_approved_applied(
        data_dir=data_dir,
        approved_id=approved_id,
        apply_run_id=run_id,
        task="wb-promotion-bids-apply",
        status=overall_status,
        run_manifest_path=manifest_paths["manifest"],
        checksum=canonical_checksum({"approved_id": approved_id, "summary": summary, "drift": drift}),
    )
    return result
