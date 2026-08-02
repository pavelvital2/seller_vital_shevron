from __future__ import annotations

from datetime import datetime, timezone
import hmac
import json
from pathlib import Path
import re
from typing import Any, Callable

from aiohttp import web

from seller_agent.control_plane.auth import (
    ControlAuthError,
    ControlSession,
    authenticate_telegram_init_data,
)
from seller_agent.control_plane.config import ControlPlaneConfig
from seller_agent.control_plane.contracts import (
    CONTROL_TASK_CONTRACTS,
    ControlTaskContract,
    control_task_contract,
)
from seller_agent.core.job_service import JobService
from seller_agent.core.job_store import ControlRequestConflictError, JobStore


CONTROL_SESSION_COOKIE = "__Host-vs_control_session"
API_PREFIX = "/vital-shevron/api/v1"
MAX_JSON_BODY_BYTES = 32_768
MAX_HEADER_BYTES = 16_384
MAX_HEADER_COUNT = 64
_PUBLIC_JOB_ID = re.compile(r"^cpj_[A-Za-z0-9_-]{20,64}$")
_SAFE_TASK_ID = re.compile(r"^[a-z][a-z0-9-]{0,95}$")
_SAFE_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)
_LIMIT = re.compile(r"^(?:[1-9]|[12][0-9]|30)$")
_SAFE_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SENSITIVE_KEY = re.compile(
    r"(?:authorization|cookie|init[_-]?data|query[_-]?id|secret|token|password|user)",
    re.IGNORECASE,
)
_SENSITIVE_TEXT = re.compile(
    r"(?:Bearer\s+\S+|\d{5,}:[A-Za-z0-9_-]{16,}|(?:^|[?&])query_id=)",
    re.IGNORECASE,
)
_PUBLIC_DROP_KEYS = frozenset({"artifacts", "inputs", "raw", "raw_payload"})
_PUBLIC_JOB_ERROR_CODES = frozenset(
    {
        "cancelled",
        "job_failed",
        "resource_locked",
        "source_unavailable",
        "task_disabled",
        "timeout",
        "workflow_busy",
        "workflow_failed",
    }
)
_PUBLIC_JOB_STATUSES = frozenset(
    {
        "created",
        "queued",
        "running",
        "waiting_confirmation",
        "success",
        "partial_success",
        "failed",
        "timeout",
        "cancelled",
    }
)
_PUBLIC_DAILY_OUTCOMES = frozenset(
    {"ok", "warning", "partial", "blocked", "error"}
)
_CSP = (
    "default-src 'self'; "
    "script-src 'self' https://telegram.org; "
    "style-src 'self'; img-src 'self' data:; connect-src 'self'; "
    "font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
    "frame-ancestors https://web.telegram.org https://*.telegram.org"
)
CONFIG_KEY = web.AppKey("control_config", ControlPlaneConfig)
STORE_KEY = web.AppKey("control_store", JobStore)
SERVICE_KEY = web.AppKey("control_service", JobService)
NOW_KEY = web.AppKey("control_now", Callable[[], datetime])
STATIC_KEY = web.AppKey("control_static_dir", Path)


class ApiProblem(Exception):
    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


def create_control_app(
    *,
    config: ControlPlaneConfig,
    store: JobStore,
    service: JobService,
    now_provider: Callable[[], datetime] | None = None,
    static_dir: Path | None = None,
) -> web.Application:
    app = web.Application(
        client_max_size=MAX_JSON_BODY_BYTES,
        middlewares=[
            _security_headers_middleware,
            _error_middleware,
            _request_limits_middleware,
            _loopback_only_middleware,
        ],
    )
    app[CONFIG_KEY] = config
    app[STORE_KEY] = store
    app[SERVICE_KEY] = service
    app[NOW_KEY] = now_provider or (lambda: datetime.now(timezone.utc))
    app.router.add_get(f"{API_PREFIX}/health", _health)
    app.router.add_get(f"{API_PREFIX}/ready", _ready)
    app.router.add_post(f"{API_PREFIX}/auth/telegram", _auth_telegram)
    app.router.add_get(f"{API_PREFIX}/session", _session_status)
    app.router.add_post(f"{API_PREFIX}/jobs", _submit_job)
    app.router.add_get(f"{API_PREFIX}/operations/summary", _operations_summary)
    app.router.add_get(f"{API_PREFIX}/jobs", _list_jobs)
    app.router.add_get(f"{API_PREFIX}/approvals", _list_approvals)
    app.router.add_get(f"{API_PREFIX}/jobs/{{public_job_id}}", _job_status)

    asset_root = static_dir or Path(__file__).with_name("static")
    if asset_root.is_dir():
        app.router.add_get("/vital-shevron/", _index)
        app.router.add_get("/vital-shevron/app.css", _static_css)
        app.router.add_get("/vital-shevron/app.js", _static_js)
        app.router.add_get("/vital-shevron/icons.svg", _static_icons)
        app[STATIC_KEY] = asset_root
    return app


