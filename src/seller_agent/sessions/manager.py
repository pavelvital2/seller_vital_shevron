from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
from typing import Any

from seller_agent.reports.writer import ensure_dir, write_json
from seller_agent.safety.locks import file_lock
from seller_agent.sessions.state import PROJECT_ROOT, combined_session_status


SCRIPT_DIR = PROJECT_ROOT / "scripts" / "sessions"
OZON_PROFILE_DIR = PROJECT_ROOT / ".sessions" / "ozon" / "chrome-profile"
SYSTEMD_SOURCE_DIR = PROJECT_ROOT / "deploy" / "systemd" / "user"
SYSTEMD_TARGET_DIR = Path.home() / ".config" / "systemd" / "user"
SYSTEMD_UNITS = [
    "vital-shevron-ozon-keeper.service",
    "vital-shevron-ozon-session-refresh.service",
    "vital-shevron-ozon-session-refresh.timer",
    "vital-shevron-wb-session-refresh.service",
    "vital-shevron-wb-session-refresh.timer",
]


SESSION_SCRIPTS = {
    "ozon": {
        "start": ["start_ozon_keeper.sh", "start_ozon_session_watchdog.sh"],
        "stop": ["stop_ozon_session_watchdog.sh", "stop_ozon_keeper.sh"],
    },
    "wb": {
        "start": ["start_wb_session_watchdog.sh"],
        "stop": ["stop_wb_session_watchdog.sh"],
    },
}


def _public_args(args: list[str]) -> list[str]:
    public = list(args)
    for index, value in enumerate(public[:-1]):
        if value in {"--email"}:
            public[index + 1] = "<redacted>"
    return public


def _run_command(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: int = 180,
    capture: bool = True,
) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            args,
            cwd=PROJECT_ROOT,
            env=env,
            text=True,
            capture_output=capture,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "error", "args": _public_args(args), "returncode": None, "error": str(exc)[:800]}
    return {
        "status": "ok" if completed.returncode == 0 else "error",
        "args": _public_args(args),
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip()[-2000:] if completed.stdout else "",
        "stderr": completed.stderr.strip()[-2000:] if completed.stderr else "",
    }


def _run_session_scripts(marketplace: str, action: str) -> list[dict[str, Any]]:
    scripts = SESSION_SCRIPTS[marketplace][action]
    results = []
    for script in scripts:
        results.append(_run_command(["bash", str(SCRIPT_DIR / script)]))
    return results


def _with_xvfb_when_needed(args: list[str]) -> list[str]:
    if os.environ.get("DISPLAY"):
        return args
    xvfb_run = shutil.which("xvfb-run")
    if not xvfb_run:
        return args
    return [xvfb_run, "-a", *args]


def _run_systemd_commands(commands: list[list[str]], *, timeout: int = 180) -> list[dict[str, Any]]:
    results = []
    if not systemd_user_available():
        return [{"status": "warning", "returncode": None, "error": "systemd --user is not available"}]
    for command in commands:
        results.append(_run_command(["systemctl", "--user", *command], timeout=timeout))
    return results


