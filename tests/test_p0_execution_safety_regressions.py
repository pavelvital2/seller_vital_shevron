from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
from threading import Barrier, Lock

import pytest

from seller_agent.bot.runtime_jobs import dispatch_runtime_job_callback
from seller_agent.bot.telegram_runner import poll_once
from seller_agent.core.job_models import ApprovalRecord, JobRecord
from seller_agent.core.job_runner import JobRunner
from seller_agent.core.job_service import JobService
from seller_agent.core.job_store import JobStore
from seller_agent.core.workflow_runner import WorkflowRunner
from seller_agent.safety.approval_package import build_approval_package
from seller_agent.safety.guard import SafetyGuard
from seller_agent.safety.plan_approval import with_plan_approval_candidate
from seller_agent.tasks.registry import RegisteredTask, TaskRegistry, default_task_registry


MISSING_WRITE_HANDLERS = (
    "approved-card-apply",
    "card-content-update-apply",
    "ozon-card-create-apply",
    "ozon-partial-approved-recovery-apply",
    "ozon-product-remove-apply",
    "seller-sku-update-apply",
    "wb-card-create-apply",
    "ozon-messenger-workflow",
)


class _CoordinatedJobStore(JobStore):
    """Expose the current select-then-run race deterministically."""

    def __init__(self, db_path: Path, select_barrier: Barrier, read_barrier: Barrier) -> None:
        super().__init__(db_path)
        self._select_barrier = select_barrier
        self._read_barrier = read_barrier
        self._gate_service_read = False

    def next_queued_job(self) -> JobRecord | None:
        job = super().next_queued_job()
        if job is not None:
            self._gate_service_read = True
            self._select_barrier.wait(timeout=5)
        return job

    def get_job(self, job_id: str) -> JobRecord | None:
        job = super().get_job(job_id)
        if self._gate_service_read:
            self._gate_service_read = False
            self._read_barrier.wait(timeout=5)
        return job


def _approval_package(
    *,
    approval_id: str = "approval-p0",
    task_id: str = "ozon-elastic-apply",
    plan_run_id: str = "plan-approved",
) -> dict:
    task = default_task_registry().get(task_id)
    return build_approval_package(
        approval_id=approval_id,
        task_id=task.name,
        source_plan_task=task.source_plan_task,
        verify_task=task.verify_task,
        source_kind="plan_run_id",
        source_ref=plan_run_id,
        apply_params={"plan_run_id": plan_run_id},
        marketplaces=task.marketplaces,
    )


def test_two_workers_execute_one_queued_job_at_most_once(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    JobStore(db_path).create_job(task_id="pricing-status", status="queued")
    select_barrier = Barrier(2)
    read_barrier = Barrier(2)
    calls: list[str] = []
    calls_lock = Lock()

    def handler(task, data_dir, credentials, inputs):  # type: ignore[no-untyped-def]
        with calls_lock:
            calls.append(task.name)
        return {"run_id": "concurrent-worker-test", "overall_status": "ok", "artifacts": {}}

    runners: list[JobRunner] = []
    for index in range(2):
        store = _CoordinatedJobStore(db_path, select_barrier, read_barrier)
        workflow_runner = WorkflowRunner(
            data_dir=tmp_path / f"data-{index}",
            lock_dir=tmp_path / f"locks-{index}",
            handlers={"pricing-status": handler},
        )
        runners.append(
            JobRunner(
                JobService(
                    store=store,
                    workflow_runner=workflow_runner,
                    data_dir=tmp_path / f"data-{index}",
                )
            )
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda runner: runner.run_next(), runners))

    assert len(calls) == 1
    assert sum(result.ran for result in results) == 1


