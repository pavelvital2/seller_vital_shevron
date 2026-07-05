# Telegram Bot MVP Runbook

Дата актуализации: 2026-06-18

## Итог

Telegram MVP - это первый безопасный слой будущего бота. Он использует
`TaskRegistry`, `RunManifest` и runtime-артефакты проекта. Базовые команды
остаются read-only, а write-операции подключаются только точечно через
отдельный approval/callback flow.

Текущая реализация подключает безопасный Telegram adapter поверх того же
command layer. Adapter умеет отправить preview-ответ, один раз обработать
входящие updates через Telegram Bot API, работать в controlled polling loop,
обрабатывать `callback_query` и прикреплять безопасный файл отчета из
`artifacts`. Live read-only `/today` и `/status` по умолчанию запускаются через
`WorkflowRunner` для совместимости. С 2026-06-30 добавлен опциональный режим
`--runtime-jobs`: polling ставит `/today` и `/status` в SQLite `JobStore`,
дедуплицирует `telegram_updates.update_id` и сразу возвращает `job_id`; запуск
очереди и отправка результата выполняются отдельной командой
`bot run-job-next` или будущим worker/timer.

С 2026-06-30 подключены точечные write-кнопки: Ozon Elastic и WB акции
`70-55-55`. Команды `/elastic` и `/wb-actions` строят fresh dry-run,
отправляют отчет и inline-кнопку применения. Нажатие кнопки является явным
подтверждением владельца только для показанного `plan_run_id`; apply
выполняется существующими контурами `apply-ozon-elastic` и
`apply-wb-actions-discounts` с fresh-check, partial drift-check, verify и
idempotency guard.

Токен бота не хранится в проекте. Если токен был отправлен в чат или попал в
логи, считать его засвеченным и перевыпустить через BotFather перед
production-запуском.

## Где находится

```text
src/seller_agent/bot/commands.py
src/seller_agent/bot/dispatcher.py
src/seller_agent/bot/job_notifier.py
src/seller_agent/bot/runtime_jobs.py
src/seller_agent/bot/telegram_runner.py
src/seller_agent/core/job_service.py
src/seller_agent/core/job_store.py
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

Опциональный async-runtime режим для live `/today` и `/status`:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli bot poll-loop \
  --live-today \
  --live-status \
  --runtime-jobs \
  --allowed-chat-id 123456789 \
  --token-file /home/pavel/.secrets/vital_shevron_telegram_bot_token
```

В этом режиме Telegram polling не строит отчет внутри процесса polling, а
создает job:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli jobs list

PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli bot run-job-next \
  --token-file /home/pavel/.secrets/vital_shevron_telegram_bot_token
