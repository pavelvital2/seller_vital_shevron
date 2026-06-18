from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path


@contextmanager
def file_lock(name: str, locks_dir: Path = Path("data/locks")):
    locks_dir.mkdir(parents=True, exist_ok=True)
    lock_path = locks_dir / f"{name}.lock"
    if lock_path.exists():
        raise RuntimeError(f"Lock already exists: {lock_path}")
    lock_path.write_text("locked\n", encoding="utf-8")
    try:
        yield lock_path
    finally:
        lock_path.unlink(missing_ok=True)

