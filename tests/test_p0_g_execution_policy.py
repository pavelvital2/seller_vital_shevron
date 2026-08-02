from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from seller_agent.tasks import inbox_workflow
from seller_agent.core import workflow_runner as workflow_runner_module
from seller_agent.core.job_service import JobService
from seller_agent.core.job_store import JobStore
from seller_agent.core.resource_keys import task_resource_keys
from seller_agent.core.workflow_runner import WorkflowRunner, default_workflow_handlers
from seller_agent.safety.approval_package import build_approval_package
from seller_agent.tasks.registry import RegisteredTask, TaskRegistry, default_task_registry


UNSUPPORTED_WRITE_CAPABILITIES = (
    "approved-card-apply",
    "card-content-update-apply",
    "ozon-card-create-apply",
    "ozon-partial-approved-recovery-apply",
    "ozon-product-remove-apply",
    "seller-sku-update-apply",
    "wb-card-create-apply",
    "ozon-messenger-workflow",
)

INVALID_INBOX_RECEIPT_STATES = (
    ("truncated", "{"),
    (
        "wrong_schema",
        json.dumps({"schema_version": "wrong-schema", "receipts": {}}),
    ),
    (
        "receipts_list",
        json.dumps(
            {
                "schema_version": "inbox-action-receipts/v1",
                "receipts": [],
            }
        ),
    ),
    (
        "invalid_record",
        json.dumps(
            {
                "schema_version": "inbox-action-receipts/v1",
                "receipts": {
                    "a" * 64: {
                        "status": "secret-state-value",
                        "source_run_id": "secret-state-value",
                    }
                },
            }
        ),
    ),
)


def _task(*, name: str, mode: str, **overrides: object) -> RegisteredTask:
    values: dict[str, object] = {
        "name": name,
        "command": name,
        "title": name,
        "description": f"Synthetic {name} task.",
        "mode": mode,
        "risk": "high" if mode == "apply" else "low",
    }
    values.update(overrides)
    return RegisteredTask(**values)  # type: ignore[arg-type]


def _valid_apply_parameter_schema(source_field: str = "plan_run_id") -> dict[str, object]:
    return {
        source_field: {"type": "string", "minLength": 1, "required": True},
        "approval_id": {"type": "string", "minLength": 1, "required": True},
        "approval_checksum": {"type": "string", "minLength": 1, "required": True},
        "confirmed_by_user": {"type": "boolean", "const": True, "required": True},
    }


def _valid_apply_result_schema() -> dict[str, object]:
    return {
        "overall_status": {
            "type": "string",
            "enum": ["ok", "warning", "partial", "blocked", "error"],
            "required": True,
        },
        "run_id": {"type": "string", "minLength": 1, "required": True},
    }


def _valid_handler(task, data_dir, credentials, inputs):  # type: ignore[no-untyped-def]
    return {"run_id": "synthetic-run", "overall_status": "ok"}


def test_policy_reports_each_missing_runtime_handler_in_write_graph() -> None:
    registry = TaskRegistry()
    registry.register(_task(name="safe-plan", mode="dry_run", marketplaces=("ozon",)))
    registry.register(_task(name="safe-verify", mode="verify", marketplaces=("ozon",)))
    registry.register(
        _task(
            name="write-apply",
            mode="apply",
            marketplaces=("ozon",),
            requires_confirmation=True,
            source_plan_task="safe-plan",
            verify_task="safe-verify",
            lock_keys=task_resource_keys(
                api_write_marketplaces=("ozon",),
                subject_keys=("synthetic:write",),
            ),
            approval_source_field="plan_run_id",
            parameter_schema=_valid_apply_parameter_schema(),
            result_schema=_valid_apply_result_schema(),
        )
    )

    issues = {row["issue"] for row in registry.policy_issues(handlers={})}

    assert issues == {
        "apply_missing_runtime_handler",
        "apply_source_plan_missing_runtime_handler",
        "apply_verify_missing_runtime_handler",
    }


def test_policy_rejects_incomplete_or_unsafe_write_graph_links() -> None:
    registry = TaskRegistry()
    registry.register(_task(name="unsafe-source", mode="maintenance"))
    registry.register(_task(name="unsafe-verify", mode="dry_run"))
    registry.register(
        _task(
            name="write-apply",
            mode="apply",
            marketplaces=("ozon",),
            requires_confirmation=True,
            source_plan_task="unsafe-source",
            verify_task="unsafe-verify",
            lock_keys=(" ",),
            approval_source_field="plan_run_id",
            parameter_schema={"plan_run_id": {"type": "string"}},
            result_schema={"overall_status": {"type": "string"}},
        )
    )
    handlers = {name: object() for name in ("unsafe-source", "unsafe-verify", "write-apply")}

    issues = {row["issue"] for row in registry.policy_issues(handlers=handlers)}

    assert "apply_source_plan_unsafe_mode" in issues
    assert "apply_verify_mode_invalid" in issues
    assert "apply_invalid_lock_key" in issues
    assert "apply_api_write_locks_mismatch" in issues
    assert "apply_runtime_handler_not_callable" in issues
    assert "apply_source_plan_runtime_handler_not_callable" in issues
    assert "apply_verify_runtime_handler_not_callable" in issues
    assert "apply_parameter_schema_invalid" in issues
    assert "apply_result_schema_invalid" in issues


