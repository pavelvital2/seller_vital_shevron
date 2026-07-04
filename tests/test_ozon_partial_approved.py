from __future__ import annotations

from pathlib import Path
from typing import Any

from seller_agent.config import AppCredentials
from seller_agent.tasks.ozon_partial_approved import (
    run_ozon_partial_approved_diagnose,
    run_ozon_partial_approved_recovery_apply,
)
from seller_agent.tasks.registry import get_task_definition


def test_ozon_partial_approved_diagnose_builds_recovery_payload(tmp_path: Path) -> None:
    result = run_ozon_partial_approved_diagnose(
        credentials=AppCredentials(ozon_seller=None, ozon_performance=None, wb=None),
        data_dir=tmp_path / "data",
        run_id="ozon_partial_test",
        ozon_adapter=_FakeOzonPartialAdapter(),
    )

    assert result["overall_status"] == "warning"
    assert result["summary"]["partial_approved_count"] == 2
    assert result["summary"]["stale_visibility_rows"] == 1
    assert result["summary"]["active_error_rows"] == 1
    payload = Path(result["artifacts"]["recovery_request"]).read_text(encoding="utf-8")
    assert '"offer_id": "stale-1"' in payload
    assert '"offer_id": "active-1"' not in payload


def test_ozon_partial_approved_apply_uses_approved_payload_and_verifies(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    adapter = _FakeOzonPartialAdapter(clear_after_update=True)
    plan = run_ozon_partial_approved_diagnose(
        credentials=AppCredentials(ozon_seller=None, ozon_performance=None, wb=None),
        data_dir=data_dir,
        run_id="ozon_partial_plan",
        ozon_adapter=adapter,
    )

    result = run_ozon_partial_approved_recovery_apply(
        credentials=AppCredentials(ozon_seller=None, ozon_performance=None, wb=None),
        data_dir=data_dir,
        plan_run_id="ozon_partial_plan",
        run_id="ozon_partial_apply",
        confirmed_by_user=True,
        wait_seconds=0,
        poll_interval=1,
        ozon_adapter=adapter,
    )

    assert result["overall_status"] == "ok"
    assert result["summary"]["partial_approved_before"] == 2
    assert result["summary"]["ready_recovery_before"] == 1
    assert result["summary"]["partial_approved_after"] == 0
    assert adapter.updated_items == [
        {
            "offer_id": "stale-1",
            "attributes": [
                {
                    "id": 23536,
                    "complex_id": 0,
                    "values": [{"dictionary_value_id": 0, "value": "false"}],
                }
            ],
        }
    ]
    assert Path(plan["artifacts"]["recovery_request"]).exists()


def test_task_registry_contains_ozon_partial_approved_tasks() -> None:
    diagnose = get_task_definition("ozon-partial-approved-diagnose")
    apply = get_task_definition("apply-ozon-partial-approved-recovery")

    assert diagnose["mode"] == "dry_run"
    assert apply["mode"] == "apply"
    assert apply["requires_confirmation"] is True
    assert "apply_missing_source_plan_task" not in apply["policy_issues"]


class _FakeOzonPartialAdapter:
    def __init__(self, *, clear_after_update: bool = False) -> None:
        self.clear_after_update = clear_after_update
        self.updated_items: list[dict[str, Any]] = []

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert path == "/v3/product/list"
        if self.clear_after_update and self.updated_items:
            return {"result": {"items": []}}
        return {
            "result": {
                "items": [
                    {"offer_id": "stale-1", "product_id": 101},
                    {"offer_id": "active-1", "product_id": 102},
                ]
            }
        }

    def fetch_product_info(self, product_ids: list[str]) -> list[dict[str, Any]]:
        if not product_ids:
            return []
        if self.clear_after_update and self.updated_items:
            return []
        rows = [
            {
                "id": 101,
                "offer_id": "stale-1",
                "statuses": {
                    "status_name": "Продается",
                    "validation_status": "success",
                    "item_errors": [],
                },
            },
            {
                "id": 102,
                "offer_id": "active-1",
                "statuses": {
                    "status_name": "Не обновлен",
                    "validation_status": "fail",
                    "item_errors": [{"code": "DESCRIPTION_DECLINE"}],
                },
            },
        ]
        return [row for row in rows if str(row["id"]) in set(product_ids)]

    def fetch_product_attributes(self, offer_ids: list[str]) -> list[dict[str, Any]]:
        return [
            {
                "id": 101,
                "offer_id": "stale-1",
                "attributes": [
                    {
                        "id": 23536,
                        "values": [{"dictionary_value_id": 0, "value": "false"}],
                    }
                ],
            }
            for offer_id in offer_ids
            if offer_id == "stale-1"
        ]

    def update_product_attributes(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        self.updated_items = items
        return {"task_id": 123}

    def fetch_product_import_info(self, task_id: int) -> dict[str, Any]:
        assert task_id == 123
        return {"result": {"items": [{"status": "imported"}]}}
