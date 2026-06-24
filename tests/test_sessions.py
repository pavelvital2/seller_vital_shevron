from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from seller_agent.sessions.manager import install_systemd_units, restore_ozon_session
from seller_agent.sessions.state import SESSION_DEFINITIONS, SessionDefinition, parse_timestamp, refresh_freshness


def test_parse_timestamp_accepts_z_suffix() -> None:
    parsed = parse_timestamp("2026-06-10T20:50:54.292Z")

    assert parsed == datetime(2026, 6, 10, 20, 50, 54, 292000, tzinfo=timezone.utc)


def test_refresh_freshness_ok_with_recent_success(tmp_path: Path, monkeypatch) -> None:
    now = datetime(2026, 6, 10, 21, 0, tzinfo=timezone.utc)
    keepalive_path = tmp_path / "keepalive.json"
    keepalive_path.write_text(
        json.dumps(
            {
                "ok": True,
                "startedAt": (now - timedelta(seconds=70)).isoformat(),
                "finishedAt": (now - timedelta(seconds=60)).isoformat(),
                "stateExported": True,
                "valuesPrinted": False,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setitem(
        SESSION_DEFINITIONS,
        "test",
        SessionDefinition(
            marketplace="test",
            default_interval_seconds=1800,
            keepalive_last_path=keepalive_path,
            watchdog_pid_path=tmp_path / "missing.pid",
        ),
    )

    result = refresh_freshness("test", now=now)

    assert result["status"] == "ok"
    assert result["age_seconds"] == 60
    assert result["interval_seconds"] == 1800
    assert result["overdue"] is False


def test_refresh_freshness_warns_when_success_is_overdue(tmp_path: Path, monkeypatch) -> None:
    now = datetime(2026, 6, 10, 21, 0, tzinfo=timezone.utc)
    keepalive_path = tmp_path / "keepalive.json"
    keepalive_path.write_text(
        json.dumps(
            {
                "ok": True,
                "startedAt": (now - timedelta(seconds=2500)).isoformat(),
                "finishedAt": (now - timedelta(seconds=2400)).isoformat(),
                "stateExported": True,
                "valuesPrinted": False,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setitem(
        SESSION_DEFINITIONS,
        "test",
        SessionDefinition(
            marketplace="test",
            default_interval_seconds=1800,
            keepalive_last_path=keepalive_path,
            watchdog_pid_path=tmp_path / "missing.pid",
        ),
    )

    result = refresh_freshness("test", now=now)

    assert result["status"] == "warning"
    assert result["overdue"] is True
    assert result["error"] == "last keepalive is overdue"


def test_restore_ozon_session_dry_run_writes_safe_plan(tmp_path: Path) -> None:
    result = restore_ozon_session(dry_run=True, data_dir=tmp_path, run_id="restore_ozon_session_test")

    assert result["overall_status"] == "ok"
    assert result["mode"] == "dry_run"
    assert "interactive_ozon_login" in result["steps"]
    assert (tmp_path / "runs" / datetime.now().strftime("%Y-%m-%d") / "restore_ozon_session_test" / "summary.json").exists()


def test_install_systemd_units_dry_run_is_plan_only() -> None:
    result = install_systemd_units(dry_run=True, switch=True)

    assert result["overall_status"] == "ok"
    assert result["plan"]["switch"] is True
    assert result["operations"] == []
