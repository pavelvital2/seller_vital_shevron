from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
import hashlib
import hmac
import json
from pathlib import Path
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from seller_agent.control_plane.api import CONTROL_SESSION_COOKIE, create_control_app
from seller_agent.control_plane.auth import SessionSigner
from seller_agent.control_plane.config import ControlPlaneConfig
from seller_agent.core.job_service import JobService
from seller_agent.core.job_store import JobStore
from seller_agent.tasks.registry import RegisteredTask, TaskRegistry


NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
BOT_TOKEN = "123456:stage4a-test-only"
SESSION_SECRET = b"s" * 32
CONTROL_TASKS = frozenset({"store-analytics-overview", "daily-morning-report"})


def _init_data(owner_id: int, *, nonce: str) -> str:
    fields = {
        "auth_date": str(int(NOW.timestamp())),
        "query_id": nonce,
        "user": json.dumps(
            {"id": owner_id, "first_name": "Owner"},
            separators=(",", ":"),
        ),
    }
    check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def _registry(
    *,
    daily_mode: str = "read_only",
    daily_enabled: bool = True,
) -> TaskRegistry:
    registry = TaskRegistry()
    registry.register(
        RegisteredTask(
            name="store-analytics-overview",
            command="store-analytics-overview",
            title="Store analytics overview",
            description="Owner analytics.",
            mode="read_only",
            risk="low",
            parameter_schema={
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
                "wb_supplier_id": {"type": "string"},
                "ozon_seller_slug": {"type": "string"},
                "query_pack_id": {"type": "string", "const": "shevron-core"},
            },
            result_schema={"overall_status": {"type": "string", "required": True}},
        )
    )
    registry.register(
        RegisteredTask(
            name="daily-morning-report",
            command="daily-morning-report",
            title="Daily morning report",
            description="Read-only daily management report.",
            mode=daily_mode,  # type: ignore[arg-type]
            risk="low",
            requires_confirmation=daily_mode == "apply",
            enabled=daily_enabled,
            disabled_reason="test-disabled" if not daily_enabled else "",
        )
    )
    for name, mode in (
        ("unsafe-write", "apply"),
        ("unsafe-dry-run", "dry_run"),
        ("unsafe-verify", "verify"),
    ):
        registry.register(
            RegisteredTask(
                name=name,
                command=name,
                title=name,
                description="Must stay outside Stage 4A.",
                mode=mode,  # type: ignore[arg-type]
                risk="high",
                requires_confirmation=mode == "apply",
            )
        )
    return registry


def _config() -> ControlPlaneConfig:
    return ControlPlaneConfig(
        bot_token=BOT_TOKEN,
        allowed_owner_ids=frozenset({42, 7}),
        session_signer=SessionSigner(SESSION_SECRET, ttl_seconds=600),
        public_app_url="https://example.test/vital-shevron/",
        allowed_task_ids=CONTROL_TASKS,
    )


def _cookie_value(response) -> str:  # type: ignore[no-untyped-def]
    parsed = SimpleCookie()
    parsed.load(response.headers["Set-Cookie"])
    return parsed[CONTROL_SESSION_COOKIE].value


async def _owner_headers(client: TestClient, owner_id: int, *, nonce: str) -> dict[str, str]:
    response = await client.post(
        "/vital-shevron/api/v1/auth/telegram",
        json={"init_data": _init_data(owner_id, nonce=nonce)},
    )
    assert response.status == 200
    return {"Cookie": f"{CONTROL_SESSION_COOKIE}={_cookie_value(response)}"}


async def _submit_headers(
    client: TestClient,
    owner_id: int,
    *,
    nonce: str,
    idempotency_key: str,
) -> dict[str, str]:
    response = await client.post(
        "/vital-shevron/api/v1/auth/telegram",
        json={"init_data": _init_data(owner_id, nonce=nonce)},
    )
    assert response.status == 200
    body = await response.json()
    return {
        "Cookie": f"{CONTROL_SESSION_COOKIE}={_cookie_value(response)}",
        "X-CSRF-Token": body["csrf_token"],
        "Idempotency-Key": idempotency_key,
    }


