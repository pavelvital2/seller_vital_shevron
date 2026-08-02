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
from seller_agent.core.job_service import JobService
from seller_agent.core.job_store import ControlRequestConflictError, JobStore
from seller_agent.tasks.store_analytics_overview import (
    DEFAULT_QUERY_PACK_ID,
    REGIONS_BY_MARKETPLACE,
)


CONTROL_SESSION_COOKIE = "__Host-vs_control_session"
API_PREFIX = "/vital-shevron/api/v1"
MAX_JSON_BODY_BYTES = 32_768
MAX_HEADER_BYTES = 16_384
MAX_HEADER_COUNT = 64
_PUBLIC_JOB_ID = re.compile(r"^cpj_[A-Za-z0-9_-]{20,64}$")
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
        task = request.app[SERVICE_KEY].registry.get("store-analytics-overview")
        if not task.enabled or not task.is_read_only:
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
    if task_id in config.allowed_task_ids:
        if set(params) != {"marketplace", "period_days", "region_id"}:
            raise ApiProblem(400, "params_invalid")
        marketplace = params.get("marketplace")
        region_id = params.get("region_id")
        if (
            not isinstance(marketplace, str)
            or not isinstance(region_id, str)
            or region_id not in REGIONS_BY_MARKETPLACE.get(marketplace, frozenset())
        ):
            raise ApiProblem(400, "params_invalid")
    service: JobService = request.app[SERVICE_KEY]
    try:
        submission = service.submit_control_read_only(
            task_id=task_id,
            params=params,
            owner_id=str(session.owner_id),
            chat_id=str(session.owner_id),
            idempotency_key=idempotency_key,
            allowed_task_ids=config.allowed_task_ids,
            server_params={
                "wb_supplier_id": config.wb_supplier_id,
                "ozon_seller_slug": config.ozon_seller_slug,
                "query_pack_id": DEFAULT_QUERY_PACK_ID,
            },
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
    if job.result:
        response["result"] = _sanitize_public_value(job.result)
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
