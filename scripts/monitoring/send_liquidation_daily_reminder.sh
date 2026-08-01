#!/usr/bin/env bash
set -euo pipefail

cd /home/pavel/projects/seller_vital_shevron
export PYTHONPATH="/home/pavel/projects/seller_vital_shevron/src:/home/pavel/projects/seller_vital_shevron${PYTHONPATH:+:$PYTHONPATH}"
exec /home/Codex/agent-tools/python/bin/python scripts/monitoring/enqueue_liquidation_daily_control.py "$@"
