# Vital Shevron Project Map

Дата: 2026-06-21

```text
/home/pavel/projects/seller_vital_shevron
```

## Root

- `AGENTS.md` - постоянные правила работы агентов.
- `README.md` - краткий вход в проект.
- `.env.example` - шаблон настроек без секретов.
- `.gitignore` - защита от попадания секретов и runtime data в git.
- `pyproject.toml` - настройки Python-проекта и pytest.

## Agent Skills

- `.agents/skills/marketplace-analytics/SKILL.md` - короткий repo
  router-skill для выбора профильного Ozon/WB skill и runbook.
- `.agents/skills/marketplace-reviews-questions/SKILL.md` - отзывы,
  вопросы, медиа, draft replies, approval/apply/verify.
- `.agents/skills/marketplace-ozon-messenger/SKILL.md` - Ozon Messenger,
  уведомления, вопросы покупателей, mark-read, хвосты и cleanup.
- `.agents/skills/marketplace-supply-planning/SKILL.md` - остатки,
  продажи, локализация, кластеры назначения, производство и поставки.
- `.agents/skills/marketplace-search-query-research/SKILL.md` - поисковые
  запросы Ozon/WB, выгрузки ЛК/API, parser-сравнение и SEO-спрос.
- `.agents/skills/marketplace-action-monitoring/SKILL.md` - контроль акций
  после apply, baseline, daily_by_product и решения keep/watch/remove.
- `.agents/skills/*/agents/openai.yaml` - интерфейсные метаданные repo
  skills.

## Git

- local git initialized: `yes`
- branch: `main`
- first commit: `b79434a Initial Vital Shevron scaffold`
- GitHub remote: `https://github.com/pavelvital2/seller_vital_shevron`
- GitHub visibility: `PRIVATE`

## Code

- `src/seller_agent/` - рабочий универсальный Python package для task-runner,
  Telegram-бота и marketplace adapters. Переименование из старого package
  выполнено 2026-06-18 отдельным rename-only этапом без новой бизнес-логики.
- `src/seller_agent/core/run_manifest.py` - единый паспорт запусков:
  запись `manifest.json`, runtime-индекс `data/runs/index.jsonl`, lifecycle
  `pending_review/applied/verified/closed`, связи
  `pending_id/approved_id/applied_by_run_id`, команды `runs list/latest/show`.
- `src/seller_agent/core/workflow_runner.py` - read-only `WorkflowRunner`:
  запуск задач через `TaskRegistry`, блокировка не-read-only задач,
  per-task locks под `.sessions/workflows/`, safe error и handler-и для
  `daily-morning-report` и `status-preflight`.
- `src/seller_agent/core/job_models.py` - dataclass-модели runtime job
  контура: job status, approval status, job events, Telegram updates и
  resource leases.
- `src/seller_agent/core/job_store.py` - SQLite Job Store MVP:
  `jobs/job_events/task_requests/approvals/resource_leases/telegram_updates`,
  dedup Telegram `update_id`, resource leases с TTL и атомарный reserve
  approval `approved -> applying`.
- `src/seller_agent/core/job_service.py` - первый JobService v1:
  `submit(task_id, params, actor)`, `run(job_id)` для read-only задач через
  текущий `WorkflowRunner`, безопасная блокировка apply-задач и cancel queued.
- `src/seller_agent/core/job_runner.py` - минимальный runner queued job-ов:
  `run(job_id)` и `run_next()`.
- `jobs list|show|submit|run|run-next|cancel` - CLI-команды нового SQLite
  runtime-контура для создания, запуска и проверки job-ов без Telegram.
- `src/seller_agent/bot/runtime_jobs.py` - optional Telegram runtime bridge:
  в режиме `bot poll-once|poll-loop --runtime-jobs` live `/status` и `/today`
  регистрируются в `telegram_updates`, дедуплицируются по `update_id` и
  ставятся в `JobStore` как queued jobs без выполнения внутри polling.
