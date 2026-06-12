from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import time
from typing import Any

from takterra_agent.config import AppCredentials
from takterra_agent.http import ApiError
from takterra_agent.marketplaces.wb.adapter import WbContentAdapter
from takterra_agent.reports.writer import ensure_dir, write_json
from takterra_agent.tasks.wb_card_create_plan import WB_BARCODE_PLACEHOLDER


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_plan_dir(data_dir: Path, plan_run_id: str | None) -> Path:
    runs_dir = data_dir / "runs"
    if plan_run_id:
        candidates = sorted(runs_dir.glob(f"*/{plan_run_id}"))
    else:
        candidates = sorted(runs_dir.glob("*/wb_card_create_plan_*"))

    candidates = [
        path
        for path in candidates
        if path.is_dir() and (path / "wb_card_create_plan.json").exists()
    ]
    if not candidates:
        label = plan_run_id or "latest wb_card_create_plan_*"
        raise FileNotFoundError(f"WB card create plan not found: {label}")
    return candidates[-1]


def _vendor_code(item: dict[str, Any]) -> str:
    return str(item.get("master_sku") or item.get("draft_variant", {}).get("vendorCode") or "")


def _replace_barcode(variant: dict[str, Any], barcode: str) -> dict[str, Any]:
    result = deepcopy(variant)
    for size in result.get("sizes") or []:
        skus = size.get("skus") or []
        size["skus"] = [
            barcode if value == WB_BARCODE_PLACEHOLDER else value
            for value in skus
        ] or [barcode]

    has_barcode_characteristic = False
    for characteristic in result.get("characteristics") or []:
        if int(characteristic.get("id") or 0) != 14177453:
            continue
        characteristic["value"] = [barcode]
        has_barcode_characteristic = True

    if not has_barcode_characteristic:
        result.setdefault("characteristics", []).append({"id": 14177453, "value": [barcode]})

    return result


