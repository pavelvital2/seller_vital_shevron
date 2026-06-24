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
- `src/seller_agent/tasks/registry.py` - единый `TaskRegistry`: метаданные
  текущих CLI-команд, режимы `read_only/dry_run/apply/maintenance`, риск,
  marketplace, runbook, требования к credentials/LK/mapping/confirmation и
  Telegram-label для будущего бота.
- `tasks list|show` - CLI-команды просмотра `TaskRegistry`.
- `src/seller_agent/tasks/catalog_unified.py` - read-only сборка внутреннего
  общего product-level каталога из confirmed Ozon/WB mapping и обработанных
  Ozon/WB каталогов; команда `build-unified-catalog` пишет
  `data/catalog/unified/products.csv/json`, issues-report и `RunManifest`.
- `src/seller_agent/tasks/catalog_internal_sku_plan.py` - read-only план
  присвоения внутренних `internal_sku` товарам `ozon_only`/`wb_only` из
  unified catalog; команда `plan-internal-skus` формирует review CSV/JSON,
  не меняет Ozon `offer_id` и WB `vendorCode`.
- `src/seller_agent/tasks/pricing_status.py` - read-only статус цен и
  готовности маржинального анализа: соединяет unified catalog с локальными
  Ozon/WB price snapshots, выводит себестоимость, доступные цены, целевые
  net-пороги и warning-коды; команда `pricing-status` пишет runtime artifacts
  в `data/pricing/` и `data/runs/`.
- `src/seller_agent/bot/dispatcher.py` - thin layer над `TaskRegistry` для
  будущего Telegram-бота.
- `src/seller_agent/bot/commands.py` - read-only Telegram MVP command layer:
  `/help`, `/status`, `/today`, `/reviews`, `/approvals`, `/catalog`, `/runs`;
  возвращает текст Telegram-summary без write-операций; `/catalog` показывает
  последний `catalog-build-unified`, `/catalog <запрос>` ищет карточку товара
  в unified catalog, а `/today` и `/status` могут запускать свежие read-only
  задачи через `WorkflowRunner`, если включены `--live-today`/`--live-status`.
- `src/seller_agent/bot/telegram_runner.py` - read-only Telegram Bot API
  adapter: загрузка токена из внешнего файла/env, `sendMessage`,
  безопасный `sendDocument` для `artifacts.report`, одноразовый `getUpdates`
  polling, controlled `poll-loop`, allowlist, lock-file, state offset под
  `.sessions/telegram/`; не запускает marketplace write-операции.
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
- `data/reference/external_reviews/` - внешние review-документы по проекту,
  сохраненные как справочные материалы; не являются источником истины, но
  используются для сверки плана развития и рисков.

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
- `data/planning/product_card_work_runbook.md` - обязательная инструкция
  покарточной работы: просмотр всех фото, описание изображения/цветов/фона,
  правила липучки и пришивных нашивок, размеры/вес/упаковка,
  материал/состав, структура описания и формат review.
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
- `data/reference/takterra_development_docs/README.md`
- `data/reference/takterra_development_docs/data/15_architecture_notes/`
- `data/reference/takterra_development_docs/data/planning/`
- `data/reference/external_reviews/2026-06-13_gpt_pro_repository_review.md` -
  внешний review репозитория от 2026-06-13: оценка готовности к Telegram-боту,
  риски TaskRegistry/RunManifest/SafetyGuard/approval/locks и рекомендуемый
  порядок развития.

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
