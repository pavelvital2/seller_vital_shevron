from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from seller_agent.config import AppCredentials
from seller_agent.core.run_manifest import latest_run, write_summary_run_manifest
from seller_agent.marketplaces.ozon.adapter import OzonSellerAdapter
from seller_agent.reports.writer import ensure_dir, write_json


ATTR_MARKING_REQUIRED = 23536


def run_ozon_partial_approved_diagnose(
    *,
    credentials: AppCredentials,
    data_dir: Path = Path("data"),
    run_id: str | None = None,
    ozon_adapter: OzonSellerAdapter | None = None,
) -> dict[str, Any]:
    started_at = datetime.now()
    run_id = run_id or f"ozon_partial_approved_diagnose_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)

    if not credentials.ozon_seller and ozon_adapter is None:
        result = _blocked_result(run_id, run_dir, "missing Ozon Seller API credentials")
        _write_diagnose_outputs(data_dir, run_dir, started_at, result)
        return result

    ozon = ozon_adapter or OzonSellerAdapter(credentials.ozon_seller)  # type: ignore[arg-type]
    partial_list = ozon.post("/v3/product/list", {"filter": {"visibility": "PARTIAL_APPROVED"}, "last_id": "", "limit": 1000})
    partial_items = (partial_list.get("result") or {}).get("items") or []
    product_ids = [str(item.get("product_id")) for item in partial_items if item.get("product_id")]
    offer_ids = [str(item.get("offer_id")) for item in partial_items if item.get("offer_id")]

    info_items = ozon.fetch_product_info(product_ids)
    attrs_items = ozon.fetch_product_attributes(offer_ids)
    write_json(run_dir / "ozon_partial_approved_list.json", partial_list)
    write_json(run_dir / "ozon_product_info.json", {"items": info_items})
    write_json(run_dir / "ozon_product_attributes.json", {"items": attrs_items})

    rows = _diagnose_rows(partial_items=partial_items, info_items=info_items, attrs_items=attrs_items)
    recovery_items = [_recovery_item(row["offer_id"]) for row in rows if row["recommended_recovery"] == "resave_23536_false"]
    recovery_payload = {"items": recovery_items}
    write_json(run_dir / "recovery_request_dry_run.json", recovery_payload)

    summary = {
        "partial_approved_count": len(partial_items),
        "active_error_rows": sum(1 for row in rows if row["classification"] == "active_errors"),
        "stale_visibility_rows": sum(1 for row in rows if row["classification"] == "stale_visibility"),
        "ready_recovery_rows": len(recovery_items),
        "blocked_rows": sum(1 for row in rows if not row["recovery_ready"]),
    }
    overall_status = "ok" if not rows else "warning"
    result = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "dry_run",
        "task": "ozon-partial-approved-diagnose",
        "overall_status": overall_status,
        "summary": summary,
        "rows": rows,
        "artifacts": {
            "run_dir": str(run_dir),
            "summary": str(run_dir / "summary.json"),
            "report": str(run_dir / "ozon_partial_approved_diagnose_report.md"),
            "recovery_request": str(run_dir / "recovery_request_dry_run.json"),
            "partial_list": str(run_dir / "ozon_partial_approved_list.json"),
            "product_info": str(run_dir / "ozon_product_info.json"),
            "product_attributes": str(run_dir / "ozon_product_attributes.json"),
            "run_manifest": str(run_dir / "manifest.json"),
        },
    }
    _write_diagnose_outputs(data_dir, run_dir, started_at, result)
    return result


