from __future__ import annotations

from dataclasses import asdict, dataclass, field
import inspect
from typing import Any, Callable, Mapping

from seller_agent.core.run_manifest import ManifestMode, ManifestRisk
from seller_agent.core.resource_keys import (
    canonical_api_write_key,
    is_canonical_api_write_key,
    is_canonical_lk_profile_key,
    task_resource_keys,
)


TaskHandler = Callable[..., dict[str, Any]]
TaskExecutor = str


def _apply_parameter_schema(
    source_field: str = "plan_run_id",
    **business_fields: dict[str, Any],
) -> dict[str, Any]:
    schema = {
        source_field: {"type": "string", "minLength": 1, "required": True},
        **business_fields,
        "approval_id": {"type": "string", "minLength": 1, "required": True},
        "approval_checksum": {"type": "string", "minLength": 1, "required": True},
        "confirmed_by_user": {"type": "boolean", "const": True, "required": True},
    }
    return schema


def _apply_result_schema() -> dict[str, Any]:
    return {
        "overall_status": {
            "type": "string",
            "enum": ["ok", "warning", "partial", "blocked", "error"],
            "required": True,
        },
        "run_id": {"type": "string", "minLength": 1, "required": True},
    }


@dataclass(frozen=True)
class RegisteredTask:
    name: str
    command: str
    title: str
    description: str
    mode: ManifestMode
    risk: ManifestRisk
    marketplaces: tuple[str, ...] = ()
    runbook_path: str = ""
    requires_credentials: bool = False
    requires_lk: bool = False
    requires_mapping: bool = False
    requires_confirmation: bool = False
    telegram_enabled: bool = False
    telegram_button_label: str = ""
    handler: TaskHandler | None = None
    aliases: tuple[str, ...] = field(default_factory=tuple)
    executor: TaskExecutor = "script"
    parameter_schema: dict[str, Any] = field(default_factory=dict)
    result_schema: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: int = 0
    lock_keys: tuple[str, ...] = field(default_factory=tuple)
    source_plan_task: str = ""
    verify_task: str = ""
    approval_source_field: str = ""
    supports_cancel: bool = False
    enabled: bool = True
    disabled_reason: str = ""

    @property
    def is_read_only(self) -> bool:
        return self.mode == "read_only"

    @property
    def is_write(self) -> bool:
        return self.mode == "apply" or self.requires_confirmation

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("handler", None)
        data["marketplaces"] = list(self.marketplaces)
        data["aliases"] = list(self.aliases)
        data["lock_keys"] = list(self.lock_keys)
        data["is_read_only"] = self.is_read_only
        data["is_write"] = self.is_write
        data["policy_issues"] = self.policy_issues()
        return data

    def policy_issues(self) -> list[str]:
        issues: list[str] = []
        if not self.enabled:
            if self.is_write:
                if not self.disabled_reason.strip():
                    issues.append("disabled_write_missing_reason")
                if self.telegram_enabled:
                    issues.append("disabled_write_telegram_exposed")
            return issues
        if self.is_write:
            if self.mode != "apply":
                issues.append("write_mode_not_apply")
            if not self.requires_confirmation:
                issues.append("apply_requires_confirmation")
            if not self.source_plan_task:
                issues.append("apply_missing_source_plan_task")
            if not self.verify_task:
                issues.append("apply_missing_verify_task")
            if not self.lock_keys:
                issues.append("apply_missing_lock_keys")
            if not self.parameter_schema:
                issues.append("apply_missing_parameter_schema")
            if not self.result_schema:
                issues.append("apply_missing_result_schema")
        if self.telegram_enabled and not self.telegram_button_label:
            issues.append("telegram_missing_button_label")
        if self.executor not in {"script", "agent", "hybrid"}:
            issues.append("invalid_executor")
        return issues


class TaskRegistry:
    def __init__(self) -> None:
        self._tasks: dict[str, RegisteredTask] = {}
        self._aliases: dict[str, str] = {}

    def register(self, task: RegisteredTask) -> None:
        if task.name in self._tasks:
            raise ValueError(f"Task already registered: {task.name}")
        if task.command in self._aliases:
            raise ValueError(f"Task command already registered: {task.command}")
        self._tasks[task.name] = task
        self._aliases[task.name] = task.name
        self._aliases[task.command] = task.name
        for alias in task.aliases:
            if alias in self._aliases:
                raise ValueError(f"Task alias already registered: {alias}")
            self._aliases[alias] = task.name

    def get(self, name: str) -> RegisteredTask:
        try:
            return self._tasks[self._aliases[name]]
        except KeyError as exc:
            raise KeyError(f"Unknown task: {name}") from exc

    def names(self) -> list[str]:
        return sorted(self._tasks)

    def commands(self) -> list[str]:
        return sorted(task.command for task in self._tasks.values())

    def list(
        self,
        *,
        mode: str | None = None,
        risk: str | None = None,
        marketplace: str | None = None,
        telegram_only: bool = False,
    ) -> list[RegisteredTask]:
        tasks = list(self._tasks.values())
        if mode:
            tasks = [task for task in tasks if task.mode == mode]
        if risk:
            tasks = [task for task in tasks if task.risk == risk]
        if marketplace:
            tasks = [task for task in tasks if marketplace in task.marketplaces or marketplace == "all"]
        if telegram_only:
            tasks = [task for task in tasks if task.telegram_enabled]
        return sorted(tasks, key=lambda task: task.command)

    def to_list(self, **filters: Any) -> list[dict[str, Any]]:
        return [task.to_dict() for task in self.list(**filters)]

    def policy_issues(
        self,
        *,
        handlers: Mapping[str, object] | None = None,
    ) -> list[dict[str, Any]]:
        if handlers is None:
            # Lazy import avoids a registry -> runner -> registry import cycle.
            from seller_agent.core.workflow_runner import default_workflow_handlers

            handlers = default_workflow_handlers()
        issues: list[dict[str, Any]] = []

        def append_issue(task: RegisteredTask, issue: str) -> None:
            issues.append(
                {
                    "task": task.name,
                    "command": task.command,
                    "mode": task.mode,
                    "risk": task.risk,
                    "issue": issue,
                }
            )

        for task in self.list():
            for issue in task.policy_issues():
                append_issue(task, issue)
            if not task.enabled or not task.is_write:
                continue
            if task.name not in handlers:
                append_issue(task, "apply_missing_runtime_handler")
            elif not callable(handlers[task.name]):
                append_issue(task, "apply_runtime_handler_not_callable")
            elif not _valid_runtime_handler_signature(handlers[task.name]):
                append_issue(task, "apply_runtime_handler_signature_invalid")
            if not _valid_apply_parameter_schema(task):
                append_issue(task, "apply_parameter_schema_invalid")
            if not _valid_apply_result_schema(task.result_schema):
                append_issue(task, "apply_result_schema_invalid")
            for lock_key in task.lock_keys:
                if not _is_canonical_lock_key(lock_key):
                    append_issue(task, "apply_invalid_lock_key")
            expected_api_write_keys = {
                canonical_api_write_key(marketplace)
                for marketplace in task.marketplaces
            }
            actual_api_write_keys = {
                lock_key
                for lock_key in task.lock_keys
                if is_canonical_api_write_key(lock_key)
            }
            if actual_api_write_keys != expected_api_write_keys:
                append_issue(task, "apply_api_write_locks_mismatch")
            if task.source_plan_task:
                source = self._tasks.get(task.source_plan_task)
                if source is None:
                    append_issue(task, "apply_source_plan_not_registered")
                else:
                    if not source.enabled:
                        append_issue(task, "apply_source_plan_disabled")
                    if source.mode != "dry_run" or source.is_write:
                        append_issue(task, "apply_source_plan_unsafe_mode")
                    if frozenset(source.marketplaces) != frozenset(task.marketplaces):
                        append_issue(task, "apply_source_plan_marketplaces_mismatch")
                    if source.name not in handlers:
                        append_issue(task, "apply_source_plan_missing_runtime_handler")
                    elif not callable(handlers[source.name]):
                        append_issue(task, "apply_source_plan_runtime_handler_not_callable")
                    elif not _valid_runtime_handler_signature(handlers[source.name]):
                        append_issue(
                            task,
                            "apply_source_plan_runtime_handler_signature_invalid",
                        )
            if task.verify_task:
                verify = self._tasks.get(task.verify_task)
                if verify is None:
                    append_issue(task, "apply_verify_not_registered")
                else:
                    if not verify.enabled:
                        append_issue(task, "apply_verify_disabled")
                    if verify.mode != "verify" or verify.is_write:
                        append_issue(task, "apply_verify_mode_invalid")
                    if frozenset(verify.marketplaces) != frozenset(task.marketplaces):
                        append_issue(task, "apply_verify_marketplaces_mismatch")
                    if verify.name not in handlers:
                        append_issue(task, "apply_verify_missing_runtime_handler")
                    elif not callable(handlers[verify.name]):
                        append_issue(task, "apply_verify_runtime_handler_not_callable")
                    elif not _valid_runtime_handler_signature(handlers[verify.name]):
                        append_issue(
                            task,
                            "apply_verify_runtime_handler_signature_invalid",
                        )
        return issues


