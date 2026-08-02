# Stage 3 owner control bot and Mini App runbook

Дата: 2026-08-02

Статус: функциональный и security production gates завершены. Постоянный
system-level service установлен с полным hardening; временный user-service
выключен.

Дополнение Stage 4A разрабатывается отдельным additive пакетом и не меняет
исторический deployment contract Stage 3. Актуальные read-only
`operations/summary`, owner-scoped `jobs`, unresolved `approvals` и второй
allowlisted task описаны в
`data/planning/stage4a_readonly_control_runbook.md`.

## Итог первого slice

Новый bot и Mini App являются дополнительным owner-only интерфейсом над
существующим runtime-контуром:

```text
Telegram raw initData
-> localhost aiohttp Control API
-> TaskRegistry / JobService
-> общий runtime/runtime.db
-> единственный существующий Job Worker
-> store-analytics-overview read-only handler
-> server-created control_bot notification route
```

Control service не запускает workflow и не читает marketplace credentials. Он
валидирует Telegram owner, создаёт только allowlisted read-only job и читает
owner-scoped статус по opaque `cpj_*` ID. Marketplace/Parser источники читает
только существующий Job Worker.

Старый bot, его token, state, lock, service и команды не меняются. Второй Job
Worker не создаётся.

## HTTP contract

Backend слушает только `127.0.0.1:8092`. Целевой same-origin prefix:

```text
/vital-shevron/
/vital-shevron/api/v1/health
/vital-shevron/api/v1/ready
/vital-shevron/api/v1/auth/telegram
/vital-shevron/api/v1/session
/vital-shevron/api/v1/jobs
/vital-shevron/api/v1/jobs/{opaque_job_id}
```

`POST /auth/telegram` принимает JSON ровно с одним полем `init_data`. Это raw
`Telegram.WebApp.initData`; `initDataUnsafe` не используется. Backend:

1. разбирает параметры без duplicate keys;
2. проверяет HMAC-SHA256 по Telegram `WebAppData` contract через
   `hmac.compare_digest`;
3. проверяет `auth_date` (TTL 300 секунд, future skew 30 секунд);
4. валидирует JSON user и exact owner allowlist;
5. атомарно consumes SHA-256 fingerprint в общей SQLite, не сохраняя raw data,
   query_id или user payload;
6. выдаёт короткую signed session cookie
   `__Host-vs_control_session; HttpOnly; Secure; SameSite=Strict; Path=/` и
   отдельный CSRF token.

Session signing secret хранится отдельным 0600-файлом и domain-separated от
bot token. Ни bot token, ни initData, ни cookie, ни CSRF не логируются.

`POST /jobs` требует session, CSRF и `Idempotency-Key`. Разрешён только
`store-analytics-overview` с `{marketplace: ozon|wb, period_days: 7|30|90,
region_id}`. Допустимые регионы:

- WB: `moscow`, `rostov-on-don`, `novosibirsk`, `kazan`;
- Ozon: `moscow`, `rostov-on-don`.

`query_pack_id=shevron-core` и ownership filters добавляет только backend;
frontend не может передать или заменить их.
Одинаковый owner/key/payload после рестарта возвращает тот же job; тот же key с
другим payload возвращает conflict. Write/dry-run/verify и произвольные task ID
fail closed до создания job.

`GET /jobs/{opaque_job_id}` ищет точное соответствие owner + opaque ID. Из
ответа удаляются artifacts/raw/inputs, secret-like keys и bearer/token-like
строки. Runtime job ID и actor не являются public contract.

Body ограничен 32 KiB, headers — 64 полями/16 KiB. CORS не включён. Backend
использует фактический socket peer и не доверяет `Forwarded` или
`X-Forwarded-*`; Nginx contract очищает их.

## SQLite additions

`JobStore.initialize()` additive создаёт:

- `control_auth_replays` — durable initData fingerprint consumption;
- `control_request_receipts` — owner-scoped idempotency и opaque job mapping;
- `job_notification_routes` — server-created route на новый bot token.

Существующие jobs/approvals/Telegram updates не меняются. Migration marker:
`control_plane_schema_v1`. Старый код игнорирует новые таблицы; rollback не
требует удаления таблиц.

## Analytics source contract

`store-analytics-overview` оркестрирует существующие read-only функции:

- текущий и предыдущий `marketplace-period-report`;
- `ozon-stock-supply-monitor` или `wb-stock-supply-monitor`;
- Parser Data API `/summary` и
  `/aggregates/store-period-comparison` с exact ownership filter.

