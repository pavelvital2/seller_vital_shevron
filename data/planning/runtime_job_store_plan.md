# Runtime Job Store Plan

Дата: 2026-06-30.

## Итог

Следующий архитектурный слой проекта - перевод CLI, Telegram и будущих timers
на единый job-контур. Цель: Telegram не выполняет долгие операции внутри
polling-процесса, write-действия не могут повториться из-за повторного
Telegram update, а каждый запуск имеет `job_id`, историю, блокировки,
approval-связи и безопасный результат.

Этот план основан на:

- `data/reference/external_reviews/2026-06-25_gpt_pro_repository_review.md`;
- `data/planning/telegram_bot_management_transition_plan.md`;
- `data/planning/run_manifest_runbook.md`;
- `data/planning/task_registry_runbook.md`;
- `data/planning/workflow_runner_runbook.md`;
- `data/planning/recommendations_index.md`, `VS-REC-091`.

## Почему это следующий шаг

Текущий проект уже имеет `seller_agent`, `TaskRegistry`, `RunManifest`,
approval packages, read-only `WorkflowRunner`, Telegram MVP и первые
approval-safe кнопки. Дальше опасно просто добавлять новые write-кнопки:

- Telegram polling может повторно обработать update после сбоя;
- долгие задачи выполняются внутри polling-процесса;
- `data/runs/index.jsonl` не должен быть оперативной базой состояния;
- approval/apply marker защищает от повтора не полностью атомарно;
- resource locks пока не являются общей транзакционной системой;
- `TaskRegistry` и отдельные runner handler-словари могут расходиться.

## Целевая модель

```text
Telegram / CLI / timer
  -> JobService.submit(task_id, params, actor)
  -> SQLite Job Store
  -> JobRunner
  -> TaskRegistry v2
  -> SafetyPolicy / resource leases / approvals
  -> Executor: script | agent | hybrid
  -> ResultValidator
  -> artifacts + final RunManifest
  -> Telegram notifier / CLI output
```

## Runtime-хранилище

Основной state хранить в SQLite с WAL:

```text
runtime/runtime.db
```

`runtime/` должен быть вне git. `manifest.json`, `data/runs/index.jsonl` и
отчеты остаются audit/export artifacts, но не должны быть единственным
источником оперативного состояния.

Минимальные таблицы:

- `jobs` - текущий статус job;
- `job_events` - append-only история событий;
- `task_requests` - входные параметры и actor;
- `approvals` - draft/pending/approved/applying/applied/verified/closed;
- `resource_leases` - атомарные locks/leases с TTL;
- `telegram_updates` - dedup Telegram update/callback;
- `artifacts` - безопасные ссылки на report/result files.

## Статусы

Job lifecycle:

```text
created -> queued -> running -> waiting_confirmation
        -> success | partial_success | failed | timeout | cancelled
```

Approval lifecycle:

```text
draft -> pending_review -> approved -> applying
      -> applied -> verified -> closed
```

`RunManifest.lifecycle_status` остается итоговым отражением результата, но не
заменяет оперативный job lifecycle.

## Этапы реализации

### Этап 0. Чистая точка

Статус: выполнено 2026-06-30.

1. Провести ревизию текущей ветки.
2. Запустить релевантные тесты.
3. Закоммитить завершенные изменения.
4. Создать отдельную ветку:

```text
feature/runtime-job-store
```

Критерий готовности: runtime-слой начинается в отдельной ветке без смешивания
с карточным apply, supply planning, reviews, actions и external review.

### Этап 1. SQLite Job Store MVP

Статус: реализован MVP в ветке `feature/runtime-job-store`.

Добавить:

```text
src/seller_agent/core/job_store.py
src/seller_agent/core/job_models.py
tests/test_job_store.py
```

MVP-функции:

- initialize schema;
- create job;
- append event;
- update status;
- get job;
- list latest jobs;
- register Telegram update with unique `update_id`;
- acquire/release resource lease.

Ограничение: на этом этапе не менять marketplace write-операции.

Реализовано:

- SQLite schema с таблицами `jobs`, `job_events`, `task_requests`,
  `approvals`, `resource_leases`, `telegram_updates`;
- `JobStore.create_job()`, `update_job_status()`, `append_event()`;
- `JobStore.register_telegram_update()` с dedup по `update_id`;
- `JobStore.acquire_resource_lease()` / `release_resource_lease()`;
- `JobStore.create_approval()` и атомарный
  `reserve_approval_for_apply(approved -> applying)`;
- тесты `tests/test_job_store.py`.

### Этап 2. JobService / JobRunner v1

Статус: реализован первый read-only MVP в ветке `feature/runtime-job-store`.

