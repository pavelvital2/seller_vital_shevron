from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Any

from takterra_agent.config import AppCredentials
from takterra_agent.marketplaces.ozon.performance_adapter import OzonPerformanceAdapter
from takterra_agent.reports.writer import ensure_dir, write_json
from takterra_agent.tasks.ozon_cpc_optimization_plan import run_ozon_cpc_optimization_plan
from takterra_agent.tasks.status_preflight import run_status_preflight


MICRO_RUB = Decimal("1000000")


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    headers = list(rows[0].keys()) if rows else ["sku"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(" ", "").replace("\u00a0", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


def _round2(value: Decimal | None) -> str:
    if value is None:
        return ""
    return str(value.quantize(Decimal("0.01")))


def _money_to_raw_bid(value: Decimal) -> str:
    return str(int((value * MICRO_RUB).to_integral_value()))


def _raw_bid_to_money(value: Any) -> Decimal | None:
    raw = _decimal(value)
    if raw is None:
        return None
    return (raw / MICRO_RUB).quantize(Decimal("0.01"))


def _latest_plan_dir(data_dir: Path) -> Path | None:
    runs_dir = data_dir / "runs"
    if not runs_dir.exists():
        return None
    candidates = [
        path
        for path in runs_dir.glob("*/*")
        if path.is_dir()
        and path.name.startswith("ozon_cpc_optimization_plan_")
        and (path / "summary.json").exists()
        and (path / "ozon_cpc_bid_changes.csv").exists()
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True)[0]


def _plan_dir(data_dir: Path, plan_run_id: str | None) -> Path:
    if plan_run_id:
        for path in (data_dir / "runs").glob(f"*/{plan_run_id}"):
            if path.is_dir():
                return path
        raise RuntimeError(f"Ozon CPC optimization plan run not found: {plan_run_id}")

    latest = _latest_plan_dir(data_dir)
    if latest is None:
        raise RuntimeError("Ozon CPC optimization plan run not found")
    return latest


def _current_bid_rows(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for product in products:
        sku = str(product.get("sku") or "").strip()
        if not sku or sku in seen:
            continue
        seen.add(sku)
        current_bid = _raw_bid_to_money(product.get("bid"))
        rows.append(
            {
                "sku": sku,
                "title": str(product.get("title") or ""),
                "current_bid": _round2(current_bid),
                "raw_bid": str(product.get("bid") or ""),
                "targetCir": product.get("targetCir"),
            }
        )
    return rows


def _current_bid_map(rows: list[dict[str, Any]]) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    for row in rows:
        bid = _decimal(row.get("current_bid"))
        sku = str(row.get("sku") or "").strip()
        if sku and bid is not None:
            result[sku] = bid
    return result


def _action_signature(row: dict[str, str]) -> tuple[str, str, str, str, str]:
    return (
        str(row.get("campaign_id") or ""),
        str(row.get("sku") or ""),
        str(row.get("recommended_action") or ""),
        str(row.get("current_bid") or ""),
        str(row.get("target_bid") or ""),
    )


def _assert_no_drift(
    *,
    approved_rows: list[dict[str, str]],
    fresh_rows: list[dict[str, str]],
) -> dict[str, Any]:
    approved_set = {_action_signature(row) for row in approved_rows}
    fresh_set = {_action_signature(row) for row in fresh_rows}
    drift = {
        "approved_action_rows": len(approved_set),
        "fresh_action_rows": len(fresh_set),
        "added": sorted(fresh_set - approved_set),
        "removed": sorted(approved_set - fresh_set),
    }
    if drift["added"] or drift["removed"]:
        raise RuntimeError("Ozon CPC bid drift-check failed")
    return drift


def _split_apply_rows(
    rows: list[dict[str, str]],
    *,
    min_bid: Decimal,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    apply_rows: list[dict[str, Any]] = []
    skipped_rows: list[dict[str, Any]] = []
    for row in rows:
        current_bid = _decimal(row.get("current_bid"))
        target_bid = _decimal(row.get("target_bid"))
        output = dict(row)
        if current_bid is None or target_bid is None:
            output["skip_reason"] = "missing current_bid or target_bid"
            skipped_rows.append(output)
            continue
        if row.get("bid_reference_type") != "current_bid_api":
            output["skip_reason"] = "current bid was not loaded from API"
            skipped_rows.append(output)
            continue
        if target_bid <= 0:
            output["skip_reason"] = "zero target requires separate product disable operation"
            skipped_rows.append(output)
            continue
        if target_bid < min_bid:
            output["skip_reason"] = f"target_bid below safe minimum {min_bid}"
            skipped_rows.append(output)
            continue
        if target_bid == current_bid:
            output["skip_reason"] = "target_bid equals current_bid"
            skipped_rows.append(output)
            continue
        output["raw_target_bid"] = _money_to_raw_bid(target_bid)
        output["raw_current_bid"] = _money_to_raw_bid(current_bid)
        apply_rows.append(output)
    return apply_rows, skipped_rows


def _verify_applied_rows(
    *,
    current_bids: dict[str, Decimal],
    apply_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    mismatches: list[dict[str, Any]] = []
    for row in apply_rows:
        sku = str(row.get("sku") or "")
        expected = _decimal(row.get("target_bid"))
        actual = current_bids.get(sku)
        if expected != actual:
            mismatches.append({"sku": sku, "expected": _round2(expected), "actual": _round2(actual)})
    return {
        "status": "ok" if not mismatches else "error",
        "checked_rows": len(apply_rows),
        "mismatches": mismatches,
    }


def _sum_field(rows: list[dict[str, Any]], field: str) -> str:
    total = Decimal("0")
    for row in rows:
        value = _decimal(row.get(field))
        if value is not None:
            total += value
    return _round2(total)


def _write_report(path: Path, result: dict[str, Any]) -> None:
    lines = [
        "# Ozon CPC Bid Apply Result",
        "",
        f"Run ID: `{result['run_id']}`",
        f"Started at: `{result['started_at']}`",
        f"Overall status: `{result['overall_status']}`",
        f"Approved plan: `{result['approved_plan_run_id']}`",
        f"Fresh plan: `{result['fresh_plan']['run_id']}`",
        "",
        "## Summary",
        "",
    ]
    for key, value in result["summary"].items():
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "## Verify", ""])
    lines.append(f"- `status`: {result['verify']['status']}")
    lines.append(f"- `checked_rows`: {result['verify']['checked_rows']}")
    lines.append(f"- `mismatches`: {len(result['verify']['mismatches'])}")
    lines.extend(["", "## Artifacts", ""])
    for key, value in sorted(result["artifacts"].items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def run_ozon_cpc_bids_apply(
    *,
    credentials: AppCredentials,
    data_dir: Path = Path("data"),
    plan_run_id: str | None = None,
    run_id: str | None = None,
    confirmed_by_user: bool = False,
    min_bid: Decimal = Decimal("1.00"),
) -> dict[str, Any]:
    if not confirmed_by_user:
        raise RuntimeError("explicit owner confirmation is required")
    if not credentials.ozon_performance:
        raise RuntimeError("missing Ozon Performance API credentials")

    approved_plan_dir = _plan_dir(data_dir, plan_run_id)
    approved_summary = json.loads((approved_plan_dir / "summary.json").read_text(encoding="utf-8"))
    approved_rows = _read_csv(approved_plan_dir / "ozon_cpc_bid_changes.csv")

    started_at = datetime.now()
    run_id = run_id or f"ozon_cpc_bids_apply_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    raw_dir = ensure_dir(run_dir / "raw")
    processed_dir = ensure_dir(run_dir / "processed")

    preflight = run_status_preflight(credentials=credentials, data_dir=data_dir, include_lk=False)
    if preflight["overall_status"] not in {"ok", "warning"}:
        raise RuntimeError(f"preflight is not ok: {preflight['overall_status']}")

    campaign_ids = sorted({str(row.get("campaign_id") or "") for row in approved_rows if row.get("campaign_id")})
    if len(campaign_ids) != 1:
        raise RuntimeError(f"expected exactly one campaign_id, got {campaign_ids}")
    campaign_id = campaign_ids[0]

    ozon = OzonPerformanceAdapter(credentials.ozon_performance)
    fresh_products = ozon.fetch_campaign_products(campaign_id)
    write_json(raw_dir / "current_products_before_apply.json", fresh_products)
    fresh_current_rows = _current_bid_rows(fresh_products)
    fresh_current_bids_path = processed_dir / "current_bids_before_apply.json"
    write_json(fresh_current_bids_path, fresh_current_rows)

    fresh_plan = run_ozon_cpc_optimization_plan(
        data_dir=data_dir,
        source_run_id=str(approved_summary.get("source_run_id") or ""),
        current_bids_json=fresh_current_bids_path,
    )
    fresh_rows = _read_csv(Path(fresh_plan["artifacts"]["bid_changes_csv"]))
    drift = _assert_no_drift(approved_rows=approved_rows, fresh_rows=fresh_rows)
    write_json(processed_dir / "drift_check.json", drift)

    apply_rows, skipped_rows = _split_apply_rows(fresh_rows, min_bid=min_bid)
    write_json(processed_dir / "apply_rows.json", apply_rows)
    write_json(processed_dir / "skipped_rows.json", skipped_rows)
    _write_csv(apply_rows, processed_dir / "apply_rows.csv")
    _write_csv(skipped_rows, processed_dir / "skipped_rows.csv")

    update_response: Any = None
    if apply_rows:
        update_response = ozon.update_campaign_product_bids(
            campaign_id,
            [{"sku": str(row["sku"]), "bid": str(row["raw_target_bid"])} for row in apply_rows],
        )
    write_json(raw_dir / "update_response.json", update_response)

    products_after = ozon.fetch_campaign_products(campaign_id)
    write_json(raw_dir / "current_products_after_apply.json", products_after)
    current_after_rows = _current_bid_rows(products_after)
    write_json(processed_dir / "current_bids_after_apply.json", current_after_rows)
    verify = _verify_applied_rows(current_bids=_current_bid_map(current_after_rows), apply_rows=apply_rows)

    summary = {
        "campaign_id": campaign_id,
        "approved_rows": len(approved_rows),
        "applied_rows": len(apply_rows),
        "skipped_rows": len(skipped_rows),
        "current_bid_sum_applied": _sum_field(apply_rows, "current_bid"),
        "target_bid_sum_applied": _sum_field(apply_rows, "target_bid"),
        "bid_change_sum_applied": _sum_field(apply_rows, "bid_change_amount"),
        "min_bid_floor": _round2(min_bid),
    }
    overall_status = "ok" if verify["status"] == "ok" else "warning"
    artifacts = {
        "run_dir": str(run_dir),
        "summary": str(run_dir / "summary.json"),
        "report": str(run_dir / "ozon_cpc_bids_apply_result.md"),
        "drift_check": str(processed_dir / "drift_check.json"),
        "apply_rows": str(processed_dir / "apply_rows.csv"),
        "skipped_rows": str(processed_dir / "skipped_rows.csv"),
        "current_bids_before": str(fresh_current_bids_path),
        "current_bids_after": str(processed_dir / "current_bids_after_apply.json"),
        "update_response": str(raw_dir / "update_response.json"),
    }
    result = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "apply",
        "overall_status": overall_status,
        "approved_plan_run_id": approved_plan_dir.name,
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
        "summary": summary,
        "update_response": update_response,
        "verify": verify,
        "artifacts": artifacts,
    }
    write_json(run_dir / "summary.json", result)
    _write_report(run_dir / "ozon_cpc_bids_apply_result.md", result)
    return result