def test_policy_rejects_semantically_wrong_schemas_and_object_handlers() -> None:
    registry = TaskRegistry()
    registry.register(_task(name="safe-plan", mode="dry_run"))
    registry.register(_task(name="safe-verify", mode="verify"))
    registry.register(
        _task(
            name="write-apply",
            mode="apply",
            marketplaces=("ozon",),
            requires_confirmation=True,
            source_plan_task="safe-plan",
            verify_task="safe-verify",
            lock_keys=task_resource_keys(
                api_write_marketplaces=("ozon",),
                subject_keys=("synthetic:write",),
            ),
            approval_source_field="source_run_id",
            parameter_schema={
                "plan_run_id": {"type": "integer", "required": True},
                "approval_id": {"type": "string", "required": True},
                "approval_checksum": {"type": "string", "minLength": 0, "required": True},
                "confirmed_by_user": {"type": "boolean", "const": False, "required": True},
            },
            result_schema={
                "overall_status": {"type": "string", "required": True},
                "run_id": {"type": "integer", "required": True},
            },
        )
    )
    handlers = {name: object() for name in ("safe-plan", "safe-verify", "write-apply")}

    issues = {row["issue"] for row in registry.policy_issues(handlers=handlers)}

    assert "apply_parameter_schema_invalid" in issues
    assert "apply_result_schema_invalid" in issues
    assert "apply_runtime_handler_not_callable" in issues
    assert "apply_source_plan_runtime_handler_not_callable" in issues
    assert "apply_verify_runtime_handler_not_callable" in issues


def test_policy_rejects_zero_argument_handlers() -> None:
    registry = TaskRegistry()
    registry.register(_task(name="safe-plan", mode="dry_run", marketplaces=("ozon",)))
    registry.register(_task(name="safe-verify", mode="verify", marketplaces=("ozon",)))
    registry.register(
        _task(
            name="write-apply",
            mode="apply",
            marketplaces=("ozon",),
            requires_confirmation=True,
            source_plan_task="safe-plan",
            verify_task="safe-verify",
            lock_keys=task_resource_keys(
                api_write_marketplaces=("ozon",),
                subject_keys=("synthetic:write",),
            ),
            approval_source_field="plan_run_id",
            parameter_schema=_valid_apply_parameter_schema(),
            result_schema=_valid_apply_result_schema(),
        )
    )

    def zero_argument_handler() -> dict[str, object]:
        return {"run_id": "invalid", "overall_status": "ok"}

    handlers = {
        name: zero_argument_handler
        for name in ("safe-plan", "safe-verify", "write-apply")
    }
    issues = {row["issue"] for row in registry.policy_issues(handlers=handlers)}

    assert "apply_runtime_handler_signature_invalid" in issues
    assert "apply_source_plan_runtime_handler_signature_invalid" in issues
    assert "apply_verify_runtime_handler_signature_invalid" in issues


def test_policy_requires_dry_run_source_and_exact_marketplace_parity() -> None:
    registry = TaskRegistry()
    registry.register(_task(name="wb-source", mode="read_only", marketplaces=("wb",)))
    registry.register(_task(name="wb-verify", mode="verify", marketplaces=("wb",)))
    registry.register(
        _task(
            name="ozon-apply",
            mode="apply",
            marketplaces=("ozon",),
            requires_confirmation=True,
            source_plan_task="wb-source",
            verify_task="wb-verify",
            lock_keys=task_resource_keys(
                api_write_marketplaces=("ozon",),
                subject_keys=("synthetic:write",),
            ),
            approval_source_field="plan_run_id",
            parameter_schema=_valid_apply_parameter_schema(),
            result_schema=_valid_apply_result_schema(),
        )
    )
    handlers = {
        name: _valid_handler
        for name in ("wb-source", "wb-verify", "ozon-apply")
    }

    issues = {row["issue"] for row in registry.policy_issues(handlers=handlers)}

    assert "apply_source_plan_unsafe_mode" in issues
    assert "apply_source_plan_marketplaces_mismatch" in issues
    assert "apply_verify_marketplaces_mismatch" in issues


def test_policy_rejects_result_statuses_outside_runtime_contract() -> None:
    registry = TaskRegistry()
    registry.register(_task(name="safe-plan", mode="dry_run", marketplaces=("ozon",)))
    registry.register(_task(name="safe-verify", mode="verify", marketplaces=("ozon",)))
    result_schema = _valid_apply_result_schema()
    result_schema["overall_status"]["enum"].append("mystery")  # type: ignore[index,union-attr]
    registry.register(
        _task(
            name="write-apply",
            mode="apply",
            marketplaces=("ozon",),
            requires_confirmation=True,
            source_plan_task="safe-plan",
            verify_task="safe-verify",
            lock_keys=task_resource_keys(
                api_write_marketplaces=("ozon",),
                subject_keys=("synthetic:write",),
            ),
            approval_source_field="plan_run_id",
            parameter_schema=_valid_apply_parameter_schema(),
            result_schema=result_schema,
        )
    )
    handlers = {
        name: _valid_handler
        for name in ("safe-plan", "safe-verify", "write-apply")
    }

    issues = {row["issue"] for row in registry.policy_issues(handlers=handlers)}

    assert "apply_result_schema_invalid" in issues