Server-side ownership неизменяем frontend:

```text
WB supplier_id = 4516781
Ozon seller_slug = vital-shevron
Parser query_pack_id = shevron-core
```

Sales-период и Parser snapshot dates — разные временные оси. Даты
`marketplace-period-report` нельзя передавать как даты SERP-срезов.

WB использует auto latest pair `/warehouse/wb/aggregates/store-period-comparison`
без sales-дат, но с явными `previous/current_region_id` и
`previous/current_query_pack_id`; для WB разрешён `query_scope=intersection`.

Ozon `store-period-comparison` требует две фактически существующие даты и не
принимает `query_scope=intersection`. Backend получает bounded список
кандидатов из unfiltered `/warehouse/ozon/run-quality` (этот live source не
поддерживает region/query-pack filters), затем подтверждает максимум 32 даты
короткими `/warehouse/ozon/query-positions?limit=1` с exact region и pack.
Только две последние подтверждённые даты передаются в comparison с
`query_scope=union`. Один подтверждённый Ozon snapshot означает
`parser_comparison_unavailable`, пустой movement и warning; он не считается
ростом.

Live ответы обоих marketplaces используют `movement` с `*_pairs`. WB также
возвращает `previous/current`, а Ozon может возвращать только
`visible_products`, `query_count`, `total_rows`. Нормализатор поддерживает эти
реальные формы и не синтезирует отсутствующий previous snapshot.

Каждый источник изолирован: failure становится `data_available=false` и
безопасным reason code, не скрывая доступные другие источники. Parser older
48 часов маркируется `stale`, а timestamp более чем на 5 минут в будущем —
`unknown`, без ложного подтверждения свежести. Сводка не выдаёт net payout за настоящую маржу:
при отсутствии себестоимости `margin.data_available=false`, а `net_before_cogs`
показывается только как явно названный proxy.

Mini App показывает текущие продажи/заказы/выручку/возвраты, их изменение к
предыдущему равному периоду и отдельно маркирует маржу как `data unavailable`,
если подтверждённой себестоимости нет. Warning результата не маскируется под
ошибку транспорта.

Artifacts и RunManifest создаются штатным read-only handler. Никакого
marketplace write, approval или apply callback этот task не содержит.

## Bot и notifier

Новый polling принимает только private message exact owner. `/start` и любое
сообщение владельца показывают одну HTTPS Web App button; бизнес-команд нет.
Перед polling `getWebhookInfo` обязан подтвердить пустой webhook URL.

State/lock изолированы:

```text
.sessions/telegram/vital_shevron_control_bot_state.json
.sessions/telegram/vital_shevron_control_bot.lock
```

Control API атомарно создаёт notification route вместе с job. Общий notifier
для такого route загружает только новый control token; отсутствие token не
может fallback на старый token. Для jobs без route поведение старого bot
остаётся прежним. Успешный route помечается `sent` и повторно не уведомляет.

Control notification — best-effort дополнение к durable результату job, а не
условие его доступности. Ошибка загрузки control token или отправки помечает
server-created route как `failed`; terminal job и его owner-scoped результат
остаются доступны в Mini App. Fallback на token старого bot запрещён. В первом
Stage 3 slice автоматического retry notification нет.

Отправка текста и разрешённых report attachments состоит из нескольких
Telegram-вызовов. Частичный успех сохраняется как `failed` и не заявляется как
exactly-once: при будущей явной ручной повторной отправке уже доставленная часть
может быть доставлена снова. Отдельный retry daemon в этот этап не входит.

## Frontend security

Frontend — локальные vanilla HTML/CSS/JS без build. Единственный runtime
external script — официальный
`https://telegram.org/js/telegram-web-app.js`. Иконки vendored как SVG sprite
с Lucide ISC/MIT notice. Dynamic UI использует `textContent`, `createElement`
и `replaceChildren`; `innerHTML`, `eval`, inline handlers/styles и browser
secret storage отсутствуют.

CSP не содержит `unsafe-inline`/`unsafe-eval`. UI mobile-first, учитывает
Telegram theme/safe-area и показывает состояния загрузки, ошибки, пустого
результата и устаревших либо недоступных данных. В исходном Stage 3 `Задания`
и `Согласования` были placeholders. Stage 4A активирует их только безопасными
read-only projections; write controls по-прежнему отсутствуют.

