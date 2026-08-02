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
from seller_agent.control_plane.config import ControlPlaneConfig, DEFAULT_CONTROL_TASK_IDS
from seller_agent.control_plane.contracts import CONTROL_TASK_CONTRACTS
from seller_agent.core.job_service import JobService
from seller_agent.core.job_store import JobStore
from seller_agent.tasks.registry import default_task_registry


NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
BOT_TOKEN = "123456:stage4a-contract-test"


def _config() -> ControlPlaneConfig:
    return ControlPlaneConfig(
        bot_token=BOT_TOKEN,
        allowed_owner_ids=frozenset({42}),
        session_signer=SessionSigner(b"c" * 32),
        public_app_url="https://example.test/vital-shevron/",
    )


def _init_data() -> str:
    fields = {
        "auth_date": str(int(NOW.timestamp())),
        "query_id": "stage4a-contract",
        "user": json.dumps({"id": 42}, separators=(",", ":")),
    }
    check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def _cookie(response) -> str:  # type: ignore[no-untyped-def]
    parsed = SimpleCookie()
    parsed.load(response.headers["Set-Cookie"])
    return parsed[CONTROL_SESSION_COOKIE].value


def test_control_contract_registry_is_exact_and_matches_task_registry() -> None:
    assert frozenset(CONTROL_TASK_CONTRACTS) == DEFAULT_CONTROL_TASK_IDS == frozenset(
        {"store-analytics-overview", "daily-morning-report"}
    )
    registry = default_task_registry()
    analytics = CONTROL_TASK_CONTRACTS["store-analytics-overview"]
    daily = CONTROL_TASK_CONTRACTS["daily-morning-report"]

    assert analytics.client_parameter_keys == frozenset(
        {"marketplace", "period_days", "region_id"}
    )
    assert analytics.server_parameter_keys == frozenset(
        {"wb_supplier_id", "ozon_seller_slug", "query_pack_id"}
    )
    assert analytics.result_renderer == "store_analytics_overview_v1"
    assert analytics.accepts_registered_task(registry.get(analytics.registry_task_id))
    assert analytics.accepts_client_params(
        {"marketplace": "wb", "period_days": 90, "region_id": "kazan"}
    )
    assert not analytics.accepts_client_params(
        {
            "marketplace": "wb",
            "period_days": 90,
            "region_id": "kazan",
            "wb_supplier_id": "override",
        }
    )
    assert daily.client_parameter_keys == frozenset()
    assert daily.server_parameter_keys == frozenset()
    assert daily.result_renderer == "daily_report_status_v1"
    assert daily.accepts_registered_task(registry.get(daily.registry_task_id))
    assert daily.accepts_client_params({})
    assert not daily.accepts_client_params({"refresh_preflight": False})


def test_daily_status_uses_minimal_renderer_and_never_exposes_raw_result(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        store = JobStore(tmp_path / "runtime.db")
        service = JobService(
            store=store,
            registry=default_task_registry(),
            data_dir=tmp_path / "data",
        )
        app = create_control_app(
            config=_config(),
            store=store,
            service=service,
            now_provider=lambda: NOW,
        )
        async with TestClient(TestServer(app)) as client:
            auth = await client.post(
                "/vital-shevron/api/v1/auth/telegram",
                json={"init_data": _init_data()},
            )
            body = await auth.json()
            headers = {
                "Cookie": f"{CONTROL_SESSION_COOKIE}={_cookie(auth)}",
                "X-CSRF-Token": body["csrf_token"],
                "Idempotency-Key": "daily-renderer-test",
            }
            submitted = await client.post(
                "/vital-shevron/api/v1/jobs",
                json={"task_id": "daily-morning-report", "params": {}},
                headers=headers,
            )
            public_job_id = (await submitted.json())["job_id"]
            internal = store.list_jobs(limit=1)[0]
            assert internal.task_id == "daily-morning-report"
            assert internal.status == "queued"
            assert internal.params == {}
            store.update_job_status(
                internal.job_id,
                "success",
                result={
                    "summary": {
                        "overall_status": "warning",
                        "api_token": "must-not-leak",
                    },
                    "artifacts": {"report": "/home/pavel/private/report.md"},
                    "raw": {"credentials": "must-not-leak"},
                },
            )
            status = await client.get(
                f"/vital-shevron/api/v1/jobs/{public_job_id}",
                headers={"Cookie": headers["Cookie"]},
            )
            payload = await status.json()
            assert payload["result_type"] == "daily_report_status_v1"
            assert payload["result"] == {"summary": {"overall_status": "warning"}}
            text = await status.text()
            assert "must-not-leak" not in text
            assert "/home/pavel" not in text
            assert "artifact" not in text

    asyncio.run(scenario())


def test_operations_summary_falls_back_to_data_unavailable_without_error_text(
    tmp_path: Path,
) -> None:
    class UnavailableSnapshotStore(JobStore):
        def get_control_operations_snapshot(self, **kwargs):  # type: ignore[no-untyped-def]
            del kwargs
            raise RuntimeError("Bearer must-not-leak")

    async def scenario() -> None:
        store = UnavailableSnapshotStore(tmp_path / "runtime.db")
        service = JobService(
            store=store,
            registry=default_task_registry(),
            data_dir=tmp_path / "data",
        )
        app = create_control_app(
            config=_config(),
            store=store,
            service=service,
            now_provider=lambda: NOW,
        )
        async with TestClient(TestServer(app)) as client:
            auth = await client.post(
                "/vital-shevron/api/v1/auth/telegram",
                json={"init_data": _init_data()},
            )
            response = await client.get(
                "/vital-shevron/api/v1/operations/summary",
                headers={
                    "Cookie": f"{CONTROL_SESSION_COOKIE}={_cookie(auth)}"
                },
            )
            assert response.status == 200
            payload = await response.json()
            assert payload["data_available"] is False
            assert payload["jobs"] == {"active": None, "terminal": None}
            assert payload["approvals"] == {
                "pending_review": None,
                "applying_unknown": None,
            }
            assert "must-not-leak" not in await response.text()

    asyncio.run(scenario())
