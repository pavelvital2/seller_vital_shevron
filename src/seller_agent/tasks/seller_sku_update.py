from __future__ import annotations

import csv
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import time
from typing import Any

from seller_agent.config import AppCredentials
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.http import ApiError
from seller_agent.marketplaces.ozon.adapter import OzonSellerAdapter
from seller_agent.marketplaces.wb.adapter import WbContentAdapter
from seller_agent.reports.writer import ensure_dir, write_json
from seller_agent.safety.approvals import (
    assert_apply_not_repeated,
    canonical_checksum,
    mark_approved_applied,
)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _normalize_int_text(value: Any) -> str:
    text = str(value or "").strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text


def _operation_from_product_row(row: dict[str, str]) -> dict[str, Any]:
    internal_sku = str(row.get("internal_sku") or row.get("master_sku") or "").strip()
    if not internal_sku:
        raise ValueError("product row has no internal_sku/master_sku")
    operation: dict[str, Any] = {"internal_sku": internal_sku}
    ozon_offer_id = str(row.get("ozon_offer_id") or "").strip()
    ozon_product_id = _normalize_int_text(row.get("ozon_product_id"))
    if ozon_offer_id and ozon_offer_id != internal_sku:
        operation["ozon"] = {
            "old_offer_id": ozon_offer_id,
            "new_offer_id": internal_sku,
            "product_id": ozon_product_id,
        }
    wb_vendor_code = str(row.get("wb_vendor_code") or "").strip()
    wb_nm_id = _normalize_int_text(row.get("wb_nm_id"))
    if wb_vendor_code and wb_vendor_code != internal_sku:
        operation["wb"] = {
            "old_vendor_code": wb_vendor_code,
            "new_vendor_code": internal_sku,
            "nm_id": wb_nm_id,
        }
    return operation


def normalize_seller_sku_operations(
    *,
    input_path: Path | None,
    internal_skus: list[str],
    products_path: Path,
) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    if input_path:
        payload = _read_json(input_path)
        raw_operations = payload.get("operations") if isinstance(payload, dict) else payload
        if not isinstance(raw_operations, list):
            raise ValueError("input must be a list or an object with operations list")
        for item in raw_operations:
            if not isinstance(item, dict):
                raise ValueError("each seller SKU operation must be an object")
            operations.append(_normalize_operation(item))

    if internal_skus:
        rows = _read_csv(products_path)
        by_sku = {str(row.get("internal_sku") or row.get("master_sku") or "").strip(): row for row in rows}
        for internal_sku in internal_skus:
            row = by_sku.get(internal_sku)
            if not row:
                raise ValueError(f"internal_sku not found in products: {internal_sku}")
            operations.append(_normalize_operation(_operation_from_product_row(row)))

    if not operations:
        raise ValueError("no operations: pass --input or --internal-sku")
    return operations


def _normalize_operation(item: dict[str, Any]) -> dict[str, Any]:
    internal_sku = str(item.get("internal_sku") or "").strip()
    if not internal_sku:
        raise ValueError("seller SKU operation has no internal_sku")
    operation: dict[str, Any] = {"internal_sku": internal_sku}

    ozon = item.get("ozon") if isinstance(item.get("ozon"), dict) else {}
    old_offer_id = str(ozon.get("old_offer_id") or ozon.get("offer_id") or "").strip()
    new_offer_id = str(ozon.get("new_offer_id") or internal_sku).strip()
    product_id = _normalize_int_text(ozon.get("product_id") or ozon.get("ozon_product_id"))
    if old_offer_id and old_offer_id != new_offer_id:
        operation["ozon"] = {
            "old_offer_id": old_offer_id,
            "new_offer_id": new_offer_id,
            "product_id": product_id,
        }

    wb = item.get("wb") if isinstance(item.get("wb"), dict) else {}
    old_vendor_code = str(wb.get("old_vendor_code") or wb.get("vendor_code") or wb.get("vendorCode") or "").strip()
    new_vendor_code = str(wb.get("new_vendor_code") or internal_sku).strip()
    nm_id = _normalize_int_text(wb.get("nm_id") or wb.get("nmID") or wb.get("wb_nm_id"))
    if old_vendor_code and old_vendor_code != new_vendor_code:
        operation["wb"] = {
            "old_vendor_code": old_vendor_code,
            "new_vendor_code": new_vendor_code,
            "nm_id": nm_id,
        }

    if "ozon" not in operation and "wb" not in operation:
        operation["status"] = "already_ok"
    return operation


