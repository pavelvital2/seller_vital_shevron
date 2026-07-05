from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
import time
from typing import Any

from seller_agent.config import AppCredentials
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.marketplaces.wb.promotion_adapter import WbPromotionAdapter
from seller_agent.reports.writer import ensure_dir, write_json
from seller_agent.safety.approvals import (
    apply_marker_for,
    assert_apply_not_repeated,
    canonical_checksum,
    mark_approved_applied,
)
from seller_agent.tasks.card_content_signals import run_collect_card_signals
from seller_agent.tasks.status_preflight import run_status_preflight
from seller_agent.tasks.wb_promotion_bid_parser_enriched_plan import run_wb_promotion_bid_parser_enriched_plan
from seller_agent.tasks.wb_promotion_bid_plan import WbPromotionBidThresholds, run_wb_promotion_bid_plan
from seller_agent.tasks.wb_promotion_bids_apply import (
    _decimal,
    _group_bid_payload,
    _read_csv,
    _round2,
    _split_apply_rows,
    _sum_field,
    _thresholds_from_summary,
    _verify_applied_rows,
    _write_csv,
)
from seller_agent.tasks.wb_promotion_report import run_wb_promotion_report


def _latest_enriched_plan_dir(data_dir: Path) -> Path | None:
    runs_dir = data_dir / "runs"
    if not runs_dir.exists():
        return None
    candidates = [
        path
        for path in runs_dir.glob("*/*")
        if path.is_dir()
        and path.name.startswith("wb_promotion_bid_parser_enriched_plan_")
        and (path / "summary.json").exists()
        and (path / "wb_promotion_bid_parser_enriched_apply_preview.csv").exists()
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True)[0]


def _enriched_plan_dir(data_dir: Path, plan_run_id: str | None) -> Path:
    if plan_run_id:
        for path in (data_dir / "runs").glob(f"*/{plan_run_id}"):
            if path.is_dir():
                return path
        raise RuntimeError(f"WB parser-enriched promotion bid plan run not found: {plan_run_id}")

    latest = _latest_enriched_plan_dir(data_dir)
    if latest is None:
        raise RuntimeError("WB parser-enriched promotion bid plan run not found")
    return latest


def _enriched_apply_signature(row: dict[str, Any]) -> tuple[str, str, str, str, str, str]:
    return (
        str(row.get("advert_id") or ""),
        str(row.get("nm_id") or ""),
        str(row.get("current_bid_place") or ""),
        str(row.get("current_bid") or ""),
        str(row.get("final_target_bid") or row.get("target_bid") or ""),
        str(row.get("parser_enriched_action") or row.get("recommended_action") or ""),
    )


def _enriched_apply_identity(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("advert_id") or ""),
        str(row.get("nm_id") or ""),
        str(row.get("current_bid_place") or ""),
    )


def _apply_preview_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [row for row in rows if row.get("parser_enriched_action") == "apply_ready"]


def _partial_drift_rows(
    *,
    approved_rows: list[dict[str, str]],
    fresh_rows: list[dict[str, str]],
) -> dict[str, Any]:
    approved_apply = _apply_preview_rows(approved_rows)
    fresh_apply = _apply_preview_rows(fresh_rows)
    fresh_by_signature = {_enriched_apply_signature(row): row for row in fresh_apply}
    approved_identities = {_enriched_apply_identity(row) for row in approved_apply}

    unchanged_rows: list[dict[str, str]] = []
    drift_rows: list[dict[str, Any]] = []
    for row in approved_apply:
        signature = _enriched_apply_signature(row)
        fresh_row = fresh_by_signature.get(signature)
        if fresh_row:
            unchanged_rows.append(fresh_row)
            continue
        output = dict(row)
        output["skip_reason"] = "drifted_or_missing_in_fresh_plan"
        drift_rows.append(output)

    new_rows: list[dict[str, Any]] = []
    for row in fresh_apply:
        identity = _enriched_apply_identity(row)
        if identity in approved_identities:
            continue
        output = dict(row)
        output["skip_reason"] = "new_fresh_row_not_owner_approved"
        new_rows.append(output)

    return {
        "approved_apply_rows": len(approved_apply),
        "fresh_apply_rows": len(fresh_apply),
        "unchanged_rows": unchanged_rows,
        "drift_rows": drift_rows,
        "new_rows": new_rows,
    }


