# WorkflowRunner Runbook

Дата актуализации: 2026-07-05

## Итог

`WorkflowRunner` - промежуточный слой между Telegram-ботом, `TaskRegistry` и
task-runner. Он нужен, чтобы бот и будущие timers запускали задачи единым
образом, а не вызывали отдельные функции напрямую.

MVP поддерживает read-only задачи:

- `daily-morning-report`;
- `pricing-status` включая optional input `refresh_api=true`, если runner
  получил credentials;
- `status-preflight`;
- `wb-parser-warehouse-analytics`.

Также подключены подтверждаемые apply handler-ы. Они не обходят safety:
`WorkflowRunner.run_task(..., allowed_modes={"apply"})` требует задачу из
`TaskRegistry`, per-task lock, явный `plan_run_id`/`approved_path`/`source_run_id`
и `confirmed_by_user=true`, а сама apply-функция продолжает делать preflight,
drift-check, idempotency и verify по профильному runbook.

## Где находится

```text
src/seller_agent/core/workflow_runner.py
tests/test_workflow_runner.py
```

Telegram `/today` и `/status` используют runner через:

```text
src/seller_agent/bot/commands.py
```

## Контур запуска

```text
Telegram command / JobService
  -> bot command layer
  -> WorkflowRunner.run_read_only(task_name) или run_task(..., allowed_modes={"apply"})
  -> TaskRegistry lookup
  -> mode gate
  -> per-task lock
  -> task handler
  -> RunManifest/artifacts
  -> Telegram summary + safe report attachment
```

## Safety

`WorkflowRunner.run_read_only()` обязан:

- найти задачу в `TaskRegistry`;
- заблокировать запуск, если задача не `mode=read_only`;
- заблокировать запуск, если для задачи нет handler;
- взять per-task lock до запуска handler;
- вернуть безопасную ошибку без секретов;
- не создавать approved package;
- не запускать apply/dry-run/write задачи.

`WorkflowRunner.run_task(..., allowed_modes={"apply"})` разрешен только для
задач, которые уже описаны в `TaskRegistry`, имеют профильный runbook и
подключенный handler. Этот вызов не является самостоятельным approval: inputs
должны содержать подтверждение владельца и ссылку на конкретный согласованный
пакет.

Текущие blocked reasons:

- `unknown_task` - задачи нет в `TaskRegistry`;
- `not_read_only` - задача не read-only;
- `unsupported_workflow` - handler для задачи не подключен;
- `workflow_busy` - другой процесс держит lock этой задачи;
- `workflow_failed` - handler завершился ошибкой.

## Locks

Runtime locks лежат вне git:

```text
.sessions/workflows/<task>.lock
```

Примеры:

```text
.sessions/workflows/daily-morning-report.lock
.sessions/workflows/pricing-status.lock
.sessions/workflows/status-preflight.lock
```

Это заменяет старый точечный lock `/today` и позволяет использовать один
механизм для следующих live read-only задач.

Для apply-задач lock-файл имеет тот же slug задачи, например:

```text
.sessions/workflows/ozon-elastic-apply.lock
.sessions/workflows/wb-actions-discount-apply.lock
.sessions/workflows/wb-promotion-bids-parser-enriched-apply.lock
```

## Handler Policy

Handler добавляется только для задачи, которая уже описана в `TaskRegistry` и
имеет runbook.

В handler нельзя передавать в summary, manifest или ошибку:

- токены;
- cookies;
- storage state;
- auth headers;
- коды входа;
- сырые payload с чувствительными данными.

Если задача требует credentials, runner загружает их внутри protected участка,
чтобы ошибка подключения вернулась как безопасный `WorkflowRunResult`, а не
падала до Telegram-слоя.

Поддержанные apply handler-ы на 2026-07-05:

- `approved-cards-batch-apply`;
- `ozon-actions-optimizer-apply`;
- `ozon-cpc-bids-apply`;
- `ozon-elastic-apply`;
- `ozon-inbox-apply`;
- `reviews-questions-apply`;
- `wb-actions-discount-apply`;
- `wb-inbox-apply`;
- `wb-promotion-bids-apply`;
- `wb-promotion-bids-parser-enriched-apply`.

## Telegram MVP

Текущее поведение:

- `/today` при `--live-today` запускает `daily-morning-report` через
  `WorkflowRunner`;
- `/status` при `--live-status` запускает `status-preflight` через
  `WorkflowRunner`;
- текстовый Telegram-summary формируется в bot command layer;
- файл полного отчета прикрепляется adapter-ом только из безопасного
  `artifacts.report`;
- `/catalog` читает последний `catalog-build-unified` из
  `data/runs/index.jsonl` и показывает ключевые цифры unified catalog;
  `/catalog <запрос>` работает локально по unified catalog и processed catalog
  CSV без запуска marketplace API;
  `/reviews` пока читает последний runtime из `data/runs/index.jsonl`.

## Проверка

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/pytest \
  tests/test_workflow_runner.py tests/test_bot_commands.py -q
```

Для полного прохода:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/pytest -q
```

## Следующий шаг

1. Проверить стабильность `/today` и `/status` в live polling.
2. Rename-only package `seller_agent` выполнен 2026-06-18 без новой логики.
3. Следующие read-only задачи подключать через тот же runner, а не через
   прямые вызовы из bot layer.
4. Ozon Elastic и WB actions Telegram write-callback-и уже переключены на
   `JobService`. Следующие callback-и переводить позже по одному и после
   каждого переключения делать smoke: dry-run -> approval callback -> job ->
   apply -> verify -> cleanup.
5. Write-операции проектировать отдельно через `SafetyGuard` и approval
   lifecycle; не расширять `run_read_only()` для apply.
