from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

import pytest

from seller_agent import cli
from seller_agent.core import job_service as job_service_module
from seller_agent.core.job_models import ApprovalRecord, JobRecord
from seller_agent.core.job_service import JobService
from seller_agent.core.job_store import JobStore
from seller_agent.core.workflow_runner import WorkflowRunner
from seller_agent.safety import guard as guard_module
from seller_agent.safety.approval_package import (
    APPROVAL_TRANSPORT_ONLY_PARAMS,
    build_approval_package,
    normalize_business_params,
    verify_approval_package,
)
from seller_agent.safety.approvals import canonical_checksum
from seller_agent.safety.guard import (
    APPROVAL_PACKAGE_MAX_CLOCK_SKEW,
    SafetyDecision,
    SafetyGuard,
)
from seller_agent.tasks.registry import default_task_registry


def _package(
    *,
    approval_id: str = "approval-p0-d",
    task_id: str = "ozon-elastic-apply",
    apply_params: dict[str, object] | None = None,
) -> dict[str, object]:
    task = default_task_registry().get(task_id)
    params = dict(apply_params or {"plan_run_id": "plan-approved"})
    source_kind = next(iter(params), "params")
    return build_approval_package(
        approval_id=approval_id,
        task_id=task.name,
        source_plan_task=task.source_plan_task,
        verify_task=task.verify_task,
        source_kind=source_kind,
        source_ref=json.dumps(params.get(source_kind), ensure_ascii=False),
        apply_params=params,
        marketplaces=task.marketplaces,
    )


def _approval(package: dict[str, object], *, status: str = "approved") -> ApprovalRecord:
    return ApprovalRecord(
        approval_id=str(package["approval_id"]),
        status=status,  # type: ignore[arg-type]
        source_job_id="plan-job",
        created_at=str(package["created_at"]),
        updated_at=str(package["created_at"]),
        checksum=str(package["approval_checksum"]),
        data=package,
    )


def _job(package: dict[str, object], business_params: dict[str, object]) -> JobRecord:
    return JobRecord(
        job_id="apply-job",
        task_id=str(package["task_id"]),
        status="queued",
        actor="owner",
        created_at=str(package["created_at"]),
        updated_at=str(package["created_at"]),
        params={
            **business_params,
            "approval_id": package["approval_id"],
            "approval_checksum": package["approval_checksum"],
            "confirmed_by_user": True,
        },
    )


def _store_approval(
    store: JobStore,
    package: dict[str, object],
    *,
    status: str = "pending_review",
) -> None:
    store.create_approval(
        approval_id=str(package["approval_id"]),
        source_job_id="plan-job",
        status=status,  # type: ignore[arg-type]
        checksum=str(package["approval_checksum"]),
        data=package,
    )


def _resign_package(package: dict[str, object]) -> None:
    checksum_payload = {
        key: value for key, value in package.items() if key != "approval_checksum"
    }
    package["approval_checksum"] = f"sha256:{canonical_checksum(checksum_payload)}"


def test_missing_schema_still_calls_package_verifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_verify(package: dict[str, object]) -> list[str]:
        calls.append(package)
        return ["approval_package_schema_invalid"]

    monkeypatch.setattr(guard_module, "verify_approval_package", fake_verify)
    task = default_task_registry().get("ozon-elastic-apply")
    approval = ApprovalRecord(
        approval_id="legacy-approval",
        status="approved",
        source_job_id="plan-job",
        created_at="2026-08-01T00:00:00Z",
        updated_at="2026-08-01T00:00:00Z",
        checksum="sha256:legacy",
        data={"task_id": task.name, "apply_params": {"plan_run_id": "legacy"}},
    )
    job = JobRecord(
        job_id="legacy-job",
        task_id=task.name,
        status="queued",
        actor="owner",
        created_at="2026-08-01T00:00:00Z",
        updated_at="2026-08-01T00:00:00Z",
        params={
            "plan_run_id": "legacy",
            "approval_id": approval.approval_id,
            "approval_checksum": approval.checksum,
            "confirmed_by_user": True,
        },
    )

    decision = SafetyGuard().validate_apply(task=task, job=job, approval=approval)

    assert calls == [approval.data]
    assert decision.allowed is False
    assert "approval_package_schema_invalid" in decision.issues


def test_approval_package_created_at_is_required_and_signed() -> None:
    package = _package(approval_id="created-at-contract")
    package.pop("created_at")

    issues = verify_approval_package(package)

    assert "approval_package_missing_created_at" in issues
    assert "approval_package_checksum_invalid" in issues