def _map_enriched_row_for_apply(row: dict[str, str]) -> dict[str, str]:
    output = dict(row)
    output["recommended_action"] = row.get("parser_enriched_action", "")
    output["target_bid"] = row.get("final_target_bid", "")
    output["bid_change_amount"] = row.get("final_bid_change_amount", "")
    return output


def _enriched_thresholds(summary: dict[str, Any]) -> tuple[int, Decimal, Decimal]:
    values = summary.get("summary", {})
    min_stock = int(values.get("min_stock") or 4)
    parser_test_increase_percent = _decimal(values.get("parser_test_increase_percent")) or Decimal("10")
    min_bid = _decimal(values.get("min_bid")) or Decimal("1.00")
    return min_stock, parser_test_increase_percent, min_bid


def _base_thresholds_from_enriched_summary(summary: dict[str, Any]) -> WbPromotionBidThresholds:
    source_base_plan_csv = str(summary.get("artifacts", {}).get("source_base_plan_csv") or "")
    if source_base_plan_csv:
        base_summary = Path(source_base_plan_csv).parent / "summary.json"
        if base_summary.exists():
            return _thresholds_from_summary(json.loads(base_summary.read_text(encoding="utf-8")))
    return WbPromotionBidThresholds()


def _write_report(path: Path, result: dict[str, Any]) -> None:
    lines = [
        "# WB Promotion Parser-Enriched Bid Apply Result",
        "",
        f"Run ID: `{result['run_id']}`",
        f"Started at: `{result['started_at']}`",
        f"Overall status: `{result['overall_status']}`",
        f"Approved enriched plan: `{result['approved_plan_run_id']}`",
        f"Fresh report: `{result['fresh_report']['run_id']}`",
        f"Fresh signal run: `{result['fresh_signals']['run_id']}`",
        f"Fresh base plan: `{result['fresh_base_plan']['run_id']}`",
        f"Fresh enriched plan: `{result['fresh_enriched_plan']['run_id']}`",
        "",
        "## Summary",
        "",
    ]
    for key, value in result["summary"].items():
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "## Safety", ""])
    lines.append("- Only owner-approved `apply_ready` rows are eligible for write.")
    lines.append("- Rows changed in the fresh parser-enriched plan are skipped, not auto-applied.")
    lines.append("- Sales signals are collected from WB Statistics API; parser is visibility context only.")
    lines.extend(["", "## Verify", ""])
    lines.append(f"- `status`: {result['verify']['status']}")
    lines.append(f"- `checked_rows`: {result['verify']['checked_rows']}")
    lines.append(f"- `mismatches`: {len(result['verify']['mismatches'])}")
    lines.extend(["", "## Artifacts", ""])
    for key, value in sorted(result["artifacts"].items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def run_wb_promotion_bid_parser_enriched_apply(
    *,
    credentials: AppCredentials,
    data_dir: Path = Path("data"),
    plan_run_id: str | None = None,
    run_id: str | None = None,
    confirmed_by_user: bool = False,
    wait_seconds: int = 45,
) -> dict[str, Any]:
    if not confirmed_by_user:
        raise RuntimeError("explicit owner confirmation is required")
    if not credentials.wb:
        raise RuntimeError("missing WB API token")

    approved_plan_dir = _enriched_plan_dir(data_dir, plan_run_id)
    approved_id = approved_plan_dir.name
    assert_apply_not_repeated(data_dir=data_dir, approved_id=approved_id)
    approved_summary = json.loads((approved_plan_dir / "summary.json").read_text(encoding="utf-8"))
    approved_rows = _read_csv(approved_plan_dir / "wb_promotion_bid_parser_enriched_apply_preview.csv")
    min_stock, parser_test_increase_percent, min_bid = _enriched_thresholds(approved_summary)
    base_thresholds = _base_thresholds_from_enriched_summary(approved_summary)

    started_at = datetime.now()
    run_id = run_id or f"wb_promotion_bid_parser_enriched_apply_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    raw_dir = ensure_dir(run_dir / "raw")
    processed_dir = ensure_dir(run_dir / "processed")

    preflight = run_status_preflight(credentials=credentials, data_dir=data_dir, include_lk=False)
    if preflight["overall_status"] not in {"ok", "warning"}:
        raise RuntimeError(f"preflight is not ok: {preflight['overall_status']}")

    fresh_report = run_wb_promotion_report(credentials=credentials, data_dir=data_dir, payment_type="cpc")
    fresh_signals = run_collect_card_signals(
        credentials=credentials,
        data_dir=data_dir,
        marketplace="wb",
        period_days=30,
        skip_api=False,
        parser_source="latest",
    )
    if fresh_signals["overall_status"] == "error":
        raise RuntimeError("fresh WB signal collection failed")
    signal_sources = fresh_signals.get("sources", {})
    if "wb_sales" not in signal_sources or "wb_stocks" not in signal_sources:
        raise RuntimeError("fresh WB sales/stocks signals from WB API are required")

    fresh_base_plan = run_wb_promotion_bid_plan(
        data_dir=data_dir,
        source_run_id=fresh_report["run_id"],
        thresholds=base_thresholds,
        active_cpc_only=True,
    )
    fresh_enriched_plan = run_wb_promotion_bid_parser_enriched_plan(
        data_dir=data_dir,
        base_plan_run_id=fresh_base_plan["run_id"],
        min_stock=min_stock,
        parser_test_increase_percent=parser_test_increase_percent,
        min_bid=min_bid,
    )

    fresh_rows = _read_csv(Path(fresh_enriched_plan["artifacts"]["apply_preview_csv"]))
    drift = _partial_drift_rows(approved_rows=approved_rows, fresh_rows=fresh_rows)
    unchanged_rows = drift["unchanged_rows"]
    drift_rows = drift["drift_rows"]
    new_rows = drift["new_rows"]
    mapped_rows = [_map_enriched_row_for_apply(row) for row in unchanged_rows]
    apply_rows, split_skipped_rows = _split_apply_rows(
        mapped_rows,
        allowed_actions={"apply_ready"},
        min_bid=min_bid,
    )
    skipped_rows = [*drift_rows, *new_rows, *split_skipped_rows]
    payload_bids = _group_bid_payload(apply_rows)

    write_json(processed_dir / "drift_check.json", {key: value for key, value in drift.items() if not key.endswith("_rows")})
    _write_csv(approved_rows, processed_dir / "approved_apply_preview.csv")
    _write_csv(fresh_rows, processed_dir / "fresh_apply_preview.csv")
    _write_csv(unchanged_rows, processed_dir / "unchanged_rows.csv")
    _write_csv(drift_rows, processed_dir / "drift_rows.csv")
    _write_csv(new_rows, processed_dir / "new_rows.csv")
    _write_csv(apply_rows, processed_dir / "apply_rows.csv")
    _write_csv(skipped_rows, processed_dir / "skipped_rows.csv")
    write_json(processed_dir / "update_payload.json", {"bids": payload_bids})

    wb = WbPromotionAdapter(credentials.wb)
    update_response: Any = {}
    if payload_bids:
        update_response = wb.update_bids(payload_bids)
    write_json(raw_dir / "update_response.json", update_response)

    if wait_seconds > 0 and apply_rows:
        time.sleep(wait_seconds)

    advert_ids = sorted({int(str(row["advert_id"])) for row in apply_rows})
    campaigns_after = wb.fetch_campaigns(ids=advert_ids, statuses=[4, 9, 11], payment_type="cpc") if advert_ids else []
    write_json(raw_dir / "campaigns_after_apply.json", {"adverts": campaigns_after})
    verify = _verify_applied_rows(campaigns=campaigns_after, apply_rows=apply_rows)

    summary = {
        "approved_rows": len(approved_rows),
        "fresh_apply_ready_rows": len(fresh_rows),
        "unchanged_rows": len(unchanged_rows),
        "drift_rows": len(drift_rows),
        "new_rows_not_approved": len(new_rows),
        "applied_rows": len(apply_rows),
        "skipped_rows": len(skipped_rows),
        "current_bid_sum_applied": _sum_field(apply_rows, "current_bid"),
        "target_bid_sum_applied": _sum_field(apply_rows, "target_bid"),
        "bid_change_sum_applied": _sum_field(apply_rows, "bid_change_amount"),
        "wait_seconds": wait_seconds,
    }
    overall_status = "ok" if verify["status"] == "ok" and not skipped_rows else "warning"
    if verify["status"] != "ok":
        overall_status = "error"
    artifacts = {
        "run_dir": str(run_dir),
        "summary": str(run_dir / "summary.json"),
        "report": str(run_dir / "wb_promotion_bid_parser_enriched_apply_result.md"),
        "drift_check": str(processed_dir / "drift_check.json"),
        "approved_apply_preview": str(processed_dir / "approved_apply_preview.csv"),
        "fresh_apply_preview": str(processed_dir / "fresh_apply_preview.csv"),
        "unchanged_rows": str(processed_dir / "unchanged_rows.csv"),
        "drift_rows": str(processed_dir / "drift_rows.csv"),
        "new_rows": str(processed_dir / "new_rows.csv"),
        "apply_rows": str(processed_dir / "apply_rows.csv"),
        "skipped_rows": str(processed_dir / "skipped_rows.csv"),
        "update_payload": str(processed_dir / "update_payload.json"),
        "update_response": str(raw_dir / "update_response.json"),
        "campaigns_after_apply": str(raw_dir / "campaigns_after_apply.json"),
        "run_manifest": str(run_dir / "manifest.json"),
        "apply_marker": str(apply_marker_for(data_dir=data_dir, approved_id=approved_id)),
    }
    result = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "apply",
        "overall_status": overall_status,
        "approved_plan_run_id": approved_id,
        "approved_id": approved_id,
        "preflight": {
            "run_id": preflight["run_id"],
            "overall_status": preflight["overall_status"],
            "artifacts": preflight["artifacts"],
        },
        "fresh_report": {
            "run_id": fresh_report["run_id"],
            "summary": fresh_report["summary"],
            "artifacts": fresh_report["artifacts"],
        },
        "fresh_signals": {
            "run_id": fresh_signals["run_id"],
            "summary": fresh_signals["summary"],
            "sources": fresh_signals["sources"],
            "artifacts": fresh_signals["artifacts"],
        },
        "fresh_base_plan": {
            "run_id": fresh_base_plan["run_id"],
            "summary": fresh_base_plan["summary"],
            "artifacts": fresh_base_plan["artifacts"],
        },
        "fresh_enriched_plan": {
            "run_id": fresh_enriched_plan["run_id"],
            "summary": fresh_enriched_plan["summary"],
            "artifacts": fresh_enriched_plan["artifacts"],
        },
        "drift": {
            "approved_apply_rows": drift["approved_apply_rows"],
            "fresh_apply_rows": drift["fresh_apply_rows"],
            "unchanged_rows": len(unchanged_rows),
            "drift_rows": len(drift_rows),
            "new_rows": len(new_rows),
        },
        "summary": summary,
        "update_response": update_response,
        "verify": verify,
        "artifacts": artifacts,
    }
    write_json(run_dir / "summary.json", result)
    _write_report(run_dir / "wb_promotion_bid_parser_enriched_apply_result.md", result)
    manifest_paths = write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=result,
        task="wb-promotion-bids-parser-enriched-apply",
        mode="apply",
        risk="high",
        marketplaces=["wb"],
        inputs={
            "plan_run_id": plan_run_id,
            "confirmed_by_user": confirmed_by_user,
            "wait_seconds": wait_seconds,
        },
    )
    mark_approved_applied(
        data_dir=data_dir,
        approved_id=approved_id,
        apply_run_id=run_id,
        task="wb-promotion-bids-parser-enriched-apply",
        status=overall_status,
        run_manifest_path=manifest_paths["manifest"],
        checksum=canonical_checksum(
            {
                "approved_id": approved_id,
                "summary": summary,
                "drift": result["drift"],
            }
        ),
    )
    return result
