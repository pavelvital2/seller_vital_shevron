from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import subprocess
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from seller_agent.config import AppCredentials
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.reports.writer import ensure_dir, write_json
from seller_agent.tasks.wb_actions_discount_plan import (
    WbActionsSnapshotLock,
    _lock_path,
)

from scripts.actions.wb_best_price_action_apply import (
    current_price_verify,
    download_action_snapshot,
    fresh_minimum_verify,
    verify_action_participation,
)
from scripts.actions.wb_best_price_action_plan import (
    build_plan,
    generate_plan_artifacts,
    json_ready,
    read_json,
    read_promo_rows,
)
from scripts.pricing.wb_price_grid import file_sha256, validate_plan_hash


def run_wb_best_price_action_plan(
    *,
    credentials: AppCredentials,
    data_dir: Path = Path("data"),
    outside_discount: int = 50,
    run_id: str | None = None,
    price_plan_path: Path | None = None,
) -> dict[str, Any]:
    _require_wb(credentials)
    discount = _discount(outside_discount)
    started = datetime.now().astimezone()
    resolved_run_id = run_id or (
        f"wb_best_price_actions_plan_{discount}_{started.strftime('%Y%m%dT%H%M%S')}"
    )
    run_dir = ensure_dir(data_dir / "runs" / started.strftime("%Y-%m-%d") / resolved_run_id)
    raw_dir = ensure_dir(run_dir / "raw")
    prices_dir = ensure_dir(run_dir / "prices")
    source_plan = (
        _resolve_project_path(price_plan_path)
        if price_plan_path is not None
        else _latest_applied_price_plan(data_dir)
    )

    with WbActionsSnapshotLock(_lock_path(data_dir)):
        _run_command(
            [
                "node",
                "scripts/actions/wb_download_active_actions.js",
                "--date",
                started.strftime("%Y-%m-%d"),
                "--out-dir",
                str(raw_dir),
                "--prices-dir",
                str(prices_dir),
            ]
        )

    snapshot_path = raw_dir / "cabinet-actions-snapshot.json"
    prices_path = _fresh_prices_path(prices_dir)
    report = generate_plan_artifacts(
        snapshot_path=snapshot_path,
        prices_path=prices_path,
        price_plan_path=source_plan,
        run_dir=run_dir,
        outside_discount=discount,
    )
    summary = dict(report["summary"])
    safe_to_apply = (
        int(summary.get("target_below_minimum") or 0) == 0
        and int(summary.get("unsafe_single_upload") or 0) == 0
    )
    result = {
        "run_id": resolved_run_id,
        "overall_status": "ok" if safe_to_apply else "warning",
        "outside_action_discount": discount,
        "safe_to_apply": safe_to_apply,
        "summary": summary,
        "artifacts": {
            "run_dir": str(run_dir),
            "report": str(run_dir / "report.html"),
            "markdown": str(run_dir / "report.md"),
            "xlsx": str(run_dir / "report.xlsx"),
            "json": str(run_dir / "report.json"),
            "run_manifest": str(run_dir / "manifest.json"),
        },
    }
    write_json(run_dir / "summary.json", result)
    write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=result,
        task="wb-best-price-action-plan",
        mode="dry_run",
        risk="normal",
        marketplaces=["wb"],
        inputs={"outside_discount": discount},
        source_run_ids=[source_plan.parent.name],
        lifecycle_status="pending_review",
        closed=False,
    )
    return result


