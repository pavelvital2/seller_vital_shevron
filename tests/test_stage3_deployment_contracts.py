from __future__ import annotations

import re
from pathlib import Path


UNIT = Path("deploy/systemd/system/vital-shevron-control-plane.service.example")
LEGACY_USER_UNIT = Path(
    "deploy/systemd/user/vital-shevron-control-plane.service.example"
)
NGINX = Path("deploy/nginx/vital-shevron-control-plane.location.conf.example")
ENV = Path("deploy/env/vital-shevron-control-plane.env.example")
GITIGNORE = Path(".gitignore")
RUNBOOK = Path("data/planning/stage3_control_plane_runbook.md")


def test_control_unit_is_isolated_and_does_not_start_another_worker() -> None:
    assert UNIT.is_file()
    assert not LEGACY_USER_UNIT.exists()
    unit = UNIT.read_text(encoding="utf-8")
    assert "User=pavel" in unit
    assert "Group=pavel" in unit
    assert "WorkingDirectory=/home/pavel/projects/seller_vital_shevron" in unit
    assert "python -m seller_agent.control_plane" in unit
    assert "127.0.0.1" not in unit
    assert "job-worker" not in unit
    assert "ProtectSystem=strict" in unit
    assert "ProtectHome=tmpfs" in unit
    assert "NoNewPrivileges=true" in unit
    for directive in (
        "PrivateDevices=true",
        "ProtectClock=true",
        "ProtectKernelLogs=true",
        "ProtectKernelModules=true",
    ):
        assert directive in unit
    assert "UnsetEnvironment=OZON_" in unit
    assert "PARSER_DATA_API_TOKEN" in unit
    assert ".sessions/ozon" in unit and ".sessions/wb" in unit
    assert "BindReadOnlyPaths=-/home/pavel/projects/seller_vital_shevron" in unit
    assert "BindPaths=-/home/pavel/projects/seller_vital_shevron/runtime" in unit
    assert "BindPaths=-/home/pavel/projects/seller_vital_shevron/.sessions/telegram" in unit
    assert "WantedBy=multi-user.target" in unit
    assert "WantedBy=default.target" not in unit


def test_control_unit_denies_unrelated_home_and_old_bot_secret() -> None:
    unit = UNIT.read_text(encoding="utf-8")
    for path in (
        "/home/pavel/.ssh",
        "/home/pavel/.codex",
        "/home/pavel/.config",
        "/home/pavel/.local",
        "/home/pavel/.secrets/vital_shevron_telegram_bot_token",
    ):
        assert f"InaccessiblePaths=-{path}" in unit
    for required in (
        "/home/pavel/projects/seller_vital_shevron/runtime",
        "/home/pavel/projects/seller_vital_shevron/.sessions/telegram",
        "/home/pavel/.secrets/vital_shevron_control_bot_token",
        "/home/pavel/.secrets/vital_shevron_control_session_secret",
    ):
        assert required in unit
    assert "/home/Codex/agent-tools/python" in unit
    assert "InaccessiblePaths=-/home/Codex" not in unit


def test_nginx_contract_is_same_origin_local_and_does_not_log_auth() -> None:
    nginx = NGINX.read_text(encoding="utf-8")
    assert "location ^~ /vital-shevron/" in nginx
    assert "proxy_pass http://127.0.0.1:8092" in nginx
    assert "access_log off" in nginx
    assert 'proxy_set_header Authorization ""' in nginx
    assert 'proxy_set_header X-Forwarded-For ""' in nginx
    assert "client_max_body_size 32k" in nginx
    assert "unsafe-inline" not in nginx
    assert "unsafe-eval" not in nginx
    assert "Access-Control-Allow-Origin" not in nginx


def test_env_example_contains_paths_but_no_secret_values() -> None:
    env = ENV.read_text(encoding="utf-8")
    assert "VITAL_SHEVRON_CONTROL_BOT_TOKEN_FILE=" in env
    assert "VITAL_SHEVRON_CONTROL_SESSION_SECRET_FILE=" in env
    assert "VITAL_SHEVRON_CONTROL_OWNER_IDS=\n" in env
    assert "BOT_TOKEN=" not in env
    assert "SESSION_SECRET=" not in env
    assert "OZON_SELLER_API_KEY" not in env
    assert "WB_API_TOKEN" not in env


def test_actual_control_env_is_exactly_ignored() -> None:
    rules = {
        line.strip()
        for line in GITIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert "/deploy/env/vital-shevron-control-plane.env" in rules
    assert "/deploy/env/vital-shevron-control-plane.env.example" not in rules


def test_runbook_records_completed_system_security_gate_without_ids() -> None:
    runbook = RUNBOOK.read_text(encoding="utf-8")
    normalized = " ".join(runbook.split())

    assert "https://83328.koara.live/vital-shevron/" in normalized
    assert "partial_success" in normalized
    assert "notification route" in normalized and "`sent`" in normalized
    assert "общий host mount namespace" in normalized
    assert "Завершённый system-unit security gate подтвердил" in normalized
    assert "active и enabled" in normalized
    assert "disabled и inactive" in normalized
    assert "отдельный от host mount namespace" in normalized
    assert "`/home` представлен" in normalized and "`tmpfs`" in normalized
    assert "inaccessible paths с mode `000`" in normalized
    assert "Parser freshness подтверждена как fresh" in normalized
    assert "публичный `/ready` вернулся" in normalized
    assert "system-level unit" in normalized
    assert "User=pavel" in normalized and "Group=pavel" in normalized
    assert re.search(r"\bMainPID(?:=|\s+)\d+\b", runbook) is None
    assert re.search(r"\bnamespace(?: ID)?[ :=]+\d{6,}\b", runbook) is None
    assert re.search(
        r"\bjob_[a-z0-9-]+_\d{8}T\d{6}Z_[a-z0-9]+\b",
        runbook,
    ) is None
