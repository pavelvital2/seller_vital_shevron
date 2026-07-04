from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import time
from typing import Any

from seller_agent.config import AppCredentials
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.http import ApiError
from seller_agent.marketplaces.ozon.adapter import OzonSellerAdapter
from seller_agent.reports.writer import ensure_dir, write_json
from seller_agent.safety.approvals import (
    apply_marker_for,
    assert_apply_not_repeated,
    canonical_checksum,
    mark_approved_applied,
)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    write_json(path, payload)


def _normalize_text(value: Any) -> str:
    text = str(value or "").strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text


def _resolve_plan_dir(data_dir: Path, plan_run_id: str | None) -> Path:
    runs_dir = data_dir / "runs"
    candidates = sorted(runs_dir.glob(f"*/{plan_run_id}")) if plan_run_id else sorted(runs_dir.glob("*/ozon_card_create_plan_*"))
    candidates = [path for path in candidates if path.is_dir() and (path / "ozon_card_create_plan.json").exists()]
    if not candidates:
        raise FileNotFoundError(f"Ozon card create plan not found: {plan_run_id or 'latest'}")
    return candidates[-1]


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
        write_json(run_dir / f"ozon_import_info_{attempt:02d}.json", result)
        text = json.dumps(result, ensure_ascii=False).lower()
        if "imported" in text or "failed" in text or time.monotonic() >= deadline:
            return result
        time.sleep(max(poll_interval, 1))


