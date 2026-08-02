from __future__ import annotations

from pathlib import Path


UNIT = Path("deploy/systemd/user/vital-shevron-control-plane.service.example")
NGINX = Path("deploy/nginx/vital-shevron-control-plane.location.conf.example")
ENV = Path("deploy/env/vital-shevron-control-plane.env.example")


def test_control_unit_is_isolated_and_does_not_start_another_worker() -> None:
    unit = UNIT.read_text(encoding="utf-8")
    assert "WorkingDirectory=/home/pavel/projects/seller_vital_shevron" in unit
    assert "python -m seller_agent.control_plane" in unit
    assert "127.0.0.1" not in unit
    assert "job-worker" not in unit
    assert "ProtectSystem=strict" in unit
    assert "ProtectHome=tmpfs" in unit
    assert "NoNewPrivileges=true" in unit
    assert "UnsetEnvironment=OZON_" in unit
    assert "PARSER_DATA_API_TOKEN" in unit
    assert ".sessions/ozon" in unit and ".sessions/wb" in unit
    assert "BindReadOnlyPaths=-/home/pavel/projects/seller_vital_shevron" in unit
    assert "BindPaths=-/home/pavel/projects/seller_vital_shevron/runtime" in unit
    assert "BindPaths=-/home/pavel/projects/seller_vital_shevron/.sessions/telegram" in unit


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