def _app(tmp_path: Path, *, registry: TaskRegistry | None = None):  # type: ignore[no-untyped-def]
    store = JobStore(tmp_path / "runtime.db")
    service = JobService(
        store=store,
        registry=registry or _registry(),
        data_dir=tmp_path / "data",
    )
    return (
        create_control_app(
            config=_config(),
            store=store,
            service=service,
            now_provider=lambda: NOW,
        ),
        store,
        service,
    )


def test_both_contract_tasks_submit_only_when_enabled_and_read_only(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        app, store, _ = _app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            headers = await _submit_headers(
                client,
                42,
                nonce="both-read-only",
                idempotency_key="analytics-stage4a",
            )
            analytics = await client.post(
                "/vital-shevron/api/v1/jobs",
                json={
                    "task_id": "store-analytics-overview",
                    "params": {
                        "marketplace": "ozon",
                        "period_days": 30,
                        "region_id": "moscow",
                    },
                },
                headers=headers,
            )
            daily = await client.post(
                "/vital-shevron/api/v1/jobs",
                json={"task_id": "daily-morning-report", "params": {}},
                headers={**headers, "Idempotency-Key": "daily-stage4a"},
            )

            assert analytics.status == 202
            assert daily.status == 202
            jobs = store.list_jobs(limit=10)
            assert {job.task_id for job in jobs} == {
                "daily-morning-report",
                "store-analytics-overview",
            }
            daily_job = next(
                job for job in jobs if job.task_id == "daily-morning-report"
            )
            assert daily_job.status == "queued"
            assert daily_job.params == {}
            assert store.get_job_notification_route(daily_job.job_id)["channel"] == "control_bot"  # type: ignore[index]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("daily_mode", "daily_enabled"),
    [("read_only", False), ("dry_run", True), ("apply", True), ("verify", True)],
)
def test_allowlisted_daily_task_fails_before_insert_unless_enabled_read_only(
    tmp_path: Path,
    daily_mode: str,
    daily_enabled: bool,
) -> None:
    async def scenario() -> None:
        app, store, _ = _app(
            tmp_path,
            registry=_registry(daily_mode=daily_mode, daily_enabled=daily_enabled),
        )
        async with TestClient(TestServer(app)) as client:
            headers = await _submit_headers(
                client,
                42,
                nonce=f"daily-{daily_mode}-{daily_enabled}",
                idempotency_key="daily-mode-check",
            )
            response = await client.post(
                "/vital-shevron/api/v1/jobs",
                json={"task_id": "daily-morning-report", "params": {}},
                headers=headers,
            )
            assert response.status == 403
            assert (await response.json())["error"]["code"] == "task_not_allowed"
            assert store.list_jobs(limit=10) == []

    asyncio.run(scenario())


