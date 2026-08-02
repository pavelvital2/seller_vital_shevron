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
    -- /usr/bin/env bash "$ROOT_DIR/scripts/sessions/start_ozon_session_watchdog.sh" "$@"
fi
SESSION_DIR="$ROOT_DIR/.sessions/ozon"
LOG_DIR="$SESSION_DIR/session_refresh_logs"
PID_FILE="$SESSION_DIR/ozon_session_watchdog.pid"
INTERVAL_SECONDS="${OZON_WATCHDOG_INTERVAL_SECONDS:-1800}"
REFRESH_SCRIPT="$ROOT_DIR/scripts/sessions/ozon_session_refresh.sh"
LOG_FILE="$LOG_DIR/ozon-session-watchdog-$(date -u +%Y%m%dT%H%M%SZ).log"

mkdir -p "$LOG_DIR"

if [[ -s "$PID_FILE" ]]; then
  OLD_PID="$(cat "$PID_FILE" || true)"
  if [[ -n "$OLD_PID" ]] && kill -0 "$OLD_PID" 2>/dev/null; then
    echo "status=already_running"
    echo "pid=$OLD_PID"
    echo "pid_file=$PID_FILE"
    exit 0
  fi
fi

{
  echo "kind=ozon_session_watchdog"
  echo "started_at=$(date -Is)"
  echo "interval_seconds=$INTERVAL_SECONDS"
  echo "refresh_script=$REFRESH_SCRIPT"
} >>"$LOG_FILE"

nohup setsid env -u SELLER_PROFILE_LEASE_HELD INTERVAL_SECONDS="$INTERVAL_SECONDS" REFRESH_SCRIPT="$REFRESH_SCRIPT" bash -c '
  while true; do
    echo "tick_at=$(date -Is)"
    "$REFRESH_SCRIPT" || echo "refresh_exit=$?"
    sleep "$INTERVAL_SECONDS"
  done
' >>"$LOG_FILE" 2>&1 < /dev/null &

PID="$!"
echo "$PID" > "$PID_FILE"
chmod 600 "$PID_FILE"

echo "status=started"
echo "pid=$PID"
echo "pid_file=$PID_FILE"
echo "log_file=$LOG_FILE"
