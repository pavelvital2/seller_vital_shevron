#!/usr/bin/env python3
from dataclasses import asdict
import argparse
import json

from seller_agent.core.scheduled_jobs import enqueue_scheduled_job


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--window-days", type=int, choices=(3, 7, 14), required=True)
    args = parser.parse_args()
    result = enqueue_scheduled_job(task_id="ozon-stars-control", params={"window_days": args.window_days}, schedule_key=f"ozon-stars-control:{args.window_days}d", title=f"Ozon Stars: контроль {args.window_days} дней")
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
