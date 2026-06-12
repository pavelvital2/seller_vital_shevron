# seller_vital_shevron

Самостоятельный проект агента Vital Shevron для управления магазинами Ozon и
Wildberries.

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
PYTHONPATH=src pytest -q
PYTHONPATH=src python3 -m takterra_agent.cli --help
PYTHONPATH=src python3 -m takterra_agent.cli status-preflight --skip-lk
```

После получения API credentials:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli fetch-catalog
PYTHONPATH=src python3 -m takterra_agent.cli daily-morning-report
```

Сессии ЛК:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli sessions status
PYTHONPATH=src python3 -m takterra_agent.cli sessions restart --marketplace ozon
PYTHONPATH=src python3 -m takterra_agent.cli install-session-systemd
```

## Секреты

Секреты задаются через `.env` и внешние файлы в `.sessions/`. Сами ключи,
cookies, storage state и коды входа не должны попадать в код, документы, отчеты
или git.

Шаблон настроек:

```bash
cp .env.example .env
```

## GitHub

После проверки чистоты проекта создать локальный git и GitHub repository.
Перед первым commit обязательно проверить `git status --short` и отсутствие
секретов/сессий/операционных артефактов.

