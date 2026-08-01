#!/usr/bin/env python3
from dataclasses import asdict
from datetime import datetime
import json

from seller_agent.core.scheduled_jobs import enqueue_scheduled_job


slot = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M")
result = enqueue_scheduled_job(task_id="ozon-lk-state-monitor", schedule_key=f"ozon-lk-state-monitor:{slot}", title="Ozon LK: изменение состояния")
print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
raise SystemExit(0 if result.ok else 1)