def test_disabled_write_requires_reason_and_no_telegram_exposure() -> None:
    registry = TaskRegistry()
    registry.register(
        _task(
            name="disabled-write",
            mode="apply",
            enabled=False,
            telegram_enabled=True,
            telegram_button_label="/unsafe",
            requires_confirmation=True,
        )
    )

    issues = {row["issue"] for row in registry.policy_issues(handlers={})}

    assert issues == {
        "disabled_write_missing_reason",
        "disabled_write_telegram_exposed",
    }


def test_default_registry_has_only_closed_enabled_write_graphs() -> None:
    registry = default_task_registry()
    handlers = default_workflow_handlers()

    assert registry.policy_issues(handlers=handlers) == []
    for task in registry.list():
        if not task.enabled or not task.is_write:
            continue
        source = registry.get(task.source_plan_task)
        verify = registry.get(task.verify_task)
        assert task.mode == "apply", task.name
        assert task.name in handlers, task.name
        assert source.enabled and source.mode == "dry_run", task.name
        assert source.name in handlers, task.name
        assert verify.enabled and verify.mode == "verify", task.name
        assert verify.name in handlers, task.name
        assert frozenset(source.marketplaces) == frozenset(task.marketplaces), task.name
        assert frozenset(verify.marketplaces) == frozenset(task.marketplaces), task.name
        assert task.lock_keys, task.name
        assert task.approval_source_field in {"plan_run_id", "source_run_id"}, task.name
        assert task.parameter_schema, task.name
        assert task.result_schema, task.name


def test_enabled_write_schemas_match_runtime_approval_transport_and_source() -> None:
    registry = default_task_registry()
    transport_fields = {"approval_id", "approval_checksum", "confirmed_by_user"}

    for task in registry.list():
        if not task.enabled or not task.is_write:
            continue
        expected_source = task.approval_source_field
        assert expected_source in task.parameter_schema, task.name
        assert transport_fields <= set(task.parameter_schema), task.name
        assert not {
            "runtime_approval_id",
            "runtime_recovery_approval_id",
        }.intersection(task.parameter_schema), task.name
        assert {"overall_status", "run_id"} <= set(task.result_schema), task.name
        assert set(task.result_schema["overall_status"]["enum"]) == {
            "ok",
            "warning",
            "partial",
            "blocked",
            "error",
        }, task.name


def test_known_unsupported_write_capabilities_are_explicitly_disabled() -> None:
    registry = default_task_registry()
    handlers = default_workflow_handlers()

    for task_id in UNSUPPORTED_WRITE_CAPABILITIES:
        task = registry.get(task_id)
        assert task.enabled is False, task_id
        assert task.telegram_enabled is False, task_id
        assert task.disabled_reason.strip(), task_id
        assert task.name not in handlers, task_id

    legacy_bridge = registry.get("reviews-questions-apply")
    assert legacy_bridge.enabled is False
    assert legacy_bridge.telegram_enabled is False
    assert "approved_path" in legacy_bridge.disabled_reason


def test_disabled_apply_graph_does_not_create_runtime_approval(tmp_path: Path) -> None:
    defaults = default_task_registry()
    plan = replace(defaults.get("ozon-elastic-plan"), name="disabled-plan", command="disabled-plan")
    apply = replace(
        defaults.get("ozon-elastic-apply"),
        name="disabled-apply",
        command="disabled-apply",
        source_plan_task=plan.name,
        enabled=False,
        telegram_enabled=False,
        disabled_reason="Unsupported write chain is disabled pending a separate implementation.",
    )
    verify = replace(defaults.get("ozon-elastic-verify"), name="disabled-verify", command="disabled-verify")
    apply = replace(apply, verify_task=verify.name)
    registry = TaskRegistry()
    for task in (plan, apply, verify):
        registry.register(task)

    def plan_handler(task, data_dir, credentials, inputs):  # type: ignore[no-untyped-def]
        return {"run_id": "disabled-plan-run", "overall_status": "ok", "artifacts": {}}

    store = JobStore(tmp_path / "runtime.db")
    service = JobService(
        store=store,
        registry=registry,
        workflow_runner=WorkflowRunner(
            registry=registry,
            data_dir=tmp_path / "data",
            lock_dir=tmp_path / "locks",
            handlers={plan.name: plan_handler},
        ),
        data_dir=tmp_path / "data",
    )

    result = service.run(service.submit(task_id=plan.name).job_id)

    assert result.ok is True
    assert store.list_approvals(limit=10) == []


