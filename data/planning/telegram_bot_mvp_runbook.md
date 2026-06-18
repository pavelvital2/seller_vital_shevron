# Telegram Bot MVP Runbook

Дата актуализации: 2026-06-18

## Итог

Read-only Telegram MVP - это первый безопасный слой будущего бота. Он
использует `TaskRegistry`, `RunManifest` и runtime-артефакты проекта, но не
выполняет write-операции в Ozon/WB.

Текущая реализация не подключает Telegram token и не запускает отдельный bot
service. Она добавляет проектный command layer и CLI preview, чтобы проверить
ответы будущего бота без риска для магазинов.

## Где находится

```text
src/takterra_agent/bot/commands.py
src/takterra_agent/bot/dispatcher.py
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

1. Подключить этот command layer к реальному Telegram runner без изменения
   business logic.
2. Добавить отправку прикрепленных файлов из `artifacts`, если файл существует
   и безопасен для отправки.
3. Добавить read-only запуск задач из Telegram только после отдельного
   `WorkflowRunner` и safety policy: сначала `/status` и `/today`, без write.
4. Write-кнопки проектировать только после `WorkflowRunner`, `SafetyGuard`,
   approved package builder для всех write-контуров и отдельного owner review.
