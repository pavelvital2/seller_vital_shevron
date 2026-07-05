from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Any

from seller_agent.config import AppCredentials
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.marketplaces.ozon.adapter import OzonSellerAdapter
from seller_agent.reports.writer import ensure_dir, write_json
from seller_agent.safety.approvals import (
    apply_marker_for,
    assert_apply_not_repeated,
    canonical_checksum,
    mark_approved_applied,
)
from seller_agent.tasks.ozon_actions_optimizer_plan import run_ozon_actions_optimizer_plan
from seller_agent.tasks.status_preflight import run_status_preflight


APPLY_ACTIONS = {
    "add_to_best_action",
    "update_current_action_price",
    "switch_to_better_action_review",
}


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter=";"))


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


def _same_decimal(left: Any, right: Any) -> bool:
    left_decimal = _decimal(left)
    right_decimal = _decimal(right)
    if left_decimal is None and right_decimal is None:
        return True
    return left_decimal is not None and right_decimal is not None and left_decimal == right_decimal


def _latest_plan_dir(data_dir: Path) -> Path | None:
    runs_dir = data_dir / "runs"
    if not runs_dir.exists():
        return None
    candidates = [
        path
        for path in runs_dir.glob("*/*")
        if path.is_dir()
        and path.name.startswith("ozon_actions_optimizer_plan_")
        and (path / "summary.json").exists()
        and (path / "ozon_actions_optimizer_recommendations.csv").exists()
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True)[0]


def _plan_dir(data_dir: Path, plan_run_id: str | None) -> Path:
    if plan_run_id:
        for path in (data_dir / "runs").glob(f"*/{plan_run_id}"):
            if path.is_dir():
                return path
        raise RuntimeError(f"Ozon actions optimizer plan run not found: {plan_run_id}")
    latest = _latest_plan_dir(data_dir)
    if latest is None:
        raise RuntimeError("Ozon actions optimizer plan run not found")
    return latest


def _apply_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [row for row in rows if row.get("recommended_action") in APPLY_ACTIONS]


def _row_signature(row: dict[str, str]) -> tuple[str, str, str, str, str, str]:
    return (
        str(row.get("recommended_action") or ""),
        str(row.get("product_id") or ""),
        str(row.get("offer_id") or ""),
        str(row.get("action_id") or ""),
        str(row.get("target_action_price") or ""),
        str(row.get("current_active_action_id") or ""),
    )


def _signature_dict(signature: tuple[str, str, str, str, str, str]) -> dict[str, str]:
    return {
        "recommended_action": signature[0],
        "product_id": signature[1],
        "offer_id": signature[2],
        "action_id": signature[3],
        "target_action_price": signature[4],
        "current_active_action_id": signature[5],
    }


