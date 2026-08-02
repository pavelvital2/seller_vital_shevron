# Stage 4A: read-only operational control

Дата: 2026-08-02

Статус: утверждено архитектором для реализации в отдельном worktree; production
deployment, commit и push пока запрещены.

## 1. Контекст

Этапы 0-3 гибридного перехода завершены. Новый owner-only Telegram bot и Mini
App работают поверх общего `TaskRegistry`, `JobService`, `runtime/runtime.db` и
единственного существующего Job Worker. Первый production slice позволяет
запускать `store-analytics-overview` только для чтения.

Текущий worktree:

```text
/home/pavel/projects/worktrees/seller_vital_shevron_stage4a_readonly_control
```

Ветка:

```text
feat/stage4a-readonly-control-20260802
```

Base commit:

```text
d1a7f44f60ac1db5458064597567877f91750f95
```

Production-каталог `/home/pavel/projects/seller_vital_shevron` разрешено
использовать только для чтения и сравнения. Его dirty worktree, runtime DB,
сервисы, токены, сессии и marketplace state не изменять.

## 2. Цель

Сделать первый пакет этапа 4, который превращает существующие read-only
возможности в полезный owner control surface без добавления marketplace write.

После реализации в Mini App должны работать четыре раздела:

1. `Состояние` - безопасная сводка runtime и запуск существующего утреннего
   read-only отчета через единственный Job Worker.
2. `Аналитика` - существующий `store-analytics-overview` без регрессий.
3. `Задания` - owner-scoped история заданий, созданных новым control plane.
4. `Согласования` - только безопасная сводка ожидающих и неопределенных
   approval, без approve/reject/apply/verify кнопок.

## 3. Неподвижные границы

- Не создавать второй Job Worker, runtime DB или бизнес-логику во frontend.
- Не вызывать Ozon/WB/Parser API и ЛК из control service или frontend.
- Любой отчет запускается только через `JobService` и существующий Worker.
- Не добавлять marketplace write, dry-run apply, approve, reject, apply или
  verify действия.
- Не менять старый Telegram bot, его token/state/lock/service и команды.
- Не менять production systemd, Nginx, secrets, runtime data или marketplace
  state на этапе реализации.
- Не выполнять live marketplace smoke.
- Не копировать `runtime/runtime.db`, `.sessions`, credentials или production
  artifacts в worktree.
- Не ослаблять Telegram HMAC, owner allowlist, replay protection, session,
  CSRF, idempotency, redaction, localhost-only и CSP contracts Stage 3.
- Commit, push, merge и deployment запрещены до независимого review
  архитектора.

## 4. Обязательная реализация

### 4.1. Control task contracts

Заменить hardcoded проверку параметров одного task на небольшой server-side
реестр control-task contracts.

Первоначально разрешены только:

```text
store-analytics-overview
daily-morning-report
```

Для каждого task контракт обязан определять:

- exact client parameter keys и validator;
- server-owned параметры, которые frontend не может переопределить;
- обязательный `TaskRegistry` task ID;
- требование `enabled=true` и `is_read_only=true`;
- безопасный result projection/renderer type.

`store-analytics-overview` сохраняет текущий контракт без изменения поведения.

`daily-morning-report` принимает от клиента пустой объект параметров. Если
существующему handler нужны параметры, их формирует только backend из
проверенной server-side конфигурации. Запуск идет через общую очередь и
единственный Worker, не синхронно в polling/control process.

Произвольный task ID, неизвестные параметры, write/dry-run/apply/verify task и
попытка заменить server-owned scope должны fail closed до создания job.

### 4.2. Read-only API

Добавить owner-authenticated endpoints под существующим prefix:

```text
GET /vital-shevron/api/v1/operations/summary
GET /vital-shevron/api/v1/jobs?limit=20
GET /vital-shevron/api/v1/approvals
```

`operations/summary` возвращает только безопасную runtime-сводку:

- количество jobs в active и terminal состояниях;
- количество `pending_review` и `applying_unknown` approval;
- последний control-plane analytics/daily-report job и его status/timestamp;
- `data_available=false`, если сведения нельзя подтвердить.

Он не выполняет shell/systemd/network checks и не объявляет marketplace/API/LK
здоровыми по одному состоянию SQLite.

