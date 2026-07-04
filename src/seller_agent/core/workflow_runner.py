from __future__ import annotations

from dataclasses import asdict, dataclass, field
import fcntl
from pathlib import Path
from typing import Any, Callable

from seller_agent.config import AppCredentials, load_credentials
from seller_agent.tasks.approved_cards_apply import run_apply_approved_cards
from seller_agent.tasks.daily_morning_report import run_daily_morning_report
from seller_agent.tasks.pricing_status import run_pricing_status
from seller_agent.tasks.registry import RegisteredTask, TaskRegistry, default_task_registry
from seller_agent.tasks.status_preflight import run_status_preflight


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
        "pricing-status": _pricing_status_handler,
        "status-preflight": _status_preflight_handler,
        "approved-cards-batch-apply": _approved_cards_batch_apply_handler,
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
    if not isinstance(internal_skus, list) or not internal_skus:
        raise ValueError("approved-cards-batch-apply requires internal_skus list.")
    return run_apply_approved_cards(
        credentials=credentials,
        data_dir=data_dir,
        internal_skus=[str(item) for item in internal_skus],
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
    )


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


def _bool_input(inputs: dict[str, Any], key: str, default: bool) -> bool:
    value = inputs.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)