- `src/seller_agent/bot/job_notifier.py` - Telegram notifier для завершенных
  runtime job-ов: находит исходный `telegram_updates` по `job_id`, отправляет
  итог в тот же chat/thread и прикрепляет безопасный `artifacts.report`.
- Telegram commands `/jobs`, `/job_<job_id>`, `/cancel_<job_id>` - просмотр
  SQLite runtime job-ов и безопасная отмена только `created/queued` job.
- `src/seller_agent/tasks/registry.py` - единый `TaskRegistry`: метаданные
  текущих CLI-команд, режимы `read_only/dry_run/apply/maintenance`, риск,
  marketplace, runbook, требования к credentials/LK/mapping/confirmation и
  Telegram-label для бота. Telegram-enabled dry-run задачи: `/elastic` для
  Ozon Elastic и `/wb-actions` для WB акций `70-55-55`.
- `tasks list|show` - CLI-команды просмотра `TaskRegistry`.
- `src/seller_agent/tasks/catalog_unified.py` - read-only сборка внутреннего
  общего product-level каталога из confirmed Ozon/WB mapping, owner-approved
  `internal_sku_assignment_owner_review.csv` для marketplace-only строк и
  обработанных Ozon/WB каталогов; команда `build-unified-catalog` пишет
  `data/catalog/unified/products.csv/json`, issues-report и `RunManifest`.
- `src/seller_agent/catalog/internal_sku_owner_review.py` - общий helper
  наложения owner-approved внутренних артикулов на unified/content derived
  слои без изменения Ozon `offer_id` и WB `vendorCode`.
- `src/seller_agent/tasks/catalog_internal_sku_plan.py` - read-only план
  присвоения внутренних `internal_sku` товарам `ozon_only`/`wb_only` из
  unified catalog; команда `plan-internal-skus` формирует review CSV/JSON,
  не меняет Ozon `offer_id` и WB `vendorCode`.
- `src/seller_agent/tasks/catalog_content_master.py` - read-only сборка
  единого контентного слоя поверх unified catalog, owner-review internal SKU
  overlay, processed Ozon/WB catalogs, optional `pricing-status` и optional
  card content index; команда `build-content-master` пишет
  `data/catalog/content/content_master.csv/json`,
  `data/catalog/content/content_audit.csv/json` и `RunManifest`, не меняет
  карточки Ozon/WB.
- `src/seller_agent/tasks/card_content_snapshot.py` - read-only snapshot
  карточного контента Ozon/WB: описания, характеристики, габариты, счетчик
  фото и хештеги/теги; команда `fetch-card-content` пишет
  `data/catalog/content/card_content_index.csv/json`, generated snapshots и
  raw runtime artifacts в `data/runs/<date>/<run_id>/raw/`.
- `src/seller_agent/tasks/card_content_audit_backlog.py` - read-only очередь
  карточного аудита по `content_master`: приоритет, причины, фокус проверки и
  следующий шаг; optional CSV-сигналы продаж, остатков и parser-видимости
  повышают точность приоритета; команда `card-content-audit-backlog` пишет
  `data/catalog/content/card_content_audit_backlog.csv/json` и `RunManifest`.
- `src/seller_agent/tasks/card_content_signals.py` - read-only collector
  нормализованных сигналов для карточного backlog: Ozon/WB продажи за период,
  текущие остатки и latest parser-видимость; команда `collect-card-signals`
  пишет `data/catalog/content/signals/*.csv` и runtime raw/summary в
  `data/runs/<date>/<run_id>/`.
- `src/seller_agent/tasks/card_content_parameter_inventory.py` - read-only
  инвентаризация параметров карточек перед полноценным мастер-паспортом:
  фактически заполненные Ozon-атрибуты/WB-характеристики из snapshots и
  схемы категорий/предметов через Ozon
  `/v1/description-category/attribute` и WB
  `/content/v2/object/charcs/{subjectId}`; команда
  `card-content-parameter-inventory` пишет CSV в
  `data/catalog/content/parameter_inventory/` и runtime report.
