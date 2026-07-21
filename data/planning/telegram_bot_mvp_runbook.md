# Telegram Bot MVP Runbook

Дата актуализации: 2026-07-20

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
`WorkflowRunner` для совместимости preview. В production включен режим
`--runtime-jobs`: polling ставит все бизнес-операции в SQLite `JobStore`,
дедуплицирует message и callback по `telegram_updates.update_id` и сразу
возвращает `job_id`; выполнение и отправка результата делает отдельный Job
Worker. В polling остаются только меню, ввод и проверка параметров, локальный
просмотр `/jobs`, `/runs`, `/approvals`, `/catalog`, отмена queued job и
локальная фиксация решения по уже сформированному файлу `В работу`.

С 2026-06-30 подключены точечные write-кнопки: Ozon Elastic и WB акции
`70-55-55`. С 2026-07-05 добавлен второй Ozon-контур `Ozon все акции`.
С 2026-07-17 в WB-меню добавлена отдельная `Ручная акция`: бот принимает
три именованных параметра скидок, просит подтвердить их, строит fresh dry-run
и только после отдельного подтверждения конкретного плана выполняет apply.
Фиксированная кнопка `70-55-55` сохранена без изменений.
С 2026-07-17 в меню Ozon и Wildberries добавлен read-only
`Отчёт за период`: выбор краткого, финансового или полного отчёта, готовые
периоды и собственный диапазон с подтверждением перед чтением API. Task
`marketplace-period-report` сохраняет Markdown, Excel и JSON.
С 2026-07-18 в меню Wildberries добавлена read-only кнопка
`Остатки и поставки`. Команда `/wb-stock-supplies` запускает task
`wb-stock-supply-monitor`, получает свежие складские остатки и все активные
FBW-поставки, включая `statusID=3` (`Отгрузка разрешена`), и сохраняет
Markdown/CSV/JSON/RunManifest. Доступный остаток, поставки и приемка выводятся
раздельно; `inWayFromClient` не называется возвратами без сверки.
Команды `/elastic`, `/ozon-actions` и `/wb-actions` строят fresh dry-run,
отправляют отчет и inline-кнопку применения. Нажатие кнопки является явным
подтверждением владельца только для показанного `plan_run_id`; apply
выполняется существующими контурами `apply-ozon-elastic`,
`apply-ozon-actions-optimizer` и `apply-wb-actions-discounts` с fresh-check,
partial drift-check, verify и idempotency guard.

Многошаговый ввод `Ручной акции` хранит только безопасный временный stage в
polling state `.sessions/telegram/vital_shevron_bot_state.json`, раздельно по
`chat_id + thread_id`. Подтвержденные параметры передаются в callback, а apply
всегда привязан к созданному `plan_run_id`. Отмена ввода и отклонение dry-run
не вызывают marketplace write.
Тот же conversation-state механизм используется для двух дат собственного
периода. Polling сохраняет состояние как после message, так и после callback,
который переводит диалог к вводу даты.

С 2026-07-20 финальные callback-и отчетов, файлов `В работу`, dry-run и apply
не выполняют бизнес-логику внутри polling. Они только создают runtime job.
Через Worker проходят `marketplace-period-report`, Ozon/WB stock monitor,
production work plans, WB parser analytics, Ozon/WB inbox, Ozon Elastic,
Ozon all-actions, WB fixed/manual actions и все пять соответствующих apply
маршрутов. Write-job сохраняют профильные `source_plan_task`, approval
checksum, resource locks, fresh check и verify. Worker notifier возвращает
бизнес-сводку, безопасный `report` и следующую inline-кнопку.

С 2026-07-05 добавлен минимальный первый экран Telegram-бота через persistent
reply-клавиатуру. `/start` и `/menu` показывают кнопки `Статус`, `Помощь`,
`Общий вчерашний отчет`, `Озон`, `Вайлдберриз`. Эти кнопки только маршрутизируют
в существующие команды; сами по себе они не являются approval для write-действий.

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

