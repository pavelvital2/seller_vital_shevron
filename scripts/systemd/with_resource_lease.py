#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import time
import uuid

from seller_agent.core.job_store import JobStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a command while holding a shared Seller runtime lease")
    parser.add_argument("--resource-key", required=True)
    parser.add_argument("--runtime-db", type=Path, default=Path("runtime/runtime.db"))
    parser.add_argument("--wait-seconds", type=int, default=120)
    parser.add_argument("--ttl-seconds", type=int, default=1800)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("command is required after --")
    owner_id = f"external:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    store = JobStore(args.runtime_db)
    deadline = time.monotonic() + max(args.wait_seconds, 0)
    lease = None
    while lease is None:
        lease = store.acquire_resource_lease(
            resource_key=args.resource_key,
            owner_id=owner_id,
            ttl_seconds=max(args.ttl_seconds, 60),
            data={"kind": "external_command", "command": Path(command[0]).name},
        )
        if lease is not None or time.monotonic() >= deadline:
            break
        time.sleep(2)
    if lease is None:
        print(f"resource lease busy: {args.resource_key}")
        return 75
    try:
        return subprocess.run(command, check=False).returncode
    finally:
        store.release_resource_lease(resource_key=args.resource_key, owner_id=owner_id)


if __name__ == "__main__":
    raise SystemExit(main())
