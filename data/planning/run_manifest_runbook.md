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
  "lifecycle_status": "created|pending_review|approved|applied|verified|failed|closed",
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

## Подключено

Первый проход Этапа 1 подключил manifest к безопасным read-only/dry-run
задачам:

- `status-preflight`: `mode=read_only`, `risk=none`;
- `daily-morning-report`: `mode=read_only`, `risk=low`;
- `reviews-questions`: `mode=dry_run`, `risk=low`, потому что команда готовит
  pending-пакет с draft-ответами, но не пишет в маркетплейсы.

Ветка `feature/run-manifest-coverage` расширяет покрытие:

- `catalog-fetch`: `mode=read_only`, `risk=low`;
- `wb-promotion-report`: `mode=read_only`, `risk=low`;
- `ozon-elastic-plan`: `mode=dry_run`, `risk=normal`;
- `ozon-elastic-apply`: `mode=apply`, `risk=high`;
- `ozon-cpc-optimization-plan`: `mode=dry_run`, `risk=normal`;
- `ozon-cpc-bids-apply`: `mode=apply`, `risk=high`;
- `wb-actions-discount-plan`: `mode=dry_run`, `risk=normal`;
- `wb-actions-discount-apply`: `mode=apply`, `risk=high`;
- `wb-promotion-bid-plan`: `mode=dry_run`, `risk=normal`;
- `wb-promotion-bids-apply`: `mode=apply`, `risk=high`;
- `wb-card-create-plan`: `mode=dry_run`, `risk=high`;
- `wb-card-create-apply`: `mode=apply`, `risk=high`;
- `actions-apply`: `mode=apply`, `risk=high`;
- `reviews-questions-apply`: `mode=apply`, `risk=low`.

Dry-run задачи получают `pending_id=<run_id>_pending`.
Apply-задачи получают `approved_id` из `approved_plan_run_id` или
`approved_path`, `applied_by_run_id=<apply_run_id>` и `source_run_ids` из
approved/fresh/preflight запусков.

## Idempotency Guard

Ветка `feature/run-manifest-coverage` добавляет первый общий guard от
повторного apply:

- до внешних write-запросов apply-команда вызывает
  `assert_apply_not_repeated(data_dir, approved_id)`;
- guard проверяет runtime marker:

```text
data/approved/applied/<sha256-approved-id>.applied.json
```

- если marker отсутствует, guard дополнительно смотрит
  `data/runs/index.jsonl` и блокирует повтор, если уже есть apply-run с тем же
  `approved_id`, статусом `ok|warning` и lifecycle `applied|verified|closed`;
- после успешного apply команда пишет marker через `mark_approved_applied`.

Marker является runtime-файлом и не коммитится. Он хранит только безопасные
поля: `approved_id`, `apply_run_id`, `task`, `status`, `run_manifest`,
`checksum`, `applied_at`.

## Approved Package Builder

Первый builder подключен для отзывов и вопросов:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m takterra_agent.cli prepare-reviews-questions-approved \
  --source-pending <pending_id> \
  --mode all|replies-only|mark-viewed-only
```

Builder читает `data/pending/<pending_id>/manifest.json` и
`draft_answers.json`, выбирает только поддерживаемые действия, проставляет
`approved: true`, `state: "approved"`, `approved_by`, `approved_at`, считает
`actions_checksum` и сохраняет пакет в `data/approved/<approved_id>/`.
`apply-reviews-questions` проверяет checksum перед write-операциями.

## Approvals Status/Close

Ветка `feature/approval-status-close` добавляет первый общий read-only обзор
approval lifecycle и maintenance-закрытие runtime-пакетов:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m takterra_agent.cli approvals status
```

Фильтры:

```bash
--kind all|pending|approved
--id <pending_id|approved_id|approved_path_identity>
--include-closed
--limit <n>
```

Команда читает:

- `data/pending/*/manifest.json`;
- `data/approved/*/approved_apply_plan.json`;
- старые `data/approved/*.approved.json`;
- `data/approved/applied/*.applied.json`;
- `data/approved/closed/*.closed.json`;
- `data/runs/index.jsonl`.

Нормализованные статусы:

- `pending_review` - pending-пакет ждет review владельца;
- `approved` - approved-пакет создан, но apply еще не выполнен;
- `applied` - apply был выполнен, но verify/закрытие не дало финальный статус;
- `verified` - apply выполнен и подтвержден verify или run lifecycle;
- `failed` - связанный apply завершился `blocked/error`;
- `closed` - пакет закрыт maintenance-маркером и скрывается из обычного
  `approvals status`.

Закрытие:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m takterra_agent.cli approvals close \
  --id <pending_id|approved_id> \
  --kind pending|approved \
  --closed-by owner \
  --reason "superseded"
```

`close` не удаляет исходный pending/approved package и не пишет в
маркетплейсы. Он создает runtime-маркер:

```text
data/approved/closed/<sha256-kind-id>.closed.json
```

и maintenance `RunManifest` с task `approvals-close`. Если закрывается
непримененный approved-пакет со статусом `approved`, команда требует
`--force`, чтобы случайно не скрыть согласованную write-операцию.

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

## Lifecycle

`lifecycle_status` нужен не вместо `status`, а поверх него:

- `pending_review` - dry-run готов к review владельца;
- `applied` - apply выполнен, но verify не дал финальный `ok`;
- `verified` - apply выполнен и verify подтвердил результат;
- `failed` - запуск заблокирован или завершился ошибкой;
- `closed` - read-only/maintenance запуск завершен и не требует дальнейших
  действий.

## Следующий шаг

1. Распространить builder approved package на акции, ставки, карточки и цены.
2. Использовать `WorkflowRunner` для live read-only задач и сохранять
   `RunManifest` как единый источник runtime-статуса.
3. Использовать `data/runs/index.jsonl` для Telegram-команд `/status`,
   `/today`, `/reviews`, `/approvals`.
4. При добавлении новых live задач проверять, что task writes manifest/report
   до подключения к Telegram.