@pytest.mark.parametrize(
    "replacement_delta",
    (timedelta(), timedelta(minutes=1)),
    ids=("current", "future_within_clock_skew"),
)
def test_stale_created_at_cannot_be_rejuvenated_without_checksum_recompute(
    replacement_delta: timedelta,
) -> None:
    package = _package(approval_id="stale-tamper-package")
    package["created_at"] = (
        datetime.now(timezone.utc) - timedelta(hours=25)
    ).isoformat(timespec="seconds")
    _resign_package(package)
    stale_checksum = package["approval_checksum"]
    package["created_at"] = (
        datetime.now(timezone.utc) + replacement_delta
    ).isoformat(timespec="seconds")
    task = default_task_registry().get(str(package["task_id"]))

    decision = SafetyGuard().validate_approval_package(
        task=task,
        approval=_approval(package),
    )

    assert package["approval_checksum"] == stale_checksum
    assert decision.allowed is False
    assert "approval_package_checksum_invalid" in decision.issues


def test_honestly_old_approval_package_remains_stale() -> None:
    package = _package(approval_id="honestly-old-package")
    package["created_at"] = (
        datetime.now(timezone.utc) - timedelta(hours=25)
    ).isoformat(timespec="seconds")
    _resign_package(package)
    task = default_task_registry().get(str(package["task_id"]))

    decision = SafetyGuard().validate_approval_package(
        task=task,
        approval=_approval(package),
    )

    assert decision.allowed is False
    assert "approval_package_stale" in decision.issues
    assert "approval_package_checksum_invalid" not in decision.issues


@pytest.mark.parametrize(
    ("future_delta", "expected_allowed"),
    (
        (APPROVAL_PACKAGE_MAX_CLOCK_SKEW - timedelta(minutes=1), True),
        (APPROVAL_PACKAGE_MAX_CLOCK_SKEW + timedelta(minutes=1), False),
    ),
    ids=("within_clock_skew", "materially_future"),
)
def test_approval_package_future_timestamp_has_bounded_clock_skew(
    future_delta: timedelta,
    expected_allowed: bool,
) -> None:
    package = _package(approval_id="future-package")
    package["created_at"] = (
        datetime.now(timezone.utc) + future_delta
    ).isoformat(timespec="seconds")
    _resign_package(package)
    task = default_task_registry().get(str(package["task_id"]))

    decision = SafetyGuard().validate_approval_package(
        task=task,
        approval=_approval(package),
    )

    assert decision.allowed is expected_allowed
    assert ("approval_package_created_at_future" in decision.issues) is (
        not expected_allowed
    )
    assert "approval_package_checksum_invalid" not in decision.issues


@pytest.mark.parametrize(
    "actual_params",
    [
        {"plan_run_id": "plan-changed", "limit": 10},
        {"plan_run_id": "plan-approved", "limit": 10, "unexpected": True},
        {"plan_run_id": "plan-approved"},
    ],
    ids=("changed", "extra", "removed"),
)
def test_business_params_must_match_exactly(actual_params: dict[str, object]) -> None:
    task = default_task_registry().get("ozon-elastic-apply")
    package = _package(
        apply_params={"plan_run_id": "plan-approved", "limit": 10},
    )

    decision = SafetyGuard().validate_apply(
        task=task,
        job=_job(package, actual_params),
        approval=_approval(package),
    )

    assert decision.allowed is False
    assert "approval_params_mismatch" in decision.issues


def test_transport_allowlist_is_exact_and_never_enters_apply_params() -> None:
    package = _package(
        apply_params={
            "plan_run_id": "plan-approved",
            "approval_id": "transport-id",
            "approval_checksum": "transport-checksum",
            "confirmed_by_user": True,
        }
    )

    assert APPROVAL_TRANSPORT_ONLY_PARAMS == {
        "approval_id",
        "approval_checksum",
        "confirmed_by_user",
    }
    assert package["apply_params"] == {"plan_run_id": "plan-approved"}
    assert normalize_business_params(
        {
            "plan_run_id": "plan-approved",
            "approval_id": "transport-id",
            "approval_checksum": "transport-checksum",
            "confirmed_by_user": True,
        }
    ) == {"plan_run_id": "plan-approved"}


def test_confirmed_flag_cannot_create_or_promote_approval(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "runtime.db")
    package = _package(approval_id="pending-package")
    _store_approval(store, package)
    before = store.get_approval("pending-package")
    service = JobService(store=store, data_dir=tmp_path / "data")

    for plan_run_id in ("plan-approved", "unreviewed-plan"):
        with pytest.raises(RuntimeError, match="submit_approval_apply"):
            service.submit(
                task_id="ozon-elastic-apply",
                params={
                    "plan_run_id": plan_run_id,
                    "confirmed_by_user": True,
                },
            )

    assert store.get_approval("pending-package") == before
    assert store.list_jobs(task_id="ozon-elastic-apply", limit=10) == []


