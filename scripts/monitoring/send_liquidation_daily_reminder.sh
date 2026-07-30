#!/usr/bin/env bash
set -euo pipefail

token_file="${VITAL_SHEVRON_TELEGRAM_BOT_TOKEN_FILE:-/home/pavel/.secrets/vital_shevron_telegram_bot_token}"
chat_id="${VITAL_SHEVRON_TELEGRAM_CHAT_ID:--1003683440820}"
thread_id="${VITAL_SHEVRON_TELEGRAM_THREAD_ID:-42336}"

payload="$(
  CHAT_ID="$chat_id" THREAD_ID="$thread_id" python3 - <<'PY'
import json
import os

payload = {
    "chat_id": int(os.environ["CHAT_ID"]),
    "message_thread_id": int(os.environ["THREAD_ID"]),
    "text": (
        "Ежедневный контроль распродажи Vital Shevron\n\n"
        "Wildberries, когорта 163 карточки:\n"
        "- проверить новые заказы/выкупы, остаток, текущую цену, minimum, акцию и CPC;\n"
        "- кандидат на остановку CPC: 10 кликов или 20 руб. расхода без заказа;\n"
        "- 18 карточек второго ценового шага не применять без fresh dry-run и согласования.\n\n"
        "Ozon, когорта 138 SKU:\n"
        "- проверить новые заказы/выкупы, остаток, Elastic и CPC;\n"
        "- кандидат на остановку CPC: расход 50 руб. x pack_qty без заказа.\n\n"
        "Контроль только read-only. Любые остановки, цены, акции и ставки — "
        "отдельным dry-run на согласование."
    ),
}
print(json.dumps(payload, ensure_ascii=False))
PY
)"

if [[ "${1:-}" == "--dry-run" ]]; then
  printf '%s\n' "$payload"
  exit 0
fi

if [[ ! -s "$token_file" ]]; then
  echo "Telegram token file is missing or empty" >&2
  exit 1
fi

bot_token="$(tr -d '\r\n' < "$token_file")"
curl --fail --silent --show-error \
  -X POST "https://api.telegram.org/bot${bot_token}/sendMessage" \
  -H "Content-Type: application/json" \
  --data-binary "$payload" >/dev/null
