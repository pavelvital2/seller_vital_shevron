from __future__ import annotations

from pathlib import Path
from typing import Any


SUPPORTED_STAGES = {"triage", "prepare-approved", "apply", "verify", "cleanup"}


def run_ozon_messenger_workflow(
    *,
    data_dir: Path = Path("data"),
    stage: str,
    approved_path: Path | None = None,
    run_id: str | None = None,
    confirmed_by_user: bool = False,
) -> dict[str, Any]:
    if stage not in SUPPORTED_STAGES:
        return {
            "overall_status": "blocked",
            "blocked_reason": "unknown_stage",
            "stage": stage,
            "supported_stages": sorted(SUPPORTED_STAGES),
        }

    checks = _preflight(stage=stage, approved_path=approved_path, confirmed_by_user=confirmed_by_user)
    return {
        "run_id": run_id or f"ozon_messenger_workflow_{stage}",
        "overall_status": "blocked",
        "blocked_reason": "workflow_adapter_not_implemented",
        "stage": stage,
        "data_dir": str(data_dir),
        "preflight": checks,
        "next_step": (
            "Реализовать task-runner adapters поверх уже подтвержденных маршрутов: "
            "API /v3/chat/list и /v3/chat/history для triage/verify, approved package "
            "для apply, /v2/chat/read для NotificationUser и LK/CDP fallback только "
            "для подтвержденных customer replies или закрытия хвостов."
        ),
    }


def _preflight(*, stage: str, approved_path: Path | None, confirmed_by_user: bool) -> dict[str, Any]:
    checks: dict[str, Any] = {
        "stage": stage,
        "confirmed_by_user": confirmed_by_user,
        "approved_path_required": stage in {"apply", "cleanup"},
    }
    if stage in {"apply", "cleanup"}:
        checks["has_confirmation"] = confirmed_by_user
        checks["has_approved_path"] = approved_path is not None
        checks["approved_path_exists"] = bool(approved_path and approved_path.exists())
        if approved_path:
            checks["approved_path"] = str(approved_path)
    return checks
