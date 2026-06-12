# seller_vital_shevron

Самостоятельный проект агента Vital Shevron для управления магазинами Ozon и
Wildberries.

GitHub repository:

```text
https://github.com/pavelvital2/seller_vital_shevron
```

Проект создан на базе рабочего каркаса `seller_takterra`, но без переноса
секретов, сессий, `.env`, операционных запусков, pending/apply packages и
рабочих каталогов TAKTERRA.

## Основная особенность

У Vital Shevron артикулы продавца на Ozon и WB до унификации могут не совпадать.
Поэтому проект работает с двумя независимыми каталогами:

```text
data/catalog/ozon/
data/catalog/wb/
data/catalog/mapping/
data/catalog/unified/
```

До унификации:

- Ozon-сценарии работают по `offer_id`, `product_id`, `sku`;
- WB-сценарии работают по `vendorCode`, `nmID`, barcode;
- mapping нужен для объединенных отчетов и cross-marketplace операций;
- отсутствие полного mapping не блокирует marketplace-local read-only/dry-run
  сценарии.

## Быстрые команды

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/pytest -q
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python -m takterra_agent.cli --help
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python -m takterra_agent.cli status-preflight --skip-lk
```

После получения API credentials:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python -m takterra_agent.cli fetch-catalog
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python -m takterra_agent.cli daily-morning-report
```

Сессии ЛК:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python -m takterra_agent.cli sessions status
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python -m takterra_agent.cli sessions restart --marketplace ozon
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python -m takterra_agent.cli install-session-systemd
```

## Операционные инструкции

Основные постоянные инструкции находятся в `data/planning/`:

- `status_preflight_runbook.md` - единая проверка состояния.
- `daily_morning_report_runbook.md` - утренний отчет.
- `catalog_mapping_runbook.md` - Ozon/WB catalog mapping.
- `ozon_elastic_runbook.md` - Ozon Elastic Boosting.
- `ozon_cpc_efficiency_runbook.md` - Ozon CPC эффективность и ставки.
- `wb_actions_runbook.md` - WB акции и скидки.
- `wb_promotion_runbook.md` - WB promotion отчеты, dry-run и apply ставок.

## Секреты

Секреты задаются через `.env` и внешние файлы в `.sessions/`. Сами ключи,
cookies, storage state и коды входа не должны попадать в код, документы, отчеты
или git.

Шаблон настроек:

```bash
cp .env.example .env
```

## GitHub

Репозиторий создан и подключен:

```text
https://github.com/pavelvital2/seller_vital_shevron
branch: main
visibility: PRIVATE
```

Перед каждым commit обязательно проверить `git status --short` и отсутствие
секретов, сессий и операционных артефактов в индексе.
