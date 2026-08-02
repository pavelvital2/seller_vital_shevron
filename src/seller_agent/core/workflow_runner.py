from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
import fcntl
from pathlib import Path
from typing import Any, Callable

from seller_agent.config import AppCredentials, load_credentials
from seller_agent.safety.plan_approval import with_plan_approval_candidate
from seller_agent.tasks.approved_cards_apply import run_apply_approved_cards, run_plan_approved_cards
from seller_agent.tasks.card_content_update import run_card_content_update_verify
from seller_agent.tasks.card_audit_prevalidator import run_card_audit_prevalidate
from seller_agent.tasks.daily_morning_report import run_daily_morning_report
from seller_agent.tasks.inbox_workflow import (
    run_ozon_inbox_apply,
    run_ozon_inbox_triage,
    run_ozon_inbox_verify,
    run_wb_inbox_apply,
    run_wb_inbox_triage,
    run_wb_inbox_verify,
)
from seller_agent.tasks.liquidation_daily_control import run_liquidation_daily_control
from seller_agent.tasks.marketplace_period_report import run_marketplace_period_report
from seller_agent.tasks.ozon_actions_optimizer_apply import (
    run_ozon_actions_optimizer_apply,
    run_ozon_actions_optimizer_verify,
)
from seller_agent.tasks.ozon_actions_optimizer_plan import run_ozon_actions_optimizer_plan
from seller_agent.tasks.ozon_cpc_bids_apply import run_ozon_cpc_bids_apply, run_ozon_cpc_bids_verify
from seller_agent.tasks.ozon_cpc_optimization_plan import run_ozon_cpc_optimization_plan
from seller_agent.tasks.ozon_card_create_apply import run_ozon_card_create_verify
from seller_agent.tasks.ozon_elastic_apply import run_ozon_elastic_apply, run_ozon_elastic_verify
from seller_agent.tasks.ozon_elastic_plan import run_ozon_elastic_plan
from seller_agent.tasks.ozon_product_remove import run_ozon_product_remove_verify
from seller_agent.tasks.ozon_pricing_margin import run_ozon_pricing_margin
from seller_agent.tasks.ozon_production_work_plan import run_ozon_production_work_plan
from seller_agent.tasks.ozon_stock_supply_monitor import run_ozon_stock_supply_monitor
from seller_agent.tasks.ozon_stars_control import run_ozon_stars_control
from seller_agent.tasks.ozon_lk_state_monitor import run_ozon_lk_state_monitor
from seller_agent.tasks.ozon_min_price_timer_plan import run_ozon_min_price_timer_plan
from seller_agent.tasks.pricing_status import run_pricing_status
from seller_agent.tasks.registry import RegisteredTask, TaskRegistry, default_task_registry
from seller_agent.tasks.reviews_questions import (
    run_reviews_questions,
    run_reviews_questions_verify,
)
from seller_agent.tasks.seller_sku_update import run_seller_sku_update_verify
from seller_agent.tasks.status_preflight import run_status_preflight
from seller_agent.tasks.store_analytics_overview import run_store_analytics_overview
from seller_agent.tasks.wb_actions_discount_apply import run_wb_actions_discount_apply, run_wb_actions_discount_verify
from seller_agent.tasks.wb_actions_discount_plan import run_wb_actions_discount_plan
from seller_agent.tasks.wb_liquidation_stage2_plan import run_wb_liquidation_stage2_plan
from seller_agent.tasks.wb_liquidation_stage2_apply import (
    run_wb_liquidation_stage2_apply,
    run_wb_liquidation_stage2_verify,
)
from seller_agent.tasks.wb_pricing_margin import run_wb_pricing_margin
from seller_agent.tasks.wb_incident_audit import run_wb_incident_audit
from seller_agent.tasks.wb_best_price_action import (
    run_wb_best_price_action_apply,
    run_wb_best_price_action_plan,
    run_wb_best_price_action_verify,
)
from seller_agent.tasks.wb_card_create_apply import run_wb_card_create_verify
from seller_agent.tasks.wb_parser_warehouse_analytics import run_wb_parser_warehouse_analytics
from seller_agent.tasks.wb_production_work_plan import run_wb_production_work_plan
from seller_agent.tasks.wb_promotion_bid_parser_enriched_apply import (
    run_wb_promotion_bid_parser_enriched_apply,
    run_wb_promotion_bid_parser_enriched_verify,
)
from seller_agent.tasks.wb_promotion_bid_parser_enriched_plan import (
    run_wb_promotion_bid_parser_enriched_plan,
)
from seller_agent.tasks.wb_promotion_bids_apply import run_wb_promotion_bids_apply, run_wb_promotion_bids_verify
from seller_agent.tasks.wb_promotion_bid_plan import run_wb_promotion_bid_plan
from seller_agent.tasks.wb_stock_supply_monitor import run_wb_stock_supply_monitor


DEFAULT_WORKFLOW_LOCK_DIR = Path(".sessions/workflows")
SENSITIVE_ERROR_MARKERS = ("token", "secret", "cookie", "storage", "auth", "api_key", "client_secret")


@dataclass(frozen=True)
class WorkflowRunResult:
    task: str
    command: str
    title: str
    ok: bool
    status: str
    mode: str
    risk: str
    summary: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    blocked_reason: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


WorkflowHandler = Callable[[RegisteredTask, Path, AppCredentials | None, dict[str, Any]], dict[str, Any]]