- `src/seller_agent/tasks/product_passport_design.py` - read-only дизайн
  полноценного master product passport: поля паспорта, JSON Schema и маппинг
  внутренних полей в Ozon attributes / WB characteristics по свежей parameter
  inventory; команда `design-product-passport` пишет generated artifacts в
  `data/catalog/content/product_passport/` и runtime report.
- `src/seller_agent/tasks/card_content_audit_packages.py` - read-only
  generator сохраненных карточных audit packages из backlog, Ozon/WB snapshots
  и master product passport schema; команда `card-content-audit-packages`
  подключает latest `seo_query_pack` при наличии, включая row-level
  `confirmed_query_rows` для спросового SEO, пишет
  `data/catalog/content/card_audit_packages/<run_id>/package_index.*`,
  `excluded_package_index.*`, `audit_package.json`, `audit_report.md`,
  `photos.html` и `RunManifest`. Строки `seo_query_pack_status` со статусами
  `needs_manual_review` и `excluded_non_patch_assortment` не выдаются
  fresh-аудиторам и попадают только в `excluded_package_index.*`.
  Визуальный аудит и рекомендации остаются pending до ручного просмотра фото
  агентом.
- `src/seller_agent/tasks/seo_query_pack.py` - read-only сборка
  централизованного `seo_query_pack` для карточных аудиторов из top-query
  источников Ozon/WB и `content_master`; команда `seo-query-pack` пишет
  `source_queries.*`, `card_seo_targets.*`, `seo_query_pack.json`,
  `RunManifest` и latest generated слой. `card_seo_targets.json` хранит
  `confirmed_query_rows` с `query`, `marketplace`, `role`, `frequency`/
  `popularity`, `period`, `source`, `seed_query`, `rank`, `collected_at`
  `data/catalog/content/seo_query_pack/`.
- `src/seller_agent/tasks/card_passport_promotion.py` - promotion
  owner-approved Layer 2 `audit.json`/HTML в Layer 3 approved master passport;
  команда `promote-approved-card-passport` по умолчанию делает dry-run, с
  `--write` создает `data/catalog/master_passport/approved/<internal_sku>.json`.
  Этот же helper используется preflight-слоем `apply-approved-cards`, чтобы
  не начинать marketplace write неполной пачкой при отсутствующем паспорте.
- `src/seller_agent/tasks/approved_cards_apply.py` - batch owner-approved
  карточный apply: проверка/восстановление Layer 3 passport, content update,
  seller SKU replacement, WB create/media, финальный catalog-sync и
  нормализованный post-verify по новым internal SKU.
- `data/planning/product_card_data_layers_runbook.md` - контракт карточного
  контура: слой 1 source marketplace data, слой 2 agent audit, слой 3
  owner-approved master passport.
- `data/planning/card_ops/quick_access.md` - быстрый вход для карточных
  write-маршрутов: инструкции, команды, batch apply после согласования пачки,
  статус автоматизации и ограничения.
- `src/seller_agent/tasks/pricing_status.py` - read-only статус цен и
  готовности маржинального анализа: соединяет unified catalog с локальными или
  fresh API Ozon/WB price snapshots, Ozon Elastic dry-run и WB actions dry-run,
  выводит себестоимость, доступные цены, action-price, целевые net-пороги и
  warning-коды; команда `pricing-status` пишет runtime artifacts в
  `data/pricing/` и `data/runs/`, а `--refresh-api` сохраняет свежие snapshots
  в `data/runs/<date>/<run_id>/raw/`.
- `src/seller_agent/marketplaces/wb/prices_adapter.py` - read-only adapter
  WB Discounts/Prices API для `GET /api/v2/list/goods/filter`.
- `src/seller_agent/bot/dispatcher.py` - thin layer над `TaskRegistry` для
  будущего Telegram-бота.
