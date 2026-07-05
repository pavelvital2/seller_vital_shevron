from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from seller_agent.config import AppCredentials
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.marketplaces.wb.prices_adapter import WbPricesAdapter
from seller_agent.reports.writer import ensure_dir, write_json
from seller_agent.safety.approvals import (
    apply_marker_for,
    assert_apply_not_repeated,
    canonical_checksum,
    mark_approved_applied,
)
from seller_agent.tasks.status_preflight import run_status_preflight
from seller_agent.tasks.wb_actions_discount_plan import (
    WbActionsSnapshotLock,
    _lock_path,
    _price_value,
    run_wb_actions_discount_plan,
)


WB_UPLOAD_URL = "https://discounts-prices-api.wildberries.ru/api/v2/upload/task"
WB_HISTORY_TASK_URL = "https://discounts-prices-api.wildberries.ru/api/v2/history/tasks"
WB_HISTORY_GOODS_URL = "https://discounts-prices-api.wildberries.ru/api/v2/history/goods/task"
WB_BUFFER_TASK_URL = "https://discounts-prices-api.wildberries.ru/api/v2/buffer/tasks"
WB_BUFFER_GOODS_URL = "https://discounts-prices-api.wildberries.ru/api/v2/buffer/goods/task"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
WB_STAGED_INTERMEDIATE_DISCOUNT = 49


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter=";"))


def _write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()), delimiter=";")
        writer.writeheader()
        writer.writerows(rows)


def _latest_plan_dir(data_dir: Path) -> Path | None:
    runs_dir = data_dir / "runs"
    if not runs_dir.exists():
        return None
    candidates = [
        path
        for path in runs_dir.glob("*/*")
        if path.is_dir()
        and path.name.startswith("wb_actions_discount_plan_")
        and (path / "summary.json").exists()
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True)[0]


def _plan_dir(data_dir: Path, plan_run_id: str | None) -> Path:
    if plan_run_id:
        for path in (data_dir / "runs").glob(f"*/{plan_run_id}"):
            if path.is_dir():
                return path
        raise RuntimeError(f"WB actions discount plan run not found: {plan_run_id}")

    latest = _latest_plan_dir(data_dir)
    if latest is None:
        raise RuntimeError("WB actions discount plan run not found")
    return latest


def _payload_from_rows(rows: list[dict[str, str]]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    changed_rows = [row for row in rows if int(row["Дельта, п.п."]) != 0]
    payload = _payload_from_changed_rows(changed_rows)
    return payload, changed_rows


def _payload_from_changed_rows(
    rows: list[dict[str, str]],
    *,
    discount_override: int | None = None,
) -> dict[str, Any]:
    return {
        "data": [
            {
                "nmID": int(row["Артикул WB"]),
                "price": _price_value(row["Базовая цена"]),
                "discount": int(discount_override if discount_override is not None else row["Финальная скидка"]),
            }
            for row in rows
        ]
    }


def _payload_signature(payload: dict[str, Any]) -> set[tuple[int, str, int]]:
    return {
        (int(row["nmID"]), str(row["price"]), int(row["discount"]))
        for row in payload.get("data", [])
    }


def _row_signature(row: dict[str, str], *, discount_override: int | None = None) -> tuple[int, str, int]:
    return (
        int(row["Артикул WB"]),
        str(_price_value(row["Базовая цена"])),
        int(discount_override if discount_override is not None else row["Финальная скидка"]),
    )


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value).replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


def _discounted_price(base_price: Decimal, discount: int) -> Decimal:
    return base_price * (Decimal("100") - Decimal(discount)) / Decimal("100")


def _requires_staged_discount(row: dict[str, str]) -> bool:
    base_price = _decimal(row.get("Базовая цена"))
    if base_price is None or base_price <= 0:
        return False
    current_discount = int(row.get("Текущая скидка") or 0)
    target_discount = int(row.get("Финальная скидка") or 0)
    if current_discount >= WB_STAGED_INTERMEDIATE_DISCOUNT or target_discount <= WB_STAGED_INTERMEDIATE_DISCOUNT:
        return False
    current_price = _discounted_price(base_price, current_discount)
    target_price = _discounted_price(base_price, target_discount)
    if target_price <= 0:
        return False
    return current_price / target_price >= Decimal("2")