def _fetch_ozon_one(ozon: OzonSellerAdapter, offer_id: str) -> dict[str, Any]:
    try:
        items = ozon.fetch_product_attributes([offer_id])
        return {
            "offer_id": offer_id,
            "status": "found" if items else "not_found_empty",
            "items": items,
        }
    except ApiError as exc:
        if exc.status == 404 and "item not found" in exc.message.lower():
            return {
                "offer_id": offer_id,
                "status": "not_found_404",
                "items": [],
                "api_error": {"status": exc.status, "message": exc.message[:500]},
            }
        raise


def _product_ids_from_ozon_result(result: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for item in result.get("items") or []:
        value = _normalize_int_text(item.get("id") or item.get("product_id"))
        if value:
            ids.add(value)
    return ids


def _ozon_precheck(
    *,
    ozon: OzonSellerAdapter | None,
    operation: dict[str, Any],
    run_dir: Path,
    skip_api: bool,
) -> dict[str, Any]:
    ozon_op = operation.get("ozon")
    if not isinstance(ozon_op, dict):
        return {"status": "not_requested"}
    if skip_api:
        return {"status": "api_skipped", "ready": False, "manual_review": True}
    if ozon is None:
        return {"status": "blocked", "ready": False, "errors": ["missing_ozon_credentials"]}

    old_offer_id = ozon_op["old_offer_id"]
    new_offer_id = ozon_op["new_offer_id"]
    old_result = _fetch_ozon_one(ozon, old_offer_id)
    new_result = _fetch_ozon_one(ozon, new_offer_id)
    write_json(run_dir / f"ozon_precheck_old_{old_offer_id}.json", old_result)
    write_json(run_dir / f"ozon_precheck_new_{new_offer_id}.json", new_result)

    errors: list[str] = []
    if old_result["status"] != "found":
        errors.append("old_offer_not_found")
    expected_product_id = str(ozon_op.get("product_id") or "").strip()
    found_product_ids = _product_ids_from_ozon_result(old_result)
    if expected_product_id and expected_product_id not in found_product_ids:
        errors.append("product_id_drift")
    if new_result["status"] == "found":
        errors.append("new_offer_already_exists")
    return {
        "status": "ready" if not errors else "blocked",
        "ready": not errors,
        "old_offer_id": old_offer_id,
        "new_offer_id": new_offer_id,
        "expected_product_id": expected_product_id,
        "found_product_ids": sorted(found_product_ids),
        "new_offer_status": new_result["status"],
        "errors": errors,
    }


def _wb_precheck(
    *,
    wb: WbContentAdapter | None,
    operation: dict[str, Any],
    run_dir: Path,
    skip_api: bool,
) -> dict[str, Any]:
    wb_op = operation.get("wb")
    if not isinstance(wb_op, dict):
        return {"status": "not_requested"}
    if skip_api:
        return {"status": "api_skipped", "ready": False, "manual_review": True}
    if wb is None:
        return {"status": "blocked", "ready": False, "errors": ["missing_wb_credentials"]}

    old_vendor_code = wb_op["old_vendor_code"]
    new_vendor_code = wb_op["new_vendor_code"]
    old_cards = wb.find_cards_by_vendor_codes({old_vendor_code})
    new_cards = wb.find_cards_by_vendor_codes({new_vendor_code})
    write_json(run_dir / f"wb_precheck_old_{old_vendor_code}.json", old_cards)
    write_json(run_dir / f"wb_precheck_new_{new_vendor_code}.json", new_cards)

    errors: list[str] = []
    old_card = old_cards.get(old_vendor_code)
    if not old_card:
        errors.append("old_vendor_not_found")
    expected_nm_id = str(wb_op.get("nm_id") or "").strip()
    found_nm_id = _normalize_int_text((old_card or {}).get("nmID"))
    if expected_nm_id and found_nm_id != expected_nm_id:
        errors.append("nm_id_drift")
    if new_vendor_code in new_cards:
        errors.append("new_vendor_already_exists")
    return {
        "status": "ready" if not errors else "blocked",
        "ready": not errors,
        "old_vendor_code": old_vendor_code,
        "new_vendor_code": new_vendor_code,
        "expected_nm_id": expected_nm_id,
        "found_nm_id": found_nm_id,
        "errors": errors,
    }


def _build_plan(
    *,
    operations: list[dict[str, Any]],
    credentials: AppCredentials,
    run_dir: Path,
    skip_api: bool,
) -> list[dict[str, Any]]:
    ozon = OzonSellerAdapter(credentials.ozon_seller) if credentials.ozon_seller else None
    wb = WbContentAdapter(credentials.wb) if credentials.wb else None
    planned: list[dict[str, Any]] = []
    for operation in operations:
        ozon_check = _ozon_precheck(ozon=ozon, operation=operation, run_dir=run_dir, skip_api=skip_api)
        wb_check = _wb_precheck(wb=wb, operation=operation, run_dir=run_dir, skip_api=skip_api)
        ready = (
            operation.get("status") == "already_ok"
            or (
                ozon_check.get("status") in {"not_requested", "ready"}
                and wb_check.get("status") in {"not_requested", "ready"}
            )
        )
        planned.append(
            {
                **operation,
                "ready": ready,
                "ozon_precheck": ozon_check,
                "wb_precheck": wb_check,
            }
        )
    return planned


def _plan_report(run_id: str, summary: dict[str, Any], plan_path: Path) -> str:
    lines = [
        "# Seller SKU Update Plan",
        "",
        f"Run ID: `{run_id}`",
        "",
        "## Summary",
        "",
    ]
    for key in sorted(summary):
        lines.append(f"- `{key}`: `{summary[key]}`")
    lines.extend(
        [
            "",
            "## Next",
            "",
            "Apply only after owner approval:",
            "",
            "```bash",
            "PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \\",
            "  -m seller_agent.cli apply-seller-sku-update \\",
            f"  --plan-run-id {run_id} \\",
            "  --confirmed-by-user",
            "```",
            "",
            f"Plan file: `{plan_path}`",
            "",
        ]
    )
    return "\n".join(lines)


def run_seller_sku_update_plan(
    *,
    credentials: AppCredentials,
    data_dir: Path = Path("data"),
    input_path: Path | None = None,
    internal_skus: list[str] | None = None,
    products_path: Path | None = None,
    run_id: str | None = None,
    skip_api: bool = False,
) -> dict[str, Any]:
    started_at = datetime.now()
    run_id = run_id or f"seller_sku_update_plan_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    products_path = products_path or data_dir / "catalog" / "unified" / "products.csv"
    operations = normalize_seller_sku_operations(
        input_path=input_path,
        internal_skus=internal_skus or [],
        products_path=products_path,
    )
    plan = _build_plan(
        operations=operations,
        credentials=credentials,
        run_dir=run_dir,
        skip_api=skip_api,
    )
    plan_path = run_dir / "seller_sku_update_plan.json"
    write_json(plan_path, plan)

    ready_rows = sum(1 for row in plan if row.get("ready"))
    blocked_rows = sum(1 for row in plan if not row.get("ready"))
    summary = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "dry_run",
        "overall_status": "ok" if blocked_rows == 0 else "warning",
        "input_rows": len(plan),
        "ready_rows": ready_rows,
        "blocked_rows": blocked_rows,
        "skip_api": skip_api,
        "artifacts": {
            "run_dir": str(run_dir),
            "seller_sku_update_plan": str(plan_path),
            "report": str(run_dir / "seller_sku_update_plan_report.md"),
        },
    }
    report_path = run_dir / "seller_sku_update_plan_report.md"
    report_path.write_text(_plan_report(run_id, summary, plan_path), encoding="utf-8")
    write_json(run_dir / "summary.json", summary)
    manifest = write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=summary,
        task="seller-sku-update-plan",
        mode="dry_run",
        risk="high",
        marketplaces=["ozon", "wb"],
        inputs={
            "input_path": str(input_path) if input_path else "",
            "internal_skus": internal_skus or [],
            "products_path": str(products_path),
            "skip_api": skip_api,
        },
        pending_id=run_id,
        lifecycle_status="pending_review",
    )
    summary["artifacts"]["run_manifest"] = manifest["manifest"]
    write_json(run_dir / "summary.json", summary)
    return summary


