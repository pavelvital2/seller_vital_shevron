from __future__ import annotations

import json
import time
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from seller_agent.config import AppCredentials
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.reports.writer import ensure_dir, write_json
from seller_agent.safety.approvals import (
    action_rows_checksum,
    apply_marker_for,
    assert_apply_not_repeated,
    canonical_checksum,
    mark_approved_applied,
)
from seller_agent.tasks.liquidation_daily_control import _read_csv, _write_csv
from seller_agent.tasks.status_preflight import run_status_preflight
from seller_agent.tasks.wb_actions_discount_apply import (
    _fetch_current_price_rows,
    _verify_current_discount_payload,
    _wb_upload_and_verify,
)
from seller_agent.tasks.wb_liquidation_stage2_plan import run_wb_liquidation_stage2_plan


def _plan_dir(data_dir: Path, plan_run_id: str) -> Path:
    matches = list((data_dir / "runs").glob(f"*/{plan_run_id}"))
    if len(matches) != 1:
        raise RuntimeError(f"WB liquidation stage2 plan run not found: {plan_run_id}")
    return matches[0]


def _load_plan(plan_dir: Path) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, str]]]:
    summary = json.loads((plan_dir / "summary.json").read_text(encoding="utf-8"))
    pending = json.loads((plan_dir / "pending_approval.json").read_text(encoding="utf-8"))
    rows = _read_csv(plan_dir / "stage2_plan.csv")
    actions = pending.get("actions") if isinstance(pending.get("actions"), list) else []
    checksum = action_rows_checksum(actions)
    issues: list[str] = []
    if summary.get("overall_status") != "ok":
        issues.append("plan_status_not_ok")
    if summary.get("apply_performed") is not False:
        issues.append("plan_not_dry_run")
    if pending.get("schema") != "wb_liquidation_stage2_approval.v1":
        issues.append("pending_schema_invalid")
    if checksum != pending.get("actions_checksum") or checksum != summary.get("actions_checksum"):
        issues.append("actions_checksum_mismatch")
    if len(actions) != int(summary.get("ready_for_owner_review") or 0):
        issues.append("actions_count_mismatch")
    if int(summary.get("blocked_rows") or 0):
        issues.append("blocked_rows_present")
    if issues:
        raise RuntimeError("invalid approved stage2 plan: " + ", ".join(issues))
    return summary, pending, rows