def _split_regular_and_staged_rows(
    *,
    eligible_payload: dict[str, Any],
    fresh_changed_rows: list[dict[str, str]],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    eligible_signatures = _payload_signature(eligible_payload)
    eligible_rows = [row for row in fresh_changed_rows if _row_signature(row) in eligible_signatures]
    staged_rows = [row for row in eligible_rows if _requires_staged_discount(row)]
    staged_signatures = {_row_signature(row) for row in staged_rows}
    regular_rows = [row for row in eligible_rows if _row_signature(row) not in staged_signatures]
    return regular_rows, staged_rows


def _assert_no_drift(
    *,
    approved_payload: dict[str, Any],
    fresh_payload: dict[str, Any],
) -> dict[str, Any]:
    approved_set = _payload_signature(approved_payload)
    fresh_set = _payload_signature(fresh_payload)
    drift = {
        "approved_payload_rows": len(approved_set),
        "fresh_payload_rows": len(fresh_set),
        "added": sorted(fresh_set - approved_set),
        "removed": sorted(approved_set - fresh_set),
    }
    if drift["added"] or drift["removed"]:
        raise RuntimeError("WB actions discount drift-check failed")
    return drift


def _build_partial_drift_payload(
    *,
    approved_payload: dict[str, Any],
    fresh_payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    approved_by_signature = {
        (int(row["nmID"]), str(row["price"]), int(row["discount"])): row
        for row in approved_payload.get("data", [])
    }
    fresh_by_signature = {
        (int(row["nmID"]), str(row["price"]), int(row["discount"])): row
        for row in fresh_payload.get("data", [])
    }
    approved_set = set(approved_by_signature)
    fresh_set = set(fresh_by_signature)
    eligible = approved_set & fresh_set
    added = fresh_set - approved_set
    removed = approved_set - fresh_set
    skipped_due_to_drift = [
        {
            "kind": "fresh_unapproved_price_or_discount",
            "fresh": {"nmID": signature[0], "price": signature[1], "discount": signature[2]},
            "row": fresh_by_signature[signature],
        }
        for signature in sorted(added, key=lambda item: item[0])
    ] + [
        {
            "kind": "approved_payload_no_longer_matching_fresh",
            "approved": {"nmID": signature[0], "price": signature[1], "discount": signature[2]},
            "row": approved_by_signature[signature],
        }
        for signature in sorted(removed, key=lambda item: item[0])
    ]
    skipped_nm_ids = sorted(
        {
            int((item.get("fresh") or item.get("approved") or {}).get("nmID"))
            for item in skipped_due_to_drift
            if (item.get("fresh") or item.get("approved") or {}).get("nmID") is not None
        }
    )
    drift = {
        "mode": "partial_apply_unchanged_rows",
        "approved_payload_rows": len(approved_set),
        "fresh_payload_rows": len(fresh_set),
        "eligible_payload_rows": len(eligible),
        "added": sorted(added),
        "removed": sorted(removed),
        "skipped_due_to_drift_count": len(skipped_due_to_drift),
        "skipped_due_to_drift_product_count": len(skipped_nm_ids),
        "skipped_due_to_drift_nm_ids": skipped_nm_ids,
        "skipped_due_to_drift": skipped_due_to_drift,
    }
    eligible_payload = {"data": [fresh_by_signature[signature] for signature in sorted(eligible)]}
    return drift, eligible_payload


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


def _write_report(path: Path, result: dict[str, Any]) -> None:
    applied = result.get("applied") or {}
    staged = applied.get("staged") if isinstance(applied.get("staged"), dict) else {}
    verify = result.get("verify") or {}
    error_summary = verify.get("error_summary") if isinstance(verify.get("error_summary"), dict) else {}
    lines = [
        "# WB Actions Discount Apply Result",
        "",
        f"Run ID: `{result['run_id']}`",
        f"Started at: `{result['started_at']}`",
        f"Overall status: `{result['overall_status']}`",
        f"Approved plan: `{result['approved_plan_run_id']}`",
        f"Fresh plan: `{result['fresh_plan']['run_id']}`",
        f"Scheme: `{result['scheme']}`",
        "",
        "## Applied",
        "",
        f"- payload rows submitted: `{result['applied']['payload_rows_count']}`",
        f"- regular rows: `{applied.get('regular_payload_rows_count', 0)}`",
        f"- staged rows: `{applied.get('staged_payload_rows_count', 0)}`",
        f"- HTTP status: `{result['applied']['response'].get('httpStatus')}`",
        f"- upload ID: `{result['applied'].get('upload_id')}`",
        f"- skipped because of drift: `{result['drift'].get('skipped_due_to_drift_count', 0)}`",
        f"- skipped products because of drift: `{result['drift'].get('skipped_due_to_drift_product_count', 0)}`",
        "",
        "## Verify",
        "",
        f"- status: `{result['verify']['status']}`",
        f"- expected rows: `{verify.get('expected_rows', 0)}`",
        f"- successful rows: `{verify.get('success_rows', 0)}`",
        f"- failed rows: `{verify.get('failed_rows', 0)}`",
        f"- poll attempts: `{len(result['verify']['polls'])}`",
        "",
        "## Staged discount",
        "",
        f"- status: `{staged.get('status', 'skipped')}`",
        f"- stage discount: `{staged.get('stage_discount', '')}`",
        f"- stage rows: `{staged.get('rows_count', 0)}`",
        f"- confirmed after stage: `{staged.get('confirmed49_rows', 0)}`",
        f"- final verified rows: `{staged.get('final_verified_rows', 0)}` / `{staged.get('target_rows', 0)}`",
        f"- final discount counts: `{json.dumps(staged.get('discount_counts_final', {}), ensure_ascii=False)}`",
        "",
        "## Upload errors",
        "",
        f"- failed upload rows: `{error_summary.get('failed_rows_count', 0)}`",
        f"- price quarantine rows: `{error_summary.get('price_quarantine_rows_count', 0)}`",
        f"- errors: `{json.dumps(error_summary.get('error_counts', {}), ensure_ascii=False)}`",
        "",
        "## Artifacts",
        "",
    ]
    for key, value in sorted(result["artifacts"].items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _latest_history_data(polls: list[dict[str, Any]]) -> dict[str, Any]:
    for poll in reversed(polls):
        data = ((((poll.get("status") or {}).get("history") or {}).get("data") or {}).get("data") or {})
        if data:
            return data
    return {}


def _latest_upload_status_data(polls: list[dict[str, Any]]) -> dict[str, Any]:
    for poll in reversed(polls):
        status = poll.get("status") or {}
        for source in ("history", "buffer"):
            data = (((status.get(source) or {}).get("data") or {}).get("data") or {})
            if data:
                return data
            direct = (status.get(source) or {}).get("data") or {}
            if direct:
                return direct
    return {}


def _history_goods(details: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(details, dict):
        return []
    goods = (((details.get("history") or {}).get("data") or {}).get("data") or {}).get("historyGoods")
    return [row for row in goods if isinstance(row, dict)] if isinstance(goods, list) else []


def _upload_error_summary(details: dict[str, Any] | None) -> dict[str, Any]:
    goods = _history_goods(details)
    failed = [row for row in goods if int(row.get("status") or 0) not in {1, 2}]
    error_counts: dict[str, int] = {}
    quarantine_rows: list[dict[str, Any]] = []
    for row in failed:
        error_text = str(row.get("errorText") or "").strip()
        if error_text:
            error_counts[error_text] = error_counts.get(error_text, 0) + 1
        lower_error = error_text.lower().replace("\xa0", " ")
        if "more than twice lower" in lower_error or "lower them gradually" in lower_error:
            quarantine_rows.append(
                {
                    "nmID": row.get("nmID"),
                    "vendorCode": row.get("vendorCode"),
                    "price": row.get("price"),
                    "discount": row.get("discount"),
                    "errorText": error_text,
                }
            )
    return {
        "history_goods_count": len(goods),
        "failed_rows_count": len(failed),
        "error_counts": error_counts,
        "price_quarantine_rows_count": len(quarantine_rows),
        "price_quarantine_rows": quarantine_rows,
    }


def _classify_verify_status(
    *,
    upload_ok: bool,
    expected_rows: int,
    success_rows: int,
    overall_rows: int,
    error_summary: dict[str, Any],
) -> str:
    if expected_rows == 0:
        return "no_rows_to_apply"
    if not upload_ok:
        return "error"
    if success_rows == expected_rows and overall_rows == expected_rows:
        return "ok"
    if success_rows == 0 and error_summary.get("price_quarantine_rows_count"):
        return "price_quarantine"
    if success_rows == 0:
        return "failed"
    if success_rows < expected_rows:
        return "partial"
    return "submitted"


def _wb_upload_and_verify(
    *,
    payload: dict[str, Any],
    token: str,
    raw_dir: Path,
    label: str,
    poll_attempts: int = 12,
) -> dict[str, Any]:
    response: dict[str, Any] = {"httpStatus": None, "data": None, "skipped": "no_eligible_rows"}
    if payload.get("data"):
        response = _wb_request_json("POST", WB_UPLOAD_URL, token, payload)
    write_json(raw_dir / f"{label}_upload_response.json", response)
    upload_id = ((response.get("data") or {}).get("data") or {}).get("id")
    polls: list[dict[str, Any]] = []
    details: dict[str, Any] | None = None
    if upload_id:
        for attempt in range(1, poll_attempts + 1):
            time.sleep(6 if attempt == 1 else 10)
            status = _wb_query_status(int(upload_id), token)
            polls.append({"attempt": attempt, "status": status})
            status_data = _latest_upload_status_data([polls[-1]])
            if status_data.get("status") in (3, 4, 5, 6):
                break
        details = _wb_query_details(int(upload_id), token)
    write_json(raw_dir / f"{label}_status_polls.json", {"uploadID": upload_id, "polls": polls})
    write_json(raw_dir / f"{label}_details.json", {"uploadID": upload_id, "details": details})

    upload_ok = bool(upload_id) and int(response.get("httpStatus") or 0) in range(200, 300)
    status_data = _latest_upload_status_data(polls)
    expected_rows = len(payload.get("data") or [])
    success_rows = int(status_data.get("successGoodsNumber") or 0)
    overall_rows = int(status_data.get("overAllGoodsNumber") or 0)
    error_summary = _upload_error_summary(details)
    verify_status = _classify_verify_status(
        upload_ok=upload_ok,
        expected_rows=expected_rows,
        success_rows=success_rows,
        overall_rows=overall_rows,
        error_summary=error_summary,
    )
    return {
        "label": label,
        "payload_rows_count": expected_rows,
        "response": response,
        "upload_id": upload_id,
        "polls": polls,
        "details": details,
        "upload_ok": upload_ok,
        "status_data": status_data,
        "verify_status": verify_status,
        "success_rows": success_rows,
        "overall_rows": overall_rows,
        "failed_rows": max(expected_rows - success_rows, 0) if overall_rows else error_summary.get("failed_rows_count", 0),
        "error_summary": error_summary,
    }


def _extract_quarantine_goods(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("json") if "json" in payload else payload
    if isinstance(data, dict):
        data = data.get("data", data)
    goods = data.get("quarantineGoods") if isinstance(data, dict) else None
    return [row for row in goods if isinstance(row, dict)] if isinstance(goods, list) else []


def _run_wb_quarantine_apply_new_price(
    *,
    targets: list[dict[str, Any]],
    data_dir: Path,
    run_dir: Path,
    raw_dir: Path,
    label: str,
) -> dict[str, Any]:
    target_path = run_dir / "processed" / f"{label}_quarantine_targets.json"
    output_path = raw_dir / f"{label}_quarantine_apply_new_price.json"
    write_json(target_path, {"targets": targets})
    command = [
        "node",
        str(PROJECT_ROOT / "scripts/actions/wb_quarantine_apply_new_price.js"),
        "--targets",
        str(target_path),
        "--out",
        str(output_path),
    ]
    env = os.environ.copy()
    env.setdefault("NODE_PATH", "/home/Codex/agent-tools/node/node_modules")
    with WbActionsSnapshotLock(_lock_path(data_dir)):
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=180,
            check=False,
        )
    if output_path.exists():
        result = json.loads(output_path.read_text(encoding="utf-8"))
    else:
        result = {
            "status": "blocked",
            "error": (completed.stderr or completed.stdout or "WB quarantine apply script failed").strip()[:2000],
        }
        write_json(output_path, result)
    result["returncode"] = completed.returncode
    if completed.returncode != 0 and result.get("status") == "ok":
        result["status"] = "blocked"
    return result


def _fetch_current_price_rows(credentials: AppCredentials, nm_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not credentials.wb:
        return {}
    wanted = {int(nm_id) for nm_id in nm_ids}
    rows = WbPricesAdapter(credentials.wb).fetch_goods_prices(limit=1000)
    return {int(row["nmID"]): row for row in rows if row.get("nmID") is not None and int(row["nmID"]) in wanted}


def _discount_counts(rows_by_nm_id: dict[int, dict[str, Any]], nm_ids: list[int]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for nm_id in nm_ids:
        row = rows_by_nm_id.get(int(nm_id)) or {}
        value = row.get("discount")
        key = str(value) if value is not None else "missing"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _run_staged_discount_flow(
    *,
    credentials: AppCredentials,
    data_dir: Path,
    run_dir: Path,
    raw_dir: Path,
    processed_dir: Path,
    stage_rows: list[dict[str, str]],
) -> dict[str, Any]:
    if not credentials.wb:
        raise RuntimeError("missing WB API token")
    if not stage_rows:
        return {"status": "skipped", "rows_count": 0}

    stage49_payload = _payload_from_changed_rows(stage_rows, discount_override=WB_STAGED_INTERMEDIATE_DISCOUNT)
    target_payload = _payload_from_changed_rows(stage_rows)
    write_json(processed_dir / "staged_stage49_payload.json", stage49_payload)
    write_json(processed_dir / "staged_target_payload.json", target_payload)
    _write_csv(stage_rows, processed_dir / "staged_changed_rows.csv")

    stage49 = _wb_upload_and_verify(
        payload=stage49_payload,
        token=credentials.wb.token,
        raw_dir=raw_dir,
        label="staged_stage49",
    )
    quarantine_apply = _run_wb_quarantine_apply_new_price(
        targets=stage49_payload["data"],
        data_dir=data_dir,
        run_dir=run_dir,
        raw_dir=raw_dir,
        label="staged_stage49",
    )
    matched_ids = [int(item["nmID"]) for item in quarantine_apply.get("matched_targets", []) if item.get("nmID") is not None]
    current49 = _fetch_current_price_rows(credentials, matched_ids)
    write_json(processed_dir / "staged_current_prices_after_49.json", current49)
    confirmed49_ids = [
        nm_id
        for nm_id in matched_ids
        if int((current49.get(nm_id) or {}).get("discount") or -1) == WB_STAGED_INTERMEDIATE_DISCOUNT
    ]
    confirmed49_set = set(confirmed49_ids)
    stage55_payload = {
        "data": [row for row in target_payload["data"] if int(row["nmID"]) in confirmed49_set]
    }
    write_json(processed_dir / "staged_stage55_payload.json", stage55_payload)
    stage55 = _wb_upload_and_verify(
        payload=stage55_payload,
        token=credentials.wb.token,
        raw_dir=raw_dir,
        label="staged_stage55",
    )
    target_by_nm_id = {int(row["nmID"]): int(row["discount"]) for row in target_payload["data"]}
    current_final = _fetch_current_price_rows(credentials, list(target_by_nm_id))
    write_json(processed_dir / "staged_current_prices_final.json", current_final)
    final_ok_ids = [
        nm_id
        for nm_id, target_discount in target_by_nm_id.items()
        if int((current_final.get(nm_id) or {}).get("discount") or -1) == target_discount
    ]
    status = "ok"
    if len(final_ok_ids) != len(target_by_nm_id):
        status = "partial" if final_ok_ids else "blocked"
    result = {
        "status": status,
        "rows_count": len(stage_rows),
        "stage_discount": WB_STAGED_INTERMEDIATE_DISCOUNT,
        "stage49": stage49,
        "quarantine_apply_new_price": quarantine_apply,
        "confirmed49_rows": len(confirmed49_ids),
        "stage55": stage55,
        "final_verified_rows": len(final_ok_ids),
        "target_rows": len(target_by_nm_id),
        "discount_counts_after_49": _discount_counts(current49, matched_ids),
        "discount_counts_final": _discount_counts(current_final, list(target_by_nm_id)),
        "missing_after_final_nm_ids": sorted(set(target_by_nm_id) - set(final_ok_ids)),
    }
    write_json(processed_dir / "staged_discount_result.json", result)
    return result


def run_wb_actions_discount_apply(
    *,
    credentials: AppCredentials,
    data_dir: Path = Path("data"),
    plan_run_id: str | None = None,
    run_id: str | None = None,
    confirmed_by_user: bool = False,
) -> dict[str, Any]:
    if not confirmed_by_user:
        raise RuntimeError("explicit owner confirmation is required")
    if not credentials.wb:
        raise RuntimeError("missing WB API token")

    approved_plan_dir = _plan_dir(data_dir, plan_run_id)
    approved_id = approved_plan_dir.name
    assert_apply_not_repeated(data_dir=data_dir, approved_id=approved_id)
    approved_summary = json.loads((approved_plan_dir / "summary.json").read_text(encoding="utf-8"))
    scheme = str(approved_summary["summary"]["scheme"])
    approved_csv = Path(approved_summary["artifacts"]["csv"])
    if not approved_csv.is_absolute():
        approved_csv = Path.cwd() / approved_csv
    approved_payload, approved_changed_rows = _payload_from_rows(_read_csv(approved_csv))

    started_at = datetime.now()
    run_id = run_id or f"wb_actions_discount_apply_{scheme}_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    raw_dir = ensure_dir(run_dir / "raw")
    processed_dir = ensure_dir(run_dir / "processed")

    preflight = run_status_preflight(
        credentials=credentials,
        data_dir=data_dir,
        include_lk=False,
        marketplaces=("wb",),
        include_ozon_performance=False,
    )
    if preflight["overall_status"] != "ok":
        raise RuntimeError(f"preflight is not ok: {preflight['overall_status']}")

    fresh_plan = run_wb_actions_discount_plan(credentials=credentials, data_dir=data_dir, scheme_text=scheme)
    fresh_csv = Path(fresh_plan["artifacts"]["csv"])
    fresh_payload, fresh_changed_rows = _payload_from_rows(_read_csv(fresh_csv))
    drift, upload_payload = _build_partial_drift_payload(
        approved_payload=approved_payload,
        fresh_payload=fresh_payload,
    )
    regular_rows, staged_rows = _split_regular_and_staged_rows(
        eligible_payload=upload_payload,
        fresh_changed_rows=fresh_changed_rows,
    )
    regular_payload = _payload_from_changed_rows(regular_rows)

    write_json(processed_dir / "approved_payload.json", approved_payload)
    write_json(processed_dir / "fresh_payload.json", fresh_payload)
    write_json(processed_dir / "upload_payload.json", upload_payload)
    write_json(processed_dir / "regular_upload_payload.json", regular_payload)
    write_json(processed_dir / "drift_check.json", drift)
    write_json(processed_dir / "skipped_drift_rows.json", drift["skipped_due_to_drift"])
    _write_csv(approved_changed_rows, processed_dir / "approved_changed_rows.csv")
    _write_csv(fresh_changed_rows, processed_dir / "fresh_changed_rows.csv")
    _write_csv(regular_rows, processed_dir / "regular_changed_rows.csv")

    regular_upload = _wb_upload_and_verify(
        payload=regular_payload,
        token=credentials.wb.token,
        raw_dir=raw_dir,
        label="regular",
    )
    staged = _run_staged_discount_flow(
        credentials=credentials,
        data_dir=data_dir,
        run_dir=run_dir,
        raw_dir=raw_dir,
        processed_dir=processed_dir,
        stage_rows=staged_rows,
    )
    response = regular_upload["response"]
    upload_id = regular_upload["upload_id"]
    polls = regular_upload["polls"]
    details = regular_upload["details"]
    history_data = regular_upload["status_data"]
    expected_rows = len(upload_payload["data"])
    success_rows = int(regular_upload["success_rows"]) + int(staged.get("final_verified_rows") or 0)
    overall_rows = int(regular_upload["overall_rows"]) + int(staged.get("target_rows") or 0)
    error_summary = regular_upload["error_summary"]
    regular_status = str(regular_upload["verify_status"])
    staged_status = str(staged.get("status") or "skipped")
    if regular_status in {"ok", "no_rows_to_apply"} and staged_status in {"ok", "skipped"}:
        verify_status = "ok" if expected_rows else "no_rows_to_apply"
    elif "partial" in {regular_status, staged_status}:
        verify_status = "partial"
    elif "submitted" in {regular_status, staged_status}:
        verify_status = "submitted"
    elif staged_status == "blocked":
        verify_status = "staged_discount_blocked"
    else:
        verify_status = regular_status
    artifacts = {
        "run_dir": str(run_dir),
        "summary": str(run_dir / "summary.json"),
        "report": str(run_dir / "wb_actions_discount_apply_result.md"),
        "approved_payload": str(processed_dir / "approved_payload.json"),
        "fresh_payload": str(processed_dir / "fresh_payload.json"),
        "upload_payload": str(processed_dir / "upload_payload.json"),
        "regular_upload_payload": str(processed_dir / "regular_upload_payload.json"),
        "drift_check": str(processed_dir / "drift_check.json"),
        "skipped_drift_rows": str(processed_dir / "skipped_drift_rows.json"),
        "approved_changed_rows": str(processed_dir / "approved_changed_rows.csv"),
        "fresh_changed_rows": str(processed_dir / "fresh_changed_rows.csv"),
        "regular_changed_rows": str(processed_dir / "regular_changed_rows.csv"),
        "upload_response": str(raw_dir / "regular_upload_response.json"),
        "upload_status_polls": str(raw_dir / "regular_status_polls.json"),
        "upload_details": str(raw_dir / "regular_details.json"),
        "run_manifest": str(run_dir / "manifest.json"),
        "apply_marker": str(apply_marker_for(data_dir=data_dir, approved_id=approved_id)),
    }
    if staged_rows:
        artifacts.update(
            {
                "staged_changed_rows": str(processed_dir / "staged_changed_rows.csv"),
                "staged_stage49_payload": str(processed_dir / "staged_stage49_payload.json"),
                "staged_stage55_payload": str(processed_dir / "staged_stage55_payload.json"),
                "staged_result": str(processed_dir / "staged_discount_result.json"),
                "staged_quarantine_apply_new_price": str(raw_dir / "staged_stage49_quarantine_apply_new_price.json"),
            }
        )
    if verify_status == "ok" and not drift["skipped_due_to_drift_count"]:
        overall_status = "ok"
    elif verify_status in {"partial", "submitted", "no_rows_to_apply"} or drift["skipped_due_to_drift_count"]:
        overall_status = "warning"
    else:
        overall_status = "blocked"
    result = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "apply",
        "overall_status": overall_status,
        "approved_plan_run_id": approved_id,
        "approved_id": approved_id,
        "scheme": scheme,
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
            "payload_rows_count": len(upload_payload["data"]),
            "regular_payload_rows_count": len(regular_payload["data"]),
            "staged_payload_rows_count": len(staged_rows),
            "response": response,
            "upload_id": upload_id,
            "regular": regular_upload,
            "staged": staged,
        },
        "verify": {
            "status": verify_status,
            "expected_rows": expected_rows,
            "success_rows": success_rows,
            "overall_rows": overall_rows,
            "failed_rows": max(expected_rows - success_rows, 0),
            "error_summary": error_summary,
            "polls": polls,
            "details": details,
        },
        "artifacts": artifacts,
    }
    write_json(run_dir / "summary.json", result)
    _write_report(run_dir / "wb_actions_discount_apply_result.md", result)
    manifest_paths = write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=result,
        task="wb-actions-discount-apply",
        mode="apply",
        risk="high",
        marketplaces=["wb"],
        inputs={"plan_run_id": plan_run_id, "confirmed_by_user": confirmed_by_user},
    )
    mark_approved_applied(
        data_dir=data_dir,
        approved_id=approved_id,
        apply_run_id=run_id,
        task="wb-actions-discount-apply",
        status=result["overall_status"],
        run_manifest_path=manifest_paths["manifest"],
        checksum=canonical_checksum({"approved_id": approved_id, "drift": drift}),
    )
    return result
