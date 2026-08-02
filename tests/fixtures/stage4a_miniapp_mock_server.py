from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiohttp import web


API_PREFIX = "/vital-shevron/api/v1"
STATIC_DIR = Path(__file__).resolve().parents[2] / "src/seller_agent/control_plane/static"
JOB_POLLS: dict[str, int] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


async def _session(request: web.Request) -> web.Response:
    del request
    return web.json_response(
        {"ok": True, "csrf_token": "stage4a-mock-csrf", "expires_at": 4102444800}
    )


async def _operations(request: web.Request) -> web.Response:
    del request
    observed_at = _now()
    return web.json_response(
        {
            "data_available": True,
            "observed_at": observed_at,
            "jobs": {"active": 2, "terminal": 18},
            "approvals": {"pending_review": 3, "applying_unknown": 1},
            "last_control_job": {
                "job_id": "cpj_stage4a_mock_daily_report",
                "task_id": "daily-morning-report",
                "status": "success",
                "created_at": observed_at,
                "updated_at": observed_at,
            },
        }
    )


async def _jobs(request: web.Request) -> web.Response:
    del request
    timestamp = _now()
    return web.json_response(
        {
            "data_available": True,
            "limit": 20,
            "jobs": [
                {
                    "job_id": "cpj_stage4a_mock_daily_report",
                    "task_id": "daily-morning-report",
                    "status": "success",
                    "created_at": timestamp,
                    "updated_at": timestamp,
                    "finished_at": timestamp,
                },
                {
                    "job_id": "cpj_stage4a_mock_analytics_overview",
                    "task_id": "store-analytics-overview",
                    "status": "running",
                    "created_at": timestamp,
                    "updated_at": timestamp,
                    "finished_at": "",
                },
            ],
        }
    )


async def _approvals(request: web.Request) -> web.Response:
    del request
    timestamp = _now()
    return web.json_response(
        {
            "data_available": True,
            "counts": {"pending_review": 3, "applying_unknown": 1},
            "approvals": [
                {
                    "task_id": "wb-actions-discount-plan",
                    "label": "WB actions discount plan",
                    "status": "pending_review",
                    "created_at": timestamp,
                    "updated_at": timestamp,
                    "requires_reconciliation": False,
                },
                {
                    "task_id": "ozon-actions-optimizer-plan",
                    "label": "Ozon actions optimizer with intentionally long safe label for responsive wrapping",
                    "status": "applying_unknown",
                    "created_at": timestamp,
                    "updated_at": timestamp,
                    "requires_reconciliation": True,
                },
            ],
        }
    )


async def _submit(request: web.Request) -> web.Response:
    payload: dict[str, Any] = await request.json()
    task_id = str(payload.get("task_id") or "")
    public_job_id = (
        "cpj_stage4a_mock_daily_submit"
        if task_id == "daily-morning-report"
        else "cpj_stage4a_mock_analytics_submit"
    )
    JOB_POLLS[public_job_id] = 0
    return web.json_response(
        {"created": True, "job_id": public_job_id, "status": "queued"},
        status=202,
    )


async def _job_status(request: web.Request) -> web.Response:
    public_job_id = request.match_info["public_job_id"]
    polls = JOB_POLLS.get(public_job_id, 0)
    JOB_POLLS[public_job_id] = polls + 1
    if polls == 0:
        return web.json_response(
            {
                "job_id": public_job_id,
                "task_id": (
                    "daily-morning-report"
                    if "daily" in public_job_id
                    else "store-analytics-overview"
                ),
                "status": "queued",
                "created_at": _now(),
                "updated_at": _now(),
                "finished_at": "",
                "result_type": "status_only_v1",
            }
        )
    if "daily" in public_job_id:
        return web.json_response(
            {
                "job_id": public_job_id,
                "task_id": "daily-morning-report",
                "status": "success",
                "created_at": _now(),
                "updated_at": _now(),
                "finished_at": _now(),
                "result_type": "daily_report_status_v1",
                "result": {"summary": {"overall_status": "ok"}},
            }
        )
    return web.json_response(
        {
            "job_id": public_job_id,
            "task_id": "store-analytics-overview",
            "status": "success",
            "created_at": _now(),
            "updated_at": _now(),
            "finished_at": _now(),
            "result_type": "store_analytics_overview_v1",
            "result": {
                "summary": {
                    "overall_status": "ok",
                    "marketplace": "ozon",
                    "period_days": 30,
                    "region_id": "moscow",
                    "sales": {
                        "data_available": True,
                        "current": {
                            "orders": 24,
                            "revenue": 58400,
                            "returns": 1,
                            "net_before_cogs": 42100,
                        },
                        "changes": {"orders": 3, "revenue": 7300, "returns": 0},
                        "margin": {"data_available": False},
                    },
                    "stocks": {"data_available": True, "total_units": 318},
                    "parser_visibility": {
                        "data_available": True,
                        "region_id": "moscow",
                        "metrics": {
                            "visible_products": 17,
                            "query_count": 42,
                            "total_rows": 126,
                        },
                        "comparison": {
                            "available": True,
                            "previous_date": "2026-08-01",
                            "current_date": "2026-08-02",
                        },
                        "freshness": {"state": "fresh", "observed_at": _now()},
                    },
                    "problems": [],
                    "next_actions": [],
                }
            },
        }
    )


async def _index(request: web.Request) -> web.FileResponse:
    del request
    return web.FileResponse(STATIC_DIR / "index.html")


async def _favicon(request: web.Request) -> web.Response:
    del request
    return web.Response(status=204)


def _asset(name: str):  # type: ignore[no-untyped-def]
    async def handler(request: web.Request) -> web.FileResponse:
        del request
        return web.FileResponse(STATIC_DIR / name)

    return handler


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get(f"{API_PREFIX}/session", _session)
    app.router.add_get(f"{API_PREFIX}/operations/summary", _operations)
    app.router.add_get(f"{API_PREFIX}/jobs", _jobs)
    app.router.add_get(f"{API_PREFIX}/approvals", _approvals)
    app.router.add_post(f"{API_PREFIX}/jobs", _submit)
    app.router.add_get(f"{API_PREFIX}/jobs/{{public_job_id}}", _job_status)
    app.router.add_get("/vital-shevron/", _index)
    app.router.add_get("/vital-shevron/app.css", _asset("app.css"))
    app.router.add_get("/vital-shevron/app.js", _asset("app.js"))
    app.router.add_get("/vital-shevron/icons.svg", _asset("icons.svg"))
    app.router.add_get("/favicon.ico", _favicon)
    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18092)
    args = parser.parse_args()
    web.run_app(create_app(), host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
