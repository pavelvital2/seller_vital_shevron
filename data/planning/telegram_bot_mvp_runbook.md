# Telegram Bot MVP Runbook

Дата актуализации: 2026-06-18

## Итог

Read-only Telegram MVP - это первый безопасный слой будущего бота. Он
использует `TaskRegistry`, `RunManifest` и runtime-артефакты проекта, но не
выполняет write-операции в Ozon/WB.

Текущая реализация подключает безопасный read-only Telegram adapter поверх
того же command layer. Adapter умеет отправить preview-ответ, один раз
обработать входящие updates через Telegram Bot API, работать в controlled
polling loop и прикреплять безопасный файл отчета из `artifacts`. Live
read-only `/today` и `/status` запускаются через `WorkflowRunner`.
Marketplace write-операции не запускаются.

Токен бота не хранится в проекте. Если токен был отправлен в чат или попал в
логи, считать его засвеченным и перевыпустить через BotFather перед
production-запуском.

## Где находится

```text
src/seller_agent/bot/commands.py
src/seller_agent/bot/dispatcher.py
src/seller_agent/bot/telegram_runner.py
src/seller_agent/core/workflow_runner.py
```

## CLI Preview

Показать ответ команды:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli bot preview \
  --message /status
```

JSON-режим для тестов/интеграции:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli bot preview \
  --message /approvals \
  --json
```

## Token File

Безопасный вариант - внешний файл вне git, например:

```text
/home/pavel/.secrets/vital_shevron_telegram_bot_token
```

Права:

```bash
chmod 600 /home/pavel/.secrets/vital_shevron_telegram_bot_token
```

Переменная окружения:

```bash
export VITAL_SHEVRON_TELEGRAM_BOT_TOKEN_FILE=/home/pavel/.secrets/vital_shevron_telegram_bot_token
```

Допустимые источники токена:

- `--token-file /path/to/token-file`;
- `VITAL_SHEVRON_TELEGRAM_BOT_TOKEN_FILE`;
- `SELLER_TELEGRAM_BOT_TOKEN_FILE`;
- `VITAL_SHEVRON_TELEGRAM_BOT_TOKEN`;
- `TELEGRAM_BOT_TOKEN`.

Токен нельзя записывать в `AGENTS.md`, `data/planning/`, `README.md`,
`data/runs/`, `.env.example`, git commit, Hermes memory, отчеты или чат.

## Real Adapter CLI

Отправить read-only preview-ответ в конкретный чат:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli bot send-preview \
  --message /help \
  --chat-id 123456789 \
  --token-file /home/pavel/.secrets/vital_shevron_telegram_bot_token
```

Если сообщение нужно отправить в topic/forum thread:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli bot send-preview \
  --message /status \
  --live-status \
  --chat-id 123456789 \
  --thread-id 987 \
  --token-file /home/pavel/.secrets/vital_shevron_telegram_bot_token
```

Одноразовая обработка входящих updates:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli bot poll-once \
  --allowed-chat-id 123456789 \
  --state-file .sessions/telegram/vital_shevron_bot_state.json \
  --token-file /home/pavel/.secrets/vital_shevron_telegram_bot_token
```

State-файл хранит только offset polling и должен лежать под `.sessions/`, чтобы
не попасть в git.

## Controlled Polling

Постоянный polling нельзя запускать без allowlist личного `chat_id`.

Runtime env-файл:

```text
.sessions/telegram/vital_shevron_telegram_bot.env
```

Минимальное содержимое env-файла:

```bash
VITAL_SHEVRON_TELEGRAM_ALLOWED_CHAT_IDS=123456789
```

Файл должен иметь права `600` и не должен попадать в git.

Ручной smoke test controlled loop:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli bot poll-loop \
  --live-today \
  --live-status \
  --allowed-chat-id 123456789 \
  --max-iterations 1 \
  --token-file /home/pavel/.secrets/vital_shevron_telegram_bot_token
```

Systemd user service template:

```text
deploy/systemd/user/vital-shevron-telegram-bot.service
```

Service должен включаться только после:

