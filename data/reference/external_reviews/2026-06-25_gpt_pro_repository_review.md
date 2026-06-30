Проверил текущий `main`. Это статическое ревью исходников и структуры; тесты локально не запускал.

## Общая оценка

Проект **заметно продвинулся**. Это уже не набор разрозненных скриптов, а рабочий read-only прототип операционной системы продавца:

```text
CLI
TaskRegistry
RunManifest
approval packages
read-only WorkflowRunner
Telegram polling
systemd-службы
Ozon/WB workflows
каталоги и mapping
тесты
```

С 18 по 24 июня добавлены переименование пакета в `seller_agent`, реестр задач, `RunManifest`, approval lifecycle, read-only runner, Telegram polling, отправка отчетов и объединенный каталог. ([GitHub][1])

**Переписывать проект с нуля не нужно.** Основу можно использовать.

Но сейчас он готов примерно к такому режиму:

```text
Telegram → запросить статус/отчет → синхронно выполнить безопасный сценарий → вернуть результат
```

И пока не готов к целевой схеме:

```text
Telegram-кнопка
→ создать job
→ поставить в очередь
→ запустить script/agent/hybrid
→ контролировать выполнение
→ запросить подтверждение
→ выполнить apply
→ проверить результат
→ закрыть job
```

Главная рекомендация: **временно прекратить добавлять новые бизнес-функции и стабилизировать runtime-ядро**.

---

# Что сделано хорошо

## 1. Появился нормальный доменный каркас

Пакет переименован в `seller_agent`; логика разделена на `bot`, `catalog`, `core`, `marketplaces`, `reports`, `safety`, `sessions`, `tasks`. Это гораздо правильнее прежней привязки к TAKTERRA. ([GitHub][2])

Структуру я бы сохранял:

```text
marketplaces — API и интеграции
tasks        — сценарии верхнего уровня
core         — запуск и жизненный цикл
safety       — подтверждения и блокировки
bot          — только интерфейс
reports      — унификация результатов
```

---

## 2. `TaskRegistry` стал содержательным

Теперь у задачи есть:

```text
name
command
title
description
mode
risk
marketplaces
runbook_path
requires_credentials
requires_lk
requires_mapping
requires_confirmation
telegram_enabled
aliases
```

Это хорошая база для будущей генерации меню и проверки политики безопасности. Реестр уже охватывает API-проверки, каталоги, сессии, Ozon Elastic/CPC, WB акции, продвижение, карточки, отзывы и согласования. ([GitHub][3])

Также есть тест, проверяющий, что CLI-команды покрыты реестром. Это правильный контроль от расхождения CLI и registry. ([GitHub][4])

---

## 3. `RunManifest` — правильное направление

У запусков уже фиксируются:

```text
run_id
task
mode
risk
marketplaces
status
lifecycle_status
started_at
finished_at
inputs
artifacts
source_run_ids
pending_id
approved_id
applied_by_run_id
closed
```

Также есть фильтрация чувствительных полей и связь между dry-run, approval и apply. ([GitHub][5])

Это пригодно как **итоговый паспорт запуска**.

---

## 4. Approval-контур стал существенно лучше

Здесь появился реальный прогресс:

* checksum набора действий;
* checksum файлов;
* проверка, что согласованный payload не изменился;
* apply-marker;
* проверка повторного применения approved package;
* связь с `RunManifest`.

Это уже не просто проверка наличия файла. ([GitHub][6])

Такой подход надо сохранить и довести до атомарной state machine.

---

## 5. Telegram MVP сделан осторожно

Сейчас поддерживаются только безопасные команды:

```text
/help
/status
/today
/reviews
/approvals
/catalog
/runs
```

При этом свежий запуск через `WorkflowRunner` используется только для `/status` и `/today`; остальные команды в основном показывают уже существующие результаты. Write-операции через Telegram явно отключены. Это правильная последовательность внедрения. ([GitHub][7])

Также хорошо сделаны:

```text
allowlist Telegram chat_id
отдельный token-file
single-process polling lock
разбиение длинных сообщений
ограничение типов отправляемых файлов
ограничение размера файла
фильтрация опасных путей
```

---

## 6. Разделение native marketplace IDs сделано правильно

