from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import sqlite3

import pytest

from seller_agent import cli
from seller_agent.bot import commands, job_notifier, runtime_jobs, telegram_runner
from seller_agent.bot.job_notifier import notify_telegram_job_result
from seller_agent.bot.runtime_jobs import dispatch_runtime_job_callback
from seller_agent.bot.telegram_runner import poll_once
from seller_agent.core.job_models import ApprovalRecord
from seller_agent.core.job_store import (
    APPROVAL_CALLBACK_TOKENS_MIGRATION,
    ApprovalCallbackTokenCollisionError,
    JobStore,
    approval_callback_token,
)
from seller_agent.safety.approval_package import build_approval_package
from seller_agent.tasks.registry import default_task_registry


def _approval_package(
    *,
    approval_id: str,
    task_id: str = "ozon-elastic-apply",
    source_plan_task: str = "ozon-elastic-plan",
    verify_task: str = "ozon-elastic-verify",
    source_kind: str = "plan_run_id",
    source_ref: str = "plan-approved",
    marketplaces: tuple[str, ...] = ("ozon",),
) -> dict[str, object]:
    return build_approval_package(
        approval_id=approval_id,
        task_id=task_id,
        source_plan_task=source_plan_task,
        verify_task=verify_task,
        source_kind=source_kind,
        source_ref=json.dumps(source_ref),
        apply_params={source_kind: source_ref},
        marketplaces=marketplaces,
    )


WRITE_TASK_ALIAS_CASES = tuple(
    (alias, task.name, task.enabled)
    for task in default_task_registry().list()
    if task.is_write
    for alias in dict.fromkeys((task.name, task.command, *task.aliases))
)


def _telegram_update_mutation_snapshot(
    runtime_db: Path,
    update_id: int,
) -> tuple[str, str, str, str]:
    with sqlite3.connect(runtime_db) as connection:
        row = connection.execute(
            """
            SELECT processing_status, job_id, payload_json, updated_at
            FROM telegram_updates
            WHERE update_id = ?
            """,
            (update_id,),
        ).fetchone()
    assert row is not None
    return tuple(str(value) for value in row)  # type: ignore[return-value]


@pytest.mark.parametrize(
    ("action", "initial_status", "final_status", "processing_status", "job_task"),
    [
        ("apa", "pending_review", "approved", "approval_approved", ""),
        ("apr", "pending_review", "rejected", "approval_rejected", ""),
        ("app", "approved", "approved", "queued", "ozon-elastic-apply"),
        ("apv", "applied", "applied", "queued", "ozon-elastic-verify"),
    ],
)
def test_every_approval_callback_is_registered_and_deduplicated_before_side_effect(
    action: str,
    initial_status: str,
    final_status: str,
    processing_status: str,
    job_task: str,
    tmp_path: Path,
) -> None:
    runtime_db = tmp_path / f"{action}.db"
    store = JobStore(runtime_db)
    approval_id = f"approval-{action}"
    package = _approval_package(approval_id=approval_id)
    store.create_approval(
        approval_id=approval_id,
        source_job_id="plan-job",
        status=initial_status,  # type: ignore[arg-type]
        checksum=str(package["approval_checksum"]),
        data=package,
    )
    token = approval_callback_token(approval_id)

    first = dispatch_runtime_job_callback(
        f"{action}:{token}",
        update_id=8100,
        chat_id=123,
        thread_id=55,
        data_dir=tmp_path / "data",
        runtime_db=runtime_db,
    )
    approval_after_first = store.get_approval(approval_id)
    jobs_after_first = store.list_jobs(limit=10)
    second = dispatch_runtime_job_callback(
        f"{action}:{token}",
        update_id=8100,
        chat_id=123,
        thread_id=55,
        data_dir=tmp_path / "data",
        runtime_db=runtime_db,
    )

    assert first is not None and first.ok is True
    assert second is not None and second.ok is True
    assert store.get_approval(approval_id) == approval_after_first
    assert approval_after_first is not None
    assert approval_after_first.status == final_status
    assert store.list_jobs(limit=10) == jobs_after_first
    assert len(jobs_after_first) == (1 if job_task else 0)
    if job_task:
        assert jobs_after_first[0].task_id == job_task
    update = store.get_telegram_update(8100)
    assert update is not None
    assert update.processing_status == processing_status
    assert update.job_id == (jobs_after_first[0].job_id if job_task else "")


