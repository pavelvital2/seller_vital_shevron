from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from seller_agent.reports.writer import ensure_dir, write_json


APPLIED_VERIFIED_STATUS = "owner_approved_applied_verified"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_text(value: Any) -> str:
    text = str(value or "").strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text


def _identity_sku(payload: dict[str, Any]) -> str:
    identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
    return _normalize_text(
        identity.get("internal_sku")
        or identity.get("master_sku")
        or payload.get("internal_sku")
        or payload.get("master_sku")
    )


def _passport_path(data_dir: Path, sku: str) -> Path:
    return data_dir / "catalog" / "master_passport" / "approved" / f"{sku}.json"


def _audit_paths_by_sku(data_dir: Path, skus: set[str]) -> dict[str, list[Path]]:
    found: dict[str, list[Path]] = {sku: [] for sku in skus}
    root = data_dir / "catalog" / "card_audits"
    if not root.exists():
        return found
    for path in root.glob("**/audit.json"):
        try:
            payload = _read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        sku = _identity_sku(payload)
        if sku in skus:
            found.setdefault(sku, []).append(path)
    return found


def _append_status_history(owner_review: dict[str, Any], event: dict[str, str]) -> None:
    history = owner_review.setdefault("status_history", [])
    if not isinstance(history, list):
        owner_review["status_history"] = history = []
    run_id = event.get("run_id")
    if run_id and any(isinstance(item, dict) and item.get("run_id") == run_id for item in history):
        return
    history.append(event)


def _sync_marketplace_apply(
    container: dict[str, Any],
    *,
    run_id: str,
    verified_at: str,
    summary_path: str,
    report_path: str,
    post_verify_run_id: str,
    content_update_run_id: str,
    seller_sku_update_run_id: str,
    catalog_sync_run_id: str,
) -> None:
    apply_state = container.setdefault("marketplace_apply", {})
    if not isinstance(apply_state, dict):
        container["marketplace_apply"] = apply_state = {}
    apply_state.update(
        {
            "status": "applied_verified",
            "batch_apply_run_id": run_id,
            "verified_at": verified_at,
            "summary_path": summary_path,
            "report_path": report_path,
        }
    )
    if post_verify_run_id:
        apply_state["post_verify_run_id"] = post_verify_run_id
    if content_update_run_id:
        apply_state["content_update_run_id"] = content_update_run_id
    if seller_sku_update_run_id:
        apply_state["seller_sku_update_run_id"] = seller_sku_update_run_id
    if catalog_sync_run_id:
        apply_state["catalog_sync_run_id"] = catalog_sync_run_id


