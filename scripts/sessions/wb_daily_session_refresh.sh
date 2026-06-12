#!/usr/bin/env bash
set -uo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<'EOF'
Usage: scripts/sessions/wb_daily_session_refresh.sh

Refreshes WB persistent session for seller.wildberries.ru and cmp.wildberries.ru.
Writes logs to 00_raw_snapshots/session_refresh_logs/.
EOF
  exit 0
fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOG_DIR="$PROJECT_ROOT/.sessions/wb/session_refresh_logs"
LOCK_FILE="$PROJECT_ROOT/.sessions/wb/wb-session-refresh.lock"
NODE_SCRIPT="$PROJECT_ROOT/scripts/sessions/wb_persistent_session.js"
KEEPALIVE_SCRIPT="$PROJECT_ROOT/scripts/sessions/wb_session_keepalive.js"

load_env_value() {
  local key="$1"
  local value
  value="$(grep -m1 -E "^${key}=" "$PROJECT_ROOT/.env" 2>/dev/null | sed -E "s/^${key}=//" || true)"
  value="${value%\"}"
  value="${value#\"}"
  value="${value%\'}"
  value="${value#\'}"
  if [[ -n "$value" ]]; then
    export "$key=$value"
  fi
}

if [[ -z "${WB_EXPECTED_SELLER:-}" ]]; then
  load_env_value "WB_EXPECTED_SELLER"
fi

mkdir -p "$LOG_DIR"

timestamp="$(date '+%Y-%m-%d_%H-%M-%S')"
log_file="$LOG_DIR/wb-session-refresh-$timestamp.log"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  {
    echo "started_at=$(date --iso-8601=seconds)"
    echo "status=skipped"
    echo "reason=another_refresh_is_running"
  } >> "$log_file"
  exit 0
fi

cd "$PROJECT_ROOT"

run_refresh() {
  local label="$1"
  local url="$2"

  echo "== $label =="
  if node "$NODE_SCRIPT" --url "$url"; then
    echo "status=ok"
  else
    echo "status=failed"
    echo "note=refresh_failed_profile_may_be_busy_or_auth_required"
  fi
}

{
  echo "started_at=$(date --iso-8601=seconds)"
  echo "profile=${WB_BROWSER_PROFILE:-$PROJECT_ROOT/.sessions/wb/browser-profile}"
  echo
  run_refresh "seller.wildberries.ru" "https://seller.wildberries.ru/"
  echo
  run_refresh "cmp.wildberries.ru" "https://cmp.wildberries.ru/campaigns/list"
  echo
  echo "== wb_session_keepalive =="
  if node "$KEEPALIVE_SCRIPT"; then
    echo "keepalive_status=ok"
  else
    echo "keepalive_status=failed"
  fi
  echo
  echo "finished_at=$(date --iso-8601=seconds)"
} >> "$log_file" 2>&1