Внутренний каталог не подменяет:

```text
Ozon offer_id / product_id / sku
WB vendorCode / nmID / barcode
```

Confirmed mapping применяется только для объединения данных, а marketplace write должен использовать исходные идентификаторы. Это правильная архитектура. ([GitHub][8])

---

## 7. Тестов уже достаточно много для ранней стадии

Есть тесты для:

```text
approvals
bot commands
catalog
unified catalog
internal SKU
daily report
RunManifest
WorkflowRunner
Ozon CPC / Elastic
WB акции / продвижение / карточки
reviews/questions
sessions
status preflight
```

Это хороший фундамент. ([GitHub][9])

---

# Что неправильно или опасно

## 1. Сейчас два источника истины для выполнения задач

В `RegisteredTask` есть поле `handler`, но задачи из `DEFAULT_TASKS` фактически регистрируются без обработчиков. Отдельно `WorkflowRunner` хранит собственный словарь `default_workflow_handlers()`, где зарегистрированы только:

```text
daily-morning-report
status-preflight
```

То есть задача может:

```text
существовать в TaskRegistry
отображаться в CLI
отображаться в Telegram metadata
но не уметь запускаться через общий WorkflowRunner
```

Это уже видно на примере `catalog-fetch`: он есть как read-only задача, но общий runner блокирует read-only задачу без отдельного handler. ([GitHub][10])

### Как исправить

Должен остаться один источник истины:

```python
TaskDefinition(
    id=...,
    executor=...,
    handler=...,
    input_schema=...,
    result_schema=...,
    locks=...,
    timeout=...,
)
```

CLI, Telegram, timer и агент должны вызывать одно:

```python
job_service.submit(task_id, params, actor)
```

`default_workflow_handlers()` после этого не нужен.

---

## 2. `RunManifest` сейчас не является полноценным lifecycle задачи

Текущие lifecycle-статусы:

```text
created
pending_review
approved
applied
verified
failed
closed
```

Они описывают approval/apply-цепочку, но не работу фоновой задачи. Нет:

```text
queued
running
waiting_confirmation
partial_success
timeout
cancelled
```

Кроме того, `manifest_from_summary()` строит manifest уже из результата работы. Если выполнение упало до создания summary, запуск может вообще не попасть в историю. Блокировки `unknown_task`, `workflow_busy`, `unsupported_workflow` и исключения runner также возвращаются вызывающему коду, но runner сам не создает для них manifest. ([GitHub][5])

### Как исправить

Не смешивать две state machine:

```text
Job lifecycle:
created → queued → running → waiting_confirmation
→ success / partial_success / failed / timeout / cancelled

Approval lifecycle:
draft → pending_review → approved → applying
→ applied → verified → closed
```

`RunManifest` можно оставить итоговым неизменяемым паспортом. Текущий статус job лучше хранить отдельно.

---

## 3. `index.jsonl` опасен при параллельной работе

Функция `_upsert_index()`:

1. читает весь `index.jsonl`;
2. удаляет старую строку с таким `run_id`;
3. добавляет новую;
4. полностью перезаписывает файл.

При этом нет file lock, транзакции или атомарной записи через временный файл. При двух параллельных запусках один процесс может потерять запись другого. Поврежденные JSON-строки при чтении просто пропускаются, то есть проблема может остаться незаметной. ([GitHub][5])

Для одного последовательного CLI это терпимо. Для Telegram, timers и нескольких одноразовых агентов — нет.

### Рекомендация

Взять SQLite, не PostgreSQL:

```text
runtime/runtime.db
```

Минимальные таблицы:

```text
jobs
job_events
task_requests
approvals
resource_locks
telegram_updates
```

SQLite с WAL и транзакциями для одного VPS здесь достаточен.

`manifest.json` и `index.jsonl` можно оставить как экспорт и audit artifact, но не как источник оперативного состояния.

---

## 4. Telegram-бот выполняет долгую работу внутри polling-процесса

`poll_once()` получает update, непосредственно вызывает `dispatch_message()`, выполняет команду, отправляет текст и файлы, после чего переходит к следующему update. Значит, пока собирается `/today`, бот не обрабатывает другие сообщения. ([GitHub][11])