- `src/seller_agent/bot/commands.py` - Telegram MVP command layer:
  `/help`, `/status`, `/today`, `/reviews`, `/approvals`, `/catalog`,
  `/runs`, `/elastic`, `/wb-actions`; `/catalog` показывает последний
  `catalog-build-unified`, `/catalog <запрос>` ищет карточку товара в unified
  catalog, а `/today` и `/status` могут запускать свежие read-only задачи
  через `WorkflowRunner`, если включены `--live-today`/`--live-status`.
  Write-действия разрешены только точечными callback-кнопками:
  `oe_apply:<ozon_elastic_plan_run_id>` и
  `wba_apply:<wb_actions_discount_plan_run_id>`.
- `src/seller_agent/bot/telegram_runner.py` - Telegram Bot API adapter:
  загрузка токена из внешнего файла/env, `sendMessage` с inline-keyboard,
  `answerCallbackQuery`, безопасный `sendDocument` для `artifacts.report`,
  одноразовый `getUpdates` polling с `message` и `callback_query`, controlled
  `poll-loop`, allowlist, lock-file, state offset под `.sessions/telegram/`;
  marketplace write допускается только через узкие approval/callback flows.
- `/elastic` - Telegram-команда Ozon Elastic: свежий dry-run, report и
  inline-кнопка apply. Callback `oe_apply:<plan_run_id>` запускает
  `apply-ozon-elastic` только для показанного plan run через штатный
  fresh preflight/drift-check/verify.
- `src/seller_agent/tasks/telegram_report_sender.py` - maintenance helper
  `send-telegram-report`: отправляет owner-facing summary и безопасно
  прикрепляет сохраненный report-файл из `data/runs`/`data/reports`; блокирует
  отсутствующие, небезопасные или потенциально секретные файлы.
- `bot preview` - CLI-команда локальной проверки Telegram MVP без подключения
  Telegram token и без отправки сообщений.
- `bot send-preview` - CLI-команда отправки read-only preview-ответа в
  Telegram chat/topic через внешний token-file.
- `bot poll-once` - CLI-команда одноразовой обработки входящих Telegram
  updates с allowlist `--allowed-chat-id`.
- `bot poll-loop` - CLI-команда постоянного controlled polling; требует
  allowlist через `--allowed-chat-id` или
  `VITAL_SHEVRON_TELEGRAM_ALLOWED_CHAT_IDS`, использует lock-file.
- `bot poll-loop --live-today --live-status` - включает свежие read-only
  `/today` и `/status`; остальные команды остаются в режиме просмотра
  сохраненных runtime-данных.
- `send-telegram-report` - CLI-команда отправки уже сохраненного отчета в
  рабочий Telegram chat/topic: summary идет текстом, report-файл
  прикрепляется отдельным документом.
- `src/seller_agent/safety/approvals.py` - approval/idempotency helpers:
  stable checksum, marker `data/approved/applied/*.applied.json`, проверка
  повторного apply по marker и `RunManifest` index, checksum action rows,
  close-marker `data/approved/closed/*.closed.json`.
- `src/seller_agent/tasks/approvals.py` - read-only/maintenance слой
  approval lifecycle: `approvals status` собирает pending/approved/applied/
  closed статусы, `approvals close` закрывает runtime-пакеты без write-
  операций в маркетплейсах.
- `prepare-reviews-questions-approved` - CLI-команда создания approved package
  из `data/pending/<pending_id>/` для отзывов/вопросов с
  `actions_checksum`.
- `approvals status|close` - CLI-команды просмотра и закрытия pending/approved
  lifecycle для будущего Telegram `/approvals`.
- `src/seller_agent/tasks/ozon_elastic_apply.py` - применение согласованного
  Ozon Elastic dry-run с fresh preflight, drift-check и verify.
- `src/seller_agent/tasks/ozon_cpc_optimization_plan.py` - SKU-level dry-run
  план рекомендаций для Ozon CPC.
- `src/seller_agent/tasks/ozon_cpc_bids_apply.py` - применение согласованных
  ставок Ozon CPC через Performance API с API-only preflight, drift-check и
  verify.
