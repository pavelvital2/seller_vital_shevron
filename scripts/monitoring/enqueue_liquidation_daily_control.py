#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date, timedelta
import json
import os
from pathlib import Path

from seller_agent.core.scheduled_jobs import DEFAULT_OWNER_BOT_CHAT_ID, enqueue_scheduled_job


def _default_thread_id() -> int | None:
    value = os.environ.get("VITAL_SHEVRON_TELEGRAM_THREAD_ID", "").strip()
    return int(value) if value else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Queue the daily liquidation control in Job Worker")
    parser.add_argument("--date-to", default=(date.today() - timedelta(days=1)).isoformat())
    parser.add_argument(
        "--chat-id",
        type=int,
        default=int(os.environ.get("VITAL_SHEVRON_TELEGRAM_CHAT_ID", str(DEFAULT_OWNER_BOT_CHAT_ID))),
    )
    parser.add_argument("--thread-id", type=int, default=_default_thread_id())
    parser.add_argument("--runtime-db", type=Path, default=Path("runtime/runtime.db"))
    args = parser.parse_args()
    result = enqueue_scheduled_job(
        task_id="liquidation-daily-control",
        params={"date_to": args.date_to},
        schedule_key=f"liquidation-daily-control:{args.date_to}",
        title="Ежедневный контроль распродажи",
        chat_id=args.chat_id,
        thread_id=args.thread_id,
        runtime_db=args.runtime_db,
    )
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
