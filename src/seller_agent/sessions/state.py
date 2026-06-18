from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import subprocess
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class SessionDefinition:
    marketplace: str
    default_interval_seconds: int
    keepalive_last_path: Path
    watchdog_pid_path: Path
    keeper_pid_path: Path | None = None
    watchdog_timer_unit: str | None = None
    keeper_service_unit: str | None = None
    cdp_host: str | None = None
    cdp_port: int | None = None


SESSION_DEFINITIONS = {
    "ozon": SessionDefinition(
        marketplace="ozon",
        default_interval_seconds=1800,
        keepalive_last_path=PROJECT_ROOT / ".sessions" / "ozon" / "ozon_session_keepalive_last.json",
        watchdog_pid_path=PROJECT_ROOT / ".sessions" / "ozon" / "ozon_session_watchdog.pid",
        keeper_pid_path=PROJECT_ROOT / ".sessions" / "ozon" / "ozon_keeper.pid",
        watchdog_timer_unit="vital-shevron-ozon-session-refresh.timer",
        keeper_service_unit="vital-shevron-ozon-keeper.service",
        cdp_host="127.0.0.1",
        cdp_port=9544,
    ),
    "wb": SessionDefinition(
        marketplace="wb",
        default_interval_seconds=3600,
        keepalive_last_path=PROJECT_ROOT / ".sessions" / "wb" / "wb_session_keepalive_last.json",
        watchdog_pid_path=PROJECT_ROOT / ".sessions" / "wb" / "wb_session_watchdog.pid",
        watchdog_timer_unit="vital-shevron-wb-session-refresh.timer",
    ),
}


def safe_error(exc: BaseException) -> str:
    return str(exc).replace("\n", " ")[:800]


def parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def process_is_running(pid_file: Path) -> dict[str, Any]:
    if not pid_file.exists():
        return {"status": "error", "pid_file": str(pid_file), "running": False, "error": "pid file missing"}

    raw_pid = pid_file.read_text(encoding="utf-8").strip()
    if not raw_pid.isdigit():
        return {"status": "error", "pid_file": str(pid_file), "running": False, "error": "invalid pid file"}

    pid = int(raw_pid)
    if not Path(f"/proc/{pid}").exists():
        return {"status": "error", "pid_file": str(pid_file), "pid": pid, "running": False, "error": "process not running"}

    return {"status": "ok", "pid_file": str(pid_file), "pid": pid, "running": True}


def systemd_user_unit_status(unit: str) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [
                "systemctl",
                "--user",
                "show",
                unit,
                "--property=LoadState,ActiveState,SubState,UnitFileState,NextElapseUSecRealtime,ExecMainPID",
            ],
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "error", "unit": unit, "active": False, "error": safe_error(exc)}

    if completed.returncode != 0:
        return {
            "status": "error",
            "unit": unit,
            "active": False,
            "error": completed.stderr.strip()[-800:] or completed.stdout.strip()[-800:] or "systemctl failed",
        }

    fields: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        fields[key] = value
    active = fields.get("ActiveState") == "active"
    loaded = fields.get("LoadState") == "loaded"
    return {
        "status": "ok" if active and loaded else "error",
        "unit": unit,
        "active": active,
        "load_state": fields.get("LoadState"),
        "active_state": fields.get("ActiveState"),
        "sub_state": fields.get("SubState"),
        "unit_file_state": fields.get("UnitFileState"),
        "next_elapse": fields.get("NextElapseUSecRealtime") or None,
        "exec_main_pid": int(fields["ExecMainPID"]) if fields.get("ExecMainPID", "").isdigit() else None,
    }


def process_or_systemd_status(
    *,
    pid_file: Path | None,
    unit: str | None,
    kind: str,
) -> dict[str, Any]:
    pid_status = process_is_running(pid_file) if pid_file else {"status": "skipped"}
    unit_status = systemd_user_unit_status(unit) if unit else {"status": "skipped"}
    ok = pid_status.get("status") == "ok" or unit_status.get("status") == "ok"
    return {
        "status": "ok" if ok else "error",
        "kind": kind,
        "source": "pid" if pid_status.get("status") == "ok" else "systemd" if unit_status.get("status") == "ok" else "none",
        "pid": pid_status,
        "systemd": unit_status,
    }


def process_env_value(pid: int | None, key: str) -> str | None:
    if pid is None:
        return None
    environ_path = Path(f"/proc/{pid}/environ")
    try:
        raw = environ_path.read_bytes()
    except OSError:
        return None
    prefix = f"{key}=".encode("utf-8")
    for item in raw.split(b"\0"):
        if item.startswith(prefix):
            return item[len(prefix) :].decode("utf-8", errors="replace")
    return None


