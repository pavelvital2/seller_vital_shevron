from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path
import time
from typing import Any

from seller_agent.config import AppCredentials
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.marketplaces.ozon.performance_adapter import OzonPerformanceAdapter
from seller_agent.marketplaces.wb.promotion_adapter import WbPromotionAdapter
from seller_agent.reports.writer import ensure_dir, write_json
from seller_agent.safety.approvals import action_rows_checksum, file_sha256
from seller_agent.tasks.wb_promotion_report import _flatten_campaign_ids


def _find_run_dir(data_dir: Path, run_id: str) -> Path:
    matches = sorted((data_dir / "runs").glob(f"*/{run_id}"))
    if not matches:
        raise FileNotFoundError(f"run not found: {run_id}")
    return matches[-1]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _active_ozon_campaigns(adapter: OzonPerformanceAdapter) -> list[dict[str, Any]]:
    payload = adapter.get("/api/client/campaign")
    campaigns = payload.get("list") if isinstance(payload, dict) else []
    return [
        row
        for row in campaigns or []
        if isinstance(row, dict)
        and row.get("PaymentType") == "CPC"
        and row.get("state") == "CAMPAIGN_STATE_RUNNING"
        and row.get("id")
    ]


def _active_wb_campaigns(adapter: WbPromotionAdapter) -> list[dict[str, Any]]:
    campaign_ids, _ = _flatten_campaign_ids(adapter.fetch_campaign_count())
    return [
        row
        for row in adapter.fetch_campaigns(ids=campaign_ids, payment_type="cpc")
        if isinstance(row, dict) and int(row.get("status") or 0) == 9
    ]


def _ozon_campaign_snapshot(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("sku") or ""): {
            "bid": str(row.get("bid") or ""),
            "targetCir": row.get("targetCir"),
        }
        for row in rows
        if str(row.get("sku") or "")
    }


def _wb_campaign_snapshot(campaign: dict[str, Any]) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    for row in campaign.get("nm_settings") or []:
        if not isinstance(row, dict):
            continue
        nm_id = str(row.get("nm_id") or "")
        if not nm_id:
            continue
        bids = row.get("bids_kopecks") if isinstance(row.get("bids_kopecks"), dict) else {}
        snapshot[nm_id] = {
            "search_bid_kopecks": int(bids.get("search") or 0),
            "recommendation_bid_kopecks": int(bids.get("recommendations") or 0),
        }
    return snapshot


def _source_targets(source_actions: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[int, dict[str, Any]]]:
    ozon: dict[str, dict[str, Any]] = {}
    wb: dict[int, dict[str, Any]] = {}
    for row in source_actions:
        marketplace = str(row.get("marketplace") or "")
        if marketplace == "ozon":
            sku = str(row.get("ozon_sku") or "").strip()
            product_id = str(row.get("product_id") or "").strip()
            if not sku or not product_id:
                raise RuntimeError("Ozon stop row requires distinct product_id and ozon_sku")
            ozon[sku] = row
        elif marketplace == "wb":
            nm_id = int(row.get("product_id") or 0)
            if not nm_id:
                raise RuntimeError("WB stop row requires nmID in product_id")
            wb[nm_id] = row
        else:
            raise RuntimeError(f"unsupported liquidation stop marketplace: {marketplace}")
    return ozon, wb


