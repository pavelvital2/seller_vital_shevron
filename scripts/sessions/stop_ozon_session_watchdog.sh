#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [[ "${SELLER_PROFILE_LEASE_HELD:-0}" != "1" ]]; then
  export PYTHONPATH="$ROOT_DIR/src:$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}"
  exec /usr/bin/env SELLER_PROFILE_LEASE_HELD=1 \
    /home/Codex/agent-tools/python/bin/python \
    "$ROOT_DIR/scripts/systemd/with_resource_lease.py" \
    --lk-profile ozon \
    --runtime-db "$ROOT_DIR/runtime/runtime.db" \
    --wait-seconds 0 \
    --ttl-seconds 900 \
    -- /usr/bin/env bash "$ROOT_DIR/scripts/sessions/stop_ozon_session_watchdog.sh" "$@"
fi
PID_FILE="$ROOT_DIR/.sessions/ozon/ozon_session_watchdog.pid"

if [[ ! -s "$PID_FILE" ]]; then
  echo "status=not_running"
  exit 0
fi

PID="$(cat "$PID_FILE" || true)"
if [[ -z "$PID" ]] || ! kill -0 "$PID" 2>/dev/null; then
  rm -f "$PID_FILE"
  echo "status=stale_pid_removed"
  exit 0
fi

kill -- "-$PID" 2>/dev/null || kill "$PID"
rm -f "$PID_FILE"
echo "status=stopped"
echo "pid=$PID"