@web.middleware
async def _error_middleware(request: web.Request, handler):  # type: ignore[no-untyped-def]
    try:
        return await handler(request)
    except ApiProblem as exc:
        return _json_error(exc.status, exc.code)
    except web.HTTPRequestEntityTooLarge:
        return _json_error(413, "request_too_large")
    except web.HTTPException as exc:
        if request.path.startswith(API_PREFIX):
            code = "not_found" if exc.status == 404 else "method_not_allowed"
            return _json_error(exc.status, code)
        raise
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _json_error(400, "json_invalid")
    except Exception:  # noqa: BLE001 - API must never expose stack or dynamic messages.
        return _json_error(500, "internal_error")


@web.middleware
async def _security_headers_middleware(request: web.Request, handler):  # type: ignore[no-untyped-def]
    response = await handler(request)
    response.headers["Content-Security-Policy"] = _CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    if request.path.startswith("/vital-shevron/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@web.middleware
async def _request_limits_middleware(request: web.Request, handler):  # type: ignore[no-untyped-def]
    if len(request.headers) > MAX_HEADER_COUNT:
        raise ApiProblem(431, "headers_too_large")
    header_size = sum(len(key) + len(value) for key, value in request.headers.items())
    if header_size > MAX_HEADER_BYTES:
        raise ApiProblem(431, "headers_too_large")
    if request.content_length is not None and request.content_length > MAX_JSON_BODY_BYTES:
        raise ApiProblem(413, "request_too_large")
    return await handler(request)


@web.middleware
async def _loopback_only_middleware(request: web.Request, handler):  # type: ignore[no-untyped-def]
    if request.remote not in {"127.0.0.1", "::1", None}:
        raise ApiProblem(403, "local_proxy_required")
    return await handler(request)


async def _health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "vital-shevron-control"})


async def _ready(request: web.Request) -> web.Response:
    try:
        request.app[STORE_KEY].initialize()
        config: ControlPlaneConfig = request.app[CONFIG_KEY]
        service: JobService = request.app[SERVICE_KEY]
        for task_id in config.allowed_task_ids:
            contract = control_task_contract(task_id)
            if contract is None:
                raise RuntimeError
            task = service.registry.get(contract.registry_task_id)
            if not contract.accepts_registered_task(task):
                raise RuntimeError
    except Exception:  # noqa: BLE001 - readiness exposes one stable code.
        return _json_error(503, "service_not_ready")
    return web.json_response({"ok": True, "ready": True})


async def _auth_telegram(request: web.Request) -> web.Response:
    payload = await _strict_json_object(request, fields={"init_data"})
    raw = payload.get("init_data")
    if not isinstance(raw, str):
        raise ApiProblem(400, "init_data_missing")
    config: ControlPlaneConfig = request.app[CONFIG_KEY]
    try:
        user = authenticate_telegram_init_data(
            raw,
            bot_token=config.bot_token,
            allowed_owner_ids=set(config.allowed_owner_ids),
            store=request.app[STORE_KEY],
            now=request.app[NOW_KEY](),
        )
    except ControlAuthError as exc:
        status = 403 if exc.code == "owner_denied" else 409 if exc.code == "init_data_replayed" else 401
        raise ApiProblem(status, exc.code) from exc
    issued = config.session_signer.issue(user.user_id, now=request.app[NOW_KEY]())
    response = web.json_response(
        {
            "ok": True,
            "csrf_token": issued.csrf_token,
            "expires_at": issued.expires_at,
        }
    )
    response.set_cookie(
        CONTROL_SESSION_COOKIE,
        issued.cookie_value,
        httponly=True,
        secure=True,
        samesite="Strict",
        path="/",
        max_age=config.session_signer.ttl_seconds,
    )
    return response