def test_duplicate_approval_callback_creates_one_job_and_one_update_record(tmp_path: Path) -> None:
    runtime_db = tmp_path / "runtime.db"
    store = JobStore(runtime_db)
    package = _approval_package()
    approval = store.create_approval(
        approval_id=package["approval_id"],
        source_job_id="plan-job",
        status="approved",
        checksum=package["approval_checksum"],
        data=package,
    )
    callback_data = "app:" + hashlib.sha256(approval.approval_id.encode("utf-8")).hexdigest()[:16]

    first = dispatch_runtime_job_callback(
        callback_data,
        update_id=7001,
        chat_id=123,
        runtime_db=runtime_db,
        data_dir=tmp_path / "data",
    )
    second = dispatch_runtime_job_callback(
        callback_data,
        update_id=7001,
        chat_id=123,
        runtime_db=runtime_db,
        data_dir=tmp_path / "data",
    )

    jobs = store.list_jobs(limit=10)
    update = store.get_telegram_update(7001)
    approval_after = store.get_approval(approval.approval_id)
    assert first is not None and first.ok is True
    assert second is not None and second.ok is True
    assert len(jobs) == 1
    assert update is not None
    assert update.job_id == jobs[0].job_id
    assert approval_after is not None
    assert approval_after.status == "approved"
    assert approval_after.updated_at == approval.updated_at


def test_write_callback_without_runtime_jobs_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from seller_agent.bot import commands

    direct_workflows: list[dict] = []

    def fake_direct_workflow(**kwargs: object) -> dict:
        direct_workflows.append(dict(kwargs))
        return {"overall_status": "ok"}

    monkeypatch.setattr(commands, "_run_plan_apply_job", fake_direct_workflow)
    sent_texts: list[str] = []

    def fake_api(token: str, method: str, payload: dict) -> dict:
        if method == "getUpdates":
            return {
                "ok": True,
                "result": [
                    {
                        "update_id": 7002,
                        "callback_query": {
                            "id": "callback-p0",
                            "data": "oe_apply:ozon_elastic_plan_approved",
                            "message": {"chat": {"id": 123}},
                        },
                    }
                ],
            }
        if method == "sendMessage":
            sent_texts.append(str(payload.get("text") or ""))
        return {"ok": True, "result": {"message_id": 1}}

    runtime_db = tmp_path / "runtime.db"
    result = poll_once(
        token="test-token",
        data_dir=tmp_path / "data",
        state_file=tmp_path / "telegram-state.json",
        allowed_chat_ids={123},
        runtime_jobs=False,
        runtime_db=runtime_db,
        api_request=fake_api,
    )

    assert direct_workflows == []
    assert JobStore(runtime_db).list_jobs() == []
    assert result["processed_updates"] == 1
    assert len(sent_texts) == 1
    assert "runtime" in sent_texts[0].lower()
    assert any(
        marker in sent_texts[0].lower()
        for marker in ("заблок", "отключ", "недоступ")
    )


def test_write_cli_route_does_not_call_marketplace_workflow_directly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from seller_agent import cli

    calls: list[dict] = []

    def fake_apply(**kwargs: object) -> dict:
        calls.append(dict(kwargs))
        return {"overall_status": "ok"}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "load_credentials", lambda: object())
    monkeypatch.setattr(cli, "run_actions_apply", fake_apply)

    cli.main(
        [
            "apply-actions",
            "--data-dir",
            str(tmp_path / "data"),
            "--pending-id",
            "approved-package",
            "--confirmed-by-user",
        ]
    )

    assert calls == []


def test_apply_without_unified_approval_package_is_blocked() -> None:
    task = default_task_registry().get("ozon-elastic-apply")
    approval = ApprovalRecord(
        approval_id="legacy-approval",
        status="approved",
        source_job_id="plan-job",
        created_at="2026-08-01T00:00:00+00:00",
        updated_at="2026-08-01T00:00:00+00:00",
        checksum="sha256:legacy",
        data={"task_id": task.name, "apply_params": {"plan_run_id": "plan-approved"}},
    )
    job = JobRecord(
        job_id="apply-job",
        task_id=task.name,
        status="queued",
        actor="owner",
        created_at="2026-08-01T00:00:00+00:00",
        updated_at="2026-08-01T00:00:00+00:00",
        params={
            "plan_run_id": "plan-approved",
            "confirmed_by_user": True,
            "approval_id": approval.approval_id,
            "approval_checksum": approval.checksum,
        },
    )

    decision = SafetyGuard().validate_apply(task=task, job=job, approval=approval)

    assert decision.allowed is False
    assert "approval_package_schema_invalid" in decision.issues


