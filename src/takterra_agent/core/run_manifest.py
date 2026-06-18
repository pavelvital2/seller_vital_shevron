from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Literal

from takterra_agent.reports.writer import ensure_dir, write_json


ManifestMode = Literal["read_only", "dry_run", "apply", "verify", "maintenance"]
ManifestRisk = Literal["none", "low", "normal", "high"]
ManifestStatus = Literal["ok", "warning", "blocked", "error"]

SENSITIVE_KEY_PARTS = (
    "authorization",
    "api_key",
    "apikey",
    "bearer",
    "client_id",
    "client_secret",
    "cookie",
    "password",
    "refresh_token",
    "secret",
    "storage_state",
    "token",
)


@dataclass(frozen=True)
class RunManifest:
    run_id: str
    task: str
    mode: ManifestMode
    risk: ManifestRisk
    marketplaces: list[str]
    status: ManifestStatus
    started_at: str
    finished_at: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    source_run_ids: list[str] = field(default_factory=list)
    pending_id: str = ""
    approved_id: str = ""
    applied_by_run_id: str = ""
    closed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def sanitize_manifest_value(value: Any) -> Any:
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text):
                safe[key_text] = "<redacted>"
            else:
                safe[key_text] = sanitize_manifest_value(item)
        return safe
    if isinstance(value, list):
        return [sanitize_manifest_value(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_manifest_value(item) for item in value]
    return value


def manifest_from_summary(
    *,
    summary: dict[str, Any],
    task: str,
    mode: ManifestMode,
    risk: ManifestRisk,
    marketplaces: list[str],
    inputs: dict[str, Any] | None = None,
    source_run_ids: list[str] | None = None,
    closed: bool = False,
) -> RunManifest:
    run_id = str(summary.get("run_id") or "")
    if not run_id:
        raise ValueError("summary must contain run_id")
    started_at = str(summary.get("started_at") or "")
    status = _normalize_status(str(summary.get("overall_status") or summary.get("status") or "warning"))
    artifacts = _string_artifacts(summary.get("artifacts") if isinstance(summary.get("artifacts"), dict) else {})

    pending_id = str(summary.get("pending_id") or "")
    approved_id = str(summary.get("approved_id") or "")
    applied_by_run_id = str(summary.get("applied_by_run_id") or "")
    if not source_run_ids:
        raw_source = summary.get("source_run_ids")
        source_run_ids = [str(item) for item in raw_source] if isinstance(raw_source, list) else []

    return RunManifest(
        run_id=run_id,
        task=task,
        mode=mode,
        risk=risk,
        marketplaces=list(marketplaces),
        status=status,
        started_at=started_at,
        finished_at=datetime.now().isoformat(timespec="seconds"),
        inputs=sanitize_manifest_value(inputs or {}),
        artifacts=artifacts,
        source_run_ids=source_run_ids,
        pending_id=pending_id,
        approved_id=approved_id,
        applied_by_run_id=applied_by_run_id,
        closed=closed,
    )


def write_run_manifest(
    *,
    data_dir: Path,
    run_dir: Path,
    manifest: RunManifest,
) -> dict[str, Any]:
    manifest_path = run_dir / "manifest.json"
    write_json(manifest_path, manifest.to_dict())
    _upsert_index(data_dir / "runs" / "index.jsonl", manifest)
    return {
        "manifest": str(manifest_path),
        "index": str(data_dir / "runs" / "index.jsonl"),
    }


def read_run_index(data_dir: Path) -> list[dict[str, Any]]:
    index_path = data_dir / "runs" / "index.jsonl"
    if not index_path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in index_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("run_id"):
            rows.append(item)
    return rows


def list_runs(
    *,
    data_dir: Path,
    task: str | None = None,
    status: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    rows = read_run_index(data_dir)
    if task:
        rows = [row for row in rows if row.get("task") == task]
    if status:
        rows = [row for row in rows if row.get("status") == status]
    rows.sort(key=_sort_key, reverse=True)
    return rows[:limit]


def latest_run(
    *,
    data_dir: Path,
    task: str | None = None,
    status: str | None = None,
) -> dict[str, Any] | None:
    rows = list_runs(data_dir=data_dir, task=task, status=status, limit=1)
    return rows[0] if rows else None


def find_run(
    *,
    data_dir: Path,
    run_id: str,
) -> dict[str, Any] | None:
    for row in read_run_index(data_dir):
        if row.get("run_id") == run_id:
            return row
    runs_dir = data_dir / "runs"
    if not runs_dir.exists():
        return None
    for manifest_path in runs_dir.glob(f"*/{run_id}/manifest.json"):
        try:
            item = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(item, dict):
            return item
    return None


def _upsert_index(index_path: Path, manifest: RunManifest) -> None:
    ensure_dir(index_path.parent)
    rows = [row for row in read_run_index(index_path.parent.parent) if row.get("run_id") != manifest.run_id]
    rows.append(manifest.to_dict())
    rows.sort(key=_sort_key)
    index_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def _normalize_status(status: str) -> ManifestStatus:
    if status in {"ok", "warning", "blocked", "error"}:
        return status  # type: ignore[return-value]
    return "warning"


def _string_artifacts(artifacts: dict[Any, Any]) -> dict[str, str]:
    safe: dict[str, str] = {}
    for key, value in artifacts.items():
        key_text = str(key)
        if _is_sensitive_key(key_text):
            continue
        safe[key_text] = str(value)
    return safe


def _sort_key(row: dict[str, Any]) -> tuple[str, str]:
    return (str(row.get("started_at") or ""), str(row.get("run_id") or ""))