async def _session_status(request: web.Request) -> web.Response:
    session = _require_session(request)
    return web.json_response(
        {
            "ok": True,
            "csrf_token": session.csrf_token,
            "expires_at": session.expires_at,
        }
    )


async def _submit_job(request: web.Request) -> web.Response:
    session = _require_session(request)
    supplied_csrf = request.headers.get("X-CSRF-Token", "")
    if not supplied_csrf or not hmac.compare_digest(supplied_csrf, session.csrf_token):
        raise ApiProblem(403, "csrf_invalid")
    idempotency_key = request.headers.get("Idempotency-Key", "")
    payload = await _strict_json_object(request, fields={"task_id", "params"})
    task_id = payload.get("task_id")
    params = payload.get("params")
    if not isinstance(task_id, str) or not isinstance(params, dict):
        raise ApiProblem(400, "request_invalid")
    config: ControlPlaneConfig = request.app[CONFIG_KEY]
    service: JobService = request.app[SERVICE_KEY]
    contract = control_task_contract(task_id)
    if contract is None:
        raise _task_access_problem(service, task_id)
    if task_id not in config.allowed_task_ids:
        raise ApiProblem(403, "task_not_allowed")
    try:
        registered_task = service.registry.get(contract.registry_task_id)
    except KeyError as exc:
        raise ApiProblem(404, "task_not_registered") from exc
    if not contract.accepts_registered_task(registered_task):
        raise ApiProblem(403, "task_not_allowed")
    if not contract.accepts_client_params(params):
        raise ApiProblem(400, "params_invalid")
    try:
        server_params = contract.server_params(config)
    except ValueError as exc:
        raise ApiProblem(500, "control_contract_invalid") from exc
    try:
        submission = service.submit_control_read_only(
            task_id=contract.registry_task_id,
            params=params,
            owner_id=str(session.owner_id),
            chat_id=str(session.owner_id),
            idempotency_key=idempotency_key,
            allowed_task_ids=frozenset({contract.registry_task_id}),
            server_params=server_params,
        )
    except ControlRequestConflictError as exc:
        raise ApiProblem(409, "idempotency_conflict") from exc
    except ValueError as exc:
        code = str(exc)
        if code == "task_not_registered":
            raise ApiProblem(404, code) from exc
        if code == "task_not_allowed":
            raise ApiProblem(403, code) from exc
        raise ApiProblem(400, code if _SAFE_ERROR_CODE.fullmatch(code) else "request_invalid") from exc
    return web.json_response(
        {
            "created": submission.created,
            "job_id": submission.public_job_id,
            "status": submission.job.status,
        },
        status=202 if submission.created else 200,
    )


async def _operations_summary(request: web.Request) -> web.Response:
    session = _require_session(request)
    observed_at = _format_public_timestamp(request.app[NOW_KEY]())
    try:
        snapshot = request.app[STORE_KEY].get_control_operations_snapshot(
            owner_id=str(session.owner_id),
            task_ids=tuple(sorted(CONTROL_TASK_CONTRACTS)),
        )
        latest = _project_control_job_row(
            request.app[SERVICE_KEY],
            snapshot.get("last_control_job"),
            include_finished=False,
        )
        return web.json_response(
            {
                "data_available": True,
                "observed_at": observed_at,
                "jobs": {
                    "active": _safe_count(snapshot.get("jobs"), "active"),
                    "terminal": _safe_count(snapshot.get("jobs"), "terminal"),
                },
                "approvals": {
                    "pending_review": _safe_count(
                        snapshot.get("approvals"), "pending_review"
                    ),
                    "applying_unknown": _safe_count(
                        snapshot.get("approvals"), "applying_unknown"
                    ),
                },
                "last_control_job": latest,
            }
        )
    except Exception:  # noqa: BLE001 - unavailable snapshot must stay data-only.
        return web.json_response(
            {
                "data_available": False,
                "observed_at": observed_at,
                "jobs": {"active": None, "terminal": None},
                "approvals": {
                    "pending_review": None,
                    "applying_unknown": None,
                },
                "last_control_job": None,
            }
        )


