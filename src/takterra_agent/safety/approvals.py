from __future__ import annotations

from pathlib import Path


def approval_file_for(run_id: str, approved_dir: Path = Path("data/approved")) -> Path:
    return approved_dir / f"{run_id}.approved.json"


def is_approved(run_id: str, approved_dir: Path = Path("data/approved")) -> bool:
    return approval_file_for(run_id, approved_dir).exists()

