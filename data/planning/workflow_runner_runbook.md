# WorkflowRunner Runbook

Дата актуализации: 2026-06-18

## Итог

`WorkflowRunner` - промежуточный слой между Telegram-ботом, `TaskRegistry` и
task-runner. Он нужен, чтобы бот и будущие timers запускали задачи единым
образом, а не вызывали отдельные функции напрямую.

Первый MVP поддерживает только read-only задачи:

- `daily-morning-report`;
- `status-preflight`.

Write-операции через `WorkflowRunner` не включены.

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
Telegram command
  -> bot command layer
  -> WorkflowRunner.run_read_only(task_name)
  -> TaskRegistry lookup
  -> read_only gate
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
.sessions/workflows/status-preflight.lock
```

Это заменяет старый точечный lock `/today` и позволяет использовать один
механизм для следующих live read-only задач.

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

## Telegram MVP

Текущее поведение:

- `/today` при `--live-today` запускает `daily-morning-report` через
  `WorkflowRunner`;
- `/status` при `--live-status` запускает `status-preflight` через
  `WorkflowRunner`;
- текстовый Telegram-summary формируется в bot command layer;
- файл полного отчета прикрепляется adapter-ом только из безопасного
  `artifacts.report`;
- `/catalog`, `/reviews` пока читают последний runtime из `data/runs/index.jsonl`.

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
4. Write-операции проектировать отдельно через `SafetyGuard` и approval
   lifecycle; не расширять `run_read_only()` для apply.