def collect_live_stop_memberships(
    *,
    credentials: AppCredentials,
    source_actions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not credentials.ozon_performance or not credentials.wb:
        raise RuntimeError("Ozon Performance and WB credentials are required")
    ozon_targets, wb_targets = _source_targets(source_actions)
    ozon_adapter = OzonPerformanceAdapter(credentials.ozon_performance)
    wb_adapter = WbPromotionAdapter(credentials.wb)

    actions: list[dict[str, Any]] = []
    baseline: dict[str, Any] = {"ozon_campaigns": {}, "wb_campaigns": {}}
    for campaign in _active_ozon_campaigns(ozon_adapter):
        campaign_id = str(campaign["id"])
        products = ozon_adapter.fetch_campaign_products(campaign_id)
        snapshot = _ozon_campaign_snapshot(products)
        matched = sorted(set(snapshot).intersection(ozon_targets))
        if not matched:
            continue
        baseline["ozon_campaigns"][campaign_id] = snapshot
        for sku in matched:
            source = ozon_targets[sku]
            actions.append(
                {
                    "marketplace": "ozon",
                    "operation": "remove_from_cpc",
                    "campaign_id": campaign_id,
                    "campaign_name": str(campaign.get("title") or campaign.get("name") or ""),
                    "product_id": str(source["product_id"]),
                    "ozon_sku": sku,
                    "offer_id": str(source.get("offer_id") or ""),
                    "current_bid": snapshot[sku]["bid"],
                    "reason": str(source.get("reason") or "liquidation_hard_stop"),
                }
            )

    for campaign in _active_wb_campaigns(wb_adapter):
        campaign_id = str(campaign.get("id") or "")
        snapshot = _wb_campaign_snapshot(campaign)
        matched = sorted({int(value) for value in snapshot}.intersection(wb_targets))
        if not matched:
            continue
        baseline["wb_campaigns"][campaign_id] = snapshot
        settings = campaign.get("settings") if isinstance(campaign.get("settings"), dict) else {}
        for nm_id in matched:
            source = wb_targets[nm_id]
            actions.append(
                {
                    "marketplace": "wb",
                    "operation": "remove_from_cpc",
                    "campaign_id": campaign_id,
                    "campaign_name": str(settings.get("name") or ""),
                    "nm_id": nm_id,
                    "internal_sku": str(source.get("offer_id") or ""),
                    "current_search_bid_kopecks": snapshot[str(nm_id)]["search_bid_kopecks"],
                    "reason": str(source.get("reason") or "liquidation_hard_stop"),
                }
            )
    actions.sort(
        key=lambda row: (
            str(row["marketplace"]),
            str(row["campaign_id"]),
            str(row.get("ozon_sku") or row.get("nm_id") or ""),
        )
    )
    return actions, baseline


def _scope_signature(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("marketplace") or ""),
        str(row.get("operation") or ""),
        str(row.get("campaign_id") or ""),
        str(row.get("ozon_sku") or row.get("nm_id") or ""),
    )


def _write_report(path: Path, *, title: str, lines: list[str]) -> None:
    path.write_text("\n".join([f"# {title}", "", *lines, ""]), encoding="utf-8")