async def _list_jobs(request: web.Request) -> web.Response:
    session = _require_session(request)
    limit = _strict_limit_query(request)
    rows = request.app[STORE_KEY].list_control_jobs_for_owner(
        owner_id=str(session.owner_id),
        limit=limit,
    )
    jobs = [
        projected
        for row in rows
        if (
            projected := _project_control_job_row(
                request.app[SERVICE_KEY],
                row,
                include_finished=True,
            )
        )
        is not None
    ]
    return web.json_response(
        {"data_available": True, "jobs": jobs, "limit": limit}
    )


async def _list_approvals(request: web.Request) -> web.Response:
    _require_session(request)
    counts, rows = request.app[STORE_KEY].list_unresolved_approval_summaries()
    service: JobService = request.app[SERVICE_KEY]
    approvals = []
    for row in rows:
        status = str(row.get("status") or "")
        if status not in {"pending_review", "applying_unknown"}:
            continue
        task_id, label = _registered_task_identity(
            service,
            str(row.get("task_id") or ""),
        )
        approvals.append(
            {
                "task_id": task_id,
                "label": label,
                "status": status,
                "created_at": _safe_timestamp(row.get("created_at")),
                "updated_at": _safe_timestamp(row.get("updated_at")),
                "requires_reconciliation": status == "applying_unknown",
            }
        )
    return web.json_response(
        {
            "data_available": True,
            "counts": {
                "pending_review": _safe_count(counts, "pending_review"),
                "applying_unknown": _safe_count(counts, "applying_unknown"),
            },
            "approvals": approvals,
        }
    )


async def _job_status(request: web.Request) -> web.Response:
    session = _require_session(request)
    public_job_id = request.match_info.get("public_job_id", "")
    if not _PUBLIC_JOB_ID.fullmatch(public_job_id):
        raise ApiProblem(404, "job_not_found")
    store: JobStore = request.app[STORE_KEY]
    job = store.get_control_job_for_owner(
        owner_id=str(session.owner_id),
        public_job_id=public_job_id,
    )
    if job is None:
        raise ApiProblem(404, "job_not_found")
    response: dict[str, Any] = {
        "job_id": public_job_id,
        "task_id": job.task_id,
        "status": job.status,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "finished_at": job.finished_at,
    }
    contract = control_task_contract(job.task_id)
    response["result_type"] = (
        contract.result_renderer if contract is not None else "status_only_v1"
    )
    if job.result:
        response["result"] = _project_public_result(contract, job.result)
    if job.error:
        response["error"] = _safe_job_error(job.error)
    return web.json_response(response)


def _require_session(request: web.Request) -> ControlSession:
    cookie = request.cookies.get(CONTROL_SESSION_COOKIE, "")
    config: ControlPlaneConfig = request.app[CONFIG_KEY]
    try:
        return config.session_signer.verify(cookie, now=request.app[NOW_KEY]())
    except ControlAuthError as exc:
        raise ApiProblem(401, exc.code) from exc


def _task_access_problem(service: JobService, task_id: str) -> ApiProblem:
    try:
        service.registry.get(task_id)
    except KeyError:
        return ApiProblem(404, "task_not_registered")
    return ApiProblem(403, "task_not_allowed")


def _strict_limit_query(request: web.Request) -> int:
    query = request.rel_url.query
    if not query:
        return 20
    if len(query) != 1 or set(query) != {"limit"}:
        raise ApiProblem(400, "query_invalid")
    values = query.getall("limit", [])
    if len(values) != 1 or not _LIMIT.fullmatch(values[0]):
        raise ApiProblem(400, "query_invalid")
    return int(values[0])


