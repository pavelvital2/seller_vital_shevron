# Vital Shevron Transfer Manifest

Дата: 2026-06-12

Цель: создать самостоятельный проект Vital Shevron на базе рабочего каркаса
`seller_takterra`, без переноса секретов, сессий и операционных данных TAKTERRA.

Целевой проект:

```text
/home/pavel/projects/seller_vital_shevron
```

## Include

Переносить как рабочий каркас:

```text
src/
scripts/
tests/
deploy/
pyproject.toml
README.md
.env.example
.gitignore
AGENTS.md
```

Переносить как очищенные документы или шаблоны:

```text
data/planning/project_map.md
data/planning/recommendations_index.md
data/planning/vital_shevron_bootstrap_plan.md
data/planning/catalog_mapping_runbook.md
data/planning/lk_connection_runbook.md
data/planning/session_manager_runbook.md
data/planning/status_preflight_runbook.md
data/planning/daily_morning_report_runbook.md
data/planning/reviews_questions_runbook.md
```

## Exclude

Не переносить:

```text
.env
.sessions/
tmp/auth/
data/runs/
data/pending/
data/approved/
data/catalog/raw/
data/catalog/processed/
data/reports/operational runs
*.log
cookies
storage state
OTP/login codes
API keys/tokens
WB/Ozon upload task IDs from TAKTERRA operations
TAKTERRA pending/apply packages
```

## Sanitize

Перед использованием в Vital Shevron заменить или удалить:

```text
TAKTERRA active-store markers
ИП Рантусова active-seller markers
Ozon/WB company-specific identifiers
run_id with operational TAKTERRA context
paths pointing to seller_takterra for active work
systemd unit names takterra-*
CDP port 9444 if both projects run on one VPS
```

Допустимо оставить упоминание TAKTERRA только как read-only source project или
как источник переносимого опыта.

## Vital Shevron Catalog Rule

У Vital Shevron артикулы продавца на Ozon и WB сейчас не совпадают. Поэтому
каркас должен работать до унификации:

- Ozon-сценарии используют Ozon-native ID:
  `offer_id`, `product_id`, `sku`;
- WB-сценарии используют WB-native ID:
  `vendorCode`, `nmID`, barcode;
- mapping Ozon/WB является дополнительным слоем для объединенных отчетов и
  cross-marketplace операций;
- отсутствие полного mapping не блокирует marketplace-local read-only/dry-run
  сценарии;
- cross-marketplace write-операции по одному товару требуют подтвержденного
  mapping для затрагиваемых строк;
- унификация seller SKU - отдельная опасная операция:
  `read-only -> mapping draft -> review -> approved -> dry-run -> approval -> apply -> verify`.

## Target Empty Data Structure

Создать в Vital Shevron:

```text
data/catalog/ozon/raw/
data/catalog/ozon/processed/
data/catalog/wb/raw/
data/catalog/wb/processed/
data/catalog/mapping/
data/catalog/unified/
data/runs/
data/pending/
data/approved/
data/reports/
.sessions/ozon/
.sessions/wb/
tmp/auth/
```

## Validation

После переноса выполнить:

```bash
PYTHONPATH=src pytest -q
PYTHONPATH=src python3 -m takterra_agent.cli --help
find . -path './.sessions/*' -type f -print
find data/runs data/pending data/approved -type f -print
```

Ожидание:

- тесты проходят;
- CLI импортируется;
- файлы сессий и операционных пакетов TAKTERRA не перенесены;
- без Vital Shevron `.env` и API credentials `status-preflight` может показывать
  ожидаемые ошибки credentials, это не считается поломкой каркаса.
