from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any

from seller_agent.config import AppCredentials
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.reports.writer import ensure_dir, write_json
from seller_agent.tasks.status_preflight import run_status_preflight


DEFAULT_STATE_PATH = Path("runtime/state/ozon_lk_monitor.json")


def classify_ozon_lk_state(preflight: dict[str, Any]) -> tuple[str, str]:
    checks = preflight.get("checks") if isinstance(preflight.get("checks"), dict) else {}
    cdp = checks.get("ozon_cdp") if isinstance(checks.get("ozon_cdp"), dict) else {}
    keepalive = checks.get("ozon_keepalive") if isinstance(checks.get("ozon_keepalive"), dict) else {}
    refresh = checks.get("ozon_refresh_state") if isinstance(checks.get("ozon_refresh_state"), dict) else {}
    if not cdp.get("listening"):
        return "unavailable", "cdp_not_listening"
    if keepalive.get("ok") is False:
        return "login_required" if any(bool(row.get("needsLogin")) for row in keepalive.get("checks") or [] if isinstance(row, dict)) else "degraded", str(keepalive.get("error") or "keepalive_failed")
    if bool(refresh.get("overdue")):
        return "degraded", "refresh_overdue"
    if keepalive.get("ok") is True and cdp.get("listening"):
        return "ok", "authorized"
    return "unknown", "insufficient_lk_evidence"


def run_ozon_lk_state_monitor(*, credentials: AppCredentials, data_dir: Path = Path("data"), state_path: Path = DEFAULT_STATE_PATH, run_id: str | None = None) -> dict[str, Any]:
    started = datetime.now().astimezone()
    run_id = run_id or f"ozon_lk_state_monitor_{started:%Y%m%dT%H%M%S}"
    run_dir = ensure_dir(data_dir / "runs" / started.strftime("%Y-%m-%d") / run_id)
    preflight = run_status_preflight(
        credentials=credentials,
        data_dir=data_dir,
        include_lk=True,
        marketplaces=("ozon",),
        include_catalog=False,
    )
    state, reason = classify_ozon_lk_state(preflight)
    previous: dict[str, Any] = {}
    if state_path.exists():
        try:
            previous = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = {}
    previous_state = str(previous.get("state") or "")
    changed = bool(previous_state and previous_state != state)
    notification_required = changed or (not previous_state and state not in {"ok", "unknown"})
    state_payload = {"state": state, "reason": reason, "checked_at": started.isoformat(timespec="seconds"), "source_run_id": preflight.get("run_id", "")}
    ensure_dir(state_path.parent)
    write_json(state_path, state_payload)
    state_path.chmod(0o600)
    summary = {"run_id": run_id, "started_at": started.isoformat(timespec="seconds"), "overall_status": "ok" if state == "ok" else "warning", "mode": "read_only", "state": state, "reason": reason, "previous_state": previous_state or None, "state_changed": changed, "notification_required": notification_required, "apply_performed": False, "artifacts": {"report": str(run_dir / "report.md"), "summary": str(run_dir / "summary.json"), "state": str(state_path), "preflight_report": str(preflight.get("artifacts", {}).get("report", ""))}}
    (run_dir / "report.md").write_text("\n".join(["# Ozon LK: контроль состояния", "", f"Состояние: **{state}** (`{reason}`).", f"Предыдущее: **{previous_state or 'первичная фиксация'}**; переход: **{'да' if changed else 'нет'}**.", "", "Уведомление владельцу отправляется только при переходе состояния или при первой фиксации ошибки."]), encoding="utf-8")
    write_json(run_dir / "summary.json", summary)
    summary["artifacts"].update(write_summary_run_manifest(data_dir=data_dir, run_dir=run_dir, summary=summary, task="ozon-lk-state-monitor", mode="read_only", risk="none", marketplaces=["ozon"], inputs={}, lifecycle_status="closed", closed=True))
    write_json(run_dir / "summary.json", summary)
    return summary
