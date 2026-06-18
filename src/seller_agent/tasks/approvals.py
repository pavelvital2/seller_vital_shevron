from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from seller_agent.core.run_manifest import read_run_index, write_summary_run_manifest
from seller_agent.reports.writer import ensure_dir, write_json
from seller_agent.safety.approvals import (
    approval_identity_from_path,
    close_marker_for,
)


def run_approvals_status(
    *,
    data_dir: Path = Path("data"),
    target_id: str | None = None,
    kind: str = "all",
    include_closed: bool = False,
    limit: int = 50,
) -> dict[str, Any]:
    if kind not in {"all", "pending", "approved"}:
        raise ValueError(f"unsupported approvals status kind: {kind}")
    rows = _approval_rows(data_dir)
    if kind != "all":
        rows = [row for row in rows if row["kind"] == kind]
    if target_id:
        rows = [row for row in rows if row["id"] == target_id or target_id in row.get("identity_candidates", [])]
    if not include_closed:
        rows = [row for row in rows if row["lifecycle_status"] != "closed"]
    rows.sort(key=_approval_sort_key, reverse=True)
    limited_rows = rows[: max(limit, 0)]
    status_counts = Counter(row["lifecycle_status"] for row in rows)
    kind_counts = Counter(row["kind"] for row in rows)
    return {
        "overall_status": "ok",
        "rows_count": len(rows),
        "returned_rows_count": len(limited_rows),
        "status_counts": dict(sorted(status_counts.items())),
        "kind_counts": dict(sorted(kind_counts.items())),
        "rows": [_public_row(row) for row in limited_rows],
        "artifacts": {
            "pending_dir": str(data_dir / "pending"),
            "approved_dir": str(data_dir / "approved"),
            "runs_index": str(data_dir / "runs" / "index.jsonl"),
        },
    }


def run_approvals_close(
    *,
    data_dir: Path = Path("data"),
    target_id: str,
    kind: str = "auto",
    closed_by: str = "owner",
    reason: str = "",
    force: bool = False,
    run_id: str | None = None,
) -> dict[str, Any]:
    if kind not in {"auto", "pending", "approved"}:
        raise ValueError(f"unsupported approvals close kind: {kind}")
    rows = _approval_rows(data_dir)
    matches = [
        row
        for row in rows
        if row["id"] == target_id or target_id in row.get("identity_candidates", [])
    ]
    if kind != "auto":
        matches = [row for row in matches if row["kind"] == kind]
    if not matches:
        raise FileNotFoundError(f"Approval target not found: {target_id}")
    unique_keys = {(row["kind"], row["id"]) for row in matches}
    if len(unique_keys) > 1 and kind == "auto":
        raise RuntimeError(f"Approval target is ambiguous, pass --kind pending|approved: {target_id}")

    row = sorted(matches, key=_approval_sort_key, reverse=True)[0]
    if row["kind"] == "approved" and row["lifecycle_status"] == "approved" and not force:
        raise RuntimeError("Closing an unapplied approved package requires --force")
    if row["lifecycle_status"] == "closed" and not force:
        raise RuntimeError(f"Approval target is already closed: {target_id}")

    started_at = datetime.now().astimezone()
    run_id = run_id or f"approvals_close_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.date().isoformat() / run_id)
    marker_path = close_marker_for(data_dir=data_dir, target_kind=row["kind"], target_id=row["id"])
    marker = {
        "schema_version": "approval-close/v1",
        "target_kind": row["kind"],
        "target_id": row["id"],
        "target_path": row["path"],
        "status_before": row["lifecycle_status"],
        "closed_at": started_at.isoformat(timespec="seconds"),
        "closed_by": closed_by,
        "reason": reason,
        "force": force,
        "identity_candidates": row.get("identity_candidates", []),
    }
    write_json(marker_path, marker)
    artifacts = {
        "run_dir": str(run_dir),
        "close_marker": str(marker_path),
        "target_path": str(row["path"]),
        "run_manifest": str(run_dir / "manifest.json"),
    }
    summary = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "overall_status": "ok",
        "mode": "maintenance",
        "target_kind": row["kind"],
        "target_id": row["id"],
        "status_before": row["lifecycle_status"],
        "closed_by": closed_by,
        "reason": reason,
        "artifacts": artifacts,
    }
    write_json(run_dir / "summary.json", summary)
    summary["artifacts"]["summary"] = str(run_dir / "summary.json")
    write_json(run_dir / "summary.json", summary)
    manifest_paths = write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=summary,
        task="approvals-close",
        mode="maintenance",
        risk="low",
        marketplaces=[],
        inputs={
            "target_kind": row["kind"],
            "target_id": row["id"],
            "closed_by": closed_by,
            "force": force,
        },
        lifecycle_status="closed",
        closed=True,
    )
    return {
        "overall_status": "ok",
        "target_kind": row["kind"],
        "target_id": row["id"],
        "status_before": row["lifecycle_status"],
        "closed": True,
        "artifacts": {**summary["artifacts"], **manifest_paths},
    }


