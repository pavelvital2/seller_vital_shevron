from __future__ import annotations

import inspect

from seller_agent.core.workflow_runner import default_workflow_handlers
from seller_agent.tasks.registry import default_task_registry


def test_store_analytics_overview_registry_contract_is_read_only_and_executable() -> None:
    registry = default_task_registry()
    task = registry.get("store-analytics-overview")
    handler = default_workflow_handlers()[task.name]

    assert task.enabled is True
    assert task.mode == "read_only"
    assert task.is_write is False
    assert task.requires_credentials is True
    assert task.marketplaces == ("ozon", "wb")
    assert task.parameter_schema["marketplace"] == {
        "type": "string",
        "enum": ["ozon", "wb"],
        "required": True,
    }
    assert task.parameter_schema["period_days"] == {
        "type": "integer",
        "enum": [7, 30, 90],
        "required": True,
    }
    assert task.parameter_schema["region_id"] == {
        "type": "string",
        "enum": ["moscow", "rostov-on-don", "novosibirsk", "kazan"],
        "required": True,
    }
    assert task.parameter_schema["query_pack_id"] == {
        "type": "string",
        "const": "shevron-core",
    }
    assert task.parameter_schema["wb_supplier_id"] == {
        "type": "string",
        "minLength": 1,
        "maxLength": 32,
    }
    assert task.parameter_schema["ozon_seller_slug"] == {
        "type": "string",
        "minLength": 1,
        "maxLength": 128,
    }
    assert {"overall_status", "run_id", "marketplace", "period_days", "region_id"} <= set(
        task.result_schema
    )
    assert callable(handler)
    assert len(inspect.signature(handler).parameters) == 4
    assert not [
        issue
        for issue in registry.policy_issues(handlers=default_workflow_handlers())
        if issue["task"] == task.name
    ]
