#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
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

nohup setsid env INTERVAL_SECONDS="$INTERVAL_SECONDS" REFRESH_SCRIPT="$REFRESH_SCRIPT" bash -c '
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