def _project_control_job_row(
    service: JobService,
    row: object,
    *,
    include_finished: bool,
) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    public_job_id = str(row.get("job_id") or "")
    if not _PUBLIC_JOB_ID.fullmatch(public_job_id):
        return None
    task_id, _ = _registered_task_identity(
        service,
        str(row.get("task_id") or ""),
        control_only=True,
    )
    if task_id == "unknown":
        return None
    projected: dict[str, Any] = {
        "job_id": public_job_id,
        "task_id": task_id,
        "status": _safe_job_status(row.get("status")),
        "created_at": _safe_timestamp(row.get("created_at")),
        "updated_at": _safe_timestamp(row.get("updated_at")),
    }
    if include_finished:
        projected["finished_at"] = _safe_timestamp(row.get("finished_at"))
    return projected


def _registered_task_identity(
    service: JobService,
    raw_task_id: str,
    *,
    control_only: bool = False,
) -> tuple[str, str]:
    if not _SAFE_TASK_ID.fullmatch(raw_task_id):
        return "unknown", "Неизвестная задача"
    try:
        task = service.registry.get(raw_task_id)
    except KeyError:
        return "unknown", "Неизвестная задача"
    if task.name != raw_task_id:
        return "unknown", "Неизвестная задача"
    contract = control_task_contract(task.name)
    if control_only and contract is None:
        return "unknown", "Неизвестная задача"
    label = contract.label if contract is not None else str(task.title).strip()[:128]
    if not label or _SENSITIVE_TEXT.search(label):
        label = "Зарегистрированная задача"
    return task.name, label


def _safe_job_status(value: object) -> str:
    normalized = str(value or "")
    return normalized if normalized in _PUBLIC_JOB_STATUSES else "unknown"


def _safe_timestamp(value: object) -> str:
    normalized = str(value or "")
    return normalized if _SAFE_TIMESTAMP.fullmatch(normalized) else ""


def _format_public_timestamp(value: datetime) -> str:
    current = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _safe_count(value: object, key: str) -> int:
    if not isinstance(value, dict):
        return 0
    count = value.get(key)
    return int(count) if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else 0


def _project_public_result(
    contract: ControlTaskContract | None,
    result: dict[str, Any],
) -> dict[str, Any]:
    if contract is None:
        return {}
    if contract.result_renderer == "store_analytics_overview_v1":
        projected = _sanitize_public_value(result)
        return projected if isinstance(projected, dict) else {}
    if contract.result_renderer == "daily_report_status_v1":
        summary = result.get("summary")
        source = summary if isinstance(summary, dict) else result
        outcome = str(source.get("overall_status") or "")
        if outcome not in _PUBLIC_DAILY_OUTCOMES:
            outcome = "unknown"
        return {"summary": {"overall_status": outcome}}
    return {}


async def _strict_json_object(request: web.Request, *, fields: set[str]) -> dict[str, Any]:
    if request.content_type != "application/json":
        raise ApiProblem(415, "content_type_invalid")
    try:
        payload = await request.json(loads=json.loads)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ApiProblem(400, "json_invalid") from exc
    if not isinstance(payload, dict) or set(payload) != fields:
        raise ApiProblem(400, "request_invalid")
    return payload


def _sanitize_public_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[redacted]" if _SENSITIVE_KEY.search(str(key)) else _sanitize_public_value(item)
            for key, item in value.items()
            if str(key).lower() not in _PUBLIC_DROP_KEYS
        }
    if isinstance(value, list):
        return [_sanitize_public_value(item) for item in value]
    if isinstance(value, str):
        if _SENSITIVE_TEXT.search(value):
            return "[redacted]"
        return value[:4096]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:256]


def _safe_job_error(value: str) -> str:
    normalized = str(value).strip()
    return normalized if normalized in _PUBLIC_JOB_ERROR_CODES else "job_failed"


def _json_error(status: int, code: str) -> web.Response:
    return web.json_response({"error": {"code": code}}, status=status)


def _asset_path(request: web.Request, name: str) -> Path:
    return request.app[STATIC_KEY] / name


async def _index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(_asset_path(request, "index.html"))


async def _static_css(request: web.Request) -> web.FileResponse:
    return web.FileResponse(_asset_path(request, "app.css"))


async def _static_js(request: web.Request) -> web.FileResponse:
    return web.FileResponse(_asset_path(request, "app.js"))


async def _static_icons(request: web.Request) -> web.FileResponse:
    return web.FileResponse(_asset_path(request, "icons.svg"))