class WorkflowRunner:
    def __init__(
        self,
        *,
        registry: TaskRegistry | None = None,
        data_dir: Path = Path("data"),
        lock_dir: Path = DEFAULT_WORKFLOW_LOCK_DIR,
        credentials: AppCredentials | None = None,
        handlers: dict[str, WorkflowHandler] | None = None,
    ) -> None:
        self.registry = registry or default_task_registry()
        self.data_dir = data_dir
        self.lock_dir = lock_dir
        self.credentials = credentials
        self.handlers = handlers or default_workflow_handlers()

    def run_read_only(self, task_name: str, *, inputs: dict[str, Any] | None = None) -> WorkflowRunResult:
        return self.run_task(task_name, inputs=inputs, allowed_modes={"read_only"})

    def run_task(
        self,
        task_name: str,
        *,
        inputs: dict[str, Any] | None = None,
        allowed_modes: set[str] | None = None,
    ) -> WorkflowRunResult:
        try:
            task = self.registry.get(task_name)
        except KeyError as exc:
            return WorkflowRunResult(
                task=task_name,
                command=task_name,
                title=task_name,
                ok=False,
                status="blocked",
                mode="unknown",
                risk="unknown",
                blocked_reason="unknown_task",
                error=_safe_error(exc),
            )

        if not task.enabled:
            return WorkflowRunResult(
                task=task.name,
                command=task.command,
                title=task.title,
                ok=False,
                status="blocked",
                mode=task.mode,
                risk=task.risk,
                blocked_reason="task_disabled",
                error=f"Task `{task.name}` is disabled for WorkflowRunner.",
            )

        allowed_modes = allowed_modes or {"read_only"}
        if task.mode not in allowed_modes:
            blocked_reason = "not_read_only" if allowed_modes == {"read_only"} else "mode_not_allowed"
            return WorkflowRunResult(
                task=task.name,
                command=task.command,
                title=task.title,
                ok=False,
                status="blocked",
                mode=task.mode,
                risk=task.risk,
                blocked_reason=blocked_reason,
                error=f"Task `{task.name}` is `{task.mode}`, allowed modes: {sorted(allowed_modes)}.",
            )

        handler = self.handlers.get(task.name)
        if handler is None:
            return WorkflowRunResult(
                task=task.name,
                command=task.command,
                title=task.title,
                ok=False,
                status="blocked",
                mode=task.mode,
                risk=task.risk,
                blocked_reason="unsupported_workflow",
                error=f"Task `{task.name}` has no WorkflowRunner handler.",
            )

        try:
            with _WorkflowLock(self.lock_dir / f"{_task_slug(task.name)}.lock"):
                summary = handler(task, self.data_dir, self._credentials_for(task), inputs or {})
        except WorkflowLockBusyError as exc:
            return WorkflowRunResult(
                task=task.name,
                command=task.command,
                title=task.title,
                ok=False,
                status="blocked",
                mode=task.mode,
                risk=task.risk,
                blocked_reason="workflow_busy",
                error=_safe_error(exc),
            )
        except Exception as exc:  # noqa: BLE001 - runner must return safe failures to Telegram.
            return WorkflowRunResult(
                task=task.name,
                command=task.command,
                title=task.title,
                ok=False,
                status="error",
                mode=task.mode,
                risk=task.risk,
                blocked_reason="workflow_failed",
                error=_safe_error(exc),
            )

        status = str(summary.get("overall_status") or summary.get("status") or "warning")
        return WorkflowRunResult(
            task=task.name,
            command=task.command,
            title=task.title,
            ok=status in {"ok", "warning"},
            status=status,
            mode=task.mode,
            risk=task.risk,
            summary=summary,
            artifacts=_safe_artifacts(summary),
        )

    def _credentials_for(self, task: RegisteredTask) -> AppCredentials | None:
        if not task.requires_credentials:
            return self.credentials
        return self.credentials or load_credentials()


class WorkflowLockBusyError(RuntimeError):
    pass


class _WorkflowLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._file: Any | None = None

    def __enter__(self) -> "_WorkflowLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._file.close()
            self._file = None
            raise WorkflowLockBusyError(f"workflow lock is busy: {self.path}") from exc
        self.path.chmod(0o600)
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._file is None:
            return
        fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        self._file.close()
        self._file = None


