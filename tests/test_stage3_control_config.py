from __future__ import annotations

from pathlib import Path

import pytest

from seller_agent.control_plane.config import ControlPlaneConfig


def _secret(path: Path, value: str) -> Path:
    path.write_text(value, encoding="utf-8")
    path.chmod(0o600)
    return path


def test_control_config_uses_separate_0600_secrets_and_exact_server_ids(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    token_file = _secret(tmp_path / "bot-token", "123456:test-control-token\n")
    session_file = _secret(tmp_path / "session-secret", "s" * 48)
    monkeypatch.setenv("VITAL_SHEVRON_CONTROL_BOT_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("VITAL_SHEVRON_CONTROL_SESSION_SECRET_FILE", str(session_file))
    monkeypatch.setenv("VITAL_SHEVRON_CONTROL_OWNER_IDS", "42")
    monkeypatch.setenv(
        "VITAL_SHEVRON_CONTROL_PUBLIC_URL",
        "https://example.test/vital-shevron/",
    )

    config = ControlPlaneConfig.from_environment()

    assert config.allowed_owner_ids == frozenset({42})
    assert config.wb_supplier_id == "4516781"
    assert config.ozon_seller_slug == "vital-shevron"
    assert "test-control-token" not in repr(config)


def test_control_config_rejects_permissive_secret_and_non_https_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    token_file = _secret(tmp_path / "bot-token", "test-token")
    session_file = _secret(tmp_path / "session-secret", "s" * 48)
    token_file.chmod(0o644)
    monkeypatch.setenv("VITAL_SHEVRON_CONTROL_BOT_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("VITAL_SHEVRON_CONTROL_SESSION_SECRET_FILE", str(session_file))
    monkeypatch.setenv("VITAL_SHEVRON_CONTROL_OWNER_IDS", "42")
    monkeypatch.setenv(
        "VITAL_SHEVRON_CONTROL_PUBLIC_URL",
        "https://example.test/vital-shevron/",
    )
    with pytest.raises(PermissionError, match="0600"):
        ControlPlaneConfig.from_environment()

    token_file.chmod(0o600)
    monkeypatch.setenv("VITAL_SHEVRON_CONTROL_PUBLIC_URL", "http://example.test/")
    with pytest.raises(ValueError, match="HTTPS"):
        ControlPlaneConfig.from_environment()
