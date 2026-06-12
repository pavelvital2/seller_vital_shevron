#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
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