Backend задаёт `Cache-Control: no-store` для index и каждого локального asset:
`app.js`, `app.css`, `icons.svg`. Это исключает использование устаревшего
bundle в Telegram WebView после rollout.

Видимый owner copy — на русском; внутренние status/reason codes в JSON остаются
стабильными английскими identifiers, но UI отображает их через русские labels.
Generated Markdown overview и control-bot notification применяют тот же
fail-closed принцип: только известные status/marketplace/region получают
фиксированные русские labels, неизвестные динамические значения не выводятся.
UI не использует decorative gradients, viewport-scaled font size или ненулевой
`letter-spacing`; радиус карточек, controls и navigation не превышает 8 px.
Исключение — круглый status dot.

## Deployment artifact и production gate

- `deploy/systemd/system/vital-shevron-control-plane.service.example`;
- `deploy/nginx/vital-shevron-control-plane.location.conf.example`;
- `deploy/env/vital-shevron-control-plane.env.example`.

Функциональный production gate 2026-08-02 до security cutover подтвердил:

- owner URL `https://83328.koara.live/vital-shevron/` доступен;
- control service вышел в active и восстановился после restart;
- реальный read-only Ozon Moscow 7-day job завершился `partial_success`;
- server-created control notification route перешёл в `sent`;
- старый bot, единственный Job Worker и Ozon keeper остались active;
- marketplace write не выполнялся.

Первичный security gate временного user-service выявил, что его MainPID
разделял общий host mount namespace, поэтому `ProtectHome=tmpfs` и
`Bind*Paths` фактически не изолировали процесс. Удаление `PrivateDevices`,
`ProtectClock`, `ProtectKernelLogs` и `ProtectKernelModules` устраняло
`218/CAPABILITIES` в user manager systemd 255, но являлось только
диагностическим compatibility workaround и не допускается как постоянный
hardening contract.

Постоянный system-level unit установлен как
`/etc/systemd/system/vital-shevron-control-plane.service`. Он запускает процесс
с `User=pavel` и `Group=pavel`, а system manager создаёт namespace до сброса
привилегий. Unit сохраняет полный исходный hardening, включая
`PrivateDevices=true`, `ProtectClock=true`, `ProtectKernelLogs=true`,
`ProtectKernelModules=true`, `ProtectHome=tmpfs`, `ProtectSystem=strict`,
selective binds и `NoNewPrivileges=true`. User-level example удалён, чтобы не
оставлять двусмысленного или ослабленного deployment path.

Фактический env `deploy/env/vital-shevron-control-plane.env` должен иметь mode
`0600` и exact запись в `.gitignore`; example остаётся tracked без значений
секретов.

Завершённый system-unit security gate подтвердил:

1. system-level service active и enabled, временный user-service disabled и
   inactive;
2. MainPID использует отдельный от host mount namespace, `/home` представлен
   отдельным `tmpfs`;
3. project, shared Python и два control secret files доступны только через
   selective binds; `runtime` и `.sessions/telegram` доступны на запись;
4. старый bot token и Parser env недоступны, Ozon/WB session directories
   заменены inaccessible paths с mode `000`;
5. после cutover реальный read-only WB Moscow 7-day job завершился
   `partial_success`, а Parser freshness подтверждена как fresh;
6. server-created control notification route перешёл в `sent`;
7. system service восстановился после restart, публичный `/ready` вернулся в
   рабочее состояние;
8. старый bot, единственный Job Worker и Ozon keeper остались active;
9. marketplace write не выполнялся.

## Rollback

Rollback Stage 3 первого slice:

1. stop/disable только system-level `vital-shevron-control-plane.service`;
2. удалить только `/vital-shevron/` Nginx location и reload Nginx после
   config test;
3. control state/lock можно архивировать; additive SQLite tables можно
   оставить;
4. старый bot, Job Worker, runtime DB business rows и marketplace state не
   трогать.

## Offline gates

```bash
python -m pytest -q tests/test_stage3_*.py tests/test_store_analytics_overview.py
VITAL_SHEVRON_RUN_PARSER_LIVE_SMOKE=1 python -m pytest -q tests/test_stage3_parser_live_smoke.py
python -m seller_agent.cli tasks policy
python -m compileall -q src tests
git diff --check
```

Live marketplace/API/LK, Telegram token, systemctl и Nginx команды не входят
в implementation checkpoint. Opt-in smoke выше обращается только к read-only
localhost Parser Data API и использует mocked marketplace sales/stock sources.