Для целевого функционала это станет проблемой:

```text
агент работает 10–30 минут
ЛК завис
API выполняет долгий отчет
несколько задач пришли подряд
```

### Как должно быть

```text
Telegram update
→ валидация пользователя
→ создание job
→ ответ “Принято, job_id: ...”
→ worker выполняет задачу
→ бот присылает изменения статуса
```

Polling-процесс не должен выполнять бизнес-сценарии.

---

## 5. Есть риск повторной обработки Telegram-команд

Offset записывается только после обработки всего полученного batch. Если процесс завершится после выполнения одного сообщения, но до `_write_state()`, Telegram отдаст тот же update повторно после рестарта. Для read-only отчета это неприятно, но допустимо. Для apply-операции это критично. ([GitHub][11])

Кроме того, `poll_loop()` перехватывает только `TelegramRunnerError`. Неожиданное исключение в dispatcher или бизнес-функции завершит polling-процесс; systemd его перезапустит, но batch может обработаться повторно. ([GitHub][11])

### Нужно добавить

```text
telegram_updates:
- update_id UNIQUE
- chat_id
- command
- received_at
- job_id
- processing_status
```

Алгоритм:

```text
получили update_id
→ атомарно зарегистрировали
→ если уже существует, пропустили
→ создали job
→ зафиксировали offset
→ начали выполнение job
```

---

## 6. Для кнопок пока нет транспортной основы

Polling получает только:

```text
message
edited_message
```

Но дерево inline-кнопок Telegram работает через `callback_query`. Сейчас этого update-типа и его маршрутизации нет. ([GitHub][11])

Это не ошибка текущего read-only MVP, но следующий Telegram-слой нужно проектировать уже вокруг:

```text
callback_query
menu state
parameter state
confirmation state
job status
```

Переход на `aiogram` необязателен. Текущий stdlib-клиент можно оставить, если транспорт разделить на небольшие модули.

---

## 7. Safety metadata содержит явную ошибку

`reviews-questions-apply` меняет состояние Ozon/WB, требует подтверждения, но имеет:

```text
risk="low"
```

Это неправильно. Также тест разрешает apply-задачам риск только из множества `{"low", "high"}`, фактически закрепляя ошибочную классификацию. ([GitHub][3])

Должно быть:

```text
индивидуальный подтвержденный ответ: normal
массовая публикация ответов: high
```

И общий invariant:

```python
if task.mode == "apply":
    assert task.requires_confirmation
    assert task.risk in {"normal", "high"}
    assert task.source_plan_task
    assert task.verify_task
    assert task.lock_keys
```

---

## 8. Approval защищает от повторного apply, но не полностью атомарно

Сейчас логика выглядит примерно так:

```text
assert_apply_not_repeated()
→ внешняя операция в Ozon/WB
→ mark_approved_applied()
```

Между проверкой и записью marker два процесса теоретически могут одновременно начать один и тот же apply. Простой marker после выполнения не закрывает эту race condition. ([GitHub][6])

### Правильная схема

В одной транзакции:

```text
approved → applying
```

Только процесс, которому удалось атомарно занять approval, выполняет внешнюю операцию.

После этого:

```text
applying → applied → verified
```

Если процесс упал после внешнего API-вызова:

```text
applying_unknown
```

Далее отдельный verify-сценарий определяет, было ли изменение фактически применено.

---

## 9. Старый `file_lock()` небезопасен

Сейчас используется:

```text
if lock_path.exists():
    error

lock_path.write_text(...)
```

Это классическая race condition: два процесса могут одновременно увидеть отсутствие файла и оба создать его. Нет PID, `run_id`, TTL и восстановления stale lock. ([GitHub][12])

В `WorkflowRunner` уже используется более нормальный `fcntl.flock`, и именно этот подход стоит централизовать. ([GitHub][10])

Нужны resource locks:

```text
wb_lk
ozon_lk
wb_prices_write
wb_ads_write
wb_cards_write
ozon_prices_write
ozon_ads_write
ozon_cards_write
reviews_apply
```

---

## 10. Маскирование секретов пока недостаточное

