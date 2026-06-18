from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from takterra_agent.config import AppCredentials
from takterra_agent.core.run_manifest import write_summary_run_manifest
from takterra_agent.marketplaces.ozon.adapter import OzonSellerAdapter
from takterra_agent.reports.writer import ensure_dir, write_json
from takterra_agent.safety.approvals import (
    apply_marker_for,
    assert_apply_not_repeated,
    canonical_checksum,
    mark_approved_applied,
)
from takterra_agent.tasks.ozon_elastic_plan import ACTIVE, _fetch_action_group, run_ozon_elastic_plan
from takterra_agent.tasks.status_preflight import run_status_preflight
from takterra_agent.tasks.wb_actions_discount_plan import _price_value, run_wb_actions_discount_plan


WB_UPLOAD_URL = "https://discounts-prices-api.wildberries.ru/api/v2/upload/task"
WB_HISTORY_TASK_URL = "https://discounts-prices-api.wildberries.ru/api/v2/history/tasks"
WB_HISTORY_GOODS_URL = "https://discounts-prices-api.wildberries.ru/api/v2/history/goods/task"
WB_BUFFER_TASK_URL = "https://discounts-prices-api.wildberries.ru/api/v2/buffer/tasks"
WB_BUFFER_GOODS_URL = "https://discounts-prices-api.wildberries.ru/api/v2/buffer/goods/task"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path, *, delimiter: str = ",") -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter=delimiter))


def _write_csv(rows: list[dict[str, Any]], path: Path, *, delimiter: str = ";") -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()), delimiter=delimiter)
        writer.writeheader()
        writer.writerows(rows)


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


def _master_skus(path: Path) -> set[str]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(row.get("master_sku") or row.get("ozon_offer_id") or row.get("wb_vendor_code") or "")
        for row in rows
        if isinstance(row, dict)
    }


def build_wb_guarded_payload(
    rows: list[dict[str, str]],
    *,
    master_skus: set[str],
) -> tuple[dict[str, Any], list[dict[str, str]], list[dict[str, str]]]:
    changed_rows = [row for row in rows if int(row["Дельта, п.п."]) != 0]
    guarded_rows = [row for row in changed_rows if row["Артикул поставщика"] in master_skus]
    excluded_rows = [row for row in changed_rows if row["Артикул поставщика"] not in master_skus]
    payload = {
        "data": [
            {
                "nmID": int(row["Артикул WB"]),
                "price": _price_value(row["Базовая цена"]),
                "discount": int(row["Финальная скидка"]),
            }
            for row in guarded_rows
        ]
    }
    return payload, guarded_rows, excluded_rows


def _wb_request_json(method: str, url: str, token: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, method=method, data=data)
    request.add_header("Authorization", token)
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            text = response.read().decode("utf-8")
            payload = json.loads(text) if text else None
            return {"httpStatus": response.status, "data": payload}
    except urllib.error.HTTPError as error:
        text = error.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(text) if text else None
        except json.JSONDecodeError:
            payload = text
        return {"httpStatus": error.code, "data": payload}


def _wb_query_status(upload_id: int, token: str) -> dict[str, Any]:
    query = urllib.parse.urlencode({"uploadID": upload_id})
    history = _wb_request_json("GET", f"{WB_HISTORY_TASK_URL}?{query}", token)
    buffer = _wb_request_json("GET", f"{WB_BUFFER_TASK_URL}?{query}", token)
    return {"history": history, "buffer": buffer}


def _wb_query_details(upload_id: int, token: str) -> dict[str, Any]:
    query = urllib.parse.urlencode({"uploadID": upload_id, "limit": 1000, "offset": 0})
    history = _wb_request_json("GET", f"{WB_HISTORY_GOODS_URL}?{query}", token)
    buffer = _wb_request_json("GET", f"{WB_BUFFER_GOODS_URL}?{query}", token)
    return {"history": history, "buffer": buffer}