def test_apply_params_changed_after_approval_are_blocked() -> None:
    task = default_task_registry().get("ozon-elastic-apply")
    package = _approval_package(plan_run_id="plan-approved")
    approval = ApprovalRecord(
        approval_id=package["approval_id"],
        status="approved",
        source_job_id="plan-job",
        created_at=package["created_at"],
        updated_at=package["created_at"],
        checksum=package["approval_checksum"],
        data=package,
    )
    job = JobRecord(
        job_id="mutated-apply-job",
        task_id=task.name,
        status="queued",
        actor="owner",
        created_at=package["created_at"],
        updated_at=package["created_at"],
        params={
            "plan_run_id": "plan-mutated",
            "confirmed_by_user": True,
            "approval_id": approval.approval_id,
            "approval_checksum": approval.checksum,
        },
    )

    decision = SafetyGuard().validate_apply(task=task, job=job, approval=approval)

    assert decision.allowed is False
    assert "approval_params_mismatch" in decision.issues


def test_confirmed_flag_does_not_create_or_approve_runtime_approval(tmp_path: Path) -> None:
    plan_run_id = "plan-pending-review"

    def plan_handler(task, data_dir, credentials, inputs):  # type: ignore[no-untyped-def]
        return with_plan_approval_candidate(
            {"run_id": plan_run_id, "overall_status": "ok", "artifacts": {}},
            action_count=1,
            source_field="plan_run_id",
        )

    store = JobStore(tmp_path / "runtime.db")
    service = JobService(
        store=store,
        workflow_runner=WorkflowRunner(
            data_dir=tmp_path / "data",
            lock_dir=tmp_path / "locks",
            credentials=object(),  # type: ignore[arg-type]
            handlers={"ozon-elastic-plan": plan_handler},
        ),
        data_dir=tmp_path / "data",
    )
    plan_job = service.submit(task_id="ozon-elastic-plan")
    assert service.run(plan_job.job_id).ok is True
    pending = store.list_approvals(statuses=("pending_review",), limit=10)
    assert len(pending) == 1

    for source in (plan_run_id, "unreviewed-plan"):
        with pytest.raises(
            (RuntimeError, ValueError),
            match=r"(?i)(approval|confirmation|согласован|подтвержден)",
        ):
            service.submit(
                task_id="ozon-elastic-apply",
                params={"plan_run_id": source, "confirmed_by_user": True},
            )

    approvals = store.list_approvals(limit=10)
    apply_jobs = store.list_jobs(task_id="ozon-elastic-apply", limit=10)
    original = store.get_approval(pending[0].approval_id)
    assert len(approvals) == 1
    assert apply_jobs == []
    assert original == pending[0]


@pytest.mark.parametrize(
    ("overall_status", "verification_confirmed", "expected_approval_status"),
    [
        ("ok", True, "closed"),
        ("warning", False, "applied"),
        ("unknown", False, "applied"),
    ],
)
def test_normal_verify_closes_only_the_matching_successfully_verified_approval(
    overall_status: str,
    verification_confirmed: bool,
    expected_approval_status: str,
    tmp_path: Path,
) -> None:
    approval_id = f"approval-verify-{overall_status}"
    package = _approval_package(approval_id=approval_id)
    unrelated_id = f"approval-unrelated-{overall_status}"
    unrelated_package = _approval_package(
        approval_id=unrelated_id,
        plan_run_id=f"unrelated-plan-{overall_status}",
    )
    store = JobStore(tmp_path / f"{overall_status}.db")
    store.create_approval(
        approval_id=approval_id,
        source_job_id="apply-job",
        status="applied",
        checksum=package["approval_checksum"],
        data=package,
    )
    unrelated_before = store.create_approval(
        approval_id=unrelated_id,
        source_job_id="unrelated-apply-job",
        status="applied",
        checksum=unrelated_package["approval_checksum"],
        data=unrelated_package,
    )

    def verify_handler(task, data_dir, credentials, inputs):  # type: ignore[no-untyped-def]
        return {
            "run_id": f"verify-{overall_status}",
            "overall_status": overall_status,
            "verification_confirmed": verification_confirmed,
            "artifacts": {},
        }

    service = JobService(
        store=store,
        workflow_runner=WorkflowRunner(
            data_dir=tmp_path / "data",
            lock_dir=tmp_path / f"locks-{overall_status}",
            credentials=object(),  # type: ignore[arg-type]
            handlers={"ozon-elastic-verify": verify_handler},
        ),
        data_dir=tmp_path / "data",
    )
    verify_job = service.submit_approval_verify(approval_id)
    verify_result = service.run(verify_job.job_id)
    approval_after = store.get_approval(approval_id)
    unrelated_after = store.get_approval(unrelated_id)

    assert verify_result.status == overall_status
    assert approval_after is not None
    assert unrelated_after == unrelated_before
    assert approval_after.status == expected_approval_status


