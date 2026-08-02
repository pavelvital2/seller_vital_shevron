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
- `card_work_items` - MVP lifecycle owner-approved карточек по internal SKU:
  `owner_approved -> applying -> applied/closed` с `plan_run_id`,
  `apply_run_id`, `post_verify_run_id` и checksum паспорта;
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
- 2026-07-08 добавлен карточный lifecycle MVP:
  `card_work_items`, `JobStore.upsert_card_work_item()`,
  `get_card_work_item()`, `list_card_work_items()`;
- тесты `tests/test_job_store.py`.

### Этап 2. JobService / JobRunner v1

Статус: реализован первый MVP в ветке `feature/runtime-job-store`.

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
- `JobService.run(job_id)` запускает read-only задачи через текущий
  `WorkflowRunner`;
- apply-задачи без `confirmed_by_user=true` переводятся в
  `waiting_confirmation`, а не выполняются;
- первый apply handler подключен для `apply-approved-cards`: job запускает
  owner-approved batch только при `confirmed_by_user=true`, с TaskRegistry
  metadata `source_plan_task`, `verify_task` и `lock_keys`;
- WorkflowRunner handlers подключены для основных подтверждаемых apply-контуров:
  `ozon-actions-optimizer-apply`, `ozon-cpc-bids-apply`,
  `ozon-elastic-apply`, `reviews-questions-apply`,
  `wb-actions-discount-apply`, `wb-promotion-bids-apply`,
  `wb-promotion-bids-parser-enriched-apply`; все они требуют явный
  `plan_run_id` или `approved_path` и `confirmed_by_user=true`;
- 2026-07-13 `JobService.run()` начал применять `TaskRegistry.lock_keys` для
  подтвержденных apply-задач: все resource leases берутся атомарно в SQLite,
  при занятом ресурсе workflow не стартует, job остается `queued`, а в
  `job_events` пишется `job_resource_blocked`; после завершения leases
  освобождаются и фиксируются событиями
  `job_resource_leases_acquired/released`;
- 2026-07-13 добавлен первый opt-in слой runtime approval guard: если
  подтвержденная write/apply job явно содержит `approval_id` или
  `runtime_approval_id`, `JobService` до старта workflow проверяет
  опциональный `approval_checksum`, атомарно резервирует approval
  `approved -> applying`, после успешного workflow переводит approval в
  `applied`, а после ошибки workflow - в `applying_unknown`; старые callback-и
  с `plan_run_id`/`approved_path` без `approval_id` пока работают по прежнему
  маршруту;
- 2026-07-14 подтвержденные write/apply jobs без явного `approval_id`
  автоматически получают runtime approval record при `JobService.submit()`:
  `approval_id` строится из канонического task id и source reference
  (`approved_path`, `plan_run_id`, `source_run_id`, `pending_id`,
  `approved_id`, `base_plan_run_id` или `internal_skus`), `approval_checksum`
  строится из параметров запуска без runtime approval полей, повторный callback
  по тому же source не перетирает существующий approval и блокируется до
  marketplace workflow, если approval уже не в статусе `approved`;
- 2026-07-14 добавлен первый crash recovery verify слой:
  `JobService.recover_runtime_approvals()` ищет approvals в
  `applying/applying_unknown`, строит безопасную verify job по сохраненным
  `apply_params` и `verify_task`, умеет сразу выполнить verify через
  `--run-verify` и переводит approval в `verified` только при точном
  `overall_status=ok`; `warning` не закрывает approval. Если `verify_task` сам является
  apply-задачей, recovery не запускает ее повторно и возвращает
  `manual_verify_required`. Recovery также не запускает verify для approval
  в `applying`, пока связанный owner apply-job остается в активном статусе:
  такая строка получает `apply_job_still_active` и требует повторной проверки
  после завершения или явной диагностики зависшего job;
- `WorkflowRunner` получил handler для read-only
  `card-content-update-verify`, чтобы карточный batch recovery мог выполняться
  штатным runner-ом;
- 2026-07-14 добавлены отдельные safe verify routes для Ozon write-контуров:
  `ozon-elastic-verify`, `ozon-actions-optimizer-verify`,
  `ozon-cpc-bids-verify`; соответствующие apply tasks теперь указывают на них
  в `TaskRegistry.verify_task`, поэтому recovery не повторяет apply-route для
  этих контуров;
- 2026-07-14 добавлены отдельные safe verify routes для WB promotion:
  `wb-promotion-bids-verify` и
  `wb-promotion-bids-parser-enriched-verify`; они читают approved plan,
  текущие WB campaign bids и сравнивают целевые ставки без повторного
  `update_bids`, соответствующие apply tasks переключены на эти
  `verify_task`;
