from __future__ import annotations

from contextlib import nullcontext
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from seller_agent import cli
from seller_agent.core.job_store import JobStore
from seller_agent.core.resource_keys import (
    OZON_API_WRITE_KEY,
    OZON_LK_PROFILE_KEY,
    WB_API_WRITE_KEY,
    WB_LK_PROFILE_KEY,
    api_write_key,
    canonical_api_write_key,
    canonical_lk_profile_key,
    is_canonical_api_write_key,
    is_canonical_lk_profile_key,
    lk_profile_key,
)
from seller_agent.tasks.registry import default_task_registry
from seller_agent.sessions import manager as session_manager
from scripts.systemd.with_resource_lease import resolve_resource_key


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LEGACY_RESOURCE_LITERAL = re.compile(
    r"(?:lk:(?:ozon:session-check|wb:browser-profile)|['\"](?:ozon-lk|wb-lk|marketplace:ozon|marketplace:wb)['\"])"
)

PROFILE_CONSUMERS = {
    "ozon": (
        "status-preflight",
        "daily-morning-report",
        "ozon-lk-state-monitor",
        "ozon-inbox",
        "ozon-inbox-apply",
        "reviews-questions",
        "reviews-questions-verify",
    ),
    "wb": (
        "status-preflight",
        "daily-morning-report",
        "wb-pricing-margin",
        "wb-actions-discount-plan",
        "wb-actions-discount-apply",
        "wb-best-price-action-plan",
        "wb-best-price-action-apply",
        "wb-inbox",
        "wb-inbox-apply",
        "reviews-questions",
        "reviews-questions-verify",
    ),
}


def test_canonical_resource_key_factories_are_exact_and_fail_closed() -> None:
    assert lk_profile_key("ozon", "chrome-profile") == "lk:ozon:profile:chrome-profile"
    assert lk_profile_key("wb", "browser-profile") == "lk:wb:profile:browser-profile"
    assert api_write_key("ozon", "vital-shevron") == "api:ozon:vital-shevron:write"
    assert api_write_key("wb", "vital-shevron") == "api:wb:vital-shevron:write"
    assert canonical_lk_profile_key("ozon") == OZON_LK_PROFILE_KEY
    assert canonical_lk_profile_key("wb") == WB_LK_PROFILE_KEY
    assert canonical_api_write_key("ozon") == OZON_API_WRITE_KEY
    assert canonical_api_write_key("wb") == WB_API_WRITE_KEY

    for invalid_marketplace in ("", "all", "marketplace:ozon"):
        with pytest.raises(ValueError):
            canonical_lk_profile_key(invalid_marketplace)
        with pytest.raises(ValueError):
            canonical_api_write_key(invalid_marketplace)
    for invalid_identifier in ("", "../profile", "profile:other", "white space"):
        with pytest.raises(ValueError):
            lk_profile_key("ozon", invalid_identifier)
        with pytest.raises(ValueError):
            api_write_key("ozon", invalid_identifier)


@pytest.mark.parametrize("marketplace", ("ozon", "wb"))
def test_every_known_profile_consumer_uses_the_exact_shared_key(marketplace: str) -> None:
    registry = default_task_registry()
    expected = canonical_lk_profile_key(marketplace)

    for task_id in PROFILE_CONSUMERS[marketplace]:
        task = registry.get(task_id)
        assert task.enabled is True
        assert expected in task.lock_keys, task_id


def test_registry_has_only_canonical_lk_and_api_write_keys() -> None:
    registry = default_task_registry()
    canonical_profiles = {OZON_LK_PROFILE_KEY, WB_LK_PROFILE_KEY}
    canonical_api_writes = {OZON_API_WRITE_KEY, WB_API_WRITE_KEY}

    for task in registry.list():
        for key in task.lock_keys:
            if key.startswith("lk:"):
                assert is_canonical_lk_profile_key(key), (task.name, key)
                assert key in canonical_profiles, (task.name, key)
            if key.startswith("api:"):
                assert is_canonical_api_write_key(key), (task.name, key)
                assert key in canonical_api_writes, (task.name, key)
                assert task.enabled is True
                assert task.mode == "apply", (task.name, key)
        assert "ozon-lk" not in task.lock_keys
        assert "wb-lk" not in task.lock_keys
        assert "marketplace:ozon" not in task.lock_keys
        assert "marketplace:wb" not in task.lock_keys