def default_workflow_handlers() -> dict[str, WorkflowHandler]:
    return {
        "daily-morning-report": _daily_morning_report_handler,
        "liquidation-daily-control": _liquidation_daily_control_handler,
        "wb-liquidation-stage2-plan": _wb_liquidation_stage2_plan_handler,
        "wb-liquidation-stage2-apply": _wb_liquidation_stage2_apply_handler,
        "wb-liquidation-stage2-verify": _wb_liquidation_stage2_verify_handler,
        "ozon-stars-control": _ozon_stars_control_handler,
        "wb-incident-audit": _wb_incident_audit_handler,
        "ozon-lk-state-monitor": _ozon_lk_state_monitor_handler,
        "ozon-min-price-timer-plan": _ozon_min_price_timer_plan_handler,
        "marketplace-period-report": _marketplace_period_report_handler,
        "store-analytics-overview": _store_analytics_overview_handler,
        "ozon-stock-supply-monitor": _ozon_stock_supply_monitor_handler,
        "ozon-production-work-plan": _ozon_production_work_plan_handler,
        "ozon-pricing-margin": _ozon_pricing_margin_handler,
        "wb-pricing-margin": _wb_pricing_margin_handler,
        "ozon-inbox": _ozon_inbox_handler,
        "wb-inbox": _wb_inbox_handler,
        "ozon-inbox-verify": _ozon_inbox_verify_handler,
        "wb-inbox-verify": _wb_inbox_verify_handler,
        "ozon-elastic-plan": _ozon_elastic_plan_handler,
        "ozon-actions-optimizer-plan": _ozon_actions_optimizer_plan_handler,
        "wb-actions-discount-plan": _wb_actions_discount_plan_handler,
        "wb-best-price-action-plan": _wb_best_price_action_plan_handler,
        "pricing-status": _pricing_status_handler,
        "status-preflight": _status_preflight_handler,
        "wb-parser-warehouse-analytics": _wb_parser_warehouse_analytics_handler,
        "wb-stock-supply-monitor": _wb_stock_supply_monitor_handler,
        "wb-production-work-plan": _wb_production_work_plan_handler,
        "approved-cards-batch-plan": _approved_cards_batch_plan_handler,
        "card-audit-prevalidate": _card_audit_prevalidate_handler,
        "approved-cards-batch-apply": _approved_cards_batch_apply_handler,
        "card-content-update-verify": _card_content_update_verify_handler,
        "ozon-card-create-verify": _ozon_card_create_verify_handler,
        "ozon-product-remove-verify": _ozon_product_remove_verify_handler,
        "ozon-actions-optimizer-apply": _ozon_actions_optimizer_apply_handler,
        "ozon-actions-optimizer-verify": _ozon_actions_optimizer_verify_handler,
        "ozon-cpc-bids-apply": _ozon_cpc_bids_apply_handler,
        "ozon-cpc-bids-verify": _ozon_cpc_bids_verify_handler,
        "ozon-cpc-optimization-plan": _ozon_cpc_optimization_plan_handler,
        "ozon-elastic-apply": _ozon_elastic_apply_handler,
        "ozon-elastic-verify": _ozon_elastic_verify_handler,
        "ozon-inbox-apply": _ozon_inbox_apply_handler,
        "reviews-questions": _reviews_questions_handler,
        "reviews-questions-verify": _reviews_questions_verify_handler,
        "seller-sku-update-verify": _seller_sku_update_verify_handler,
        "wb-actions-discount-apply": _wb_actions_discount_apply_handler,
        "wb-actions-discount-verify": _wb_actions_discount_verify_handler,
        "wb-best-price-action-apply": _wb_best_price_action_apply_handler,
        "wb-best-price-action-verify": _wb_best_price_action_verify_handler,
        "wb-card-create-verify": _wb_card_create_verify_handler,
        "wb-inbox-apply": _wb_inbox_apply_handler,
        "wb-promotion-bids-apply": _wb_promotion_bids_apply_handler,
        "wb-promotion-bids-verify": _wb_promotion_bids_verify_handler,
        "wb-promotion-bid-plan": _wb_promotion_bid_plan_handler,
        "wb-promotion-bid-parser-enriched-plan": _wb_promotion_bid_parser_enriched_plan_handler,
        "wb-promotion-bids-parser-enriched-apply": _wb_promotion_bids_parser_enriched_apply_handler,
        "wb-promotion-bids-parser-enriched-verify": _wb_promotion_bids_parser_enriched_verify_handler,
    }


def _daily_morning_report_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    return run_daily_morning_report(
        credentials=credentials,
        data_dir=data_dir,
        run_id=_optional_str(inputs.get("run_id")),
        refresh_preflight=_bool_input(inputs, "refresh_preflight", True),
        seller_v2=_bool_input(inputs, "seller_v2", False),
        seller_v3=_bool_input(inputs, "seller_v3", True),
    )


def _liquidation_daily_control_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    return run_liquidation_daily_control(
        credentials=credentials,
        data_dir=data_dir,
        date_to=_optional_str(inputs.get("date_to")),
        ozon_cohort_path=Path(inputs.get("ozon_cohort_path") or "data/runs/2026-07-30/ozon_dormant_reset_plan_docs_fixed_20260730T1050/ozon_dormant_reset_plan.csv"),
        wb_cohort_path=Path(inputs.get("wb_cohort_path") or "data/runs/2026-07-30/wb_dormant_liquidation_fresh_preapply_20260730T1421/liquidation_plan.csv"),
        run_id=_optional_str(inputs.get("run_id")),
    )


def _wb_liquidation_stage2_plan_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    result = run_wb_liquidation_stage2_plan(
        credentials=credentials,
        data_dir=data_dir,
        cohort_path=Path(inputs.get("cohort_path") or "data/runs/2026-07-30/wb_dormant_liquidation_fresh_preapply_20260730T1421/liquidation_plan.csv"),
        run_id=_optional_str(inputs.get("run_id")),
    )
    return with_plan_approval_candidate(
        result,
        action_count=_plan_action_count(result, "ready_for_owner_review"),
        source_field="plan_run_id",
    )


def _wb_liquidation_stage2_apply_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    return run_wb_liquidation_stage2_apply(
        credentials=credentials,
        data_dir=data_dir,
        plan_run_id=_required_str(inputs, "plan_run_id", task.name),
        confirmed_by_user=_bool_input(inputs, "confirmed_by_user", False),
        run_id=_optional_str(inputs.get("run_id")),
    )