- `src/seller_agent/tasks/daily_morning_report.py` - ежедневный read-only
  отчет Ozon/WB v3; использует Ozon `/v1/analytics/data`,
  `/v4/product/info/stocks`, `/v3/finance/transaction/list` для выкупов,
  расходов и CPC-списаний, `/v2/posting/fbo/list` для операционных FBO-отмен,
  Review/Question API с LK/CDP fallback, WB Statistics/Finance/Promotion/
  Communications API и последние dry-run отчеты по акциям.
- `src/seller_agent/tasks/supply_workbooks_plan.py` - read-only entrypoint
  `plan-supply-workbooks`: собирает Ozon/WB read-only snapshots по остаткам,
  продажам за 90 дней, локализации спроса и active inbound поставкам,
  вычитает inbound, применяет производственные ограничения и формирует CSV,
  Markdown report, RunManifest и Excel-файлы "в работу".
- `src/seller_agent/marketplaces/wb/fbw_supplies_adapter.py` - read-only
  adapter WB FBW Supplies API `https://supplies-api.wildberries.ru`:
  список поставок, детали поставки и товары в поставке для вычитания
  confirmed inbound.
- `src/seller_agent/tasks/ozon_messenger_workflow.py` - maintenance entrypoint
  `ozon-messenger-workflow`: единый будущий workflow Ozon Messenger
  `triage -> approval -> apply -> verify -> cleanup`. Пока блокируется как
  `workflow_adapter_not_implemented`, чтобы новые агенты не продолжали
  одноразовые ручные apply-скрипты.
- `src/seller_agent/marketplaces/wb/finance_adapter.py` - read-only адаптер
  WB Finance API для ежедневных финансовых отчетов реализации
  `/api/finance/v1/sales-reports/list`.
- `src/seller_agent/tasks/wb_promotion_report.py` - read-only отчет по WB
  продвижению через Promotion API.
- `src/seller_agent/tasks/wb_promotion_bid_plan.py` - dry-run план изменений
  ставок WB продвижения по активным CPC-кампаниям.
- `src/seller_agent/tasks/wb_promotion_bids_apply.py` - применение
  согласованных ставок WB promotion через Promotion API с fresh report,
  drift-check и verify.
- `scripts/` - JS/Bash helpers для ЛК, сессий, отзывов/вопросов и операций.
- `scripts/card_reviews/prepare_owner_review.py` - быстрый сборщик
  owner-review HTML из Layer 2 `audit.json`: применяет guardrail-правки
  карточного аудита, встраивает фото в HTML, проверяет mobile/desktop
  верстку через Playwright и пишет результат для отправки владельцу.
- `scripts/lib/ozon_cdp_guard.js` - обязательный guard для Ozon LK/CDP
  сценариев: до `chromium.connectOverCDP` проверяет локальный порт `9544` и
  Chrome `--user-data-dir` Vital Shevron, чтобы не подключиться к TAKTERRA или
  другому проекту.
- `scripts/reviews/ozon_review_media_detail_cdp.js` - read-only helper для
  получения фото/видео Ozon-отзывов через LK/CDP `/api/v2/review/detail`;
  сохраняет redacted detail и медиа без raw buyer/order/chat fields.
- `scripts/research/ozon_messenger_page_probe_cdp.js` - read-only probe
  страницы Ozon Messenger через CDP: сохраняет только redacted HTTP/websocket
  shape и UI summary без текстов сообщений, cookies и auth headers.
- `scripts/messenger/ozon_send_messages_cdp.js` - LK/CDP fallback для
  отправки уже согласованных Ozon Messenger ответов из approved package;
  использовать только после safety-цепочки и проверять результат через Seller
  API `/v3/chat/history`.
- `scripts/search_queries/collect_seo_query_pack_sources.js` - read-only
  helper для свежего сбора top-query источников Ozon/WB под карточный
  `seo_query_pack`; Ozon использует CDP guard порта `9544`, WB использует
  отдельный persistent profile, секреты и auth headers в артефакты не пишет.
- `tests/` - тесты переносимого каркаса.

## Deploy

