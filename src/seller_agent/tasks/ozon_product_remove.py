from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any

from seller_agent.config import AppCredentials
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.http import ApiError
from seller_agent.marketplaces.ozon.adapter import OzonSellerAdapter
from seller_agent.reports.writer import ensure_dir, write_json
from seller_agent.safety.approvals import (
    assert_apply_not_repeated,
    canonical_checksum,
    mark_approved_applied,
)


def _normalize_text(value: Any) -> str:
    text = str(value or "").strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_plan_dir(data_dir: Path, plan_run_id: str | None) -> Path:
    runs_dir = data_dir / "runs"
    candidates = (
        sorted(runs_dir.glob(f"*/{plan_run_id}"))
        if plan_run_id
        else sorted(runs_dir.glob("*/ozon_product_remove_plan_*"))
    )
    candidates = [path for path in candidates if path.is_dir() and (path / "ozon_product_remove_plan.json").exists()]
    if not candidates:
        raise FileNotFoundError(f"Ozon product remove plan not found: {plan_run_id or 'latest'}")
    return candidates[-1]


def _status_block(info: dict[str, Any]) -> dict[str, Any]:
    statuses = info.get("statuses") if isinstance(info.get("statuses"), dict) else {}
    return {
        "product_id": _normalize_text(info.get("id") or info.get("product_id")),
        "offer_id": _normalize_text(info.get("offer_id")),
        "name": _normalize_text(info.get("name")),
        "sku": _normalize_text(info.get("sku")),
        "is_archived": bool(info.get("is_archived")),
        "is_autoarchived": bool(info.get("is_autoarchived")),
        "has_stock": bool((info.get("stocks") or {}).get("has_stock")) if isinstance(info.get("stocks"), dict) else False,
        "status": _normalize_text(statuses.get("status")),
        "status_failed": _normalize_text(statuses.get("status_failed")),
        "status_description": _normalize_text(statuses.get("status_description")),
        "status_tooltip": _normalize_text(statuses.get("status_tooltip")),
        "is_created": bool(statuses.get("is_created")),
        "errors": info.get("errors") if isinstance(info.get("errors"), list) else [],
    }


def _choose_action(status: dict[str, Any], requested_action: str) -> tuple[str, list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not status.get("offer_id"):
        errors.append("ozon_offer_id_missing")
    if not status.get("product_id"):
        errors.append("ozon_product_id_missing")

    if requested_action in {"archive", "delete"}:
        action = requested_action
    elif not status.get("is_created") or not status.get("sku") or status.get("sku") == "0":
        action = "delete"
        warnings.append("ozon_not_created_or_without_sku_delete_instead_of_archive")
    else:
        action = "archive"

    if action == "archive" and status.get("has_stock"):
        errors.append("ozon_archive_requires_zero_stock")
    if action == "delete" and status.get("is_created") and status.get("sku") not in {"", "0"}:
        errors.append("ozon_delete_allowed_only_for_error_archive_without_sku")
    return action, errors, warnings


def _write_plan_report(run_dir: Path, plan: dict[str, Any]) -> Path:
    lines = [
        f"# Ozon product remove dry-run: {plan['run_id']}",
        "",
        f"- offer_id: `{plan['offer_id']}`",
        f"- product_id: `{plan['product_id']}`",
        f"- action: `{plan['action']}`",
        f"- ready: `{plan['ready']}`",
        f"- reason: {plan['reason']}",
        f"- errors: `{', '.join(plan['errors']) or 'none'}`",
        f"- warnings: `{', '.join(plan['warnings']) or 'none'}`",
        "",
        "## Source",
        "",
        "- Ozon OpenAPI: `/v1/product/archive`, `/v2/products/delete`.",
        "- Rule: not created / no SKU -> delete; created card -> archive after zero stock.",
    ]
    path = run_dir / "ozon_product_remove_dry_run.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run_ozon_product_remove_plan(
    *,
    credentials: AppCredentials,
    data_dir: Path = Path("data"),
    offer_id: str,
    product_id: str | int | None = None,
    action: str = "auto",
    reason: str = "",
    run_id: str | None = None,
) -> dict[str, Any]:
    if not credentials.ozon_seller:
        raise RuntimeError("Ozon Seller credentials are required")
    if action not in {"auto", "archive", "delete"}:
        raise ValueError("action must be auto, archive or delete")

    started_at = datetime.now()
    run_id = run_id or f"ozon_product_remove_plan_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    ozon = OzonSellerAdapter(credentials.ozon_seller)

    info_items: list[dict[str, Any]] = []
    if product_id and str(product_id).strip().isdigit():
        info_items = ozon.fetch_product_info([str(product_id)])
    if not info_items:
        attrs = ozon.fetch_product_attributes([offer_id])
        attr_product_ids = [
            _normalize_text(item.get("id") or item.get("product_id"))
            for item in attrs
            if _normalize_text(item.get("id") or item.get("product_id"))
        ]
        if attr_product_ids:
            info_items = ozon.fetch_product_info(attr_product_ids[:1])

    write_json(run_dir / "ozon_product_info_before.json", info_items)
    info = info_items[0] if info_items else {}
    status = _status_block(info)
    if not status["offer_id"]:
        status["offer_id"] = offer_id
    chosen_action, errors, warnings = _choose_action(status, action)
    ready = not errors

    plan = {
        "run_id": run_id,
        "created_at": started_at.isoformat(timespec="seconds"),
        "marketplace": "ozon",
        "offer_id": status["offer_id"],
        "product_id": status["product_id"],
        "action": chosen_action,
        "requested_action": action,
        "ready": ready,
        "reason": reason,
        "status_before": status,
        "payload": (
            {"products": [{"offer_id": status["offer_id"]}]}
            if chosen_action == "delete"
            else {"product_id": [int(status["product_id"])]} if status["product_id"].isdigit() else {}
        ),
        "errors": errors,
        "warnings": warnings,
        "source_docs": [
            "data/reference/api_docs/ozon/openapi/seller_swagger_20260629.json#/paths/~1v1~1product~1archive",
            "data/reference/api_docs/ozon/openapi/seller_swagger_20260629.json#/paths/~1v2~1products~1delete",
        ],
    }
    plan_path = run_dir / "ozon_product_remove_plan.json"
    write_json(plan_path, plan)
    report_path = _write_plan_report(run_dir, plan)
    summary = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "dry_run",
        "overall_status": "ok" if ready else "blocked",
        "action": chosen_action,
        "ready": ready,
        "errors": errors,
        "warnings": warnings,
        "artifacts": {"run_dir": str(run_dir), "plan": str(plan_path), "report": str(report_path)},
    }
    write_json(run_dir / "summary.json", summary)
    manifest = write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=summary,
        task="ozon-product-remove-plan",
        mode="dry_run",
        risk="high",
        marketplaces=["ozon"],
        inputs={"offer_id": offer_id, "product_id": str(product_id or ""), "action": action, "reason": reason},
        pending_id=run_id,
        lifecycle_status="pending_review",
    )
    summary["artifacts"]["run_manifest"] = manifest["manifest"]
    write_json(run_dir / "summary.json", summary)
    return summary