def _resolve_plan_dir(data_dir: Path, plan_run_id: str | None) -> Path:
    runs_dir = data_dir / "runs"
    if plan_run_id:
        candidates = sorted(runs_dir.glob(f"*/{plan_run_id}"))
    else:
        candidates = sorted(runs_dir.glob("*/seller_sku_update_plan_*"))
    candidates = [path for path in candidates if path.is_dir() and (path / "seller_sku_update_plan.json").exists()]
    if not candidates:
        label = plan_run_id or "latest seller_sku_update_plan_*"
        raise FileNotFoundError(f"Seller SKU update plan not found: {label}")
    return candidates[-1]


def _build_wb_vendor_update_variant(card: dict[str, Any], new_vendor_code: str) -> dict[str, Any]:
    variant = {
        "nmID": int(card["nmID"]),
        "vendorCode": new_vendor_code,
        "title": card.get("title") or "",
        "description": card.get("description") or "",
        "brand": card.get("brand") or "VitalEmb",
        "dimensions": deepcopy(card.get("dimensions") or {}),
        "characteristics": deepcopy(card.get("characteristics") or []),
        "sizes": deepcopy(card.get("sizes") or []),
    }
    if "kizMarked" in card:
        variant["kizMarked"] = bool(card.get("kizMarked"))
    return variant


