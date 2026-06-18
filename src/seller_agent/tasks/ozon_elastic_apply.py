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
from seller_agent.tasks.ozon_elastic_plan import ACTIVE, _fetch_action_group, run_ozon_elastic_plan
from seller_agent.tasks.status_preflight import run_status_preflight


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


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
        and path.name.startswith("ozon_elastic_plan_")
        and (path / "summary.json").exists()
        and (path / "ozon_elastic_dry_run.csv").exists()
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True)[0]


def _plan_dir(data_dir: Path, plan_run_id: str | None) -> Path:
    if plan_run_id:
        for path in (data_dir / "runs").glob(f"*/{plan_run_id}"):
            if path.is_dir():
                return path
        direct = data_dir / "runs" / plan_run_id
        if direct.is_dir():
            return direct
        raise RuntimeError(f"Ozon Elastic plan run not found: {plan_run_id}")

    latest = _latest_plan_dir(data_dir)
    if latest is None:
        raise RuntimeError("Ozon Elastic plan run not found")
    return latest


def _action_rows(rows: list[dict[str, str]]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    activate_rows = [
        row
        for row in rows
        if row.get("planned_action") == "add_to_action"
        or (
            row.get("planned_action") == "update_action_price"
            and not _same_decimal(row.get("current_action_price"), row.get("calculated_action_price"))
        )
    ]
    deactivate_rows = [row for row in rows if row.get("planned_action") == "deactivate_from_action"]
    return activate_rows, deactivate_rows


def _row_signature(row: dict[str, str], *, include_price: bool) -> tuple[str, str, str, str]:
    return (
        str(row.get("planned_action") or ""),
        str(row.get("product_id") or ""),
        str(row.get("offer_id") or ""),
        str(row.get("calculated_action_price") or "") if include_price else "",
    )


def _assert_no_drift(
    *,
    approved_rows: list[dict[str, str]],
    fresh_rows: list[dict[str, str]],
    approved_summary: dict[str, Any],
    fresh_summary: dict[str, Any],
) -> dict[str, Any]:
    if str(approved_summary.get("action_id")) != str(fresh_summary.get("action_id")):
        raise RuntimeError(
            f"Ozon Elastic action drift: approved {approved_summary.get('action_id')}, "
            f"fresh {fresh_summary.get('action_id')}"
        )

    approved_activate, approved_deactivate = _action_rows(approved_rows)
    fresh_activate, fresh_deactivate = _action_rows(fresh_rows)
    approved_activate_set = {_row_signature(row, include_price=True) for row in approved_activate}
    fresh_activate_set = {_row_signature(row, include_price=True) for row in fresh_activate}
    approved_deactivate_set = {_row_signature(row, include_price=False) for row in approved_deactivate}
    fresh_deactivate_set = {_row_signature(row, include_price=False) for row in fresh_deactivate}

    drift = {
        "approved_activate_count": len(approved_activate_set),
        "fresh_activate_count": len(fresh_activate_set),
        "approved_deactivate_count": len(approved_deactivate_set),
        "fresh_deactivate_count": len(fresh_deactivate_set),
        "activate_added": sorted(fresh_activate_set - approved_activate_set),
        "activate_removed": sorted(approved_activate_set - fresh_activate_set),
        "deactivate_added": sorted(fresh_deactivate_set - approved_deactivate_set),
        "deactivate_removed": sorted(approved_deactivate_set - fresh_deactivate_set),
    }
    if (
        drift["activate_added"]
        or drift["activate_removed"]
        or drift["deactivate_added"]
        or drift["deactivate_removed"]
    ):
        raise RuntimeError("Ozon Elastic drift-check failed")
    return drift


def _verify_ozon_elastic(
    *,
    ozon: OzonSellerAdapter,
    action_id: str,
    activate_rows: list[dict[str, str]],
    deactivate_rows: list[dict[str, str]],
    raw_dir: Path,
) -> dict[str, Any]:
    active_rows = _fetch_action_group(ozon, action_id=action_id, source_group=ACTIVE, raw_dir=raw_dir)
    active_by_id = {str(row.get("product_id") or ""): row for row in active_rows}
    mismatches: list[dict[str, Any]] = []

    for row in activate_rows:
        product_id = str(row.get("product_id") or "")
        active = active_by_id.get(product_id)
        if not active:
            mismatches.append({"product_id": product_id, "field": "active_membership", "expected": "active"})
            continue
        if not _same_decimal(active.get("current_action_price"), row.get("calculated_action_price")):
            mismatches.append(
                {
                    "product_id": product_id,
                    "field": "current_action_price",
                    "actual": active.get("current_action_price"),
                    "expected": row.get("calculated_action_price"),
                }
            )

    still_active = [
        str(row.get("product_id") or "")
        for row in deactivate_rows
        if str(row.get("product_id") or "") in active_by_id
    ]
    return {
        "status": "ok" if not mismatches and not still_active else "error",
        "active_rows_checked": len(active_rows),
        "price_mismatches": mismatches,
        "still_active_deactivated": still_active,
    }


def _write_report(path: Path, result: dict[str, Any]) -> None:
    lines = [
        "# Ozon Elastic Apply Result",
        "",
        f"Run ID: `{result['run_id']}`",
        f"Started at: `{result['started_at']}`",
        f"Overall status: `{result['overall_status']}`",
        f"Approved plan: `{result['approved_plan_run_id']}`",
        f"Fresh plan: `{result['fresh_plan']['run_id']}`",
        "",
        "## Applied",
        "",
        f"- activate/update rows: `{result['applied']['activate_rows_count']}`",
        f"- deactivate rows: `{result['applied']['deactivate_rows_count']}`",
        "",
        "## Verify",
        "",
        f"- status: `{result['verify']['status']}`",
        f"- price mismatches: `{len(result['verify']['price_mismatches'])}`",
        f"- still active after deactivate: `{len(result['verify']['still_active_deactivated'])}`",
        "",
        "## Artifacts",
        "",
    ]
    for key, value in sorted(result["artifacts"].items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def run_ozon_elastic_apply(
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
    approved_rows = _read_csv(approved_plan_dir / "ozon_elastic_dry_run.csv")

    started_at = datetime.now()
    run_id = run_id or f"ozon_elastic_apply_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    raw_dir = ensure_dir(run_dir / "raw")
    processed_dir = ensure_dir(run_dir / "processed")

    preflight = run_status_preflight(credentials=credentials, data_dir=data_dir)
    if preflight["overall_status"] != "ok":
        raise RuntimeError(f"preflight is not ok: {preflight['overall_status']}")

    fresh_plan = run_ozon_elastic_plan(credentials=credentials, data_dir=data_dir)
    fresh_rows = _read_csv(Path(fresh_plan["artifacts"]["csv"]))
    drift = _assert_no_drift(
        approved_rows=approved_rows,
        fresh_rows=fresh_rows,
        approved_summary=approved_summary.get("summary", {}),
        fresh_summary=fresh_plan["summary"],
    )
    write_json(processed_dir / "drift_check.json", drift)

    activate_rows, deactivate_rows = _action_rows(fresh_rows)
    write_json(processed_dir / "activate_rows.json", activate_rows)
    write_json(processed_dir / "deactivate_rows.json", deactivate_rows)

    ozon = OzonSellerAdapter(credentials.ozon_seller)
    action_id = str(fresh_plan["summary"]["action_id"])

    activate_response: dict[str, Any] | None = None
    if activate_rows:
        activate_response = ozon.post(
            "/v1/actions/products/activate",
            {
                "action_id": action_id,
                "products": [
                    {
                        "product_id": str(row["product_id"]),
                        "offer_id": str(row["offer_id"]),
                        "action_price": str(row["calculated_action_price"]),
                    }
                    for row in activate_rows
                ],
            },
        )
        write_json(raw_dir / "ozon_activate_response.json", activate_response)

    deactivate_response: dict[str, Any] | None = None
    if deactivate_rows:
        deactivate_response = ozon.post(
            "/v1/actions/products/deactivate",
            {
                "action_id": action_id,
                "product_ids": [int(row["product_id"]) for row in deactivate_rows],
            },
        )
        write_json(raw_dir / "ozon_deactivate_response.json", deactivate_response)

    verify = _verify_ozon_elastic(
        ozon=ozon,
        action_id=action_id,
        activate_rows=activate_rows,
        deactivate_rows=deactivate_rows,
        raw_dir=ensure_dir(raw_dir / "verify"),
    )
    overall_status = "ok" if verify["status"] == "ok" else "warning"
    artifacts = {
        "run_dir": str(run_dir),
        "summary": str(run_dir / "summary.json"),
        "report": str(run_dir / "ozon_elastic_apply_result.md"),
        "drift_check": str(processed_dir / "drift_check.json"),
        "activate_rows": str(processed_dir / "activate_rows.json"),
        "deactivate_rows": str(processed_dir / "deactivate_rows.json"),
        "activate_response": str(raw_dir / "ozon_activate_response.json"),
        "deactivate_response": str(raw_dir / "ozon_deactivate_response.json"),
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
            "action_id": action_id,
            "activate_rows_count": len(activate_rows),
            "deactivate_rows_count": len(deactivate_rows),
            "activate_response": activate_response,
            "deactivate_response": deactivate_response,
        },
        "verify": verify,
        "artifacts": artifacts,
    }
    write_json(run_dir / "summary.json", result)
    _write_report(run_dir / "ozon_elastic_apply_result.md", result)
    manifest_paths = write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=result,
        task="ozon-elastic-apply",
        mode="apply",
        risk="high",
        marketplaces=["ozon"],
        inputs={"plan_run_id": plan_run_id, "confirmed_by_user": confirmed_by_user},
    )
    mark_approved_applied(
        data_dir=data_dir,
        approved_id=approved_id,
        apply_run_id=run_id,
        task="ozon-elastic-apply",
        status=overall_status,
        run_manifest_path=manifest_paths["manifest"],
        checksum=canonical_checksum({"approved_id": approved_id, "drift": drift}),
    )
    return result
