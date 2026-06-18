from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any

from takterra_agent.core.run_manifest import read_run_index
from takterra_agent.reports.writer import ensure_dir, write_json


def approval_file_for(run_id: str, approved_dir: Path = Path("data/approved")) -> Path:
    return approved_dir / f"{run_id}.approved.json"


def is_approved(run_id: str, approved_dir: Path = Path("data/approved")) -> bool:
    return approval_file_for(run_id, approved_dir).exists()


def canonical_checksum(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def approval_identity_from_path(path: Path) -> str:
    return str(path.expanduser().resolve())


def apply_marker_for(
    *,
    data_dir: Path,
    approved_id: str,
) -> Path:
    marker_name = canonical_checksum({"approved_id": approved_id})
    return data_dir / "approved" / "applied" / f"{marker_name}.applied.json"


def assert_apply_not_repeated(
    *,
    data_dir: Path,
    approved_id: str,
    current_run_id: str | None = None,
) -> None:
    if not approved_id:
        raise RuntimeError("approved_id is required for apply idempotency guard")

    marker_path = apply_marker_for(data_dir=data_dir, approved_id=approved_id)
    if marker_path.exists():
        marker = _read_json(marker_path)
        raise RuntimeError(
            "approved package already applied: "
            f"{approved_id} by {marker.get('apply_run_id') or marker_path.name}"
        )

    for row in read_run_index(data_dir):
        if current_run_id and row.get("run_id") == current_run_id:
            continue
        if row.get("mode") != "apply":
            continue
        if row.get("approved_id") != approved_id:
            continue
        if row.get("status") not in {"ok", "warning"}:
            continue
        if row.get("lifecycle_status") not in {"applied", "verified", "closed"}:
            continue
        raise RuntimeError(
            "approved package already applied: "
            f"{approved_id} by {row.get('run_id')}"
        )


def mark_approved_applied(
    *,
    data_dir: Path,
    approved_id: str,
    apply_run_id: str,
    task: str,
    status: str,
    run_manifest_path: str = "",
    checksum: str = "",
) -> dict[str, Any]:
    if not approved_id:
        raise RuntimeError("approved_id is required for apply marker")
    marker_path = apply_marker_for(data_dir=data_dir, approved_id=approved_id)
    marker = {
        "approved_id": approved_id,
        "apply_run_id": apply_run_id,
        "task": task,
        "status": status,
        "run_manifest": run_manifest_path,
        "checksum": checksum,
        "applied_at": datetime.now().isoformat(timespec="seconds"),
    }
    ensure_dir(marker_path.parent)
    write_json(marker_path, marker)
    return {**marker, "path": str(marker_path)}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}