def _apply_ozon_updates(
    *,
    ozon: OzonSellerAdapter,
    plan: list[dict[str, Any]],
    run_dir: Path,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for row in plan:
        ozon_op = row.get("ozon")
        if not row.get("ready") or not isinstance(ozon_op, dict):
            continue
        payload_rows = [
            {
                "offer_id": ozon_op["old_offer_id"],
                "new_offer_id": ozon_op["new_offer_id"],
            }
        ]
        request_path = run_dir / f"ozon_update_offer_id_{ozon_op['old_offer_id']}_request.json"
        response_path = run_dir / f"ozon_update_offer_id_{ozon_op['old_offer_id']}_response.json"
        write_json(request_path, {"update_offer_id": payload_rows})
        try:
            response = ozon.update_offer_ids(payload_rows)
            write_json(response_path, response)
            ok = not response.get("errors")
            results.append({"operation": ozon_op, "ok": ok, "response": str(response_path)})
            if not ok:
                break
        except ApiError as exc:
            error = {"status": exc.status, "message": exc.message[:1000]}
            write_json(response_path, {"error": error})
            results.append({"operation": ozon_op, "ok": False, "error": error})
            break
    return results


def _apply_wb_updates(
    *,
    wb: WbContentAdapter,
    plan: list[dict[str, Any]],
    run_dir: Path,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for row in plan:
        wb_op = row.get("wb")
        if not row.get("ready") or not isinstance(wb_op, dict):
            continue
        found = wb.find_cards_by_vendor_codes({wb_op["old_vendor_code"]})
        write_json(run_dir / f"wb_before_update_{wb_op['old_vendor_code']}.json", found)
        card = found.get(wb_op["old_vendor_code"])
        if not card:
            results.append({"operation": wb_op, "ok": False, "error": "old_vendor_not_found_before_apply"})
            break
        variant = _build_wb_vendor_update_variant(card, wb_op["new_vendor_code"])
        payload = [variant]
        request_path = run_dir / f"wb_update_vendor_code_{wb_op['old_vendor_code']}_request.json"
        response_path = run_dir / f"wb_update_vendor_code_{wb_op['old_vendor_code']}_response.json"
        write_json(request_path, payload)
        try:
            response = wb.update_cards(payload)
            write_json(response_path, response)
            ok = not response.get("error")
            results.append({"operation": wb_op, "ok": ok, "response": str(response_path)})
            if not ok:
                break
        except ApiError as exc:
            error = {"status": exc.status, "message": exc.message[:1000]}
            write_json(response_path, {"error": error})
            results.append({"operation": wb_op, "ok": False, "error": error})
            break
    return results


def _verify_ozon_updates(
    *,
    ozon: OzonSellerAdapter,
    plan: list[dict[str, Any]],
    run_dir: Path,
) -> list[dict[str, Any]]:
    product_ids = [
        str(row["ozon"].get("product_id") or "").strip()
        for row in plan
        if row.get("ready") and isinstance(row.get("ozon"), dict) and str(row["ozon"].get("product_id") or "").strip()
    ]
    info = ozon.fetch_product_info(product_ids) if product_ids else []
    write_json(run_dir / "ozon_verify_by_product_id.json", info)
    by_product_id = {_normalize_int_text(item.get("id") or item.get("product_id")): item for item in info}
    results: list[dict[str, Any]] = []
    for row in plan:
        ozon_op = row.get("ozon")
        if not row.get("ready") or not isinstance(ozon_op, dict):
            continue
        product_id = str(ozon_op.get("product_id") or "").strip()
        item = by_product_id.get(product_id) if product_id else None
        if item:
            results.append(
                {
                    "operation": ozon_op,
                    "ok": str(item.get("offer_id") or "") == ozon_op["new_offer_id"],
                    "verify_method": "product_id",
                    "found_offer_id": str(item.get("offer_id") or ""),
                }
            )
            continue
        fallback = _fetch_ozon_one(ozon, ozon_op["old_offer_id"])
        write_json(run_dir / f"ozon_verify_by_old_offer_{ozon_op['old_offer_id']}.json", fallback)
        found_offer_ids = {
            str(item.get("offer_id") or "")
            for item in fallback.get("items") or []
            if str(item.get("offer_id") or "")
        }
        results.append(
            {
                "operation": ozon_op,
                "ok": ozon_op["new_offer_id"] in found_offer_ids,
                "verify_method": "old_offer_attributes",
                "found_offer_ids": sorted(found_offer_ids),
            }
        )
    return results


def _verify_wb_updates(
    *,
    wb: WbContentAdapter,
    plan: list[dict[str, Any]],
    run_dir: Path,
    wait_seconds: int,
    poll_interval: int,
) -> list[dict[str, Any]]:
    new_codes = {
        row["wb"]["new_vendor_code"]
        for row in plan
        if row.get("ready") and isinstance(row.get("wb"), dict)
    }
    found: dict[str, dict[str, Any]] = {}
    deadline = time.monotonic() + max(wait_seconds, 0)
    attempt = 0
    while new_codes:
        attempt += 1
        found = wb.find_cards_by_vendor_codes(new_codes)
        write_json(run_dir / f"wb_verify_attempt_{attempt:02d}.json", found)
        if set(found) == new_codes or time.monotonic() >= deadline:
            break
        time.sleep(max(poll_interval, 1))
    results: list[dict[str, Any]] = []
    for row in plan:
        wb_op = row.get("wb")
        if not row.get("ready") or not isinstance(wb_op, dict):
            continue
        card = found.get(wb_op["new_vendor_code"])
        expected_nm_id = str(wb_op.get("nm_id") or "").strip()
        found_nm_id = _normalize_int_text((card or {}).get("nmID"))
        results.append(
            {
                "operation": wb_op,
                "ok": bool(card and (not expected_nm_id or found_nm_id == expected_nm_id)),
                "expected_nm_id": expected_nm_id,
                "found_nm_id": found_nm_id,
            }
        )
    try:
        errors = wb.fetch_card_errors(limit=100)
        write_json(run_dir / "wb_card_errors_after_seller_sku_update.json", errors)
    except ApiError as exc:
        write_json(
            run_dir / "wb_card_errors_after_seller_sku_update_error.json",
            {"status": exc.status, "message": exc.message[:1000]},
        )
    return results


def run_seller_sku_update_verify(
    *,
    credentials: AppCredentials,
    data_dir: Path = Path("data"),
    plan_run_id: str | None = None,
    run_id: str | None = None,
    wait_seconds: int = 0,
    poll_interval: int = 5,
) -> dict[str, Any]:
    """Verify seller SKU changes from current Ozon/WB state without writes."""
    started_at = datetime.now()
    run_id = run_id or f"seller_sku_update_verify_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    plan_dir = _resolve_plan_dir(data_dir, plan_run_id)
    plan = _read_json(plan_dir / "seller_sku_update_plan.json")
    if not isinstance(plan, list) or not plan:
        raise RuntimeError(f"Seller SKU update plan has no rows: {plan_dir}")
    if any(not row.get("ready") for row in plan):
        raise RuntimeError("Seller SKU update plan contains blocked rows")

    ozon_expected = sum(1 for row in plan if isinstance(row.get("ozon"), dict))
    wb_expected = sum(1 for row in plan if isinstance(row.get("wb"), dict))
    if ozon_expected and not credentials.ozon_seller:
        raise RuntimeError("Ozon credentials are required for planned Ozon seller SKU updates")
    if wb_expected and not credentials.wb:
        raise RuntimeError("WB credentials are required for planned WB seller SKU updates")

    ozon_verify = (
        _verify_ozon_updates(
            ozon=OzonSellerAdapter(credentials.ozon_seller),
            plan=plan,
            run_dir=run_dir,
        )
        if ozon_expected and credentials.ozon_seller
        else []
    )
    wb_verify = (
        _verify_wb_updates(
            wb=WbContentAdapter(credentials.wb),
            plan=plan,
            run_dir=run_dir,
            wait_seconds=wait_seconds,
            poll_interval=poll_interval,
        )
        if wb_expected and credentials.wb
        else []
    )
    ozon_ok = len(ozon_verify) == ozon_expected and all(item.get("ok") for item in ozon_verify)
    wb_ok = len(wb_verify) == wb_expected and all(item.get("ok") for item in wb_verify)
    overall_status = "ok" if ozon_ok and wb_ok else "warning"
    summary = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "verify",
        "overall_status": overall_status,
        "approved_plan_run_id": plan_dir.name,
        "plan_checksum": canonical_checksum(plan),
        "ozon_expected_rows": ozon_expected,
        "ozon_verified_rows": sum(1 for item in ozon_verify if item.get("ok")),
        "wb_expected_rows": wb_expected,
        "wb_verified_rows": sum(1 for item in wb_verify if item.get("ok")),
        "verify": {"status": overall_status, "ozon": ozon_verify, "wb": wb_verify},
        "artifacts": {
            "run_dir": str(run_dir),
            "ozon_verify": str(run_dir / "ozon_verify_results.json"),
            "wb_verify": str(run_dir / "wb_verify_results.json"),
            "summary": str(run_dir / "summary.json"),
        },
    }
    write_json(run_dir / "ozon_verify_results.json", ozon_verify)
    write_json(run_dir / "wb_verify_results.json", wb_verify)
    write_json(run_dir / "summary.json", summary)
    manifest = write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=summary,
        task="seller-sku-update-verify",
        mode="verify",
        risk="low",
        marketplaces=[name for name, count in (("ozon", ozon_expected), ("wb", wb_expected)) if count],
        inputs={"plan_run_id": plan_dir.name, "wait_seconds": wait_seconds, "poll_interval": poll_interval},
        source_run_ids=[plan_dir.name],
        approved_id=plan_dir.name,
        lifecycle_status="verified" if overall_status == "ok" else "created",
        closed=overall_status == "ok",
    )
    summary["artifacts"]["run_manifest"] = manifest["manifest"]
    write_json(run_dir / "summary.json", summary)
    return summary