def _wb_liquidation_stage2_verify_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    return run_wb_liquidation_stage2_verify(
        credentials=credentials,
        data_dir=data_dir,
        plan_run_id=_required_str(inputs, "plan_run_id", task.name),
        run_id=_optional_str(inputs.get("run_id")),
    )


def _ozon_stars_control_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    return run_ozon_stars_control(credentials=credentials, data_dir=data_dir, window_days=_int_input(inputs, "window_days", 3), date_to=_optional_str(inputs.get("date_to")), run_id=_optional_str(inputs.get("run_id")))


def _wb_incident_audit_handler(task: RegisteredTask, data_dir: Path, credentials: AppCredentials | None, inputs: dict[str, Any]) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    return run_wb_incident_audit(credentials=credentials, data_dir=data_dir, run_id=_optional_str(inputs.get("run_id")))


def _ozon_lk_state_monitor_handler(task: RegisteredTask, data_dir: Path, credentials: AppCredentials | None, inputs: dict[str, Any]) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    return run_ozon_lk_state_monitor(credentials=credentials, data_dir=data_dir, state_path=Path(inputs.get("state_path") or "runtime/state/ozon_lk_monitor.json"), run_id=_optional_str(inputs.get("run_id")))


def _ozon_min_price_timer_plan_handler(task: RegisteredTask, data_dir: Path, credentials: AppCredentials | None, inputs: dict[str, Any]) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    return run_ozon_min_price_timer_plan(credentials=credentials, data_dir=data_dir, warning_days=_int_input(inputs, "warning_days", 5), run_id=_optional_str(inputs.get("run_id")))


def _marketplace_period_report_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    return run_marketplace_period_report(
        credentials=credentials,
        data_dir=data_dir,
        marketplace=_optional_str(inputs.get("marketplace")) or "",
        report_type=_optional_str(inputs.get("report_type")) or "",
        date_from=_optional_str(inputs.get("date_from")) or "",
        date_to=_optional_str(inputs.get("date_to")) or "",
        run_id=_optional_str(inputs.get("run_id")),
    )


def _store_analytics_overview_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    return run_store_analytics_overview(
        credentials=credentials,
        data_dir=data_dir,
        marketplace=_optional_str(inputs.get("marketplace")) or "",
        period_days=_int_input(inputs, "period_days", 0),
        run_id=_optional_str(inputs.get("run_id")),
        wb_supplier_id=_optional_str(inputs.get("wb_supplier_id")) or "4516781",
        ozon_seller_slug=_optional_str(inputs.get("ozon_seller_slug")) or "vital-shevron",
        region_id=_optional_str(inputs.get("region_id")) or "",
        query_pack_id=_optional_str(inputs.get("query_pack_id")) or "",
    )


def _ozon_stock_supply_monitor_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    return run_ozon_stock_supply_monitor(
        credentials=credentials,
        data_dir=data_dir,
        run_id=_optional_str(inputs.get("run_id")),
    )


def _ozon_production_work_plan_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    return run_ozon_production_work_plan(
        credentials=credentials,
        data_dir=data_dir,
        mode=_required_str(inputs, "mode", task.name),
        value=_int_input(inputs, "value", 0),
        cluster_count=_int_input(inputs, "cluster_count", 0),
        run_id=_optional_str(inputs.get("run_id")),
    )


def _ozon_pricing_margin_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    return run_ozon_pricing_margin(
        credentials=credentials,
        data_dir=data_dir,
        unit_cost=inputs.get("unit_cost"),
        target_margin=inputs.get("target_margin"),
        period_days=_int_input(inputs, "period_days", 0),
        run_id=_optional_str(inputs.get("run_id")),
    )


def _wb_pricing_margin_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    return run_wb_pricing_margin(
        credentials=credentials,
        data_dir=data_dir,
        unit_cost=inputs.get("unit_cost"),
        target_margin=inputs.get("target_margin"),
        period_days=_int_input(inputs, "period_days", 0),
        run_id=_optional_str(inputs.get("run_id")),
    )


def _ozon_inbox_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    result = run_ozon_inbox_triage(
        credentials=credentials,
        data_dir=data_dir,
        run_id=_optional_str(inputs.get("run_id")),
        limit=_int_input(inputs, "limit", 300),
    )
    return with_plan_approval_candidate(
        result,
        action_count=_plan_action_count(result, "apply_actions_count"),
        source_field="source_run_id",
    )


def _wb_inbox_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    result = run_wb_inbox_triage(
        credentials=credentials,
        data_dir=data_dir,
        run_id=_optional_str(inputs.get("run_id")),
        limit=_int_input(inputs, "limit", 100),
    )
    return with_plan_approval_candidate(
        result,
        action_count=_plan_action_count(result, "apply_actions_count"),
        source_field="source_run_id",
    )


def _ozon_inbox_verify_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    return run_ozon_inbox_verify(
        data_dir=data_dir,
        source_run_id=_required_str(inputs, "source_run_id", task.name),
        run_id=_optional_str(inputs.get("run_id")),
    )


def _wb_inbox_verify_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    return run_wb_inbox_verify(
        data_dir=data_dir,
        source_run_id=_required_str(inputs, "source_run_id", task.name),
        run_id=_optional_str(inputs.get("run_id")),
    )