def test_apply_job_is_created_only_from_existing_approved_package(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "runtime.db")
    package = _package(
        approval_id="approved-package",
        apply_params={"plan_run_id": "approved-plan", "limit": 25},
    )
    _store_approval(store, package)
    service = JobService(store=store, data_dir=tmp_path / "data")

    with pytest.raises(RuntimeError, match="submit_approval_apply"):
        service.submit(
            task_id="ozon-elastic-apply",
            params={
                "plan_run_id": "approved-plan",
                "limit": 25,
                "approval_id": "approved-package",
                "approval_checksum": package["approval_checksum"],
                "confirmed_by_user": True,
            },
        )
    assert store.list_jobs(limit=10) == []

    service.approve("approved-package")
    job = service.submit_approval_apply("approved-package")

    assert normalize_business_params(job.params) == package["apply_params"]
    assert job.params["approval_id"] == "approved-package"
    assert job.params["approval_checksum"] == package["approval_checksum"]
    assert job.params["confirmed_by_user"] is True


def test_generic_cli_jobs_submit_write_requires_existing_approval(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime_db = tmp_path / "runtime.db"
    missing_exit = cli.main(
        [
            "jobs",
            "submit",
            "--task",
            "ozon-elastic-apply",
            "--runtime-db",
            str(runtime_db),
            "--data-dir",
            str(tmp_path / "data"),
            "--params-json",
            json.dumps({"confirmed_by_user": True, "plan_run_id": "unsafe"}),
        ]
    )
    missing_payload = json.loads(capsys.readouterr().out)

    assert missing_exit == 2
    assert missing_payload["status"] == "blocked"
    assert JobStore(runtime_db).list_jobs(limit=10) == []

    store = JobStore(runtime_db)
    package = _package(approval_id="cli-approved-package")
    _store_approval(store, package, status="approved")
    approved_exit = cli.main(
        [
            "jobs",
            "submit",
            "--task",
            "ozon-elastic-apply",
            "--runtime-db",
            str(runtime_db),
            "--data-dir",
            str(tmp_path / "data"),
            "--params-json",
            json.dumps({"approval_id": "cli-approved-package"}),
        ]
    )
    approved_payload = json.loads(capsys.readouterr().out)

    assert approved_exit == 0
    assert approved_payload["job"]["task_id"] == "ozon-elastic-apply"
    queued = store.list_jobs(limit=10)
    assert len(queued) == 1
    assert normalize_business_params(queued[0].params) == package["apply_params"]


def test_cli_write_alias_rejects_cross_task_approval_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    runtime_db = tmp_path / "runtime" / "runtime.db"
    store = JobStore(runtime_db)
    package = _package(
        approval_id="cross-task-alias-approval",
        task_id="wb-actions-discount-apply",
        apply_params={"plan_run_id": "wb-actions-plan"},
    )
    _store_approval(store, package, status="approved")
    before = store.get_approval("cross-task-alias-approval")

    exit_code = cli.main(
        [
            "apply-ozon-elastic",
            "--data-dir",
            str(tmp_path / "data"),
            "--plan-run-id",
            "ozon-elastic-plan",
            "--confirmed-by-user",
            "--approval-id",
            "cross-task-alias-approval",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["status"] == "blocked"
    assert "approval_task_mismatch" in payload["message"]
    assert store.list_jobs(limit=10) == []
    assert store.get_approval("cross-task-alias-approval") == before


def test_generic_cli_jobs_submit_rejects_cross_task_approval_without_mutation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime_db = tmp_path / "runtime.db"
    store = JobStore(runtime_db)
    package = _package(
        approval_id="cross-task-generic-approval",
        task_id="wb-actions-discount-apply",
        apply_params={"plan_run_id": "wb-actions-plan"},
    )
    _store_approval(store, package, status="approved")
    before = store.get_approval("cross-task-generic-approval")

    exit_code = cli.main(
        [
            "jobs",
            "submit",
            "--task",
            "ozon-elastic-apply",
            "--runtime-db",
            str(runtime_db),
            "--data-dir",
            str(tmp_path / "data"),
            "--params-json",
            json.dumps({"approval_id": "cross-task-generic-approval"}),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["status"] == "blocked"
    assert "approval_task_mismatch" in payload["message"]
    assert store.list_jobs(limit=10) == []
    assert store.get_approval("cross-task-generic-approval") == before


def test_legacy_approval_migration_preserves_states_and_requires_new_dry_run(
    tmp_path: Path,
) -> None:
    runtime_db = tmp_path / "runtime.db"
    now = "2026-08-01T00:00:00Z"
    legacy_statuses = (
        "pending_review",
        "approved",
        "applying",
        "applying_unknown",
        "closed",
    )
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
        for status in legacy_statuses:
            connection.execute(
                """
                INSERT INTO approvals (
                  approval_id, source_job_id, status, owner_job_id, checksum,
                  data_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"legacy-{status}",
                    "legacy-plan-job",
                    status,
                    "legacy-owner-job" if status.startswith("applying") else "",
                    "sha256:legacy",
                    json.dumps(
                        {
                            "task_id": "ozon-elastic-apply",
                            "apply_params": {"plan_run_id": "legacy-plan"},
                        }
                    ),
                    now,
                    now,
                ),
            )

    store = JobStore(runtime_db)
    store.initialize()
    before = {
        status: store.get_approval(f"legacy-{status}")
        for status in legacy_statuses
    }
    service = JobService(store=store, data_dir=tmp_path / "data")

    with pytest.raises(RuntimeError, match="SafetyGuard"):
        service.approve("legacy-pending_review")
    with pytest.raises(RuntimeError, match="SafetyGuard"):
        service.submit_approval_apply("legacy-approved")

    after_initialize = {
        status: store.get_approval(f"legacy-{status}")
        for status in legacy_statuses
    }
    assert after_initialize == before

    def plan_handler(task, data_dir, credentials, inputs):  # type: ignore[no-untyped-def]
        return {"run_id": "new-safe-plan", "overall_status": "ok", "artifacts": {}}

    service = JobService(
        store=store,
        workflow_runner=WorkflowRunner(
            data_dir=tmp_path / "data",
            lock_dir=tmp_path / "locks",
            handlers={"ozon-elastic-plan": plan_handler},
        ),
        data_dir=tmp_path / "data",
    )
    plan_job = service.submit(task_id="ozon-elastic-plan")
    assert service.run(plan_job.job_id).ok is True

    new_pending = [
        approval
        for approval in store.list_approvals(statuses=("pending_review",), limit=10)
        if approval.approval_id != "legacy-pending_review"
    ]
    assert len(new_pending) == 1
    assert new_pending[0].data["schema"] == "seller.approval_package.v1"
    assert store.get_approval("legacy-pending_review") == before["pending_review"]
    assert store.get_approval("legacy-approved") == before["approved"]
    assert store.get_approval("legacy-applying") == before["applying"]
    assert store.get_approval("legacy-applying_unknown") == before["applying_unknown"]
    assert store.get_approval("legacy-closed") == before["closed"]


@pytest.mark.parametrize("status", ("pending_review", "approved"))
def test_rerunning_same_plan_does_not_rejuvenate_existing_approval(
    status: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    old_created_at = (
        datetime.now(timezone.utc) - timedelta(hours=25)
    ).isoformat(timespec="seconds")
    new_created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    created_at_values = iter((old_created_at, new_created_at))
    generated_packages: list[dict[str, object]] = []

    def build_with_sequenced_timestamp(**kwargs: object) -> dict[str, object]:
        package = build_approval_package(**kwargs)  # type: ignore[arg-type]
        package["created_at"] = next(created_at_values)
        _resign_package(package)
        generated_packages.append(deepcopy(package))
        return package

    monkeypatch.setattr(
        job_service_module,
        "build_approval_package",
        build_with_sequenced_timestamp,
    )

    def plan_handler(task, data_dir, credentials, inputs):  # type: ignore[no-untyped-def]
        return {"run_id": "same-plan-run", "overall_status": "ok", "artifacts": {}}

    store = JobStore(tmp_path / "runtime.db")
    service = JobService(
        store=store,
        workflow_runner=WorkflowRunner(
            data_dir=tmp_path / "data",
            lock_dir=tmp_path / "locks",
            handlers={"ozon-elastic-plan": plan_handler},
        ),
        data_dir=tmp_path / "data",
    )
    first_job = service.submit(task_id="ozon-elastic-plan")
    assert service.run(first_job.job_id).ok is True
    first = store.list_approvals(limit=10)
    assert len(first) == 1
    if status == "approved":
        assert store.decide_approval(
            approval_id=first[0].approval_id,
            status="approved",
        )
    before = store.get_approval(first[0].approval_id)
    assert before is not None

    second_job = service.submit(task_id="ozon-elastic-plan")
    assert service.run(second_job.job_id).ok is True
    after = store.get_approval(first[0].approval_id)

    assert len(generated_packages) == 2
    assert generated_packages[0]["approval_id"] == generated_packages[1]["approval_id"]
    assert generated_packages[0]["source_ref"] == generated_packages[1]["source_ref"]
    assert generated_packages[0]["created_at"] == old_created_at
    assert generated_packages[1]["created_at"] == new_created_at
    assert generated_packages[0]["approval_checksum"] != generated_packages[1][
        "approval_checksum"
    ]
    assert after == before
    assert after.status == status
    assert after.data == generated_packages[0]
    assert after.checksum == generated_packages[0]["approval_checksum"]
    assert after.data["created_at"] == old_created_at
    assert len(store.list_approvals(limit=10)) == 1


def test_safety_guard_failure_is_sanitized_before_persistence(tmp_path: Path) -> None:
    secret = "sensitive-value-must-not-leak"
    store = JobStore(tmp_path / "runtime.db")
    package = _package(approval_id="sanitize-package")
    _store_approval(store, package, status="approved")
    job = store.create_job(
        task_id="ozon-elastic-apply",
        status="queued",
        params={
            "plan_run_id": secret,
            "api_token": secret,
            "approval_id": package["approval_id"],
            "approval_checksum": package["approval_checksum"],
            "confirmed_by_user": True,
        },
    )

    def forbidden_handler(*args: object, **kwargs: object) -> dict[str, object]:
        raise AssertionError("workflow must not start after SafetyGuard failure")

    service = JobService(
        store=store,
        workflow_runner=WorkflowRunner(
            data_dir=tmp_path / "data",
            lock_dir=tmp_path / "locks",
            handlers={"ozon-elastic-apply": forbidden_handler},
        ),
        data_dir=tmp_path / "data",
    )

    result = service.run(job.job_id)
    persisted = store.get_job(job.job_id)
    events_json = json.dumps(
        [event.__dict__ for event in store.list_events(job.job_id)],
        ensure_ascii=False,
    )

    assert result.ok is False
    assert result.status == "safety_blocked"
    assert persisted is not None
    assert "approval_params_mismatch" in persisted.error
    assert secret not in persisted.error
    assert secret not in result.message
    assert secret not in events_json


def test_unknown_safety_issues_and_corrupt_approval_status_are_semantically_sanitized(
    tmp_path: Path,
) -> None:
    corrupt_status = "secretstatusabc123"
    injected_issues = (
        "approval_params_mismatch:secretabc123",
        "token123",
    )
    store = JobStore(tmp_path / "runtime.db")
    package = _package(approval_id="semantic-sanitize-package")
    _store_approval(store, package, status=corrupt_status)
    job = store.create_job(
        task_id="ozon-elastic-apply",
        status="queued",
        params={
            **package["apply_params"],  # type: ignore[dict-item]
            "approval_id": package["approval_id"],
            "approval_checksum": package["approval_checksum"],
            "confirmed_by_user": True,
        },
    )

    class InjectedIssueSafetyGuard(SafetyGuard):
        def validate_apply(self, *, task, job, approval):  # type: ignore[no-untyped-def]
            decision = super().validate_apply(task=task, job=job, approval=approval)
            return SafetyDecision(
                allowed=False,
                issues=(*decision.issues, *injected_issues),
            )

    def forbidden_handler(*args: object, **kwargs: object) -> dict[str, object]:
        raise AssertionError("workflow must not start after SafetyGuard failure")

    service = JobService(
        store=store,
        workflow_runner=WorkflowRunner(
            data_dir=tmp_path / "data",
            lock_dir=tmp_path / "locks",
            handlers={"ozon-elastic-apply": forbidden_handler},
        ),
        data_dir=tmp_path / "data",
        safety_guard=InjectedIssueSafetyGuard(),
    )

    result = service.run(job.job_id)
    persisted = store.get_job(job.job_id)
    events = store.list_events(job.job_id)
    externally_visible = json.dumps(
        {
            "result": result.__dict__,
            "job": persisted.__dict__ if persisted else {},
            "events": [event.__dict__ for event in events],
        },
        ensure_ascii=False,
        default=str,
    )

    assert result.ok is False
    assert result.status == "approval_not_available"
    assert persisted is not None
    assert persisted.error == (
        "SafetyGuard blocked apply: approval_status_invalid, safety_policy_violation"
    )
    assert events[-1].event_type == "job_approval_blocked"
    assert events[-1].data["issues"] == [
        "approval_status_invalid",
        "safety_policy_violation",
    ]
    assert corrupt_status not in externally_visible
    assert all(issue not in externally_visible for issue in injected_issues)