def _valid_runtime_handler_signature(handler: object) -> bool:
    try:
        parameters = tuple(inspect.signature(handler).parameters.values())
    except (TypeError, ValueError):
        return False
    expected_names = ("task", "data_dir", "credentials", "inputs")
    return bool(
        len(parameters) == len(expected_names)
        and tuple(parameter.name for parameter in parameters) == expected_names
        and all(
            parameter.kind
            in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
            and parameter.default is inspect.Parameter.empty
            for parameter in parameters
        )
    )


def _required_string_field(schema: Mapping[str, Any], field_name: str) -> bool:
    field_schema = schema.get(field_name)
    return bool(
        isinstance(field_schema, Mapping)
        and field_schema.get("type") == "string"
        and field_schema.get("required") is True
        and type(field_schema.get("minLength")) is int
        and field_schema["minLength"] >= 1
    )


def _valid_apply_parameter_schema(task: RegisteredTask) -> bool:
    schema = task.parameter_schema
    source_field = task.approval_source_field
    if source_field not in {"plan_run_id", "source_run_id"}:
        return False
    if not _required_string_field(schema, source_field):
        return False
    conflicting_source = (
        {"plan_run_id", "source_run_id"} - {source_field}
    ).intersection(schema)
    if conflicting_source:
        return False
    if not _required_string_field(schema, "approval_id"):
        return False
    if not _required_string_field(schema, "approval_checksum"):
        return False
    confirmation = schema.get("confirmed_by_user")
    return bool(
        isinstance(confirmation, Mapping)
        and confirmation.get("type") == "boolean"
        and confirmation.get("required") is True
        and confirmation.get("const") is True
        and not {
            "runtime_approval_id",
            "runtime_recovery_approval_id",
        }.intersection(schema)
    )


def _valid_apply_result_schema(schema: Mapping[str, Any]) -> bool:
    overall_status = schema.get("overall_status")
    if not (
        isinstance(overall_status, Mapping)
        and overall_status.get("type") == "string"
        and overall_status.get("required") is True
    ):
        return False
    allowed_statuses = overall_status.get("enum")
    expected_statuses = {"ok", "warning", "partial", "blocked", "error"}
    if not (
        isinstance(allowed_statuses, list)
        and len(allowed_statuses) == len(expected_statuses)
        and all(isinstance(value, str) for value in allowed_statuses)
        and set(allowed_statuses) == expected_statuses
    ):
        return False
    return _required_string_field(schema, "run_id")


def _is_canonical_lock_key(value: str) -> bool:
    key = str(value or "")
    if not key or any(character.isspace() for character in key):
        return False
    if key.startswith("lk:"):
        return is_canonical_lk_profile_key(key)
    if key.startswith("api:"):
        return is_canonical_api_write_key(key)
    return not (
        key.endswith("-lk")
        or key.startswith("marketplace:")
    )


def default_task_registry() -> TaskRegistry:
    registry = TaskRegistry()
    for task in DEFAULT_TASKS:
        registry.register(task)
    return registry


def get_task_definition(name: str) -> dict[str, Any]:
    return default_task_registry().get(name).to_dict()


def list_task_definitions(
    *,
    mode: str | None = None,
    risk: str | None = None,
    marketplace: str | None = None,
    telegram_only: bool = False,
) -> list[dict[str, Any]]:
    return default_task_registry().to_list(
        mode=mode,
        risk=risk,
        marketplace=marketplace,
        telegram_only=telegram_only,
    )