def _ozon_elastic_plan_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    result = run_ozon_elastic_plan(
        credentials=credentials,
        data_dir=data_dir,
        run_id=_optional_str(inputs.get("run_id")),
    )
    return with_plan_approval_candidate(
        result,
        action_count=_plan_action_count(
            result,
            "add_to_action",
            "update_action_price_with_changed_price",
            "deactivate_from_action",
        ),
        source_field="plan_run_id",
    )


def _ozon_actions_optimizer_plan_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    result = run_ozon_actions_optimizer_plan(
        credentials=credentials,
        data_dir=data_dir,
        run_id=_optional_str(inputs.get("run_id")),
        lk_boost_summary_json=_optional_path(inputs.get("lk_boost_summary_json")),
    )
    return with_plan_approval_candidate(
        result,
        action_count=_plan_action_count(
            result,
            "recommended_add",
            "recommended_update",
            "recommended_switch_review",
        ),
        source_field="plan_run_id",
    )


def _wb_actions_discount_plan_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    result = run_wb_actions_discount_plan(
        credentials=credentials,
        data_dir=data_dir,
        run_id=_optional_str(inputs.get("run_id")),
        scheme_text=_optional_str(inputs.get("scheme_text")) or "70-55-55",
        actions_dir=_optional_path(inputs.get("actions_dir")),
        prices_json=_optional_path(inputs.get("prices_json")),
    )
    return with_plan_approval_candidate(
        result,
        action_count=_plan_action_count(result, "changed_rows"),
        source_field="plan_run_id",
    )


def _wb_best_price_action_plan_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    result = run_wb_best_price_action_plan(
        credentials=credentials,
        data_dir=data_dir,
        outside_discount=_int_input(inputs, "outside_discount", 50),
        run_id=_optional_str(inputs.get("run_id")),
        price_plan_path=_optional_path(inputs.get("price_plan_path")),
    )
    return with_plan_approval_candidate(
        result,
        action_count=_plan_action_count(result, "to_change_discount"),
        source_field="plan_run_id",
    )


def _status_preflight_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    return run_status_preflight(
        credentials=credentials,
        data_dir=data_dir,
        run_id=_optional_str(inputs.get("run_id")),
        include_lk=_bool_input(inputs, "include_lk", True),
    )


def _pricing_status_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    return run_pricing_status(
        credentials=credentials,
        data_dir=data_dir,
        products_path=_optional_path(inputs.get("products_path")),
        ozon_prices_path=_optional_path(inputs.get("ozon_prices_path")),
        wb_prices_path=_optional_path(inputs.get("wb_prices_path")),
        ozon_actions_path=_optional_path(inputs.get("ozon_actions_path")),
        wb_actions_path=_optional_path(inputs.get("wb_actions_path")),
        output_dir=_optional_path(inputs.get("output_dir")),
        run_id=_optional_str(inputs.get("run_id")),
        refresh_api=_bool_input(inputs, "refresh_api", False),
        refresh_marketplace=_optional_str(inputs.get("refresh_marketplace")) or "all",
    )


def _wb_parser_warehouse_analytics_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    return run_wb_parser_warehouse_analytics(
        data_dir=data_dir,
        run_id=_optional_str(inputs.get("run_id")),
        supplier_id=_optional_str(inputs.get("supplier_id")) or "4516781",
        limit=_int_input(inputs, "limit", 500),
        report_limit=_int_input(inputs, "report_limit", 50),
    )


def _wb_stock_supply_monitor_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    return run_wb_stock_supply_monitor(
        credentials=credentials,
        data_dir=data_dir,
        run_id=_optional_str(inputs.get("run_id")),
    )


def _wb_production_work_plan_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    return run_wb_production_work_plan(
        credentials=credentials,
        data_dir=data_dir,
        mode=_required_str(inputs, "mode", task.name),
        value=_int_input(inputs, "value", 0),
        cluster_count=_int_input(inputs, "cluster_count", 0),
        run_id=_optional_str(inputs.get("run_id")),
    )


def _card_audit_prevalidate_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    raw_paths = inputs.get("audit_paths") or inputs.get("audit_path") or []
    if isinstance(raw_paths, (str, Path)):
        raw_paths = [raw_paths]
    if not isinstance(raw_paths, list) or not raw_paths:
        raise ValueError(f"Task `{task.name}` requires audit_paths.")
    return run_card_audit_prevalidate(
        data_dir=data_dir,
        audit_paths=[Path(str(path)) for path in raw_paths],
        run_id=_optional_str(inputs.get("run_id")),
    )


