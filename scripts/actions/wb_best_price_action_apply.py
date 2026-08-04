#!/usr/bin/env python3
"""Apply an owner-approved WB best-price action plan with fresh drift checks."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from scripts.actions.wb_best_price_action_plan import (  # noqa: E402
    build_plan,
    json_ready,
    price_plan_with_fresh_minimums,
    read_json,
    read_promo_rows,
)
from scripts.pricing.wb_price_grid import (  # noqa: E402
    file_sha256 as price_plan_sha256,
    verify_minimum_targets,
    validate_plan_hash,
)
from seller_agent.config import load_credentials  # noqa: E402
from seller_agent.core.run_manifest import write_summary_run_manifest  # noqa: E402
from seller_agent.marketplaces.wb.prices_adapter import WbPricesAdapter  # noqa: E402
from seller_agent.reports.writer import ensure_dir, write_json  # noqa: E402
from seller_agent.safety.approvals import (  # noqa: E402
    apply_marker_for,
    assert_apply_not_repeated,
    canonical_checksum,
    mark_approved_applied,
)
from seller_agent.tasks.status_preflight import run_status_preflight  # noqa: E402
from seller_agent.tasks.wb_actions_discount_apply import (  # noqa: E402
    _verify_current_discount_payload,
    _wb_upload_and_verify,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--approved-report", type=Path, required=True)
    parser.add_argument("--approved-sha256", required=True)
    parser.add_argument("--price-plan", type=Path, required=True)
    parser.add_argument("--price-plan-sha256", required=True)
    parser.add_argument("--outside-discount", type=int, default=50)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--run-id")
    parser.add_argument("--confirmed-by-user", action="store_true")
    parser.add_argument("--action-verify-attempts", type=int, default=6)
    parser.add_argument("--action-verify-delay", type=int, default=20)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def as_decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def product_signature(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        int(row["nm_id"]),
        str(as_decimal(row["base_price"]).normalize()),
        str(as_decimal(row["minimum"]).normalize()),
        int(row["current_discount"]),
        str(as_decimal(row["current_price"]).normalize()),
        int(row["chosen_action_id"]) if row.get("chosen_action_id") not in ("", None) else 0,
        int(row["target_discount"]),
        str(as_decimal(row["target_price"]).normalize()),
    )


def plan_drift(
    approved_products: list[dict[str, Any]],
    fresh_products: list[dict[str, Any]],
) -> dict[str, Any]:
    approved = {int(row["nm_id"]): product_signature(row) for row in approved_products}
    fresh = {int(row["nm_id"]): product_signature(row) for row in fresh_products}
    all_ids = sorted(set(approved) | set(fresh))
    rows = [
        {
            "nm_id": nm_id,
            "approved": approved.get(nm_id),
            "fresh": fresh.get(nm_id),
        }
        for nm_id in all_ids
        if approved.get(nm_id) != fresh.get(nm_id)
    ]
    return {
        "status": "ok" if not rows else "blocked",
        "approved_rows": len(approved),
        "fresh_rows": len(fresh),
        "drift_rows_count": len(rows),
        "drift_rows": rows,
    }


def target_payload(products: list[dict[str, Any]], *, changes_only: bool) -> dict[str, Any]:
    rows = []
    for row in products:
        if changes_only and int(row["current_discount"]) == int(row["target_discount"]):
            continue
        rows.append(
            {
                "nmID": int(row["nm_id"]),
                "price": int(as_decimal(row["base_price"])),
                "discount": int(row["target_discount"]),
            }
        )
    return {"data": rows}


def run_command(command: list[str], *, timeout: int = 900) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    result = {
        "returncode": completed.returncode,
        "stdout": completed.stdout[-8000:],
        "stderr": completed.stderr[-2000:],
    }
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): "
            f"{(completed.stderr or completed.stdout).strip()[-1000:]}"
        )
    return result


def fresh_minimum_verify(
    *,
    run_dir: Path,
    expected_products: list[dict[str, Any]],
) -> dict[str, Any]:
    out_dir = ensure_dir(run_dir / "raw" / "minimum")
    command_result = run_command(
        [
            "node",
            "scripts/pricing/wb_autoaction_min_price_apply.js",
            "--mode",
            "download",
            "--out-dir",
            str(out_dir),
        ]
    )
    workbook = out_dir / "fresh_source.xlsx"
    if not workbook.exists():
        raise RuntimeError("fresh WB minimum-price workbook was not created")
    expected_plan = {
        "rows": [
            {
                "nm_id": int(row["nm_id"]),
                "target_minimum": int(as_decimal(row["minimum"])),
            }
            for row in expected_products
        ]
    }
    verify = verify_minimum_targets(expected_plan, workbook)
    verify["expected_source"] = "owner_approved_report"
    verify["source_xlsx"] = str(workbook)
    write_json(run_dir / "processed" / "minimum_verify.json", verify)
    write_json(
        run_dir / "raw" / "minimum_download_result.json",
        {
            "returncode": command_result["returncode"],
            "source_xlsx": str(workbook),
        },
    )
    if verify["status"] != "ok":
        raise RuntimeError("fresh WB minimum-price verification failed")
    return verify


def download_action_snapshot(run_dir: Path, label: str) -> tuple[Path, dict[str, Any]]:
    raw_dir = ensure_dir(run_dir / "raw" / label)
    prices_dir = ensure_dir(run_dir / "raw" / f"{label}_prices")
    command_result = run_command(
        [
            "node",
            "scripts/actions/wb_download_active_actions.js",
            "--date",
            datetime.now().strftime("%Y-%m-%d"),
            "--out-dir",
            str(raw_dir),
            "--prices-dir",
            str(prices_dir),
        ]
    )
    snapshot_path = raw_dir / "cabinet-actions-snapshot.json"
    if not snapshot_path.exists():
        raise RuntimeError("fresh WB action snapshot was not created")
    snapshot = read_json(snapshot_path)
    write_json(
        raw_dir / "command_result.json",
        {"returncode": command_result["returncode"], "snapshot": str(snapshot_path)},
    )
    return snapshot_path, snapshot


def build_fresh_report(
    *,
    snapshot_path: Path,
    snapshot: dict[str, Any],
    price_plan: dict[str, Any],
    outside_discount: int,
) -> dict[str, Any]:
    scope_nm_ids = {int(row["nm_id"]) for row in price_plan.get("rows", [])}
    promos, promo_rows = read_promo_rows(
        snapshot_path,
        scope_nm_ids=scope_nm_ids,
    )
    products, candidates, summary = build_plan(
        snapshot=snapshot,
        prices=snapshot["prices"],
        price_plan=price_plan,
        promo_rows_by_nm=promo_rows,
        outside_discount=outside_discount,
    )
    return {
        "snapshot_checked_at": snapshot["checkedAt"],
        "summary": summary,
        "promos": list(promos.values()),
        "products": products,
        "candidates": candidates,
    }


def current_price_verify(
    *,
    credentials: Any,
    products: list[dict[str, Any]],
    run_dir: Path,
) -> dict[str, Any]:
    payload = target_payload(products, changes_only=False)
    rows = WbPricesAdapter(credentials.wb).fetch_goods_prices(limit=1000)
    current = {
        int(row["nmID"]): row
        for row in rows
        if row.get("nmID") is not None
    }
    verify = _verify_current_discount_payload(
        target_payload=payload,
        current_rows=current,
    )
    write_json(run_dir / "raw" / "current_prices_after.json", rows)
    write_json(run_dir / "processed" / "price_verify.json", verify)
    return verify


def verify_action_participation(
    *,
    run_dir: Path,
    selected_products: list[dict[str, Any]],
    attempts: int,
    delay: int,
) -> dict[str, Any]:
    history: list[dict[str, Any]] = []
    selected = {
        (int(row["nm_id"]), int(row["chosen_action_id"]))
        for row in selected_products
    }
    for attempt in range(1, attempts + 1):
        snapshot_path, snapshot = download_action_snapshot(
            run_dir, f"action_verify_{attempt:02d}"
        )
        _, promo_rows = read_promo_rows(
            snapshot_path,
            scope_nm_ids={nm_id for nm_id, _action_id in selected},
        )
        confirmed: set[tuple[int, int]] = set()
        observed: list[dict[str, Any]] = []
        for nm_id, action_id in sorted(selected):
            matching = [
                row
                for row in promo_rows.get(nm_id, [])
                if int(row["action_id"]) == action_id
            ]
            participates = any(row["currently_participates"] for row in matching)
            if participates:
                confirmed.add((nm_id, action_id))
            observed.append(
                {
                    "nm_id": nm_id,
                    "action_id": action_id,
                    "participates": participates,
                    "statuses": [row["status"] for row in matching],
                }
            )
        entry = {
            "attempt": attempt,
            "checked_at": snapshot.get("checkedAt", ""),
            "confirmed": len(confirmed),
            "target": len(selected),
            "rows": observed,
        }
        history.append(entry)
        if confirmed == selected:
            break
        if attempt < attempts:
            time.sleep(delay)
    result = {
        "status": "ok" if history and history[-1]["confirmed"] == len(selected) else "pending",
        "confirmed": history[-1]["confirmed"] if history else 0,
        "target": len(selected),
        "history": history,
    }
    write_json(run_dir / "processed" / "action_participation_verify.json", result)
    return result


def write_report(path: Path, result: dict[str, Any]) -> None:
    lines = [
        "# WB best-price action apply",
        "",
        f"- Run ID: `{result['run_id']}`.",
        f"- Status: `{result['overall_status']}`.",
        f"- Approved report: `{result['approved']['run_id']}`.",
        f"- Approved SHA-256: `{result['approved']['sha256']}`.",
        f"- Fresh plan: `{result['fresh']['snapshot_checked_at']}`.",
        f"- Minimum prices verified: `{result['minimum_verify']['verified']}` / "
        f"`{result['minimum_verify']['target']}`.",
        f"- Uploaded rows: `{result['apply']['submitted_rows']}`.",
        f"- Upload ID: `{result['apply']['upload_id']}`.",
        f"- Upload verify: `{result['apply']['upload_verify_status']}`.",
        f"- Price verify: `{result['price_verify']['matched_rows']}` / "
        f"`{result['price_verify']['expected_rows']}`.",
        f"- Action participation verify: `{result['action_verify']['confirmed']}` / "
        f"`{result['action_verify']['target']}`.",
        "- Other WB prices, minimum prices and marketplace data were not changed.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = parse_args()
    if not args.confirmed_by_user:
        raise RuntimeError("explicit owner confirmation is required")
    approved_path = args.approved_report.resolve()
    actual_approved_sha = sha256(approved_path)
    if actual_approved_sha != args.approved_sha256:
        raise RuntimeError(
            f"approved report checksum mismatch: {actual_approved_sha}"
        )
    if price_plan_sha256(args.price_plan.resolve()) != args.price_plan_sha256:
        raise RuntimeError("WB price-grid plan checksum mismatch")

    approved = read_json(approved_path)
    if approved.get("schema") != "wb_best_price_action_plan.v2":
        raise RuntimeError("unsupported approved report schema")
    if int(approved.get("outside_action_discount")) != int(args.outside_discount):
        raise RuntimeError("outside-action discount differs from approved report")
    if int(approved.get("summary", {}).get("target_below_minimum") or 0):
        raise RuntimeError("approved report contains target prices below minimum")
    if int(approved.get("summary", {}).get("unsafe_single_upload") or 0):
        raise RuntimeError("approved report contains rows unsafe for a single WB upload")

    credentials = load_credentials()
    if credentials.wb is None:
        raise RuntimeError("WB credentials are not configured")

    started = datetime.now().astimezone()
    run_id = args.run_id or f"wb_best_price_action_apply_{started.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(
        args.data_dir / "runs" / started.strftime("%Y-%m-%d") / run_id
    )
    ensure_dir(run_dir / "raw")
    ensure_dir(run_dir / "processed")
    approved_id = f"{approved['run_id']}:{actual_approved_sha}"
    write_json(run_dir / "processed" / "approved_report.json", approved)

    preflight = run_status_preflight(
        credentials=credentials,
        data_dir=args.data_dir,
        include_lk=False,
        marketplaces=("wb",),
        include_ozon_performance=False,
    )
    write_json(run_dir / "processed" / "preflight.json", preflight)
    if preflight["overall_status"] != "ok":
        raise RuntimeError(f"WB preflight failed: {preflight['overall_status']}")

    reference_price_plan = validate_plan_hash(
        args.price_plan.resolve(), args.price_plan_sha256
    )
    minimum_verify = fresh_minimum_verify(
        run_dir=run_dir,
        expected_products=approved["products"],
    )
    price_plan, minimum_snapshot = price_plan_with_fresh_minimums(
        reference_price_plan,
        Path(str(minimum_verify["source_xlsx"])),
    )
    write_json(run_dir / "processed" / "effective_price_plan.json", json_ready(price_plan))
    write_json(run_dir / "processed" / "minimum_snapshot.json", minimum_snapshot)
    fresh_snapshot_path, fresh_snapshot = download_action_snapshot(
        run_dir, "fresh_plan"
    )
    fresh = build_fresh_report(
        snapshot_path=fresh_snapshot_path,
        snapshot=fresh_snapshot,
        price_plan=price_plan,
        outside_discount=args.outside_discount,
    )
    if int(fresh["summary"].get("target_below_minimum") or 0):
        raise RuntimeError("fresh report contains target prices below minimum")
    if int(fresh["summary"].get("unsafe_single_upload") or 0):
        raise RuntimeError("fresh report contains rows unsafe for a single WB upload")
    write_json(run_dir / "processed" / "fresh_report.json", json_ready(fresh))
    drift = plan_drift(approved["products"], fresh["products"])
    write_json(run_dir / "processed" / "drift_check.json", drift)
    if drift["status"] != "ok":
        raise RuntimeError(
            f"fresh WB best-price plan drifted on {drift['drift_rows_count']} products"
        )

    payload = target_payload(fresh["products"], changes_only=True)
    if len(payload["data"]) != int(approved["summary"]["to_change_discount"]):
        raise RuntimeError("fresh write payload row count differs from approved report")
    write_json(run_dir / "processed" / "upload_payload.json", payload)
    assert_apply_not_repeated(
        data_dir=args.data_dir,
        approved_id=approved_id,
        current_run_id=run_id,
    )

    upload = _wb_upload_and_verify(
        payload=payload,
        token=credentials.wb.token,
        raw_dir=run_dir / "raw",
        label="best_price",
    )
    upload_status = str(upload["verify_status"])
    if upload_status not in {"ok", "submitted"}:
        overall_status = "blocked"
        action_verify = {
            "status": "skipped",
            "confirmed": 0,
            "target": len(payload["data"]),
        }
        price_verify = {
            "status": "skipped",
            "matched_rows": 0,
            "expected_rows": len(fresh["products"]),
        }
    else:
        price_verify = current_price_verify(
            credentials=credentials,
            products=fresh["products"],
            run_dir=run_dir,
        )
        selected = [
            row for row in fresh["products"] if row.get("chosen_action_id")
        ]
        action_verify = verify_action_participation(
            run_dir=run_dir,
            selected_products=selected,
            attempts=args.action_verify_attempts,
            delay=args.action_verify_delay,
        )
        if price_verify["status"] == "ok" and action_verify["status"] == "ok":
            overall_status = "ok"
        elif price_verify["status"] in {"ok", "warning"}:
            overall_status = "warning"
        else:
            overall_status = "blocked"

    result = {
        "run_id": run_id,
        "started_at": started.isoformat(),
        "finished_at": datetime.now().astimezone().isoformat(),
        "mode": "apply",
        "overall_status": overall_status,
        "approved_id": approved_id,
        "approved": {
            "run_id": approved["run_id"],
            "sha256": actual_approved_sha,
            "report": str(approved_path),
        },
        "preflight": {
            "run_id": preflight["run_id"],
            "overall_status": preflight["overall_status"],
        },
        "minimum_verify": minimum_verify,
        "fresh": {
            "snapshot_checked_at": fresh["snapshot_checked_at"],
            "summary": fresh["summary"],
        },
        "drift": drift,
        "apply": {
            "submitted_rows": len(payload["data"]),
            "upload_id": upload.get("upload_id"),
            "upload_verify_status": upload_status,
            "success_rows": upload.get("success_rows"),
            "failed_rows": upload.get("failed_rows"),
            "error_summary": upload.get("error_summary"),
        },
        "price_verify": {
            key: value
            for key, value in price_verify.items()
            if key != "rows"
        },
        "action_verify": action_verify,
        "artifacts": {
            "run_dir": str(run_dir),
            "summary": str(run_dir / "summary.json"),
            "report": str(run_dir / "report.md"),
            "approved_report": str(run_dir / "processed" / "approved_report.json"),
            "fresh_report": str(run_dir / "processed" / "fresh_report.json"),
            "minimum_verify": str(run_dir / "processed" / "minimum_verify.json"),
            "drift_check": str(run_dir / "processed" / "drift_check.json"),
            "upload_payload": str(run_dir / "processed" / "upload_payload.json"),
            "price_verify": str(run_dir / "processed" / "price_verify.json"),
            "action_verify": str(
                run_dir / "processed" / "action_participation_verify.json"
            ),
            "run_manifest": str(run_dir / "manifest.json"),
            "apply_marker": str(
                apply_marker_for(data_dir=args.data_dir, approved_id=approved_id)
            ),
        },
    }
    write_json(run_dir / "summary.json", json_ready(result))
    write_report(run_dir / "report.md", result)
    manifest_paths = write_summary_run_manifest(
        data_dir=args.data_dir,
        run_dir=run_dir,
        summary=result,
        task="wb-best-price-action-apply",
        mode="apply",
        risk="high",
        marketplaces=["wb"],
        inputs={
            "approved_report": str(approved_path),
            "approved_sha256": actual_approved_sha,
            "outside_discount": args.outside_discount,
            "confirmed_by_user": True,
        },
        source_run_ids=[approved["run_id"], preflight["run_id"]],
        approved_id=approved_id,
        lifecycle_status=(
            "verified" if overall_status == "ok" else "needs_attention"
        ),
        closed=overall_status == "ok",
    )
    if upload.get("upload_id"):
        mark_approved_applied(
            data_dir=args.data_dir,
            approved_id=approved_id,
            apply_run_id=run_id,
            task="wb-best-price-action-apply",
            status=overall_status,
            run_manifest_path=manifest_paths["manifest"],
            checksum=canonical_checksum(
                {
                    "approved_sha256": actual_approved_sha,
                    "payload": payload,
                }
            ),
        )
    print(json.dumps(json_ready(result), ensure_ascii=False, indent=2))
    if overall_status == "blocked":
        raise SystemExit(3)


if __name__ == "__main__":
    main()