def _apply_local_row_update(row: dict[str, Any], updates: dict[str, dict[str, str]], run_id: str) -> bool:
    sku = str(row.get("internal_sku") or row.get("master_sku") or "").strip()
    update = updates.get(sku)
    if not update:
        return False
    if update.get("ozon_offer_id") and "ozon_offer_id" in row:
        row["ozon_offer_id"] = update["ozon_offer_id"]
    if update.get("wb_vendor_code") and "wb_vendor_code" in row:
        row["wb_vendor_code"] = update["wb_vendor_code"]
    if "notes" in row:
        marker = f"seller_sku_update_cli:{run_id}"
        notes = str(row.get("notes") or "")
        row["notes"] = notes if marker in notes else notes + (";" if notes else "") + marker
    return True


def _update_local_layers(*, data_dir: Path, plan: list[dict[str, Any]], run_id: str) -> dict[str, Any]:
    updates: dict[str, dict[str, str]] = {}
    for row in plan:
        internal_sku = row["internal_sku"]
        update = updates.setdefault(internal_sku, {"internal_sku": internal_sku})
        if isinstance(row.get("ozon"), dict):
            update["ozon_offer_id"] = row["ozon"]["new_offer_id"]
        if isinstance(row.get("wb"), dict):
            update["wb_vendor_code"] = row["wb"]["new_vendor_code"]
    changed: dict[str, int] = {}
    for rel_path in [
        "catalog/unified/products.csv",
        "catalog/content/content_master.csv",
        "catalog/processed/master_catalog.csv",
    ]:
        path = data_dir / rel_path
        if not path.exists():
            continue
        rows = _read_csv(path)
        if not rows:
            continue
        fieldnames = list(rows[0].keys())
        count = 0
        for row in rows:
            count += int(_apply_local_row_update(row, updates, run_id))
        _write_csv(path, rows, fieldnames)
        changed[rel_path] = count
    for rel_path in [
        "catalog/unified/products.json",
        "catalog/content/content_master.json",
        "catalog/processed/master_catalog.json",
    ]:
        path = data_dir / rel_path
        if not path.exists():
            continue
        payload = _read_json(path)
        if not isinstance(payload, list):
            continue
        count = 0
        for row in payload:
            if isinstance(row, dict):
                count += int(_apply_local_row_update(row, updates, run_id))
        write_json(path, payload)
        changed[rel_path] = count
    return changed