def test_production_sources_and_units_have_no_legacy_resource_key_literals() -> None:
    paths = (
        *PROJECT_ROOT.joinpath("src").rglob("*.py"),
        *PROJECT_ROOT.joinpath("scripts").rglob("*.py"),
        *PROJECT_ROOT.joinpath("scripts").rglob("*.sh"),
        *PROJECT_ROOT.joinpath("deploy", "systemd", "user").glob("*.service"),
    )

    for path in paths:
        contents = path.read_text(encoding="utf-8")
        assert LEGACY_RESOURCE_LITERAL.search(contents) is None, path.relative_to(PROJECT_ROOT)


def test_enabled_apply_tasks_get_only_their_marketplace_api_write_keys() -> None:
    registry = default_task_registry()

    for task in registry.list():
        actual = {key for key in task.lock_keys if is_canonical_api_write_key(key)}
        expected = (
            {canonical_api_write_key(marketplace) for marketplace in task.marketplaces}
            if task.enabled and task.mode == "apply"
            else set()
        )
        assert actual == expected, task.name


def test_exact_leases_conflict_only_for_the_same_mutable_resource(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "runtime.db")

    assert store.acquire_resource_lease(
        resource_key=OZON_LK_PROFILE_KEY,
        owner_id="ozon-profile-owner",
        ttl_seconds=60,
    ) is not None
    assert store.acquire_resource_lease(
        resource_key=OZON_LK_PROFILE_KEY,
        owner_id="other-ozon-profile-owner",
        ttl_seconds=60,
    ) is None
    assert store.acquire_resource_lease(
        resource_key=WB_LK_PROFILE_KEY,
        owner_id="wb-profile-owner",
        ttl_seconds=60,
    ) is not None
    assert store.acquire_resource_lease(
        resource_key=OZON_API_WRITE_KEY,
        owner_id="ozon-api-owner",
        ttl_seconds=60,
    ) is not None
    assert store.acquire_resource_lease(
        resource_key=OZON_API_WRITE_KEY,
        owner_id="other-ozon-api-owner",
        ttl_seconds=60,
    ) is None
    assert store.acquire_resource_lease(
        resource_key=WB_API_WRITE_KEY,
        owner_id="wb-api-owner",
        ttl_seconds=60,
    ) is not None


@pytest.mark.parametrize(
    ("marketplace", "unit_name"),
    (
        ("ozon", "vital-shevron-ozon-session-refresh.service"),
        ("wb", "vital-shevron-wb-session-refresh.service"),
    ),
)
def test_session_refresh_unit_uses_short_canonical_profile_lease(
    marketplace: str,
    unit_name: str,
) -> None:
    unit = (PROJECT_ROOT / "deploy" / "systemd" / "user" / unit_name).read_text(encoding="utf-8")

    assert "Type=oneshot" in unit
    assert "scripts/systemd/with_resource_lease.py" in unit
    assert f"--lk-profile {marketplace}" in unit
    assert "--runtime-db /home/pavel/projects/seller_vital_shevron/runtime/runtime.db" in unit
    assert "--resource-key" not in unit
    assert "lk:wb:browser-profile" not in unit
    assert "lk:ozon:session-check" not in unit


def test_external_lease_wrapper_resolves_profile_alias_from_canonical_module() -> None:
    assert resolve_resource_key(resource_key=None, lk_profile="ozon") == OZON_LK_PROFILE_KEY
    assert resolve_resource_key(resource_key=None, lk_profile="wb") == WB_LK_PROFILE_KEY
    with pytest.raises(ValueError):
        resolve_resource_key(resource_key="subject:lock", lk_profile="ozon")
    with pytest.raises(ValueError):
        resolve_resource_key(resource_key=None, lk_profile=None)
    for legacy_key in ("ozon-lk", "wb-lk", "lk:ozon:session-check", "lk:wb:browser-profile"):
        with pytest.raises(ValueError):
            resolve_resource_key(resource_key=legacy_key, lk_profile=None)


