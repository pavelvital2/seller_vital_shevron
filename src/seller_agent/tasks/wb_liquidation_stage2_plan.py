from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from seller_agent.config import AppCredentials
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.marketplaces.wb.prices_adapter import WbPricesAdapter
from seller_agent.reports.writer import ensure_dir, write_json
from seller_agent.safety.approvals import action_rows_checksum
from seller_agent.tasks.liquidation_daily_control import DEFAULT_WB_COHORT, _decimal, _integer, _read_csv, _write_csv


def build_stage2_rows(cohort: list[dict[str, str]], prices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    current = {_integer(row.get("nmID")): row for row in prices}
    rows: list[dict[str, Any]] = []
    for source in cohort:
        if str(source.get("requires_second_price_stage") or "").lower() != "true":
            continue
        nm_id = _integer(source.get("nm_id"))
        live = current.get(nm_id, {})
        base = _decimal((live.get("prices") or [source.get("base_price")])[0])
        current_discount = _integer(live.get("discount"))
        target_discount = _integer(source.get("target_discount"))
        target_price = (base * (Decimal(100) - target_discount) / Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        floor = _decimal(source.get("clearance_floor"))
        already_at_target = bool(live) and current_discount == target_discount
        ready = bool(live) and not already_at_target and target_price >= floor
        sizes = live.get("sizes") if isinstance(live.get("sizes"), list) else []
        live_discounted_price = _decimal((live.get("discountedPrices") or [0])[0])
        if not live_discounted_price and sizes and isinstance(sizes[0], dict):
            live_discounted_price = _decimal(sizes[0].get("discountedPrice"))
        rows.append(
            {
                "nm_id": nm_id,
                "internal_sku": source.get("internal_sku", ""),
                "title": source.get("title", ""),
                "pack_qty": max(_integer(source.get("pack_qty")), 1),
                "live_base_price": float(base),
                "live_discount": current_discount,
                "live_discounted_price": float(live_discounted_price),
                "stage1_expected_discount": _integer(source.get("upload_discount_stage1")),
                "target_discount": target_discount,
                "target_price": float(target_price),
                "clearance_floor": float(floor),
                "minimum_price_documented": float(_decimal(source.get("target_minimum"))),
                "drift_from_stage1": bool(live) and current_discount != _integer(source.get("upload_discount_stage1")),
                "ready_for_owner_review": ready,
                "already_at_target": already_at_target,
                "blocked_reason": "" if ready or already_at_target else ("missing_live_price" if not live else "target_below_floor"),
            }
        )
    return rows


def run_wb_liquidation_stage2_plan(
    *,
    credentials: AppCredentials,
    data_dir: Path = Path("data"),
    cohort_path: Path = DEFAULT_WB_COHORT,
    run_id: str | None = None,
) -> dict[str, Any]:
    if not credentials.wb:
        raise RuntimeError("WB credentials are required")
    started = datetime.now().astimezone()
    run_id = run_id or f"wb_liquidation_stage2_plan_{started:%Y%m%dT%H%M%S}"
    run_dir = ensure_dir(data_dir / "runs" / started.strftime("%Y-%m-%d") / run_id)
    prices = WbPricesAdapter(credentials.wb).fetch_goods_prices()
    cohort = _read_csv(cohort_path)
    rows = build_stage2_rows(cohort, prices)
    actions = [
        {"nm_id": row["nm_id"], "discount": row["target_discount"], "expected_price": row["target_price"]}
        for row in rows
        if row["ready_for_owner_review"]
    ]
    pending = {
        "schema": "wb_liquidation_stage2_approval.v1",
        "pending_id": run_id,
        "source_run_id": run_id,
        "mode": "dry_run",
        "apply_allowed": False,
        "actions": actions,
        "actions_checksum": action_rows_checksum(actions),
    }
    csv_path = run_dir / "stage2_plan.csv"
    report_path = run_dir / "report.md"
    pending_path = run_dir / "pending_approval.json"
    _write_csv(csv_path, rows)
    write_json(run_dir / "raw_prices.json", prices)
    write_json(pending_path, pending)
    already_at_target = sum(bool(row["already_at_target"]) for row in rows)
    blocked_rows = sum(bool(row["blocked_reason"]) for row in rows)
    status = "ok" if len(rows) == 18 and len(actions) + already_at_target == len(rows) else "warning"
    summary = {
        "run_id": run_id,
        "started_at": started.isoformat(timespec="seconds"),
        "overall_status": status,
        "mode": "dry_run",
        "cohort_rows": len(rows),
        "ready_for_owner_review": len(actions),
        "already_at_target": already_at_target,
        "blocked_rows": blocked_rows,
        "drift_rows": sum(bool(row["drift_from_stage1"]) for row in rows),
        "actions_checksum": pending["actions_checksum"],
        "apply_performed": False,
        "pending_id": run_id,
        "artifacts": {"report": str(report_path), "plan_csv": str(csv_path), "pending_approval": str(pending_path), "summary": str(run_dir / "summary.json")},
    }
    report_path.write_text(
        "\n".join([
            "# WB: второй ценовой шаг распродажи",
            "",
            f"Run ID: `{run_id}`",
            f"Точная когорта: **{len(rows)}**; требуется изменение: **{len(actions)}**; уже цель: **{already_at_target}**; блокированы: **{blocked_rows}**; drift от первого шага: **{summary['drift_rows']}**.",
            "",
            "Это fresh dry-run. Скидки, цены, акции и minimum в WB не менялись.",
            f"Checksum действий: `{pending['actions_checksum']}`.",
        ]),
        encoding="utf-8",
    )
    write_json(run_dir / "summary.json", summary)
    summary["artifacts"].update(write_summary_run_manifest(data_dir=data_dir, run_dir=run_dir, summary=summary, task="wb-liquidation-stage2-plan", mode="dry_run", risk="normal", marketplaces=["wb"], inputs={"cohort_path": str(cohort_path)}, pending_id=run_id, lifecycle_status="pending_review"))
    write_json(run_dir / "summary.json", summary)
    return summary
