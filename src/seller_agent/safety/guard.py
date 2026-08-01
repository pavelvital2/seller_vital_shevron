from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from seller_agent.core.job_models import ApprovalRecord, JobRecord
from seller_agent.safety.approval_package import (
    normalize_business_params,
    verify_approval_package,
)
from seller_agent.tasks.registry import RegisteredTask


APPROVAL_PACKAGE_MAX_AGE = timedelta(hours=24)
APPROVAL_PACKAGE_MAX_CLOCK_SKEW = timedelta(minutes=5)


@dataclass(frozen=True)
class SafetyDecision:
    allowed: bool
    issues: tuple[str, ...] = ()


class SafetyGuard:
    """Central pre-workflow gate for every marketplace apply job."""

    def validate_approval_package(
        self,
        *,
        task: RegisteredTask,
        approval: ApprovalRecord,
    ) -> SafetyDecision:
        issues = list(verify_approval_package(approval.data))
        if approval.data.get("approval_id") != approval.approval_id:
            issues.append("approval_package_id_mismatch")
        if approval.data.get("approval_checksum") != approval.checksum:
            issues.append("approval_record_checksum_mismatch")
        if approval.data.get("task_id") != task.name:
            issues.append("approval_task_mismatch")
        if approval.data.get("verify_task") != task.verify_task:
            issues.append("approval_verify_task_mismatch")
        if approval.data.get("source_plan_task") != task.source_plan_task:
            issues.append("approval_source_plan_task_mismatch")
        package_marketplaces = approval.data.get("marketplaces")
        if isinstance(package_marketplaces, list) and sorted(package_marketplaces) != sorted(
            task.marketplaces
        ):
            issues.append("approval_marketplaces_mismatch")
        try:
            created_at = datetime.fromisoformat(
                str(approval.data.get("created_at") or "").replace("Z", "+00:00")
            )
            if created_at.tzinfo is None:
                raise ValueError("approval package timestamp must include timezone")
            now = datetime.now(timezone.utc)
            created_at_utc = created_at.astimezone(timezone.utc)
            if created_at_utc > now + APPROVAL_PACKAGE_MAX_CLOCK_SKEW:
                issues.append("approval_package_created_at_future")
            elif now - created_at_utc > APPROVAL_PACKAGE_MAX_AGE:
                issues.append("approval_package_stale")
        except (TypeError, ValueError):
            issues.append("approval_package_created_at_invalid")
        return SafetyDecision(allowed=not issues, issues=tuple(dict.fromkeys(issues)))

    def validate_apply(self, *, task: RegisteredTask, job: JobRecord, approval: ApprovalRecord | None) -> SafetyDecision:
        issues: list[str] = []
        if not task.is_write or task.mode != "apply":
            issues.append("task_is_not_apply")
        if not _confirmed(job.params):
            issues.append("owner_confirmation_missing")
        if not task.source_plan_task:
            issues.append("source_plan_task_missing")
        if not task.verify_task:
            issues.append("verify_task_missing")
        if not task.lock_keys:
            issues.append("resource_locks_missing")
        if task.requires_mapping and not any(job.params.get(key) for key in ("mapping_verified", "internal_skus", "plan_run_id", "source_run_id", "approved_path")):
            issues.append("mapping_evidence_missing")
        approval_id = str(job.params.get("approval_id") or "")
        checksum = str(job.params.get("approval_checksum") or "")
        if not approval_id:
            issues.append("approval_id_missing")
        if not checksum:
            issues.append("approval_checksum_missing")
        if approval is None:
            issues.append("approval_record_missing")
        else:
            if approval.status != "approved":
                issues.append("approval_status_invalid")
            if checksum and approval.checksum != checksum:
                issues.append("approval_record_checksum_mismatch")
            issues.extend(
                self.validate_approval_package(task=task, approval=approval).issues
            )
            expected_params = approval.data.get("apply_params")
            if not isinstance(expected_params, dict) or normalize_business_params(
                job.params
            ) != normalize_business_params(expected_params):
                issues.append("approval_params_mismatch")
        return SafetyDecision(allowed=not issues, issues=tuple(dict.fromkeys(issues)))


def _confirmed(params: dict[str, object]) -> bool:
    value = params.get("confirmed_by_user", False)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)