def test_external_profile_lease_is_released_after_the_short_operation(tmp_path: Path) -> None:
    runtime_db = tmp_path / "runtime.db"
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{PROJECT_ROOT / 'src'}:{PROJECT_ROOT}"

    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "systemd" / "with_resource_lease.py"),
            "--lk-profile",
            "ozon",
            "--runtime-db",
            str(runtime_db),
            "--wait-seconds",
            "0",
            "--ttl-seconds",
            "60",
            "--",
            "/usr/bin/true",
        ],
        cwd=PROJECT_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    assert JobStore(runtime_db).acquire_resource_lease(
        resource_key=OZON_LK_PROFILE_KEY,
        owner_id="after-short-refresh",
        ttl_seconds=60,
    ) is not None


@pytest.mark.parametrize(
    ("marketplace", "script_name"),
    (
        ("ozon", "ozon_session_refresh.sh"),
        ("wb", "wb_daily_session_refresh.sh"),
    ),
)
def test_manual_session_refresh_wrapper_uses_the_same_short_profile_lease(
    marketplace: str,
    script_name: str,
) -> None:
    script = (PROJECT_ROOT / "scripts" / "sessions" / script_name).read_text(encoding="utf-8")

    assert "scripts/systemd/with_resource_lease.py" in script
    assert f"--lk-profile {marketplace}" in script
    assert "--runtime-db" in script
    assert "runtime/runtime.db" in script
    assert "SELLER_PROFILE_LEASE_HELD" in script
    assert "lk:wb:browser-profile" not in script
    assert "lk:ozon:session-check" not in script


def test_persistent_browser_host_does_not_hold_the_operation_lease() -> None:
    keeper = (
        PROJECT_ROOT / "deploy" / "systemd" / "user" / "vital-shevron-ozon-keeper.service"
    ).read_text(encoding="utf-8")

    assert "Type=simple" in keeper
    assert "Restart=always" in keeper
    assert "with_resource_lease.py" not in keeper
    assert "--lk-profile" not in keeper
    manual_host = (
        PROJECT_ROOT / "scripts" / "sessions" / "open_ozon_seller_agent.sh"
    ).read_text(encoding="utf-8")
    assert "exec google-chrome" in manual_host
    assert "with_resource_lease.py" not in manual_host


@pytest.mark.parametrize("marketplace", ("ozon", "wb"))
@pytest.mark.parametrize("action", ("start", "stop", "restart"))
def test_direct_session_manager_mutation_conflicts_and_then_releases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    marketplace: str,
    action: str,
) -> None:
    runtime_db = tmp_path / "runtime.db"
    resource_key = canonical_lk_profile_key(marketplace)
    store = JobStore(runtime_db)
    assert store.acquire_resource_lease(
        resource_key=resource_key,
        owner_id="held-by-job-worker",
        ttl_seconds=60,
    ) is not None
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        session_manager,
        "_run_session_scripts",
        lambda selected, selected_action: calls.append((selected, selected_action)) or [{"status": "ok"}],
    )
    monkeypatch.setattr(
        session_manager,
        "combined_session_status",
        lambda selected: {"overall_status": "ok", "sessions": {item: {} for item in selected}},
    )

    blocked = session_manager.run_session_manager(
        action=action,
        marketplace=marketplace,
        data_dir=tmp_path / "data",
        runtime_db=runtime_db,
    )

    assert blocked["overall_status"] == "blocked"
    assert blocked["blocked_reason"] == "resource_lease_busy"
    assert blocked["resource_keys"] == [resource_key]
    assert blocked["operations"] == {}
    assert calls == []

    assert store.release_resource_lease(
        resource_key=resource_key,
        owner_id="held-by-job-worker",
    ) is True
    completed = session_manager.run_session_manager(
        action=action,
        marketplace=marketplace,
        data_dir=tmp_path / "data",
        run_id=f"sessions-{marketplace}-{action}-released",
        runtime_db=runtime_db,
    )

    assert completed["overall_status"] == "ok"
    assert calls == (
        [(marketplace, "stop"), (marketplace, "start")]
        if action == "restart"
        else [(marketplace, action)]
    )
    assert store.acquire_resource_lease(
        resource_key=resource_key,
        owner_id="after-session-operation",
        ttl_seconds=60,
    ) is not None