def run_wb_best_price_action_apply(
    *,
    credentials: AppCredentials,
    data_dir: Path,
    plan_run_id: str,
    confirmed_by_user: bool,
    run_id: str | None = None,
) -> dict[str, Any]:
    _require_wb(credentials)
    if not confirmed_by_user:
        raise RuntimeError("explicit owner confirmation is required")
    plan_dir = _plan_dir(data_dir, plan_run_id)
    report_path = plan_dir / "report.json"
    approved = read_json(report_path)
    source_plan = _resolve_project_path(Path(str(approved["source"]["price_plan"])))
    resolved_run_id = run_id or (
        f"wb_best_price_action_apply_{datetime.now().astimezone().strftime('%Y%m%dT%H%M%S')}"
    )
    command = [
        sys.executable,
        "scripts/actions/wb_best_price_action_apply.py",
        "--approved-report",
        str(report_path),
        "--approved-sha256",
        _sha256(report_path),
        "--price-plan",
        str(source_plan),
        "--price-plan-sha256",
        file_sha256(source_plan),
        "--outside-discount",
        str(_discount(approved["outside_action_discount"])),
        "--data-dir",
        str(data_dir),
        "--run-id",
        resolved_run_id,
        "--confirmed-by-user",
    ]
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        timeout=1800,
        check=False,
    )
    summary_path = _find_summary(data_dir, resolved_run_id, "wb_best_price_action_apply_")
    if summary_path is not None:
        result = read_json(summary_path)
        if completed.returncode not in {0, 3}:
            result["overall_status"] = "blocked"
            result["safe_error"] = _safe_process_error(completed)
        return result
    if completed.returncode != 0:
        raise RuntimeError(_safe_process_error(completed))
    raise RuntimeError("WB best-price apply finished without summary.json")


def run_wb_best_price_action_verify(
    *,
    credentials: AppCredentials,
    data_dir: Path,
    plan_run_id: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    _require_wb(credentials)
    plan_dir = _plan_dir(data_dir, plan_run_id)
    approved = read_json(plan_dir / "report.json")
    source_plan_path = _resolve_project_path(Path(str(approved["source"]["price_plan"])))
    source_plan_hash = file_sha256(source_plan_path)
    price_plan = validate_plan_hash(source_plan_path, source_plan_hash)
    started = datetime.now().astimezone()
    resolved_run_id = run_id or f"wb_best_price_action_verify_{started.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started.strftime("%Y-%m-%d") / resolved_run_id)
    ensure_dir(run_dir / "raw")
    ensure_dir(run_dir / "processed")

    minimum = fresh_minimum_verify(
        run_dir=run_dir,
        price_plan_path=source_plan_path,
        price_plan_hash=source_plan_hash,
    )
    snapshot_path, snapshot = download_action_snapshot(run_dir, "verify")
    _, promo_rows = read_promo_rows(snapshot_path)
    fresh_products, _, fresh_summary = build_plan(
        snapshot=snapshot,
        prices=snapshot["prices"],
        price_plan=price_plan,
        promo_rows_by_nm=promo_rows,
        outside_discount=_discount(approved["outside_action_discount"]),
    )
    target_check = _target_plan_check(approved["products"], fresh_products)
    price = current_price_verify(
        credentials=credentials,
        products=fresh_products,
        run_dir=run_dir,
    )
    selected = [row for row in fresh_products if row.get("chosen_action_id")]
    actions = verify_action_participation(
        run_dir=run_dir,
        selected_products=selected,
        attempts=1,
        delay=0,
    )
    overall_status = (
        "ok"
        if minimum["status"] == "ok"
        and target_check["status"] == "ok"
        and price["status"] == "ok"
        and actions["status"] == "ok"
        else "warning"
    )
    result = {
        "run_id": resolved_run_id,
        "overall_status": overall_status,
        "plan_run_id": plan_run_id,
        "minimum_verify": minimum,
        "target_plan_verify": target_check,
        "price_verify": {key: value for key, value in price.items() if key != "rows"},
        "action_verify": actions,
        "fresh_summary": json_ready(fresh_summary),
        "artifacts": {
            "run_dir": str(run_dir),
            "report": str(run_dir / "summary.json"),
            "minimum_verify": str(run_dir / "processed" / "minimum_verify.json"),
            "price_verify": str(run_dir / "processed" / "price_verify.json"),
            "action_verify": str(run_dir / "processed" / "action_participation_verify.json"),
        },
    }
    write_json(run_dir / "processed" / "target_plan_verify.json", target_check)
    write_json(run_dir / "summary.json", result)
    write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=result,
        task="wb-best-price-action-verify",
        mode="verify",
        risk="low",
        marketplaces=["wb"],
        inputs={"plan_run_id": plan_run_id},
        source_run_ids=[plan_run_id],
        lifecycle_status="verified" if overall_status == "ok" else "needs_attention",
        closed=overall_status == "ok",
    )
    return result


