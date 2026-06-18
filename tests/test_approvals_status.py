from __future__ import annotations

import json
from pathlib import Path

import pytest

from takterra_agent.safety.approvals import approval_identity_from_path, mark_approved_applied
from takterra_agent.tasks.approvals import run_approvals_close, run_approvals_status


def test_approvals_status_links_pending_to_approved_package(tmp_path: Path) -> None:
    pending_id = "reviews_questions_test_pending"
    _write_json(
        tmp_path / "pending" / pending_id / "manifest.json",
        {
            "pending_id": pending_id,
            "run_id": "reviews_questions_test",
            "status": "pending_owner_review",
            "created_at": "2026-06-18T10:00:00",
        },
    )
    _write_json(
        tmp_path / "approved" / "reviews_questions_test_approved" / "approved_apply_plan.json",
        {
            "schema_version": "approval-package/v1",
            "package_type": "reviews_questions",
            "status": "approved",
            "approved_id": "reviews_questions_test_approved",
            "pending_id": pending_id,
            "source_run_id": "reviews_questions_test",
            "created_at": "2026-06-18T10:10:00",
            "selected_actions_count": 1,
            "actions": [],
        },
    )

    result = run_approvals_status(data_dir=tmp_path, include_closed=True)
    rows = {(row["kind"], row["id"]): row for row in result["rows"]}

    assert rows[("pending", pending_id)]["lifecycle_status"] == "approved"
    assert rows[("pending", pending_id)]["linked_approved_ids"] == ["reviews_questions_test_approved"]
    assert rows[("approved", "reviews_questions_test_approved")]["lifecycle_status"] == "approved"
    assert result["status_counts"] == {"approved": 2}


def test_approvals_status_detects_applied_package_by_marker(tmp_path: Path) -> None:
    package_path = tmp_path / "approved" / "reviews_questions_test_approved" / "approved_apply_plan.json"
    _write_json(
        package_path,
        {
            "schema_version": "approval-package/v1",
            "package_type": "reviews_questions",
            "status": "approved",
            "approved_id": "reviews_questions_test_approved",
            "pending_id": "reviews_questions_test_pending",
            "source_run_id": "reviews_questions_test",
            "created_at": "2026-06-18T10:10:00",
            "actions": [],
        },
    )
    approved_identity = approval_identity_from_path(package_path)
    mark_approved_applied(
        data_dir=tmp_path,
        approved_id=approved_identity,
        apply_run_id="reviews_questions_apply_test",
        task="reviews-questions-apply",
        status="ok",
    )

    result = run_approvals_status(data_dir=tmp_path, kind="approved", include_closed=True)

    assert result["rows"][0]["id"] == "reviews_questions_test_approved"
    assert result["rows"][0]["lifecycle_status"] == "applied"
    assert result["rows"][0]["apply_run_id"] == "reviews_questions_apply_test"


def test_approvals_close_requires_force_for_unapplied_approved_package(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "approved" / "reviews_questions_test_approved" / "approved_apply_plan.json",
        {
            "schema_version": "approval-package/v1",
            "package_type": "reviews_questions",
            "status": "approved",
            "approved_id": "reviews_questions_test_approved",
            "created_at": "2026-06-18T10:10:00",
            "actions": [],
        },
    )

    with pytest.raises(RuntimeError, match="requires --force"):
        run_approvals_close(
            data_dir=tmp_path,
            target_id="reviews_questions_test_approved",
            kind="approved",
        )

    result = run_approvals_close(
        data_dir=tmp_path,
        target_id="reviews_questions_test_approved",
        kind="approved",
        closed_by="owner-test",
        reason="superseded",
        force=True,
        run_id="approvals_close_test",
    )
    status = run_approvals_status(data_dir=tmp_path, kind="approved", include_closed=True)

    assert result["closed"] is True
    assert result["status_before"] == "approved"
    assert status["rows"][0]["lifecycle_status"] == "closed"
    assert Path(result["artifacts"]["close_marker"]).exists()
    assert Path(result["artifacts"]["manifest"]).exists()


def test_approvals_close_pending_package_without_force(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "pending" / "reviews_questions_test_pending" / "manifest.json",
        {
            "pending_id": "reviews_questions_test_pending",
            "run_id": "reviews_questions_test",
            "status": "pending_owner_review",
            "created_at": "2026-06-18T10:00:00",
        },
    )

    result = run_approvals_close(
        data_dir=tmp_path,
        target_id="reviews_questions_test_pending",
        kind="pending",
        closed_by="owner-test",
        reason="answered manually",
        run_id="approvals_close_pending_test",
    )
    status = run_approvals_status(data_dir=tmp_path, kind="pending", include_closed=True)

    assert result["target_kind"] == "pending"
    assert result["status_before"] == "pending_review"
    assert status["rows"][0]["lifecycle_status"] == "closed"


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