С 2026-07-18 владелец согласовал включение Job Worker. Штатный bot unit должен
запускать polling с `--runtime-jobs --runtime-db runtime/runtime.db`, а
`vital-shevron-telegram-job-worker.timer` - раз в минуту запускать one-shot
worker. Перед первым включением обязательно проверить отсутствие старых
`created/queued/running/waiting_confirmation` write-job и approvals в
`applying/applying_unknown`. После установки выполнить read-only smoke:
polling создает `/status` job, worker выполняет ее, Telegram получает summary
и безопасный report-файл.

Фактическое включение 2026-07-18 подтверждено: timer `enabled/active`, основной
bot service `active` и использует `--runtime-jobs`; read-only smoke
`job_status-preflight_20260718T171356Z_126c9b38` завершился `success`, Worker
отправил владельцу Telegram-summary и report-документ. Перед включением
активная очередь и незавершенные runtime approvals были пусты.

## Поддерживаемые команды

- `/start` - показывает первый экран reply-клавиатуры.
- `/menu` - возвращает на первый экран reply-клавиатуры.
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
- `/ozon-actions` - строит свежий dry-run второго Ozon-контура `Ozon все
  акции`: сравнивает Elastic и доступные Ozon акции по товару, учитывает
  `min_price`, FBO-остаток и доступный boost%, показывает summary и
  прикрепляет report-файл. Если есть строки к применению, добавляет
  inline-кнопку `Применить Ozon акции`.
- `/wb-actions` - строит свежий dry-run WB акций по схеме `70-55-55`,
  показывает summary, бизнес-причины изменения скидки и прикрепляет
  report-файл. Если есть строки к применению, добавляет inline-кнопку
  `Применить WB 70-55-55`.
- `/wb-stock-supplies` - строит свежий read-only отчет по остаткам на складах,
  всем активным FBW-поставкам, приемке, физическим изделиям по `pack_qty` и
  аномалиям состояний WB. Ничего в WB не изменяет.
- `/runs` - краткий список последних runtime-статусов по Telegram-задачам.

## Reply-кнопки первого экрана

Первый экран создается через Telegram `ReplyKeyboardMarkup`, чтобы владелец мог
быстро запускать частые операции без ввода команд вручную.

Главная клавиатура:

```text
Статус | Помощь
Общий вчерашний отчет
Озон | Вайлдберриз
```

Маршрутизация кнопок:

- `Статус` -> `/status`;
- `Помощь` -> `/help`;
- `Общий вчерашний отчет` -> `/today`;
- `Озон` -> `/ozon`;
- `Вайлдберриз` -> `/wb`;
- `Назад` -> `/menu`.

Подменю Ozon:

```text
Ozon акции | Ozon эластик
Ozon входящие
Назад
```

Маршрутизация:

- `Ozon акции` -> `/ozon-actions`;
- `Ozon эластик` -> `/elastic`;
- `Ozon входящие` -> `/ozon-inbox`.

Подменю Wildberries:

```text
WB акции | Ручная акция
WB аналитика
Остатки и поставки
Отчёт за период WB
WB входящие
Назад
```

Маршрутизация:

- `WB акции` -> `/wb-actions`;
- `Ручная акция` -> `/wb-actions-manual`;
- `WB аналитика` -> `/wb-analytics`;
- `Остатки и поставки` -> `/wb-stock-supplies`;
- `Отчёт за период WB` -> `/period-report-wb`;
- `WB входящие` -> `/wb-inbox`.

Важно: reply-кнопка только отправляет текст команды от имени пользователя.
Она не подтверждает write-операцию. Подтверждением write остается только
inline-кнопка, показанная под конкретным fresh dry-run/report.

## WB аналитика

`/wb-analytics` - read-only команда. Она запускает task
`wb-parser-warehouse-analytics`, читает Parser Data API warehouse endpoints,
фильтрует данные Vital Shevron по `supplier_id=4516781`, сохраняет
производные CSV/Markdown/summary/RunManifest и показывает владельцу краткую
сводку:

- свежесть warehouse;
- количество наших товаров и запросов в выдаче;
- top-10/top-30/top-100;
- строки с остатком и без остатка;
- daily changes, missing и кандидаты с остатком вне top-30;
- путь к полному отчету и CSV кандидатов.