def _build_payloads(
    plan_items: list[dict[str, Any]],
    barcode_by_vendor: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    create_by_subject: OrderedDict[int, list[dict[str, Any]]] = OrderedDict()
    add_by_imt: OrderedDict[int, list[dict[str, Any]]] = OrderedDict()
    applied_variants: dict[str, dict[str, Any]] = {}

    for item in plan_items:
        vendor_code = _vendor_code(item)
        variant = _replace_barcode(item["draft_variant"], barcode_by_vendor[vendor_code])
        applied_variants[vendor_code] = variant

        target = item["target"]
        if target["action"] == "upload_add":
            imt_id = int(target["imt_id"])
            add_by_imt.setdefault(imt_id, []).append(variant)
        else:
            subject_id = int(target["subject_id"])
            create_by_subject.setdefault(subject_id, []).append(variant)

    upload_payload = [
        {"subjectID": subject_id, "variants": variants}
        for subject_id, variants in create_by_subject.items()
    ]
    upload_add_payloads = [
        {"imtID": imt_id, "cardsToAdd": variants}
        for imt_id, variants in add_by_imt.items()
    ]
    return upload_payload, upload_add_payloads, applied_variants


def _api_response_ok(response: dict[str, Any]) -> bool:
    return not bool(response.get("error"))


def _call_wb(
    *,
    run_dir: Path,
    name: str,
    request_payload: Any,
    call: Any,
) -> dict[str, Any]:
    request_path = run_dir / f"{name}_request.json"
    response_path = run_dir / f"{name}_response.json"
    write_json(request_path, request_payload)
    try:
        response = call(request_payload)
        write_json(response_path, response)
        return {
            "name": name,
            "ok": _api_response_ok(response),
            "request": str(request_path),
            "response": str(response_path),
            "response_data": response,
        }
    except ApiError as exc:
        error = {
            "method": exc.method,
            "url": exc.url,
            "status": exc.status,
            "message": exc.message,
        }
        write_json(response_path, {"error": error})
        return {
            "name": name,
            "ok": False,
            "request": str(request_path),
            "response": str(response_path),
            "error": error,
        }


def _cards_by_vendor(cards: list[dict[str, Any]], vendor_codes: set[str]) -> dict[str, dict[str, Any]]:
    return {
        str(card.get("vendorCode")): card
        for card in cards
        if str(card.get("vendorCode")) in vendor_codes
    }


def _poll_cards(
    *,
    wb: WbContentAdapter,
    run_dir: Path,
    vendor_codes: set[str],
    wait_seconds: int,
    poll_interval: int,
) -> dict[str, dict[str, Any]]:
    deadline = time.monotonic() + max(wait_seconds, 0)
    attempt = 0
    found: dict[str, dict[str, Any]] = {}

    while True:
        attempt += 1
        found = wb.find_cards_by_vendor_codes(vendor_codes)
        write_json(
            run_dir / f"verify_cards_attempt_{attempt:02d}.json",
            {
                "attempt": attempt,
                "found_vendor_codes": sorted(found),
                "missing_vendor_codes": sorted(vendor_codes - set(found)),
                "found_cards": found,
            },
        )
        if set(found) == vendor_codes:
            return found
        try:
            error_response = wb.fetch_card_errors(limit=100)
            relevant_errors = _extract_relevant_error_batches(error_response, vendor_codes - set(found))
        except ApiError:
            relevant_errors = []
        if relevant_errors:
            write_json(
                run_dir / f"verify_cards_attempt_{attempt:02d}_blocking_errors.json",
                relevant_errors,
            )
            return found
        if time.monotonic() >= deadline:
            return found
        time.sleep(max(poll_interval, 1))


def _extract_relevant_error_batches(
    error_response: dict[str, Any],
    vendor_codes: set[str],
) -> list[dict[str, Any]]:
    candidates = error_response.get("data")
    if isinstance(candidates, dict):
        candidates = candidates.get("items")
    if not isinstance(candidates, list):
        candidates = error_response.get("cards")
    if not isinstance(candidates, list):
        return []

    relevant: list[dict[str, Any]] = []
    for item in candidates:
        serialized = json.dumps(item, ensure_ascii=False)
        if any(vendor_code in serialized for vendor_code in vendor_codes):
            relevant.append(item)
    return relevant


def _media_by_vendor(plan_items: list[dict[str, Any]]) -> dict[str, list[str]]:
    return {
        _vendor_code(item): [
            str(url).strip()
            for url in item.get("images_from_ozon") or []
            if str(url).strip()
        ]
        for item in plan_items
    }


def run_wb_card_create_apply(
    *,
    credentials: AppCredentials,
    data_dir: Path = Path("data"),
    plan_run_id: str | None = None,
    run_id: str | None = None,
    confirmed_by_user: bool = False,
    allow_manual_review: bool = False,
    wait_seconds: int = 600,
    poll_interval: int = 30,
) -> dict[str, Any]:
    if not confirmed_by_user:
        raise RuntimeError("Apply requires explicit user confirmation")
    if not allow_manual_review:
        raise RuntimeError("Apply requires --allow-manual-review for current WB plan")
    if not credentials.wb:
        raise RuntimeError("Wildberries credentials are required")

    started_at = datetime.now()
    run_id = run_id or f"wb_card_create_apply_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    plan_dir = _resolve_plan_dir(data_dir, plan_run_id)
    plan_items = _read_json(plan_dir / "wb_card_create_plan.json")
    if not isinstance(plan_items, list) or not plan_items:
        raise RuntimeError(f"Plan has no items: {plan_dir}")

    vendor_codes = [_vendor_code(item) for item in plan_items]
    if any(not vendor_code for vendor_code in vendor_codes):
        raise RuntimeError("Plan contains item without vendor code")

    wb = WbContentAdapter(credentials.wb)
    vendor_code_set = set(vendor_codes)
    existing_by_vendor = wb.find_cards_by_vendor_codes(vendor_code_set)
    trash_by_vendor = wb.find_trash_cards_by_vendor_codes(vendor_code_set)
    skipped_existing = sorted(existing_by_vendor)
    items_to_apply = [
        item for item in plan_items if _vendor_code(item) not in existing_by_vendor
    ]
    codes_to_apply = [_vendor_code(item) for item in items_to_apply]

    write_json(run_dir / "source_plan_items.json", plan_items)
    write_json(run_dir / "existing_cards_before_apply.json", existing_by_vendor)
    write_json(run_dir / "trash_cards_before_apply.json", trash_by_vendor)

    operations: list[dict[str, Any]] = []
    generated_barcodes: list[str] = []
    barcode_by_vendor: dict[str, str] = {}
    upload_payload: list[dict[str, Any]] = []
    upload_add_payloads: list[dict[str, Any]] = []
    applied_variants: dict[str, dict[str, Any]] = {}

    if items_to_apply:
        generated_barcodes = wb.generate_barcodes(len(items_to_apply))
        write_json(run_dir / "generated_barcodes.json", generated_barcodes)
        if len(generated_barcodes) < len(items_to_apply):
            raise RuntimeError(
                f"WB returned {len(generated_barcodes)} barcodes for {len(items_to_apply)} cards"
            )

        barcode_by_vendor = dict(zip(codes_to_apply, generated_barcodes, strict=True))
        write_json(run_dir / "barcode_by_vendor.json", barcode_by_vendor)
        upload_payload, upload_add_payloads, applied_variants = _build_payloads(
            items_to_apply,
            barcode_by_vendor,
        )
        write_json(run_dir / "wb_cards_upload_payload_applied.json", upload_payload)
        write_json(run_dir / "wb_cards_upload_add_payloads_applied.json", upload_add_payloads)

        for index, payload in enumerate(upload_add_payloads, start=1):
            operations.append(
                _call_wb(
                    run_dir=run_dir,
                    name=f"upload_add_{index:02d}_imt_{payload['imtID']}",
                    request_payload=payload,
                    call=wb.upload_add_cards,
                )
            )
            time.sleep(6)

        if upload_payload:
            operations.append(
                _call_wb(
                    run_dir=run_dir,
                    name="upload_cards",
                    request_payload=upload_payload,
                    call=wb.upload_cards,
                )
            )
            time.sleep(6)
    else:
        write_json(run_dir / "generated_barcodes.json", [])
        write_json(run_dir / "barcode_by_vendor.json", {})
        write_json(run_dir / "wb_cards_upload_payload_applied.json", [])
        write_json(run_dir / "wb_cards_upload_add_payloads_applied.json", [])

    submitted_ok = bool(items_to_apply) and all(operation["ok"] for operation in operations)
    found_cards: dict[str, dict[str, Any]] = {}
    media_operations: list[dict[str, Any]] = []

    if items_to_apply:
        found_cards = _poll_cards(
            wb=wb,
            run_dir=run_dir,
            vendor_codes=vendor_code_set,
            wait_seconds=wait_seconds,
            poll_interval=poll_interval,
        )
    else:
        found_cards = dict(existing_by_vendor)
    write_json(run_dir / "found_cards_after_apply.json", found_cards)

    images_by_vendor = _media_by_vendor(plan_items)
    for vendor_code in vendor_codes:
        card = found_cards.get(vendor_code)
        urls = images_by_vendor.get(vendor_code) or []
        if not card or not urls:
            continue
        if card.get("photos"):
            continue
        nm_id = int(card.get("nmID") or card.get("nmId"))
        media_operations.append(
            _call_wb(
                run_dir=run_dir,
                name=f"media_save_{vendor_code}",
                request_payload={"nmId": nm_id, "data": urls},
                call=lambda payload: wb.save_media_links(
                    nm_id=int(payload["nmId"]),
                    urls=list(payload["data"]),
                ),
            )
        )
        time.sleep(1)

    try:
        error_response = wb.fetch_card_errors(limit=100)
    except ApiError as exc:
        error_response = {
            "error": {
                "method": exc.method,
                "url": exc.url,
                "status": exc.status,
                "message": exc.message,
            }
        }
    write_json(run_dir / "wb_card_errors_after_apply.json", error_response)
    relevant_errors = _extract_relevant_error_batches(error_response, set(vendor_codes))
    write_json(run_dir / "wb_card_errors_relevant.json", relevant_errors)

    media_failures = {
        operation["name"].removeprefix("media_save_")
        for operation in media_operations
        if not operation["ok"]
    }
    pending_media = []
    for vendor_code in vendor_codes:
        urls = images_by_vendor.get(vendor_code) or []
        card = found_cards.get(vendor_code)
        if not card:
            reason = "nmID not visible yet"
        elif not urls:
            reason = "no images"
        elif vendor_code in media_failures:
            reason = "media upload failed"
        else:
            continue
        pending_media.append({"vendorCode": vendor_code, "reason": reason, "images": urls})
    write_json(run_dir / "pending_media_uploads.json", pending_media)
    write_json(run_dir / "operations.json", operations)
    write_json(run_dir / "media_operations.json", media_operations)

    summary = {
        "plan_run_id": plan_dir.name,
        "plan_items": len(plan_items),
        "skipped_existing": len(skipped_existing),
        "trash_found_before_apply": len(trash_by_vendor),
        "submitted_items": len(items_to_apply),
        "upload_add_requests": len(upload_add_payloads),
        "upload_requests": 1 if upload_payload else 0,
        "operation_errors": sum(1 for operation in operations if not operation["ok"]),
        "submitted_ok": submitted_ok,
        "found_after_apply": len(found_cards),
        "missing_after_apply": len(vendor_code_set - set(found_cards)),
        "media_upload_attempts": len(media_operations),
        "media_upload_errors": sum(1 for operation in media_operations if not operation["ok"]),
        "pending_media_uploads": len(pending_media),
        "relevant_error_batches": len(relevant_errors),
    }

    artifacts = {
        "run_dir": str(run_dir),
        "source_plan_items": str(run_dir / "source_plan_items.json"),
        "generated_barcodes": str(run_dir / "generated_barcodes.json"),
        "barcode_by_vendor": str(run_dir / "barcode_by_vendor.json"),
        "trash_cards_before_apply": str(run_dir / "trash_cards_before_apply.json"),
        "upload_payload": str(run_dir / "wb_cards_upload_payload_applied.json"),
        "upload_add_payloads": str(run_dir / "wb_cards_upload_add_payloads_applied.json"),
        "operations": str(run_dir / "operations.json"),
        "found_cards_after_apply": str(run_dir / "found_cards_after_apply.json"),
        "media_operations": str(run_dir / "media_operations.json"),
        "pending_media_uploads": str(run_dir / "pending_media_uploads.json"),
        "wb_card_errors_after_apply": str(run_dir / "wb_card_errors_after_apply.json"),
        "wb_card_errors_relevant": str(run_dir / "wb_card_errors_relevant.json"),
        "report": str(run_dir / "wb_card_create_apply_report.md"),
        "summary": str(run_dir / "summary.json"),
    }

    result = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "summary": summary,
        "artifacts": artifacts,
    }
    write_json(run_dir / "summary.json", result)
    _write_report(
        run_dir / "wb_card_create_apply_report.md",
        run_id=run_id,
        started_at=started_at,
        plan_dir=plan_dir,
        vendor_codes=vendor_codes,
        skipped_existing=skipped_existing,
        barcode_by_vendor=barcode_by_vendor,
        operations=operations,
        found_cards=found_cards,
        media_operations=media_operations,
        pending_media=pending_media,
        relevant_errors=relevant_errors,
        summary=summary,
        artifacts=artifacts,
    )
    return result