def _ozon_profile_pids() -> list[int]:
    completed = subprocess.run(
        ["ps", "-eo", "pid=,args="],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        return []
    markers = [
        str(OZON_PROFILE_DIR),
        str(SCRIPT_DIR / "ozon_keep_dashboard_open.js"),
    ]
    current_pid = os.getpid()
    pids: list[int] = []
    for line in completed.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        pid_text, _, args = stripped.partition(" ")
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        if pid == current_pid:
            continue
        if any(marker in args for marker in markers):
            pids.append(pid)
    return sorted(set(pids))


def _cleanup_ozon_profile_processes() -> dict[str, Any]:
    before = _ozon_profile_pids()
    terminated: list[int] = []
    killed: list[int] = []
    for pid in before:
        try:
            os.kill(pid, signal.SIGTERM)
            terminated.append(pid)
        except ProcessLookupError:
            continue
        except PermissionError:
            continue
    if terminated:
        time.sleep(3)
    remaining = _ozon_profile_pids()
    for pid in remaining:
        try:
            os.kill(pid, signal.SIGKILL)
            killed.append(pid)
        except ProcessLookupError:
            continue
        except PermissionError:
            continue
    if killed:
        time.sleep(1)

    after = _ozon_profile_pids()
    locks_removed: list[str] = []
    if not after:
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            path = OZON_PROFILE_DIR / name
            try:
                path.unlink()
                locks_removed.append(str(path))
            except FileNotFoundError:
                continue
    return {
        "status": "ok" if not after else "warning",
        "pids_before": before,
        "terminated": terminated,
        "killed": killed,
        "pids_after": after,
        "locks_removed": locks_removed,
    }


def _selected_marketplaces(marketplace: str) -> list[str]:
    if marketplace == "all":
        return ["ozon", "wb"]
    if marketplace not in SESSION_SCRIPTS:
        raise ValueError(f"unknown marketplace: {marketplace}")
    return [marketplace]


def run_session_manager(
    *,
    action: str,
    marketplace: str = "all",
    data_dir: Path = Path("data"),
    run_id: str | None = None,
) -> dict[str, Any]:
    started_at = datetime.now()
    run_id = run_id or f"sessions_{action}_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    selected = _selected_marketplaces(marketplace)

    operations: dict[str, Any] = {}
    if action == "status":
        operations = {}
    elif action in {"start", "stop"}:
        for item in selected:
            operations[item] = _run_session_scripts(item, action)
    elif action == "restart":
        for item in selected:
            operations[item] = {
                "stop": _run_session_scripts(item, "stop"),
                "start": _run_session_scripts(item, "start"),
            }
    else:
        raise ValueError(f"unknown session action: {action}")

    status = combined_session_status(selected)
    result = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "action": action,
        "marketplace": marketplace,
        "overall_status": status["overall_status"],
        "operations": operations,
        "sessions": status["sessions"],
        "artifacts": {
            "run_dir": str(run_dir),
            "summary": str(run_dir / "summary.json"),
        },
    }
    write_json(run_dir / "summary.json", result)
    return result


def restore_ozon_session(
    *,
    email: str | None = None,
    expected_store: str = "Vital Shevron",
    max_codes: int = 3,
    dry_run: bool = False,
    data_dir: Path = Path("data"),
    run_id: str | None = None,
) -> dict[str, Any]:
    started_at = datetime.now()
    run_id = run_id or f"restore_ozon_session_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    steps = [
        "stop_ozon_session_watchdog",
        "stop_ozon_keeper",
        "interactive_ozon_login",
        "start_ozon_keeper",
        "start_ozon_session_watchdog",
    ]
    if dry_run:
        result = {
            "run_id": run_id,
            "started_at": started_at.isoformat(timespec="seconds"),
            "mode": "dry_run",
            "overall_status": "ok",
            "steps": steps,
            "artifacts": {"run_dir": str(run_dir), "summary": str(run_dir / "summary.json")},
        }
        write_json(run_dir / "summary.json", result)
        return result

    operations: dict[str, Any] = {}
    login_returncode: int | None = None
    systemd_available = systemd_user_available()
    with file_lock("ozon-session-restore"):
        if systemd_available:
            operations["stop_systemd"] = _run_systemd_commands(
                [
                    ["stop", "vital-shevron-ozon-session-refresh.timer"],
                    ["stop", "vital-shevron-ozon-session-refresh.service"],
                    ["stop", "vital-shevron-ozon-keeper.service"],
                ],
                timeout=90,
            )
        operations["stop_watchdog"] = _run_command(["bash", str(SCRIPT_DIR / "stop_ozon_session_watchdog.sh")])
        operations["stop_keeper"] = _run_command(["bash", str(SCRIPT_DIR / "stop_ozon_keeper.sh")])
        operations["cleanup_profile_processes"] = _cleanup_ozon_profile_processes()

        env = dict(os.environ)
        env["OZON_EXPECTED_STORE"] = expected_store
        env["OZON_MAX_CODES"] = str(max_codes)
        if email:
            env["OZON_SELLER_EMAIL"] = email
        login_args = [
            "node",
            str(SCRIPT_DIR / "ozon_seller_interactive_login.js"),
            "--expected-store",
            expected_store,
            "--max-codes",
            str(max_codes),
            "--precheck-dashboard",
        ]
        if email:
            login_args.extend(["--email", email])
        operations["interactive_login"] = _run_command(
            _with_xvfb_when_needed(login_args),
            env=env,
            timeout=1800,
            capture=False,
        )
        login_returncode = operations["interactive_login"].get("returncode")

        if systemd_available:
            operations["start_systemd"] = _run_systemd_commands(
                [
                    ["restart", "vital-shevron-ozon-keeper.service"],
                    ["restart", "vital-shevron-ozon-session-refresh.timer"],
                    ["start", "vital-shevron-ozon-session-refresh.service"],
                ],
                timeout=180,
            )
        else:
            operations["start_keeper"] = _run_command(["bash", str(SCRIPT_DIR / "start_ozon_keeper.sh")])
            operations["start_watchdog"] = _run_command(["bash", str(SCRIPT_DIR / "start_ozon_session_watchdog.sh")])

    status = combined_session_status(["ozon"])
    result_status = "ok" if login_returncode == 0 and status["overall_status"] == "ok" else "warning"
    result = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "apply",
        "overall_status": result_status,
        "steps": steps,
        "operations": operations,
        "sessions": status["sessions"],
        "artifacts": {"run_dir": str(run_dir), "summary": str(run_dir / "summary.json")},
    }
    write_json(run_dir / "summary.json", result)
    return result


