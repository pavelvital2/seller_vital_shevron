# Stage 4A read-only operational control runbook

Дата: 2026-08-02

Статус: развёрнуто в production 2026-08-02. Проверенный кодовый commit:
`311ec23c7389640acb04f09c1c4abeba12ddad57`.

Независимая приёмка, controlled fast-forward и production owner smoke
завершены. Старый Telegram-бот, Nginx, Job Worker, marketplace runtime и
существующие незакоммиченные карточные/ликвидационные изменения не менялись.

## Граница этапа

Stage 4A расширяет существующий owner-only Mini App только read-only
операционным контролем:

```text
owner session
-> localhost aiohttp Control API
-> server-side control task contract
-> JobService.submit_control_read_only()
-> общая runtime SQLite queue
-> единственный существующий Job Worker
```

Control service не вызывает `WorkflowRunner`, marketplace/Parser API, ЛК,
shell, systemd или network health checks. Он не имеет approve/reject/apply/
verify endpoint и не создаёт второй Worker или runtime DB.

## Control task contracts

Источник истины:

`src/seller_agent/control_plane/contracts.py`

Разрешены только два exact task ID:

| Task | Client params | Server-owned params | Result renderer |
|---|---|---|---|
| `store-analytics-overview` | exact `marketplace`, `period_days`, `region_id` | `wb_supplier_id`, `ozon_seller_slug`, `query_pack_id` | `store_analytics_overview_v1` |
| `daily-morning-report` | exact `{}` | empty set; handler использует проверенные backend defaults | `daily_report_status_v1` |

Оба контракта требуют exact `TaskRegistry` ID, `enabled=true`,
`is_read_only=true` и `is_write=false`. Alias, произвольный task ID,
неизвестное поле, client override server scope и зарегистрированный
write/dry-run/verify task блокируются до job insert.

`store-analytics-overview` сохраняет Stage 3 client contract. Утренний отчёт
ставится в общую очередь с пустыми params; действующий handler сам применяет
backend defaults `refresh_preflight=true`, `seller_v2=false`, `seller_v3=true`.
Frontend не может изменить эти значения.

## Read-only HTTP API

Все endpoint требуют действующую signed owner session:

```text
GET /vital-shevron/api/v1/operations/summary
GET /vital-shevron/api/v1/jobs?limit=20
GET /vital-shevron/api/v1/approvals
```

`operations/summary` читает только общую SQLite:

- количество active и terminal jobs;
- количество `pending_review` и `applying_unknown` approvals;
- последний receipt-backed analytics/daily job текущего owner;
- `data_available=false`, если snapshot нельзя подтвердить.

Сводка не объявляет marketplace, API, ЛК, systemd или сеть здоровыми.

`GET /jobs` принимает только один canonical `limit` от 1 до 30, default 20.
Он делает exact join owner receipt -> job и возвращает только opaque `cpj_*`,
нормализованный task ID, status и timestamps. Internal job ID, actor, params,
result, error, artifacts, paths и notification routing не входят в projection.

`GET /approvals` показывает только unresolved `pending_review` и
`applying_unknown`. Projection не содержит approval ID, `data_json`, checksum,
apply params, marketplace payload или source paths. `applying_unknown`
обязательно получает `requires_reconciliation=true`; action controls нет.

Ошибки API содержат только стабильный `error.code`. Stack trace и dynamic DB
text не возвращаются.

## Mini App

Четыре активных раздела:

- `Состояние` — SQLite summary, время обновления и создание queued
  `daily-morning-report`;
- `Аналитика` — прежние Ozon/WB, 7/30/90 и region controls;
- `Задания` — только owner-scoped control receipts;
- `Согласования` — безопасные counters/list без действий.

UI строит dynamic DOM только через `textContent`, `createElement` и
`replaceChildren`. `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `eval`,
string handlers и browser secret storage отсутствуют. Polling ограничен 100
попытками, прекращается для terminal status и не допускает параллельный
повторный submit одной кнопкой. При transport uncertainty повтор использует
тот же in-memory idempotency key для того же payload.

Для summary, analytics, jobs и approvals предусмотрены loading, empty, error,
stale или unavailable состояния. UI не отображает raw backend objects.

## Offline operator verification

Запускать только из isolated worktree без production env и marketplace smoke:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/pytest -q \
  tests/test_stage4a_*.py tests/test_stage3_*.py \
  tests/test_store_analytics_overview.py

PYTHONPATH=src /home/Codex/agent-tools/python/bin/pytest -q

PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli tasks policy

PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m compileall -q src tests

git diff --check
```

Secret scan выполняется только по Git diff/worktree paths; нельзя читать или
печатать production secret files. Playwright запускается против mock fixture
на `390x844`, `768x1024`, `1366x900`; production Telegram initData и
marketplace calls не используются.

Mock fixture:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  tests/fixtures/stage4a_miniapp_mock_server.py --port 18092
```

Проверить:

1. horizontal overflow отсутствует на всех трёх viewport;
2. четыре navigation state открываются и содержимое не перекрывается;
3. daily button создаёт только queued job;
4. jobs/approvals projections не содержат внутренних данных;
5. `applying_unknown` не имеет repeat-apply control;
6. Stage 3 auth/session/CSRF/replay/idempotency/notifier tests green.

## Rollback

Rollback production должен:

1. вернуть предыдущий reviewed code bundle control service;
2. restart только `vital-shevron-control-plane.service`;
3. не менять Nginx route, старый bot, Job Worker, runtime DB или marketplace
   state;
4. не удалять уже созданные control receipts/jobs — они являются durable
   audit history и безопасны для старого Stage 3 кода.

Stage 4A не добавляет SQLite migration, systemd/Nginx/runtime config или
секреты, поэтому schema rollback не требуется.

Точки восстановления deployment 2026-08-02:

- Git ref: `backup/pre-stage4a-20260802-171142`;
- SQLite backup:
  `/home/pavel/backups/vital-shevron-control-plane/20260802_171227-pre-stage4a/runtime.db`.

После deployment проверено:

- focused production suite: `66 passed`;
- task policy gaps: `0`;
- `vital-shevron-control-plane.service`: `active` после restart;
- localhost и public `/ready`: HTTP 200;
- public TLS: valid;
- owner auth/session и read-only `operations`, `jobs`, `approvals`: HTTP 200;
- unauthenticated read-only endpoints: HTTP 401;
- старый Telegram-бот: `active`, tmux-сессий: `9`;
- marketplace jobs и write-операции в smoke не запускались.

## Следующий gate

Следующий пакет Stage 4 не начинать без отдельного ТЗ. Добавление любого
write/dry-run/apply/approve/verify control требует нового server-side exact
contract, safety review, owner approval и отдельного production gate.