def _target_plan_check(
    approved_products: list[dict[str, Any]],
    fresh_products: list[dict[str, Any]],
) -> dict[str, Any]:
    def signature(row: dict[str, Any]) -> tuple[Any, ...]:
        def decimal_text(value: Any) -> str:
            return str(Decimal(str(value)).normalize())

        return (
            int(row["nm_id"]),
            decimal_text(row["minimum"]),
            int(row["chosen_action_id"]) if row.get("chosen_action_id") not in ("", None) else 0,
            int(row["target_discount"]),
            decimal_text(row["target_price"]),
        )

    approved = {int(row["nm_id"]): signature(row) for row in approved_products}
    fresh = {int(row["nm_id"]): signature(row) for row in fresh_products}
    mismatches = [
        {"nm_id": nm_id, "approved": approved.get(nm_id), "fresh": fresh.get(nm_id)}
        for nm_id in sorted(set(approved) | set(fresh))
        if approved.get(nm_id) != fresh.get(nm_id)
    ]
    return {
        "status": "ok" if not mismatches else "warning",
        "approved_rows": len(approved),
        "fresh_rows": len(fresh),
        "mismatch_rows": len(mismatches),
        "mismatches": mismatches,
    }


def _latest_applied_price_plan(data_dir: Path) -> Path:
    summaries = sorted(
        (data_dir / "runs").glob("*/wb_price_grid_apply_*/summary.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    expected_hash = ""
    for path in summaries:
        payload = read_json(path)
        if payload.get("overall_status") == "ok" and payload.get("plan_sha256"):
            expected_hash = str(payload["plan_sha256"])
            break
    if not expected_hash:
        raise RuntimeError("verified WB price-grid apply was not found")
    candidates = sorted(
        (data_dir / "runs").glob("*/wb_price_grid_plan_*/plan.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        if file_sha256(path) == expected_hash:
            return path
    raise RuntimeError("applied WB price-grid source plan was not found by checksum")


def _fresh_prices_path(prices_dir: Path) -> Path:
    candidates = [
        path
        for path in prices_dir.glob("current-prices-list-goods-filter-*.json")
        if "latest" not in path.name
    ]
    if not candidates:
        raise RuntimeError("fresh WB prices file was not created")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _plan_dir(data_dir: Path, run_id: str) -> Path:
    if not run_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in run_id):
        raise ValueError("invalid WB best-price plan run id")
    candidates = list((data_dir / "runs").glob(f"*/{run_id}"))
    if len(candidates) != 1 or not candidates[0].is_dir():
        raise RuntimeError(f"WB best-price plan not found: {run_id}")
    return candidates[0]


def _find_summary(data_dir: Path, run_id: str, prefix: str) -> Path | None:
    if run_id:
        matches = list((data_dir / "runs").glob(f"*/{run_id}/summary.json"))
        return matches[0] if len(matches) == 1 else None
    candidates = sorted(
        (data_dir / "runs").glob(f"*/{prefix}*/summary.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _run_id_from_output(output: str) -> str:
    try:
        payload = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        return ""
    return str(payload.get("run_id") or "")


def _run_command(command: list[str]) -> None:
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        timeout=900,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(_safe_process_error(completed))


def _safe_process_error(completed: subprocess.CompletedProcess[str]) -> str:
    text = " ".join((completed.stderr or completed.stdout or "command failed").split())
    lowered = text.lower()
    if any(marker in lowered for marker in ("token", "cookie", "authorization", "storage_state")):
        return f"subprocess failed with code {completed.returncode}"
    return text[-1000:]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_project_path(path: Path) -> Path:
    return path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def _discount(value: Any) -> int:
    try:
        discount = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("outside-action discount must be an integer from 0 to 99") from exc
    if not 0 <= discount <= 99:
        raise ValueError("outside-action discount must be an integer from 0 to 99")
    return discount


def _require_wb(credentials: AppCredentials) -> None:
    if credentials.wb is None:
        raise RuntimeError("WB credentials are not configured")
