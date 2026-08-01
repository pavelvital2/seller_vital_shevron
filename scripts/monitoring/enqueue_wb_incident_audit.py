#!/usr/bin/env python3
from dataclasses import asdict
import json

from seller_agent.core.scheduled_jobs import enqueue_scheduled_job


result = enqueue_scheduled_job(task_id="wb-incident-audit", schedule_key="wb-incident-audit:2026-08-03", title="WB: контроль складских инцидентов")
print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
raise SystemExit(0 if result.ok else 1)