def run_ozon_partial_approved_recovery_apply(
    *,
    credentials: AppCredentials,
    data_dir: Path = Path("data"),
    plan_run_id: str | None = None,
    run_id: str | None = None,
    confirmed_by_user: bool = False,
    wait_seconds: int = 120,
    poll_interval: int = 5,
    ozon_adapter: OzonSellerAdapter | None = None,
) -> dict[str, Any]:
    started_at = datetime.now()
    run_id = run_id or f"ozon_partial_approved_recovery_apply_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)

    if not confirmed_by_user:
        result = _blocked_result(run_id, run_dir, "missing explicit owner confirmation")
        _write_apply_outputs(data_dir, run_dir, started_at, result)
        return result
    if not credentials.ozon_seller and ozon_adapter is None:
        result = _blocked_result(run_id, run_dir, "missing Ozon Seller API credentials")
        _write_apply_outputs(data_dir, run_dir, started_at, result)
        return result

    plan_dir = _find_plan_dir(data_dir=data_dir, plan_run_id=plan_run_id)
    plan_summary = json.loads((plan_dir / "summary.json").read_text(encoding="utf-8"))
    request_path = plan_dir / "recovery_request_dry_run.json"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    items = request.get("items") or []
    if not items:
        result = _blocked_result(run_id, run_dir, "approved diagnose plan has no recovery items")
        _write_apply_outputs(data_dir, run_dir, started_at, result)
        return result

    ozon = ozon_adapter or OzonSellerAdapter(credentials.ozon_seller)  # type: ignore[arg-type]
    before = run_ozon_partial_approved_diagnose(
        credentials=credentials,
        data_dir=data_dir,
        run_id=f"{run_id}_fresh_preflight",
        ozon_adapter=ozon,
    )
    write_json(run_dir / "fresh_preflight_summary.json", before)
    if before["summary"]["ready_recovery_rows"] != len(items):
        result = _blocked_result(run_id, run_dir, "fresh preflight differs from approved recovery package")
        result["approved_plan"] = str(plan_dir)
        result["fresh_preflight"] = before.get("artifacts", {})
        _write_apply_outputs(data_dir, run_dir, started_at, result)
        return result

    write_json(run_dir / "ozon_attributes_update_request.json", {"items": items})
    response = ozon.update_product_attributes(items)
    write_json(run_dir / "ozon_attributes_update_response.json", response)
    task_id = _task_id(response)
    import_info: dict[str, Any] = {}
    if task_id:
        import_info = _wait_ozon_import(ozon, int(task_id), run_dir, wait_seconds, poll_interval)

    # Give Ozon visibility index a short chance to recalculate.
    time.sleep(min(max(poll_interval, 1), 10))
    after = run_ozon_partial_approved_diagnose(
        credentials=credentials,
        data_dir=data_dir,
        run_id=f"{run_id}_verify",
        ozon_adapter=ozon,
    )
    write_json(run_dir / "verify_summary.json", after)
    status = "ok" if after["summary"]["partial_approved_count"] == 0 else "warning"
    result = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "apply",
        "task": "ozon-partial-approved-recovery-apply",
        "overall_status": status,
        "approved_plan_run_id": plan_summary.get("run_id") or plan_dir.name,
        "task_id": task_id,
        "summary": {
            "items_count": len(items),
            "partial_approved_before": before["summary"]["partial_approved_count"],
            "partial_approved_after": after["summary"]["partial_approved_count"],
            "ready_recovery_before": before["summary"]["ready_recovery_rows"],
            "ready_recovery_after": after["summary"]["ready_recovery_rows"],
        },
        "artifacts": {
            "run_dir": str(run_dir),
            "summary": str(run_dir / "summary.json"),
            "report": str(run_dir / "ozon_partial_approved_recovery_apply_report.md"),
            "request": str(run_dir / "ozon_attributes_update_request.json"),
            "response": str(run_dir / "ozon_attributes_update_response.json"),
            "fresh_preflight": str(run_dir / "fresh_preflight_summary.json"),
            "verify": str(run_dir / "verify_summary.json"),
            "run_manifest": str(run_dir / "manifest.json"),
        },
        "import_info": import_info,
    }
    _write_apply_outputs(data_dir, run_dir, started_at, result)
    return result


