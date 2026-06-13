# Vital Shevron Project Map

Дата: 2026-06-13

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

- `.agents/skills/marketplace-analytics/SKILL.md` - repo skill для read-only
  аналитики Ozon/WB: SEO, parser-позиции, поисковые запросы, цены,
  продвижение, отзывы/вопросы и повторяемые отчеты.
- `.agents/skills/marketplace-analytics/agents/openai.yaml` - интерфейсные
  метаданные skill.

## Git

- local git initialized: `yes`
- branch: `main`
- first commit: `b79434a Initial Vital Shevron scaffold`
- GitHub remote: `https://github.com/pavelvital2/seller_vital_shevron`
- GitHub visibility: `PRIVATE`

## Code

- `src/takterra_agent/` - рабочий Python package. Имя оставлено временно для
  снижения риска при переносе; будущий rename stage должен переименовать пакет
  в универсальный `seller_agent`, подходящий под любой магазин.
- `src/takterra_agent/tasks/ozon_elastic_apply.py` - применение согласованного
  Ozon Elastic dry-run с fresh preflight, drift-check и verify.
- `src/takterra_agent/tasks/ozon_cpc_optimization_plan.py` - SKU-level dry-run
  план рекомендаций для Ozon CPC.
- `src/takterra_agent/tasks/ozon_cpc_bids_apply.py` - применение согласованных
  ставок Ozon CPC через Performance API с API-only preflight, drift-check и
  verify.
- `src/takterra_agent/tasks/wb_promotion_report.py` - read-only отчет по WB
  продвижению через Promotion API.
- `src/takterra_agent/tasks/wb_promotion_bid_plan.py` - dry-run план изменений
  ставок WB продвижения по активным CPC-кампаниям.
- `src/takterra_agent/tasks/wb_promotion_bids_apply.py` - применение
  согласованных ставок WB promotion через Promotion API с fresh report,
  drift-check и verify.
- `scripts/` - JS/Bash helpers для ЛК, сессий, отзывов/вопросов и операций.
- `tests/` - тесты переносимого каркаса.

## Deploy

- `deploy/systemd/user/vital-shevron-ozon-keeper.service`
- `deploy/systemd/user/vital-shevron-ozon-session-refresh.service`
- `deploy/systemd/user/vital-shevron-ozon-session-refresh.timer`
- `deploy/systemd/user/vital-shevron-wb-session-refresh.service`
- `deploy/systemd/user/vital-shevron-wb-session-refresh.timer`

Ozon CDP port по умолчанию: `9544`.

## Data

- `data/catalog/ozon/raw/` - raw Ozon API snapshots, не коммитить.
- `data/catalog/ozon/processed/` - обработанный Ozon catalog, не коммитить.
- `data/catalog/wb/raw/` - raw WB API snapshots, не коммитить.
- `data/catalog/wb/processed/` - обработанный WB catalog, не коммитить.
- `data/catalog/mapping/` - mapping Ozon/WB товаров.
- `data/catalog/unified/` - будущий план унификации seller SKU.
- `data/runs/` - runtime reports, не коммитить.
- `data/pending/` - pending packages перед approval, не коммитить.
- `data/approved/` - approved packages, не коммитить.
- `data/reports/` - экспортные отчеты, не коммитить без отдельного решения.
- `data/reference/takterra_development_docs/` - read-only копия markdown-
  документов TAKTERRA по развитию проекта, архитектуре, task-runner,
  safety-контуры и Telegram-боту; использовать как справочный слой, не как
  действующие правила Vital Shevron.

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
- `data/planning/vital_shevron_bootstrap_plan.md`
- `data/planning/catalog_mapping_runbook.md`
- `data/planning/seller_sku_rules.md`
- `data/planning/lk_connection_runbook.md`
- `data/planning/session_manager_runbook.md`
- `data/planning/ozon_cabinet_map.md`
- `data/planning/wb_cabinet_map.md`
- `data/planning/status_preflight_runbook.md`
- `data/planning/daily_morning_report_runbook.md`
- `data/planning/reviews_questions_runbook.md`
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
- `data/planning/analytics_skills_development_plan.md`
- `data/planning/telegram_bot_management_transition_plan.md`
- `data/reference/takterra_development_docs/README.md`
- `data/reference/takterra_development_docs/data/15_architecture_notes/`
- `data/reference/takterra_development_docs/data/planning/`

## Sessions

- `.sessions/ozon/` - Ozon API credential files, Chrome profile, storage state,
  keepalive logs. Не коммитить.
- `.sessions/wb/` - WB token file, browser profile, storage state, keepalive
  logs. Не коммитить.
- `tmp/auth/` - временные auth/cookie файлы. Не коммитить.