def test_contract_params_and_non_stage4a_tasks_fail_closed_before_insert(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        app, store, _ = _app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            headers = await _submit_headers(
                client,
                42,
                nonce="negative-contracts",
                idempotency_key="negative-contract-1",
            )
            cases = [
                (
                    {
                        "task_id": "store-analytics-overview",
                        "params": {
                            "marketplace": "wb",
                            "period_days": 30,
                            "region_id": "kazan",
                            "wb_supplier_id": "attacker-scope",
                        },
                    },
                    400,
                ),
                (
                    {
                        "task_id": "daily-morning-report",
                        "params": {"seller_v3": False},
                    },
                    400,
                ),
                ({"task_id": "unsafe-write", "params": {}}, 403),
                ({"task_id": "unsafe-dry-run", "params": {}}, 403),
                ({"task_id": "unsafe-verify", "params": {}}, 403),
                ({"task_id": "arbitrary-task", "params": {}}, 404),
            ]
            for index, (payload, expected_status) in enumerate(cases, start=1):
                response = await client.post(
                    "/vital-shevron/api/v1/jobs",
                    json=payload,
                    headers={
                        **headers,
                        "Idempotency-Key": f"negative-contract-{index}",
                    },
                )
                assert response.status == expected_status
            assert store.list_jobs(limit=20) == []

    asyncio.run(scenario())


def test_jobs_list_is_owner_scoped_bounded_and_minimally_projected(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        app, store, service = _app(tmp_path)
        owner_job = service.submit_control_read_only(
            task_id="store-analytics-overview",
            params={"marketplace": "wb", "period_days": 30, "region_id": "kazan"},
            owner_id="42",
            chat_id="42",
            idempotency_key="owner-list-job",
            allowed_task_ids=CONTROL_TASKS,
            server_params={
                "wb_supplier_id": "4516781",
                "ozon_seller_slug": "vital-shevron",
                "query_pack_id": "shevron-core",
            },
        )
        service.submit_control_read_only(
            task_id="store-analytics-overview",
            params={"marketplace": "ozon", "period_days": 7, "region_id": "moscow"},
            owner_id="7",
            chat_id="7",
            idempotency_key="foreign-list-job",
            allowed_task_ids=CONTROL_TASKS,
            server_params={
                "wb_supplier_id": "4516781",
                "ozon_seller_slug": "vital-shevron",
                "query_pack_id": "shevron-core",
            },
        )
        store.update_job_status(
            owner_job.job.job_id,
            "success",
            result={
                "raw": {"api_token": "must-not-leak"},
                "artifacts": {"report": "/home/pavel/private/report.md"},
            },
            error="Bearer must-not-leak",
        )
        async with TestClient(TestServer(app)) as client:
            owner_headers = await _owner_headers(client, 42, nonce="owner-list")
            response = await client.get(
                "/vital-shevron/api/v1/jobs?limit=1",
                headers=owner_headers,
            )
            assert response.status == 200
            payload = await response.json()
            assert set(payload) == {"data_available", "jobs", "limit"}
            assert payload["data_available"] is True
            assert payload["limit"] == 1
            assert len(payload["jobs"]) == 1
            assert set(payload["jobs"][0]) == {
                "job_id",
                "task_id",
                "status",
                "created_at",
                "updated_at",
                "finished_at",
            }
            assert payload["jobs"][0]["job_id"] == owner_job.public_job_id
            text = await response.text()
            for forbidden in (
                owner_job.job.job_id,
                "control_owner",
                "params",
                "artifact",
                "/home/pavel",
                "must-not-leak",
                "notification",
            ):
                assert forbidden not in text

            foreign_headers = await _owner_headers(client, 7, nonce="foreign-list")
            foreign = await client.get(
                "/vital-shevron/api/v1/jobs?limit=20",
                headers=foreign_headers,
            )
            foreign_text = await foreign.text()
            assert owner_job.public_job_id not in foreign_text
            assert len((await foreign.json())["jobs"]) == 1

            for query in (
                "limit=0",
                "limit=31",
                "limit=-1",
                "limit=abc",
                "limit=1.0",
                "limit=01",
                "limit=1&limit=2",
                "limit=1&other=2",
            ):
                invalid = await client.get(
                    f"/vital-shevron/api/v1/jobs?{query}",
                    headers=owner_headers,
                )
                assert invalid.status == 400
                assert (await invalid.json())["error"]["code"] == "query_invalid"

            missing = await client.get("/vital-shevron/api/v1/jobs")
            assert missing.status == 401
            expired = _config().session_signer.issue(
                42,
                now=NOW - timedelta(seconds=601),
            )
            expired_response = await client.get(
                "/vital-shevron/api/v1/jobs",
                headers={
                    "Cookie": (
                        f"{CONTROL_SESSION_COOKIE}={expired.cookie_value}"
                    )
                },
            )
            assert expired_response.status == 401
            assert (await expired_response.json())["error"]["code"] == "session_expired"

    asyncio.run(scenario())


def test_approvals_are_unresolved_read_only_projections_without_apply_payload(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        app, store, _ = _app(tmp_path)
        source = store.create_job(
            task_id="store-analytics-overview",
            params={"private_path": "/home/pavel/private"},
            actor="internal-actor",
            status="success",
        )
        store.create_approval(
            approval_id="approval-pending-secret",
            source_job_id=source.job_id,
            status="pending_review",
            checksum="secret-checksum",
            data={"apply_params": {"api_token": "must-not-leak"}},
        )
        store.create_approval(
            approval_id="approval-unknown-secret",
            source_job_id=source.job_id,
            status="applying_unknown",
            checksum="unknown-checksum",
            data={"marketplace_payload": {"secret": "must-not-leak"}},
        )
        store.create_approval(
            approval_id="approval-closed",
            source_job_id=source.job_id,
            status="closed",
        )
        async with TestClient(TestServer(app)) as client:
            headers = await _owner_headers(client, 42, nonce="approvals")
            response = await client.get(
                "/vital-shevron/api/v1/approvals",
                headers=headers,
            )
            assert response.status == 200
            payload = await response.json()
            assert set(payload) == {"data_available", "counts", "approvals"}
            assert payload["counts"] == {
                "pending_review": 1,
                "applying_unknown": 1,
            }
            assert len(payload["approvals"]) == 2
            assert all(
                set(item)
                == {
                    "task_id",
                    "label",
                    "status",
                    "created_at",
                    "updated_at",
                    "requires_reconciliation",
                }
                for item in payload["approvals"]
            )
            unknown = next(
                item
                for item in payload["approvals"]
                if item["status"] == "applying_unknown"
            )
            assert unknown["requires_reconciliation"] is True
            text = await response.text()
            for forbidden in (
                "approval-pending-secret",
                "approval-unknown-secret",
                source.job_id,
                "checksum",
                "apply_params",
                "marketplace_payload",
                "/home/pavel",
                "must-not-leak",
            ):
                assert forbidden not in text

    asyncio.run(scenario())


def test_operations_summary_has_safe_counts_and_latest_owner_control_job(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        app, store, service = _app(tmp_path)
        store.create_job(task_id="status-preflight", status="running")
        store.create_job(task_id="status-preflight", status="failed")
        store.create_job(task_id="status-preflight", status="cancelled")
        latest = service.submit_control_read_only(
            task_id="store-analytics-overview",
            params={"marketplace": "wb", "period_days": 7, "region_id": "kazan"},
            owner_id="42",
            chat_id="42",
            idempotency_key="summary-latest",
            allowed_task_ids=CONTROL_TASKS,
            server_params={
                "wb_supplier_id": "4516781",
                "ozon_seller_slug": "vital-shevron",
                "query_pack_id": "shevron-core",
            },
        )
        source = store.create_job(task_id="unsafe-dry-run", status="success")
        store.create_approval(
            approval_id="summary-pending",
            source_job_id=source.job_id,
            status="pending_review",
            data={"secret": "must-not-leak"},
        )
        store.create_approval(
            approval_id="summary-unknown",
            source_job_id=source.job_id,
            status="applying_unknown",
        )
        async with TestClient(TestServer(app)) as client:
            headers = await _owner_headers(client, 42, nonce="summary")
            response = await client.get(
                "/vital-shevron/api/v1/operations/summary",
                headers=headers,
            )
            assert response.status == 200
            payload = await response.json()
            assert set(payload) == {
                "data_available",
                "observed_at",
                "jobs",
                "approvals",
                "last_control_job",
            }
            assert payload["data_available"] is True
            assert payload["jobs"] == {"active": 2, "terminal": 3}
            assert payload["approvals"] == {
                "pending_review": 1,
                "applying_unknown": 1,
            }
            assert payload["last_control_job"] == {
                "job_id": latest.public_job_id,
                "task_id": "store-analytics-overview",
                "status": "queued",
                "created_at": latest.job.created_at,
                "updated_at": latest.job.updated_at,
            }
            text = await response.text()
            assert "systemd" not in text
            assert "marketplace_healthy" not in text
            assert "must-not-leak" not in text

    asyncio.run(scenario())


def test_stage4a_get_endpoints_require_owner_session(tmp_path: Path) -> None:
    async def scenario() -> None:
        app, _, _ = _app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            for path in (
                "/vital-shevron/api/v1/operations/summary",
                "/vital-shevron/api/v1/jobs",
                "/vital-shevron/api/v1/approvals",
            ):
                response = await client.get(path)
                assert response.status == 401
                assert (await response.json())["error"]["code"] == "session_invalid"

    asyncio.run(scenario())