- 2026-07-14 добавлены отдельные safe verify routes для WB actions и
  reviews/questions: `wb-actions-discount-verify` сверяет owner-approved
  plan с текущими WB prices/discounts без повторного upload, а
  `reviews-questions-verify` делает свежий read-only срез Ozon/WB
  отзывов/вопросов и проверяет, что approved actions больше не находятся в
  pending-очереди; `TaskRegistry.verify_task` соответствующих apply routes
  переключен на эти verify tasks;
- 2026-07-14 закрыты card recovery gaps: добавлены
  `ozon-card-create-verify`, `wb-card-create-verify`,
  `ozon-product-remove-verify` и `seller-sku-update-verify`. Они читают
  owner-approved plan и фактическое состояние Ozon/WB, но не вызывают
  import/upload/update/delete/archive. Legacy `actions-apply` помечен
  `enabled=false`, поэтому `WorkflowRunner` и `JobService` его не запускают;
- `JobService.cancel(job_id)` отменяет `created/queued/waiting_confirmation`;
- `JobRunner.run_next()` выполняет первый queued job;
- CLI-команды `jobs list/show/submit/run/run-next/recover-approvals/cancel`;
- тесты `tests/test_job_service.py`.

Еще не сделано:

- отдельные безопасные verify handlers для inbox/Messenger cleanup и
  оставшихся apply-контуров, где verify пока основан на общем dry-run;

CLI:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli jobs list

PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli jobs submit --task status-preflight \
  --params-json '{"skip_lk": true}'

PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli jobs run-next

PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli jobs recover-approvals

PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli jobs recover-approvals --run-verify
```

### Этап 3. TaskRegistry v2 contract

Статус: начат совместимый v2 metadata слой.

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

Реализовано:

- v2-поля добавлены в `RegisteredTask` с безопасными дефолтами;
- `policy_issues()` показывает незаполненные элементы apply-gate без
  принудительной блокировки существующих задач;
- `source_plan_task`, `verify_task` и `lock_keys` заполнены для основных
  подтверждаемых apply-контуров: Ozon actions optimizer, Ozon Elastic,
  Ozon CPC, WB actions, WB promotion bids, WB parser-enriched promotion bids,
  reviews/questions, approved card batch, card create/remove и seller SKU;
- legacy `actions-apply` отключен через `enabled=false`; `WorkflowRunner` и
  `JobService` блокируют disabled-задачи, а `tasks policy` не считает их
  активным runtime-долгом.

Еще не сделано:

- заполнить timeout и schemas для apply-задач;
- включить жесткое policy validation для write-кнопок после заполнения
  metadata.

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
- Telegram получает `job_id` сразу - выполнено для всех текущих бизнес-задач
  и финальных callback-операций в режиме
  `bot poll-once|poll-loop --runtime-jobs`;
- итоговый report отправляется после завершения - выполнено для Telegram job
  через `bot run-job-next` и безопасный `artifacts.report`;
- все запуски видны в `jobs` и `job_events`;
- `RunManifest` продолжает писаться как artifact.

### Этап 5. Telegram dispatcher

Изменить Telegram MVP:

```text
update/callback -> register telegram_updates -> create job -> send job_id
worker/job runner -> result -> send final report
```

Нужно добавить:

- `callback_query` support - выполнено для финальных report/plan/apply
  callback-ов;
- dedup by `update_id` - выполнено для message и callback updates в режиме
  `--runtime-jobs`;
- команды `/jobs`, `/job_<id>`, `/cancel_<id>` - выполнено как
  maintenance-команды Telegram;
- безопасную отправку report files через существующий attachment policy.

Реализовано в первом optional-слое:

- `src/seller_agent/bot/runtime_jobs.py`;
- `bot poll-once|poll-loop --runtime-jobs --runtime-db ...`;
- `bot run-job-next` выполняет первый queued job и отправляет итог в исходный
  Telegram chat/thread;
- `bot run-job-loop --max-iterations N` выполняет управляемый worker loop,
  останавливается на пустой очереди и подходит для будущего systemd/timer;
- `deploy/systemd/user/vital-shevron-telegram-job-worker.service|timer` -
  repository templates установленного и включенного worker timer;
- `/jobs`, `/job_<id>`, `/cancel_<id>` показывают и безопасно отменяют только
  `created/queued` runtime job;
- `/status`, `/today`, stock/supply, parser analytics, inbox и action plan
  команды создают queued job;
- финальные callback-и period report и Ozon/WB production work plan создают
  queued read-only job;
- Ozon Elastic, Ozon all-actions, WB fixed/manual actions и Ozon/WB inbox
  apply callback-и создают queued apply job, но не запускают write внутри
  polling;
- повторный Telegram `update_id` не создает второй job;
- `JobRunner.run_next()` берет самый старый queued job (FIFO);
- notifier отправляет task-aware сводку, report и следующую inline-кнопку.

С 2026-08-02 transient-конфликт resource lease имеет отдельный worker
контракт. `resource_locked` не является terminal failure: job остается
`queued`, claim освобождается, результат помечается как `deferred`, а текущий
`run-job-loop` после одной такой попытки прекращается и уступает очередь до
следующего timer tick. Это исключает tight loop с повторным выбором того же
FIFO job и не ослабляет exact resource lease. Отдельный `not_before` в SQLite
не используется: durable retry сохраняется статусом `queued`, а bounded
backoff задается штатной периодичностью worker timer. `after_run` и оба CLI
entrypoint (`run-job-next`, `run-job-loop`) вызывают Telegram notifier только
для terminal job. Сам notifier дополнительно fail-closed возвращает
`job_not_terminal` без отправки и без изменения `telegram_updates`, если ему
передали nonterminal job напрямую.

С 2026-07-18 по явному решению владельца systemd worker/timer вводится в
эксплуатацию вместе с `--runtime-jobs` в основном bot unit. Перед включением
проверяется пустая активная очередь и отсутствие approvals в
`applying/applying_unknown`; после включения выполняется read-only smoke и
проверяется доставка Telegram-результата. Основные
apply handler-ы уже есть в `WorkflowRunner`. С 2026-07-20 все фактически
доступные в меню бота API/LK операции и apply callback-и переключены на
очередь Job Worker. Карточные batch apply и promotion bids не считаются
пропущенными маршрутами: соответствующих кнопок в текущем меню бота нет;
при их добавлении обязательна постановка через тот же runtime bridge.

Live deployment 2026-07-18:

- `vital-shevron-telegram-job-worker.timer` установлен, `enabled` и `active`;
- `vital-shevron-telegram-bot.service` установлен с `--runtime-jobs` и
  `runtime/runtime.db`;
- пустая очередь прошла one-shot worker smoke с `ran_jobs=0` и exit code `0`;
- синтетический безопасный Telegram `/status` update создал
  `job_status-preflight_20260718T171356Z_126c9b38`;
- Worker завершил job со статусом `success` и успешно отправил Telegram text и
  report-document;
- после успешной доставки связанный `telegram_updates.processing_status`
  закрывается как `completed`, при ошибке отправки - как
  `notification_failed`;
- marketplace write в smoke не выполнялся.

Full bot routing deployment 2026-07-20:

- все текущие business message/callback операции переведены на queued jobs;
- `JobService` принимает `dry_run`, а `WorkflowRunner` получил handlers для
  Ozon/WB action plans и inbox triage;
- callback apply больше не вызывает `JobService.run()` внутри polling;
- очередь обрабатывается FIFO;
- notifier сохраняет owner-facing summary, безопасный report и следующую
  inline-кнопку;
- полный набор тестов: `447 passed`, `tasks policy` вернул пустой список;
- после проверки пустой очереди и approvals перезапущен bot service;
- Telegram-origin read-only smoke
  `job_status-preflight_20260720T193638Z_1023b02e` завершился `success`,
  связанный update получил `processing_status=completed`;
- marketplace write в smoke не выполнялся.

### Этап 6. Атомарные approvals и resource leases

Статус: начат. Resource leases подключены в `JobService` для apply-задач с
`TaskRegistry.lock_keys`; approval reserve/checksum подключен для явных
`approval_id` и для подтвержденных write/apply jobs, где runtime approval
создается автоматически при постановке job.

До новых write-кнопок:

- approval reserve: `approved -> applying` в одной транзакции - первый слой
  реализован для явного `approval_id`/`runtime_approval_id` и для auto-created
  runtime approvals подтвержденных write jobs;
- idempotency key - пока остается task-level/approval-level, единый контракт
  еще не введен;
- resource lease per marketplace/action - первый слой реализован:
  `JobStore.acquire_resource_leases()` берет набор locks атомарно, а
  `JobService.run()` не запускает apply workflow при занятом ресурсе;
- crash state `applying_unknown` - первый слой реализован при ошибке workflow
  после успешного резервирования approval;
- обязательный verify перед повтором после сбоя - первый слой реализован:
  `recover-approvals` запускает только safe verify/read-only/dry-run task и
  блокирует apply-mode verify как `manual_verify_required`;
- закрытие approval после `verified` - первый слой реализован для successful
  recovery verify; финальное `closed` состояние остается следующим lifecycle
  шагом.

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
