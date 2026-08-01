from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from seller_agent.safety.approvals import canonical_checksum


APPROVAL_PACKAGE_SCHEMA = "seller.approval_package.v1"


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
        "apply_params": dict(apply_params),
        "actions": list(actions or []),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    checksum_payload = {key: value for key, value in package.items() if key != "created_at"}
    package["approval_checksum"] = f"sha256:{canonical_checksum(checksum_payload)}"
    return package


def verify_approval_package(package: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if package.get("schema") != APPROVAL_PACKAGE_SCHEMA:
        issues.append("approval_package_schema_invalid")
    for key in ("approval_id", "task_id", "source_kind", "source_ref", "approval_checksum"):
        if not package.get(key):
            issues.append(f"approval_package_missing_{key}")
    expected = str(package.get("approval_checksum") or "")
    checksum_payload = {key: value for key, value in package.items() if key not in {"created_at", "approval_checksum"}}
    actual = f"sha256:{canonical_checksum(checksum_payload)}"
    if expected and expected != actual:
        issues.append("approval_package_checksum_invalid")
    return issues