- `deploy/systemd/user/vital-shevron-ozon-keeper.service`
- `deploy/systemd/user/vital-shevron-ozon-session-refresh.service`
- `deploy/systemd/user/vital-shevron-ozon-session-refresh.timer`
- `deploy/systemd/user/vital-shevron-wb-session-refresh.service`
- `deploy/systemd/user/vital-shevron-wb-session-refresh.timer`
- `deploy/systemd/user/vital-shevron-telegram-bot.service` - шаблон
  read-only Telegram polling service с live `/today` и `/status`; включать
  только после runtime allowlist `.sessions/telegram/vital_shevron_telegram_bot.env`.

Ozon CDP port по умолчанию: `9544`.

## Data

- `data/catalog/ozon/raw/` - raw Ozon API snapshots, не коммитить.
- `data/catalog/ozon/processed/` - обработанный Ozon catalog, не коммитить.
- `data/catalog/wb/raw/` - raw WB API snapshots, не коммитить.
- `data/catalog/wb/processed/` - обработанный WB catalog, не коммитить.
- `data/catalog/mapping/` - mapping Ozon/WB товаров.
- `data/catalog/unified/` - внутренний product-level каталог
  `products.csv/json`, планы внутренних артикулов marketplace-only товаров и
  будущие планы унификации seller SKU; не коммитить.
- `data/catalog/content/` - generated read-only content master и content audit
  для будущей унификации названий, описаний, характеристик, фото и SEO;
  содержит `content_master.*`, `content_audit.*`, `card_content_index.*` и
  `card_content_audit_backlog.*`, generated card snapshots и
  `parameter_inventory/*.csv`, `product_passport/*`,
  `card_audit_packages/<run_id>/*`; не коммитить, кроме `README.md`.
- `data/catalog/card_audits/` - слой 2 карточного контура: результаты
  личного аудита агента, рекомендации `сейчас -> рекомендую`, группы
  будущих пакетных правок и owner-review статусы; рабочие файлы не коммитить,
  кроме `README.md` и схем.
- `data/catalog/master_passport/` - слой 3 карточного контура:
  согласованные владельцем мастер-паспорта товаров для marketplace-specific
  dry-run; рабочие файлы не коммитить, кроме `README.md` и схем.
- `data/runs/` - runtime reports, не коммитить.
- `data/runs/index.jsonl` - runtime-индекс `RunManifest`, не коммитить.
- `data/pending/` - pending packages перед approval, не коммитить.
- `data/approved/` - approved packages, включая
  `approved_apply_plan.json`/`skipped_actions.json`/`APPROVED_PACKAGE.md`, не
  коммитить.
- `data/approved/applied/` - runtime-маркеры уже примененных approved/pending
  пакетов для idempotency guard, не коммитить.
- `data/approved/closed/` - runtime-маркеры закрытых pending/approved пакетов,
  не коммитить.
- `data/reports/` - экспортные отчеты, не коммитить без отдельного решения.
- `data/reference/takterra_development_docs/` - read-only копия markdown-
  документов TAKTERRA по развитию проекта, архитектуре, task-runner,
  safety-контуры и Telegram-боту; использовать как справочный слой, не как
  действующие правила Vital Shevron.
- `data/reference/api_docs/` - реестр API-документации Ozon/WB: официальные
  источники, локальные OpenAPI/Swagger-схемы, карточки endpoint-ов, даты
  проверки, ограничения и известные расхождения.
- `data/reference/external_reviews/` - внешние review-документы по проекту,
  сохраненные как справочные материалы; не являются источником истины, но
  используются для сверки плана развития и рисков. Индекс:
  `data/reference/external_reviews/README.md`.

Первый read-only catalog snapshot от 2026-06-12:

- Ozon rows: `548`
- WB rows: `431`
- exact seller SKU matches: `98`
- mapping rows requiring owner review: `783`

## Planning

