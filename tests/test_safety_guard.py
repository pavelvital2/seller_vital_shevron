from seller_agent.core.job_models import ApprovalRecord, JobRecord
from seller_agent.safety.approval_package import build_approval_package, verify_approval_package
from seller_agent.safety.guard import SafetyGuard
from seller_agent.tasks.registry import default_task_registry


def test_unified_approval_package_is_self_verifying() -> None:
    package = build_approval_package(approval_id="approval-1", task_id="ozon-elastic-apply", source_plan_task="ozon-elastic-plan", verify_task="ozon-elastic-verify", source_kind="plan_run_id", source_ref="plan-1", apply_params={"plan_run_id": "plan-1", "confirmed_by_user": True}, marketplaces=["ozon"])
    assert package["schema"] == "seller.approval_package.v1"
    assert package["approval_checksum"].startswith("sha256:")
    assert verify_approval_package(package) == []


def test_safety_guard_blocks_tampered_unified_package() -> None:
    task = default_task_registry().get("ozon-elastic-apply")
    package = build_approval_package(approval_id="approval-1", task_id=task.name, source_plan_task=task.source_plan_task, verify_task=task.verify_task, source_kind="plan_run_id", source_ref="plan-1", apply_params={"plan_run_id": "plan-1", "confirmed_by_user": True}, marketplaces=["ozon"])
    package["apply_params"]["plan_run_id"] = "tampered"
    job = JobRecord(job_id="job-1", task_id=task.name, status="queued", actor="owner", created_at="now", updated_at="now", params={"confirmed_by_user": True, "approval_id": "approval-1", "approval_checksum": package["approval_checksum"]})
    approval = ApprovalRecord(approval_id="approval-1", status="approved", source_job_id="job-1", created_at="now", updated_at="now", checksum=package["approval_checksum"], data=package)
    decision = SafetyGuard().validate_apply(task=task, job=job, approval=approval)
    assert decision.allowed is False
    assert "approval_package_checksum_invalid" in decision.issues