- token-file создан вне проекта;
- личный `chat_id` подтвержден через `poll-once`;
- `.sessions/telegram/vital_shevron_telegram_bot.env` содержит allowlist;
- `poll-loop --max-iterations 1` прошел без ошибок.

## Поддерживаемые команды

- `/help` - список доступных read-only экранов.
- `/status` - при `--live-status` строит свежий read-only `status-preflight`;
  без `--live-status` показывает последний `status-preflight` из
  `data/runs/index.jsonl`.
- `/today` - при `--live-today` строит свежий read-only
  `daily-morning-report --seller-v3`; без `--live-today` показывает последний
  `daily-morning-report` из `data/runs/index.jsonl`.
- `/reviews` - последний `reviews-questions` из `data/runs/index.jsonl`.
- `/approvals` - текущий обзор `approvals status`.
- `/catalog` - последний `catalog-build-unified` из `data/runs/index.jsonl`;
  Telegram-summary показывает ключевые цифры unified catalog из безопасного
  `summary` artifact.
- `/catalog <запрос>` - read-only поиск товара в
  `data/catalog/unified/products.json` по `internal_sku`,
  `internal_product_id`, названию, Ozon `offer_id/product_id/sku/barcode`,
  WB `vendorCode/nmID/barcode`; barcode подтягивается из processed Ozon/WB
  catalog CSV, если эти файлы есть.
- `/runs` - краткий список последних runtime-статусов по Telegram-задачам.

## Прикрепление файлов

После текстового Telegram-summary adapter отправляет `sendDocument`, если
команда вернула безопасный `report`-артефакт.

Разрешено прикреплять только:

- ключ `artifacts.report`;
- существующий файл внутри `data/runs/` или `data/reports/`;
- расширения `.md`, `.txt`, `.csv`, `.xlsx`, `.pdf`;
- файл размером не больше 20 MB;
- путь без маркеров `token`, `secret`, `cookie`, `storage`, `auth`,
  `password`, `credential`.

Технические артефакты вроде `summary.json`, `manifest.json`, raw snapshots,
cookies, storage state и файлы вне разрешенных директорий не прикрепляются.
Если файл отчета не прошел фильтр, бот оставляет путь в тексте, но не должен
прикреплять подозрительный файл.

## Safety

- MVP не запускает apply-команды.
- MVP не создает approved package.
- MVP не отправляет ответы покупателям.
- MVP не меняет цены, акции, ставки, карточки, фото, остатки или поставки.
- MVP запускает из Telegram только live read-only `/today` и `/status`, если
  явно включены `--live-today` и `--live-status`. Остальные команды показывают
  уже сохраненные runtime-данные.
- Постоянный polling требует allowlist и lock-file; второй экземпляр polling
  должен завершаться с ошибкой lock.
- Live read-only задачи используют per-task lock
  `.sessions/workflows/<task>.lock`; для `/today` это
  `.sessions/workflows/daily-morning-report.lock`, для `/status` -
  `.sessions/workflows/status-preflight.lock`.
- Прикрепление файлов ограничено безопасным `report`-артефактом и не должно
  отправлять секреты, raw snapshots или закрытые runtime-файлы.
- Неподдерживаемые команды возвращают `unsupported_command`.
- Если runtime-данных нет, команда возвращает `no_runtime_data` и пишет:
  `я не могу это подтвердить`.

## Источники данных

- `TaskRegistry` - список команд и safety metadata.
- `WorkflowRunner` - единый read-only запуск live задач с gate/lock/safe error.
- `data/runs/index.jsonl` - последние RunManifest по задачам.
- `data/pending/` и `data/approved/` - только read-only для `/approvals`.
- `data/approved/applied/` и `data/approved/closed/` - только read-only для
  lifecycle статуса.

## Следующий шаг

1. Проверить стабильность live `/today` и `/status` в постоянном polling.
2. Rename-only package `seller_agent` выполнен 2026-06-18 без новой логики.
3. Следующий кандидат - каталог/mapping и `/reviews` в безопасном
   dry-run/read-only режиме, но только после отдельного review.
4. Write-кнопки проектировать только после `WorkflowRunner`, `SafetyGuard`,
   approved package builder для всех write-контуров и отдельного owner review.