- `data/planning/fresh_agent_handoff_2026-06-13.md` - актуальный handoff.
- `data/planning/fresh_agent_handoff_2026-06-12.md` - исторический handoff.
- `data/planning/revision_2026-06-13.md`
- `data/planning/recommendations_index.md`
- `data/planning/followups.md` - контрольные follow-up задачи, которые нельзя
  потерять между сессиями агентов.
- `data/planning/vital_shevron_bootstrap_plan.md`
- `data/planning/catalog_mapping_runbook.md`
- `data/planning/seller_sku_rules.md`
- `data/planning/lk_connection_runbook.md`
- `data/planning/session_manager_runbook.md`
- `data/planning/ozon_cabinet_map.md`
- `data/planning/wb_cabinet_map.md`
- `data/planning/status_preflight_runbook.md`
- `data/planning/run_manifest_runbook.md`
- `data/planning/task_registry_runbook.md`
- `data/planning/telegram_bot_mvp_runbook.md`
- `data/planning/runtime_job_store_plan.md` - план runtime hardening:
  SQLite Job Store, JobService/JobRunner, TaskRegistry v2, Telegram update
  deduplication, atomic approvals/resource leases и перевод Telegram в
  dispatcher job-ов. Этапы 1-2 начаты в `feature/runtime-job-store`:
  добавлены `JobStore`, `JobService`, `JobRunner`, CLI `jobs`, optional
  Telegram `--runtime-jobs` для `/status`/`/today`, `bot run-job-next`
  notifier и tests.
- `data/planning/daily_morning_report_runbook.md`
- `data/planning/reviews_questions_runbook.md`
- `data/planning/ozon_messenger_runbook.md` - Ozon Messenger/уведомления:
  ежедневный triage вопросов покупателей, важных сообщений площадки и шума,
  API-first `/v3/chat/*` плюс LK websocket fallback.
- `data/planning/chat_report_templates.md`
- `data/planning/ozon_elastic_runbook.md`
- `data/planning/ozon_cpc_efficiency_runbook.md`
- `data/planning/wb_promotion_runbook.md`
- `data/planning/wb_actions_runbook.md`
- `data/planning/pricing_runbook.md`
- `data/planning/ozon_parser_positions_runbook.md`
- `data/planning/wb_parser_positions_runbook.md`
- `data/planning/search_queries_runbook.md`
- `data/planning/seo_audit_runbook.md`
- `data/planning/card_content_standards_runbook.md` - постоянный стандарт
  заполнения карточек Ozon/WB после doc-review: названия, описания,
  характеристики, фото, хештеги, группировка и mapping в master product
  passport.
- `data/planning/master_product_passport_runbook.md` - архитектура целевого
  внутреннего паспорта товара, порядок запуска `design-product-passport`,
  generated artifacts и маппинг внутренних полей в Ozon/WB.
- `data/planning/product_card_data_layers_runbook.md` - контракт
  трехслойной архитектуры карточек: исходники, аудит агента, согласованный
  мастер-паспорт.
- `data/planning/product_card_fill_template_runbook.md` - согласованный
  шаблон целевого заполнения карточки и рекомендуемый pipeline автоматизации
  приведения карточек Ozon/WB к шаблону с учетом SEO.
- `data/planning/product_card_editor_field_map_runbook.md` - карта реальных
  полей редакторов Ozon/WB для шевронов, нашивок, петлиц и комплектов:
  что заполнять пачками, что оставлять, что требует отдельного review.
- `data/planning/product_card_audit_orchestration_runbook.md` -
  предварительная схема массового аудита карточек через оркестратора,
  одноразовых fresh-аудиторов, fresh-проверяющих, проверенные HTML/JSON слоя 2
  и общий индекс/дашборд.
- `data/planning/card_audit_agent_docs/` - минимальный пакет документов для
  одноразовых агентов карточного аудита: prompt аудитора, prompt
  проверяющего, краткие правила карточки и контракт HTML/JSON результата.
- `data/planning/product_card_work_runbook.md` - обязательная инструкция
  покарточной работы: просмотр всех фото, описание изображения/цветов/фона,
  правила липучки и пришивных нашивок, размеры/вес/упаковка,
  материал/состав, структура описания и формат review.
