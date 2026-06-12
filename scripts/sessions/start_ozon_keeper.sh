#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SESSION_DIR="$ROOT_DIR/.sessions/ozon"
LOG_DIR="$SESSION_DIR/session_refresh_logs"
PID_FILE="$SESSION_DIR/ozon_keeper.pid"
PORT="${OZON_REMOTE_DEBUGGING_PORT:-9544}"
LOG_FILE="$LOG_DIR/ozon-keeper-$(date -u +%Y%m%dT%H%M%SZ).log"

mkdir -p "$LOG_DIR"

port_is_busy() {
  ss -ltn 2>/dev/null | grep -Eq "127\\.0\\.0\\.1:${PORT}\\b|0\\.0\\.0\\.0:${PORT}\\b|\\*:${PORT}\\b|\\[::\\]:${PORT}\\b"
}

if [[ -s "$PID_FILE" ]]; then
  OLD_PID="$(cat "$PID_FILE" || true)"
  if [[ -n "$OLD_PID" ]] && kill -0 "$OLD_PID" 2>/dev/null; then
    echo "status=already_running"
    echo "pid=$OLD_PID"
    echo "pid_file=$PID_FILE"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

if port_is_busy; then
  echo "status=port_busy"
  echo "port=$PORT"
  echo "pid_file=$PID_FILE"
  exit 8
fi

{
  echo "kind=ozon_keeper"
  echo "started_at=$(date -Is)"
  echo "port=$PORT"
} >>"$LOG_FILE"

nohup setsid env OZON_REMOTE_DEBUGGING_PORT="$PORT" \
  node "$ROOT_DIR/scripts/sessions/ozon_keep_dashboard_open.js" >>"$LOG_FILE" 2>&1 < /dev/null &

PID="$!"
echo "$PID" > "$PID_FILE"
chmod 600 "$PID_FILE"

echo "status=started"
echo "pid=$PID"
echo "pid_file=$PID_FILE"
echo "log_file=$LOG_FILE"
echo "port=$PORT"