Добавить:

```text
src/seller_agent/core/job_service.py
src/seller_agent/core/job_runner.py
tests/test_job_service.py
```

Интерфейс:

```python
submit(task_id, params, actor) -> job_id
run(job_id) -> JobResult
cancel(job_id) -> JobResult
get_status(job_id) -> JobStatus
```

На первом проходе поддержать только script executor для read-only задач.

Реализовано:

- `JobService.submit(task_id, params, actor)` создает queued job;
- `JobService.run(job_id)` запускает только read-only задачи через текущий
  `WorkflowRunner`;
- apply-задачи в JobService v1 безопасно блокируются как `failed`;
- `JobService.cancel(job_id)` отменяет только `created/queued`;
- `JobRunner.run_next()` выполняет первый queued job;
- CLI-команды `jobs list/show/submit/run/run-next/cancel`;
- тесты `tests/test_job_service.py`.

Еще не сделано:

- Telegram routing через JobService;
- worker/timer;
- поддержка dry-run/apply через approvals/resource leases.

CLI:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli jobs list

PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli jobs submit --task status-preflight \
  --params-json '{"skip_lk": true}'

PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli jobs run-next
```

### Этап 3. TaskRegistry v2 contract

Расширить metadata задач:

- `executor`: `script|agent|hybrid`;
- `handler`;
- `parameter_schema`;
- `result_schema`;
- `timeout_seconds`;
- `lock_keys`;
- `source_plan_task`;
- `verify_task`;
- `telegram_menu_path`;
- `supports_cancel`;
- `enabled`.

Добавить policy validation:

- `apply` требует `requires_confirmation=true`;
- `apply` требует `source_plan_task`;
- `apply` требует `verify_task`;
- `apply` требует `lock_keys`;
- Telegram-enabled задача требует безопасный report contract;
- agent-задача требует prompt/output contract.

### Этап 4. Подключить первые read-only задачи

Через JobService запускать:

- `status-preflight`;
- `daily-morning-report`;
- `pricing-status`;
- `catalog-build-unified`;
- `reviews-questions` в режиме draft/dry-run позже, после проверки.

Критерий готовности:

- CLI может создать job и дождаться результата - выполнено для read-only
  задач, которые уже поддерживает текущий `WorkflowRunner`;
- Telegram получает `job_id` сразу;
- итоговый report отправляется после завершения;
- все запуски видны в `jobs` и `job_events`;
- `RunManifest` продолжает писаться как artifact.

### Этап 5. Telegram dispatcher

Изменить Telegram MVP:

```text
update/callback -> register telegram_updates -> create job -> send job_id
worker/job runner -> result -> send final report
```

Нужно добавить:

- `callback_query` support;
- dedup by `update_id`;
- команды `/jobs`, `/job_<id>`, `/cancel_<id>`;
- безопасную отправку report files через существующий attachment policy.

### Этап 6. Атомарные approvals и resource leases

До новых write-кнопок:

- approval reserve: `approved -> applying` в одной транзакции;
- idempotency key;
- resource lease per marketplace/action;
- crash state `applying_unknown`;
- обязательный verify перед повтором после сбоя;
- закрытие approval после `verified`.

Первый write-контур для переноса: самый простой и контролируемый, например
одна approved операция с малым числом строк. Массовые карточки, цены, ставки и
скидки подключать только после проверки crash recovery.

## Как это влияет на карточки

Карточные правила, HTML-шаблон, Layer 1/2/3 и owner approval не меняются.
Меняется только execution layer:

- согласованная карточка или пачка карточек становится job/batch-job;
- `apply-approved-cards` получает job status по каждой карточке;
- повторный Telegram update не создает второй apply;
- ошибка одной карточки не теряет состояние всей пачки;
- после сбоя система сначала делает verify, а не повторяет write вслепую.

## Что не делать в этом этапе

- Не переписывать marketplace adapters без необходимости.
- Не добавлять новые бизнес write-кнопки до Job Store и atomic approvals.
- Не переносить runtime state в PostgreSQL: SQLite достаточно для одного VPS.
- Не делать web-панель.
- Не заменять Telegram transport на `aiogram`, пока stdlib-клиент справляется.

## Критерии готовности к следующему слою

- Каждое действие из Telegram имеет `job_id`.
- Повторный Telegram update не запускает второе действие.
- `index.jsonl` больше не используется как оперативный state.
- Approval нельзя применить дважды.
- Resource locks атомарны и имеют TTL/recovery.
- Сбой после внешнего API-вызова переводит операцию в verify/recovery, а не в
  повторный apply.
- Минимальные проверки идут в тестах.
