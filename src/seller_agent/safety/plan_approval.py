from __future__ import annotations

from typing import Any


PLAN_APPROVAL_CANDIDATE_SCHEMA = "seller.plan_approval_candidate.v1"
PLAN_APPROVAL_SOURCE_FIELDS = frozenset({"plan_run_id", "source_run_id"})


def with_plan_approval_candidate(
    result: dict[str, Any],
    *,
    action_count: int,
    source_field: str,
) -> dict[str, Any]:
    """Attach planner-owned evidence used to decide whether owner review exists."""
    if type(action_count) is not int or action_count < 0:
        raise ValueError("plan approval action_count must be a non-negative integer")
    if source_field not in PLAN_APPROVAL_SOURCE_FIELDS:
        raise ValueError("plan approval source_field is invalid")
    source_ref = str(result.get("run_id") or "").strip()
    if not source_ref:
        raise ValueError("plan approval candidate requires result run_id")
    output = dict(result)
    output["approval_candidate"] = {
        "schema_version": PLAN_APPROVAL_CANDIDATE_SCHEMA,
        "action_count": action_count,
        "source_field": source_field,
        "source_ref": source_ref,
    }
    return output


def actionable_plan_source_ref(
    result: dict[str, Any],
    *,
    expected_source_field: str,
) -> str:
    """Return the exact source ref only for a valid, non-empty planner action set."""
    candidate = result.get("approval_candidate")
    if not isinstance(candidate, dict):
        return ""
    if candidate.get("schema_version") != PLAN_APPROVAL_CANDIDATE_SCHEMA:
        return ""
    if candidate.get("source_field") != expected_source_field:
        return ""
    action_count = candidate.get("action_count")
    if type(action_count) is not int or action_count <= 0:
        return ""
    source_ref = str(candidate.get("source_ref") or "").strip()
    result_run_id = str(result.get("run_id") or "").strip()
    if not source_ref or source_ref != result_run_id:
        return ""
    return source_ref