`GET /jobs` показывает только jobs, созданные текущим owner через control-plane
receipts. Ограничение `limit`: 1..30, default 20. Не возвращать internal
runtime job ID, actor, params, raw result, artifacts, paths, errors, token-like
values или notification routing.

`GET /approvals` возвращает только безопасную проекцию unresolved approval:

- нормализованный task ID/label, если он подтверждается связанным job;
- status;
- created/updated timestamps;
- `requires_reconciliation=true` для `applying_unknown`;
- счетчики по статусам.

Не возвращать `data_json`, checksum, apply params, credentials, marketplace
payload, source artifacts или внутренние пути. В этом slice approval нельзя
изменять.

Все три endpoint требуют действующую owner session. Ошибки имеют только
стабильные безопасные коды без stack/dynamic DB text.

### 4.3. Mini App

Сохранить текущий mobile-first визуальный язык и существующую аналитику.

Активировать:

- `Состояние`: runtime-сводка, время последнего обновления и кнопка запуска
  `daily-morning-report`;
- `Задания`: список control-plane jobs со статусом и временем;
- `Согласования`: read-only список/счетчики без action controls;
- `Аналитика`: текущее поведение Ozon/WB, 7/30/90 и регионы без регрессий.

Frontend не должен использовать `innerHTML`, `eval`, unsafe sinks или получать
raw backend objects. DOM создается через `textContent`/`createElement`.

Обязательны loading, empty, error, stale/unavailable states. Polling должен быть
bounded и останавливаться для terminal job. Повторное нажатие не должно
создавать duplicate write или обходить idempotency.

### 4.4. Документация

Обновить в этой ветке:

- `data/planning/hybrid_store_management_transition_plan_2026-08-01.md`:
  Stage 0-3 фактически завершены, Stage 4A реализуется/ожидает review;
- `data/planning/stage3_control_plane_runbook.md` или отдельный Stage 4A
  runbook с API, rollback и operator verification;
- project map/relevant registry docs только если изменился фактический
  контракт.

## 5. Тесты и негативные сценарии

Обязательно red-first покрыть:

1. Оба allowlisted task проходят только как `enabled + read_only`.
2. Write/dry-run/apply/verify и arbitrary task ID блокируются до job insert.
3. Exact parameter validation для analytics и пустого daily report.
4. Client не может заменить server-owned store/ownership scope.
5. `GET /jobs` показывает только receipts текущего owner, с limit 1..30.
6. Чужой owner, отсутствующая/expired session и malformed query fail closed.
7. Jobs projection не содержит internal IDs, params, artifacts, paths, raw
   errors, secrets или token-like строки.
8. Approvals projection не содержит `data_json`, checksum и apply payload.
9. `applying_unknown` честно помечается как требующий reconciliation и не
   получает кнопку повторного apply.
10. Existing auth/session/CSRF/replay/idempotency tests Stage 3 остаются green.
11. Existing `store-analytics-overview` contract и notifier routing не
    регрессируют.
12. Daily report создается в общей runtime queue; control process не запускает
    workflow напрямую.

## 6. Проверка

В isolated worktree выполнить:

```text
focused control-plane tests
full pytest
tasks policy
compileall
git diff --check
secret scan
```

Frontend проверить Playwright на:

```text
390x844
768x1024
1366x900
```

Проверить отсутствие горизонтального overflow, overlap, обрезанного текста и
неработающих navigation states. Использовать только mocks/fixtures; production
Telegram initData, token и marketplace calls не применять.

## 7. Критерий готовности

Stage 4A готов к architect review, когда:

- все четыре Mini App раздела функциональны в test environment;
- утренний отчет создается только через общий Job Worker contract;
- jobs/approvals проекции owner-scoped и не раскрывают внутренние данные;
- write surface не расширен;
- focused/full/security/frontend gates прошли;
- worktree не содержит runtime artifacts или секретов;
- исполнитель сохранил Hermes summary без секретов;
- commit/push/deployment не выполнялись.

## 8. Формат отчета исполнителя

Сначала итог, затем:

- files changed;
- implemented contracts;
- tests and exact counts;
- frontend screenshot paths;
- security/compatibility findings;
- remaining risks;
- explicit confirmation: no production changes, no marketplace write, no
  commit/push.
