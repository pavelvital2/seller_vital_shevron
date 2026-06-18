from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from seller_agent.core.run_manifest import ManifestMode, ManifestRisk


TaskHandler = Callable[..., dict[str, Any]]


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
        data["is_read_only"] = self.is_read_only
        data["is_write"] = self.is_write
        return data


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
        name="catalog-fetch",
        command="fetch-catalog",
        title="Catalog fetch",
        description="Fetch Ozon/WB catalogs and build read-only master catalog report.",
        mode="read_only",
        risk="low",
        marketplaces=("ozon", "wb"),
        runbook_path="data/planning/catalog_mapping_runbook.md",
        requires_credentials=True,
        telegram_enabled=True,
        telegram_button_label="/catalog",
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
