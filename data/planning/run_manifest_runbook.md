# RunManifest Runbook

Дата актуализации: 2026-06-18

## Итог

`RunManifest` - единый машинно-читаемый паспорт запуска task-runner. Он нужен,
чтобы CLI, будущий Telegram-бот и агенты могли быстро найти последний запуск,
его статус, режим, риск, маркетплейсы и основные артефакты без ручного обхода
папок `data/runs/YYYY-MM-DD/<run_id>/`.

## Где хранится

На каждый подключенный запуск:

```text
data/runs/YYYY-MM-DD/<run_id>/manifest.json
```

Общий runtime-индекс:

```text
data/runs/index.jsonl
```

`data/runs/index.jsonl` не коммитится. Это операционный runtime-файл, как и
остальные содержательные файлы `data/runs/`.

## Минимальная схема

```json
{
  "run_id": "",
  "task": "",
  "mode": "read_only|dry_run|apply|verify|maintenance",
  "risk": "none|low|normal|high",
  "marketplaces": ["ozon", "wb"],
  "status": "ok|warning|blocked|error",
  "started_at": "",
  "finished_at": "",
  "inputs": {},
  "artifacts": {},
  "source_run_ids": [],
  "pending_id": "",
  "approved_id": "",
  "applied_by_run_id": "",
  "closed": false
}
```

## Подключено в MVP

Первый проход Этапа 1 подключает manifest к безопасным read-only/dry-run
задачам:

- `status-preflight`: `mode=read_only`, `risk=none`;
- `daily-morning-report`: `mode=read_only`, `risk=low`;
- `reviews-questions`: `mode=dry_run`, `risk=low`, потому что команда готовит
  pending-пакет с draft-ответами, но не пишет в маркетплейсы.

Write/apply-задачи будут подключаться следующим проходом после проверки MVP.

## CLI

Список последних запусков:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python -m takterra_agent.cli runs list
```

Последний запуск конкретной задачи:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python -m takterra_agent.cli runs latest --task status-preflight
```

Показать конкретный запуск:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python -m takterra_agent.cli runs show --run-id <run_id>
```

Фильтры:

```bash
--task <task>
--status ok|warning|blocked|error
--limit <n>
```

## Безопасность

В manifest нельзя записывать:

- API-ключи;
- токены;
- cookies;
- storage state;
- auth headers;
- коды входа;
- raw payload с чувствительными данными.

Модуль `src/takterra_agent/core/run_manifest.py` редактирует значения в
`inputs` по секретоподобным ключам и не переносит artifact-ключи с
секретоподобными именами. Это не заменяет ручную дисциплину: новые задачи
должны передавать в manifest только безопасные summary-level данные.

## Следующий шаг

После проверки MVP:

1. Подключить `RunManifest` ко всем dry-run/apply/verify задачам.
2. Добавить связь `pending_id -> approved_id -> applied_by_run_id`.
3. Подключить будущий `TaskRegistry` к `task`, `mode`, `risk`, `marketplaces`
   и `runbook_path`.
4. Использовать `data/runs/index.jsonl` для Telegram-команд `/status`,
   `/today`, `/reviews`, `/approvals`.
