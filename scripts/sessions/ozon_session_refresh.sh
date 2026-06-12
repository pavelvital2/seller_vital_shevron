#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SESSION_DIR="$ROOT_DIR/.sessions/ozon"
LOG_DIR="$SESSION_DIR/session_refresh_logs"
LOCK_DIR="$SESSION_DIR/ozon_session_refresh.lock"
COOKIE_FILE="${OZON_COOKIE_FILE:-$ROOT_DIR/tmp/auth/ozon_user_cookies.json}"
EXPECTED_STORE="${OZON_EXPECTED_STORE:-Vital Shevron}"
CDP_PORT="${OZON_REMOTE_DEBUGGING_PORT:-9544}"
CDP_URL="${OZON_CDP_URL:-http://127.0.0.1:$CDP_PORT}"
DATE_ID="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="$LOG_DIR/ozon-session-refresh-$DATE_ID.log"

mkdir -p "$LOG_DIR" "$ROOT_DIR/tmp/auth"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "status=locked"
  echo "lock=$LOCK_DIR"
  exit 9
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

{
  echo "kind=ozon_session_refresh"
  echo "started_at=$(date -Is)"
  echo "expected_store=$EXPECTED_STORE"
  echo "cdp_url=$CDP_URL"
  echo "cookie_file_present=$([[ -s "$COOKIE_FILE" ]] && echo true || echo false)"
  echo "cookie_values_printed=false"

  if ss -ltn 2>/dev/null | grep -Eq "127\\.0\\.0\\.1:${CDP_PORT}\\b|0\\.0\\.0\\.0:${CDP_PORT}\\b|\\*:${CDP_PORT}\\b|\\[::\\]:${CDP_PORT}\\b"; then
    echo "mode=cdp_keepalive"
    OZON_CDP_URL="$CDP_URL" OZON_EXPECTED_STORE="$EXPECTED_STORE" \
      node "$ROOT_DIR/scripts/sessions/ozon_session_keepalive_cdp.js"
  elif [[ -s "$COOKIE_FILE" ]]; then
    echo "mode=cookie_import"
    xvfb-run -a node "$ROOT_DIR/scripts/sessions/ozon_import_cookies_check.js" \
      --cookie-file "$COOKIE_FILE" \
      --expected-store "$EXPECTED_STORE" \
      --headful
  else
    echo "mode=persistent_refresh"
    xvfb-run -a node "$ROOT_DIR/scripts/sessions/ozon_seller_persistent_session.js" \
      --expected-store "$EXPECTED_STORE" \
      --headful \
      --url "https://seller.ozon.ru/app/dashboard/main"
  fi

  echo "finished_at=$(date -Is)"
} 2>&1 | tee "$LOG_FILE"