@pytest.mark.parametrize(
    ("prefix", "task_id"),
    tuple(sorted(runtime_jobs.LEGACY_WRITE_CALLBACK_TASKS.items())),
)
def test_every_legacy_write_callback_requires_exact_existing_approval_id(
    prefix: str,
    task_id: str,
    tmp_path: Path,
) -> None:
    runtime_db = tmp_path / "runtime.db"
    store = JobStore(runtime_db)
    task = default_task_registry().get(task_id)
    source_kind = "source_run_id" if task.source_plan_task.endswith("inbox") else "plan_run_id"
    source_ref = f"source-{task_id}"

    blocked = dispatch_runtime_job_callback(
        f"{prefix}legacy-plan-without-approval",
        update_id=8200,
        chat_id=123,
        data_dir=tmp_path / "data",
        runtime_db=runtime_db,
    )

    assert blocked is not None and blocked.ok is False
    assert blocked.blocked_reason == "runtime_approval_required"
    assert store.list_jobs(limit=10) == []
    blocked_update = store.get_telegram_update(8200)
    assert blocked_update is not None
    assert blocked_update.processing_status == "approval_required"

    approval_id = f"approval-existing-{task_id}"
    package = _approval_package(
        approval_id=approval_id,
        task_id=task.name,
        source_plan_task=task.source_plan_task,
        verify_task=task.verify_task,
        source_kind=source_kind,
        source_ref=source_ref,
        marketplaces=task.marketplaces,
    )
    store.create_approval(
        approval_id=approval_id,
        source_job_id="plan-job",
        status="approved",
        checksum=str(package["approval_checksum"]),
        data=package,
    )
    accepted = dispatch_runtime_job_callback(
        f"{prefix}{approval_id}",
        update_id=8201,
        chat_id=123,
        data_dir=tmp_path / "data",
        runtime_db=runtime_db,
    )

    assert accepted is not None and accepted.ok is True
    jobs = store.list_jobs(limit=10)
    assert len(jobs) == 1
    assert jobs[0].task_id == task.name
    assert jobs[0].params["approval_id"] == approval_id
    assert jobs[0].params[source_kind] == source_ref


def test_poll_registers_read_only_updates_and_deduplicates_callback_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dispatched: list[str] = []
    original_dispatch = telegram_runner.dispatch_callback

    def counted_dispatch(data: str, **kwargs: object):  # type: ignore[no-untyped-def]
        dispatched.append(data)
        return original_dispatch(data, **kwargs)

    monkeypatch.setattr(telegram_runner, "dispatch_callback", counted_dispatch)

    def fake_api(token: str, method: str, payload: dict) -> dict:
        if method == "getUpdates":
            callback = {
                "update_id": 8300,
                "callback_query": {
                    "id": "read-only-callback",
                    "data": "opm_period:30",
                    "message": {"chat": {"id": 123}},
                },
            }
            return {"ok": True, "result": [callback, callback]}
        return {"ok": True, "result": {"message_id": 1}}

    runtime_db = tmp_path / "runtime.db"
    result = poll_once(
        token="test-token",
        data_dir=tmp_path / "data",
        state_file=tmp_path / "state.json",
        allowed_chat_ids={123},
        runtime_jobs=False,
        runtime_db=runtime_db,
        api_request=fake_api,
    )

    assert result["processed_updates"] == 2
    assert dispatched == ["opm_period:30"]
    updates = JobStore(runtime_db).list_telegram_updates(limit=10)
    assert len(updates) == 1
    assert updates[0].processing_status == "processed"
    assert updates[0].job_id == ""