def run_seller_sku_update_apply(
    *,
    credentials: AppCredentials,
    data_dir: Path = Path("data"),
    plan_run_id: str | None = None,
    run_id: str | None = None,
    confirmed_by_user: bool = False,
    wait_seconds: int = 60,
    poll_interval: int = 5,
    update_local_layers: bool = True,
) -> dict[str, Any]:
    if not confirmed_by_user:
        raise RuntimeError("Apply requires explicit user confirmation")
    if not credentials.ozon_seller and not credentials.wb:
        raise RuntimeError("Ozon or WB credentials are required")

    started_at = datetime.now()
    run_id = run_id or f"seller_sku_update_apply_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    plan_dir = _resolve_plan_dir(data_dir, plan_run_id)
    approved_id = plan_dir.name
    assert_apply_not_repeated(data_dir=data_dir, approved_id=approved_id, current_run_id=run_id)
    plan = _read_json(plan_dir / "seller_sku_update_plan.json")
    if not isinstance(plan, list) or not plan:
        raise RuntimeError(f"Seller SKU update plan has no rows: {plan_dir}")
    if any(not row.get("ready") for row in plan):
        raise RuntimeError("Seller SKU update plan contains blocked rows")

    checksum = canonical_checksum(plan)
    ozon = OzonSellerAdapter(credentials.ozon_seller) if credentials.ozon_seller else None
    wb = WbContentAdapter(credentials.wb) if credentials.wb else None
    if any(isinstance(row.get("ozon"), dict) for row in plan) and not ozon:
        raise RuntimeError("Ozon credentials are required for planned Ozon seller SKU updates")
    if any(isinstance(row.get("wb"), dict) for row in plan) and not wb:
        raise RuntimeError("WB credentials are required for planned WB seller SKU updates")

    ozon_results: list[dict[str, Any]] = []
    wb_results: list[dict[str, Any]] = []
    ozon_verify: list[dict[str, Any]] = []
    wb_verify: list[dict[str, Any]] = []
    if ozon and any(isinstance(row.get("ozon"), dict) for row in plan):
        ozon_results = _apply_ozon_updates(ozon=ozon, plan=plan, run_dir=run_dir)
        if all(row.get("ok") for row in ozon_results):
            ozon_verify = _verify_ozon_updates(ozon=ozon, plan=plan, run_dir=run_dir)
    if wb and all(row.get("ok") for row in ozon_results) and any(isinstance(row.get("wb"), dict) for row in plan):
        wb_results = _apply_wb_updates(wb=wb, plan=plan, run_dir=run_dir)
        if all(row.get("ok") for row in wb_results):
            wb_verify = _verify_wb_updates(
                wb=wb,
                plan=plan,
                run_dir=run_dir,
                wait_seconds=wait_seconds,
                poll_interval=poll_interval,
            )

    ozon_ok = all(row.get("ok") for row in ozon_results + ozon_verify)
    wb_ok = all(row.get("ok") for row in wb_results + wb_verify)
    overall_ok = ozon_ok and wb_ok
    local_updates = _update_local_layers(data_dir=data_dir, plan=plan, run_id=run_id) if overall_ok and update_local_layers else {}
    summary = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "apply",
        "overall_status": "ok" if overall_ok else "error",
        "approved_plan_run_id": approved_id,
        "plan_checksum": checksum,
        "ozon_apply_rows": len(ozon_results),
        "ozon_verify_rows": len(ozon_verify),
        "wb_apply_rows": len(wb_results),
        "wb_verify_rows": len(wb_verify),
        "local_updates": local_updates,
        "artifacts": {
            "run_dir": str(run_dir),
            "summary": str(run_dir / "summary.json"),
        },
    }
    write_json(run_dir / "ozon_apply_results.json", ozon_results)
    write_json(run_dir / "ozon_verify_results.json", ozon_verify)
    write_json(run_dir / "wb_apply_results.json", wb_results)
    write_json(run_dir / "wb_verify_results.json", wb_verify)
    write_json(run_dir / "summary.json", summary)
    manifest = write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=summary,
        task="seller-sku-update-apply",
        mode="apply",
        risk="high",
        marketplaces=["ozon", "wb"],
        inputs={"plan_run_id": plan_run_id or "", "update_local_layers": update_local_layers},
        source_run_ids=[approved_id],
        approved_id=approved_id,
        lifecycle_status="verified" if overall_ok else "failed",
        closed=overall_ok,
    )
    summary["artifacts"]["run_manifest"] = manifest["manifest"]
    write_json(run_dir / "summary.json", summary)
    if overall_ok:
        mark_approved_applied(
            data_dir=data_dir,
            approved_id=approved_id,
            apply_run_id=run_id,
            task="seller-sku-update-apply",
            status="verified",
            run_manifest_path=manifest["manifest"],
            checksum=checksum,
        )
    return summary