def run_liquidation_stop_plan(
    *,
    credentials: AppCredentials,
    data_dir: Path,
    source_run_id: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    source_dir = _find_run_dir(data_dir, source_run_id)
    source_path = source_dir / "processed" / "stop_review.json"
    source = _read_json(source_path)
    if source.get("schema") != "liquidation_stop_review.v1":
        raise RuntimeError("unsupported liquidation stop review schema")
    source_actions = source.get("actions") if isinstance(source.get("actions"), list) else []
    if not source_actions:
        raise RuntimeError("liquidation stop review has no actions")
    if str(source.get("actions_checksum") or "") != action_rows_checksum(source_actions):
        raise RuntimeError("liquidation stop review checksum mismatch")

    actions, baseline = collect_live_stop_memberships(
        credentials=credentials,
        source_actions=source_actions,
    )
    source_target_count = len(_source_targets(source_actions)[0]) + len(_source_targets(source_actions)[1])
    planned_target_count = len(
        {
            (row["marketplace"], str(row.get("ozon_sku") or row.get("nm_id") or ""))
            for row in actions
        }
    )
    if planned_target_count != source_target_count:
        raise RuntimeError(
            f"active CPC membership scope mismatch: source targets={source_target_count}, live targets={planned_target_count}"
        )

    started_at = datetime.now().astimezone()
    resolved_run_id = run_id or f"liquidation_stop_plan_{started_at:%Y%m%dT%H%M%S}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.date().isoformat() / resolved_run_id)
    processed_dir = ensure_dir(run_dir / "processed")
    write_json(processed_dir / "actions.json", actions)
    write_json(processed_dir / "baseline.json", baseline)
    checksum = action_rows_checksum(actions)
    artifacts = {
        "report": str(run_dir / "report.md"),
        "summary": str(run_dir / "summary.json"),
        "actions": str(processed_dir / "actions.json"),
        "baseline": str(processed_dir / "baseline.json"),
    }
    summary = {
        "run_id": resolved_run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "overall_status": "ok",
        "mode": "dry_run",
        "source_run_id": source_run_id,
        "source_stop_review": str(source_path),
        "source_stop_review_sha256": file_sha256(source_path),
        "actions_checksum": checksum,
        "actions_count": len(actions),
        "target_products": planned_target_count,
        "ozon_actions": sum(row["marketplace"] == "ozon" for row in actions),
        "wb_actions": sum(row["marketplace"] == "wb" for row in actions),
        "apply_performed": False,
        "artifacts": artifacts,
    }
    _write_report(
        Path(artifacts["report"]),
        title="Liquidation CPC hard-stop plan",
        lines=[
            f"- Source control: `{source_run_id}`.",
            f"- Exact products: `{planned_target_count}`; campaign removals: `{len(actions)}`.",
            f"- Ozon: `{summary['ozon_actions']}`; WB: `{summary['wb_actions']}`.",
            f"- Actions checksum: `{checksum}`.",
            "- Prices, minimum prices, actions and campaign budgets are outside this package.",
            "- Marketplace writes: `0`.",
        ],
    )
    write_json(Path(artifacts["summary"]), summary)
    summary["artifacts"].update(
        write_summary_run_manifest(
            data_dir=data_dir,
            run_dir=run_dir,
            summary=summary,
            task="liquidation-stop-plan",
            mode="dry_run",
            risk="normal",
            marketplaces=["ozon", "wb"],
            inputs={"source_run_id": source_run_id},
            lifecycle_status="pending_review",
            closed=False,
        )
    )
    write_json(Path(artifacts["summary"]), summary)
    return summary


def _fresh_campaigns_for_plan(
    credentials: AppCredentials,
    actions: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    if not credentials.ozon_performance or not credentials.wb:
        raise RuntimeError("Ozon Performance and WB credentials are required")
    ozon_ids = {str(row["campaign_id"]) for row in actions if row["marketplace"] == "ozon"}
    wb_ids = {int(row["campaign_id"]) for row in actions if row["marketplace"] == "wb"}
    ozon_adapter = OzonPerformanceAdapter(credentials.ozon_performance)
    wb_adapter = WbPromotionAdapter(credentials.wb)
    ozon = {
        campaign_id: _ozon_campaign_snapshot(ozon_adapter.fetch_campaign_products(campaign_id))
        for campaign_id in sorted(ozon_ids)
    }
    wb = {
        str(campaign.get("id")): _wb_campaign_snapshot(campaign)
        for campaign in wb_adapter.fetch_campaigns(ids=sorted(wb_ids), payment_type="cpc")
        if int(campaign.get("id") or 0) in wb_ids
    }
    if set(wb) != {str(value) for value in wb_ids}:
        raise RuntimeError("one or more approved WB campaigns are unavailable")
    return ozon, wb


def _verify_removed(
    *,
    actions: list[dict[str, Any]],
    ozon_campaigns: dict[str, dict[str, Any]],
    wb_campaigns: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for action in actions:
        campaign_id = str(action["campaign_id"])
        target_id = str(action.get("ozon_sku") or action.get("nm_id") or "")
        current = ozon_campaigns.get(campaign_id, {}) if action["marketplace"] == "ozon" else wb_campaigns.get(campaign_id, {})
        rows.append(
            {
                "marketplace": action["marketplace"],
                "campaign_id": campaign_id,
                "target_id": target_id,
                "status": "ok" if target_id not in current else "still_active",
            }
        )
    return rows


def run_liquidation_stop_apply(
    *,
    credentials: AppCredentials,
    data_dir: Path,
    plan_run_id: str,
    confirmed_by_user: bool,
    run_id: str | None = None,
) -> dict[str, Any]:
    if not confirmed_by_user:
        raise RuntimeError("explicit owner confirmation is required")
    plan_dir = _find_run_dir(data_dir, plan_run_id)
    approved_summary = _read_json(plan_dir / "summary.json")
    actions = _read_json(plan_dir / "processed" / "actions.json")
    if not isinstance(actions, list) or not actions:
        raise RuntimeError("approved liquidation stop plan is empty")
    if approved_summary.get("actions_checksum") != action_rows_checksum(actions):
        raise RuntimeError("approved liquidation stop plan checksum mismatch")

    before_ozon, before_wb = _fresh_campaigns_for_plan(credentials, actions)
    missing_before = _verify_removed(
        actions=actions,
        ozon_campaigns=before_ozon,
        wb_campaigns=before_wb,
    )
    if any(row["status"] != "still_active" for row in missing_before):
        raise RuntimeError("approved liquidation stop membership drifted before apply")

    started_at = datetime.now().astimezone()
    resolved_run_id = run_id or f"liquidation_stop_apply_{started_at:%Y%m%dT%H%M%S}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.date().isoformat() / resolved_run_id)
    raw_dir = ensure_dir(run_dir / "raw")
    processed_dir = ensure_dir(run_dir / "processed")
    write_json(processed_dir / "before_ozon.json", before_ozon)
    write_json(processed_dir / "before_wb.json", before_wb)

    ozon_adapter = OzonPerformanceAdapter(credentials.ozon_performance)  # type: ignore[arg-type]
    wb_adapter = WbPromotionAdapter(credentials.wb)  # type: ignore[arg-type]
    responses: list[dict[str, Any]] = []
    grouped_ozon: dict[str, list[str]] = defaultdict(list)
    grouped_wb: dict[int, list[int]] = defaultdict(list)
    for action in actions:
        if action["marketplace"] == "ozon":
            grouped_ozon[str(action["campaign_id"])].append(str(action["ozon_sku"]))
        else:
            grouped_wb[int(action["campaign_id"])].append(int(action["nm_id"]))
    for campaign_id, skus in sorted(grouped_ozon.items()):
        response = ozon_adapter.remove_campaign_products(campaign_id, sorted(skus))
        responses.append({"marketplace": "ozon", "campaign_id": campaign_id, "targets": sorted(skus), "response": response})
    for campaign_id, nm_ids in sorted(grouped_wb.items()):
        response = wb_adapter.update_campaign_products(advert_id=campaign_id, delete=sorted(nm_ids))
        responses.append({"marketplace": "wb", "campaign_id": campaign_id, "targets": sorted(nm_ids), "response": response})
    write_json(raw_dir / "responses.json", responses)

    after_ozon: dict[str, dict[str, Any]] = {}
    after_wb: dict[str, dict[str, Any]] = {}
    verify_rows: list[dict[str, Any]] = []
    for attempt in range(1, 7):
        if attempt > 1:
            time.sleep(3)
        after_ozon, after_wb = _fresh_campaigns_for_plan(credentials, actions)
        verify_rows = _verify_removed(actions=actions, ozon_campaigns=after_ozon, wb_campaigns=after_wb)
        write_json(raw_dir / f"verify_{attempt:02d}.json", {"ozon": after_ozon, "wb": after_wb, "rows": verify_rows})
        if all(row["status"] == "ok" for row in verify_rows):
            break

    target_by_campaign: dict[tuple[str, str], set[str]] = defaultdict(set)
    for action in actions:
        target_by_campaign[(action["marketplace"], str(action["campaign_id"]))].add(
            str(action.get("ozon_sku") or action.get("nm_id") or "")
        )
    integrity_rows: list[dict[str, Any]] = []
    for marketplace, before, after in (("ozon", before_ozon, after_ozon), ("wb", before_wb, after_wb)):
        for campaign_id, before_rows in before.items():
            targets = target_by_campaign[(marketplace, campaign_id)]
            expected = {key: value for key, value in before_rows.items() if key not in targets}
            actual = after.get(campaign_id, {})
            integrity_rows.append(
                {
                    "marketplace": marketplace,
                    "campaign_id": campaign_id,
                    "status": "ok" if actual == expected else "unexpected_campaign_drift",
                    "expected_members": len(expected),
                    "actual_members": len(actual),
                }
            )
    write_json(processed_dir / "verify_rows.json", verify_rows)
    write_json(processed_dir / "campaign_integrity.json", integrity_rows)
    verified = all(row["status"] == "ok" for row in verify_rows) and all(row["status"] == "ok" for row in integrity_rows)
    artifacts = {
        "report": str(run_dir / "report.md"),
        "summary": str(run_dir / "summary.json"),
        "verify_rows": str(processed_dir / "verify_rows.json"),
        "campaign_integrity": str(processed_dir / "campaign_integrity.json"),
    }
    summary = {
        "run_id": resolved_run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "overall_status": "ok" if verified else "warning",
        "mode": "apply",
        "approved_plan_run_id": plan_run_id,
        "actions_count": len(actions),
        "removed_targets": sum(row["status"] == "ok" for row in verify_rows),
        "verification_confirmed": verified,
        "prices_changed": 0,
        "minimum_prices_changed": 0,
        "actions_changed": 0,
        "budgets_changed": 0,
        "artifacts": artifacts,
    }
    _write_report(
        Path(artifacts["report"]),
        title="Liquidation CPC hard-stop apply",
        lines=[
            f"- Approved plan: `{plan_run_id}`.",
            f"- Removed and verified: `{summary['removed_targets']}` / `{len(actions)}`.",
            f"- Campaign integrity: `{'ok' if all(row['status'] == 'ok' for row in integrity_rows) else 'warning'}`.",
            "- Prices, minimum prices, marketplace actions and campaign budgets were not changed.",
        ],
    )
    write_json(Path(artifacts["summary"]), summary)
    summary["artifacts"].update(
        write_summary_run_manifest(
            data_dir=data_dir,
            run_dir=run_dir,
            summary=summary,
            task="liquidation-stop-apply",
            mode="apply",
            risk="high",
            marketplaces=["ozon", "wb"],
            inputs={"plan_run_id": plan_run_id, "confirmed_by_user": True},
            lifecycle_status="applied" if verified else "applying_unknown",
            closed=False,
        )
    )
    write_json(Path(artifacts["summary"]), summary)
    return summary


def run_liquidation_stop_verify(
    *,
    credentials: AppCredentials,
    data_dir: Path,
    plan_run_id: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    plan_dir = _find_run_dir(data_dir, plan_run_id)
    actions = _read_json(plan_dir / "processed" / "actions.json")
    if not isinstance(actions, list) or not actions:
        raise RuntimeError("approved liquidation stop plan is empty")
    current_ozon, current_wb = _fresh_campaigns_for_plan(credentials, actions)
    verify_rows = _verify_removed(
        actions=actions,
        ozon_campaigns=current_ozon,
        wb_campaigns=current_wb,
    )
    verified = all(row["status"] == "ok" for row in verify_rows)
    started_at = datetime.now().astimezone()
    resolved_run_id = run_id or f"liquidation_stop_verify_{started_at:%Y%m%dT%H%M%S}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.date().isoformat() / resolved_run_id)
    processed_dir = ensure_dir(run_dir / "processed")
    write_json(processed_dir / "verify_rows.json", verify_rows)
    artifacts = {
        "report": str(run_dir / "report.md"),
        "summary": str(run_dir / "summary.json"),
        "verify_rows": str(processed_dir / "verify_rows.json"),
    }
    summary = {
        "run_id": resolved_run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "overall_status": "ok" if verified else "warning",
        "mode": "verify",
        "plan_run_id": plan_run_id,
        "expected_rows": len(actions),
        "matched_rows": sum(row["status"] == "ok" for row in verify_rows),
        "verification_confirmed": verified,
        "lifecycle_status": "verified" if verified else "applying_unknown",
        "artifacts": artifacts,
    }
    _write_report(
        Path(artifacts["report"]),
        title="Liquidation CPC hard-stop verify",
        lines=[
            f"- Approved plan: `{plan_run_id}`.",
            f"- Confirmed absent from CPC: `{summary['matched_rows']}` / `{len(actions)}`.",
        ],
    )
    write_json(Path(artifacts["summary"]), summary)
    summary["artifacts"].update(
        write_summary_run_manifest(
            data_dir=data_dir,
            run_dir=run_dir,
            summary=summary,
            task="liquidation-stop-verify",
            mode="verify",
            risk="low",
            marketplaces=["ozon", "wb"],
            inputs={"plan_run_id": plan_run_id},
            lifecycle_status="verified" if verified else "applying_unknown",
            closed=verified,
        )
    )
    write_json(Path(artifacts["summary"]), summary)
    return summary