def _build_partial_drift_plan(
    *,
    approved_rows: list[dict[str, str]],
    fresh_rows: list[dict[str, str]],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    approved_by_signature = {_row_signature(row): row for row in _apply_rows(approved_rows)}
    fresh_by_signature = {_row_signature(row): row for row in _apply_rows(fresh_rows)}
    approved_set = set(approved_by_signature)
    fresh_set = set(fresh_by_signature)
    eligible_signatures = approved_set & fresh_set
    eligible_rows = [fresh_by_signature[signature] for signature in sorted(eligible_signatures, key=lambda item: item[1])]
    added = fresh_set - approved_set
    removed = approved_set - fresh_set
    skipped_due_to_drift = [
        {
            "kind": "fresh_unapproved_action_row",
            "fresh": _signature_dict(signature),
            "row": fresh_by_signature[signature],
        }
        for signature in sorted(added, key=lambda item: item[1])
    ] + [
        {
            "kind": "approved_action_row_no_longer_matching_fresh",
            "approved": _signature_dict(signature),
            "row": approved_by_signature[signature],
        }
        for signature in sorted(removed, key=lambda item: item[1])
    ]
    skipped_product_ids = sorted(
        {
            str((item.get("fresh") or item.get("approved") or {}).get("product_id") or "")
            for item in skipped_due_to_drift
            if str((item.get("fresh") or item.get("approved") or {}).get("product_id") or "")
        }
    )
    drift = {
        "mode": "partial_apply_unchanged_rows",
        "approved_apply_rows_count": len(approved_set),
        "fresh_apply_rows_count": len(fresh_set),
        "eligible_rows_count": len(eligible_signatures),
        "added": sorted(added),
        "removed": sorted(removed),
        "skipped_due_to_drift_count": len(skipped_due_to_drift),
        "skipped_due_to_drift_product_count": len(skipped_product_ids),
        "skipped_due_to_drift_product_ids": skipped_product_ids,
        "skipped_due_to_drift": skipped_due_to_drift,
    }
    return drift, eligible_rows


def _group_activate_rows(rows: list[dict[str, str]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        action_id = str(row.get("action_id") or "")
        if not action_id:
            continue
        grouped.setdefault(action_id, []).append(
            {
                "product_id": int(row["product_id"]) if str(row.get("product_id") or "").isdigit() else row.get("product_id"),
                "offer_id": str(row.get("offer_id") or ""),
                "action_price": str(row.get("target_action_price") or ""),
            }
        )
    return grouped


def _group_deactivate_rows(rows: list[dict[str, str]]) -> dict[str, list[int]]:
    grouped: dict[str, list[int]] = {}
    for row in rows:
        current_action_id = str(row.get("current_active_action_id") or "")
        product_id = str(row.get("product_id") or "")
        if not current_action_id or not product_id.isdigit():
            continue
        grouped.setdefault(current_action_id, []).append(int(product_id))
    return grouped


def _fetch_active_rows(
    *,
    ozon: OzonSellerAdapter,
    action_id: str,
    raw_dir: Path,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    last_id = ""
    page_no = 1
    while True:
        payload: dict[str, Any] = {"action_id": int(action_id) if action_id.isdigit() else action_id, "limit": 100}
        if last_id:
            payload["last_id"] = last_id
        data = ozon.post("/v1/actions/products", payload)
        write_json(raw_dir / f"ozon_action_{action_id}_active_page_{page_no:03d}.json", data)
        result = data.get("result") or {}
        page_rows = result.get("products") or []
        if not isinstance(page_rows, list):
            page_rows = []
        rows.extend([row for row in page_rows if isinstance(row, dict)])
        total = result.get("total")
        next_last_id = str(result.get("last_id") or "")
        if total is not None and len(rows) >= int(total):
            break
        if total is None and len(page_rows) < 100:
            break
        if not next_last_id or next_last_id == last_id:
            break
        last_id = next_last_id
        page_no += 1
    return rows


def _verify_ozon_actions(
    *,
    ozon: OzonSellerAdapter,
    rows: list[dict[str, str]],
    raw_dir: Path,
) -> dict[str, Any]:
    target_action_ids = sorted({str(row.get("action_id") or "") for row in rows if row.get("action_id")})
    source_action_ids = sorted(
        {str(row.get("current_active_action_id") or "") for row in rows if row.get("current_active_action_id")}
    )
    active_by_action: dict[str, dict[str, dict[str, Any]]] = {}
    for action_id in sorted(set(target_action_ids + source_action_ids)):
        active_rows = _fetch_active_rows(ozon=ozon, action_id=action_id, raw_dir=raw_dir)
        active_by_action[action_id] = {str(row.get("id") or row.get("product_id") or ""): row for row in active_rows}

    mismatches: list[dict[str, Any]] = []
    for row in rows:
        product_id = str(row.get("product_id") or "")
        target_action_id = str(row.get("action_id") or "")
        target_active = active_by_action.get(target_action_id, {}).get(product_id)
        if not target_active:
            mismatches.append(
                {
                    "product_id": product_id,
                    "field": "target_action_membership",
                    "expected_action_id": target_action_id,
                }
            )
            continue
        if not _same_decimal(target_active.get("action_price"), row.get("target_action_price")):
            mismatches.append(
                {
                    "product_id": product_id,
                    "field": "target_action_price",
                    "actual": target_active.get("action_price"),
                    "expected": row.get("target_action_price"),
                }
            )

        if row.get("recommended_action") == "switch_to_better_action_review":
            source_action_id = str(row.get("current_active_action_id") or "")
            if source_action_id and product_id in active_by_action.get(source_action_id, {}):
                mismatches.append(
                    {
                        "product_id": product_id,
                        "field": "source_action_membership",
                        "source_action_id": source_action_id,
                        "expected": "deactivated",
                    }
                )

    return {
        "status": "ok" if not mismatches else "error",
        "checked_rows": len(rows),
        "checked_action_ids": sorted(active_by_action),
        "mismatches": mismatches,
    }


def _preflight_failure_summary(preflight: dict[str, Any]) -> str:
    failed: list[str] = []
    checks = preflight.get("checks") if isinstance(preflight.get("checks"), dict) else {}
    for key, value in checks.items():
        if not isinstance(value, dict):
            continue
        status = str(value.get("status") or "")
        if status not in {"error", "warning"}:
            continue
        error = str(value.get("error") or value.get("message") or "").replace("\n", " ").strip()
        failed.append(f"{key}={status}" + (f" ({error[:180]})" if error else ""))
    return "; ".join(failed[:8])


def _write_report(path: Path, result: dict[str, Any]) -> None:
    lines = [
        "# Ozon All Actions Apply Result",
        "",
        f"Run ID: `{result['run_id']}`",
        f"Started at: `{result['started_at']}`",
        f"Overall status: `{result['overall_status']}`",
        f"Approved plan: `{result['approved_plan_run_id']}`",
        f"Fresh plan: `{result['fresh_plan']['run_id']}`",
        "",
        "## Applied",
        "",
        f"- eligible rows: `{result['drift']['eligible_rows_count']}`",
        f"- activate/update rows: `{result['applied']['activate_rows_count']}`",
        f"- switch rows: `{result['applied']['switch_rows_count']}`",
        f"- deactivate source rows: `{result['applied']['deactivate_rows_count']}`",
        f"- skipped because of drift: `{result['drift'].get('skipped_due_to_drift_count', 0)}`",
        "",
        "## Verify",
        "",
        f"- status: `{result['verify']['status']}`",
        f"- mismatches: `{len(result['verify']['mismatches'])}`",
        "",
        "## Artifacts",
        "",
    ]
    for key, value in sorted(result["artifacts"].items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def run_ozon_actions_optimizer_apply(
    *,
    credentials: AppCredentials,
    data_dir: Path = Path("data"),
    plan_run_id: str | None = None,
    run_id: str | None = None,
    confirmed_by_user: bool = False,
) -> dict[str, Any]:
    if not confirmed_by_user:
        raise RuntimeError("explicit owner confirmation is required")
    if not credentials.ozon_seller:
        raise RuntimeError("missing Ozon Seller API credentials")

    approved_plan_dir = _plan_dir(data_dir, plan_run_id)
    approved_id = approved_plan_dir.name
    assert_apply_not_repeated(data_dir=data_dir, approved_id=approved_id)
    approved_summary = json.loads((approved_plan_dir / "summary.json").read_text(encoding="utf-8"))
    approved_rows = _read_csv(approved_plan_dir / "ozon_actions_optimizer_recommendations.csv")

    started_at = datetime.now()
    run_id = run_id or f"ozon_actions_optimizer_apply_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    raw_dir = ensure_dir(run_dir / "raw")
    processed_dir = ensure_dir(run_dir / "processed")

    preflight = run_status_preflight(
        credentials=credentials,
        data_dir=data_dir,
        include_lk=False,
        marketplaces=("ozon",),
        include_catalog=False,
        include_ozon_performance=False,
    )
    if preflight["overall_status"] not in {"ok", "warning"}:
        failure_summary = _preflight_failure_summary(preflight)
        details = f"; failed checks: {failure_summary}" if failure_summary else ""
        raise RuntimeError(f"preflight is not ok: {preflight['overall_status']}{details}")

    approved_lk_source = (approved_summary.get("summary") or {}).get("lk_boost_source_path")
    lk_path = Path(str(approved_lk_source)) if approved_lk_source else None
    if lk_path and not lk_path.exists():
        lk_path = None
    fresh_plan = run_ozon_actions_optimizer_plan(
        credentials=credentials,
        data_dir=data_dir,
        lk_boost_summary_json=lk_path,
    )
    fresh_rows = _read_csv(Path(fresh_plan["artifacts"]["recommendations_csv"]))
    drift, eligible_rows = _build_partial_drift_plan(approved_rows=approved_rows, fresh_rows=fresh_rows)
    write_json(processed_dir / "drift_check.json", drift)
    write_json(processed_dir / "eligible_rows.json", eligible_rows)
    write_json(processed_dir / "skipped_drift_rows.json", drift["skipped_due_to_drift"])

    activate_rows = [
        row
        for row in eligible_rows
        if row.get("recommended_action") in {"add_to_best_action", "update_current_action_price", "switch_to_better_action_review"}
    ]
    switch_rows = [row for row in eligible_rows if row.get("recommended_action") == "switch_to_better_action_review"]
    deactivate_by_action = _group_deactivate_rows(switch_rows)
    activate_by_action = _group_activate_rows(activate_rows)
    write_json(processed_dir / "activate_by_action.json", activate_by_action)
    write_json(processed_dir / "deactivate_by_action.json", deactivate_by_action)

    ozon = OzonSellerAdapter(credentials.ozon_seller)
    deactivate_responses: dict[str, Any] = {}
    for action_id, product_ids in deactivate_by_action.items():
        response = ozon.post(
            "/v1/actions/products/deactivate",
            {
                "action_id": int(action_id) if action_id.isdigit() else action_id,
                "product_ids": product_ids,
            },
        )
        deactivate_responses[action_id] = response
    write_json(raw_dir / "ozon_deactivate_responses.json", deactivate_responses)

    activate_responses: dict[str, Any] = {}
    for action_id, products in activate_by_action.items():
        response = ozon.post(
            "/v1/actions/products/activate",
            {
                "action_id": int(action_id) if action_id.isdigit() else action_id,
                "products": products,
            },
        )
        activate_responses[action_id] = response
    write_json(raw_dir / "ozon_activate_responses.json", activate_responses)

    verify = _verify_ozon_actions(
        ozon=ozon,
        rows=eligible_rows,
        raw_dir=ensure_dir(raw_dir / "verify"),
    )
    rejected_count = sum(
        len(((response.get("result") or {}).get("rejected") or []))
        for response in list(activate_responses.values()) + list(deactivate_responses.values())
        if isinstance(response, dict)
    )
    overall_status = "ok" if verify["status"] == "ok" and not drift["skipped_due_to_drift_count"] and not rejected_count else "warning"
    if verify["status"] != "ok":
        overall_status = "error"

    artifacts = {
        "run_dir": str(run_dir),
        "summary": str(run_dir / "summary.json"),
        "report": str(run_dir / "ozon_actions_optimizer_apply_result.md"),
        "drift_check": str(processed_dir / "drift_check.json"),
        "eligible_rows": str(processed_dir / "eligible_rows.json"),
        "skipped_drift_rows": str(processed_dir / "skipped_drift_rows.json"),
        "activate_by_action": str(processed_dir / "activate_by_action.json"),
        "deactivate_by_action": str(processed_dir / "deactivate_by_action.json"),
        "activate_responses": str(raw_dir / "ozon_activate_responses.json"),
        "deactivate_responses": str(raw_dir / "ozon_deactivate_responses.json"),
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
        "fresh_plan": {
            "run_id": fresh_plan["run_id"],
            "summary": fresh_plan["summary"],
            "artifacts": fresh_plan["artifacts"],
        },
        "drift": drift,
        "applied": {
            "activate_rows_count": len(activate_rows),
            "switch_rows_count": len(switch_rows),
            "deactivate_rows_count": sum(len(items) for items in deactivate_by_action.values()),
            "activate_action_count": len(activate_by_action),
            "deactivate_action_count": len(deactivate_by_action),
            "rejected_count": rejected_count,
            "activate_responses": activate_responses,
            "deactivate_responses": deactivate_responses,
        },
        "verify": verify,
        "artifacts": artifacts,
    }
    write_json(run_dir / "summary.json", result)
    _write_report(run_dir / "ozon_actions_optimizer_apply_result.md", result)
    manifest_paths = write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=result,
        task="ozon-actions-optimizer-apply",
        mode="apply",
        risk="high",
        marketplaces=["ozon"],
        inputs={"plan_run_id": plan_run_id, "confirmed_by_user": confirmed_by_user},
    )
    mark_approved_applied(
        data_dir=data_dir,
        approved_id=approved_id,
        apply_run_id=run_id,
        task="ozon-actions-optimizer-apply",
        status=overall_status,
        run_manifest_path=manifest_paths["manifest"],
        checksum=canonical_checksum({"approved_id": approved_id, "drift": drift}),
    )
    return result