def _payload_from_plan(
    *,
    pending: dict[str, Any],
    rows: list[dict[str, str]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    by_nm_id = {int(row["nm_id"]): row for row in rows}
    payload_rows: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []
    for action in pending.get("actions") or []:
        nm_id = int(action["nm_id"])
        source = by_nm_id.get(nm_id)
        if source is None:
            raise RuntimeError(f"approved action is absent from stage2 plan: {nm_id}")
        base = Decimal(str(source["live_base_price"]))
        discount = int(action["discount"])
        expected_price = Decimal(str(action["expected_price"]))
        calculated_price = (
            base * (Decimal(100) - Decimal(discount)) / Decimal(100)
        ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        floor = Decimal(str(source["clearance_floor"]))
        if calculated_price != expected_price:
            raise RuntimeError(f"target price mismatch for nmID {nm_id}")
        if calculated_price < floor:
            raise RuntimeError(f"target price below clearance floor for nmID {nm_id}")
        price: int | float = int(base) if base == base.to_integral_value() else float(base)
        payload_rows.append({"nmID": nm_id, "price": price, "discount": discount})
        review_rows.append(
            {
                "nm_id": nm_id,
                "internal_sku": source.get("internal_sku", ""),
                "current_discount": int(source.get("live_discount") or 0),
                "target_discount": discount,
                "current_price": float(Decimal(str(source.get("live_discounted_price") or 0))),
                "target_price": float(calculated_price),
                "base_price": float(base),
                "clearance_floor": float(floor),
            }
        )
    return {"data": payload_rows}, review_rows


def _wait_for_verify(
    *,
    credentials: AppCredentials,
    target_payload: dict[str, Any],
    attempts: int = 6,
    delay_seconds: int = 5,
) -> tuple[dict[str, Any], dict[int, dict[str, Any]], int]:
    nm_ids = [int(row["nmID"]) for row in target_payload.get("data") or []]
    verify: dict[str, Any] = {}
    current: dict[int, dict[str, Any]] = {}
    for attempt in range(1, attempts + 1):
        current = _fetch_current_price_rows(credentials, nm_ids)
        verify = _verify_current_discount_payload(
            target_payload=target_payload,
            current_rows=current,
        )
        if verify.get("status") == "ok":
            return verify, current, attempt
        if attempt < attempts:
            time.sleep(delay_seconds)
    return verify, current, attempts


def run_wb_liquidation_stage2_apply(
    *,
    credentials: AppCredentials,
    data_dir: Path,
    plan_run_id: str,
    confirmed_by_user: bool,
    run_id: str | None = None,
) -> dict[str, Any]:
    if not confirmed_by_user:
        raise RuntimeError("explicit owner confirmation is required")
    if not credentials.wb:
        raise RuntimeError("missing WB API token")

    approved_dir = _plan_dir(data_dir, plan_run_id)
    approved_id = approved_dir.name
    assert_apply_not_repeated(data_dir=data_dir, approved_id=approved_id)
    approved_summary, approved_pending, approved_rows = _load_plan(approved_dir)
    approved_payload, approved_review_rows = _payload_from_plan(
        pending=approved_pending,
        rows=approved_rows,
    )

    started = datetime.now().astimezone()
    resolved_run_id = run_id or f"wb_liquidation_stage2_apply_{started:%Y%m%dT%H%M%S}"
    run_dir = ensure_dir(data_dir / "runs" / started.strftime("%Y-%m-%d") / resolved_run_id)
    raw_dir = ensure_dir(run_dir / "raw")
    processed_dir = ensure_dir(run_dir / "processed")

    preflight = run_status_preflight(
        credentials=credentials,
        data_dir=data_dir,
        include_lk=False,
        marketplaces=("wb",),
        include_ozon_performance=False,
    )
    if preflight.get("overall_status") != "ok":
        raise RuntimeError(f"preflight is not ok: {preflight.get('overall_status')}")

    fresh = run_wb_liquidation_stage2_plan(
        credentials=credentials,
        data_dir=data_dir,
        run_id=f"wb_liquidation_stage2_fresh_{started:%Y%m%dT%H%M%S}",
    )
    fresh_dir = _plan_dir(data_dir, str(fresh["run_id"]))
    fresh_summary, fresh_pending, fresh_rows = _load_plan(fresh_dir)
    fresh_payload, fresh_review_rows = _payload_from_plan(pending=fresh_pending, rows=fresh_rows)

    approved_checksum = str(approved_summary["actions_checksum"])
    fresh_checksum = str(fresh_summary["actions_checksum"])
    if fresh_checksum != approved_checksum or fresh_payload != approved_payload:
        write_json(
            processed_dir / "drift_check.json",
            {
                "status": "blocked",
                "approved_checksum": approved_checksum,
                "fresh_checksum": fresh_checksum,
                "approved_payload": approved_payload,
                "fresh_payload": fresh_payload,
            },
        )
        raise RuntimeError("fresh stage2 plan drifted from owner-approved plan")

    drift = {
        "status": "ok",
        "approved_checksum": approved_checksum,
        "fresh_checksum": fresh_checksum,
        "approved_rows": len(approved_payload["data"]),
        "fresh_rows": len(fresh_payload["data"]),
        "drift_rows": 0,
    }
    write_json(processed_dir / "approved_payload.json", approved_payload)
    write_json(processed_dir / "fresh_payload.json", fresh_payload)
    write_json(processed_dir / "drift_check.json", drift)
    _write_csv(processed_dir / "approved_rows.csv", approved_review_rows)
    _write_csv(processed_dir / "fresh_rows.csv", fresh_review_rows)

    upload = _wb_upload_and_verify(
        payload=fresh_payload,
        token=credentials.wb.token,
        raw_dir=raw_dir,
        label="stage2",
    )
    verify, current_rows, verify_attempts = _wait_for_verify(
        credentials=credentials,
        target_payload=fresh_payload,
    )
    write_json(raw_dir / "current_prices.json", current_rows)
    _write_csv(processed_dir / "verify_rows.csv", verify.get("rows") or [])

    upload_status = str(upload.get("verify_status") or "")
    verification_ok = upload_status == "ok" and verify.get("status") == "ok"
    overall_status = "ok" if verification_ok else "warning"
    result = {
        "run_id": resolved_run_id,
        "started_at": started.isoformat(timespec="seconds"),
        "mode": "apply",
        "overall_status": overall_status,
        "lifecycle_status": "verified" if verification_ok else "needs_attention",
        "verification_confirmed": verification_ok,
        "approved_plan_run_id": approved_id,
        "approved_id": approved_id,
        "actions_checksum": approved_checksum,
        "preflight": {
            "run_id": preflight.get("run_id"),
            "overall_status": preflight.get("overall_status"),
            "artifacts": preflight.get("artifacts") or {},
        },
        "fresh_plan": {"run_id": fresh.get("run_id"), "actions_checksum": fresh_checksum},
        "drift": drift,
        "applied": {
            "payload_rows_count": len(fresh_payload["data"]),
            "upload_id": upload.get("upload_id"),
            "upload_status": upload_status,
            "success_rows": upload.get("success_rows"),
            "failed_rows": upload.get("failed_rows"),
        },
        "verify": {
            **{key: value for key, value in verify.items() if key != "rows"},
            "attempts": verify_attempts,
        },
        "artifacts": {
            "run_dir": str(run_dir),
            "summary": str(run_dir / "summary.json"),
            "approved_payload": str(processed_dir / "approved_payload.json"),
            "fresh_payload": str(processed_dir / "fresh_payload.json"),
            "drift_check": str(processed_dir / "drift_check.json"),
            "verify_rows": str(processed_dir / "verify_rows.csv"),
            "current_prices": str(raw_dir / "current_prices.json"),
            "run_manifest": str(run_dir / "manifest.json"),
            "apply_marker": str(apply_marker_for(data_dir=data_dir, approved_id=approved_id)),
        },
    }
    write_json(run_dir / "summary.json", result)
    manifest = write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=result,
        task="wb-liquidation-stage2-apply",
        mode="apply",
        risk="high",
        marketplaces=["wb"],
        inputs={"plan_run_id": plan_run_id, "confirmed_by_user": confirmed_by_user},
        source_run_ids=[plan_run_id, str(fresh.get("run_id"))],
        approved_id=approved_id,
        lifecycle_status="verified" if verification_ok else "needs_attention",
        closed=verification_ok,
    )
    mark_approved_applied(
        data_dir=data_dir,
        approved_id=approved_id,
        apply_run_id=resolved_run_id,
        task="wb-liquidation-stage2-apply",
        status=overall_status,
        run_manifest_path=manifest["manifest"],
        checksum=canonical_checksum({"approved_id": approved_id, "drift": drift}),
    )
    return result


def run_wb_liquidation_stage2_verify(
    *,
    credentials: AppCredentials,
    data_dir: Path,
    plan_run_id: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    if not credentials.wb:
        raise RuntimeError("missing WB API token")
    plan_dir = _plan_dir(data_dir, plan_run_id)
    _, pending, rows = _load_plan(plan_dir)
    target_payload, review_rows = _payload_from_plan(pending=pending, rows=rows)
    started = datetime.now().astimezone()
    resolved_run_id = run_id or f"wb_liquidation_stage2_verify_{started:%Y%m%dT%H%M%S}"
    run_dir = ensure_dir(data_dir / "runs" / started.strftime("%Y-%m-%d") / resolved_run_id)
    raw_dir = ensure_dir(run_dir / "raw")
    processed_dir = ensure_dir(run_dir / "processed")
    verify, current_rows, attempts = _wait_for_verify(
        credentials=credentials,
        target_payload=target_payload,
        attempts=1,
        delay_seconds=0,
    )
    write_json(raw_dir / "current_prices.json", current_rows)
    write_json(processed_dir / "target_payload.json", target_payload)
    _write_csv(processed_dir / "target_rows.csv", review_rows)
    _write_csv(processed_dir / "verify_rows.csv", verify.get("rows") or [])
    status = str(verify.get("status") or "warning")
    result = {
        "run_id": resolved_run_id,
        "started_at": started.isoformat(timespec="seconds"),
        "mode": "verify",
        "overall_status": status,
        "lifecycle_status": "verified" if status == "ok" else "needs_attention",
        "verification_confirmed": status == "ok",
        "approved_plan_run_id": plan_run_id,
        "verify": {**{key: value for key, value in verify.items() if key != "rows"}, "attempts": attempts},
        "artifacts": {
            "run_dir": str(run_dir),
            "summary": str(run_dir / "summary.json"),
            "target_payload": str(processed_dir / "target_payload.json"),
            "verify_rows": str(processed_dir / "verify_rows.csv"),
            "current_prices": str(raw_dir / "current_prices.json"),
            "run_manifest": str(run_dir / "manifest.json"),
        },
    }
    write_json(run_dir / "summary.json", result)
    write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=result,
        task="wb-liquidation-stage2-verify",
        mode="verify",
        risk="low",
        marketplaces=["wb"],
        inputs={"plan_run_id": plan_run_id},
        source_run_ids=[plan_run_id],
        approved_id=plan_run_id,
        lifecycle_status="verified" if status == "ok" else "needs_attention",
        closed=status == "ok",
    )
    return result
