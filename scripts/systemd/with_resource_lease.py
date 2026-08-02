#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import time
import uuid

from seller_agent.core.job_store import JobStore
from seller_agent.core.resource_keys import canonical_lk_profile_key, task_resource_keys


def resolve_resource_key(*, resource_key: str | None, lk_profile: str | None) -> str:
    if bool(resource_key) == bool(lk_profile):
        raise ValueError("exactly one resource key source is required")
    if lk_profile:
        return canonical_lk_profile_key(lk_profile)
    return task_resource_keys(subject_keys=(str(resource_key),))[0]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a command while holding a shared Seller runtime lease")
    resource = parser.add_mutually_exclusive_group(required=True)
    resource.add_argument("--resource-key")
    resource.add_argument("--lk-profile", choices=("ozon", "wb"))
    parser.add_argument("--runtime-db", type=Path, default=Path("runtime/runtime.db"))
    parser.add_argument("--wait-seconds", type=int, default=120)
    parser.add_argument("--ttl-seconds", type=int, default=1800)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("command is required after --")
    try:
        resource_key = resolve_resource_key(resource_key=args.resource_key, lk_profile=args.lk_profile)
    except ValueError as exc:
        parser.error(str(exc))
    owner_id = f"external:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    store = JobStore(args.runtime_db)
    deadline = time.monotonic() + max(args.wait_seconds, 0)
    lease = None
    while lease is None:
        lease = store.acquire_resource_lease(
            resource_key=resource_key,
            owner_id=owner_id,
            ttl_seconds=max(args.ttl_seconds, 60),
            data={"kind": "external_command", "command": Path(command[0]).name},
        )
        if lease is not None or time.monotonic() >= deadline:
            break
        time.sleep(2)
    if lease is None:
        print(f"resource lease busy: {resource_key}")
        return 75
    try:
        return subprocess.run(command, check=False).returncode
    finally:
        store.release_resource_lease(resource_key=resource_key, owner_id=owner_id)


if __name__ == "__main__":
    raise SystemExit(main())