- `data/planning/product_card_work_checkpoint.md` - текущая точка
  восстановления карточной работы: последнее примененное состояние, статусы
  Layer 2/Layer 3, фиксированный HTML-шаблон, правила owner approval,
  batch-apply команды и следующий безопасный шаг.
- `data/planning/wb_card_create_runbook.md` - безопасное создание новой
  WB-карточки из owner-approved Layer 3 паспорта, WB barcode, media upload,
  verify и обновление локального каталожного контура.
- `data/planning/ozon_product_card_content_runbook.md` - Ozon-инструкция по
  наполнению карточек: title, аннотация, атрибуты, материал/состав,
  цвет/название цвета, хештеги, фото, контент-рейтинг и API read-only/dry-run.
- `data/planning/ozon_hashtag_frequency_table.md` - таблица частотности
  Ozon-хештегов из редактора карточек; обновлять не реже одного раза в месяц
  и использовать для добора релевантных хештегов до лимита 30.
- `data/planning/product_card_designer_tasks.md` - постоянный backlog задач
  дизайнеру по недостающим фото, вариантам ношения и сервисной инфографике
  карточек.
- `data/planning/card_grouping_runbook.md`
- `data/planning/supply_planning_runbook.md`
- `data/planning/analytics_skills_development_plan.md`
- `data/planning/telegram_bot_management_transition_plan.md`
- `data/reference/api_docs/README.md`
- `data/reference/api_docs/ozon/README.md`
- `data/reference/api_docs/ozon/endpoints/supply_order.md` - Ozon
  `supply-order` цепочка для FBO заявок поставки: list/get/details/bundle,
  статусы, bundle_id, official OpenAPI check через CDP/ЛК.
- `data/reference/api_docs/ozon/openapi/seller_swagger_20260629.json` -
  локальная копия официального Swagger Ozon Seller API `2.1`, загруженная
  через CDP/ЛК Vital Shevron; использовать как источник enum и схем
  `supply-order`.
- `data/reference/api_docs/wb/README.md`
- `data/reference/api_docs/wb/endpoints/fbw_supplies.md` - WB FBW Supplies
  API: список поставок, детали, товары, упаковки; источник для inbound на
  склады WB вместо FBS `/api/v3/supplies`.
- `data/reference/takterra_development_docs/README.md`
- `data/reference/takterra_development_docs/data/15_architecture_notes/`
- `data/reference/takterra_development_docs/data/planning/`
- `data/reference/external_reviews/README.md` - read-only индекс внешних
  review-документов и правила их использования без дублирования.
- `data/reference/external_reviews/2026-06-13_gpt_pro_repository_review.md` -
  внешний review репозитория от 2026-06-13: оценка готовности к Telegram-боту,
  риски TaskRegistry/RunManifest/SafetyGuard/approval/locks и рекомендуемый
  порядок развития.
- `data/reference/external_reviews/2026-06-25_gpt_pro_repository_review.md` -
  внешний review текущего `main` от 2026-06-25: оценка прогресса после
  `seller_agent`, `RunManifest`, `TaskRegistry`, approval packages,
  read-only `WorkflowRunner` и Telegram MVP; рекомендует `SQLite Job Store`,
  `TaskRegistry v2`, единый `JobRunner` и Telegram как dispatcher перед
  дальнейшим расширением write-кнопок.

## Sessions

- `.sessions/ozon/` - Ozon API credential files, Chrome profile, storage state,
  keepalive logs. Не коммитить.
- `.sessions/wb/` - WB token file, browser profile, storage state, keepalive
  logs. Не коммитить.
- `.sessions/telegram/` - runtime state Telegram bot polling, включая offset.
  Не коммитить. Здесь же runtime env с allowlist chat_id и lock-file. Токен
  хранить во внешнем secret-файле вне проекта, например
  `/home/pavel/.secrets/vital_shevron_telegram_bot_token`.
- `tmp/auth/` - временные auth/cookie файлы. Не коммитить.