def _verify_created_cards(
    *,
    ozon: OzonSellerAdapter,
    offer_ids: list[str],
    run_dir: Path,
    wait_seconds: int,
    poll_interval: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + max(wait_seconds, 0)
    attempt = 0
    items: list[dict[str, Any]] = []
    found_by_offer: dict[str, dict[str, Any]] = {}
    expected = set(offer_ids)
    while True:
        attempt += 1
        try:
            items = ozon.fetch_product_attributes(offer_ids)
        except ApiError as exc:
            if exc.status == 404 and "item not found" in exc.message.lower():
                items = []
            else:
                raise
        found_by_offer = {
            _normalize_text(item.get("offer_id")): item
            for item in items
            if _normalize_text(item.get("offer_id")) in expected
        }
        write_json(
            run_dir / f"verify_created_cards_attempt_{attempt:02d}.json",
            {
                "attempt": attempt,
                "found_offer_ids": sorted(found_by_offer),
                "missing_offer_ids": sorted(expected - set(found_by_offer)),
                "items": items,
            },
        )
        if set(found_by_offer) == expected or time.monotonic() >= deadline:
            break
        time.sleep(max(poll_interval, 1))

    results = []
    for offer_id in offer_ids:
        item = found_by_offer.get(offer_id)
        if not item:
            results.append({"offer_id": offer_id, "status": "missing", "product_id": "", "sku": ""})
            continue
        results.append(
            {
                "offer_id": offer_id,
                "status": "ok",
                "product_id": _normalize_text(item.get("id") or item.get("product_id")),
                "sku": _normalize_text(item.get("sku")),
                "name": _normalize_text(item.get("name")),
                "moderate_status": _normalize_text(item.get("moderate_status")),
                "validation_status": _normalize_text(item.get("validation_status")),
            }
        )
    summary = {
        "status": "ok" if results and all(item["status"] == "ok" for item in results) else "warning",
        "rows": len(results),
        "found_rows": sum(1 for item in results if item["status"] == "ok"),
        "missing_rows": sum(1 for item in results if item["status"] != "ok"),
        "results": results,
    }
    write_json(run_dir / "ozon_card_create_verify_summary.json", summary)
    return summary


def _price_value(item: dict[str, Any], key: str) -> str:
    price = item.get("price") if isinstance(item.get("price"), dict) else item
    value = price.get(key) if isinstance(price, dict) else item.get(key)
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return _normalize_text(value)


def _verify_prices(
    *,
    ozon: OzonSellerAdapter,
    price_payloads: list[dict[str, Any]],
    run_dir: Path,
) -> dict[str, Any]:
    offer_ids = [_normalize_text(item.get("offer_id")) for item in price_payloads if _normalize_text(item.get("offer_id"))]
    rows = ozon.fetch_product_info_prices_by_offer_ids(offer_ids)
    write_json(run_dir / "ozon_product_info_prices_after_create.json", rows)
    by_offer = {_normalize_text(row.get("offer_id")): row for row in rows if _normalize_text(row.get("offer_id"))}
    results: list[dict[str, Any]] = []
    for payload in price_payloads:
        offer_id = _normalize_text(payload.get("offer_id"))
        row = by_offer.get(offer_id)
        if not row:
            results.append({"offer_id": offer_id, "status": "missing"})
            continue
        checks = {
            "price": _price_value(row, "price") == _normalize_text(payload.get("price")),
            "old_price": _price_value(row, "old_price") == _normalize_text(payload.get("old_price")),
            "min_price": _price_value(row, "min_price") == _normalize_text(payload.get("min_price")),
        }
        results.append(
            {
                "offer_id": offer_id,
                "status": "ok" if all(checks.values()) else "warning",
                "checks": checks,
                "actual": {
                    "price": _price_value(row, "price"),
                    "old_price": _price_value(row, "old_price"),
                    "min_price": _price_value(row, "min_price"),
                    "currency_code": _price_value(row, "currency_code"),
                },
                "target": {
                    "price": _normalize_text(payload.get("price")),
                    "old_price": _normalize_text(payload.get("old_price")),
                    "min_price": _normalize_text(payload.get("min_price")),
                },
            }
        )
    summary = {
        "status": "ok" if results and all(item["status"] == "ok" for item in results) else "warning",
        "rows": len(results),
        "results": results,
    }
    write_json(run_dir / "ozon_price_verify_summary.json", summary)
    return summary


def _update_passports(
    *,
    data_dir: Path,
    verify: dict[str, Any],
    run_id: str,
) -> list[dict[str, str]]:
    updates: list[dict[str, str]] = []
    approved_dir = data_dir / "catalog" / "master_passport" / "approved"
    for item in verify.get("results") or []:
        if item.get("status") != "ok":
            continue
        offer_id = _normalize_text(item.get("offer_id"))
        path = approved_dir / f"{offer_id}.json"
        if not path.exists():
            continue
        passport = _read_json(path)
        identity = passport.setdefault("identity", {})
        identity["ozon_offer_id"] = offer_id
        if _normalize_text(item.get("product_id")):
            identity["ozon_product_id"] = _normalize_text(item.get("product_id"))
        if _normalize_text(item.get("sku")):
            identity["ozon_sku"] = _normalize_text(item.get("sku"))
        identity["marketplace_presence"] = "ozon_wb" if _normalize_text(identity.get("wb_vendor_code")) else "ozon_only"
        ozon = passport.setdefault("ozon", {})
        ozon["export_status"] = "applied_verified"
        approval = passport.setdefault("approval", {})
        marketplace_apply = approval.setdefault("marketplace_apply", {})
        marketplace_apply["ozon_card_create_run_id"] = run_id
        marketplace_apply["status"] = "applied_verified"
        safety = passport.setdefault("safety", {})
        safety["marketplace_write_status"] = "applied_verified"
        path.write_text(json.dumps(passport, ensure_ascii=False, indent=2), encoding="utf-8")
        updates.append({"internal_sku": offer_id, "path": str(path)})
    return updates


def run_ozon_card_create_apply(
    *,
    credentials: AppCredentials,
    data_dir: Path = Path("data"),
    plan_run_id: str | None = None,
    run_id: str | None = None,
    confirmed_by_user: bool = False,
    allow_manual_review: bool = False,
    wait_seconds: int = 300,
    poll_interval: int = 10,
) -> dict[str, Any]:
    if not confirmed_by_user:
        raise RuntimeError("Apply requires explicit user confirmation")
    if not credentials.ozon_seller:
        raise RuntimeError("Ozon Seller credentials are required")

    started_at = datetime.now()
    run_id = run_id or f"ozon_card_create_apply_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    plan_dir = _resolve_plan_dir(data_dir, plan_run_id)
    approved_id = plan_dir.name
    assert_apply_not_repeated(data_dir=data_dir, approved_id=approved_id, current_run_id=run_id)
    plan_items = _read_json(plan_dir / "ozon_card_create_plan.json")
    if not isinstance(plan_items, list) or not plan_items:
        raise RuntimeError(f"Ozon card create plan has no rows: {plan_dir}")
    blocked = [item for item in plan_items if not item.get("ready")]
    manual_review = [item for item in plan_items if item.get("needs_manual_review")]
    if blocked:
        raise RuntimeError("Ozon card create plan contains blocked rows")
    if manual_review and not allow_manual_review:
        raise RuntimeError("Apply requires --allow-manual-review for current Ozon plan")

    payload_items = [item["payload"] for item in plan_items if item.get("payload")]
    price_payload_items = [item["price_payload"] for item in plan_items if item.get("price_payload")]
    offer_ids = [_normalize_text(payload.get("offer_id")) for payload in payload_items if _normalize_text(payload.get("offer_id"))]
    if not payload_items or len(offer_ids) != len(payload_items):
        raise RuntimeError("Ozon card create plan has no valid payload items")

    checksum = canonical_checksum(plan_items)
    ozon = OzonSellerAdapter(credentials.ozon_seller)
    _write_json(run_dir / "source_plan_items.json", plan_items)
    _write_json(run_dir / "ozon_product_import_request.json", {"items": payload_items})
    response = ozon.import_products(payload_items)
    _write_json(run_dir / "ozon_product_import_response.json", response)
    task_id = ((response.get("result") or {}).get("task_id")) if isinstance(response, dict) else None
    import_info: dict[str, Any] = {}
    if task_id:
        import_info = _wait_ozon_import(ozon, int(task_id), run_dir, wait_seconds, poll_interval)
    verify = _verify_created_cards(
        ozon=ozon,
        offer_ids=offer_ids,
        run_dir=run_dir,
        wait_seconds=wait_seconds,
        poll_interval=poll_interval,
    )
    passport_updates = _update_passports(data_dir=data_dir, verify=verify, run_id=run_id)
    found_offer_ids = {
        _normalize_text(item.get("offer_id"))
        for item in verify.get("results") or []
        if item.get("status") == "ok"
    }
    price_payloads_to_apply = [
        item for item in price_payload_items if _normalize_text(item.get("offer_id")) in found_offer_ids
    ]
    price_response: dict[str, Any] = {}
    price_verify: dict[str, Any] = {"status": "skipped", "rows": 0, "results": []}
    if price_payloads_to_apply:
        _write_json(run_dir / "ozon_product_import_prices_request.json", {"prices": price_payloads_to_apply})
        price_response = ozon.import_product_prices(price_payloads_to_apply)
        _write_json(run_dir / "ozon_product_import_prices_response.json", price_response)
        time.sleep(max(poll_interval, 1))
        price_verify = _verify_prices(ozon=ozon, price_payloads=price_payloads_to_apply, run_dir=run_dir)
    else:
        _write_json(run_dir / "ozon_product_import_prices_request.json", {"prices": []})

    import_ok = bool(task_id)
    verify_ok = verify.get("status") == "ok"
    price_ok = price_verify.get("status") in {"ok", "skipped"}
    summary = {
        "submitted_items": len(payload_items),
        "price_items": len(price_payloads_to_apply),
        "task_id": task_id or "",
        "import_ok": import_ok,
        "verify_status": verify.get("status"),
        "price_verify_status": price_verify.get("status"),
        "found_rows": verify.get("found_rows", 0),
        "missing_rows": verify.get("missing_rows", 0),
        "passport_updates": len(passport_updates),
    }
    artifacts = {
        "run_dir": str(run_dir),
        "source_plan_items": str(run_dir / "source_plan_items.json"),
        "request": str(run_dir / "ozon_product_import_request.json"),
        "response": str(run_dir / "ozon_product_import_response.json"),
        "price_request": str(run_dir / "ozon_product_import_prices_request.json"),
        "price_response": str(run_dir / "ozon_product_import_prices_response.json"),
        "price_verify": str(run_dir / "ozon_price_verify_summary.json"),
        "verify": str(run_dir / "ozon_card_create_verify_summary.json"),
        "report": str(run_dir / "ozon_card_create_apply_report.md"),
        "summary": str(run_dir / "summary.json"),
        "apply_marker": str(apply_marker_for(data_dir=data_dir, approved_id=approved_id)),
    }
    result = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "apply",
        "overall_status": "ok" if import_ok and verify_ok and price_ok else "warning",
        "approved_plan_run_id": approved_id,
        "approved_id": approved_id,
        "plan_checksum": checksum,
        "summary": summary,
        "verify": verify,
        "price_response": price_response,
        "price_verify": price_verify,
        "import_info": import_info,
        "passport_updates": passport_updates,
        "artifacts": artifacts,
    }
    write_json(run_dir / "summary.json", result)
    _write_report(run_dir / "ozon_card_create_apply_report.md", result, offer_ids, passport_updates)
    manifest = write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=result,
        task="ozon-card-create-apply",
        mode="apply",
        risk="high",
        marketplaces=["ozon"],
        inputs={
            "plan_run_id": plan_run_id or "",
            "confirmed_by_user": confirmed_by_user,
            "allow_manual_review": allow_manual_review,
            "wait_seconds": wait_seconds,
            "poll_interval": poll_interval,
        },
        source_run_ids=[approved_id],
        approved_id=approved_id,
        lifecycle_status="verified" if result["overall_status"] == "ok" else "applied",
        closed=result["overall_status"] == "ok",
    )
    result["artifacts"]["run_manifest"] = manifest["manifest"]
    write_json(run_dir / "summary.json", result)
    mark_approved_applied(
        data_dir=data_dir,
        approved_id=approved_id,
        apply_run_id=run_id,
        task="ozon-card-create-apply",
        status=result["overall_status"],
        run_manifest_path=manifest["manifest"],
        checksum=checksum,
    )
    return result


def _write_report(
    path: Path,
    result: dict[str, Any],
    offer_ids: list[str],
    passport_updates: list[dict[str, str]],
) -> None:
    lines = [
        "# Ozon Card Create Apply Report",
        "",
        f"Run ID: `{result.get('run_id')}`",
        f"Source plan: `{result.get('approved_plan_run_id')}`",
        "",
        "## Summary",
        "",
    ]
    for key, value in sorted((result.get("summary") or {}).items()):
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "## Offer IDs", ""])
    for offer_id in offer_ids:
        lines.append(f"- `{offer_id}`")
    lines.extend(["", "## Passport Updates", ""])
    if passport_updates:
        for item in passport_updates:
            lines.append(f"- `{item['internal_sku']}`: `{item['path']}`")
    else:
        lines.append("- none")
    lines.extend(["", "## Artifacts", ""])
    for key, value in sorted((result.get("artifacts") or {}).items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