```

`bot run-job-next` выполняет первый queued job и, если job была создана из
Telegram update, отправляет итоговый Telegram-summary в исходный chat/thread и
прикрепляет безопасный `report`-файл через тот же attachment policy.
`bot run-job-loop --max-iterations N` выполняет тот же контур в управляемом
loop-режиме и останавливается на пустой очереди.

Systemd user service template:

```text
deploy/systemd/user/vital-shevron-telegram-bot.service
deploy/systemd/user/vital-shevron-telegram-job-worker.service
deploy/systemd/user/vital-shevron-telegram-job-worker.timer
```

Service должен включаться только после:

- token-file создан вне проекта;
- личный `chat_id` подтвержден через `poll-once`;
- `.sessions/telegram/vital_shevron_telegram_bot.env` содержит allowlist;
- `poll-loop --max-iterations 1` прошел без ошибок.
- `bot run-job-loop --max-iterations 1` прошел smoke на тестовой или пустой
  очереди.

`vital-shevron-telegram-job-worker.timer` пока не включать автоматически без
отдельного согласования владельца. Сначала нужно проверить, что live polling
создает job через `--runtime-jobs`, worker выполняет ее, а Telegram получает
summary и безопасный report-файл.

## Поддерживаемые команды

- `/help` - список доступных read-only экранов.
- `/status` - при `--live-status` строит свежий read-only `status-preflight`;
  при дополнительном `--runtime-jobs` ставит `status-preflight` в SQLite
  очередь и сразу возвращает `job_id`; без `--live-status` показывает
  последний `status-preflight` из `data/runs/index.jsonl`.
- `/today` - при `--live-today` строит свежий read-only
  `daily-morning-report --seller-v3`; при дополнительном `--runtime-jobs`
  ставит `daily-morning-report` в SQLite очередь и сразу возвращает `job_id`;
  без `--live-today` показывает последний `daily-morning-report` из
  `data/runs/index.jsonl`.
- `/reviews` - последний `reviews-questions` из `data/runs/index.jsonl`.
- `/ozon-inbox` - строит свежий dry-run по Ozon отзывам/вопросам,
  Ozon Messenger и Ozon уведомлениям, прикрепляет report-файл и при наличии
  действий показывает inline-кнопку `Применить Ozon входящие`.
- `/wb-inbox` - строит свежий dry-run по WB отзывам и WB вопросам,
  прикрепляет report-файл и при наличии действий показывает inline-кнопку
  `Применить WB входящие`. WB новости/уведомления читаются read-only из ЛК
  `news-v2`; отчет показывает общее количество прочитанных новостей и
  выделяет важные. Mark-read для WB уведомлений пока не выполняется.
- `/approvals` - текущий обзор `approvals status`.
- `/jobs` - последние runtime job из SQLite `runtime/runtime.db`, статусы и
  команды для просмотра/отмены.
- `/job_<job_id>` - подробности runtime job и последние события.
- `/cancel_<job_id>` - отменяет только `created/queued` runtime job; running
  или завершенные job не отменяются.
- `/catalog` - последний `catalog-build-unified` из `data/runs/index.jsonl`;
  Telegram-summary показывает ключевые цифры unified catalog из безопасного
  `summary` artifact.
- `/catalog <запрос>` - read-only поиск товара в
  `data/catalog/unified/products.json` по `internal_sku`,
  `internal_product_id`, названию, Ozon `offer_id/product_id/sku/barcode`,
  WB `vendorCode/nmID/barcode`; barcode подтягивается из processed Ozon/WB
  catalog CSV, если эти файлы есть.
- `/elastic` - строит свежий dry-run Ozon Elastic, показывает summary и
  прикрепляет report-файл. Если есть строки к применению, добавляет
  inline-кнопку `Применить Ozon Elastic`.
- `/wb-actions` - строит свежий dry-run WB акций по схеме `70-55-55`,
  показывает summary, бизнес-причины изменения скидки и прикрепляет
  report-файл. Если есть строки к применению, добавляет inline-кнопку
  `Применить WB 70-55-55`.
- `/runs` - краткий список последних runtime-статусов по Telegram-задачам.

## Inline-кнопки и callback

Бот обрабатывает Telegram `callback_query` только для разрешенных chat_id.
Callback должен быть узким и безопасным. На 2026-06-30 поддерживается:

```text
oe_apply:<ozon_elastic_plan_run_id>
wba_apply:<wb_actions_discount_plan_run_id>
ozin_apply:<ozon_inbox_run_id>
wbin_apply:<wb_inbox_run_id>
```

Правила:

- Ozon Elastic callback принимает только `plan_run_id`, начинающийся с
  `ozon_elastic_plan_`;
- WB actions callback принимает только `plan_run_id`, начинающийся с
  `wb_actions_discount_plan_`;
- Ozon inbox callback принимает только `run_id`, начинающийся с
  `ozon_inbox_`;
- WB inbox callback принимает только `run_id`, начинающийся с `wb_inbox_`;
- нажатие кнопки = explicit owner approval для этого dry-run;
- Ozon Elastic apply запускается через
  `run_ozon_elastic_apply(..., confirmed_by_user=True)`;
- WB actions apply запускается через
  `run_wb_actions_discount_apply(..., confirmed_by_user=True)`;
- Ozon inbox apply создает runtime job `ozon-inbox-apply` через `JobService`
  с `confirmed_by_user=true`; `WorkflowRunner` применяет только пакет,
  сохраненный в `data/pending/<ozon_inbox_run_id>_pending/`;
- WB inbox apply создает runtime job `wb-inbox-apply` через `JobService` с
  `confirmed_by_user=true`; `WorkflowRunner` применяет только пакет,
  сохраненный в `data/pending/<wb_inbox_run_id>_pending/`;
- перед записью Ozon Elastic apply выполняет свежий scoped preflight только для
  Ozon API, новый dry-run, partial drift-check и verify;
- перед записью WB actions apply выполняет штатный WB preflight, новый dry-run
  по схеме утвержденного плана, partial drift-check `nmID + price + discount`,
  upload и verify через WB history/buffer;
- если часть строк изменилась, применяются только неизменившиеся строки, а
  drift-строки выводятся в отчет на новый review;
- повторный apply того же approved plan блокируется idempotency marker.

## Прикрепление файлов

После текстового Telegram-summary adapter отправляет `sendDocument`, если
команда вернула безопасный `report`-артефакт.

Разрешено прикреплять только:

- ключ `artifacts.report`;
- существующий файл внутри `data/runs/` или `data/reports/`;
- расширения `.md`, `.txt`, `.csv`, `.xlsx`, `.pdf`, `.html`;
- файл размером не больше 20 MB;
- путь без маркеров `token`, `secret`, `cookie`, `storage`, `auth`,
  `password`, `credential`.
- для `.html` дополнительно выполняется scan содержимого на unsafe-маркеры
  `api_key`, `api-key`, `authorization:`, `bot_token`, `client_secret`,
  `cookie`, `storage_state`.

Технические артефакты вроде `summary.json`, `manifest.json`, raw snapshots,
cookies, storage state и файлы вне разрешенных директорий не прикрепляются.
Если файл отчета не прошел фильтр, бот оставляет путь в тексте, но не должен
прикреплять подозрительный файл.

## Safety

- MVP не запускает прямые marketplace write-команды без callback/approval.
- MVP не создает универсальный approved package для всех операций; Ozon Elastic
  и WB actions пока используют `plan_run_id` как approved identity для
  существующих apply-контуров.
- MVP не отправляет ответы покупателям без inline callback/approval.
- MVP не меняет цены, акции, ставки, карточки, фото, остатки или поставки.
- Исключение: `/elastic` + inline-кнопка Ozon Elastic применяет только
  конкретный показанный Ozon Elastic dry-run через `apply-ozon-elastic` и
  штатный safety-контур.
- Исключение: `/wb-actions` + inline-кнопка WB actions применяет только
  конкретный показанный WB `70-55-55` dry-run через
  `apply-wb-actions-discounts` и штатный safety-контур.
- Исключение: `/ozon-inbox` + inline-кнопка Ozon inbox применяет только
  конкретный показанный пакет Ozon отзывов/вопросов/Messenger/уведомлений:
  публичные ответы, отметку просмотренных отзывов, ответы в Ozon Messenger и
  `mark-read` уведомлений.
- Исключение: `/wb-inbox` + inline-кнопка WB inbox применяет только конкретный
  показанный пакет WB отзывов и вопросов. WB новости/уведомления читаются
  read-only из `news-v2`; write/mark-read по ним пока не выполняется.
- MVP запускает из Telegram только live read-only `/today` и `/status`, если
  явно включены `--live-today` и `--live-status`. Остальные команды показывают
  уже сохраненные runtime-данные.
- При `--runtime-jobs` live `/today` и `/status` не выполняются внутри polling:
  Telegram update регистрируется в `telegram_updates`, повторный `update_id`
  не создает второй job, а выполнение переносится на `jobs run-next` /
  будущий worker.
- Постоянный polling требует allowlist и lock-file; второй экземпляр polling
  должен завершаться с ошибкой lock.
- После изменения кода Telegram-команд или задач, которые бот импортирует
  напрямую, нужно перезапустить `vital-shevron-telegram-bot.service`. Иначе
  polling-процесс продолжит работать со старым кодом в памяти, даже если
  файлы проекта уже исправлены.
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
