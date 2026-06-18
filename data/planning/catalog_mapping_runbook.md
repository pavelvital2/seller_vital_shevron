# Catalog Mapping Runbook

## Итог

Vital Shevron стартует с разными артикулами продавца на Ozon и WB. Поэтому
проект ведет два независимых каталога и optional mapping. Функционал не должен
ждать унификации артикулов.

## Структура

```text
data/catalog/ozon/raw/
data/catalog/ozon/processed/ozon_catalog.json
data/catalog/ozon/processed/ozon_catalog.csv
data/catalog/wb/raw/
data/catalog/wb/processed/wb_catalog.json
data/catalog/wb/processed/wb_catalog.csv
data/catalog/mapping/ozon_wb_product_mapping.csv
data/catalog/mapping/ozon_wb_product_mapping_review.md
data/catalog/unified/future_seller_sku_plan.csv
```

Файлы mapping и unified plan считаются рабочими бизнес-данными и по умолчанию
не коммитятся в GitHub. В git остается только инструкция `README.md`.

## Получение каталогов

```bash
PYTHONPATH=src python3 -m seller_agent.cli fetch-catalog
```

Команда сохраняет:

- Ozon raw snapshots;
- WB raw snapshots;
- отдельный Ozon catalog;
- отдельный WB catalog;
- legacy `master_catalog` для совместимости старых сценариев.

## Правила работы до унификации

- Ozon-only сценарии используют `offer_id`, `product_id`, `sku`.
- WB-only сценарии используют `vendorCode`, `nmID`, barcode.
- Mapping нужен для объединенных отчетов и cross-marketplace операций.
- Неполный mapping не блокирует read-only/dry-run по отдельному маркетплейсу.
- Cross-marketplace write требует подтвержденного mapping по затрагиваемым
  строкам.

## Минимальные поля mapping

```text
internal_product_id
product_name
ozon_offer_id
ozon_product_id
ozon_sku
wb_vendor_code
wb_nm_id
barcode
match_confidence
match_basis
needs_owner_review
target_unified_seller_sku
```

`match_basis`:

```text
name
barcode
photo
dimensions
characteristics
manual_owner_confirmed
```

## Унификация seller SKU

Не выполнять на первом этапе.

Правило формирования новых артикулов продавца Vital Shevron зафиксировано в:

```text
data/planning/seller_sku_rules.md
```

Для новых товаров использовать это правило сразу. Для существующих товаров
Ozon/WB оно применяется только при подготовке будущего dry-run rename plan и
после отдельного согласования владельца.

Цепочка:

```text
read-only -> mapping draft -> owner review -> approved mapping ->
dry-run rename plan -> owner approval -> apply -> verify -> result
```

Перед apply проверить официальные API/ЛК ограничения Ozon/WB на изменение
`offer_id` и `vendorCode` у существующих карточек.