def sync_card_apply_status(
    *,
    data_dir: Path,
    internal_skus: list[str],
    run_id: str,
    summary_path: str = "",
    report_path: str = "",
    post_verify_run_id: str = "",
    content_update_run_id: str = "",
    seller_sku_update_run_id: str = "",
    catalog_sync_run_id: str = "",
    verified_at: str | None = None,
) -> dict[str, Any]:
    """Close Layer 2/Layer 3 card states after successful apply -> verify."""

    skus = [sku.strip() for sku in internal_skus if sku and sku.strip()]
    verified_at = verified_at or datetime.now().isoformat(timespec="seconds")
    audit_paths = _audit_paths_by_sku(data_dir, set(skus))
    updated_passports = 0
    updated_audits = 0
    missing_passports: list[str] = []
    missing_audits: list[str] = []

    for sku in skus:
        passport_path = _passport_path(data_dir, sku)
        if not passport_path.exists():
            missing_passports.append(sku)
        else:
            passport = _read_json(passport_path)
            if isinstance(passport, dict):
                approval = passport.setdefault("approval", {})
                if not isinstance(approval, dict):
                    passport["approval"] = approval = {}
                approval["status"] = APPLIED_VERIFIED_STATUS
                approval["verified_at"] = verified_at
                approval["verify_run_id"] = post_verify_run_id
                _sync_marketplace_apply(
                    approval,
                    run_id=run_id,
                    verified_at=verified_at,
                    summary_path=summary_path,
                    report_path=report_path,
                    post_verify_run_id=post_verify_run_id,
                    content_update_run_id=content_update_run_id,
                    seller_sku_update_run_id=seller_sku_update_run_id,
                    catalog_sync_run_id=catalog_sync_run_id,
                )
                _append_status_history(
                    approval,
                    {
                        "status": APPLIED_VERIFIED_STATUS,
                        "run_id": run_id,
                        "verified_at": verified_at,
                        "source": "card_status_sync",
                    },
                )
                write_json(passport_path, passport)
                updated_passports += 1

        sku_audit_paths = audit_paths.get(sku) or []
        if not sku_audit_paths:
            missing_audits.append(sku)
        for audit_path in sku_audit_paths:
            audit = _read_json(audit_path)
            if not isinstance(audit, dict):
                continue
            owner_review = audit.setdefault("owner_review", {})
            if not isinstance(owner_review, dict):
                audit["owner_review"] = owner_review = {}
            owner_review.update(
                {
                    "status": APPLIED_VERIFIED_STATUS,
                    "awaiting": "closed",
                    "apply_status": "applied_verified",
                    "queue_status": "closed_do_not_resubmit",
                    "verified_at": verified_at,
                    "verify_run_id": post_verify_run_id,
                    "final_state_fixed_at": verified_at,
                    "batch_apply_run_id": run_id,
                }
            )
            _sync_marketplace_apply(
                audit,
                run_id=run_id,
                verified_at=verified_at,
                summary_path=summary_path,
                report_path=report_path,
                post_verify_run_id=post_verify_run_id,
                content_update_run_id=content_update_run_id,
                seller_sku_update_run_id=seller_sku_update_run_id,
                catalog_sync_run_id=catalog_sync_run_id,
            )
            _append_status_history(
                owner_review,
                {
                    "status": APPLIED_VERIFIED_STATUS,
                    "run_id": run_id,
                    "verified_at": verified_at,
                    "source": "card_status_sync",
                },
            )
            write_json(audit_path, audit)
            updated_audits += 1

    snapshot = build_card_status_snapshot(data_dir=data_dir, generated_at=verified_at)
    snapshot["last_sync"] = {
        "run_id": run_id,
        "internal_skus": skus,
        "updated_passports": updated_passports,
        "updated_audits": updated_audits,
        "missing_passports": missing_passports,
        "missing_audits": missing_audits,
    }
    snapshot_path = write_card_status_snapshot(data_dir=data_dir, snapshot=snapshot)
    return {
        "status": "ok" if not missing_passports else "warning",
        "updated_passports": updated_passports,
        "updated_audits": updated_audits,
        "missing_passports": missing_passports,
        "missing_audits": missing_audits,
        "snapshot_path": str(snapshot_path),
    }


def build_card_status_snapshot(*, data_dir: Path, generated_at: str | None = None) -> dict[str, Any]:
    generated_at = generated_at or datetime.now().isoformat(timespec="seconds")
    approved_root = data_dir / "catalog" / "master_passport" / "approved"
    passports = sorted(approved_root.glob("*.json")) if approved_root.exists() else []
    approval_statuses: Counter[str] = Counter()
    marketplace_apply_statuses: Counter[str] = Counter()
    not_applied_skus: list[str] = []
    for path in passports:
        try:
            payload = _read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        sku = _identity_sku(payload) or path.stem
        approval = payload.get("approval") if isinstance(payload.get("approval"), dict) else {}
        marketplace_apply = approval.get("marketplace_apply") if isinstance(approval.get("marketplace_apply"), dict) else {}
        approval_status = _normalize_text(approval.get("status") or "unknown")
        apply_status = _normalize_text(marketplace_apply.get("status") or "unknown")
        approval_statuses[approval_status] += 1
        marketplace_apply_statuses[apply_status] += 1
        if apply_status != "applied_verified":
            not_applied_skus.append(sku)
    return {
        "generated_at": generated_at,
        "total_passports": len(passports),
        "approval_status_counts": dict(sorted(approval_statuses.items())),
        "marketplace_apply_status_counts": dict(sorted(marketplace_apply_statuses.items())),
        "not_applied_skus": sorted(not_applied_skus),
    }


def write_card_status_snapshot(*, data_dir: Path, snapshot: dict[str, Any] | None = None) -> Path:
    snapshot = snapshot or build_card_status_snapshot(data_dir=data_dir)
    path = data_dir / "catalog" / "card_status" / "latest.json"
    ensure_dir(path.parent)
    write_json(path, snapshot)
    return path