def _diagnose_rows(
    *,
    partial_items: list[dict[str, Any]],
    info_items: list[dict[str, Any]],
    attrs_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    info_by_offer = {str(item.get("offer_id") or ""): item for item in info_items}
    attrs_by_offer = {str(item.get("offer_id") or ""): item for item in attrs_items}
    rows: list[dict[str, Any]] = []
    for item in partial_items:
        offer_id = str(item.get("offer_id") or "")
        info = info_by_offer.get(offer_id, {})
        attrs = attrs_by_offer.get(offer_id, {})
        statuses = info.get("statuses") or {}
        errors = statuses.get("item_errors") or []
        mark_values = _attr_values(attrs, ATTR_MARKING_REQUIRED)
        mark_false = any(str(value.get("value", "")).lower() == "false" for value in mark_values)
        status_name = str(statuses.get("status_name") or "")
        validation_status = str(statuses.get("validation_status") or "")
        if errors:
            classification = "active_errors"
            recommended_recovery = "manual_error_specific_recovery"
        elif status_name == "Продается" and validation_status == "success" and mark_false:
            classification = "stale_visibility"
            recommended_recovery = "resave_23536_false"
        else:
            classification = "needs_manual_review"
            recommended_recovery = "manual_review"
        rows.append(
            {
                "offer_id": offer_id,
                "product_id": str(item.get("product_id") or info.get("id") or attrs.get("id") or ""),
                "status_name": status_name,
                "validation_status": validation_status,
                "item_errors": errors,
                "current_23536": mark_values,
                "classification": classification,
                "recommended_recovery": recommended_recovery,
                "recovery_ready": recommended_recovery == "resave_23536_false",
            }
        )
    return rows


def _attr_values(item: dict[str, Any], attr_id: int) -> list[dict[str, Any]]:
    for attr in item.get("attributes") or []:
        if int(attr.get("id") or 0) == attr_id:
            return [value for value in attr.get("values") or [] if isinstance(value, dict)]
    return []


def _recovery_item(offer_id: str) -> dict[str, Any]:
    return {
        "offer_id": offer_id,
        "attributes": [
            {
                "id": ATTR_MARKING_REQUIRED,
                "complex_id": 0,
                "values": [{"dictionary_value_id": 0, "value": "false"}],
            }
        ],
    }


def _task_id(response: dict[str, Any]) -> Any:
    result = response.get("result") if isinstance(response.get("result"), dict) else {}
    return result.get("task_id") or response.get("task_id")


def _wait_ozon_import(
    ozon: OzonSellerAdapter,
    task_id: int,
    run_dir: Path,
    wait_seconds: int,
    poll_interval: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + max(wait_seconds, 0)
    attempt = 0
    result: dict[str, Any] = {}
    while True:
        attempt += 1
        result = ozon.fetch_product_import_info(task_id)
        write_json(run_dir / f"ozon_import_info_attempt_{attempt:02d}.json", result)
        text = json.dumps(result, ensure_ascii=False).lower()
        if "imported" in text or "failed" in text or time.monotonic() >= deadline:
            return result
        time.sleep(max(poll_interval, 1))


def _find_plan_dir(*, data_dir: Path, plan_run_id: str | None) -> Path:
    if plan_run_id:
        for path in sorted((data_dir / "runs").glob(f"*/{plan_run_id}")):
            if path.is_dir() and (path / "recovery_request_dry_run.json").exists():
                return path
        direct = Path(plan_run_id)
        if direct.is_dir() and (direct / "recovery_request_dry_run.json").exists():
            return direct
        raise FileNotFoundError(f"Ozon partial approved diagnose run not found: {plan_run_id}")
    latest = latest_run(data_dir=data_dir, task="ozon-partial-approved-diagnose")
    if not latest:
        raise FileNotFoundError("No ozon-partial-approved-diagnose run found.")
    run_dir = Path(str((latest.get("artifacts") or {}).get("run_dir") or ""))
    if not run_dir.is_absolute():
        run_dir = Path.cwd() / run_dir
    return run_dir


def _blocked_result(run_id: str, run_dir: Path, reason: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "mode": "blocked",
        "task": "ozon-partial-approved",
        "overall_status": "blocked",
        "blocked_reason": reason,
        "summary": {},
        "artifacts": {
            "run_dir": str(run_dir),
            "summary": str(run_dir / "summary.json"),
            "report": str(run_dir / "report.md"),
        },
    }


def _write_diagnose_outputs(data_dir: Path, run_dir: Path, started_at: datetime, result: dict[str, Any]) -> None:
    write_json(run_dir / "summary.json", result)
    _write_report(run_dir / "ozon_partial_approved_diagnose_report.md", result)
    write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=result,
        task="ozon-partial-approved-diagnose",
        mode="dry_run",
        risk="normal",
        marketplaces=["ozon"],
        inputs={"started_at": started_at.isoformat(timespec="seconds")},
    )


def _write_apply_outputs(data_dir: Path, run_dir: Path, started_at: datetime, result: dict[str, Any]) -> None:
    write_json(run_dir / "summary.json", result)
    _write_report(run_dir / "ozon_partial_approved_recovery_apply_report.md", result)
    write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=result,
        task="ozon-partial-approved-recovery-apply",
        mode="apply",
        risk="high",
        marketplaces=["ozon"],
        inputs={"started_at": started_at.isoformat(timespec="seconds")},
        lifecycle_status="verified" if result.get("overall_status") == "ok" else "applied",
        closed=result.get("overall_status") == "ok",
    )


def _write_report(path: Path, result: dict[str, Any]) -> None:
    lines = [
        "# Ozon PARTIAL_APPROVED Diagnostics",
        "",
        f"Run ID: `{result.get('run_id')}`",
        f"Status: `{result.get('overall_status')}`",
        "",
        "## Summary",
        "",
    ]
    for key, value in sorted((result.get("summary") or {}).items()):
        lines.append(f"- `{key}`: `{value}`")
    if result.get("blocked_reason"):
        lines.extend(["", "## Blocked", "", f"- `{result['blocked_reason']}`"])
    rows = result.get("rows") or []
    if rows:
        lines.extend(["", "## Rows", ""])
        for row in rows:
            lines.append(
                f"- `{row['offer_id']}` / `{row['product_id']}`: "
                f"`{row['classification']}` -> `{row['recommended_recovery']}`"
            )
    lines.extend(["", "## Artifacts", ""])
    for key, value in sorted((result.get("artifacts") or {}).items()):
        lines.append(f"- `{key}`: `{value}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
