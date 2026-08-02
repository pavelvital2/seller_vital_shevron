from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from http.cookies import SimpleCookie
import hashlib
import hmac
import json
from pathlib import Path
from urllib.parse import urlencode

from aiohttp.test_utils import TestClient, TestServer

from seller_agent.control_plane.api import CONTROL_SESSION_COOKIE, create_control_app
from seller_agent.control_plane.auth import SessionSigner
from seller_agent.control_plane.config import ControlPlaneConfig
from seller_agent.core.job_service import JobService
from seller_agent.core.job_store import JobStore
from seller_agent.tasks.registry import RegisteredTask, TaskRegistry


NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
BOT_TOKEN = "123456:test-only-token"


def _init_data(owner_id: int, *, nonce: str) -> str:
    fields = {
        "auth_date": str(int(NOW.timestamp())),
        "query_id": nonce,
        "user": json.dumps({"id": owner_id, "first_name": "Owner"}, separators=(",", ":")),
    }
    check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def _registry() -> TaskRegistry:
    registry = TaskRegistry()
    registry.register(
        RegisteredTask(
            name="store-analytics-overview",
            command="store-analytics-overview",
            title="Overview",
            description="Owner analytics.",
            mode="read_only",
            risk="low",
            parameter_schema={
                "marketplace": {"type": "string", "enum": ["ozon", "wb"], "required": True},
                "period_days": {"type": "integer", "enum": [7, 30, 90], "required": True},
                "region_id": {
                    "type": "string",
                    "enum": ["moscow", "rostov-on-don", "novosibirsk", "kazan"],
                    "required": True,
                },
                "wb_supplier_id": {"type": "string"},
                "ozon_seller_slug": {"type": "string"},
                "query_pack_id": {"type": "string", "const": "shevron-core"},
            },
            result_schema={"overall_status": {"type": "string", "required": True}},
        )
    )
    registry.register(
        RegisteredTask(
            name="write-task",
            command="write-task",
            title="Write",
            description="Unsafe.",
            mode="apply",
            risk="high",
            requires_confirmation=True,
        )
    )
    return registry


def _config() -> ControlPlaneConfig:
    return ControlPlaneConfig(
        bot_token=BOT_TOKEN,
        allowed_owner_ids=frozenset({42, 7}),
        session_signer=SessionSigner(b"k" * 32, ttl_seconds=600),
        public_app_url="https://example.test/vital-shevron/",
        allowed_task_ids=frozenset({"store-analytics-overview"}),
    )


def _cookie_value(response) -> str:  # type: ignore[no-untyped-def]
    parsed = SimpleCookie()
    parsed.load(response.headers["Set-Cookie"])
    return parsed[CONTROL_SESSION_COOKIE].value


