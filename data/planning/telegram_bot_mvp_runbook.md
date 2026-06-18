# Telegram Bot MVP Runbook

Дата актуализации: 2026-06-18

## Итог

Read-only Telegram MVP - это первый безопасный слой будущего бота. Он
использует `TaskRegistry`, `RunManifest` и runtime-артефакты проекта, но не
выполняет write-операции в Ozon/WB.

Текущая реализация подключает безопасный read-only Telegram adapter поверх
того же command layer. Adapter умеет отправить preview-ответ и один раз
обработать входящие updates через Telegram Bot API, но не запускает
marketplace write-операции и не содержит отдельного daemon/service.

Токен бота не хранится в проекте. Если токен был отправлен в чат или попал в
логи, считать его засвеченным и перевыпустить через BotFather перед
production-запуском.

## Где находится

```text
src/takterra_agent/bot/commands.py
src/takterra_agent/bot/dispatcher.py
src/takterra_agent/bot/telegram_runner.py
```

## CLI Preview

Показать ответ команды:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m takterra_agent.cli bot preview \
  --message /status
```

JSON-режим для тестов/интеграции:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m takterra_agent.cli bot preview \
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
  -m takterra_agent.cli bot send-preview \
  --message /help \
  --chat-id 123456789 \
  --token-file /home/pavel/.secrets/vital_shevron_telegram_bot_token
```

Если сообщение нужно отправить в topic/forum thread:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m takterra_agent.cli bot send-preview \
  --message /status \
  --chat-id 123456789 \
  --thread-id 987 \
  --token-file /home/pavel/.secrets/vital_shevron_telegram_bot_token
```

Одноразовая обработка входящих updates:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m takterra_agent.cli bot poll-once \
  --allowed-chat-id 123456789 \
  --state-file .sessions/telegram/vital_shevron_bot_state.json \
  --token-file /home/pavel/.secrets/vital_shevron_telegram_bot_token
```

State-файл хранит только offset polling и должен лежать под `.sessions/`, чтобы
не попасть в git.

## Поддерживаемые команды

- `/help` - список доступных read-only экранов.
- `/status` - последний `status-preflight` из `data/runs/index.jsonl`.
- `/today` - последний `daily-morning-report` из `data/runs/index.jsonl`.
- `/reviews` - последний `reviews-questions` из `data/runs/index.jsonl`.
- `/approvals` - текущий обзор `approvals status`.
- `/catalog` - последний `catalog-fetch` из `data/runs/index.jsonl`.
- `/runs` - краткий список последних runtime-статусов по Telegram-задачам.

## Safety

- MVP не запускает apply-команды.
- MVP не создает approved package.
- MVP не отправляет ответы покупателям.
- MVP не меняет цены, акции, ставки, карточки, фото, остатки или поставки.
- MVP не запускает task-runner задачи из Telegram; он показывает только уже
  сохраненные runtime-данные.
- Неподдерживаемые команды возвращают `unsupported_command`.
- Если runtime-данных нет, команда возвращает `no_runtime_data` и пишет:
  `я не могу это подтвердить`.

## Источники данных

- `TaskRegistry` - список команд и safety metadata.
- `data/runs/index.jsonl` - последние RunManifest по задачам.
- `data/pending/` и `data/approved/` - только read-only для `/approvals`.
- `data/approved/applied/` и `data/approved/closed/` - только read-only для
  lifecycle статуса.

## Следующий шаг

1. Добавить отправку прикрепленных файлов из `artifacts`, если файл существует
   и безопасен для отправки.
2. Подготовить systemd user service/timer для read-only polling после
   подтверждения token-file, chat id и topic id.
3. Добавить read-only запуск задач из Telegram только после отдельного
   `WorkflowRunner` и safety policy: сначала `/status` и `/today`, без write.
4. Write-кнопки проектировать только после `WorkflowRunner`, `SafetyGuard`,
   approved package builder для всех write-контуров и отдельного owner review.