DEFAULT_TASKS: tuple[RegisteredTask, ...] = (
    RegisteredTask(
        name="runs",
        command="runs",
        title="Run history",
        description="List and inspect RunManifest entries from data/runs/index.jsonl.",
        mode="read_only",
        risk="none",
        runbook_path="data/planning/run_manifest_runbook.md",
        telegram_enabled=True,
        telegram_button_label="/runs",
    ),
    RegisteredTask(
        name="jobs",
        command="jobs",
        title="Runtime jobs",
        description="Submit, run and inspect SQLite runtime jobs.",
        mode="maintenance",
        risk="low",
        runbook_path="data/planning/runtime_job_store_plan.md",
        telegram_enabled=True,
        telegram_button_label="/jobs",
    ),
    RegisteredTask(
        name="approvals",
        command="approvals",
        title="Approvals lifecycle",
        description="List or close pending/approved approval packages.",
        mode="maintenance",
        risk="low",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/run_manifest_runbook.md",
        telegram_enabled=True,
        telegram_button_label="/approvals",
    ),
    RegisteredTask(
        name="tasks",
        command="tasks",
        title="Task registry",
        description="List task metadata for CLI, agents and future Telegram bot.",
        mode="read_only",
        risk="none",
        runbook_path="data/planning/telegram_bot_management_transition_plan.md",
        telegram_enabled=True,
        telegram_button_label="/help",
    ),
    RegisteredTask(
        name="bot-preview",
        command="bot",
        title="Telegram read-only adapter",
        description="Preview or send read-only Telegram MVP responses without write actions.",
        mode="read_only",
        risk="none",
        runbook_path="data/planning/telegram_bot_mvp_runbook.md",
    ),
    RegisteredTask(
        name="telegram-send-report",
        command="send-telegram-report",
        title="Send Telegram report",
        description="Send an owner-facing Telegram summary and attach a safe saved report file.",
        mode="maintenance",
        risk="low",
        runbook_path="data/planning/chat_report_templates.md",
    ),
    RegisteredTask(
        name="catalog-fetch",
        command="fetch-catalog",
        title="Catalog fetch",
        description="Fetch Ozon/WB catalogs and build read-only master catalog report.",
        mode="read_only",
        risk="low",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/catalog_mapping_runbook.md",
        requires_credentials=True,
    ),
    RegisteredTask(
        name="catalog-build-unified",
        command="build-unified-catalog",
        title="Build unified catalog",
        description=(
            "Build internal product-level catalog from confirmed Ozon/WB mapping "
            "and processed marketplace catalogs."
        ),
        mode="read_only",
        risk="low",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/catalog_mapping_runbook.md",
        requires_mapping=True,
        telegram_enabled=True,
        telegram_button_label="/catalog",
    ),
    RegisteredTask(
        name="catalog-internal-sku-plan",
        command="plan-internal-skus",
        title="Plan internal SKUs",
        description=(
            "Build read-only internal SKU proposal plan for Ozon-only and WB-only "
            "products from unified catalog."
        ),
        mode="read_only",
        risk="low",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/catalog_mapping_runbook.md",
        requires_mapping=True,
    ),
    RegisteredTask(
        name="catalog-content-master",
        command="build-content-master",
        title="Build content master",
        description=(
            "Build read-only unified content master and card-work audit from "
            "unified catalog, processed Ozon/WB catalogs and optional pricing status."
        ),
        mode="read_only",
        risk="low",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/product_card_work_runbook.md",
        requires_mapping=True,
    ),
    RegisteredTask(
        name="card-content-snapshot",
        command="fetch-card-content",
        title="Fetch card content snapshots",
        description=(
            "Fetch read-only Ozon/WB card content snapshots: descriptions, "
            "attributes, dimensions, photos count and tags for content master and SEO audits."
        ),
        mode="read_only",
        risk="low",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/product_card_work_runbook.md",
        requires_credentials=True,
        requires_mapping=True,
    ),
    RegisteredTask(
        name="card-content-audit-backlog",
        command="card-content-audit-backlog",
        title="Card content audit backlog",
        description=(
            "Build read-only product backlog for visual card audit, SEO/content "
            "unification and marketplace transfer review from content master; "
            "optionally enrich priorities with sales, stock and parser visibility CSV signals."
        ),
        mode="read_only",
        risk="low",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/product_card_work_runbook.md",
        requires_mapping=True,
    ),
    RegisteredTask(
        name="card-content-signals",
        command="collect-card-signals",
        title="Collect card content signals",
        description=(
            "Collect read-only normalized sales, stock and parser visibility CSV signals "
            "for card content audit backlog."
        ),
        mode="read_only",
        risk="low",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/product_card_work_runbook.md",
        requires_credentials=True,
        requires_mapping=True,
    ),
    RegisteredTask(
        name="card-content-parameter-inventory",
        command="card-content-parameter-inventory",
        title="Card content parameter inventory",
        description=(
            "Build read-only inventory of actually filled Ozon/WB card parameters "
            "and marketplace category/subject schemas before product-passport design."
        ),
        mode="read_only",
        risk="low",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/product_card_work_runbook.md",
        requires_credentials=True,
        requires_mapping=True,
    ),
    RegisteredTask(
        name="product-passport-design",
        command="design-product-passport",
        title="Design master product passport",
        description=(
            "Build read-only master product passport schema and internal field "
            "mapping to Ozon attributes and WB characteristics before mass card audits."
        ),
        mode="read_only",
        risk="low",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/master_product_passport_runbook.md",
        requires_mapping=True,
    ),
    RegisteredTask(
        name="card-content-audit-packages",
        command="card-content-audit-packages",
        title="Card content audit packages",
        description=(
            "Build saved read-only source packages for card audits from backlog, "
            "Ozon/WB snapshots and product-passport schema. Visual audit and "
            "recommendations remain pending until an agent inspects all photos."
        ),
        mode="read_only",
        risk="low",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/product_card_work_runbook.md",
        requires_mapping=True,
    ),
    RegisteredTask(
        name="seo-query-pack",
        command="seo-query-pack",
        title="SEO query pack",
        description=(
            "Build read-only Ozon/WB search query tables and target query clusters "
            "for product card auditors."
        ),
        mode="read_only",
        risk="low",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/search_queries_runbook.md",
        requires_mapping=True,
    ),
    RegisteredTask(
        name="pricing-status",
        command="pricing-status",
        title="Pricing status",
        description=(
            "Build read-only Ozon/WB price and margin-readiness report from "
            "unified catalog and local price snapshots."
        ),
        mode="read_only",
        risk="low",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/pricing_runbook.md",
        requires_mapping=True,
    ),
    RegisteredTask(
        name="ozon-pricing-margin",
        command="ozon-pricing-margin",
        title="Ozon pricing and margin calculator",
        description=(
            "Build a read-only Ozon FBO expense model and price ladder from owner-provided "
            "unit cost, target margin and a completed 15- or 30-day finance period."
        ),
        mode="dry_run",
        risk="low",
        marketplaces=("ozon",),
        runbook_path="data/planning/pricing_margin_button_plan.md",
        requires_credentials=True,
        requires_mapping=True,
        telegram_enabled=True,
        telegram_button_label="/ozon-pricing-margin",
        parameter_schema={
            "unit_cost": {"type": "number", "exclusiveMinimum": 0, "required": True},
            "target_margin": {"type": "number", "minimum": 0, "required": True},
            "period_days": {"type": "integer", "enum": [15, 30], "required": True},
        },
        timeout_seconds=900,
    ),
    RegisteredTask(
        name="wb-pricing-margin",
        command="wb-pricing-margin",
        title="WB pricing and margin calculator",
        description=(
            "Build a WB expense model and price ladder from owner-provided unit cost, "
            "target margin and a completed 15- or 30-day finance period."
        ),
        mode="dry_run",
        risk="low",
        marketplaces=("wb",),
        runbook_path="data/planning/pricing_margin_button_plan.md",
        requires_credentials=True,
        requires_mapping=True,
        telegram_enabled=True,
        telegram_button_label="/wb-pricing-margin",
        parameter_schema={
            "unit_cost": {"type": "number", "exclusiveMinimum": 0, "required": True},
            "target_margin": {"type": "number", "minimum": 0, "required": True},
            "period_days": {"type": "integer", "enum": [15, 30], "required": True},
        },
        timeout_seconds=900,
        lock_keys=task_resource_keys(lk_marketplaces=("wb",)),
    ),
    RegisteredTask(
        name="status-preflight",
        command="status-preflight",
        title="Status preflight",
        description="Run read-only project preflight for APIs, LK sessions and master catalog.",
        mode="read_only",
        risk="none",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/status_preflight_runbook.md",
        requires_credentials=True,
        telegram_enabled=True,
        telegram_button_label="/status",
        lock_keys=task_resource_keys(lk_marketplaces=("ozon", "wb")),
    ),
    RegisteredTask(
        name="daily-morning-report",
        command="daily-morning-report",
        title="Daily morning report",
        description="Build read-only daily management report for Ozon/WB.",
        mode="read_only",
        risk="low",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/daily_morning_report_runbook.md",
        requires_credentials=True,
        telegram_enabled=True,
        telegram_button_label="/today",
        lock_keys=task_resource_keys(lk_marketplaces=("ozon", "wb")),
    ),
    RegisteredTask(
        name="liquidation-daily-control",
        command="liquidation-daily-control",
        title="Liquidation daily control",
        description=(
            "Build the daily read-only Ozon/WB liquidation control for the exact approved "
            "cohorts and produce a checksummed stop-review without marketplace writes."
        ),
        mode="read_only",
        risk="low",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/liquidation_daily_control_runbook.md",
        requires_credentials=True,
        telegram_enabled=True,
        telegram_button_label="/liquidation-control",
        parameter_schema={"date_to": {"type": "string", "format": "date"}},
        timeout_seconds=1800,
        lock_keys=("analytics:liquidation-control",),
    ),
    RegisteredTask(
        name="liquidation-stop-plan",
        command="liquidation-stop-plan",
        title="Liquidation CPC hard-stop plan",
        description="Build an exact fresh CPC removal plan from a checksummed liquidation stop-review.",
        mode="dry_run",
        risk="normal",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/liquidation_daily_control_runbook.md",
        requires_credentials=True,
        parameter_schema={"source_run_id": {"type": "string", "minLength": 1, "required": True}},
        timeout_seconds=600,
        lock_keys=("analytics:liquidation-stop",),
    ),
    RegisteredTask(
        name="liquidation-stop-apply",
        command="apply-liquidation-stop",
        title="Apply liquidation CPC hard stops",
        description="Remove only owner-approved hard-stop products from current Ozon/WB CPC campaigns.",
        mode="apply",
        risk="high",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/liquidation_daily_control_runbook.md",
        requires_credentials=True,
        requires_confirmation=True,
        approval_source_field="plan_run_id",
        parameter_schema=_apply_parameter_schema(),
        result_schema=_apply_result_schema(),
        source_plan_task="liquidation-stop-plan",
        verify_task="liquidation-stop-verify",
        timeout_seconds=600,
        lock_keys=task_resource_keys(
            api_write_marketplaces=("ozon", "wb"),
            subject_keys=("promotion:ozon:cpc-membership", "promotion:wb:cpc-membership"),
        ),
    ),
    RegisteredTask(
        name="liquidation-stop-verify",
        command="verify-liquidation-stop",
        title="Verify liquidation CPC hard stops",
        description="Verify exact approved products are absent from their Ozon/WB CPC campaigns.",
        mode="verify",
        risk="low",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/liquidation_daily_control_runbook.md",
        requires_credentials=True,
        parameter_schema={"plan_run_id": {"type": "string", "minLength": 1, "required": True}},
        timeout_seconds=300,
    ),
    RegisteredTask(
        name="wb-liquidation-stage2-plan",
        command="wb-liquidation-stage2-plan",
        title="WB liquidation second price stage",
        description="Build a fresh dry-run for only the 18 approved WB second-stage liquidation rows.",
        mode="dry_run",
        risk="normal",
        marketplaces=("wb",),
        runbook_path="data/planning/wb_promotion_runbook.md",
        requires_credentials=True,
        telegram_enabled=True,
        telegram_button_label="/wb-liquidation-stage2",
        timeout_seconds=600,
    ),
    RegisteredTask(
        name="wb-liquidation-stage2-apply",
        command="apply-wb-liquidation-stage2",
        title="Apply WB liquidation second price stage",
        description="Apply the exact owner-approved WB liquidation second-stage discounts after zero-drift checks.",
        mode="apply",
        risk="high",
        marketplaces=("wb",),
        runbook_path="data/planning/liquidation_daily_control_runbook.md",
        requires_credentials=True,
        requires_confirmation=True,
        approval_source_field="plan_run_id",
        parameter_schema=_apply_parameter_schema(),
        result_schema=_apply_result_schema(),
        source_plan_task="wb-liquidation-stage2-plan",
        verify_task="wb-liquidation-stage2-verify",
        timeout_seconds=900,
        lock_keys=task_resource_keys(
            api_write_marketplaces=("wb",),
            subject_keys=("actions:wb:discounts",),
        ),
    ),
    RegisteredTask(
        name="wb-liquidation-stage2-verify",
        command="verify-wb-liquidation-stage2",
        title="Verify WB liquidation second price stage",
        description="Read-only verification of the approved WB liquidation second-stage discounts and base prices.",
        mode="verify",
        risk="low",
        marketplaces=("wb",),
        runbook_path="data/planning/liquidation_daily_control_runbook.md",
        requires_credentials=True,
        timeout_seconds=300,
    ),
    RegisteredTask(
        name="ozon-stars-control",
        command="ozon-stars-control",
        title="Ozon Stars control",
        description="Compare 3/7/14 completed days after Stars deactivation with the documented baseline.",
        mode="read_only",
        risk="low",
        marketplaces=("ozon",),
        runbook_path="data/planning/ozon_stars_profitability_runbook.md",
        requires_credentials=True,
        telegram_enabled=True,
        telegram_button_label="/ozon-stars-control",
        parameter_schema={"window_days": {"type": "integer", "enum": [3, 7, 14], "required": True}},
        timeout_seconds=1200,
        lock_keys=("analytics:ozon-stars",),
    ),
    RegisteredTask(
        name="wb-incident-audit",
        command="wb-incident-audit",
        title="WB incident audit",
        description="Reconcile WB stock balance, sales and compensation evidence after warehouse incidents.",
        mode="read_only",
        risk="low",
        marketplaces=("wb",),
        runbook_path="data/planning/supply_planning_runbook.md",
        requires_credentials=True,
        telegram_enabled=True,
        telegram_button_label="/wb-incident-audit",
        timeout_seconds=1200,
        lock_keys=("analytics:wb-incident",),
    ),
    RegisteredTask(
        name="ozon-lk-state-monitor",
        command="ozon-lk-state-monitor",
        title="Ozon LK state monitor",
        description="Track Ozon LK authorization state and notify only on state transitions.",
        mode="read_only",
        risk="none",
        marketplaces=("ozon",),
        runbook_path="data/planning/lk_connection_runbook.md",
        requires_credentials=True,
        requires_lk=True,
        telegram_enabled=True,
        telegram_button_label="/ozon-lk-state",
        timeout_seconds=300,
        lock_keys=task_resource_keys(lk_marketplaces=("ozon",)),
    ),
    RegisteredTask(
        name="ozon-min-price-timer-plan",
        command="ozon-min-price-timer-plan",
        title="Ozon minimum price timer plan",
        description="Read timer status and build a checksummed refresh dry-run before minimum-price protection expires.",
        mode="dry_run",
        risk="normal",
        marketplaces=("ozon",),
        runbook_path="data/planning/pricing_runbook.md",
        requires_credentials=True,
        telegram_enabled=True,
        telegram_button_label="/ozon-min-price-timer",
        timeout_seconds=600,
    ),
    RegisteredTask(
        name="marketplace-period-report",
        command="marketplace-period-report",
        title="Marketplace period report",
        description="Build a read-only Ozon or WB report for an owner-selected period.",
        mode="read_only",
        risk="low",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/marketplace_period_report_runbook.md",
        requires_credentials=True,
        telegram_enabled=True,
        telegram_button_label="/period-report",
        parameter_schema={
            "marketplace": {"type": "string", "enum": ["ozon", "wb"], "required": True},
            "report_type": {"type": "string", "enum": ["short", "financial", "full"], "required": True},
            "date_from": {"type": "string", "format": "date", "required": True},
            "date_to": {"type": "string", "format": "date", "required": True},
        },
    ),
    RegisteredTask(
        name="store-analytics-overview",
        command="store-analytics-overview",
        title="Store analytics overview",
        description=(
            "Compose read-only period, stock and Parser warehouse facts for the owner control Mini App."
        ),
        mode="read_only",
        risk="low",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/stage3_control_plane_runbook.md",
        requires_credentials=True,
        parameter_schema={
            "marketplace": {"type": "string", "enum": ["ozon", "wb"], "required": True},
            "period_days": {"type": "integer", "enum": [7, 30, 90], "required": True},
            "region_id": {
                "type": "string",
                "enum": ["moscow", "rostov-on-don", "novosibirsk", "kazan"],
                "required": True,
            },
            "wb_supplier_id": {"type": "string", "minLength": 1, "maxLength": 32},
            "ozon_seller_slug": {"type": "string", "minLength": 1, "maxLength": 128},
            "query_pack_id": {"type": "string", "const": "shevron-core"},
        },
        result_schema={
            "overall_status": {
                "type": "string",
                "enum": ["ok", "warning"],
                "required": True,
            },
            "run_id": {"type": "string", "minLength": 1, "required": True},
            "marketplace": {
                "type": "string",
                "enum": ["ozon", "wb"],
                "required": True,
            },
            "period_days": {
                "type": "integer",
                "enum": [7, 30, 90],
                "required": True,
            },
            "region_id": {
                "type": "string",
                "enum": ["moscow", "rostov-on-don", "novosibirsk", "kazan"],
                "required": True,
            },
        },
        timeout_seconds=1200,
    ),
    RegisteredTask(
        name="wb-parser-warehouse-analytics",
        command="wb-parser-warehouse-analytics",
        title="WB parser warehouse analytics",
        description=(
            "Build read-only Wildberries visibility analytics from Parser Data API "
            "warehouse endpoints without copying full parser datasets."
        ),
        mode="read_only",
        risk="low",
        marketplaces=("wb",),
        runbook_path="data/planning/wb_parser_positions_runbook.md",
        telegram_enabled=True,
        telegram_button_label="/wb-analytics",
    ),
    RegisteredTask(
        name="wb-stock-supply-monitor",
        command="wb-stock-supply-monitor",
        title="WB stock and supply monitor",
        description=(
            "Build a fresh read-only WB report for warehouse stocks, all active FBW supplies, "
            "acceptance progress, physical pieces and state anomalies."
        ),
        mode="read_only",
        risk="low",
        marketplaces=("wb",),
        runbook_path="data/planning/supply_planning_runbook.md",
        requires_credentials=True,
        telegram_enabled=True,
        telegram_button_label="/wb-stock-supplies",
    ),
    RegisteredTask(
        name="ozon-stock-supply-monitor",
        command="ozon-stock-supply-monitor",
        title="Ozon stock and supply monitor",
        description=(
            "Build a fresh read-only Ozon report for general FBO stock, warehouse stock, "
            "active supply orders, physical pieces and source reconciliation."
        ),
        mode="read_only",
        risk="low",
        marketplaces=("ozon",),
        runbook_path="data/planning/supply_planning_runbook.md",
        requires_credentials=True,
        telegram_enabled=True,
        telegram_button_label="/ozon-stock-supplies",
    ),
    RegisteredTask(
        name="ozon-production-work-plan",
        command="ozon-production-work-plan",
        title="Ozon production work plan",
        description=(
            "Build a read-only Ozon production workbook by physical capacity or coverage days, "
            "selecting the owner-requested number of destination clusters by cluster-local net need."
        ),
        mode="read_only",
        risk="low",
        marketplaces=("ozon",),
        runbook_path="data/planning/supply_planning_runbook.md",
        requires_credentials=True,
        telegram_enabled=True,
        telegram_button_label="/ozon-work-plan",
        parameter_schema={
            "mode": {"type": "string", "enum": ["capacity", "coverage_days"], "required": True},
            "value": {"type": "integer", "minimum": 1, "required": True},
            "cluster_count": {"type": "integer", "minimum": 1, "maximum": 20, "required": True},
        },
    ),
    RegisteredTask(
        name="wb-production-work-plan",
        command="wb-production-work-plan",
        title="WB production work plan",
        description=(
            "Build a read-only WB production workbook by available physical capacity "
            "or owner-selected stock coverage days, with automatic destination-region allocation."
        ),
        mode="read_only",
        risk="low",
        marketplaces=("wb",),
        runbook_path="data/planning/supply_planning_runbook.md",
        requires_credentials=True,
        telegram_enabled=True,
        telegram_button_label="/wb-work-plan",
        parameter_schema={
            "mode": {"type": "string", "enum": ["capacity", "coverage_days"], "required": True},
            "value": {"type": "integer", "minimum": 1, "required": True},
            "cluster_count": {"type": "integer", "minimum": 1, "maximum": 6, "required": True},
        },
    ),
    RegisteredTask(
        name="supply-workbooks-plan",
        command="plan-supply-workbooks",
        title="Supply workbook automation plan",
        description=(
            "Build read-only readiness report for generating Excel supply workbooks "
            "from stocks, 90-day sales, localization and active inbound supplies."
        ),
        mode="read_only",
        risk="low",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/supply_planning_runbook.md",
        requires_credentials=True,
    ),
    RegisteredTask(
        name="ozon-messenger-workflow",
        command="ozon-messenger-workflow",
        title="Ozon Messenger workflow",
        description="Lifecycle for Ozon Messenger triage, approval, apply, verify and cleanup.",
        mode="maintenance",
        risk="normal",
        marketplaces=("ozon",),
        runbook_path="data/planning/ozon_messenger_runbook.md",
        requires_credentials=True,
        requires_confirmation=True,
        enabled=False,
        disabled_reason=(
            "Unsupported combined messenger write workflow has no separated "
            "plan/apply/verify runtime chain; keep disabled pending a P1 design."
        ),
    ),
    RegisteredTask(
        name="ozon-inbox",
        command="ozon-inbox",
        title="Ozon inbox",
        description="Build fresh Ozon reviews/questions plus Messenger/notifications approval package for Telegram.",
        mode="dry_run",
        risk="low",
        marketplaces=("ozon",),
        runbook_path="data/planning/ozon_messenger_runbook.md",
        requires_credentials=True,
        telegram_enabled=True,
        telegram_button_label="/ozon-inbox",
        lock_keys=task_resource_keys(lk_marketplaces=("ozon",)),
    ),
    RegisteredTask(
        name="ozon-inbox-apply",
        command="apply-ozon-inbox",
        title="Apply Ozon inbox",
        description="Apply owner-approved Ozon inbox package: replies, viewed reviews and read notifications.",
        mode="apply",
        risk="low",
        marketplaces=("ozon",),
        runbook_path="data/planning/ozon_messenger_runbook.md",
        requires_credentials=True,
        requires_lk=True,
        requires_confirmation=True,
        approval_source_field="source_run_id",
        parameter_schema=_apply_parameter_schema("source_run_id"),
        result_schema=_apply_result_schema(),
        source_plan_task="ozon-inbox",
        verify_task="ozon-inbox-verify",
        lock_keys=task_resource_keys(
            api_write_marketplaces=("ozon",),
            lk_marketplaces=("ozon",),
            subject_keys=("ozon-inbox",),
        ),
    ),
    RegisteredTask(
        name="ozon-inbox-verify",
        command="verify-ozon-inbox",
        title="Verify Ozon inbox",
        description=(
            "Verify the matching Ozon inbox package from durable per-action "
            "success receipts without repeating replies or mark-read writes."
        ),
        mode="verify",
        risk="low",
        marketplaces=("ozon",),
        runbook_path="data/planning/ozon_messenger_runbook.md",
    ),
    RegisteredTask(
        name="wb-inbox",
        command="wb-inbox",
        title="WB inbox",
        description="Build fresh WB reviews/questions approval package for Telegram; reports WB notifications as a separate route.",
        mode="dry_run",
        risk="low",
        marketplaces=("wb",),
        runbook_path="data/planning/reviews_questions_runbook.md",
        requires_credentials=True,
        telegram_enabled=True,
        telegram_button_label="/wb-inbox",
        lock_keys=task_resource_keys(lk_marketplaces=("wb",)),
    ),
    RegisteredTask(
        name="wb-inbox-apply",
        command="apply-wb-inbox",
        title="Apply WB inbox",
        description="Apply owner-approved WB inbox package: replies to WB reviews and questions.",
        mode="apply",
        risk="low",
        marketplaces=("wb",),
        runbook_path="data/planning/reviews_questions_runbook.md",
        requires_credentials=True,
        requires_confirmation=True,
        approval_source_field="source_run_id",
        parameter_schema=_apply_parameter_schema("source_run_id"),
        result_schema=_apply_result_schema(),
        source_plan_task="wb-inbox",
        verify_task="wb-inbox-verify",
        lock_keys=task_resource_keys(
            api_write_marketplaces=("wb",),
            lk_marketplaces=("wb",),
            subject_keys=("wb-inbox",),
        ),
    ),
    RegisteredTask(
        name="wb-inbox-verify",
        command="verify-wb-inbox",
        title="Verify WB inbox",
        description=(
            "Verify the matching WB inbox package from durable per-action "
            "success receipts without repeating replies or marketplace writes."
        ),
        mode="verify",
        risk="low",
        marketplaces=("wb",),
        runbook_path="data/planning/reviews_questions_runbook.md",
    ),
    RegisteredTask(
        name="sessions",
        command="sessions",
        title="LK session manager",
        description="Manage local Ozon/WB LK session contours.",
        mode="maintenance",
        risk="low",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/session_manager_runbook.md",
        requires_lk=True,
    ),
    RegisteredTask(
        name="restore-ozon-session",
        command="restore-ozon-session",
        title="Restore Ozon LK session",
        description="Stop Ozon keeper/watchdog, run interactive login, then start them again.",
        mode="maintenance",
        risk="normal",
        marketplaces=("ozon",),
        runbook_path="data/planning/session_manager_runbook.md",
        requires_lk=True,
    ),
    RegisteredTask(
        name="install-session-systemd",
        command="install-session-systemd",
        title="Install session systemd units",
        description="Install systemd --user units for Ozon/WB session timers.",
        mode="maintenance",
        risk="normal",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/session_manager_runbook.md",
        requires_lk=True,
    ),
    RegisteredTask(
        name="ozon-elastic-plan",
        command="plan-ozon-elastic",
        title="Plan Ozon Elastic",
        description="Build dry-run plan for Ozon Elastic Boosting.",
        mode="dry_run",
        risk="normal",
        marketplaces=("ozon",),
        runbook_path="data/planning/ozon_elastic_runbook.md",
        requires_credentials=True,
        telegram_enabled=True,
        telegram_button_label="/elastic",
    ),
    RegisteredTask(
        name="ozon-actions-optimizer-plan",
        command="plan-ozon-actions-optimizer",
        title="Plan Ozon actions optimizer",
        description=(
            "Build dry-run recommendations across all available Ozon actions by product, "
            "min price, FBO stock and boost."
        ),
        mode="dry_run",
        risk="normal",
        marketplaces=("ozon",),
        runbook_path="data/planning/ozon_actions_optimizer_runbook.md",
        requires_credentials=True,
        telegram_enabled=True,
        telegram_button_label="/ozon-actions",
    ),
    RegisteredTask(
        name="ozon-actions-optimizer-apply",
        command="apply-ozon-actions-optimizer",
        title="Apply Ozon actions optimizer",
        description="Apply approved Ozon all-actions optimizer changes after fresh checks.",
        mode="apply",
        risk="high",
        marketplaces=("ozon",),
        runbook_path="data/planning/ozon_actions_optimizer_runbook.md",
        requires_credentials=True,
        requires_confirmation=True,
        approval_source_field="plan_run_id",
        parameter_schema=_apply_parameter_schema(),
        result_schema=_apply_result_schema(),
        source_plan_task="ozon-actions-optimizer-plan",
        verify_task="ozon-actions-optimizer-verify",
        lock_keys=task_resource_keys(
            api_write_marketplaces=("ozon",),
            subject_keys=("actions:ozon",),
        ),
    ),
    RegisteredTask(
        name="ozon-actions-optimizer-verify",
        command="verify-ozon-actions-optimizer",
        title="Verify Ozon actions optimizer",
        description="Read-only verify approved Ozon all-actions optimizer changes without repeating apply.",
        mode="verify",
        risk="low",
        marketplaces=("ozon",),
        runbook_path="data/planning/ozon_actions_optimizer_runbook.md",
        requires_credentials=True,
    ),
    RegisteredTask(
        name="ozon-elastic-apply",
        command="apply-ozon-elastic",
        title="Apply Ozon Elastic",
        description="Apply approved Ozon Elastic changes after fresh checks.",
        mode="apply",
        risk="high",
        marketplaces=("ozon",),
        runbook_path="data/planning/ozon_elastic_runbook.md",
        requires_credentials=True,
        requires_confirmation=True,
        approval_source_field="plan_run_id",
        parameter_schema=_apply_parameter_schema(),
        result_schema=_apply_result_schema(),
        source_plan_task="ozon-elastic-plan",
        verify_task="ozon-elastic-verify",
        lock_keys=task_resource_keys(
            api_write_marketplaces=("ozon",),
            subject_keys=("actions:ozon:elastic",),
        ),
    ),
    RegisteredTask(
        name="ozon-elastic-verify",
        command="verify-ozon-elastic",
        title="Verify Ozon Elastic",
        description="Read-only verify approved Ozon Elastic action membership and action prices without repeating apply.",
        mode="verify",
        risk="low",
        marketplaces=("ozon",),
        runbook_path="data/planning/ozon_elastic_runbook.md",
        requires_credentials=True,
    ),
    RegisteredTask(
        name="ozon-cpc-optimization-plan",
        command="plan-ozon-cpc-optimization",
        title="Plan Ozon CPC optimization",
        description="Build dry-run optimization recommendations from an Ozon CPC efficiency report.",
        mode="dry_run",
        risk="normal",
        marketplaces=("ozon",),
        runbook_path="data/planning/ozon_cpc_efficiency_runbook.md",
    ),
    RegisteredTask(
        name="ozon-cpc-bids-apply",
        command="apply-ozon-cpc-bids",
        title="Apply Ozon CPC bids",
        description="Apply approved Ozon CPC bid changes after fresh checks.",
        mode="apply",
        risk="high",
        marketplaces=("ozon",),
        runbook_path="data/planning/ozon_cpc_efficiency_runbook.md",
        requires_credentials=True,
        requires_confirmation=True,
        approval_source_field="plan_run_id",
        parameter_schema=_apply_parameter_schema(
            min_bid={"type": "number", "exclusiveMinimum": 0},
        ),
        result_schema=_apply_result_schema(),
        source_plan_task="ozon-cpc-optimization-plan",
        verify_task="ozon-cpc-bids-verify",
        lock_keys=task_resource_keys(
            api_write_marketplaces=("ozon",),
            subject_keys=("ads:ozon:cpc",),
        ),
    ),
    RegisteredTask(
        name="ozon-cpc-bids-verify",
        command="verify-ozon-cpc-bids",
        title="Verify Ozon CPC bids",
        description="Read-only verify approved Ozon CPC bid changes without repeating apply.",
        mode="verify",
        risk="low",
        marketplaces=("ozon",),
        runbook_path="data/planning/ozon_cpc_efficiency_runbook.md",
        requires_credentials=True,
    ),
    RegisteredTask(
        name="wb-actions-discount-plan",
        command="plan-wb-actions-discounts",
        title="Plan WB action discounts",
        description="Download WB active actions and build discount dry-run plan.",
        mode="dry_run",
        risk="normal",
        marketplaces=("wb",),
        runbook_path="data/planning/wb_actions_runbook.md",
        requires_credentials=True,
        telegram_enabled=True,
        telegram_button_label="/wb-actions",
        lock_keys=task_resource_keys(lk_marketplaces=("wb",)),
    ),
    RegisteredTask(
        name="wb-actions-discount-apply",
        command="apply-wb-actions-discounts",
        title="Apply WB action discounts",
        description="Apply approved WB action discount dry-run after fresh checks.",
        mode="apply",
        risk="high",
        marketplaces=("wb",),
        runbook_path="data/planning/wb_actions_runbook.md",
        requires_credentials=True,
        requires_confirmation=True,
        approval_source_field="plan_run_id",
        parameter_schema=_apply_parameter_schema(),
        result_schema=_apply_result_schema(),
        source_plan_task="wb-actions-discount-plan",
        verify_task="wb-actions-discount-verify",
        lock_keys=task_resource_keys(
            api_write_marketplaces=("wb",),
            lk_marketplaces=("wb",),
            subject_keys=("actions:wb:discounts",),
        ),
    ),
    RegisteredTask(
        name="wb-actions-discount-verify",
        command="verify-wb-actions-discounts",
        title="Verify WB action discounts",
        description="Read-only verify approved WB action discounts against current WB prices and discounts without repeating apply.",
        mode="verify",
        risk="low",
        marketplaces=("wb",),
        runbook_path="data/planning/wb_actions_runbook.md",
        requires_credentials=True,
    ),
    RegisteredTask(
        name="wb-best-price-action-plan",
        command="plan-wb-best-price-actions",
        title="Plan WB actions from minimum prices",
        description=(
            "Build a fresh WB action dry-run constrained by approved per-product minimum prices."
        ),
        mode="dry_run",
        risk="normal",
        marketplaces=("wb",),
        runbook_path="data/planning/wb_actions_runbook.md",
        requires_credentials=True,
        telegram_enabled=True,
        telegram_button_label="/wb-actions-min-price",
        timeout_seconds=900,
        lock_keys=task_resource_keys(lk_marketplaces=("wb",)),
    ),
    RegisteredTask(
        name="wb-best-price-action-apply",
        command="apply-wb-best-price-actions",
        title="Apply WB actions from minimum prices",
        description=(
            "Apply an owner-approved WB best-price action plan after full minimum and drift checks."
        ),
        mode="apply",
        risk="high",
        marketplaces=("wb",),
        runbook_path="data/planning/wb_actions_runbook.md",
        requires_credentials=True,
        requires_confirmation=True,
        approval_source_field="plan_run_id",
        parameter_schema=_apply_parameter_schema(),
        result_schema=_apply_result_schema(),
        source_plan_task="wb-best-price-action-plan",
        verify_task="wb-best-price-action-verify",
        timeout_seconds=1800,
        lock_keys=task_resource_keys(
            api_write_marketplaces=("wb",),
            lk_marketplaces=("wb",),
            subject_keys=("actions:wb:discounts",),
        ),
    ),
    RegisteredTask(
        name="wb-best-price-action-verify",
        command="verify-wb-best-price-actions",
        title="Verify WB actions from minimum prices",
        description=(
            "Read-only verification of WB target discounts, minimum prices and selected actions."
        ),
        mode="verify",
        risk="low",
        marketplaces=("wb",),
        runbook_path="data/planning/wb_actions_runbook.md",
        requires_credentials=True,
        timeout_seconds=900,
    ),
    RegisteredTask(
        name="wb-promotion-report",
        command="wb-promotion-report",
        title="WB promotion report",
        description="Build read-only WB Promotion campaigns and statistics report.",
        mode="read_only",
        risk="low",
        marketplaces=("wb",),
        runbook_path="data/planning/wb_promotion_runbook.md",
        requires_credentials=True,
        telegram_enabled=True,
        telegram_button_label="/ads",
    ),
    RegisteredTask(
        name="wb-promotion-bid-plan",
        command="plan-wb-promotion-bids",
        title="Plan WB promotion bids",
        description="Build dry-run WB Promotion bid optimization recommendations.",
        mode="dry_run",
        risk="normal",
        marketplaces=("wb",),
        runbook_path="data/planning/wb_promotion_runbook.md",
    ),
    RegisteredTask(
        name="wb-promotion-bid-parser-enriched-plan",
        command="plan-wb-promotion-bids-parser-enriched",
        title="Plan WB promotion bids with parser signals",
        description=(
            "Build dry-run WB Promotion bid recommendations enriched with "
            "WB parser visibility, stock and sales signals."
        ),
        mode="dry_run",
        risk="normal",
        marketplaces=("wb",),
        runbook_path="data/planning/wb_promotion_runbook.md",
        requires_mapping=True,
    ),
    RegisteredTask(
        name="wb-promotion-bids-parser-enriched-apply",
        command="apply-wb-promotion-bids-parser-enriched",
        title="Apply WB promotion bids with parser signals",
        description=(
            "Apply owner-approved parser-enriched WB Promotion bid changes after "
            "fresh WB report, fresh sales/stock/parser signals, partial drift skip and verify."
        ),
        mode="apply",
        risk="high",
        marketplaces=("wb",),
        runbook_path="data/planning/wb_promotion_runbook.md",
        requires_credentials=True,
        requires_confirmation=True,
        requires_mapping=True,
        approval_source_field="plan_run_id",
        parameter_schema=_apply_parameter_schema(
            wait_seconds={"type": "integer", "minimum": 0},
            approved_actions={"type": "array", "items": {"type": "string"}},
        ),
        result_schema=_apply_result_schema(),
        source_plan_task="wb-promotion-bid-parser-enriched-plan",
        verify_task="wb-promotion-bids-parser-enriched-verify",
        lock_keys=task_resource_keys(
            api_write_marketplaces=("wb",),
            subject_keys=("ads:wb:promotion-bids",),
        ),
    ),
    RegisteredTask(
        name="wb-promotion-bids-parser-enriched-verify",
        command="verify-wb-promotion-bids-parser-enriched",
        title="Verify WB promotion bids with parser signals",
        description="Read-only verify owner-approved parser-enriched WB Promotion bid changes without repeating apply.",
        mode="verify",
        risk="low",
        marketplaces=("wb",),
        runbook_path="data/planning/wb_promotion_runbook.md",
        requires_credentials=True,
        requires_mapping=True,
    ),
    RegisteredTask(
        name="wb-promotion-bids-apply",
        command="apply-wb-promotion-bids",
        title="Apply WB promotion bids",
        description="Apply approved WB Promotion bid changes after fresh checks.",
        mode="apply",
        risk="high",
        marketplaces=("wb",),
        runbook_path="data/planning/wb_promotion_runbook.md",
        requires_credentials=True,
        requires_confirmation=True,
        approval_source_field="plan_run_id",
        parameter_schema=_apply_parameter_schema(
            wait_seconds={"type": "integer", "minimum": 0},
            allowed_actions={"type": "array", "items": {"type": "string"}},
        ),
        result_schema=_apply_result_schema(),
        source_plan_task="wb-promotion-bid-plan",
        verify_task="wb-promotion-bids-verify",
        lock_keys=task_resource_keys(
            api_write_marketplaces=("wb",),
            subject_keys=("ads:wb:promotion-bids",),
        ),
    ),
    RegisteredTask(
        name="wb-promotion-bids-verify",
        command="verify-wb-promotion-bids",
        title="Verify WB promotion bids",
        description="Read-only verify approved WB Promotion bid changes without repeating apply.",
        mode="verify",
        risk="low",
        marketplaces=("wb",),
        runbook_path="data/planning/wb_promotion_runbook.md",
        requires_credentials=True,
    ),
    RegisteredTask(
        name="actions-apply",
        command="apply-actions",
        title="Legacy combined actions apply",
        description="Disabled legacy combined apply; use marketplace-specific plan/apply/verify routes.",
        mode="apply",
        risk="high",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/run_manifest_runbook.md",
        requires_credentials=True,
        requires_confirmation=True,
        enabled=False,
        disabled_reason=(
            "Legacy combined apply route is orphaned; use a marketplace-specific "
            "runtime approval plan/apply/verify chain."
        ),
    ),
    RegisteredTask(
        name="wb-card-create-plan",
        command="plan-wb-card-create",
        title="Plan WB card create",
        description="Build dry-run plan for creating Ozon-only product cards in WB.",
        mode="dry_run",
        risk="high",
        marketplaces=("wb",),
        runbook_path="data/planning/catalog_mapping_runbook.md",
        requires_credentials=True,
        requires_mapping=True,
    ),
    RegisteredTask(
        name="wb-card-create-apply",
        command="apply-wb-card-create",
        title="Apply WB card create",
        description="Create planned Ozon-only product cards in WB and verify the result.",
        mode="apply",
        risk="high",
        marketplaces=("wb",),
        runbook_path="data/planning/catalog_mapping_runbook.md",
        requires_credentials=True,
        requires_mapping=True,
        requires_confirmation=True,
        source_plan_task="wb-card-create-plan",
        verify_task="wb-card-create-verify",
        enabled=False,
        disabled_reason=(
            "Runtime plan/apply handlers are not wired as one tested approval "
            "chain; keep card creation disabled pending a P1 design."
        ),
    ),
    RegisteredTask(
        name="wb-card-create-verify",
        command="verify-wb-card-create",
        title="Verify WB card create",
        description="Read-only verify WB card existence, nmID, barcode, media and error state without repeating upload.",
        mode="verify",
        risk="low",
        marketplaces=("wb",),
        runbook_path="data/planning/wb_card_create_runbook.md",
        requires_credentials=True,
        requires_mapping=True,
    ),
    RegisteredTask(
        name="ozon-card-create-plan",
        command="plan-ozon-card-create",
        title="Plan Ozon card create",
        description="Build dry-run plan for creating WB-only product cards in Ozon from owner-approved Layer 3 passports.",
        mode="dry_run",
        risk="high",
        marketplaces=("ozon",),
        runbook_path="data/planning/card_ops/04_ozon_card_create_later.md",
        requires_credentials=True,
        requires_mapping=True,
    ),
    RegisteredTask(
        name="ozon-card-create-apply",
        command="apply-ozon-card-create",
        title="Apply Ozon card create",
        description="Create planned WB-only product cards in Ozon through /v3/product/import and verify the result.",
        mode="apply",
        risk="high",
        marketplaces=("ozon",),
        runbook_path="data/planning/card_ops/04_ozon_card_create_later.md",
        requires_credentials=True,
        requires_mapping=True,
        requires_confirmation=True,
        source_plan_task="ozon-card-create-plan",
        verify_task="ozon-card-create-verify",
        enabled=False,
        disabled_reason=(
            "Runtime plan/apply handlers are not wired as one tested approval "
            "chain; keep card creation disabled pending a P1 design."
        ),
    ),
    RegisteredTask(
        name="ozon-card-create-verify",
        command="verify-ozon-card-create",
        title="Verify Ozon card create",
        description="Read-only verify Ozon card, product status and target prices without repeating product import.",
        mode="verify",
        risk="low",
        marketplaces=("ozon",),
        runbook_path="data/planning/card_ops/04_ozon_card_create_later.md",
        requires_credentials=True,
        requires_mapping=True,
    ),
    RegisteredTask(
        name="ozon-product-remove-plan",
        command="plan-ozon-product-remove",
        title="Plan Ozon product remove",
        description=(
            "Build dry-run plan to delete an uncreated/no-SKU Ozon product or "
            "archive an existing Ozon product through documented Ozon API routes."
        ),
        mode="dry_run",
        risk="high",
        marketplaces=("ozon",),
        runbook_path="data/planning/card_ops/05_ozon_product_remove.md",
        requires_credentials=True,
    ),
    RegisteredTask(
        name="ozon-product-remove-apply",
        command="apply-ozon-product-remove",
        title="Apply Ozon product remove",
        description="Apply an owner-approved Ozon product delete/archive dry-run and verify the result.",
        mode="apply",
        risk="high",
        marketplaces=("ozon",),
        runbook_path="data/planning/card_ops/05_ozon_product_remove.md",
        requires_credentials=True,
        requires_confirmation=True,
        source_plan_task="ozon-product-remove-plan",
        verify_task="ozon-product-remove-verify",
        enabled=False,
        disabled_reason=(
            "Runtime plan/apply handlers are not wired as one tested approval "
            "chain; keep product removal disabled pending a P1 design."
        ),
    ),
    RegisteredTask(
        name="ozon-product-remove-verify",
        command="verify-ozon-product-remove",
        title="Verify Ozon product remove",
        description="Read-only verify that an Ozon product is archived or absent after an approved remove operation.",
        mode="verify",
        risk="low",
        marketplaces=("ozon",),
        runbook_path="data/planning/card_ops/05_ozon_product_remove.md",
        requires_credentials=True,
    ),
    RegisteredTask(
        name="ozon-partial-approved-diagnose",
        command="ozon-partial-approved-diagnose",
        title="Diagnose Ozon PARTIAL_APPROVED",
        description=(
            "Read Ozon PARTIAL_APPROVED cards, classify active item errors versus "
            "stale visibility, and build an exact recovery dry-run."
        ),
        mode="dry_run",
        risk="normal",
        marketplaces=("ozon",),
        runbook_path="data/planning/card_ops/03_card_content_update_ozon_wb.md",
        requires_credentials=True,
    ),
    RegisteredTask(
        name="ozon-partial-approved-recovery-apply",
        command="apply-ozon-partial-approved-recovery",
        title="Apply Ozon PARTIAL_APPROVED recovery",
        description=(
            "Apply an owner-approved Ozon PARTIAL_APPROVED recovery package, "
            "then verify visibility and card attributes."
        ),
        mode="apply",
        risk="high",
        marketplaces=("ozon",),
        runbook_path="data/planning/card_ops/03_card_content_update_ozon_wb.md",
        requires_credentials=True,
        requires_confirmation=True,
        source_plan_task="ozon-partial-approved-diagnose",
        verify_task="ozon-partial-approved-diagnose",
        enabled=False,
        disabled_reason=(
            "The existing diagnose task is not a strict verify handler and no "
            "complete runtime recovery chain exists; keep disabled pending P1."
        ),
    ),
    RegisteredTask(
        name="seller-sku-update-plan",
        command="plan-seller-sku-update",
        title="Plan seller SKU update",
        description=(
            "Build dry-run plan for replacing Ozon offer_id and WB vendorCode "
            "with owner-approved internal SKU."
        ),
        mode="dry_run",
        risk="high",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/card_ops/02_seller_sku_update_ozon_wb.md",
        requires_credentials=True,
        requires_mapping=True,
    ),
    RegisteredTask(
        name="seller-sku-update-apply",
        command="apply-seller-sku-update",
        title="Apply seller SKU update",
        description=(
            "Apply approved Ozon offer_id and WB vendorCode replacement plan, "
            "verify marketplace state and update local catalog CSV/JSON layers."
        ),
        mode="apply",
        risk="high",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/card_ops/02_seller_sku_update_ozon_wb.md",
        requires_credentials=True,
        requires_mapping=True,
        requires_confirmation=True,
        source_plan_task="seller-sku-update-plan",
        verify_task="seller-sku-update-verify",
        enabled=False,
        disabled_reason=(
            "Runtime plan/apply handlers are not wired as one tested approval "
            "chain; keep seller SKU mutation disabled pending a P1 design."
        ),
    ),
    RegisteredTask(
        name="seller-sku-update-verify",
        command="verify-seller-sku-update",
        title="Verify seller SKU update",
        description="Read-only verify Ozon offer_id and WB vendorCode replacements against their native product IDs.",
        mode="verify",
        risk="low",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/card_ops/02_seller_sku_update_ozon_wb.md",
        requires_credentials=True,
        requires_mapping=True,
    ),
    RegisteredTask(
        name="card-content-update-plan",
        command="plan-card-content-update",
        title="Plan card content update",
        description=(
            "Build dry-run plan for applying owner-approved Layer 3 passport "
            "fields to existing Ozon/WB cards."
        ),
        mode="dry_run",
        risk="high",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/card_ops/03_card_content_update_ozon_wb.md",
        requires_credentials=True,
        requires_mapping=True,
    ),
    RegisteredTask(
        name="card-audit-prevalidate",
        command="prevalidate-card-audit",
        title="Prevalidate card audit",
        description="Block structurally incomplete Layer 2 audits before owner review and passport promotion.",
        mode="dry_run",
        risk="low",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/product_card_audit_orchestration_runbook.md",
        requires_credentials=False,
        requires_mapping=False,
    ),
    RegisteredTask(
        name="approved-card-passport-promote",
        command="promote-approved-card-passport",
        title="Promote approved card passport",
        description=(
            "Promote an owner-approved Layer 2 card audit/HTML package into "
            "a Layer 3 approved master passport before marketplace apply."
        ),
        mode="dry_run",
        risk="normal",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/card_ops/quick_access.md",
        requires_credentials=False,
        requires_mapping=True,
    ),
    RegisteredTask(
        name="card-content-update-apply",
        command="apply-card-content-update",
        title="Apply card content update",
        description=(
            "Apply approved Ozon/WB title, description, dimensions and "
            "attribute changes from a card content update plan."
        ),
        mode="apply",
        risk="high",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/card_ops/03_card_content_update_ozon_wb.md",
        requires_credentials=True,
        requires_mapping=True,
        requires_confirmation=True,
        source_plan_task="card-content-update-plan",
        verify_task="card-content-update-verify",
        enabled=False,
        disabled_reason=(
            "Runtime plan/apply handlers are not wired as one tested approval "
            "chain; keep card content mutation disabled pending a P1 design."
        ),
    ),
    RegisteredTask(
        name="card-content-update-verify",
        command="verify-card-content-update",
        title="Verify card content update",
        description=(
            "Read-only verify actual Ozon/WB card state against owner-approved "
            "Layer 3 passports without preparing a new apply plan."
        ),
        mode="verify",
        risk="low",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/card_ops/03_card_content_update_ozon_wb.md",
        requires_credentials=True,
        requires_mapping=True,
    ),
    RegisteredTask(
        name="approved-card-apply",
        command="apply-approved-card",
        title="Apply approved card",
        description=(
            "Fast owner-approved existing-card path: build targeted plan, apply "
            "Ozon/WB changes and run targeted verify without full catalog refresh."
        ),
        mode="apply",
        risk="high",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/card_ops/03_card_content_update_ozon_wb.md",
        requires_credentials=True,
        requires_mapping=True,
        requires_confirmation=True,
        source_plan_task="owner-approved-card-html-layer3-passport",
        verify_task="card-content-update-verify",
        enabled=False,
        disabled_reason=(
            "The source link is not a registered executable plan task and no "
            "complete runtime handler exists; keep the fast path disabled pending P1."
        ),
    ),
    RegisteredTask(
        name="approved-cards-batch-plan",
        command="plan-approved-cards",
        title="Plan approved cards batch",
        description=(
            "Build a dry-run owner-approved batch apply plan with stage run ids, "
            "passport checksums and marketplace readiness checks before write operations."
        ),
        mode="dry_run",
        risk="high",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/card_ops/quick_access.md",
        requires_credentials=True,
        requires_mapping=True,
        verify_task="card-content-update-verify",
        lock_keys=task_resource_keys(subject_keys=("cards:batch-plan",)),
    ),
    RegisteredTask(
        name="approved-cards-batch-apply",
        command="apply-approved-cards",
        title="Apply approved cards batch",
        description=(
            "Batch owner-approved card path: apply content updates, seller SKU "
            "replacement, WB card creation and Ozon card creation for several "
            "cards in one run."
        ),
        mode="apply",
        risk="high",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/card_ops/quick_access.md",
        requires_credentials=True,
        requires_mapping=True,
        requires_confirmation=True,
        approval_source_field="plan_run_id",
        parameter_schema=_apply_parameter_schema(
            internal_skus={"type": "array", "items": {"type": "string"}},
        ),
        result_schema=_apply_result_schema(),
        source_plan_task="approved-cards-batch-plan",
        verify_task="card-content-update-verify",
        lock_keys=task_resource_keys(
            api_write_marketplaces=("ozon", "wb"),
            subject_keys=("cards:batch-apply",),
        ),
    ),
    RegisteredTask(
        name="reviews-questions",
        command="reviews-questions",
        title="Reviews and questions",
        description="Build read-only reviews/questions snapshot and owner-review reply drafts.",
        mode="dry_run",
        risk="low",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/reviews_questions_runbook.md",
        requires_credentials=True,
        requires_lk=True,
        telegram_enabled=True,
        telegram_button_label="/reviews",
        lock_keys=task_resource_keys(lk_marketplaces=("ozon", "wb")),
    ),
    RegisteredTask(
        name="reviews-questions-apply",
        command="apply-reviews-questions",
        title="Apply reviews and questions",
        description="Apply approved reviews/questions replies and verify the result.",
        mode="apply",
        risk="low",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/reviews_questions_runbook.md",
        requires_credentials=True,
        requires_lk=True,
        requires_confirmation=True,
        source_plan_task="reviews-questions",
        verify_task="reviews-questions-verify",
        enabled=False,
        disabled_reason=(
            "The current runtime plan produces a pending review package while the "
            "apply handler requires a separately prepared approved_path; keep this "
            "two-stage legacy bridge disabled pending a complete runtime approval design."
        ),
    ),
    RegisteredTask(
        name="reviews-questions-verify",
        command="verify-reviews-questions",
        title="Verify reviews and questions",
        description="Read-only verify approved reviews/questions actions are absent from fresh pending queues without repeating apply.",
        mode="verify",
        risk="low",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/reviews_questions_runbook.md",
        requires_credentials=True,
        requires_lk=True,
        lock_keys=task_resource_keys(lk_marketplaces=("ozon", "wb")),
    ),
    RegisteredTask(
        name="reviews-questions-prepare-approved",
        command="prepare-reviews-questions-approved",
        title="Prepare reviews/questions approved package",
        description="Create approved reviews/questions package from a pending dry-run package.",
        mode="maintenance",
        risk="low",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/reviews_questions_runbook.md",
    ),
)