def _approved_cards_batch_apply_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    internal_skus = inputs.get("internal_skus") or inputs.get("internal_sku") or []
    if isinstance(internal_skus, str):
        internal_skus = [item.strip() for item in internal_skus.replace(",", " ").split() if item.strip()]
    plan_run_id = _optional_str(inputs.get("plan_run_id")) or ""
    if not isinstance(internal_skus, list):
        internal_skus = []
    if not internal_skus and not plan_run_id:
        raise ValueError("approved-cards-batch-apply requires internal_skus list or plan_run_id.")
    return run_apply_approved_cards(
        credentials=credentials,
        data_dir=data_dir,
        internal_skus=[str(item) for item in internal_skus],
        plan_run_id=plan_run_id,
        run_id=_optional_str(inputs.get("run_id")),
        confirmed_by_user=_bool_input(inputs, "confirmed_by_user", False),
        content_wait_seconds=int(inputs.get("content_wait_seconds") or 180),
        content_poll_interval=int(inputs.get("content_poll_interval") or 10),
        seller_sku_wait_seconds=int(inputs.get("seller_sku_wait_seconds") or 60),
        seller_sku_poll_interval=int(inputs.get("seller_sku_poll_interval") or 5),
        wb_create_wait_seconds=int(inputs.get("wb_create_wait_seconds") or 600),
        wb_create_poll_interval=int(inputs.get("wb_create_poll_interval") or 30),
        ozon_create_min_price=_optional_str(inputs.get("ozon_create_min_price")) or "",
        ozon_create_allow_manual_review=_bool_input(inputs, "ozon_create_allow_manual_review", False),
        ozon_create_wait_seconds=int(inputs.get("ozon_create_wait_seconds") or 300),
        ozon_create_poll_interval=int(inputs.get("ozon_create_poll_interval") or 10),
        runtime_db=_optional_path(inputs.get("runtime_db")),
    )


def _approved_cards_batch_plan_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    internal_skus = inputs.get("internal_skus") or inputs.get("internal_sku") or []
    if isinstance(internal_skus, str):
        internal_skus = [item.strip() for item in internal_skus.replace(",", " ").split() if item.strip()]
    if not isinstance(internal_skus, list) or not internal_skus:
        raise ValueError("approved-cards-batch-plan requires internal_skus list.")
    result = run_plan_approved_cards(
        credentials=credentials,
        data_dir=data_dir,
        internal_skus=[str(item) for item in internal_skus],
        run_id=_optional_str(inputs.get("run_id")),
        ozon_create_min_price=_optional_str(inputs.get("ozon_create_min_price")) or "",
        ozon_create_allow_manual_review=_bool_input(inputs, "ozon_create_allow_manual_review", False),
        runtime_db=_optional_path(inputs.get("runtime_db")),
    )
    return with_plan_approval_candidate(
        result,
        action_count=_plan_action_count(
            result,
            "seller_sku_ready",
            "content_ready",
            "wb_create_ready",
            "ozon_create_ready",
        ),
        source_field="plan_run_id",
    )


def _card_content_update_verify_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    internal_skus = inputs.get("internal_skus") or inputs.get("internal_sku") or []
    if isinstance(internal_skus, str):
        internal_skus = [item.strip() for item in internal_skus.replace(",", " ").split() if item.strip()]
    passport_paths = inputs.get("passport_paths") or inputs.get("passport_path") or []
    if isinstance(passport_paths, str):
        passport_paths = [item.strip() for item in passport_paths.replace(",", " ").split() if item.strip()]
    return run_card_content_update_verify(
        credentials=credentials,
        data_dir=data_dir,
        passport_paths=[Path(item) for item in passport_paths] if isinstance(passport_paths, list) else [],
        internal_skus=[str(item) for item in internal_skus] if isinstance(internal_skus, list) else [],
        run_id=_optional_str(inputs.get("run_id")),
        skip_api=_bool_input(inputs, "skip_api", False),
    )


def _ozon_card_create_verify_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    return run_ozon_card_create_verify(
        credentials=credentials,
        data_dir=data_dir,
        plan_run_id=_required_str(inputs, "plan_run_id", task.name),
        run_id=_optional_str(inputs.get("run_id")),
        wait_seconds=_int_input(inputs, "wait_seconds", 0),
        poll_interval=_int_input(inputs, "poll_interval", 5),
    )


def _ozon_product_remove_verify_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    return run_ozon_product_remove_verify(
        credentials=credentials,
        data_dir=data_dir,
        plan_run_id=_required_str(inputs, "plan_run_id", task.name),
        run_id=_optional_str(inputs.get("run_id")),
    )


def _seller_sku_update_verify_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    return run_seller_sku_update_verify(
        credentials=credentials,
        data_dir=data_dir,
        plan_run_id=_required_str(inputs, "plan_run_id", task.name),
        run_id=_optional_str(inputs.get("run_id")),
        wait_seconds=_int_input(inputs, "wait_seconds", 0),
        poll_interval=_int_input(inputs, "poll_interval", 5),
    )


def _wb_card_create_verify_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    return run_wb_card_create_verify(
        credentials=credentials,
        data_dir=data_dir,
        plan_run_id=_required_str(inputs, "plan_run_id", task.name),
        run_id=_optional_str(inputs.get("run_id")),
        wait_seconds=_int_input(inputs, "wait_seconds", 0),
        poll_interval=_int_input(inputs, "poll_interval", 5),
    )


def _ozon_actions_optimizer_apply_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    return run_ozon_actions_optimizer_apply(
        credentials=credentials,
        data_dir=data_dir,
        plan_run_id=_required_str(inputs, "plan_run_id", task.name),
        run_id=_optional_str(inputs.get("run_id")),
        confirmed_by_user=_bool_input(inputs, "confirmed_by_user", False),
    )


def _ozon_actions_optimizer_verify_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    return run_ozon_actions_optimizer_verify(
        credentials=credentials,
        data_dir=data_dir,
        plan_run_id=_required_str(inputs, "plan_run_id", task.name),
        run_id=_optional_str(inputs.get("run_id")),
    )


def _ozon_elastic_apply_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    return run_ozon_elastic_apply(
        credentials=credentials,
        data_dir=data_dir,
        plan_run_id=_required_str(inputs, "plan_run_id", task.name),
        run_id=_optional_str(inputs.get("run_id")),
        confirmed_by_user=_bool_input(inputs, "confirmed_by_user", False),
    )


