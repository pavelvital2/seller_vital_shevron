from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from seller_agent.config import AppCredentials
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.marketplaces.ozon.adapter import OzonSellerAdapter
from seller_agent.reports.writer import ensure_dir, write_json
from seller_agent.safety.approvals import action_rows_checksum
from seller_agent.tasks.liquidation_daily_control import _write_csv


def build_timer_rows(product_ids: list[str], statuses: list[dict[str, Any]], *, now: datetime, warning_days: int = 5) -> list[dict[str, Any]]:
    by_id = {str(row.get("product_id") or ""): row for row in statuses}
    rows = []
    for product_id in product_ids:
        source = by_id.get(str(product_id), {})
        enabled = bool(source.get("min_price_for_auto_actions_enabled"))
        raw_expired = str(source.get("expired_at") or "")
        try:
            expired_at = datetime.fromisoformat(raw_expired.replace("Z", "+00:00")).astimezone(timezone.utc)
            days_left = (expired_at - now.astimezone(timezone.utc)).total_seconds() / 86400
        except ValueError:
            expired_at = None
            days_left = None
        refresh = not enabled or days_left is None or days_left <= warning_days
        rows.append({"product_id": str(product_id), "enabled": enabled, "expired_at": raw_expired, "days_left": round(days_left, 2) if days_left is not None else "", "refresh_candidate": refresh, "reason": "disabled" if not enabled else "missing_expiry" if days_left is None else "expiring" if days_left <= warning_days else "ok"})
    return rows


def run_ozon_min_price_timer_plan(*, credentials: AppCredentials, data_dir: Path = Path("data"), warning_days: int = 5, run_id: str | None = None) -> dict[str, Any]:
    if not credentials.ozon_seller:
        raise RuntimeError("Ozon Seller credentials are required")
    started = datetime.now().astimezone()
    run_id = run_id or f"ozon_min_price_timer_plan_{started:%Y%m%dT%H%M%S}"
    run_dir = ensure_dir(data_dir / "runs" / started.strftime("%Y-%m-%d") / run_id)
    adapter = OzonSellerAdapter(credentials.ozon_seller)
    products = adapter.fetch_product_list(visibility="ALL")
    product_ids = sorted({str(row.get("product_id") or "") for row in products if str(row.get("product_id") or "")})
    statuses = adapter.fetch_action_timer_statuses(product_ids)
    rows = build_timer_rows(product_ids, statuses, now=started, warning_days=warning_days)
    actions = [{"product_id": row["product_id"]} for row in rows if row["refresh_candidate"]]
    pending = {"schema": "ozon_min_price_timer_refresh.v1", "pending_id": run_id, "source_run_id": run_id, "mode": "dry_run", "apply_allowed": False, "endpoint_if_approved": "POST /v1/product/action/timer/update", "actions": actions, "actions_checksum": action_rows_checksum(actions)}
    _write_csv(run_dir / "timer_status.csv", rows)
    write_json(run_dir / "raw_statuses.json", statuses)
    write_json(run_dir / "pending_approval.json", pending)
    summary = {"run_id": run_id, "started_at": started.isoformat(timespec="seconds"), "overall_status": "warning" if actions else "ok", "mode": "dry_run", "products_checked": len(product_ids), "statuses_received": len(statuses), "refresh_candidates": len(actions), "warning_days": warning_days, "actions_checksum": pending["actions_checksum"], "notification_required": bool(actions), "pending_id": run_id, "apply_performed": False, "artifacts": {"report": str(run_dir / "report.md"), "summary": str(run_dir / "summary.json"), "status_csv": str(run_dir / "timer_status.csv"), "pending_approval": str(run_dir / "pending_approval.json")}}
    (run_dir / "report.md").write_text("\n".join(["# Ozon: срок защиты минимальной цены", "", f"Проверено: **{len(product_ids)}**; статусов: **{len(statuses)}**; refresh-кандидатов в горизонте {warning_days} дней: **{len(actions)}**.", f"Checksum: `{pending['actions_checksum']}`.", "", "Это dry-run. `/v1/product/action/timer/update` не вызывался, цены и minimum не менялись."]), encoding="utf-8")
    write_json(run_dir / "summary.json", summary)
    summary["artifacts"].update(write_summary_run_manifest(data_dir=data_dir, run_dir=run_dir, summary=summary, task="ozon-min-price-timer-plan", mode="dry_run", risk="normal", marketplaces=["ozon"], inputs={"warning_days": warning_days}, pending_id=run_id, lifecycle_status="pending_review" if actions else "closed", closed=not actions))
    write_json(run_dir / "summary.json", summary)
    return summary
