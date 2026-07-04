from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from seller_agent.core.run_manifest import ManifestMode, ManifestRisk


TaskHandler = Callable[..., dict[str, Any]]
TaskExecutor = str


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
    supports_cancel: bool = False
    enabled: bool = True

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
        if self.mode == "apply":
            if not self.requires_confirmation:
                issues.append("apply_requires_confirmation")
            if not self.source_plan_task:
                issues.append("apply_missing_source_plan_task")
            if not self.verify_task:
                issues.append("apply_missing_verify_task")
            if not self.lock_keys:
                issues.append("apply_missing_lock_keys")
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

    def policy_issues(self) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        for task in self.list():
            for issue in task.policy_issues():
                issues.append(
                    {
                        "task": task.name,
                        "command": task.command,
                        "mode": task.mode,
                        "risk": task.risk,
                        "issue": issue,
                    }
                )
        return issues


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
    ),
    RegisteredTask(
        name="actions-apply",
        command="apply-actions",
        title="Legacy combined actions apply",
        description="Legacy combined apply for confirmed Ozon Elastic and WB actions.",
        mode="apply",
        risk="high",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/run_manifest_runbook.md",
        requires_credentials=True,
        requires_confirmation=True,
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
        verify_task="wb-card-create-apply",
        lock_keys=("marketplace:wb", "cards:wb:create"),
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
        lock_keys=("marketplace:ozon", "cards:ozon:create"),
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
        verify_task="ozon-product-remove-apply",
        lock_keys=("marketplace:ozon", "cards:ozon:remove"),
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
        lock_keys=("marketplace:ozon", "cards:ozon:partial-approved"),
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
        verify_task="seller-sku-update-apply",
        lock_keys=("marketplace:ozon", "marketplace:wb", "cards:seller-sku"),
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
        lock_keys=("marketplace:ozon", "marketplace:wb", "cards:content"),
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
        lock_keys=("marketplace:ozon", "marketplace:wb", "cards:approved-card"),
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
        source_plan_task="owner-approved-card-html-layer3-passport",
        verify_task="card-content-update-verify",
        lock_keys=("marketplace:ozon", "marketplace:wb", "cards:batch-apply"),
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