def _approval_rows(data_dir: Path) -> list[dict[str, Any]]:
    run_rows = read_run_index(data_dir)
    apply_rows = _apply_rows(run_rows)
    applied_markers = _applied_markers(data_dir)
    close_markers = _close_markers(data_dir)
    approved_rows = _approved_rows(
        data_dir=data_dir,
        run_rows=run_rows,
        apply_rows=apply_rows,
        applied_markers=applied_markers,
        close_markers=close_markers,
    )
    pending_rows = _pending_rows(
        data_dir=data_dir,
        approved_rows=approved_rows,
        close_markers=close_markers,
    )
    return pending_rows + approved_rows


def _pending_rows(
    *,
    data_dir: Path,
    approved_rows: list[dict[str, Any]],
    close_markers: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    pending_dir = data_dir / "pending"
    if not pending_dir.exists():
        return []
    approved_by_pending: dict[str, list[dict[str, Any]]] = {}
    for row in approved_rows:
        pending_id = str(row.get("pending_id") or "")
        if pending_id:
            approved_by_pending.setdefault(pending_id, []).append(row)
    rows: list[dict[str, Any]] = []
    for manifest_path in pending_dir.glob("*/manifest.json"):
        pending_id = manifest_path.parent.name
        manifest = _safe_read_json(manifest_path)
        if not isinstance(manifest, dict):
            manifest = {}
        source_run_id = str(manifest.get("run_id") or "")
        close_marker = close_markers.get(("pending", pending_id))
        linked_approved_rows = approved_by_pending.get(pending_id, [])
        linked_approved = sorted(str(row["id"]) for row in linked_approved_rows)
        lifecycle_status = _pending_lifecycle_status(
            manifest=manifest,
            linked_approved_rows=linked_approved_rows,
            close_marker=close_marker,
        )
        rows.append(
            {
                "kind": "pending",
                "id": pending_id,
                "path": str(manifest_path.parent),
                "manifest": str(manifest_path),
                "package_type": _pending_package_type(pending_id, manifest),
                "lifecycle_status": lifecycle_status,
                "status": str(manifest.get("status") or ""),
                "created_at": str(manifest.get("created_at") or ""),
                "source_run_id": source_run_id,
                "pending_id": pending_id,
                "approved_id": "",
                "linked_approved_ids": linked_approved,
                "closed_marker": str(close_marker.get("path") or "") if close_marker else "",
                "identity_candidates": [pending_id],
            }
        )
    return rows


def _pending_lifecycle_status(
    *,
    manifest: dict[str, Any],
    linked_approved_rows: list[dict[str, Any]],
    close_marker: dict[str, Any] | None,
) -> str:
    if close_marker:
        return "closed"
    manifest_status = str(manifest.get("status") or "")
    if manifest_status in {"applied", "verified", "closed"}:
        return manifest_status
    linked_statuses = {str(row.get("lifecycle_status") or "") for row in linked_approved_rows}
    for status in ("failed", "verified", "applied", "closed", "approved"):
        if status in linked_statuses:
            return status
    return "pending_review"


def _approved_rows(
    *,
    data_dir: Path,
    run_rows: list[dict[str, Any]],
    apply_rows: dict[str, dict[str, Any]],
    applied_markers: dict[str, dict[str, Any]],
    close_markers: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for package_path in _approved_package_paths(data_dir):
        package = _safe_read_json(package_path)
        if not isinstance(package, dict):
            package = {}
        approved_id = str(package.get("approved_id") or _approved_id_from_path(package_path))
        candidates = _approved_identity_candidates(package_path, approved_id, package)
        applied_marker = _first_by_candidates(applied_markers, candidates)
        apply_row = _first_by_candidates(apply_rows, candidates)
        close_marker = close_markers.get(("approved", approved_id))
        lifecycle_status = _approved_lifecycle_status(
            package=package,
            apply_row=apply_row,
            applied_marker=applied_marker,
            close_marker=close_marker,
        )
        rows.append(
            {
                "kind": "approved",
                "id": approved_id,
                "path": str(package_path),
                "manifest": "",
                "package_type": str(package.get("package_type") or package.get("task") or ""),
                "lifecycle_status": lifecycle_status,
                "status": str(package.get("status") or ""),
                "created_at": str(package.get("created_at") or ""),
                "pending_id": str(package.get("pending_id") or ""),
                "source_run_id": str(package.get("source_run_id") or ""),
                "approved_id": approved_id,
                "selected_actions_count": package.get("selected_actions_count", ""),
                "actions_checksum": str(package.get("actions_checksum") or ""),
                "apply_run_id": str(_value_from_first("apply_run_id", applied_marker, apply_row) or ""),
                "apply_status": str(_value_from_first("status", apply_row, applied_marker) or ""),
                "apply_lifecycle_status": str(_value_from_first("lifecycle_status", apply_row) or ""),
                "applied_marker": str(applied_marker.get("path") or "") if applied_marker else "",
                "closed_marker": str(close_marker.get("path") or "") if close_marker else "",
                "identity_candidates": candidates,
                "source_pending_exists": bool(
                    str(package.get("pending_id") or "") and (data_dir / "pending" / str(package.get("pending_id"))).exists()
                ),
                "source_run_exists": _run_exists(run_rows, str(package.get("source_run_id") or "")),
            }
        )
    return rows


def _approved_package_paths(data_dir: Path) -> list[Path]:
    approved_dir = data_dir / "approved"
    if not approved_dir.exists():
        return []
    paths: list[Path] = []
    paths.extend(approved_dir.glob("*/approved_apply_plan.json"))
    paths.extend(path for path in approved_dir.glob("*.approved.json") if path.is_file())
    return sorted(dict.fromkeys(paths))


def _approved_lifecycle_status(
    *,
    package: dict[str, Any],
    apply_row: dict[str, Any] | None,
    applied_marker: dict[str, Any] | None,
    close_marker: dict[str, Any] | None,
) -> str:
    if close_marker:
        return "closed"
    if apply_row:
        if apply_row.get("status") in {"blocked", "error"}:
            return "failed"
        lifecycle = str(apply_row.get("lifecycle_status") or "")
        if lifecycle in {"verified", "closed"}:
            return "verified"
        if lifecycle == "failed":
            return "failed"
        if lifecycle == "applied":
            return "applied"
    if applied_marker:
        if applied_marker.get("status") in {"blocked", "error"}:
            return "failed"
        return "applied"
    if package.get("status") == "approved":
        return "approved"
    return str(package.get("status") or "unknown")


def _apply_rows(run_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for row in run_rows:
        if row.get("mode") != "apply":
            continue
        approved_id = str(row.get("approved_id") or "")
        if not approved_id:
            continue
        rows[approved_id] = row
    return rows


def _applied_markers(data_dir: Path) -> dict[str, dict[str, Any]]:
    marker_dir = data_dir / "approved" / "applied"
    markers: dict[str, dict[str, Any]] = {}
    if not marker_dir.exists():
        return markers
    for marker_path in marker_dir.glob("*.applied.json"):
        marker = _safe_read_json(marker_path)
        if not isinstance(marker, dict):
            continue
        approved_id = str(marker.get("approved_id") or "")
        if approved_id:
            markers[approved_id] = {**marker, "path": str(marker_path)}
    return markers


def _close_markers(data_dir: Path) -> dict[tuple[str, str], dict[str, Any]]:
    marker_dir = data_dir / "approved" / "closed"
    markers: dict[tuple[str, str], dict[str, Any]] = {}
    if not marker_dir.exists():
        return markers
    for marker_path in marker_dir.glob("*.closed.json"):
        marker = _safe_read_json(marker_path)
        if not isinstance(marker, dict):
            continue
        target_kind = str(marker.get("target_kind") or "")
        target_id = str(marker.get("target_id") or "")
        if target_kind and target_id:
            markers[(target_kind, target_id)] = {**marker, "path": str(marker_path)}
    return markers


def _approved_identity_candidates(package_path: Path, approved_id: str, package: dict[str, Any]) -> list[str]:
    candidates = [
        approved_id,
        str(package_path),
        approval_identity_from_path(package_path),
        str(package.get("approved_path") or ""),
    ]
    return list(dict.fromkeys(candidate for candidate in candidates if candidate))


def _first_by_candidates(rows_by_key: dict[str, dict[str, Any]], candidates: list[str]) -> dict[str, Any] | None:
    for candidate in candidates:
        row = rows_by_key.get(candidate)
        if row:
            return row
    return None


def _value_from_first(key: str, *rows: dict[str, Any] | None) -> Any:
    for row in rows:
        if row and row.get(key) not in {None, ""}:
            return row.get(key)
    return ""


def _approved_id_from_path(package_path: Path) -> str:
    if package_path.name == "approved_apply_plan.json":
        return package_path.parent.name
    return package_path.stem.removesuffix(".approved")


def _pending_package_type(pending_id: str, manifest: dict[str, Any]) -> str:
    raw_type = str(manifest.get("package_type") or manifest.get("task") or "")
    if raw_type:
        return raw_type
    return pending_id.removesuffix("_pending")


def _run_exists(run_rows: list[dict[str, Any]], run_id: str) -> bool:
    return bool(run_id and any(row.get("run_id") == run_id for row in run_rows))


def _safe_read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _public_row(row: dict[str, Any]) -> dict[str, Any]:
    hidden = {"identity_candidates"}
    return {key: value for key, value in row.items() if key not in hidden}


def _approval_sort_key(row: dict[str, Any]) -> tuple[str, str]:
    return (str(row.get("created_at") or ""), str(row.get("id") or ""))