def test_direct_session_manager_all_marketplaces_claims_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_db = tmp_path / "runtime.db"
    store = JobStore(runtime_db)
    assert store.acquire_resource_lease(
        resource_key=WB_LK_PROFILE_KEY,
        owner_id="held-wb-profile",
        ttl_seconds=60,
    ) is not None
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        session_manager,
        "_run_session_scripts",
        lambda marketplace, action: calls.append((marketplace, action)) or [],
    )
    monkeypatch.setattr(
        session_manager,
        "combined_session_status",
        lambda selected: {"overall_status": "ok", "sessions": {item: {} for item in selected}},
    )

    result = session_manager.run_session_manager(
        action="start",
        marketplace="all",
        data_dir=tmp_path / "data",
        runtime_db=runtime_db,
    )

    assert result["overall_status"] == "blocked"
    assert result["resource_keys"] == [OZON_LK_PROFILE_KEY, WB_LK_PROFILE_KEY]
    assert calls == []
    assert store.acquire_resource_lease(
        resource_key=OZON_LK_PROFILE_KEY,
        owner_id="proves-no-partial-ozon-claim",
        ttl_seconds=60,
    ) is not None


def test_session_status_does_not_create_or_take_runtime_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_db = tmp_path / "not-created.db"
    monkeypatch.setattr(
        session_manager,
        "combined_session_status",
        lambda selected: {"overall_status": "ok", "sessions": {item: {} for item in selected}},
    )

    result = session_manager.run_session_manager(
        action="status",
        marketplace="ozon",
        data_dir=tmp_path / "data",
        runtime_db=runtime_db,
    )

    assert result["overall_status"] == "ok"
    assert runtime_db.exists() is False