def _write_report(
    path: Path,
    *,
    run_id: str,
    started_at: datetime,
    plan_dir: Path,
    vendor_codes: list[str],
    skipped_existing: list[str],
    barcode_by_vendor: dict[str, str],
    operations: list[dict[str, Any]],
    found_cards: dict[str, dict[str, Any]],
    media_operations: list[dict[str, Any]],
    pending_media: list[dict[str, Any]],
    relevant_errors: list[dict[str, Any]],
    summary: dict[str, Any],
    artifacts: dict[str, str],
) -> None:
    lines = [
        "# WB Card Create Apply Report",
        "",
        f"Run ID: `{run_id}`",
        f"Started at: `{started_at.isoformat(timespec='seconds')}`",
        f"Source plan: `{plan_dir}`",
        "",
        "## Summary",
        "",
    ]
    for key in sorted(summary):
        lines.append(f"- `{key}`: {summary[key]}")

    lines.extend(["", "## Vendor Codes", ""])
    for vendor_code in vendor_codes:
        barcode = barcode_by_vendor.get(vendor_code, "")
        card = found_cards.get(vendor_code) or {}
        status = "skipped_existing" if vendor_code in skipped_existing else "submitted"
        nm_id = card.get("nmID") or card.get("nmId") or ""
        lines.append(f"- `{vendor_code}`: {status}, barcode `{barcode}`, nmID `{nm_id}`")

    lines.extend(["", "## WB Write Operations", ""])
    if operations:
        for operation in operations:
            lines.append(f"- `{operation['name']}`: ok `{operation['ok']}`")
    else:
        lines.append("- none")

    lines.extend(["", "## Media", ""])
    if media_operations:
        for operation in media_operations:
            lines.append(f"- `{operation['name']}`: ok `{operation['ok']}`")
    else:
        lines.append("- no media uploads attempted")
    if pending_media:
        lines.append("")
        lines.append("Pending media:")
        for item in pending_media:
            lines.append(f"- `{item['vendorCode']}`: {item['reason']}, images {len(item['images'])}")

    lines.extend(["", "## WB Error Batches", ""])
    if relevant_errors:
        lines.append(f"- relevant batches: {len(relevant_errors)}")
    else:
        lines.append("- no relevant error batches found in `/content/v2/cards/error/list` response")

    lines.extend(["", "## Notes", ""])
    lines.append("- WB creates product cards asynchronously; synchronization can take up to 30 minutes.")
    lines.append("- Prices, stocks, discounts and ads were not changed by this run.")
    lines.append("- Media was uploaded only for cards that became visible and had `nmID` in API verification.")

    lines.extend(["", "## Artifacts", ""])
    for key in sorted(artifacts):
        lines.append(f"- `{key}`: `{artifacts[key]}`")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