def _ozon_elastic_verify_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    return run_ozon_elastic_verify(
        credentials=credentials,
        data_dir=data_dir,
        plan_run_id=_required_str(inputs, "plan_run_id", task.name),
        run_id=_optional_str(inputs.get("run_id")),
    )


def _ozon_cpc_optimization_plan_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    result = run_ozon_cpc_optimization_plan(
        data_dir=data_dir,
        run_id=_optional_str(inputs.get("run_id")),
        source_run_id=_optional_str(inputs.get("source_run_id")),
        rows_csv=_optional_path(inputs.get("rows_csv")),
        current_bids_json=_optional_path(inputs.get("current_bids_json")),
    )
    return with_plan_approval_candidate(
        result,
        action_count=_plan_action_count(result, "apply_payload_rows"),
        source_field="plan_run_id",
    )


def _ozon_cpc_bids_apply_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    return run_ozon_cpc_bids_apply(
        credentials=credentials,
        data_dir=data_dir,
        plan_run_id=_required_str(inputs, "plan_run_id", task.name),
        run_id=_optional_str(inputs.get("run_id")),
        confirmed_by_user=_bool_input(inputs, "confirmed_by_user", False),
        min_bid=Decimal(_optional_str(inputs.get("min_bid")) or "1.00"),
    )


def _ozon_cpc_bids_verify_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    return run_ozon_cpc_bids_verify(
        credentials=credentials,
        data_dir=data_dir,
        plan_run_id=_required_str(inputs, "plan_run_id", task.name),
        run_id=_optional_str(inputs.get("run_id")),
        min_bid=Decimal(_optional_str(inputs.get("min_bid")) or "1.00"),
    )


def _ozon_inbox_apply_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    source_run_id = _optional_str(inputs.get("source_run_id")) or ""
    if not source_run_id:
        raise ValueError("ozon-inbox-apply requires source_run_id.")
    return run_ozon_inbox_apply(
        credentials=credentials,
        data_dir=data_dir,
        source_run_id=source_run_id,
        confirmed_by_user=_bool_input(inputs, "confirmed_by_user", False),
    )


def _reviews_questions_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    return run_reviews_questions(
        credentials=credentials,
        data_dir=data_dir,
        run_id=_optional_str(inputs.get("run_id")),
        marketplace=_optional_str(inputs.get("marketplace")) or "all",
        limit=_int_input(inputs, "limit", 100),
    )


def _reviews_questions_verify_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    return run_reviews_questions_verify(
        credentials=credentials,
        data_dir=data_dir,
        approved_path=Path(_required_str(inputs, "approved_path", task.name)),
        run_id=_optional_str(inputs.get("run_id")),
        limit=_int_input(inputs, "limit", 300),
    )


def _wb_actions_discount_apply_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    return run_wb_actions_discount_apply(
        credentials=credentials,
        data_dir=data_dir,
        plan_run_id=_required_str(inputs, "plan_run_id", task.name),
        run_id=_optional_str(inputs.get("run_id")),
        confirmed_by_user=_bool_input(inputs, "confirmed_by_user", False),
    )


def _wb_actions_discount_verify_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    return run_wb_actions_discount_verify(
        credentials=credentials,
        data_dir=data_dir,
        plan_run_id=_required_str(inputs, "plan_run_id", task.name),
        run_id=_optional_str(inputs.get("run_id")),
    )


def _wb_best_price_action_apply_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    return run_wb_best_price_action_apply(
        credentials=credentials,
        data_dir=data_dir,
        plan_run_id=_required_str(inputs, "plan_run_id", task.name),
        run_id=_optional_str(inputs.get("run_id")),
        confirmed_by_user=_bool_input(inputs, "confirmed_by_user", False),
    )


def _wb_best_price_action_verify_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    return run_wb_best_price_action_verify(
        credentials=credentials,
        data_dir=data_dir,
        plan_run_id=_required_str(inputs, "plan_run_id", task.name),
        run_id=_optional_str(inputs.get("run_id")),
    )


def _wb_inbox_apply_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    source_run_id = _optional_str(inputs.get("source_run_id")) or ""
    if not source_run_id:
        raise ValueError("wb-inbox-apply requires source_run_id.")
    return run_wb_inbox_apply(
        credentials=credentials,
        data_dir=data_dir,
        source_run_id=source_run_id,
        confirmed_by_user=_bool_input(inputs, "confirmed_by_user", False),
    )


def _wb_promotion_bid_plan_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    result = run_wb_promotion_bid_plan(
        data_dir=data_dir,
        run_id=_optional_str(inputs.get("run_id")),
        source_run_id=_optional_str(inputs.get("source_run_id")),
        products_csv=_optional_path(inputs.get("products_csv")),
        campaigns_json=_optional_path(inputs.get("campaigns_json")),
        active_cpc_only=_bool_input(inputs, "active_cpc_only", True),
    )
    return with_plan_approval_candidate(
        result,
        action_count=_plan_action_count(result, "apply_payload_rows"),
        source_field="plan_run_id",
    )