@pytest.mark.parametrize("marketplace", ("ozon", "wb"))
def test_inbox_verify_treats_local_receipts_as_manual_only_without_repeating_write(
    marketplace: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_run_id = f"{marketplace}_inbox_policy_test"
    reviews_pending_id = f"{source_run_id}_reviews_pending"
    action = {
        "platform": marketplace,
        "source_type": "review",
        "source_id": "review-1",
        "action_type": "public_review_reply",
        "draft_text": "Approved reply",
    }
    reviews_dir = tmp_path / "pending" / reviews_pending_id
    reviews_dir.mkdir(parents=True)
    (reviews_dir / "draft_answers.json").write_text(
        json.dumps({"actions": [action]}),
        encoding="utf-8",
    )
    inbox_dir = tmp_path / "pending" / f"{source_run_id}_pending"
    inbox_dir.mkdir(parents=True)
    (inbox_dir / "inbox_pending.json").write_text(
        json.dumps(
            {
                "marketplace": marketplace,
                "reviews_pending_id": reviews_pending_id,
                "messenger_actions": [],
            }
        ),
        encoding="utf-8",
    )

    def forbidden_write(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("verify must not call an apply workflow")

    monkeypatch.setattr(inbox_workflow, "_prepare_and_apply_reviews", forbidden_write)
    monkeypatch.setattr(inbox_workflow, "_apply_ozon_messenger", forbidden_write)
    verify = (
        inbox_workflow.run_ozon_inbox_verify
        if marketplace == "ozon"
        else inbox_workflow.run_wb_inbox_verify
    )

    missing = verify(data_dir=tmp_path, source_run_id=source_run_id, run_id="verify-missing")
    inbox_workflow._record_inbox_receipts(  # noqa: SLF001 - pins the durable verification contract.
        data_dir=tmp_path,
        source_run_id=source_run_id,
        actions=[action],
    )
    local_receipt_only = verify(
        data_dir=tmp_path,
        source_run_id=source_run_id,
        run_id="verify-local-receipt",
    )

    assert missing["overall_status"] == "warning"
    assert missing["verification_confirmed"] is False
    assert missing["verification"]["remaining_count"] == 1
    assert local_receipt_only["overall_status"] == "warning"
    assert local_receipt_only["verification_confirmed"] is False
    assert local_receipt_only["manual_verification_required"] is True
    assert local_receipt_only["verification"]["local_receipt_count"] == 1
    assert local_receipt_only["verification"]["remaining_count"] == 0
    assert "independent_marketplace_verification_unavailable" in local_receipt_only[
        "verification"
    ]["reason_codes"]


def test_inbox_local_receipt_only_does_not_close_runtime_approval(tmp_path: Path) -> None:
    source_run_id = "wb_inbox_receipt_only_runtime"
    reviews_pending_id = f"{source_run_id}_reviews_pending"
    action = {
        "platform": "wb",
        "source_type": "review",
        "source_id": "review-runtime-1",
        "action_type": "public_review_reply",
        "draft_text": "Approved reply",
    }
    reviews_dir = tmp_path / "data" / "pending" / reviews_pending_id
    reviews_dir.mkdir(parents=True)
    (reviews_dir / "draft_answers.json").write_text(
        json.dumps({"actions": [action]}),
        encoding="utf-8",
    )
    inbox_dir = tmp_path / "data" / "pending" / f"{source_run_id}_pending"
    inbox_dir.mkdir(parents=True)
    (inbox_dir / "inbox_pending.json").write_text(
        json.dumps(
            {
                "marketplace": "wb",
                "reviews_pending_id": reviews_pending_id,
                "messenger_actions": [],
            }
        ),
        encoding="utf-8",
    )
    inbox_workflow._record_inbox_receipts(  # noqa: SLF001 - local receipt is intentionally insufficient.
        data_dir=tmp_path / "data",
        source_run_id=source_run_id,
        actions=[action],
    )
    registry = default_task_registry()
    apply_task = registry.get("wb-inbox-apply")
    package = build_approval_package(
        approval_id="approval-wb-inbox-receipt-only",
        task_id=apply_task.name,
        source_plan_task=apply_task.source_plan_task,
        verify_task=apply_task.verify_task,
        source_kind="source_run_id",
        source_ref=json.dumps(source_run_id),
        apply_params={"source_run_id": source_run_id},
        marketplaces=apply_task.marketplaces,
    )
    store = JobStore(tmp_path / "runtime.db")
    store.ensure_approval(
        approval_id=package["approval_id"],
        source_job_id="wb-inbox-source-job",
        status="applied",
        checksum=package["approval_checksum"],
        data=package,
    )
    service = JobService(store=store, registry=registry, data_dir=tmp_path / "data")

    result = service.run(service.submit_approval_verify(package["approval_id"]).job_id)
    approval = store.get_approval(package["approval_id"])

    assert result.ok is True
    assert result.status == "warning"
    assert result.job.result["summary"]["manual_verification_required"] is True
    assert approval is not None
    assert approval.status == "applied"


@pytest.mark.parametrize("state", ("missing", "corrupt", "zero"))
def test_inbox_verify_missing_corrupt_or_zero_review_actions_is_manual(
    state: str,
    tmp_path: Path,
) -> None:
    marketplace = "wb"
    source_run_id = f"wb_inbox_{state}_source"
    reviews_pending_id = f"{source_run_id}_reviews_pending"
    reviews_dir = tmp_path / "pending" / reviews_pending_id
    if state != "missing":
        reviews_dir.mkdir(parents=True)
        (reviews_dir / "draft_answers.json").write_text(
            "{not-json" if state == "corrupt" else json.dumps({"actions": []}),
            encoding="utf-8",
        )
    inbox_dir = tmp_path / "pending" / f"{source_run_id}_pending"
    inbox_dir.mkdir(parents=True)
    (inbox_dir / "inbox_pending.json").write_text(
        json.dumps(
            {
                "marketplace": marketplace,
                "reviews_pending_id": reviews_pending_id,
                "messenger_actions": [],
            }
        ),
        encoding="utf-8",
    )

    result = inbox_workflow.run_wb_inbox_verify(
        data_dir=tmp_path,
        source_run_id=source_run_id,
        run_id=f"verify-{state}",
    )

    assert result["overall_status"] == "warning"
    assert result["verification_confirmed"] is False
    assert result["manual_verification_required"] is True
    assert result["verification"]["source_actions_count"] == 0
    assert result["verification"]["reason_codes"]


def test_disabled_queued_apply_is_blocked_before_safety_or_approval_reservation(
    tmp_path: Path,
) -> None:
    defaults = default_task_registry()
    plan = replace(defaults.get("ozon-elastic-plan"), name="disable-plan", command="disable-plan")
    verify = replace(
        defaults.get("ozon-elastic-verify"),
        name="disable-verify",
        command="disable-verify",
    )
    apply = replace(
        defaults.get("ozon-elastic-apply"),
        name="disable-apply",
        command="disable-apply",
        source_plan_task=plan.name,
        verify_task=verify.name,
    )
    registry = TaskRegistry()
    for task in (plan, apply, verify):
        registry.register(task)
    store = JobStore(tmp_path / "runtime.db")
    package = build_approval_package(
        approval_id="approval-disabled-after-queue",
        task_id=apply.name,
        source_plan_task=plan.name,
        verify_task=verify.name,
        source_kind="plan_run_id",
        source_ref=json.dumps("disabled-plan-run"),
        apply_params={"plan_run_id": "disabled-plan-run"},
        marketplaces=apply.marketplaces,
    )
    approval = store.ensure_approval(
        approval_id=package["approval_id"],
        source_job_id="source-plan-job",
        status="approved",
        checksum=package["approval_checksum"],
        data=package,
    )

    def forbidden_handler(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("disabled apply handler must not run")

    service = JobService(
        store=store,
        registry=registry,
        workflow_runner=WorkflowRunner(
            registry=registry,
            data_dir=tmp_path / "data",
            lock_dir=tmp_path / "locks",
            handlers={apply.name: forbidden_handler},
        ),
        data_dir=tmp_path / "data",
    )
    job = service.submit_approval_apply(approval.approval_id)
    claim = store.claim_queued_job(
        job_id=job.job_id,
        worker_id="disabled-policy-worker",
        ttl_seconds=60,
    )
    assert claim is not None
    registry._tasks[apply.name] = replace(  # noqa: SLF001 - simulates an operational policy disable after enqueue.
        apply,
        enabled=False,
        disabled_reason="Disabled after approval and enqueue by runtime policy.",
    )

    result = service.run(
        job.job_id,
        claim_token=claim.claim_token,
        worker_id=claim.worker_id,
    )
    current_approval = store.get_approval(approval.approval_id)
    event_types = {event.event_type for event in store.list_events(job_id=job.job_id)}

    assert result.ok is False
    assert result.status == "blocked"
    assert result.job.status == "failed"
    assert current_approval == approval
    assert store.get_job_claim(job.job_id) is None
    assert "job_approval_reserved" not in event_types
    assert "job_write_window_started" not in event_types


@pytest.mark.parametrize(
    ("task_id", "params", "source_files"),
    (
        (
            "ozon-cpc-optimization-plan",
            {"run_id": "ozon_cpc_empty_plan", "rows_csv": "ozon_rows.csv"},
            {"ozon_rows.csv": "campaign_id,campaign_title,sku,title,views,clicks,to_cart,orders,spend,orders_money\n"},
        ),
        (
            "wb-promotion-bid-plan",
            {
                "run_id": "wb_promotion_zero_candidate_plan",
                "products_csv": "wb_products.csv",
                "campaigns_json": "campaigns.json",
            },
            {
                "wb_products.csv": "advert_id,campaign_name,status,payment_type,nm_id,orders,spend,revenue\n",
                "campaigns.json": "[]",
            },
        ),
    ),
)
def test_noop_real_plan_does_not_create_pending_runtime_approval(
    task_id: str,
    params: dict[str, str],
    source_files: dict[str, str],
    tmp_path: Path,
) -> None:
    for filename, content in source_files.items():
        (tmp_path / filename).write_text(content, encoding="utf-8")
    resolved_params = {
        key: str(tmp_path / value) if key.endswith(("_csv", "_json")) else value
        for key, value in params.items()
    }
    store = JobStore(tmp_path / "runtime.db")
    service = JobService(store=store, data_dir=tmp_path / "data")

    result = service.run(service.submit(task_id=task_id, params=resolved_params).job_id)

    assert result.ok is True
    assert store.list_approvals(statuses=("pending_review",), limit=10) == []


@pytest.mark.parametrize("marketplace", ("ozon", "wb"))
@pytest.mark.parametrize(
    ("case", "expected_apply_count"),
    (("manual_only", 0), ("fully_receipted", 0), ("actionable", None)),
)
def test_inbox_plan_counts_only_unreceipted_approvable_actions(
    marketplace: str,
    case: str,
    expected_apply_count: int | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    run_id = f"{marketplace}_inbox_{case}_plan"
    reviews_pending_id = f"{run_id}_reviews_pending"
    reviews_path = data_dir / "pending" / reviews_pending_id / "draft_answers.json"
    reviews_path.parent.mkdir(parents=True)
    if case == "manual_only":
        review_actions = [
            {
                "platform": marketplace,
                "source_type": "question",
                "source_id": "manual-question",
                "action_type": "manual_question_review",
                "draft_text": "",
            },
            {
                "platform": marketplace,
                "source_type": "review",
                "source_id": "empty-reply",
                "action_type": "public_review_reply",
                "draft_text": "   ",
            },
        ]
        messenger_actions = [
            {
                "platform": "ozon",
                "source_type": "messenger",
                "chat_id": "manual-chat",
                "action_type": "manual_chat_review",
                "draft_reply": "",
            }
        ] if marketplace == "ozon" else []
    else:
        review_actions = [
            {
                "platform": marketplace,
                "source_type": "review",
                "source_id": "answered-review",
                "action_type": "public_review_reply",
                "draft_text": "Approved reply",
            }
        ]
        messenger_actions = [
            {
                "platform": "ozon",
                "source_type": "messenger",
                "chat_id": "answered-chat",
                "from_message_id": "42",
                "action_type": "send_chat_message",
                "draft_reply": "Approved reply",
            }
        ] if marketplace == "ozon" else []
        if case == "fully_receipted":
            inbox_workflow._record_inbox_receipts(  # noqa: SLF001 - pins durable deduplication.
                data_dir=data_dir,
                source_run_id=run_id,
                actions=[*review_actions, *messenger_actions],
            )
    reviews_path.write_text(
        json.dumps({"actions": review_actions}),
        encoding="utf-8",
    )

    def reviews_summary(**kwargs: object) -> dict[str, object]:
        return {
            "run_id": f"{run_id}_reviews",
            "pending_id": reviews_pending_id,
            "overall_status": "ok",
            "actions_count": len(review_actions),
            "verification_warnings": [],
            "artifacts": {
                "actions": str(reviews_path),
                "report": "",
                "summary": "",
            },
        }

    monkeypatch.setattr(inbox_workflow, "run_reviews_questions", reviews_summary)
    monkeypatch.setattr(
        inbox_workflow,
        "_collect_ozon_messenger_actions",
        lambda **kwargs: {"status": "ok", "actions": messenger_actions},
    )
    monkeypatch.setattr(
        inbox_workflow,
        "_collect_wb_notifications",
        lambda **kwargs: {
            "status": "ok",
            "source": "offline-test",
            "items": [],
            "important_items": [],
            "raw_path": "",
        },
    )
    store = JobStore(tmp_path / "runtime.db")
    service = JobService(
        store=store,
        workflow_runner=WorkflowRunner(
            data_dir=data_dir,
            lock_dir=tmp_path / "locks",
            credentials=object(),  # type: ignore[arg-type]
        ),
        data_dir=data_dir,
    )

    result = service.run(
        service.submit(
            task_id=f"{marketplace}-inbox",
            params={"run_id": run_id},
        ).job_id
    )
    summary = result.job.result["summary"]

    assert result.ok is True
    assert summary["actions_count"] > 0
    expected_apply_count = (
        2 if marketplace == "ozon" else 1
    ) if expected_apply_count is None else expected_apply_count
    assert summary["apply_actions_count"] == expected_apply_count
    approvals = store.list_approvals(statuses=("pending_review",), limit=10)
    assert len(approvals) == int(expected_apply_count > 0)


@pytest.mark.parametrize("marketplace", ("ozon", "wb"))
@pytest.mark.parametrize(("state_name", "state_text"), INVALID_INBOX_RECEIPT_STATES)
def test_invalid_durable_receipt_state_blocks_inbox_plan_without_approval(
    marketplace: str,
    state_name: str,
    state_text: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    run_id = f"{marketplace}_inbox_invalid_receipts_{state_name}"
    reviews_pending_id = f"{run_id}_reviews_pending"
    reviews_path = data_dir / "pending" / reviews_pending_id / "draft_answers.json"
    reviews_path.parent.mkdir(parents=True)
    action = {
        "platform": marketplace,
        "source_type": "review",
        "source_id": "review-1",
        "action_type": "public_review_reply",
        "draft_text": "Approved reply",
    }
    reviews_path.write_text(json.dumps({"actions": [action]}), encoding="utf-8")
    receipt_path = data_dir / "state" / "inbox_action_receipts.json"
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text(state_text, encoding="utf-8")

    collection_calls: list[dict[str, object]] = []

    def reviews_summary(**kwargs: object) -> dict[str, object]:
        collection_calls.append(dict(kwargs))
        return {
            "run_id": f"{run_id}_reviews",
            "pending_id": reviews_pending_id,
            "overall_status": "ok",
            "actions_count": 1,
            "verification_warnings": [],
            "artifacts": {
                "actions": str(reviews_path),
                "report": "",
                "summary": "",
            },
        }

    monkeypatch.setattr(inbox_workflow, "run_reviews_questions", reviews_summary)
    monkeypatch.setattr(
        inbox_workflow,
        "_collect_ozon_messenger_actions",
        lambda **kwargs: {"status": "ok", "actions": []},
    )
    monkeypatch.setattr(
        inbox_workflow,
        "_collect_wb_notifications",
        lambda **kwargs: {
            "status": "ok",
            "source": "offline-test",
            "items": [],
            "important_items": [],
            "raw_path": "",
        },
    )
    store = JobStore(tmp_path / "runtime.db")
    service = JobService(
        store=store,
        workflow_runner=WorkflowRunner(
            data_dir=data_dir,
            lock_dir=tmp_path / "locks",
            credentials=object(),  # type: ignore[arg-type]
        ),
        data_dir=data_dir,
    )

    result = service.run(
        service.submit(
            task_id=f"{marketplace}-inbox",
            params={"run_id": run_id},
        ).job_id
    )

    assert result.ok is False
    assert result.status == "runtime_state_invalid"
    assert result.job.error == "inbox_receipt_state_invalid"
    assert "secret-state-value" not in result.job.error
    assert store.list_approvals(statuses=("pending_review",), limit=10) == []
    assert receipt_path.read_text(encoding="utf-8") == state_text
    assert collection_calls == []


@pytest.mark.parametrize("marketplace", ("ozon", "wb"))
@pytest.mark.parametrize(("state_name", "state_text"), INVALID_INBOX_RECEIPT_STATES)
def test_invalid_durable_receipt_state_blocks_inbox_apply_before_write(
    marketplace: str,
    state_name: str,
    state_text: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_run_id = f"{marketplace}_inbox_invalid_apply_{state_name}"
    reviews_pending_id = f"{source_run_id}_reviews_pending"
    reviews_dir = tmp_path / "pending" / reviews_pending_id
    inbox_dir = tmp_path / "pending" / f"{source_run_id}_pending"
    reviews_dir.mkdir(parents=True)
    inbox_dir.mkdir(parents=True)
    action = {
        "platform": marketplace,
        "source_type": "review",
        "source_id": "review-1",
        "action_type": "public_review_reply",
        "draft_text": "Approved reply",
    }
    (reviews_dir / "draft_answers.json").write_text(
        json.dumps({"actions": [action]}),
        encoding="utf-8",
    )
    (inbox_dir / "inbox_pending.json").write_text(
        json.dumps(
            {
                "marketplace": marketplace,
                "reviews_pending_id": reviews_pending_id,
                "messenger_actions": [],
            }
        ),
        encoding="utf-8",
    )
    receipt_path = tmp_path / "state" / "inbox_action_receipts.json"
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text(state_text, encoding="utf-8")

    def forbidden_write(**kwargs: object) -> dict[str, object]:
        raise AssertionError("inbox apply side effect must not start")

    monkeypatch.setattr(inbox_workflow, "_prepare_and_apply_reviews", forbidden_write)
    monkeypatch.setattr(inbox_workflow, "_apply_ozon_messenger", forbidden_write)
    apply = (
        inbox_workflow.run_ozon_inbox_apply
        if marketplace == "ozon"
        else inbox_workflow.run_wb_inbox_apply
    )

    with pytest.raises(RuntimeError, match="^inbox_receipt_state_invalid$"):
        apply(
            credentials=object(),  # type: ignore[arg-type]
            data_dir=tmp_path,
            source_run_id=source_run_id,
            confirmed_by_user=True,
        )

    assert receipt_path.read_text(encoding="utf-8") == state_text
    assert not inbox_workflow.apply_marker_for(
        data_dir=tmp_path,
        approved_id=f"{source_run_id}_owner_approved",
    ).exists()


def test_invalid_receipt_state_blocks_jobservice_before_inbox_apply_handler(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    receipt_path = data_dir / "state" / "inbox_action_receipts.json"
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text("{", encoding="utf-8")
    registry = default_task_registry()
    task = registry.get("wb-inbox-apply")
    source_run_id = "wb_inbox_precondition_block"
    package = build_approval_package(
        approval_id="approval-inbox-precondition-block",
        task_id=task.name,
        source_plan_task=task.source_plan_task,
        verify_task=task.verify_task,
        source_kind="source_run_id",
        source_ref=json.dumps(source_run_id),
        apply_params={"source_run_id": source_run_id},
        marketplaces=task.marketplaces,
    )
    store = JobStore(tmp_path / "runtime.db")
    approval_before = store.ensure_approval(
        approval_id=package["approval_id"],
        source_job_id="source-job",
        status="approved",
        checksum=package["approval_checksum"],
        data=package,
    )

    def forbidden_handler(task, data_dir, credentials, inputs):  # type: ignore[no-untyped-def]
        raise AssertionError("inbox apply handler must not run")

    service = JobService(
        store=store,
        registry=registry,
        workflow_runner=WorkflowRunner(
            registry=registry,
            data_dir=data_dir,
            lock_dir=tmp_path / "locks",
            handlers={task.name: forbidden_handler},
        ),
        data_dir=data_dir,
    )

    result = service.run(
        service.submit_approval_apply(package["approval_id"]).job_id
    )
    event_types = {
        event.event_type for event in store.list_events(job_id=result.job.job_id)
    }

    assert result.ok is False
    assert result.status == "runtime_state_invalid"
    assert result.job.error == "inbox_receipt_state_invalid"
    assert store.get_approval(package["approval_id"]) == approval_before
    assert "job_approval_reserved" not in event_types
    assert "job_write_window_started" not in event_types


def test_invalid_receipt_state_keeps_verify_manual_and_safe(tmp_path: Path) -> None:
    source_run_id = "wb_inbox_invalid_receipts_verify"
    reviews_pending_id = f"{source_run_id}_reviews_pending"
    reviews_dir = tmp_path / "pending" / reviews_pending_id
    inbox_dir = tmp_path / "pending" / f"{source_run_id}_pending"
    reviews_dir.mkdir(parents=True)
    inbox_dir.mkdir(parents=True)
    action = {
        "platform": "wb",
        "source_type": "review",
        "source_id": "review-verify",
        "action_type": "public_review_reply",
        "draft_text": "Approved reply",
    }
    (reviews_dir / "draft_answers.json").write_text(
        json.dumps({"actions": [action]}),
        encoding="utf-8",
    )
    (inbox_dir / "inbox_pending.json").write_text(
        json.dumps(
            {
                "marketplace": "wb",
                "reviews_pending_id": reviews_pending_id,
                "messenger_actions": [],
            }
        ),
        encoding="utf-8",
    )
    receipt_path = tmp_path / "state" / "inbox_action_receipts.json"
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text("{", encoding="utf-8")

    result = inbox_workflow.run_wb_inbox_verify(
        data_dir=tmp_path,
        source_run_id=source_run_id,
        run_id="verify-invalid-receipts",
    )

    assert result["overall_status"] == "warning"
    assert result["verification_confirmed"] is False
    assert result["manual_verification_required"] is True
    assert "receipt_state_invalid" in result["verification"]["reason_codes"]
    assert "secret" not in json.dumps(result)


def test_wb_best_price_plan_without_discount_changes_does_not_create_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_change_plan(**kwargs: object) -> dict[str, object]:
        return {
            "run_id": "wb_best_price_no_discount_changes",
            "overall_status": "ok",
            "summary": {"scope_total": 18, "to_change_discount": 0},
            "artifacts": {},
        }

    monkeypatch.setattr(
        workflow_runner_module,
        "run_wb_best_price_action_plan",
        no_change_plan,
    )
    store = JobStore(tmp_path / "runtime.db")
    service = JobService(
        store=store,
        workflow_runner=WorkflowRunner(
            data_dir=tmp_path / "data",
            lock_dir=tmp_path / "locks",
            credentials=object(),  # type: ignore[arg-type]
        ),
        data_dir=tmp_path / "data",
    )

    result = service.run(
        service.submit(task_id="wb-best-price-action-plan").job_id
    )

    assert result.ok is True
    assert result.job.result["summary"]["approval_candidate"]["action_count"] == 0
    assert store.list_approvals(statuses=("pending_review",), limit=10) == []


@pytest.mark.parametrize(
    ("task_id", "planner_name", "summary"),
    (
        (
            "ozon-cpc-optimization-plan",
            "run_ozon_cpc_optimization_plan",
            {"action_rows_with_current_bid": 3, "apply_payload_rows": 0},
        ),
        (
            "wb-promotion-bid-plan",
            "run_wb_promotion_bid_plan",
            {"changed_rows": 4, "apply_payload_rows": 0},
        ),
        (
            "ozon-inbox",
            "run_ozon_inbox_triage",
            {"actions_count": 2, "apply_actions_count": 0},
        ),
        (
            "wb-promotion-bid-parser-enriched-plan",
            "run_wb_promotion_bid_parser_enriched_plan",
            {"apply_ready_rows": 2, "apply_payload_rows": 0},
        ),
    ),
)
def test_non_payload_plan_rows_do_not_create_runtime_approval(
    task_id: str,
    planner_name: str,
    summary: dict[str, int],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def plan(**kwargs: object) -> dict[str, object]:
        return {
            "run_id": f"{task_id}-non-payload",
            "overall_status": "ok",
            "summary": summary,
            "artifacts": {},
        }

    monkeypatch.setattr(workflow_runner_module, planner_name, plan)
    store = JobStore(tmp_path / "runtime.db")
    service = JobService(
        store=store,
        workflow_runner=WorkflowRunner(
            data_dir=tmp_path / "data",
            lock_dir=tmp_path / "locks",
            credentials=object(),  # type: ignore[arg-type]
        ),
        data_dir=tmp_path / "data",
    )

    result = service.run(service.submit(task_id=task_id).job_id)

    assert result.ok is True
    assert result.job.result["summary"]["approval_candidate"]["action_count"] == 0
    assert store.list_approvals(statuses=("pending_review",), limit=10) == []


def test_ozon_actions_switch_payload_creates_runtime_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def switch_plan(**kwargs: object) -> dict[str, object]:
        return {
            "run_id": "ozon_actions_switch_payload",
            "overall_status": "ok",
            "summary": {
                "recommended_add": 0,
                "recommended_update": 0,
                "recommended_switch_review": 1,
            },
            "artifacts": {},
        }

    monkeypatch.setattr(
        workflow_runner_module,
        "run_ozon_actions_optimizer_plan",
        switch_plan,
    )
    store = JobStore(tmp_path / "runtime.db")
    service = JobService(
        store=store,
        workflow_runner=WorkflowRunner(
            data_dir=tmp_path / "data",
            lock_dir=tmp_path / "locks",
            credentials=object(),  # type: ignore[arg-type]
        ),
        data_dir=tmp_path / "data",
    )

    result = service.run(service.submit(task_id="ozon-actions-optimizer-plan").job_id)
    approvals = store.list_approvals(statuses=("pending_review",), limit=10)

    assert result.ok is True
    assert result.job.result["summary"]["approval_candidate"]["action_count"] == 1
    assert len(approvals) == 1