Команда не подтверждает и не выполняет write-операции. Для решений по карточкам
и продвижению ее результат нужно соединять с продажами, остатками, акциями и
ставками.

## WB остатки и поставки

`/wb-stock-supplies` запускает через `WorkflowRunner` read-only task
`wb-stock-supply-monitor`. Источники:

- `POST /api/analytics/v1/stocks-report/wb-warehouses`;
- `POST /api/v1/supplies`;
- `GET /api/v1/supplies/{supplyID}`;
- `GET /api/v1/supplies/{supplyID}/goods`.

Отчет показывает доступный `quantity` по складам, `inWayToClient`,
`inWayFromClient`, все активные статусы `1/2/3/4/6`, количество поставок и
товарных единиц по каждому статусу, ход приемки и физические изделия через
подтвержденный `pack_qty`. Каждый запуск сохраняет снимок для сравнения со
следующим и предупреждает о резкой переклассификации состояний при почти
неизменной общей товарной массе. Активные поставки не прибавляются к
доступному остатку.

## Inline-кнопки и callback

Бот обрабатывает Telegram `callback_query` только для разрешенных chat_id.
Callback должен быть узким и безопасным. На 2026-07-05 поддерживается:

```text
oe_apply:<ozon_elastic_plan_run_id>
oza_apply:<ozon_actions_optimizer_plan_run_id>
wba_apply:<wb_actions_discount_plan_run_id>
ozin_apply:<ozon_inbox_run_id>
wbin_apply:<wb_inbox_run_id>
```

Правила:

- Ozon Elastic callback принимает только `plan_run_id`, начинающийся с
  `ozon_elastic_plan_`;
- Ozon actions optimizer callback принимает только `plan_run_id`,
  начинающийся с `ozon_actions_optimizer_plan_`;
- WB actions callback принимает только `plan_run_id`, начинающийся с
  `wb_actions_discount_plan_`;
- Ozon inbox callback принимает только `run_id`, начинающийся с
  `ozon_inbox_`;
- WB inbox callback принимает только `run_id`, начинающийся с `wb_inbox_`;
- нажатие кнопки = explicit owner approval для этого dry-run;
- Ozon Elastic apply создает runtime job `ozon-elastic-apply` через
  `JobService` с `plan_run_id` и `confirmed_by_user=true`;
- Ozon actions optimizer apply запускается через
  `run_ozon_actions_optimizer_apply(..., confirmed_by_user=True)`;
- WB actions apply создает runtime job `wb-actions-discount-apply` через
  `JobService` с `plan_run_id` и `confirmed_by_user=true`;
- Ozon inbox apply создает runtime job `ozon-inbox-apply` через `JobService`
  с `confirmed_by_user=true`; `WorkflowRunner` применяет только пакет,
  сохраненный в `data/pending/<ozon_inbox_run_id>_pending/`;
- WB inbox apply создает runtime job `wb-inbox-apply` через `JobService` с
  `confirmed_by_user=true`; `WorkflowRunner` применяет только пакет,
  сохраненный в `data/pending/<wb_inbox_run_id>_pending/`;
- перед записью Ozon Elastic apply выполняет свежий scoped preflight только для
  Ozon API, новый dry-run, partial drift-check и verify;
- перед записью Ozon actions optimizer apply выполняет штатный Ozon preflight,
  новый dry-run, partial drift-check и verify; применять можно только строки
  из показанного `ozon_actions_optimizer_plan_*`;
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
  конкретный показанный Ozon Elastic dry-run через runtime job
  `ozon-elastic-apply` и штатный safety-контур.
- Исключение: `/ozon-actions` + inline-кнопка Ozon actions применяет только
  конкретный показанный dry-run второго Ozon-контура через
  `apply-ozon-actions-optimizer` и штатный safety-контур.
- Исключение: `/wb-actions` + inline-кнопка WB actions применяет только
  конкретный показанный WB `70-55-55` dry-run через runtime job
  `wb-actions-discount-apply` и штатный safety-контур.
