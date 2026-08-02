# TaskRegistry Runbook

Дата актуализации: 2026-06-24

## Итог

`TaskRegistry` - единый машинно-читаемый список операций проекта для CLI,
fresh-агентов и будущего Telegram-бота. Он не заменяет сами CLI-команды, а
описывает их метаданные: режим, риск, маркетплейсы, runbook, требования к
credentials/LK/mapping и необходимость подтверждения владельца.

## Где находится

```text
src/seller_agent/tasks/registry.py
```

Bot dispatcher должен брать список задач из этого же registry:

```text
src/seller_agent/bot/dispatcher.py
```

## CLI

Список задач:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli tasks list
```

Показать одну задачу:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli tasks show --task reviews-questions
```

Проверить пробелы v2 safety metadata:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli tasks policy
```

SEO-pack для карточных аудиторов также зарегистрирован как read-only задача:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli tasks show --task seo-query-pack
```

Быстрый индекс карточных write-маршрутов и команд:

```text
data/planning/card_ops/quick_access.md
```

Runtime Job Store CLI:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli jobs list

PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli jobs submit --task status-preflight \
  --params-json '{"skip_lk": true}'

PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli jobs run-next

PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli bot run-job-next \
  --token-file /home/pavel/.secrets/vital_shevron_telegram_bot_token

PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli bot run-job-loop \
  --max-iterations 5 \
  --token-file /home/pavel/.secrets/vital_shevron_telegram_bot_token