def test_index_and_static_assets_are_never_cached(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = JobStore(tmp_path / "runtime.db")
        app = create_control_app(
            config=_config(),
            store=store,
            service=JobService(store=store, registry=_registry(), data_dir=tmp_path / "data"),
        )
        async with TestClient(TestServer(app)) as client:
            for path in (
                "/vital-shevron/",
                "/vital-shevron/app.js",
                "/vital-shevron/app.css",
                "/vital-shevron/icons.svg",
            ):
                response = await client.get(path)
                assert response.status == 200
                assert response.headers["Cache-Control"] == "no-store"

    asyncio.run(scenario())


def test_auth_cookie_csrf_submit_idempotency_and_owner_status(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = JobStore(tmp_path / "runtime.db")
        registry = _registry()
        service = JobService(store=store, registry=registry, data_dir=tmp_path / "data")
        app = create_control_app(
            config=_config(),
            store=store,
            service=service,
            now_provider=lambda: NOW,
        )
        async with TestClient(TestServer(app)) as client:
            health = await client.get("/vital-shevron/api/v1/health")
            ready = await client.get("/vital-shevron/api/v1/ready")
            assert health.status == 200 and ready.status == 200
            assert "Access-Control-Allow-Origin" not in health.headers
            assert "unsafe-inline" not in health.headers["Content-Security-Policy"]
            assert "unsafe-eval" not in health.headers["Content-Security-Policy"]
            auth = await client.post(
                "/vital-shevron/api/v1/auth/telegram",
                json={"init_data": _init_data(42, nonce="owner-42")},
            )
            assert auth.status == 200
            auth_body = await auth.json()
            csrf = auth_body["csrf_token"]
            cookie = _cookie_value(auth)
            set_cookie = auth.headers["Set-Cookie"]
            assert "HttpOnly" in set_cookie
            assert "Secure" in set_cookie
            assert "SameSite=Strict" in set_cookie
            assert "Path=/" in set_cookie
            restored = await client.get(
                "/vital-shevron/api/v1/session",
                headers={"Cookie": f"{CONTROL_SESSION_COOKIE}={cookie}"},
            )
            assert restored.status == 200
            assert (await restored.json())["csrf_token"] == csrf

            headers = {
                "Cookie": f"{CONTROL_SESSION_COOKIE}={cookie}",
                "X-CSRF-Token": csrf,
                "Idempotency-Key": "overview-request-1",
            }
            payload = {
                "task_id": "store-analytics-overview",
                "params": {
                    "marketplace": "ozon",
                    "period_days": 30,
                    "region_id": "moscow",
                },
            }
            first = await client.post(
                "/vital-shevron/api/v1/jobs",
                json=payload,
                headers=headers,
            )
            assert first.status == 202
            first_body = await first.json()
            assert set(first_body) == {"created", "job_id", "status"}
            duplicate = await client.post(
                "/vital-shevron/api/v1/jobs",
                json=payload,
                headers=headers,
            )
            assert duplicate.status == 200
            assert (await duplicate.json())["job_id"] == first_body["job_id"]

            conflict = await client.post(
                "/vital-shevron/api/v1/jobs",
                json={
                    "task_id": "store-analytics-overview",
                    "params": {
                        "marketplace": "ozon",
                        "period_days": 90,
                        "region_id": "moscow",
                    },
                },
                headers=headers,
            )
            assert conflict.status == 409
            assert (await conflict.json())["error"]["code"] == "idempotency_conflict"

            status = await client.get(
                f"/vital-shevron/api/v1/jobs/{first_body['job_id']}",
                headers={"Cookie": f"{CONTROL_SESSION_COOKIE}={cookie}"},
            )
            assert status.status == 200
            status_body = await status.json()
            assert status_body["job_id"] == first_body["job_id"]
            assert "actor" not in status_body
            assert "params" not in status_body

            internal = store.list_jobs(limit=10)[0]
            assert internal.params == {
                "marketplace": "ozon",
                "period_days": 30,
                "region_id": "moscow",
                "wb_supplier_id": "4516781",
                "ozon_seller_slug": "vital-shevron",
                "query_pack_id": "shevron-core",
            }
            store.update_job_status(
                internal.job_id,
                "success",
                error="token123",
                result={
                    "summary": {
                        "overall_status": "ok",
                        "api_token": "must-not-leak",
                        "note": "Bearer must-not-leak",
                        "artifacts": {"report": "/home/pavel/private/report.md"},
                    }
                },
            )
            sanitized = await client.get(
                f"/vital-shevron/api/v1/jobs/{first_body['job_id']}",
                headers={"Cookie": f"{CONTROL_SESSION_COOKIE}={cookie}"},
            )
            sanitized_text = await sanitized.text()
            assert "must-not-leak" not in sanitized_text
            assert "token123" not in sanitized_text
            assert "/home/pavel" not in sanitized_text
            assert "[redacted]" in sanitized_text
            assert "job_failed" in sanitized_text

            other_auth = await client.post(
                "/vital-shevron/api/v1/auth/telegram",
                json={"init_data": _init_data(7, nonce="owner-7")},
            )
            other_cookie = _cookie_value(other_auth)
            denied = await client.get(
                f"/vital-shevron/api/v1/jobs/{first_body['job_id']}",
                headers={"Cookie": f"{CONTROL_SESSION_COOKIE}={other_cookie}"},
            )
            assert denied.status == 404

    asyncio.run(scenario())


def test_api_fails_closed_for_csrf_content_task_path_and_secrets(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = JobStore(tmp_path / "runtime.db")
        registry = _registry()
        service = JobService(store=store, registry=registry, data_dir=tmp_path / "data")
        app = create_control_app(
            config=_config(), store=store, service=service, now_provider=lambda: NOW
        )
        async with TestClient(TestServer(app)) as client:
            auth = await client.post(
                "/vital-shevron/api/v1/auth/telegram",
                json={"init_data": _init_data(42, nonce="negative")},
            )
            cookie = _cookie_value(auth)
            base = {"Cookie": f"{CONTROL_SESSION_COOKIE}={cookie}"}
            no_csrf = await client.post(
                "/vital-shevron/api/v1/jobs",
                json={
                    "task_id": "store-analytics-overview",
                    "params": {
                        "marketplace": "wb",
                        "period_days": 7,
                        "region_id": "moscow",
                    },
                },
                headers={**base, "Idempotency-Key": "negative-key-1"},
            )
            assert no_csrf.status == 403
            assert "unsafe-inline" not in no_csrf.headers["Content-Security-Policy"]
            assert no_csrf.headers["X-Content-Type-Options"] == "nosniff"
            bad_type = await client.post(
                "/vital-shevron/api/v1/jobs",
                data="not-json",
                headers={
                    **base,
                    "Content-Type": "text/plain",
                    "X-CSRF-Token": (await auth.json())["csrf_token"],
                },
            )
            assert bad_type.status == 415
            oversized = await client.post(
                "/vital-shevron/api/v1/jobs",
                data=b"{" + (b"x" * 33_000) + b"}",
                headers={
                    **base,
                    "Content-Type": "application/json",
                    "X-CSRF-Token": (await auth.json())["csrf_token"],
                    "Idempotency-Key": "oversized-request-key",
                },
            )
            assert oversized.status == 413
            traversal = await client.get(
                "/vital-shevron/api/v1/jobs/..%2Fsecrets",
                headers=base,
            )
            assert traversal.status == 404

            headers = {
                **base,
                "X-CSRF-Token": (await auth.json())["csrf_token"],
                "Idempotency-Key": "negative-key-2",
            }
            write = await client.post(
                "/vital-shevron/api/v1/jobs",
                json={"task_id": "write-task", "params": {}},
                headers=headers,
            )
            arbitrary = await client.post(
                "/vital-shevron/api/v1/jobs",
                json={"task_id": "../../../etc/passwd", "params": {}},
                headers={**headers, "Idempotency-Key": "negative-key-3"},
            )
            assert write.status == 403
            assert arbitrary.status == 404
            raw = await write.text()
            assert BOT_TOKEN not in raw
            assert "Traceback" not in raw
            assert len(store.list_jobs(limit=20)) == 0

            bad_region = await client.post(
                "/vital-shevron/api/v1/jobs",
                json={
                    "task_id": "store-analytics-overview",
                    "params": {
                        "marketplace": "ozon",
                        "period_days": 30,
                        "region_id": "kazan",
                    },
                },
                headers={**headers, "Idempotency-Key": "negative-key-4"},
            )
            client_pack = await client.post(
                "/vital-shevron/api/v1/jobs",
                json={
                    "task_id": "store-analytics-overview",
                    "params": {
                        "marketplace": "wb",
                        "period_days": 30,
                        "region_id": "kazan",
                        "query_pack_id": "other-pack",
                    },
                },
                headers={**headers, "Idempotency-Key": "negative-key-5"},
            )
            assert bad_region.status == 400
            assert (await bad_region.json())["error"]["code"] == "params_invalid"
            assert client_pack.status == 400
            assert len(store.list_jobs(limit=20)) == 0

    asyncio.run(scenario())


def test_api_accepts_wb_only_region_and_keeps_query_pack_server_side(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        store = JobStore(tmp_path / "runtime.db")
        service = JobService(store=store, registry=_registry(), data_dir=tmp_path / "data")
        app = create_control_app(
            config=_config(), store=store, service=service, now_provider=lambda: NOW
        )
        async with TestClient(TestServer(app)) as client:
            auth = await client.post(
                "/vital-shevron/api/v1/auth/telegram",
                json={"init_data": _init_data(42, nonce="wb-kazan")},
            )
            cookie = _cookie_value(auth)
            response = await client.post(
                "/vital-shevron/api/v1/jobs",
                json={
                    "task_id": "store-analytics-overview",
                    "params": {
                        "marketplace": "wb",
                        "period_days": 7,
                        "region_id": "kazan",
                    },
                },
                headers={
                    "Cookie": f"{CONTROL_SESSION_COOKIE}={cookie}",
                    "X-CSRF-Token": (await auth.json())["csrf_token"],
                    "Idempotency-Key": "wb-kazan-request",
                },
            )

            assert response.status == 202
            job = store.list_jobs(limit=10)[0]
            assert job.params["region_id"] == "kazan"
            assert job.params["query_pack_id"] == "shevron-core"

    asyncio.run(scenario())