def run_ozon_product_remove_apply(
    *,
    credentials: AppCredentials,
    data_dir: Path = Path("data"),
    plan_run_id: str | None = None,
    run_id: str | None = None,
    confirmed_by_user: bool = False,
) -> dict[str, Any]:
    if not confirmed_by_user:
        raise RuntimeError("Apply requires explicit user confirmation")
    if not credentials.ozon_seller:
        raise RuntimeError("Ozon Seller credentials are required")

    started_at = datetime.now()
    run_id = run_id or f"ozon_product_remove_apply_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    plan_dir = _resolve_plan_dir(data_dir, plan_run_id)
    approved_id = plan_dir.name
    assert_apply_not_repeated(data_dir=data_dir, approved_id=approved_id, current_run_id=run_id)
    plan = _read_json(plan_dir / "ozon_product_remove_plan.json")
    if not isinstance(plan, dict) or not plan.get("ready"):
        raise RuntimeError(f"Ozon product remove plan is not ready: {plan_dir}")

    action = _normalize_text(plan.get("action"))
    offer_id = _normalize_text(plan.get("offer_id"))
    product_id = _normalize_text(plan.get("product_id"))
    ozon = OzonSellerAdapter(credentials.ozon_seller)

    if action == "delete":
        response = ozon.delete_products([offer_id])
    elif action == "archive":
        response = ozon.archive_products([product_id])
    else:
        raise RuntimeError(f"Unsupported Ozon product remove action: {action}")
    write_json(run_dir / "ozon_product_remove_response.json", response)

    info_after: list[dict[str, Any]] = []
    if product_id:
        try:
            info_after = ozon.fetch_product_info([product_id])
        except ApiError as exc:
            write_json(run_dir / "ozon_product_info_after_error.json", {"status": exc.status, "message": exc.message})
            info_after = []
    write_json(run_dir / "ozon_product_info_after.json", info_after)
    status_after = _status_block(info_after[0]) if info_after else {}

    if action == "delete":
        response_status = response.get("status") if isinstance(response.get("status"), list) else []
        matched = next((item for item in response_status if _normalize_text(item.get("offer_id")) == offer_id), {})
        verified = bool(matched.get("is_deleted")) and not bool(status_after.get("is_created"))
        verify_reason = matched.get("error") or ("deleted_response_and_not_created" if verified else "delete_not_verified")
    else:
        verified = bool(status_after.get("is_archived"))
        verify_reason = "is_archived_true" if verified else "archive_not_verified"

    summary = {
        "overall_status": "ok" if verified else "warning",
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "apply",
        "plan_run_id": approved_id,
        "action": action,
        "offer_id": offer_id,
        "product_id": product_id,
        "verified": verified,
        "verify_reason": verify_reason,
        "status_before": plan.get("status_before"),
        "status_after": status_after,
        "response": response,
    }
    write_json(run_dir / "summary.json", summary)
    checksum = canonical_checksum(plan)
    manifest = write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=summary,
        task="ozon-product-remove-apply",
        mode="apply",
        risk="high",
        marketplaces=["ozon"],
        inputs={"plan_run_id": approved_id, "confirmed_by_user": confirmed_by_user},
        source_run_ids=[approved_id],
        approved_id=approved_id,
        applied_by_run_id=run_id,
        lifecycle_status="verified" if verified else "failed",
        closed=verified,
    )
    summary.setdefault("artifacts", {})
    summary["artifacts"]["run_manifest"] = manifest["manifest"]
    write_json(run_dir / "summary.json", summary)
    if verified:
        mark_approved_applied(
            data_dir=data_dir,
            approved_id=approved_id,
            apply_run_id=run_id,
            task="ozon-product-remove-apply",
            status="verified",
            run_manifest_path=manifest["manifest"],
            checksum=checksum,
        )
    return {
        "overall_status": "ok" if verified else "warning",
        "run_id": run_id,
        "plan_run_id": approved_id,
        "action": action,
        "verified": verified,
        "verify_reason": verify_reason,
        "artifacts": {
            "summary": str(run_dir / "summary.json"),
            "response": str(run_dir / "ozon_product_remove_response.json"),
            "info_after": str(run_dir / "ozon_product_info_after.json"),
        },
    }