def tcp_port_open(host: str, port: int, *, timeout: float = 2.0) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def watchdog_interval_seconds(definition: SessionDefinition, watchdog_pid: int | None = None) -> int:
    raw_interval = process_env_value(watchdog_pid, "INTERVAL_SECONDS")
    if raw_interval and raw_interval.isdigit():
        return int(raw_interval)
    return definition.default_interval_seconds


def refresh_freshness(
    marketplace: str,
    *,
    now: datetime | None = None,
    overdue_grace_seconds: int = 300,
) -> dict[str, Any]:
    definition = SESSION_DEFINITIONS[marketplace]
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    watchdog = process_or_systemd_status(
        pid_file=definition.watchdog_pid_path,
        unit=definition.watchdog_timer_unit,
        kind="watchdog",
    )
    pid_status = watchdog.get("pid") if isinstance(watchdog.get("pid"), dict) else {}
    interval_seconds = watchdog_interval_seconds(definition, pid_status.get("pid") if pid_status.get("running") else None)
    overdue_after_seconds = interval_seconds + overdue_grace_seconds

    result: dict[str, Any] = {
        "status": "error",
        "marketplace": marketplace,
        "path": str(definition.keepalive_last_path),
        "interval_seconds": interval_seconds,
        "overdue_after_seconds": overdue_after_seconds,
        "watchdog_running": watchdog.get("status") == "ok",
        "watchdog_source": watchdog.get("source"),
    }

    if not definition.keepalive_last_path.exists():
        result["error"] = "keepalive last file missing"
        return result

    try:
        payload = json.loads(definition.keepalive_last_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        result["error"] = safe_error(exc)
        return result

    finished_at = parse_timestamp(payload.get("finishedAt") or payload.get("finished_at"))
    started_at = parse_timestamp(payload.get("startedAt") or payload.get("started_at"))
    ok = bool(payload.get("ok"))
    state_exported = payload.get("stateExported")
    values_printed = payload.get("valuesPrinted")
    age_seconds = None
    if finished_at:
        age_seconds = max(0, int((now - finished_at).total_seconds()))

    overdue = age_seconds is None or age_seconds > overdue_after_seconds
    if ok and not overdue:
        status = "ok"
    elif ok and overdue:
        status = "warning"
    else:
        status = "error"

    result.update(
        {
            "status": status,
            "ok": ok,
            "started_at": started_at.isoformat() if started_at else None,
            "finished_at": finished_at.isoformat() if finished_at else None,
            "age_seconds": age_seconds,
            "overdue": overdue,
            "state_exported": state_exported,
            "values_printed": values_printed,
        }
    )
    if not ok:
        result["error"] = str(payload.get("error") or "last keepalive was not ok")[:800]
    elif overdue:
        result["error"] = "last keepalive is overdue"
    return result


def session_snapshot(marketplace: str) -> dict[str, Any]:
    definition = SESSION_DEFINITIONS[marketplace]
    watchdog = process_or_systemd_status(
        pid_file=definition.watchdog_pid_path,
        unit=definition.watchdog_timer_unit,
        kind="watchdog",
    )
    snapshot: dict[str, Any] = {
        "marketplace": marketplace,
        "watchdog": watchdog,
        "refresh": refresh_freshness(marketplace),
    }
    if definition.keeper_pid_path:
        snapshot["keeper"] = process_or_systemd_status(
            pid_file=definition.keeper_pid_path,
            unit=definition.keeper_service_unit,
            kind="keeper",
        )
    if definition.cdp_host and definition.cdp_port:
        snapshot["cdp"] = {
            "status": "ok" if tcp_port_open(definition.cdp_host, definition.cdp_port) else "error",
            "host": definition.cdp_host,
            "port": definition.cdp_port,
        }
    return snapshot


def combined_session_status(marketplaces: list[str] | None = None) -> dict[str, Any]:
    selected = marketplaces or sorted(SESSION_DEFINITIONS)
    sessions = {marketplace: session_snapshot(marketplace) for marketplace in selected}
    statuses: list[str] = []
    for snapshot in sessions.values():
        for value in snapshot.values():
            if isinstance(value, dict) and isinstance(value.get("status"), str):
                statuses.append(value["status"])
            if isinstance(value, dict) and isinstance(value.get("refresh"), dict):
                statuses.append(value["refresh"].get("status", "error"))
    if any(status == "error" for status in statuses):
        overall_status = "error"
    elif any(status in {"warning", "skipped"} for status in statuses):
        overall_status = "warning"
    else:
        overall_status = "ok"
    return {
        "overall_status": overall_status,
        "sessions": sessions,
    }
