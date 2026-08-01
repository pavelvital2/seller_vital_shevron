from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from seller_agent.safety.approvals import canonical_checksum


APPROVAL_PACKAGE_SCHEMA = "seller.approval_package.v1"
APPROVAL_TRANSPORT_ONLY_PARAMS = frozenset(
    {
        "approval_id",
        "approval_checksum",
        "confirmed_by_user",
    }
)


def build_approval_package(
    *,
    approval_id: str,
    task_id: str,
    source_plan_task: str,
    verify_task: str,
    source_kind: str,
    source_ref: str,
    apply_params: dict[str, Any],
    marketplaces: list[str] | tuple[str, ...] = (),
    actions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    package = {
        "schema": APPROVAL_PACKAGE_SCHEMA,
        "approval_id": approval_id,
        "task_id": task_id,
        "source_plan_task": source_plan_task,
        "verify_task": verify_task,
        "source_kind": source_kind,
        "source_ref": source_ref,
        "marketplaces": list(marketplaces),
        "apply_params": normalize_business_params(apply_params),
        "actions": list(actions or []),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    package["approval_checksum"] = _approval_package_checksum(package)
    return package


def verify_approval_package(package: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if package.get("schema") != APPROVAL_PACKAGE_SCHEMA:
        issues.append("approval_package_schema_invalid")
    for key in (
        "approval_id",
        "task_id",
        "source_plan_task",
        "verify_task",
        "source_kind",
        "source_ref",
        "created_at",
        "approval_checksum",
    ):
        if not package.get(key):
            issues.append(f"approval_package_missing_{key}")
    apply_params = package.get("apply_params")
    if not isinstance(apply_params, dict):
        issues.append("approval_package_apply_params_invalid")
    elif APPROVAL_TRANSPORT_ONLY_PARAMS.intersection(apply_params):
        issues.append("approval_package_transport_params_invalid")
    marketplaces = package.get("marketplaces")
    if not isinstance(marketplaces, list) or not all(
        isinstance(item, str) and item.strip() for item in marketplaces
    ):
        issues.append("approval_package_marketplaces_invalid")
    expected = str(package.get("approval_checksum") or "")
    actual = _approval_package_checksum(package)
    if expected and expected != actual:
        issues.append("approval_package_checksum_invalid")
    return list(dict.fromkeys(issues))


def verify_approval_package_linkage(
    package: dict[str, Any],
    *,
    record_approval_id: str,
    record_checksum: str,
    task_id: str,
    source_plan_task: str,
    verify_task: str,
    marketplaces: list[str] | tuple[str, ...],
) -> list[str]:
    """Validate immutable package contents and their current registry linkage."""
    issues = list(verify_approval_package(package))
    if package.get("approval_id") != record_approval_id:
        issues.append("approval_package_id_mismatch")
    if package.get("approval_checksum") != record_checksum:
        issues.append("approval_record_checksum_mismatch")
    if package.get("task_id") != task_id:
        issues.append("approval_task_mismatch")
    if package.get("source_plan_task") != source_plan_task:
        issues.append("approval_source_plan_task_mismatch")
    if package.get("verify_task") != verify_task:
        issues.append("approval_verify_task_mismatch")
    package_marketplaces = package.get("marketplaces")
    if isinstance(package_marketplaces, list) and sorted(package_marketplaces) != sorted(
        marketplaces
    ):
        issues.append("approval_marketplaces_mismatch")
    return list(dict.fromkeys(issues))


def _approval_package_checksum(package: dict[str, Any]) -> str:
    checksum_payload = {
        key: value for key, value in package.items() if key != "approval_checksum"
    }
    return f"sha256:{canonical_checksum(checksum_payload)}"


def normalize_business_params(params: dict[str, Any]) -> dict[str, Any]:
    """Return the canonical JSON-safe business payload, excluding transport gates."""
    business = {
        key: value
        for key, value in params.items()
        if key not in APPROVAL_TRANSPORT_ONLY_PARAMS
    }
    normalized = json.loads(
        json.dumps(
            business,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    )
    if not isinstance(normalized, dict):
        raise ValueError("business params must normalize to an object")
    return normalized