def test_direct_restore_conflicts_before_cleanup_and_releases_afterward(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_db = tmp_path / "runtime.db"
    store = JobStore(runtime_db)
    assert store.acquire_resource_lease(
        resource_key=OZON_LK_PROFILE_KEY,
        owner_id="held-by-runtime-job",
        ttl_seconds=60,
    ) is not None
    calls: list[list[str]] = []
    monkeypatch.setattr(
        session_manager,
        "systemd_user_available",
        lambda: (_ for _ in ()).throw(AssertionError("must fail before systemd")),
    )
    monkeypatch.setattr(
        session_manager,
        "_run_command",
        lambda args, **kwargs: calls.append(args) or {"status": "ok", "returncode": 0},
    )
    monkeypatch.setattr(
        session_manager,
        "_cleanup_ozon_profile_processes",
        lambda: (_ for _ in ()).throw(AssertionError("must not clean a leased profile")),
    )

    blocked = session_manager.restore_ozon_session(
        data_dir=tmp_path / "data",
        runtime_db=runtime_db,
    )

    assert blocked["overall_status"] == "blocked"
    assert blocked["blocked_reason"] == "resource_lease_busy"
    assert blocked["resource_keys"] == [OZON_LK_PROFILE_KEY]
    assert blocked["operations"] == {}
    assert calls == []

    assert store.release_resource_lease(
        resource_key=OZON_LK_PROFILE_KEY,
        owner_id="held-by-runtime-job",
    ) is True
    monkeypatch.setattr(session_manager, "systemd_user_available", lambda: False)
    monkeypatch.setattr(session_manager, "file_lock", lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(
        session_manager,
        "_cleanup_ozon_profile_processes",
        lambda: {"status": "ok", "pids_before": [], "pids_after": [], "locks_removed": []},
    )
    monkeypatch.setattr(
        session_manager,
        "combined_session_status",
        lambda selected: {"overall_status": "ok", "sessions": {"ozon": {}}},
    )
    completed = session_manager.restore_ozon_session(
        data_dir=tmp_path / "data",
        run_id="restore-after-release",
        runtime_db=runtime_db,
    )

    assert completed["overall_status"] == "ok"
    assert calls
    assert store.acquire_resource_lease(
        resource_key=OZON_LK_PROFILE_KEY,
        owner_id="after-restore",
        ttl_seconds=60,
    ) is not None


def test_systemd_switch_fails_before_direct_session_mutation_when_profile_busy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_db = tmp_path / "runtime.db"
    store = JobStore(runtime_db)
    assert store.acquire_resource_lease(
        resource_key=OZON_LK_PROFILE_KEY,
        owner_id="held-ozon-profile",
        ttl_seconds=60,
    ) is not None
    monkeypatch.setattr(
        session_manager,
        "systemd_user_available",
        lambda: (_ for _ in ()).throw(AssertionError("must fail before systemd")),
    )

    result = session_manager.install_systemd_units(
        switch=True,
        dry_run=False,
        runtime_db=runtime_db,
    )

    assert result["overall_status"] == "blocked"
    assert result["blocked_reason"] == "resource_lease_busy"
    assert result["resource_keys"] == [OZON_LK_PROFILE_KEY, WB_LK_PROFILE_KEY]
    assert store.acquire_resource_lease(
        resource_key=WB_LK_PROFILE_KEY,
        owner_id="proves-no-partial-wb-claim",
        ttl_seconds=60,
    ) is not None


def test_systemd_switch_releases_both_short_profile_leases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_db = tmp_path / "runtime.db"
    commands: list[list[str]] = []
    store = JobStore(runtime_db)
    switch_was_exclusive: list[bool] = []
    refresh_started_after_release: list[str] = []

    def run_command(args: list[str], **kwargs: object) -> dict[str, object]:
        commands.append(args)
        refresh_service = next(
            (item for item in args if item.endswith("session-refresh.service")),
            None,
        )
        if refresh_service is None:
            if "daemon-reload" in args:
                switch_was_exclusive.append(
                    store.acquire_resource_leases(
                        resource_keys=[OZON_LK_PROFILE_KEY, WB_LK_PROFILE_KEY],
                        owner_id="probe-during-switch",
                        ttl_seconds=60,
                    )
                    is None
                )
            return {"status": "ok", "returncode": 0}

        marketplace = "ozon" if "ozon" in refresh_service else "wb"
        resource_key = canonical_lk_profile_key(marketplace)
        owner_id = f"probe-refresh-{marketplace}"
        assert store.acquire_resource_lease(
            resource_key=resource_key,
            owner_id=owner_id,
            ttl_seconds=60,
        ) is not None
        assert store.release_resource_lease(
            resource_key=resource_key,
            owner_id=owner_id,
        ) is True
        refresh_started_after_release.append(marketplace)
        return {"status": "ok", "returncode": 0}

    monkeypatch.setattr(session_manager, "SYSTEMD_TARGET_DIR", tmp_path / "systemd-user")
    monkeypatch.setattr(session_manager, "systemd_user_available", lambda: True)
    monkeypatch.setattr(session_manager, "_run_command", run_command)

    result = session_manager.install_systemd_units(
        switch=True,
        dry_run=False,
        runtime_db=runtime_db,
    )

    assert result["overall_status"] == "ok"
    assert commands
    queued_refreshes = [
        command
        for command in commands
        if any(item.endswith("session-refresh.service") for item in command)
    ]
    assert len(queued_refreshes) == 2
    assert all("--no-block" not in command for command in queued_refreshes)
    assert switch_was_exclusive == [True]
    assert refresh_started_after_release == ["ozon", "wb"]
    assert store.acquire_resource_leases(
        resource_keys=[OZON_LK_PROFILE_KEY, WB_LK_PROFILE_KEY],
        owner_id="after-systemd-switch",
        ttl_seconds=60,
    ) is not None


@pytest.mark.parametrize("failed_marketplace", ("ozon", "wb"))
def test_systemd_switch_surfaces_synchronous_refresh_service_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_marketplace: str,
) -> None:
    runtime_db = tmp_path / "runtime.db"
    commands: list[list[str]] = []

    def run_command(args: list[str], **kwargs: object) -> dict[str, object]:
        commands.append(args)
        refresh_service = next(
            (item for item in args if item.endswith("session-refresh.service")),
            None,
        )
        if refresh_service and failed_marketplace in refresh_service:
            return {"status": "error", "returncode": 1, "error": "refresh_unit_failed"}
        return {"status": "ok", "returncode": 0}

    monkeypatch.setattr(session_manager, "SYSTEMD_TARGET_DIR", tmp_path / "systemd-user")
    monkeypatch.setattr(session_manager, "systemd_user_available", lambda: True)
    monkeypatch.setattr(session_manager, "_run_command", run_command)

    result = session_manager.install_systemd_units(
        switch=True,
        dry_run=False,
        runtime_db=runtime_db,
    )

    refresh_starts = [
        command
        for command in commands
        if any(item.endswith("session-refresh.service") for item in command)
    ]
    assert len(refresh_starts) == 2
    assert all("--no-block" not in command for command in refresh_starts)
    assert result["overall_status"] == "error"
    assert any(
        operation.get("status") == "error"
        and operation.get("error") == "refresh_unit_failed"
        for operation in result["operations"]
    )


@pytest.mark.parametrize(
    ("switch", "dry_run"),
    ((True, True), (False, False)),
)
def test_systemd_install_without_applied_switch_does_not_take_profile_leases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    switch: bool,
    dry_run: bool,
) -> None:
    runtime_db = tmp_path / "not-created.db"
    monkeypatch.setattr(session_manager, "SYSTEMD_TARGET_DIR", tmp_path / "systemd-user")
    monkeypatch.setattr(session_manager, "systemd_user_available", lambda: True)
    monkeypatch.setattr(
        session_manager,
        "_run_command",
        lambda args, **kwargs: {"status": "ok", "returncode": 0},
    )

    result = session_manager.install_systemd_units(
        switch=switch,
        dry_run=dry_run,
        runtime_db=runtime_db,
    )

    assert result["overall_status"] == "ok"
    assert runtime_db.exists() is False