`RunManifest` маскирует значения, если имя ключа содержит `token`, `password`, `secret` и т. п. Это полезно, но значение секрета может находиться под нейтральным ключом. ([GitHub][5])

`_safe_error()` просто заменяет в тексте слова-маркеры вроде `token` или `secret`. Он не маскирует само известное значение API-ключа, если оно попало в exception message без такого слова. ([GitHub][10])

### Лучше использовать

1. Allowlist полей, разрешенных в `inputs`.
2. Реестр известных секретных значений из загруженной конфигурации.
3. Маскирование этих значений во всех исключениях и логах.
4. В Telegram — только код ошибки и безопасное описание.
5. Полный traceback — только в локальном логе с правами `0600`.

---

## 11. `telegram_runner.py` и `commands.py` становятся монолитными

GitHub показывает:

```text
commands.py — 546 строк
telegram_runner.py — около 593 строк
```

В одном Telegram runner сейчас находятся:

```text
загрузка токена
HTTP API
multipart
отправка текста
отправка файлов
polling
offset state
process lock
attachment policy
dispatch
logging
```

Это пока работает, но с callback-кнопками, FSM и очередью файл быстро станет неудобным. ([GitHub][7])

Разделить:

```text
bot/api_client.py
bot/polling.py
bot/router.py
bot/menus.py
bot/states.py
bot/attachments.py
bot/security.py
bot/notifications.py
```

---

## 12. В бизнес-правилах появляются hardcoded данные

В runbook зафиксирована себестоимость `85 рублей за единицу`. Там же находятся реальные объемы каталогов и результаты owner review. ([GitHub][13])

Себестоимость нельзя делать постоянным default в коде или runbook. Нужен справочник:

```text
product_costs:
internal_product_id
cost
currency
valid_from
valid_to
source
approved_by
```

И каждый отчет должен сохранять использованный cost snapshot. Иначе изменение себестоимости изменит смысл старых расчетов.

---

## 13. Репозиторий публичный, хотя README утверждает обратное

GitHub показывает репозиторий как Public, а README содержит `visibility: PRIVATE`. При этом в runbook находятся коммерческие правила, себестоимость, размеры каталогов, ассортиментные решения и детальная операционная логика. ([GitHub][8])

После ревью репозиторий рационально вернуть в private.

Также `.env.example` все еще содержит устаревшее замечание, что package называется `takterra_agent`, хотя переименование уже выполнено. ([GitHub][14])

---

## 14. Packaging и CI пока слишком слабые

`pyproject.toml` содержит только минимальный `[project]`, одну зависимость `openpyxl>=3.1` и pytest-настройки. Нет:

```text
build-system
console script
dev dependencies
ruff
mypy
coverage
dependency lock
```

В корне репозитория также не видно CI workflow. ([GitHub][15])

Для проекта, который будет менять цены, ставки и карточки, CI обязателен хотя бы в минимальном виде:

```text
compileall
pytest
ruff
secret scan
registry policy validation
```

---

# Что можно пока оставить

Не нужно сейчас:

1. Переезжать на Django.
2. Поднимать PostgreSQL.
3. Немедленно переписывать Telegram на `aiogram`.
4. Переписывать готовые Ozon/WB task-функции.
5. Делать web-панель.
6. Добавлять все 44 бизнес-процесса в кнопки.

Для одного VPS приемлемы:

```text
Python
SQLite
systemd user services
файловые artifacts
текущий Telegram API transport
```

Но authoritative state должен перейти из JSONL/CSV в SQLite.

---

# Рекомендуемая целевая архитектура

```text
Telegram UI
    ↓
Bot Router / FSM
    ↓
JobService.submit()
    ↓
SQLite Job Store
    ↓
Worker
    ↓
TaskRegistry
    ↓
SafetyPolicy
    ↓
Executor
    ├─ ScriptExecutor
    ├─ AgentExecutor
    └─ HybridExecutor
    ↓
ResultValidator
    ↓
Artifacts + Final RunManifest
    ↓
Telegram Notifier
```

---

# План дальнейших шагов

## Этап 1. Зафиксировать и очистить текущую основу

Сейчас:

1. Вернуть repository в private.
2. Исправить README: фактическая visibility.
3. Удалить устаревшее упоминание `takterra_agent`.
4. Унифицировать абсолютный путь проекта.
5. Исправить риск `reviews-questions-apply`.
6. Изменить registry-тест: apply не может быть `low`.
7. Добавить CI.
8. Не добавлять новые write-команды до следующих этапов.

---

## Этап 2. Ввести Job Store на SQLite

Создать сущности:

```text
jobs
job_events
task_requests
approvals
resource_leases
telegram_updates
```

Статусы job:

```text
created
queued
running
waiting_confirmation
success
partial_success
failed
timeout
cancelled
```

Каждый запрос, включая blocked и failed, должен иметь `job_id`.

Структура файлов:

```text
runtime/jobs/<job_id>/
├─ input.json
├─ status.json
├─ result.json
├─ report.md
├─ stdout.log
├─ stderr.log
└─ artifacts/
```

---

## Этап 3. Сделать `TaskRegistry v2`

Добавить поля:

```text
executor: script / agent / hybrid
handler
parameter_schema
result_schema
timeout_seconds
lock_keys
source_plan_task
verify_task
prompt_template
telegram_menu_path
enabled
supports_cancel
```

Ввести автоматическую валидацию реестра:

```text
apply требует confirmation
apply требует source dry-run
apply требует verify
apply требует lock
agent требует prompt_template
telegram task требует menu metadata
```

После этого убрать отдельный `default_workflow_handlers()`.

---

## Этап 4. Сделать единый `JobRunner`

Интерфейс:

```python
submit(task_id, params, actor)
run(job_id)
cancel(job_id)
retry(job_id)
get_status(job_id)
```

На первом этапе реализовать только `ScriptExecutor` и обернуть существующие функции:

```text
status-preflight
daily-morning-report
catalog-fetch
build-unified-catalog
wb-promotion-report
reviews-questions dry-run
```

CLI и Telegram должны запускать их через один `JobService`.

---

## Этап 5. Завершить safety-контур

Нужно реализовать:

```text
атомарное reserve approval
payload checksum
idempotency key
resource lock
fresh-data limit
drift check
apply marker
post-apply verify
crash recovery
```

Критический сценарий:

```text
процесс отправил запрос в WB/Ozon
но упал до сохранения локального результата
```

Система должна уметь не повторить действие вслепую, а сначала выполнить verify.

---

## Этап 6. Переделать Telegram из исполнителя в диспетчер

Telegram должен:

```text
1. Принять команду или callback.
2. Собрать параметры.
3. Создать job.
4. Сразу вернуть job_id.
5. Показывать статус.
6. Позволять отменить queued/running задачу.
7. Прислать итоговый отчет.
```

Добавить:

```text
callback_query
inline keyboards
menu state
parameter state
confirmation state
/update deduplication
/history
/job_<id>
/cancel_<id>
```

Существующие `/status`, `/today`, `/runs` сохранить как диагностические команды.

---

## Этап 7. Добавить одноразовых Codex-агентов

Только после готовности job/approval/runtime.

Контракт `AgentExecutor`:

```text
один job
одна рабочая папка
один prompt.final.md
ограниченный список разрешенных действий
ограниченный набор credentials
timeout
stdout/stderr
result.json
report.md
screenshots/downloads
обязательное завершение процесса
```

Не использовать tmux как источник состояния задачи. Tmux можно оставить для наблюдения и отладки, но статус должен храниться в Job Store.

Агент-консультант должен остаться отдельным постоянным контуром без права выполнять apply напрямую.

---

## Этап 8. Порядок включения бизнес-процессов

Первый набор:

```text
1. System healthcheck
2. Status preflight
3. Daily report
4. WB promotion analytics
5. Текущие цены и скидки
6. Promo 70-55-55 dry-run
7. Reviews/questions drafts
8. История запусков
```

Затем провести контролируемый тест write-контура:

```text
одна строка
один approved package
один apply
один verify
```

Только после успешной отработки crash recovery и защиты от двойного запуска подключать:

```text
ставки
цены
скидки
карточки
поставки
массовые ответы
```

---

# Критерии готовности к write-кнопкам

Write-кнопки нельзя включать, пока не выполнено всё:

```text
[ ] любое нажатие создает job_id
[ ] повторный Telegram update не создает второй apply
[ ] один approval невозможно применить дважды
[ ] approval привязан к checksum действий
[ ] параллельные операции блокируются по ресурсу
[ ] crash после API-вызова восстанавливается через verify
[ ] blocked/error/timeout сохраняются в истории
[ ] секреты не попадают в Telegram и manifest
[ ] результат валидируется по схеме
[ ] все проверки запускаются в CI
```

## Итог

**Что пойдет:** marketplace-модули, бизнес-задачи, runbook-и, тесты, каталог, approval checksums, read-only Telegram MVP, systemd-контур.

**Что сейчас неправильно:** два источника dispatch-логики, неатомарный JSONL index, отсутствие job queue, синхронное выполнение внутри poller, слабая дедупликация Telegram, race condition approval/apply, старый file lock и ошибочная классификация риска.

**Главный следующий шаг:** не добавлять очередной процесс Ozon/WB, а сделать `SQLite Job Store + TaskRegistry v2 + единый JobRunner`. После этого текущий код станет устойчивой платформой, а не растущим набором хорошо документированных, но по-разному запускаемых сценариев.

[1]: https://github.com/pavelvital2/seller_vital_shevron/commits/main/ "Commits · pavelvital2/seller_vital_shevron · GitHub"
[2]: https://github.com/pavelvital2/seller_vital_shevron/tree/main/src/seller_agent "seller_vital_shevron/src/seller_agent at main · pavelvital2/seller_vital_shevron · GitHub"
[3]: https://raw.githubusercontent.com/pavelvital2/seller_vital_shevron/main/src/seller_agent/tasks/registry.py "raw.githubusercontent.com"
[4]: https://github.com/pavelvital2/seller_vital_shevron/blob/main/tests/test_task_registry.py "seller_vital_shevron/tests/test_task_registry.py at main · pavelvital2/seller_vital_shevron · GitHub"
[5]: https://github.com/pavelvital2/seller_vital_shevron/blob/main/src/seller_agent/core/run_manifest.py "seller_vital_shevron/src/seller_agent/core/run_manifest.py at main · pavelvital2/seller_vital_shevron · GitHub"
[6]: https://github.com/pavelvital2/seller_vital_shevron/blob/main/src/seller_agent/safety/approvals.py "seller_vital_shevron/src/seller_agent/safety/approvals.py at main · pavelvital2/seller_vital_shevron · GitHub"
[7]: https://github.com/pavelvital2/seller_vital_shevron/blob/main/src/seller_agent/bot/commands.py "seller_vital_shevron/src/seller_agent/bot/commands.py at main · pavelvital2/seller_vital_shevron · GitHub"
[8]: https://github.com/pavelvital2/seller_vital_shevron "GitHub - pavelvital2/seller_vital_shevron · GitHub"
[9]: https://github.com/pavelvital2/seller_vital_shevron/tree/main/tests "seller_vital_shevron/tests at main · pavelvital2/seller_vital_shevron · GitHub"
[10]: https://github.com/pavelvital2/seller_vital_shevron/blob/main/src/seller_agent/core/workflow_runner.py "seller_vital_shevron/src/seller_agent/core/workflow_runner.py at main · pavelvital2/seller_vital_shevron · GitHub"
[11]: https://github.com/pavelvital2/seller_vital_shevron/blob/main/src/seller_agent/bot/telegram_runner.py "seller_vital_shevron/src/seller_agent/bot/telegram_runner.py at main · pavelvital2/seller_vital_shevron · GitHub"
[12]: https://github.com/pavelvital2/seller_vital_shevron/blob/main/src/seller_agent/safety/locks.py "seller_vital_shevron/src/seller_agent/safety/locks.py at main · pavelvital2/seller_vital_shevron · GitHub"
[13]: https://raw.githubusercontent.com/pavelvital2/seller_vital_shevron/main/data/planning/catalog_mapping_runbook.md "raw.githubusercontent.com"
[14]: https://raw.githubusercontent.com/pavelvital2/seller_vital_shevron/main/.env.example "raw.githubusercontent.com"
[15]: https://raw.githubusercontent.com/pavelvital2/seller_vital_shevron/main/pyproject.toml "raw.githubusercontent.com"
