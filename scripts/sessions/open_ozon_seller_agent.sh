#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROFILE_DIR="${OZON_SELLER_PROFILE:-$ROOT_DIR/.sessions/ozon/chrome-profile}"
URL="${1:-https://seller.ozon.ru/app/dashboard/main}"
PORT="${OZON_REMOTE_DEBUGGING_PORT:-9544}"
USER_AGENT="${OZON_USER_AGENT:-Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36}"

mkdir -p "$PROFILE_DIR"

if [[ -z "${DISPLAY:-}" && -z "${WAYLAND_DISPLAY:-}" ]]; then
  cat >&2 <<EOF
No graphical display is available in this shell.

Run this script from a desktop terminal or through Xvfb/VNC:
  OZON_REMOTE_DEBUGGING_PORT=$PORT $0

Chrome profile data will be stored in:
  $PROFILE_DIR
EOF
  exit 2
fi

exec google-chrome \
  --user-data-dir="$PROFILE_DIR" \
  --profile-directory=Default \
  --no-first-run \
  --no-default-browser-check \
  --password-store=basic \
  --user-agent="$USER_AGENT" \
  --lang=ru-RU \
  --remote-debugging-port="$PORT" \
  --window-size=1440,1000 \
  "$URL"
