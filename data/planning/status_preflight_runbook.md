# Status Preflight Runbook

## Команды

Без ЛК:

```bash
PYTHONPATH=src python3 -m seller_agent.cli status-preflight --skip-lk
```

`--skip-lk` означает API/catalog-only проверку: LK/CDP/keeper/watchdog проверки
не добавляются в `checks` как `skipped` и не должны превращать результат в
`warning`. Такой режим используется marketplace-local apply-контурами, где
актуальность данных проверяется отдельным fresh dry-run/snapshot перед write.

Полная проверка:

```bash
PYTHONPATH=src python3 -m seller_agent.cli status-preflight
```

Полный preflight остается общей проверкой здоровья проекта. Ошибка unrelated
LK-контура не должна автоматически блокировать marketplace-local API write,
если профильный apply-контур выполняет scoped API/catalog preflight, fresh
dry-run, drift-check и verify.

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