def run_ozon_product_remove_verify(
    *,
    credentials: AppCredentials,
    data_dir: Path = Path("data"),
    plan_run_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Verify Ozon delete/archive state without repeating the write operation."""
    if not credentials.ozon_seller:
        raise RuntimeError("Ozon Seller credentials are required")

    started_at = datetime.now()
    run_id = run_id or f"ozon_product_remove_verify_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    plan_dir = _resolve_plan_dir(data_dir, plan_run_id)
    plan = _read_json(plan_dir / "ozon_product_remove_plan.json")
    if not isinstance(plan, dict) or not plan.get("ready"):
        raise RuntimeError(f"Ozon product remove plan is not ready: {plan_dir}")

    action = _normalize_text(plan.get("action"))
    offer_id = _normalize_text(plan.get("offer_id"))
    product_id = _normalize_text(plan.get("product_id"))
    ozon = OzonSellerAdapter(credentials.ozon_seller)
    info_items: list[dict[str, Any]] = []
    attributes: list[dict[str, Any]] = []
    if product_id:
        try:
            info_items = ozon.fetch_product_info([product_id])
        except ApiError as exc:
            if exc.status != 404:
                raise
    try:
        attributes = ozon.fetch_product_attributes([offer_id])
    except ApiError as exc:
        if exc.status != 404:
            raise
    write_json(run_dir / "ozon_product_info_verify.json", info_items)
    write_json(run_dir / "ozon_product_attributes_verify.json", attributes)

    status_after = _status_block(info_items[0]) if info_items else {}
    matching_attributes = [
        item for item in attributes if _normalize_text(item.get("offer_id")) == offer_id
    ]
    if action == "delete":
        checks = {
            "offer_absent_from_attributes": not matching_attributes,
            "product_not_created": not bool(status_after.get("is_created")),
        }
        verify_reason = "offer_absent_and_product_not_created" if all(checks.values()) else "delete_not_verified"
    elif action == "archive":
        checks = {
            "product_info_found": bool(info_items),
            "is_archived": bool(status_after.get("is_archived")),
        }
        verify_reason = "is_archived_true" if all(checks.values()) else "archive_not_verified"
    else:
        raise RuntimeError(f"Unsupported Ozon product remove action: {action}")

    verified = all(checks.values())
    summary = {
        "overall_status": "ok" if verified else "warning",
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "verify",
        "approved_plan_run_id": plan_dir.name,
        "action": action,
        "offer_id": offer_id,
        "product_id": product_id,
        "verified": verified,
        "verify_reason": verify_reason,
        "checks": checks,
        "status_after": status_after,
        "verify": {"status": "ok" if verified else "warning", "checks": checks},
        "artifacts": {
            "run_dir": str(run_dir),
            "product_info": str(run_dir / "ozon_product_info_verify.json"),
            "attributes": str(run_dir / "ozon_product_attributes_verify.json"),
            "summary": str(run_dir / "summary.json"),
        },
    }
    write_json(run_dir / "summary.json", summary)
    manifest = write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=summary,
        task="ozon-product-remove-verify",
        mode="verify",
        risk="low",
        marketplaces=["ozon"],
        inputs={"plan_run_id": plan_dir.name},
        source_run_ids=[plan_dir.name],
        approved_id=plan_dir.name,
        lifecycle_status="verified" if verified else "created",
        closed=verified,
    )
    summary["artifacts"]["run_manifest"] = manifest["manifest"]
    write_json(run_dir / "summary.json", summary)
    return summary