- Исключение: `/ozon-inbox` + inline-кнопка Ozon inbox применяет только
  конкретный показанный пакет Ozon отзывов/вопросов/Messenger/уведомлений:
  публичные ответы, отметку просмотренных отзывов, ответы в Ozon Messenger и
  `mark-read` уведомлений.
- Исключение: `/wb-inbox` + inline-кнопка WB inbox применяет только конкретный
  показанный пакет WB отзывов и вопросов. WB новости/уведомления читаются
  read-only из `news-v2`; write/mark-read по ним пока не выполняется.
- При `--runtime-jobs` все API/LK операции, расчеты, dry-run и apply не
  выполняются внутри polling: Telegram update регистрируется в
  `telegram_updates`, повторный `update_id` не создает второй job, а Worker
  обрабатывает очередь FIFO и отправляет результат в исходный chat/thread.
- Навигация, ввод/валидация параметров, `/jobs`, `/job_*`, `/cancel_*`,
  локальный поиск каталога и локальное решение `утвердить/отклонить` для файла
  `В работу` остаются мгновенными и не обращаются к API маркетплейса.
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

## Ozon `Остатки и поставки`

Кнопка `Ozon -> Остатки и поставки Ozon` сразу запускает fresh read-only task
`ozon-stock-supply-monitor`. Telegram показывает общий FBO present,
свободный/зарезервированный остаток по складам, promised, активные supply-order
и подтвержденный inbound; полный Markdown прикрепляется как `report`.

Общий остаток, складской разрез и активные поставки не складываются. Команда
не требует approval, потому что ничего не меняет в Ozon.

## Ozon `В работу`

Кнопка `Ozon -> В работу Ozon` запускает диалог:

1. выбор `По объёму производства` или `По дням покрытия`;
2. ввод физической мощности или дней;
3. ввод количества кластеров назначения `1..20`;
4. review параметров и запуск fresh task `ozon-production-work-plan`;
5. Telegram summary и Excel `Артикулы / Ozon кластеры / Контроль`;
6. локальное решение `Утвердить в работу` или `Отклонить`.

Кластеры выбираются по убыванию чистой потребности. Продажи, свободный
остаток и confirmed inbound сопоставляются внутри одного кластера через
`/v2/cluster/list`; федеральные итоги не вычитаются из кластерного дефицита.
Ни формирование, ни локальное checksum-bound утверждение не создают поставку
и не выполняют write в Ozon.

## WB `В работу`

Кнопка `Вайлдберриз -> В работу` запускает диалог:

1. выбор `По объёму производства` или `По дням покрытия`;
2. ввод положительного целого значения;
3. ввод количества кластеров назначения `1..6`;
4. показ параметров и отдельное подтверждение запуска расчета;
5. fresh read-only task `wb-production-work-plan`;
6. Telegram summary и Excel с листами `Артикулы`, `ВБ регионы`, `Контроль`;
7. локальное решение `Утвердить в работу` или `Отклонить`.

В capacity mode число означает физические изделия, в coverage mode - дни.
Кластеры ранжируются по суммарной положительной физической потребности. Для
каждого товара расчет вычитает только доступный остаток товара и confirmed
inbound в том же кластере; общий остаток магазина не уменьшает локальный
дефицит.
Решение после просмотра Excel привязано к checksum файла. Ни формирование,
ни локальное утверждение не создают поставку и не выполняют write в WB.

## Ozon `Цены и маржа`

Кнопка `Ozon -> Цены и маржа Ozon` запускает read-only диалог:

1. выбор периода расходов `15` или `30` завершенных дней;
2. ввод себестоимости одного физического изделия;
3. ввод целевой маржи одного физического изделия;
4. постановка `ozon-pricing-margin` в Job Worker;
5. Telegram summary и Excel с расходами, ценовой сеткой и товарами.

В чат отдельно выводятся расходы Ozon на проданный товар/комплект и на одно
физическое изделие. Цена в кабинете не меняется; callback подтверждения apply
в первом варианте отсутствует.
