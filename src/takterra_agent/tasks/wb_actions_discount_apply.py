from __future__ import annotations

import csv
from datetime import datetime
import json
from pathlib import Path
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from takterra_agent.config import AppCredentials
from takterra_agent.reports.writer import ensure_dir, write_json
from takterra_agent.tasks.status_preflight import run_status_preflight
from takterra_agent.tasks.wb_actions_discount_plan import _price_value, run_wb_actions_discount_plan


WB_UPLOAD_URL = "https://discounts-prices-api.wildberries.ru/api/v2/upload/task"
WB_HISTORY_TASK_URL = "https://discounts-prices-api.wildberries.ru/api/v2/history/tasks"
WB_HISTORY_GOODS_URL = "https://discounts-prices-api.wildberries.ru/api/v2/history/goods/task"
WB_BUFFER_TASK_URL = "https://discounts-prices-api.wildberries.ru/api/v2/buffer/tasks"
WB_BUFFER_GOODS_URL = "https://discounts-prices-api.wildberries.ru/api/v2/buffer/goods/task"


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
    payload = {
        "data": [
            {
                "nmID": int(row["Артикул WB"]),
                "price": _price_value(row["Базовая цена"]),
                "discount": int(row["Финальная скидка"]),
            }
            for row in changed_rows
        ]
    }
    return payload, changed_rows


def _payload_signature(payload: dict[str, Any]) -> set[tuple[int, str, int]]:
    return {
        (int(row["nmID"]), str(row["price"]), int(row["discount"]))
        for row in payload.get("data", [])
    }


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
        f"- HTTP status: `{result['applied']['response'].get('httpStatus')}`",
        f"- upload ID: `{result['applied'].get('upload_id')}`",
        "",
        "## Verify",
        "",
        f"- status: `{result['verify']['status']}`",
        f"- poll attempts: `{len(result['verify']['polls'])}`",
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

    preflight = run_status_preflight(credentials=credentials, data_dir=data_dir)
    if preflight["overall_status"] != "ok":
        raise RuntimeError(f"preflight is not ok: {preflight['overall_status']}")

    fresh_plan = run_wb_actions_discount_plan(credentials=credentials, data_dir=data_dir, scheme_text=scheme)
    fresh_csv = Path(fresh_plan["artifacts"]["csv"])
    fresh_payload, fresh_changed_rows = _payload_from_rows(_read_csv(fresh_csv))
    drift = _assert_no_drift(approved_payload=approved_payload, fresh_payload=fresh_payload)

    write_json(processed_dir / "approved_payload.json", approved_payload)
    write_json(processed_dir / "fresh_payload.json", fresh_payload)
    write_json(processed_dir / "drift_check.json", drift)
    _write_csv(approved_changed_rows, processed_dir / "approved_changed_rows.csv")
    _write_csv(fresh_changed_rows, processed_dir / "fresh_changed_rows.csv")

    response = _wb_request_json("POST", WB_UPLOAD_URL, credentials.wb.token, fresh_payload)
    write_json(raw_dir / "wb_upload_response.json", response)
    upload_id = ((response.get("data") or {}).get("data") or {}).get("id")
    polls: list[dict[str, Any]] = []
    details: dict[str, Any] | None = None
    if upload_id:
        for attempt in range(1, 11):
            time.sleep(6 if attempt == 1 else 10)
            status = _wb_query_status(int(upload_id), credentials.wb.token)
            polls.append({"attempt": attempt, "status": status})
            history_data = ((status.get("history") or {}).get("data") or {}).get("data") or {}
            history_status = history_data.get("status")
            if history_status in (3, 4, 5, 6):
                break
        details = _wb_query_details(int(upload_id), credentials.wb.token)
    write_json(raw_dir / "wb_upload_status_polls.json", {"uploadID": upload_id, "polls": polls})
    write_json(raw_dir / "wb_upload_details.json", {"uploadID": upload_id, "details": details})

    upload_ok = bool(upload_id) and int(response.get("httpStatus") or 0) in range(200, 300)
    history_data = _latest_history_data(polls)
    expected_rows = len(fresh_payload["data"])
    success_rows = int(history_data.get("successGoodsNumber") or 0)
    overall_rows = int(history_data.get("overAllGoodsNumber") or 0)
    if upload_ok and expected_rows and success_rows == expected_rows and overall_rows == expected_rows:
        verify_status = "ok"
    elif upload_ok:
        verify_status = "submitted"
    else:
        verify_status = "error"
    artifacts = {
        "run_dir": str(run_dir),
        "summary": str(run_dir / "summary.json"),
        "report": str(run_dir / "wb_actions_discount_apply_result.md"),
        "approved_payload": str(processed_dir / "approved_payload.json"),
        "fresh_payload": str(processed_dir / "fresh_payload.json"),
        "drift_check": str(processed_dir / "drift_check.json"),
        "approved_changed_rows": str(processed_dir / "approved_changed_rows.csv"),
        "fresh_changed_rows": str(processed_dir / "fresh_changed_rows.csv"),
        "upload_response": str(raw_dir / "wb_upload_response.json"),
        "upload_status_polls": str(raw_dir / "wb_upload_status_polls.json"),
        "upload_details": str(raw_dir / "wb_upload_details.json"),
    }
    result = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "apply",
        "overall_status": "ok" if upload_ok else "warning",
        "approved_plan_run_id": approved_plan_dir.name,
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
            "payload_rows_count": len(fresh_payload["data"]),
            "response": response,
            "upload_id": upload_id,
        },
        "verify": {
            "status": verify_status,
            "polls": polls,
            "details": details,
        },
        "artifacts": artifacts,
    }
    write_json(run_dir / "summary.json", result)
    _write_report(run_dir / "wb_actions_discount_apply_result.md", result)
    return result