def test_duplicate_message_preserves_existing_conversation_without_redispatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime_db = tmp_path / "runtime.db"
    state_file = tmp_path / "state.json"
    update_id = 8350
    conversation_key = "123:55"
    conversation = {
        "stage": "ozon_pricing_margin_input",
        "period_days": 30,
        "unit_cost": "85.50",
    }
    state_file.write_text(
        json.dumps(
            {
                "offset": update_id + 1,
                "conversations": {conversation_key: conversation},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    store = JobStore(runtime_db)
    assert store.register_telegram_update(
        update_id=update_id,
        chat_id="123",
        command="message",
        job_id="job-owned-by-first-message-handler",
        payload={
            "kind": "message",
            "message": "120",
            "thread_id": 55,
        },
        processing_status="received",
    ) is True
    with sqlite3.connect(runtime_db) as connection:
        connection.execute(
            "UPDATE telegram_updates SET updated_at = ? WHERE update_id = ?",
            ("2026-08-01T00:00:00Z", update_id),
        )
    update_before = store.get_telegram_update(update_id)
    mutation_snapshot_before = _telegram_update_mutation_snapshot(runtime_db, update_id)
    direct_dispatches: list[str] = []
    runtime_dispatches: list[str] = []

    def forbidden_direct_dispatch(message: str, **kwargs: object):  # type: ignore[no-untyped-def]
        direct_dispatches.append(message)
        pytest.fail("duplicate message must not reach direct dispatcher")

    def forbidden_runtime_dispatch(message: str, **kwargs: object):  # type: ignore[no-untyped-def]
        runtime_dispatches.append(message)
        pytest.fail("duplicate message must not reach runtime dispatcher")

    monkeypatch.setattr(telegram_runner, "dispatch_message", forbidden_direct_dispatch)
    monkeypatch.setattr(
        telegram_runner,
        "dispatch_runtime_job_message",
        forbidden_runtime_dispatch,
    )

    def fake_api(token: str, method: str, payload: dict) -> dict:
        if method == "getUpdates":
            return {
                "ok": True,
                "result": [
                    {
                        "update_id": update_id,
                        "message": {
                            "chat": {"id": 123},
                            "text": "120",
                            "message_thread_id": 55,
                        },
                    }
                ],
            }
        return {"ok": True, "result": {"message_id": 1}}

    result = poll_once(
        token="test-token",
        data_dir=tmp_path / "data",
        state_file=state_file,
        allowed_chat_ids={123},
        runtime_jobs=True,
        runtime_db=runtime_db,
        api_request=fake_api,
    )

    state_after = json.loads(state_file.read_text(encoding="utf-8"))
    assert result["processed_updates"] == 1
    assert direct_dispatches == []
    assert runtime_dispatches == []
    assert state_after["conversations"] == {conversation_key: conversation}
    assert store.get_telegram_update(update_id) == update_before
    assert _telegram_update_mutation_snapshot(runtime_db, update_id) == mutation_snapshot_before
    assert store.list_jobs(limit=10) == []


def test_duplicate_callback_does_not_finalize_foreign_delegated_update(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime_db = tmp_path / "runtime.db"
    update_id = 8360
    store = JobStore(runtime_db)
    assert store.register_telegram_update(
        update_id=update_id,
        chat_id="123",
        command="callback",
        job_id="job-owned-by-first-callback-handler",
        payload={
            "kind": "callback",
            "callback_data": "opm_period:30",
            "thread_id": 55,
        },
        processing_status="delegated_read_only",
    ) is True
    with sqlite3.connect(runtime_db) as connection:
        connection.execute(
            "UPDATE telegram_updates SET updated_at = ? WHERE update_id = ?",
            ("2026-08-01T00:00:00Z", update_id),
        )
    update_before = store.get_telegram_update(update_id)
    mutation_snapshot_before = _telegram_update_mutation_snapshot(runtime_db, update_id)
    direct_dispatches: list[str] = []
    runtime_dispatches: list[str] = []

    def forbidden_direct_dispatch(data: str, **kwargs: object):  # type: ignore[no-untyped-def]
        direct_dispatches.append(data)
        pytest.fail("duplicate callback must not reach direct dispatcher")

    def forbidden_runtime_dispatch(data: str, **kwargs: object):  # type: ignore[no-untyped-def]
        runtime_dispatches.append(data)
        pytest.fail("duplicate callback must not reach runtime dispatcher")

    monkeypatch.setattr(telegram_runner, "dispatch_callback", forbidden_direct_dispatch)
    monkeypatch.setattr(
        telegram_runner,
        "dispatch_runtime_job_callback",
        forbidden_runtime_dispatch,
    )

    def fake_api(token: str, method: str, payload: dict) -> dict:
        if method == "getUpdates":
            return {
                "ok": True,
                "result": [
                    {
                        "update_id": update_id,
                        "callback_query": {
                            "id": "duplicate-callback",
                            "data": "opm_period:30",
                            "message": {
                                "chat": {"id": 123},
                                "message_thread_id": 55,
                            },
                        },
                    }
                ],
            }
        return {"ok": True, "result": {"message_id": 1}}

    result = poll_once(
        token="test-token",
        data_dir=tmp_path / "data",
        state_file=tmp_path / "state.json",
        allowed_chat_ids={123},
        runtime_jobs=True,
        runtime_db=runtime_db,
        api_request=fake_api,
    )

    assert result["processed_updates"] == 1
    assert direct_dispatches == []
    assert runtime_dispatches == []
    assert store.get_telegram_update(update_id) == update_before
    assert _telegram_update_mutation_snapshot(runtime_db, update_id) == mutation_snapshot_before
    assert store.list_jobs(limit=10) == []


def test_enabled_cli_write_enqueues_existing_approval_without_direct_handler(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    runtime_db = tmp_path / "runtime" / "runtime.db"
    store = JobStore(runtime_db)
    approval_id = "approval-cli-elastic"
    package = _approval_package(
        approval_id=approval_id,
        source_ref="ozon_elastic_plan_cli",
    )
    store.create_approval(
        approval_id=approval_id,
        source_job_id="cli-plan-job",
        status="approved",
        checksum=str(package["approval_checksum"]),
        data=package,
    )

    exit_code = cli.main(
        [
            "apply-ozon-elastic",
            "--data-dir",
            str(tmp_path / "data"),
            "--plan-run-id",
            "ozon_elastic_plan_cli",
            "--confirmed-by-user",
            "--approval-id",
            approval_id,
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["status"] == "queued"
    jobs = store.list_jobs(limit=10)
    assert len(jobs) == 1
    assert jobs[0].task_id == "ozon-elastic-apply"
    assert jobs[0].actor == "cli"


@pytest.mark.parametrize(("alias", "task_id", "enabled"), WRITE_TASK_ALIAS_CASES)
def test_every_task_registry_write_alias_enqueues_or_fails_closed(
    alias: str,
    task_id: str,
    enabled: bool,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime_db = tmp_path / "runtime.db"
    task = default_task_registry().get(task_id)
    approval_id = f"approval-cli-{task_id}"
    can_build_approval = bool(task.enabled and task.source_plan_task and task.verify_task)
    if can_build_approval:
        package = build_approval_package(
            approval_id=approval_id,
            task_id=task.name,
            source_plan_task=task.source_plan_task,
            verify_task=task.verify_task,
            source_kind="params",
            source_ref="{}",
            apply_params={},
            marketplaces=task.marketplaces,
        )
        JobStore(runtime_db).create_approval(
            approval_id=approval_id,
            source_job_id="cli-plan-job",
            status="approved",
            checksum=str(package["approval_checksum"]),
            data=package,
        )
    args = argparse.Namespace(
        command=alias,
        data_dir=str(tmp_path / "data"),
        runtime_db=str(runtime_db),
        no_runtime_db=False,
        confirmed_by_user=False,
        approval_id=approval_id,
    )

    exit_code = cli._enqueue_cli_write(args)
    payload = json.loads(capsys.readouterr().out)
    jobs = JobStore(runtime_db).list_jobs(limit=10)

    assert default_task_registry().get(alias).name == task_id
    assert task.enabled is enabled
    if can_build_approval:
        assert exit_code == 0
        assert payload["status"] == "queued"
        assert len(jobs) == 1
        assert jobs[0].task_id == task_id
        assert jobs[0].status == "queued"
    else:
        assert exit_code == 2
        assert payload["status"] == "blocked"
        assert payload["blocked_reason"] in {"task_disabled", "runtime_enqueue_failed"}
        assert jobs == []


def test_runtime_plan_notification_uses_approval_callback_not_plan_run_id(tmp_path: Path) -> None:
    runtime_db = tmp_path / "runtime.db"
    store = JobStore(runtime_db)
    job = store.create_job(
        task_id="ozon-elastic-plan",
        actor="telegram:123",
        job_id="job-plan-notification",
        status="queued",
    )
    store.register_telegram_update(
        update_id=8400,
        chat_id="123",
        command="/elastic",
        job_id=job.job_id,
        processing_status="queued",
    )
    store.update_job_status(
        job.job_id,
        "success",
        result={
            "status": "ok",
            "summary": {
                "run_id": "ozon_elastic_plan_notification",
                "overall_status": "ok",
                "summary": {"add_to_action": 1},
                "artifacts": {},
            },
        },
    )
    approval_id = "approval-plan-notification"
    package = _approval_package(approval_id=approval_id, source_ref="ozon_elastic_plan_notification")
    store.create_approval(
        approval_id=approval_id,
        source_job_id=job.job_id,
        status="pending_review",
        checksum=str(package["approval_checksum"]),
        data=package,
    )
    calls: list[tuple[str, str, dict]] = []

    def fake_api(token: str, method: str, payload: dict) -> dict:
        calls.append((token, method, payload))
        return {"ok": True, "result": {"message_id": 1}}

    notification = notify_telegram_job_result(
        token="test-token",
        job_id=job.job_id,
        store=store,
        data_dir=tmp_path / "data",
        api_request=fake_api,
    )

    assert notification.ok is True
    send_payload = next(payload for _, method, payload in calls if method == "sendMessage")
    callback_data = send_payload["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    expected_token = approval_callback_token(approval_id)
    assert callback_data == f"apa:{expected_token}"
    assert "ozon_elastic_plan_notification" not in callback_data


def test_approval_lookup_migration_backfills_more_than_500_rows_and_preserves_history(
    tmp_path: Path,
) -> None:
    runtime_db = tmp_path / "legacy.db"
    target_approval_id = "approval-zz-target-after-500"
    source_job_id = "job-plan-after-500"
    package = _approval_package(
        approval_id=target_approval_id,
        source_ref="ozon_elastic_plan_after_500",
    )
    now = "2026-08-01T00:00:00Z"
    with sqlite3.connect(runtime_db) as connection:
        connection.execute(
            """
            CREATE TABLE approvals (
              approval_id TEXT PRIMARY KEY,
              source_job_id TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL,
              owner_job_id TEXT NOT NULL DEFAULT '',
              checksum TEXT NOT NULL DEFAULT '',
              data_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO approvals (
              approval_id, source_job_id, status, owner_job_id, checksum,
              data_json, created_at, updated_at
            ) VALUES (?, ?, ?, '', '', '{}', ?, ?)
            """,
            [
                (f"approval-{index:04d}", f"old-source-{index:04d}", "pending_review", now, now)
                for index in range(600)
            ],
        )
        connection.execute(
            """
            INSERT INTO approvals (
              approval_id, source_job_id, status, owner_job_id, checksum,
              data_json, created_at, updated_at
            ) VALUES (?, ?, 'pending_review', '', ?, ?, ?, ?)
            """,
            (
                target_approval_id,
                source_job_id,
                str(package["approval_checksum"]),
                json.dumps(package, ensure_ascii=False, sort_keys=True),
                now,
                now,
            ),
        )

    store = JobStore(runtime_db)
    store.initialize()
    job = store.create_job(
        task_id="ozon-elastic-plan",
        actor="telegram:123",
        job_id=source_job_id,
        status="queued",
    )
    store.register_telegram_update(
        update_id=8500,
        chat_id="123",
        command="/elastic",
        job_id=job.job_id,
        processing_status="queued",
    )
    store.update_job_status(
        job.job_id,
        "success",
        result={
            "status": "ok",
            "summary": {
                "run_id": "ozon_elastic_plan_after_500",
                "overall_status": "ok",
                "summary": {"add_to_action": 1},
                "artifacts": {},
            },
        },
    )
    calls: list[dict] = []

    def fake_api(token: str, method: str, payload: dict) -> dict:
        calls.append(payload)
        return {"ok": True, "result": {"message_id": 1}}

    notification = notify_telegram_job_result(
        token="test-token",
        job_id=job.job_id,
        store=store,
        data_dir=tmp_path / "data",
        api_request=fake_api,
    )
    token = approval_callback_token(target_approval_id)
    callback_data = calls[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    approved = dispatch_runtime_job_callback(
        f"apa:{token}",
        update_id=8501,
        chat_id=123,
        data_dir=tmp_path / "data",
        runtime_db=runtime_db,
    )

    assert notification.ok is True
    assert callback_data == f"apa:{token}"
    assert approved is not None and approved.ok is True
    assert store.get_approval(target_approval_id).status == "approved"  # type: ignore[union-attr]
    assert store.get_approval_by_source_job_id(source_job_id).approval_id == target_approval_id  # type: ignore[union-attr]
    assert store.get_approval_by_callback_token(token).approval_id == target_approval_id  # type: ignore[union-attr]
    assert target_approval_id not in {
        approval.approval_id for approval in store.list_approvals(limit=500)
    }
    assert len(store.list_approvals(limit=1000)) == 601
    with sqlite3.connect(runtime_db) as connection:
        mapping_count = connection.execute(
            "SELECT COUNT(*) FROM approval_callback_tokens"
        ).fetchone()[0]
        migration_count = connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE migration_name = ?",
            (APPROVAL_CALLBACK_TOKENS_MIGRATION,),
        ).fetchone()[0]
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
    assert mapping_count == 601
    assert migration_count == 1
    assert "idx_approval_callback_tokens_token" in indexes
    assert "idx_approvals_source_job_id" in indexes


def test_callback_token_collision_fails_closed_without_approval_or_job_mutation(
    tmp_path: Path,
) -> None:
    runtime_db = tmp_path / "runtime.db"
    store = JobStore(runtime_db)
    approval_ids = ("approval-collision-first", "approval-collision-second")
    for approval_id in approval_ids:
        package = _approval_package(approval_id=approval_id, source_ref=approval_id)
        store.create_approval(
            approval_id=approval_id,
            source_job_id=f"source-{approval_id}",
            status="pending_review",
            checksum=str(package["approval_checksum"]),
            data=package,
        )
    token = approval_callback_token(approval_ids[0])
    with sqlite3.connect(runtime_db) as connection:
        connection.execute(
            "UPDATE approval_callback_tokens SET callback_token = ? WHERE approval_id = ?",
            (token, approval_ids[1]),
        )

    with pytest.raises(ApprovalCallbackTokenCollisionError, match="ambiguous"):
        store.get_approval_by_callback_token(token)
    result = dispatch_runtime_job_callback(
        f"apa:{token}",
        update_id=8600,
        chat_id=123,
        data_dir=tmp_path / "data",
        runtime_db=runtime_db,
    )

    assert result is not None and result.ok is False
    assert result.blocked_reason == "invalid_runtime_callback"
    assert [store.get_approval(item).status for item in approval_ids] == [  # type: ignore[union-attr]
        "pending_review",
        "pending_review",
    ]
    assert store.list_jobs(limit=10) == []
    update = store.get_telegram_update(8600)
    assert update is not None
    assert update.processing_status == "approval_token_ambiguous"


def test_notifier_source_job_ambiguity_fails_closed_before_send(tmp_path: Path) -> None:
    runtime_db = tmp_path / "runtime.db"
    store = JobStore(runtime_db)
    job = store.create_job(
        task_id="ozon-elastic-plan",
        actor="telegram:123",
        job_id="job-plan-ambiguous-approval",
        status="queued",
    )
    store.register_telegram_update(
        update_id=8700,
        chat_id="123",
        command="/elastic",
        job_id=job.job_id,
        processing_status="queued",
    )
    store.update_job_status(job.job_id, "success", result={"status": "ok"})
    for suffix in ("first", "second"):
        approval_id = f"approval-source-ambiguous-{suffix}"
        package = _approval_package(approval_id=approval_id, source_ref=suffix)
        store.create_approval(
            approval_id=approval_id,
            source_job_id=job.job_id,
            status="pending_review",
            checksum=str(package["approval_checksum"]),
            data=package,
        )

    def forbidden_api(token: str, method: str, payload: dict) -> dict:
        raise AssertionError("ambiguous approval must fail before Telegram send")

    result = notify_telegram_job_result(
        token="test-token",
        job_id=job.job_id,
        store=store,
        data_dir=tmp_path / "data",
        api_request=forbidden_api,
    )

    assert result.ok is False
    assert result.blocked_reason == "ambiguous_runtime_approval"
    assert "Multiple runtime approvals" in result.error
    assert store.get_job(job.job_id).status == "success"  # type: ignore[union-attr]
    assert [
        approval.status
        for approval in store.list_approvals(statuses=("pending_review",), limit=10)
    ] == ["pending_review", "pending_review"]
    update = store.get_telegram_update(8700)
    assert update is not None
    assert update.processing_status == "notification_failed"
    assert update.job_id == job.job_id


def test_known_production_entrypoints_have_no_direct_apply_handler_calls() -> None:
    direct_handler_names = {
        "run_actions_apply",
        "run_apply_approved_card",
        "run_apply_approved_cards",
        "run_card_content_update_apply",
        "run_ozon_actions_optimizer_apply",
        "run_ozon_card_create_apply",
        "run_ozon_cpc_bids_apply",
        "run_ozon_elastic_apply",
        "run_ozon_inbox_apply",
        "run_ozon_partial_approved_recovery_apply",
        "run_ozon_product_remove_apply",
        "run_reviews_questions_apply",
        "run_seller_sku_update_apply",
        "run_wb_actions_discount_apply",
        "run_wb_card_create_apply",
        "run_wb_inbox_apply",
        "run_wb_promotion_bid_parser_enriched_apply",
        "run_wb_promotion_bids_apply",
    }
    for module in (cli, commands, runtime_jobs, telegram_runner):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert called.isdisjoint(direct_handler_names), module.__name__

    commands_tree = ast.parse(Path(commands.__file__).read_text(encoding="utf-8"))
    synchronous_helpers = {"_run_plan_apply_job", "_run_inbox_apply_job"}
    entrypoint = next(
        node
        for node in commands_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "handle_telegram_callback"
    )
    entrypoint_calls = {
        node.func.id
        for node in ast.walk(entrypoint)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert entrypoint_calls.isdisjoint(synchronous_helpers)


def test_production_bot_does_not_generate_legacy_write_callback_buttons() -> None:
    legacy_prefixes = tuple(runtime_jobs.LEGACY_WRITE_CALLBACK_TASKS)
    bot_source_dir = Path(commands.__file__).parent
    offenders: list[str] = []

    for source_path in sorted(bot_source_dir.rglob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values, strict=True):
                if not (
                    isinstance(key, ast.Constant)
                    and key.value == "callback_data"
                ):
                    continue
                generated_value = ast.unparse(value)
                for prefix in legacy_prefixes:
                    if prefix in generated_value:
                        offenders.append(
                            f"{source_path.relative_to(bot_source_dir)}:{node.lineno}:{prefix}"
                        )

    assert offenders == []


def test_runtime_job_notifier_generates_only_real_approval_token_callbacks() -> None:
    approval_id = "approval-notifier-callback-contract"
    token = approval_callback_token(approval_id)
    cases = {
        "pending_review": {f"apa:{token}", f"apr:{token}"},
        "approved": {f"app:{token}", f"apr:{token}"},
        "applied": {f"apv:{token}"},
        "applying_unknown": {f"apv:{token}"},
    }

    for status, expected_callbacks in cases.items():
        approval = ApprovalRecord(
            approval_id=approval_id,
            source_job_id="source-job",
            status=status,  # type: ignore[arg-type]
            created_at="2026-08-01T00:00:00Z",
            updated_at="2026-08-01T00:00:00Z",
        )
        markup = job_notifier._runtime_approval_markup(approval)
        callbacks = {
            button["callback_data"]
            for row in markup["inline_keyboard"]
            for button in row
        }

        assert callbacks == expected_callbacks