def test_policy_detects_all_missing_write_execution_metadata() -> None:
    registry = TaskRegistry()
    registry.register(
        RegisteredTask(
            name="unsafe-write",
            command="unsafe-write",
            title="Unsafe write",
            description="Synthetic incomplete write capability.",
            mode="apply",
            risk="high",
            requires_confirmation=True,
        )
    )

    issue_names = {row["issue"] for row in registry.policy_issues()}

    assert "apply_missing_source_plan_task" in issue_names
    assert "apply_missing_verify_task" in issue_names
    assert "apply_missing_lock_keys" in issue_names
    assert len([issue for issue in issue_names if "schema" in issue]) >= 2
    assert any("handler" in issue for issue in issue_names)


@pytest.mark.parametrize("task_id", MISSING_WRITE_HANDLERS)
def test_policy_reports_each_known_enabled_write_task_without_handler(task_id: str) -> None:
    from seller_agent.core.workflow_runner import default_workflow_handlers

    registry = default_task_registry()
    task = registry.get(task_id)
    handlers = default_workflow_handlers()
    if not task.enabled:
        assert task.telegram_enabled is False
        return

    assert task.name in handlers
    assert task.mode == "apply"
    assert task.is_write is True
    assert task.requires_confirmation is True
    assert task.source_plan_task
    source_task = registry.get(task.source_plan_task)
    assert source_task.enabled is True
    assert source_task.mode in {"read_only", "dry_run"}
    assert source_task.name in handlers
    assert task.verify_task
    verify_task = registry.get(task.verify_task)
    assert verify_task.enabled is True
    assert verify_task.mode == "verify"
    assert verify_task.name in handlers
    assert task.lock_keys
    assert all(lock_key.strip() for lock_key in task.lock_keys)
    assert task.parameter_schema
    assert task.result_schema
    assert task.policy_issues() == []
    assert [row for row in registry.policy_issues() if row["task"] == task.name] == []


@pytest.mark.parametrize(
    ("marketplace", "consumer_ids"),
    [
        (
            "ozon",
            (
                "status-preflight",
                "daily-morning-report",
                "ozon-lk-state-monitor",
                "ozon-messenger-workflow",
                "ozon-inbox",
                "ozon-inbox-apply",
                "reviews-questions",
                "reviews-questions-apply",
                "reviews-questions-verify",
            ),
        ),
        (
            "wb",
            (
                "status-preflight",
                "daily-morning-report",
                "wb-pricing-margin",
                "wb-inbox",
                "wb-inbox-apply",
                "reviews-questions",
                "reviews-questions-apply",
                "reviews-questions-verify",
            ),
        ),
    ],
)
def test_same_lk_profile_consumers_share_one_conflicting_lease(
    marketplace: str,
    consumer_ids: tuple[str, ...],
) -> None:
    registry = default_task_registry()
    consumers = [registry.get(task_id) for task_id in consumer_ids]
    lock_sets = [set(task.lock_keys) for task in consumers if task.enabled]
    common_locks = set.intersection(*lock_sets)

    assert common_locks, f"{marketplace} LK consumers do not share an exact profile lease"
    assert any(lock.startswith(f"lk:{marketplace}:profile:") for lock in common_locks)
