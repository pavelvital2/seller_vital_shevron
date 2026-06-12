# Vital Shevron Project Map

Дата: 2026-06-12

```text
/home/pavel/projects/seller_vital_shevron
```

## Root

- `AGENTS.md` - постоянные правила работы агентов.
- `README.md` - краткий вход в проект.
- `.env.example` - шаблон настроек без секретов.
- `.gitignore` - защита от попадания секретов и runtime data в git.
- `pyproject.toml` - настройки Python-проекта и pytest.

## Git

- local git initialized: `yes`
- branch: `main`
- first commit: `b79434a Initial Vital Shevron scaffold`
- GitHub remote: `https://github.com/pavelvital2/seller_vital_shevron`
- GitHub visibility: `PRIVATE`

## Code

- `src/takterra_agent/` - рабочий Python package. Имя оставлено временно для
  снижения риска при переносе; отдельный rename stage вынесен в рекомендации.
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

Первый read-only catalog snapshot от 2026-06-12:

- Ozon rows: `548`
- WB rows: `431`
- exact seller SKU matches: `98`
- mapping rows requiring owner review: `783`

## Planning

- `data/planning/recommendations_index.md`
- `data/planning/vital_shevron_bootstrap_plan.md`
- `data/planning/catalog_mapping_runbook.md`
- `data/planning/lk_connection_runbook.md`
- `data/planning/session_manager_runbook.md`
- `data/planning/status_preflight_runbook.md`
- `data/planning/daily_morning_report_runbook.md`
- `data/planning/reviews_questions_runbook.md`

## Sessions

- `.sessions/ozon/` - Ozon API credential files, Chrome profile, storage state,
  keepalive logs. Не коммитить.
- `.sessions/wb/` - WB token file, browser profile, storage state, keepalive
  logs. Не коммитить.
- `tmp/auth/` - временные auth/cookie файлы. Не коммитить.
