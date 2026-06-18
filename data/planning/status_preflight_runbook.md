# Status Preflight Runbook

## Команды

Без ЛК:

```bash
PYTHONPATH=src python3 -m seller_agent.cli status-preflight --skip-lk
```

Полная проверка:

```bash
PYTHONPATH=src python3 -m seller_agent.cli status-preflight
```

## Vital Shevron Catalog Gate

По умолчанию:

```text
SELLER_SKU_MODE=separate
```

В этом режиме Ozon-only/WB-only строки не являются ошибкой сами по себе:
артикулы продавца могут отличаться до унификации. Ошибка остается для пустого
каталога или barcode conflicts.

Для будущего режима после унификации:

```text
SELLER_SKU_MODE=unified
```

Тогда preflight снова требует полного совпадения seller SKU.

