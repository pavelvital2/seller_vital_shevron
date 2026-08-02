from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from seller_agent.tasks.store_analytics_overview import (
    DEFAULT_QUERY_PACK_ID,
    REGIONS_BY_MARKETPLACE,
)

if TYPE_CHECKING:
    from seller_agent.control_plane.config import ControlPlaneConfig
    from seller_agent.tasks.registry import RegisteredTask


ClientParamsValidator = Callable[[dict[str, Any]], bool]
ServerParamsFactory = Callable[["ControlPlaneConfig"], dict[str, Any]]


@dataclass(frozen=True)
class ControlTaskContract:
    """Fail-closed server contract for one owner control-plane task."""

    registry_task_id: str
    label: str
    client_parameter_keys: frozenset[str]
    server_parameter_keys: frozenset[str]
    result_renderer: str
    client_params_validator: ClientParamsValidator
    server_params_factory: ServerParamsFactory
    require_enabled: bool = True
    require_read_only: bool = True

    def accepts_client_params(self, params: dict[str, Any]) -> bool:
        return (
            set(params) == self.client_parameter_keys
            and not self.client_parameter_keys.intersection(self.server_parameter_keys)
            and self.client_params_validator(params)
        )

    def server_params(self, config: "ControlPlaneConfig") -> dict[str, Any]:
        values = self.server_params_factory(config)
        if set(values) != self.server_parameter_keys:
            raise ValueError("control_contract_invalid")
        return values

    def accepts_registered_task(self, task: "RegisteredTask") -> bool:
        return (
            task.name == self.registry_task_id
            and (not self.require_enabled or task.enabled)
            and (not self.require_read_only or task.is_read_only)
            and not task.is_write
        )


def _analytics_client_params(params: dict[str, Any]) -> bool:
    marketplace = params.get("marketplace")
    period_days = params.get("period_days")
    region_id = params.get("region_id")
    return (
        isinstance(marketplace, str)
        and isinstance(period_days, int)
        and not isinstance(period_days, bool)
        and period_days in {7, 30, 90}
        and isinstance(region_id, str)
        and region_id in REGIONS_BY_MARKETPLACE.get(marketplace, frozenset())
    )


def _analytics_server_params(config: "ControlPlaneConfig") -> dict[str, Any]:
    return {
        "wb_supplier_id": config.wb_supplier_id,
        "ozon_seller_slug": config.ozon_seller_slug,
        "query_pack_id": DEFAULT_QUERY_PACK_ID,
    }


def _empty_client_params(params: dict[str, Any]) -> bool:
    return params == {}


def _empty_server_params(config: "ControlPlaneConfig") -> dict[str, Any]:
    del config
    return {}


CONTROL_TASK_CONTRACTS: dict[str, ControlTaskContract] = {
    "store-analytics-overview": ControlTaskContract(
        registry_task_id="store-analytics-overview",
        label="Аналитика магазина",
        client_parameter_keys=frozenset(
            {"marketplace", "period_days", "region_id"}
        ),
        server_parameter_keys=frozenset(
            {"wb_supplier_id", "ozon_seller_slug", "query_pack_id"}
        ),
        result_renderer="store_analytics_overview_v1",
        client_params_validator=_analytics_client_params,
        server_params_factory=_analytics_server_params,
    ),
    "daily-morning-report": ControlTaskContract(
        registry_task_id="daily-morning-report",
        label="Утренний отчёт",
        client_parameter_keys=frozenset(),
        server_parameter_keys=frozenset(),
        result_renderer="daily_report_status_v1",
        client_params_validator=_empty_client_params,
        server_params_factory=_empty_server_params,
    ),
}


def control_task_contract(task_id: str) -> ControlTaskContract | None:
    """Resolve only an exact contract ID; aliases and normalization are forbidden."""
    if not isinstance(task_id, str):
        return None
    return CONTROL_TASK_CONTRACTS.get(task_id)