def _wb_promotion_bid_parser_enriched_plan_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    result = run_wb_promotion_bid_parser_enriched_plan(
        data_dir=data_dir,
        run_id=_optional_str(inputs.get("run_id")),
        base_plan_run_id=_optional_str(inputs.get("base_plan_run_id")),
        base_plan_csv=_optional_path(inputs.get("base_plan_csv")),
        signals_csv=_path_list_input(inputs, "signals_csv"),
        min_stock=_int_input(inputs, "min_stock", 4),
        parser_test_increase_percent=Decimal(
            _optional_str(inputs.get("parser_test_increase_percent")) or "10"
        ),
        min_bid=Decimal(_optional_str(inputs.get("min_bid")) or "1.00"),
    )
    return with_plan_approval_candidate(
        result,
        action_count=_plan_action_count(result, "apply_payload_rows"),
        source_field="plan_run_id",
    )


def _wb_promotion_bids_apply_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    return run_wb_promotion_bids_apply(
        credentials=credentials,
        data_dir=data_dir,
        plan_run_id=_required_str(inputs, "plan_run_id", task.name),
        run_id=_optional_str(inputs.get("run_id")),
        confirmed_by_user=_bool_input(inputs, "confirmed_by_user", False),
        allowed_actions=_csv_set_input(inputs, "allowed_actions", {"scale_candidate"}),
        wait_seconds=_int_input(inputs, "wait_seconds", 45),
    )


def _wb_promotion_bids_verify_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    return run_wb_promotion_bids_verify(
        credentials=credentials,
        data_dir=data_dir,
        plan_run_id=_required_str(inputs, "plan_run_id", task.name),
        run_id=_optional_str(inputs.get("run_id")),
        allowed_actions=_csv_set_input(inputs, "allowed_actions", {"scale_candidate"}),
    )


def _wb_promotion_bids_parser_enriched_apply_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    return run_wb_promotion_bid_parser_enriched_apply(
        credentials=credentials,
        data_dir=data_dir,
        plan_run_id=_required_str(inputs, "plan_run_id", task.name),
        run_id=_optional_str(inputs.get("run_id")),
        confirmed_by_user=_bool_input(inputs, "confirmed_by_user", False),
        wait_seconds=_int_input(inputs, "wait_seconds", 45),
        approved_actions=_csv_set_input(inputs, "approved_actions", {"apply_ready"}),
    )


def _wb_promotion_bids_parser_enriched_verify_handler(
    task: RegisteredTask,
    data_dir: Path,
    credentials: AppCredentials | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if credentials is None:
        raise ValueError(f"Task `{task.name}` requires credentials.")
    return run_wb_promotion_bid_parser_enriched_verify(
        credentials=credentials,
        data_dir=data_dir,
        plan_run_id=_required_str(inputs, "plan_run_id", task.name),
        run_id=_optional_str(inputs.get("run_id")),
        approved_actions=_csv_set_input(inputs, "approved_actions", {"apply_ready"}),
    )


def _plan_action_count(result: dict[str, Any], *field_names: str) -> int:
    summary = result.get("summary")
    summary_data = summary if isinstance(summary, dict) else {}
    total = 0
    for field_name in field_names:
        value = summary_data.get(field_name, result.get(field_name, 0))
        if isinstance(value, bool):
            raise ValueError(f"planner action count `{field_name}` must be an integer")
        try:
            count = int(value or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"planner action count `{field_name}` must be an integer"
            ) from exc
        if count < 0:
            raise ValueError(f"planner action count `{field_name}` cannot be negative")
        total += count
    return total


def _safe_artifacts(summary: dict[str, Any]) -> dict[str, str]:
    artifacts = summary.get("artifacts")
    if not isinstance(artifacts, dict):
        return {}
    safe: dict[str, str] = {}
    for key, value in artifacts.items():
        key_text = str(key)
        if any(marker in key_text.lower() for marker in SENSITIVE_ERROR_MARKERS):
            continue
        safe[key_text] = str(value)
    return safe


def _safe_error(exc: BaseException) -> str:
    raw = str(exc).replace("\n", " ").replace("\r", " ")
    for marker in SENSITIVE_ERROR_MARKERS:
        raw = raw.replace(marker, "<redacted>")
    return raw[:500]


def _task_slug(task_name: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in task_name)


def _optional_str(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _optional_path(value: Any) -> Path | None:
    text = _optional_str(value)
    return Path(text) if text else None


def _path_list_input(inputs: dict[str, Any], key: str) -> list[Path] | None:
    value = inputs.get(key)
    if value in (None, ""):
        return None
    if isinstance(value, str):
        return [Path(item.strip()) for item in value.replace(";", ",").split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [Path(str(item)) for item in value if str(item).strip()]
    return None


def _required_str(inputs: dict[str, Any], key: str, task_name: str) -> str:
    value = _optional_str(inputs.get(key))
    if not value:
        raise ValueError(f"{task_name} requires {key}.")
    return value


def _csv_set_input(inputs: dict[str, Any], key: str, default: set[str]) -> set[str]:
    value = inputs.get(key)
    if value in (None, ""):
        return set(default)
    if isinstance(value, str):
        return {item.strip() for item in value.replace(";", ",").split(",") if item.strip()}
    if isinstance(value, (list, tuple, set)):
        return {str(item).strip() for item in value if str(item).strip()}
    return set(default)


def _bool_input(inputs: dict[str, Any], key: str, default: bool) -> bool:
    value = inputs.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _int_input(inputs: dict[str, Any], key: str, default: int) -> int:
    value = inputs.get(key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