def systemd_user_available() -> bool:
    result = _run_command(["systemctl", "--user", "show-environment"], timeout=30)
    return result["status"] == "ok"


def install_systemd_units(*, switch: bool = False, dry_run: bool = True) -> dict[str, Any]:
    plan = {
        "source_dir": str(SYSTEMD_SOURCE_DIR),
        "target_dir": str(SYSTEMD_TARGET_DIR),
        "units": SYSTEMD_UNITS,
        "switch": switch,
        "dry_run": dry_run,
    }
    if dry_run:
        return {"overall_status": "ok", "plan": plan, "operations": []}
    if not systemd_user_available():
        return {"overall_status": "error", "plan": plan, "operations": [{"status": "error", "error": "systemd --user is not available"}]}

    ensure_dir(SYSTEMD_TARGET_DIR)
    operations: list[dict[str, Any]] = []
    for unit in SYSTEMD_UNITS:
        source = SYSTEMD_SOURCE_DIR / unit
        target = SYSTEMD_TARGET_DIR / unit
        shutil.copy2(source, target)
        operations.append({"status": "ok", "operation": "copy", "source": str(source), "target": str(target)})

    operations.append(_run_command(["systemctl", "--user", "daemon-reload"], timeout=60))
    if switch:
        operations.append(_run_command(["bash", str(SCRIPT_DIR / "stop_ozon_session_watchdog.sh")]))
        operations.append(_run_command(["bash", str(SCRIPT_DIR / "stop_wb_session_watchdog.sh")]))
        operations.append(_run_command(["bash", str(SCRIPT_DIR / "stop_ozon_keeper.sh")]))
        operations.append(_run_command(["systemctl", "--user", "enable", "vital-shevron-ozon-keeper.service"], timeout=60))
        operations.append(_run_command(["systemctl", "--user", "restart", "vital-shevron-ozon-keeper.service"], timeout=60))
        operations.append(_run_command(["systemctl", "--user", "enable", "vital-shevron-ozon-session-refresh.timer"], timeout=60))
        operations.append(_run_command(["systemctl", "--user", "restart", "vital-shevron-ozon-session-refresh.timer"], timeout=60))
        operations.append(_run_command(["systemctl", "--user", "enable", "vital-shevron-wb-session-refresh.timer"], timeout=60))
        operations.append(_run_command(["systemctl", "--user", "restart", "vital-shevron-wb-session-refresh.timer"], timeout=60))
        operations.append(_run_command(["systemctl", "--user", "start", "vital-shevron-ozon-session-refresh.service"], timeout=180))
        operations.append(_run_command(["systemctl", "--user", "start", "vital-shevron-wb-session-refresh.service"], timeout=240))
    else:
        operations.append(_run_command(["systemctl", "--user", "enable", "vital-shevron-ozon-session-refresh.timer"], timeout=60))
        operations.append(_run_command(["systemctl", "--user", "enable", "vital-shevron-wb-session-refresh.timer"], timeout=60))

    overall_status = "error" if any(item.get("status") == "error" for item in operations) else "ok"
    return {"overall_status": overall_status, "plan": plan, "operations": operations}
