#!/usr/bin/env python3
from dataclasses import asdict
from datetime import date
import json

from seller_agent.core.scheduled_jobs import enqueue_scheduled_job


result = enqueue_scheduled_job(task_id="ozon-min-price-timer-plan", params={"warning_days": 5}, schedule_key=f"ozon-min-price-timer-plan:{date.today().isoformat()}", title="Ozon: срок защиты минимальной цены")
print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
raise SystemExit(0 if result.ok else 1)