def test_cli_session_mutations_forward_the_selected_runtime_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_db = tmp_path / "selected-runtime.db"
    calls: list[tuple[str, Path]] = []
    monkeypatch.setattr(
        cli,
        "run_session_manager",
        lambda **kwargs: calls.append(("sessions", kwargs["runtime_db"]))
        or {"overall_status": "ok"},
    )
    monkeypatch.setattr(
        cli,
        "restore_ozon_session",
        lambda **kwargs: calls.append(("restore", kwargs["runtime_db"]))
        or {"overall_status": "ok"},
    )
    monkeypatch.setattr(
        cli,
        "install_systemd_units",
        lambda **kwargs: calls.append(("systemd-switch", kwargs["runtime_db"]))
        or {"overall_status": "ok"},
    )

    assert cli.main(["sessions", "start", "--runtime-db", str(runtime_db)]) == 0
    assert cli.main(["restore-ozon-session", "--runtime-db", str(runtime_db)]) == 0
    assert (
        cli.main(
            [
                "install-session-systemd",
                "--apply",
                "--switch",
                "--runtime-db",
                str(runtime_db),
            ]
        )
        == 0
    )

    assert calls == [
        ("sessions", runtime_db),
        ("restore", runtime_db),
        ("systemd-switch", runtime_db),
    ]
    assert runtime_db.exists() is False


@pytest.mark.parametrize(
    ("marketplace", "script_name"),
    (
        ("ozon", "start_ozon_keeper.sh"),
        ("ozon", "stop_ozon_keeper.sh"),
        ("ozon", "start_ozon_session_watchdog.sh"),
        ("ozon", "stop_ozon_session_watchdog.sh"),
        ("wb", "start_wb_session_watchdog.sh"),
        ("wb", "stop_wb_session_watchdog.sh"),
    ),
)
def test_direct_session_control_script_uses_short_canonical_profile_lease(
    marketplace: str,
    script_name: str,
) -> None:
    script = (PROJECT_ROOT / "scripts" / "sessions" / script_name).read_text(encoding="utf-8")

    assert "scripts/systemd/with_resource_lease.py" in script
    assert f"--lk-profile {marketplace}" in script
    assert "--runtime-db" in script
    assert "SELLER_PROFILE_LEASE_HELD" in script
    if script_name.startswith("start_"):
        assert "env -u SELLER_PROFILE_LEASE_HELD" in script