```

`jobs` - maintenance-команда для проверки и ручного запуска нового SQLite
runtime-контура. Она не заменяет `RunManifest`: job store хранит оперативное
состояние, а `RunManifest` остается audit/export artifact. `bot run-job-next`
использовать для Telegram-origin job-ов: команда выполняет первый queued job и
отправляет итоговый summary/report обратно в исходный chat/thread.
`bot run-job-loop` делает то же в управляемом loop-режиме и останавливается на
пустой очереди.

С 2026-07-20 `JobService` поддерживает `read_only`, `dry_run`, `verify` и
подтвержденные `apply` задачи. Production Telegram polling не запускает
зарегистрированные бизнес-задачи напрямую: message/callback сначала
регистрируется в `telegram_updates`, затем создается queued job. Worker берет
job FIFO. Навигация, ввод параметров и команды просмотра/отмены runtime jobs
не создают вложенные job и остаются синхронными.

## Control-plane allowlist Stage 4A

Mini App не считает весь `TaskRegistry` публичным API. Дополнительный
server-side реестр `src/seller_agent/control_plane/contracts.py` разрешает
только exact `store-analytics-overview` и `daily-morning-report`. Каждый
control contract фиксирует client keys, server-owned keys, registry task ID,
требования `enabled + read_only` и safe result renderer.

`daily-morning-report` принимает от клиента только `{}` и создаётся через
`JobService.submit_control_read_only()` как queued job. Control service не
вызывает `WorkflowRunner`; задачу забирает единственный штатный Job Worker.
Добавление любого следующего task требует отдельного ТЗ, тестов, result
projection и review. Наличие read-only задачи в `TaskRegistry` само по себе не
открывает её в Mini App.

Карточные команды, уже добавленные в `TaskRegistry`:

- `plan-seller-sku-update`;
- `apply-seller-sku-update`;
- `plan-card-content-update`;
- `promote-approved-card-passport`;
- `apply-card-content-update`;
- `apply-approved-card`;
- `apply-approved-cards`;
- `plan-ozon-card-create`;
- `apply-ozon-card-create`;
- `plan-ozon-product-remove`;
- `apply-ozon-product-remove`;

`apply-approved-cards` использовать после согласования владельцем пачки
карточек: команда объединяет content update, seller SKU replacement, WB create
и Ozon create в один batch-run с общим отчетом. Для Ozon create обязательно
передавать `--ozon-create-min-price`, иначе блокируется только стадия создания
Ozon-карточек. Перед внешними write-операциями команда
проверяет наличие Layer 3 passport и при необходимости вызывает
`promote-approved-card-passport` для отсутствующих owner-approved Layer 2
audit/HTML. После смены seller SKU запускает нормализованный post-verify по
новым internal SKU. Не запускать одноштучный `apply-approved-card` по кругу,
если согласовано несколько карточек.

Фильтры:

```bash
--mode read_only|dry_run|apply|verify|maintenance
--risk none|low|normal|high
--marketplace all|ozon|wb
--telegram-only
```

## Поля задачи

Минимальная схема:

```json
{
  "name": "",
  "command": "",
  "title": "",
  "description": "",
  "mode": "read_only|dry_run|apply|verify|maintenance",
  "risk": "none|low|normal|high",
  "marketplaces": ["ozon", "wb"],
  "runbook_path": "",
  "requires_credentials": false,
  "requires_lk": false,
  "requires_mapping": false,
  "requires_confirmation": false,
  "telegram_enabled": false,
  "telegram_button_label": "",
  "executor": "script|agent|hybrid",
  "parameter_schema": {},
  "result_schema": {},
  "timeout_seconds": 0,
  "lock_keys": [],
  "source_plan_task": "",
  "verify_task": "",
  "supports_cancel": false,
  "enabled": true,
  "aliases": [],
  "is_read_only": true,
  "is_write": false,
  "policy_issues": []
}
```

На 2026-07-14 v2-поля добавлены совместимо и не блокируют существующие
команды. `source_plan_task`, `verify_task` и `lock_keys` заполнены для
основных write-контуров, которые можно запускать через `WorkflowRunner` и
`JobService`: Ozon actions optimizer, Ozon Elastic, Ozon CPC, WB actions, WB
promotion bids, WB parser-enriched promotion bids, reviews/questions и
approved card batch, card create/remove и seller SKU update. Legacy
`actions-apply` имеет `enabled=false`: старый смешанный CLI-маршрут сохранен
для совместимости, но `WorkflowRunner`/`JobService` его не запускают, в новые
кнопки и Telegram его не добавлять. `tasks policy` для активных задач должен
быть пустым.

## Safety-правила

- Все `mode=apply` задачи должны иметь `requires_confirmation: true`.
- Write-задачи не должны запускаться будущим Telegram-ботом напрямую: бот
  может показывать статус, risks, rows и создавать approved package, но apply
  остается отдельным подтверждаемым действием.

## Важные read-only задачи

- `build-unified-catalog` - собирает внутренний product-level catalog из
  confirmed mapping и marketplace catalogs.
- `plan-internal-skus` - готовит owner-review план внутренних артикулов для
  `ozon_only`/`wb_only` товаров.
- `build-content-master` - строит read-only единый контентный слой и аудит
  карточной работы по unified catalog, processed Ozon/WB catalogs и optional
  `pricing-status`/`card_content_index`. Команда пишет generated artifacts в
  `data/catalog/content/` и `data/runs/`, не меняет карточки, фото, цены,
  остатки или seller SKU.
- `fetch-card-content` - читает Ozon `/v4/product/info/attributes` и
  `/v1/product/info/description`, WB `/content/v2/get/cards/list`, сохраняет
  raw runtime snapshots и производный `card_content_index.csv/json` для
  content master и будущих SEO-аудитов. Это не визуальный фото-аудит и не
  write-операция.
- `card-content-audit-backlog` - строит read-only очередь карточного аудита
  из `content_master`: причины, score, high/normal/low priority, фокус аудита
  и следующий шаг. Optional `--sales-signals-csv`, `--stock-signals-csv`,
  `--parser-signals-csv` добавляют продажи, остатки и parser-видимость для
  бизнес-приоритизации. Это не финальная рекомендация по карточке и не apply.
- `collect-card-signals` - собирает read-only нормализованные CSV-сигналы
  продаж, остатков и latest parser-видимости для `card-content-audit-backlog`.
  Пишет `data/catalog/content/signals/sales_signals.csv`,
  `stock_signals.csv`, `parser_signals.csv`, `all_signals.csv` и runtime
  report. Опция `--skip-api` оставляет только локальный parser-слой без
  обращений к Ozon/WB API.
- `card-content-parameter-inventory` - строит read-only инвентаризацию
  параметров карточек перед мастер-паспортом товара: что фактически заполнено
  в Ozon/WB snapshots и какие атрибуты доступны/обязательны по схемам
  площадок. Использует Ozon `POST /v1/description-category/attribute` и WB
  `GET /content/v2/object/charcs/{subjectId}`. Пишет CSV в
  `data/catalog/content/parameter_inventory/` и runtime report. Это не
  визуальный аудит и не изменение карточек.
- `design-product-passport` - строит read-only структуру полноценного
  master product passport, JSON Schema и маппинг внутренних полей в Ozon
  attributes / WB characteristics по свежей parameter inventory. Пишет
  generated artifacts в `data/catalog/content/product_passport/` и runtime
  report. Это не карточная рекомендация, не dry-run payload и не write.
- `card-content-audit-packages` - строит сохраненные read-only source
  packages для карточного аудита из backlog, Ozon/WB snapshots и master
  product passport schema. Пишет `package_index.csv/json`,
  `audit_package.json`, `audit_report.md`, `photos.html` и runtime report.
  Это не визуальный аудит, не рекомендация и не write: каждый пакет должен
  оставаться со статусами `visual_audit_status=pending_agent_review` и
  `recommendation_status=not_prepared`, пока агент не откроет все фото.
- `pricing-status` - строит read-only статус цен и готовности маржинального
  анализа по unified catalog и локальным или fresh API Ozon/WB price snapshots.
  Опция `--refresh-api` обращается только к read-only price endpoints и
  сохраняет snapshots в `data/runs/<date>/<run_id>/raw/`. Требует mapping как
  слой нормализации, дополнительно читает Ozon/WB action dry-run CSV для
  action-price, но не меняет цены и не обращается к write API.
- `requires_mapping: true` означает, что задача не должна переходить к
  cross-marketplace write без подтвержденного mapping.
- `requires_lk: true` означает, что перед запуском нужно проверить профильную
  LK/session инструкцию и не выводить cookies/storage state в отчеты.
- `runbook_path` обязателен для всех постоянных задач.

## Подключено

Ветка `feature/task-registry` регистрирует все текущие CLI-команды:

- `runs`;
- `jobs`;
- `tasks`;
- `approvals`;
- `fetch-catalog`;
- `build-unified-catalog`;
- `plan-internal-skus`;
- `build-content-master`;
- `fetch-card-content`;
- `collect-card-signals`;
- `card-content-audit-backlog`;
- `card-content-parameter-inventory`;
- `design-product-passport`;
- `card-content-audit-packages`;
- `status-preflight`;
- `daily-morning-report`;
- `wb-stock-supply-monitor`;
- `plan-supply-workbooks`;
- `send-telegram-report`;
- `ozon-messenger-workflow`;
- `sessions`;
- `restore-ozon-session`;
- `install-session-systemd`;
- `plan-ozon-elastic`;
- `apply-ozon-elastic`;
- `plan-ozon-cpc-optimization`;
- `apply-ozon-cpc-bids`;
- `plan-wb-actions-discounts`;
- `apply-wb-actions-discounts`;
- `wb-liquidation-stage2-plan`;
- `apply-wb-liquidation-stage2`;
- `verify-wb-liquidation-stage2`;
- `plan-wb-best-price-actions`;
- `apply-wb-best-price-actions`;
- `verify-wb-best-price-actions`;
- `wb-promotion-report`;
- `plan-wb-promotion-bids`;
- `apply-wb-promotion-bids`;
- `apply-actions`;
- `plan-wb-card-create`;
- `apply-wb-card-create`;
- `promote-approved-card-passport`;
- `reviews-questions`;
- `apply-reviews-questions`;
- `prepare-reviews-questions-approved`.

`build-unified-catalog`:

- task name: `catalog-build-unified`;
- mode: `read_only`;
- risk: `low`;
- marketplaces: `ozon`, `wb`;
- requires_mapping: `true`;
- credentials/LK/confirmation не требуются;
- Telegram: `/catalog` показывает последний `catalog-build-unified` из
  `RunManifest` и ключевые цифры из `summary` artifact; `/catalog <запрос>`
  ищет товар в unified catalog по internal/Ozon/WB идентификаторам, barcode и
  названию;
- runbook: `data/planning/catalog_mapping_runbook.md`;
- назначение: собрать внутренний `data/catalog/unified/products.csv/json` из
  confirmed mapping и локальных processed каталогов Ozon/WB.

`ozon-elastic-plan`:

- task name: `ozon-elastic-plan`;
- command: `plan-ozon-elastic`;
- mode: `dry_run`;
- risk: `normal`;
- marketplace: `ozon`;
- requires_credentials: `true`;
- Telegram: `/elastic` запускает свежий dry-run, возвращает summary и report,
  а при наличии строк к применению показывает inline-кнопку apply;
- runbook: `data/planning/ozon_elastic_runbook.md`;
- назначение: рассчитать актуальные действия Ozon Elastic без записи в Ozon.

`ozon-elastic-apply`:

- task name: `ozon-elastic-apply`;
- command: `apply-ozon-elastic`;
- mode: `apply`;
- risk: `high`;
- marketplace: `ozon`;
- requires_credentials: `true`;
- requires_confirmation: `true`;
- source_plan_task: `ozon-elastic-plan`;
- verify_task: `ozon-elastic-apply`;
- lock_keys: `marketplace:ozon`, `actions:ozon:elastic`;
- Telegram: запускается только callback-кнопкой `oe_apply:<plan_run_id>` после
  показанного dry-run `/elastic`; callback считается явным подтверждением
  владельца для конкретного `plan_run_id`;
- apply делает fresh preflight, fresh dry-run, partial drift-check, verify и
  idempotency guard.

Поддержанные apply handler-ы `WorkflowRunner`:

- `approved-cards-batch-apply`: требует `internal_skus` и
  `confirmed_by_user=true`;
- `ozon-actions-optimizer-apply`: требует `plan_run_id` и
  `confirmed_by_user=true`;
- `ozon-cpc-bids-apply`: требует `plan_run_id` и `confirmed_by_user=true`;
- `ozon-elastic-apply`: требует `plan_run_id` и `confirmed_by_user=true`;
- `ozon-inbox-apply`: требует `source_run_id` и `confirmed_by_user=true`;
- `reviews-questions-apply`: требует `approved_path` и
  `confirmed_by_user=true`;
- `wb-actions-discount-apply`: требует `plan_run_id` и
  `confirmed_by_user=true`;
- `wb-liquidation-stage2-apply`: требует `plan_run_id` и
  `confirmed_by_user=true`; fresh checksum и полный payload должны совпасть с
  owner-approved планом, после одного upload обязателен Prices API verify;
- `wb-best-price-action-apply`: требует `plan_run_id` и
  `confirmed_by_user=true`; использует owner-reviewed план выбора акций от
  минимальной цены и отдельный read-only verify;
- `wb-inbox-apply`: требует `source_run_id` и `confirmed_by_user=true`;
- `wb-promotion-bids-apply`: требует `plan_run_id` и
  `confirmed_by_user=true`;
- `wb-promotion-bids-parser-enriched-apply`: требует `plan_run_id` и
  `confirmed_by_user=true`; допускает `approved_actions=apply_ready` или
  owner-approved `review_only`.

`plan-internal-skus`:

- task name: `catalog-internal-sku-plan`;
- mode: `read_only`;
- risk: `low`;
- marketplaces: `ozon`, `wb`;
- requires_mapping: `true`;
- credentials/LK/confirmation не требуются;
- runbook: `data/planning/catalog_mapping_runbook.md`;
- назначение: построить review-план внутренних `internal_sku` для
  `ozon_only`/`wb_only` товаров без изменения артикулов продавца на Ozon/WB.

## Ozon/WB stock and production tasks

`ozon-stock-supply-monitor`:

- command: `ozon-stock-supply-monitor`;
- mode: `read_only`, risk: `low`, marketplace: `ozon`;
- Telegram: `/ozon-stock-supplies`, кнопка `Остатки и поставки Ozon`;
- формирует fresh общий/складской FBO-разрез и активные supply-order с
  физическими изделиями, ничего в Ozon не меняет;
- runbook: `data/planning/supply_planning_runbook.md`.

`ozon-production-work-plan`:

- command: `ozon-production-work-plan`;
- mode: `read_only`, risk: `low`, marketplace: `ozon`;
- параметры: `mode=capacity|coverage_days`, положительный integer `value`,
  `cluster_count=1..20`;
- Telegram: `/ozon-work-plan`, кнопка `В работу Ozon`;
- выбирает кластеры по чистой кластерной потребности и формирует Excel;
  поставку Ozon не создает;
- runbook: `data/planning/supply_planning_runbook.md`.

`wb-production-work-plan`:

- command: `wb-production-work-plan`;
- mode: `read_only`, risk: `low`, marketplace: `wb`;
- параметры: `mode=capacity|coverage_days`, положительный integer `value`,
  `cluster_count=1..6`;
- Telegram: `/wb-work-plan`, кнопка `В работу` в WB-меню;
- ранжирует кластеры по чистой локальной потребности, формирует owner-facing
  Excel и локальный review status, поставку WB не создает;
- runbook: `data/planning/supply_planning_runbook.md`.

## Ozon `Цены и маржа`

`ozon-pricing-margin` зарегистрирован как Telegram-enabled read-only задача с
низким риском. Обязательные параметры: `unit_cost > 0`,
`target_margin >= 0`, `period_days in {15, 30}`. Задача требует Ozon Seller
credentials и unified mapping, выполняется через Job Worker и намеренно не
имеет apply/verify пары: marketplace write в первом варианте отсутствует.

## Следующий шаг

1. Ozon Elastic и WB actions callbacks уже переключены на `JobService`.
   Следующие write-callback-и переключать позже по одному: Ozon optimizer,
   WB promotion и карточный batch apply.
2. Подключить генерацию CLI/help или документации из registry без изменения
   внешнего поведения команд.
3. Расширять `WorkflowRunner` только через задачи, уже описанные в registry и
   профильных runbook-ах.
4. Проектировать общий `SafetyGuard`, чтобы
   apply-команды не дублировали проверки подтверждения, preflight, drift-check
   и idempotency.