def _ozon_apply_rows(rows: list[dict[str, str]]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    activate_rows = [
        row
        for row in rows
        if row["planned_action"] == "add_to_action"
        or (
            row["planned_action"] == "update_action_price"
            and not _same_decimal(row.get("current_action_price"), row.get("calculated_action_price"))
        )
    ]
    deactivate_rows = [row for row in rows if row["planned_action"] == "deactivate_from_action"]
    return activate_rows, deactivate_rows


def _verify_ozon(
    ozon: OzonSellerAdapter,
    *,
    action_id: str,
    activate_rows: list[dict[str, str]],
    deactivate_rows: list[dict[str, str]],
    raw_dir: Path,
) -> dict[str, Any]:
    requested_prices = {str(row["product_id"]): row["calculated_action_price"] for row in activate_rows}
    deactivate_ids = {str(row["product_id"]) for row in deactivate_rows}
    attempts: list[dict[str, Any]] = []
    for attempt in range(1, 5):
        if attempt > 1:
            time.sleep(8)
        attempt_dir = ensure_dir(raw_dir / f"attempt_{attempt:02d}")
        active_rows = _fetch_action_group(ozon, action_id=action_id, source_group=ACTIVE, raw_dir=attempt_dir)
        active_by_id = {str(row.get("product_id")): row for row in active_rows}
        price_mismatches = []
        for product_id, requested_price in requested_prices.items():
            active = active_by_id.get(product_id)
            if not active:
                price_mismatches.append({"product_id": product_id, "field": "active_membership"})
                continue
            if not _same_decimal(active.get("current_action_price"), requested_price):
                price_mismatches.append(
                    {
                        "product_id": product_id,
                        "field": "current_action_price",
                        "actual": active.get("current_action_price"),
                        "expected": requested_price,
                    }
                )
        still_active_deactivated = sorted(product_id for product_id in deactivate_ids if product_id in active_by_id)
        attempt_result = {
            "attempt": attempt,
            "active_rows": len(active_rows),
            "price_mismatches": price_mismatches,
            "still_active_deactivated": still_active_deactivated,
        }
        attempts.append(attempt_result)
        if not price_mismatches and not still_active_deactivated:
            return {"status": "ok", "attempts": attempts}
    return {"status": "error", "attempts": attempts}


def _assert_expected_counts(
    *,
    pending_manifest: dict[str, Any],
    ozon_activate_count: int,
    ozon_deactivate_count: int,
    wb_guarded_count: int,
) -> None:
    expected_ozon = pending_manifest["planned_changes"]["ozon"]
    expected_wb = pending_manifest["planned_changes"]["wb"]
    expected_changed = int(expected_ozon["changed_action_price_rows"])
    expected_deactivate = int(expected_ozon["payload_deactivate_rows"])
    expected_wb_guarded = int(expected_wb["payload_rows_master_catalog_guard"])
    if ozon_activate_count != expected_changed:
        raise RuntimeError(f"Ozon drift: expected {expected_changed} activate/update rows, got {ozon_activate_count}")
    if ozon_deactivate_count != expected_deactivate:
        raise RuntimeError(f"Ozon drift: expected {expected_deactivate} deactivate rows, got {ozon_deactivate_count}")
    if wb_guarded_count != expected_wb_guarded:
        raise RuntimeError(f"WB drift: expected {expected_wb_guarded} guarded rows, got {wb_guarded_count}")


def _write_report(path: Path, result: dict[str, Any]) -> None:
    lines = [
        "# Actions Apply Result",
        "",
        f"Run ID: `{result['run_id']}`",
        f"Mode: `{result['mode']}`",
        f"Overall status: `{result['overall_status']}`",
        "",
        "## Preflight",
        "",
        f"- `run_id`: `{result['preflight']['run_id']}`",
        f"- `overall_status`: `{result['preflight']['overall_status']}`",
        "",
        "## Ozon Elastic",
        "",
        f"- Fresh dry-run: `{result['ozon']['fresh_run_id']}`",
        f"- Activate/update rows applied: `{result['ozon']['activate_rows_count']}`",
        f"- Deactivate rows applied: `{result['ozon']['deactivate_rows_count']}`",
        f"- Verify status: `{result['ozon']['verify']['status']}`",
        "",
        "## WB 65-50-50",
        "",
        f"- Fresh dry-run: `{result['wb']['fresh_run_id']}`",
        f"- Guarded payload rows applied: `{result['wb']['payload_rows_count']}`",
        f"- Excluded rows: `{result['wb']['excluded_rows_count']}`",
        f"- HTTP status: `{result['wb']['response'].get('httpStatus')}`",
        f"- Upload ID: `{result['wb'].get('upload_id')}`",
        "",
        "## Artifacts",
        "",
    ]
    for key, value in sorted(result["artifacts"].items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def run_actions_apply(
    *,
    credentials: AppCredentials,
    data_dir: Path = Path("data"),
    pending_id: str = "actions_apply_pending_20260610T232837",
    confirmed_by_user: bool = False,
    run_id: str | None = None,
) -> dict[str, Any]:
    if not confirmed_by_user:
        raise RuntimeError("explicit owner confirmation is required")
    if not credentials.ozon_seller:
        raise RuntimeError("missing Ozon Seller API credentials")
    if not credentials.wb:
        raise RuntimeError("missing WB API token")

    started_at = datetime.now()
    day = started_at.strftime("%Y-%m-%d")
    run_id = run_id or f"actions_apply_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / day / run_id)
    raw_dir = ensure_dir(run_dir / "raw")
    processed_dir = ensure_dir(run_dir / "processed")

    pending_dir = data_dir / "pending" / pending_id
    approved_id = pending_id
    assert_apply_not_repeated(data_dir=data_dir, approved_id=approved_id)
    pending_manifest = _read_json(pending_dir / "manifest.json")

    preflight = run_status_preflight(credentials=credentials, data_dir=data_dir)
    if preflight["overall_status"] != "ok":
        raise RuntimeError(f"preflight is not ok: {preflight['overall_status']}")

    ozon_plan = run_ozon_elastic_plan(credentials=credentials, data_dir=data_dir)
    wb_plan = run_wb_actions_discount_plan(credentials=credentials, data_dir=data_dir, scheme_text="65-50-50")

    ozon_rows = _read_csv(Path(ozon_plan["artifacts"]["csv"]))
    ozon_activate_rows, ozon_deactivate_rows = _ozon_apply_rows(ozon_rows)

    master = _master_skus(data_dir / "catalog" / "processed" / "master_catalog.json")
    wb_rows = _read_csv(Path(wb_plan["artifacts"]["csv"]), delimiter=";")
    wb_payload, wb_guarded_rows, wb_excluded_rows = build_wb_guarded_payload(wb_rows, master_skus=master)

    _assert_expected_counts(
        pending_manifest=pending_manifest,
        ozon_activate_count=len(ozon_activate_rows),
        ozon_deactivate_count=len(ozon_deactivate_rows),
        wb_guarded_count=len(wb_guarded_rows),
    )

    write_json(processed_dir / "ozon_activate_rows.json", ozon_activate_rows)
    write_json(processed_dir / "ozon_deactivate_rows.json", ozon_deactivate_rows)
    write_json(processed_dir / "wb_guarded_payload.json", wb_payload)
    _write_csv(wb_guarded_rows, processed_dir / "wb_guarded_rows.csv")
    _write_csv(wb_excluded_rows, processed_dir / "wb_excluded_rows.csv")

    ozon = OzonSellerAdapter(credentials.ozon_seller)
    ozon_activate_response: dict[str, Any] | None = None
    if ozon_activate_rows:
        ozon_activate_response = ozon.post(
            "/v1/actions/products/activate",
            {
                "action_id": str(ozon_plan["summary"]["action_id"]),
                "products": [
                    {
                        "product_id": str(row["product_id"]),
                        "offer_id": str(row["offer_id"]),
                        "action_price": str(row["calculated_action_price"]),
                    }
                    for row in ozon_activate_rows
                ],
            },
        )
        write_json(raw_dir / "ozon_activate_response.json", ozon_activate_response)

    ozon_deactivate_response: dict[str, Any] | None = None
    if ozon_deactivate_rows:
        ozon_deactivate_response = ozon.post(
            "/v1/actions/products/deactivate",
            {
                "action_id": str(ozon_plan["summary"]["action_id"]),
                "product_ids": [int(row["product_id"]) for row in ozon_deactivate_rows],
            },
        )
        write_json(raw_dir / "ozon_deactivate_response.json", ozon_deactivate_response)

    ozon_verify = _verify_ozon(
        ozon,
        action_id=str(ozon_plan["summary"]["action_id"]),
        activate_rows=ozon_activate_rows,
        deactivate_rows=ozon_deactivate_rows,
        raw_dir=ensure_dir(raw_dir / "ozon_verify"),
    )

    wb_response = _wb_request_json("POST", WB_UPLOAD_URL, credentials.wb.token, wb_payload)
    write_json(raw_dir / "wb_upload_response.json", wb_response)
    upload_id = ((wb_response.get("data") or {}).get("data") or {}).get("id")
    wb_polls: list[dict[str, Any]] = []
    wb_details: dict[str, Any] | None = None
    if upload_id:
        for attempt in range(1, 11):
            time.sleep(6 if attempt == 1 else 10)
            status = _wb_query_status(int(upload_id), credentials.wb.token)
            wb_polls.append({"attempt": attempt, "status": status})
            history_data = ((status.get("history") or {}).get("data") or {}).get("data") or {}
            history_status = history_data.get("status")
            if history_status in (3, 4, 5, 6):
                break
        wb_details = _wb_query_details(int(upload_id), credentials.wb.token)
    write_json(raw_dir / "wb_upload_status_polls.json", {"uploadID": upload_id, "polls": wb_polls})
    write_json(raw_dir / "wb_upload_details.json", {"uploadID": upload_id, "details": wb_details})

    wb_upload_ok = bool(upload_id) and int(wb_response.get("httpStatus") or 0) in range(200, 300)
    overall_status = "ok" if ozon_verify["status"] == "ok" and wb_upload_ok else "warning"
    artifacts = {
        "run_dir": str(run_dir),
        "summary": str(run_dir / "summary.json"),
        "report": str(run_dir / "actions_apply_result.md"),
        "ozon_activate_response": str(raw_dir / "ozon_activate_response.json"),
        "ozon_deactivate_response": str(raw_dir / "ozon_deactivate_response.json"),
        "wb_upload_response": str(raw_dir / "wb_upload_response.json"),
        "wb_upload_status_polls": str(raw_dir / "wb_upload_status_polls.json"),
        "wb_upload_details": str(raw_dir / "wb_upload_details.json"),
        "wb_guarded_payload": str(processed_dir / "wb_guarded_payload.json"),
        "wb_guarded_rows": str(processed_dir / "wb_guarded_rows.csv"),
        "wb_excluded_rows": str(processed_dir / "wb_excluded_rows.csv"),
        "run_manifest": str(run_dir / "manifest.json"),
        "apply_marker": str(apply_marker_for(data_dir=data_dir, approved_id=approved_id)),
    }
    result = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "apply",
        "overall_status": overall_status,
        "pending_id": pending_id,
        "approved_id": approved_id,
        "preflight": {
            "run_id": preflight["run_id"],
            "overall_status": preflight["overall_status"],
            "artifacts": preflight["artifacts"],
        },
        "ozon": {
            "fresh_run_id": ozon_plan["run_id"],
            "activate_rows_count": len(ozon_activate_rows),
            "deactivate_rows_count": len(ozon_deactivate_rows),
            "activate_response": ozon_activate_response,
            "deactivate_response": ozon_deactivate_response,
            "verify": ozon_verify,
        },
        "wb": {
            "fresh_run_id": wb_plan["run_id"],
            "payload_rows_count": len(wb_payload["data"]),
            "excluded_rows_count": len(wb_excluded_rows),
            "response": wb_response,
            "upload_id": upload_id,
            "last_poll": wb_polls[-1] if wb_polls else None,
        },
        "artifacts": artifacts,
    }
    write_json(run_dir / "summary.json", result)
    _write_report(run_dir / "actions_apply_result.md", result)
    manifest_paths = write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=result,
        task="actions-apply",
        mode="apply",
        risk="high",
        marketplaces=["ozon", "wb"],
        inputs={"pending_id": pending_id, "confirmed_by_user": confirmed_by_user},
    )
    mark_approved_applied(
        data_dir=data_dir,
        approved_id=approved_id,
        apply_run_id=run_id,
        task="actions-apply",
        status=overall_status,
        run_manifest_path=manifest_paths["manifest"],
        checksum=canonical_checksum({"approved_id": approved_id, "pending_manifest": pending_manifest}),
    )
    return result
