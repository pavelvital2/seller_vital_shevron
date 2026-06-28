# Product Card Data Layers Runbook

Дата создания: 2026-06-25

## Назначение

Этот документ фиксирует трехслойную архитектуру работы с карточками товаров
Vital Shevron. Цель - отделить текущие данные маркетплейсов, агентский аудит и
финальный согласованный мастер-паспорт товара.

Без этого разделения легко смешать:

- то, что сейчас реально заполнено на Ozon/WB;
- то, что агент рекомендует изменить;
- то, что владелец уже согласовал и можно готовить к dry-run загрузки.

## Слой 0: Identity / Mapping

Слой 0 не является карточным контентом, но обязателен для всех остальных
слоев.

Хранит:

- `internal_product_id` - стабильный внутренний ключ строки проекта;
- `internal_sku` - утвержденный внутренний артикул, если уже присвоен;
- Ozon native IDs: `offer_id`, `product_id`, `sku`;
- WB native IDs: `vendorCode`, `nmID`, barcode;
- `marketplace_presence`: `ozon_wb`, `ozon_only`, `wb_only`;
- статус связи и owner-review.

Основные файлы:

```text
data/catalog/unified/products.csv
data/catalog/unified/products.json
data/catalog/mapping/ozon_wb_internal_sku_confirmed.csv
data/catalog/unified/internal_sku_assignment_owner_review.csv
```

Правило: marketplace-only товары сохраняют `internal_product_id` вида
`ozon:<offer_id>` или `wb:<vendorCode>`. Утвержденный владельцем внутренний
артикул хранится отдельно в `internal_sku` и не означает переименование
артикула продавца на маркетплейсе.

## Слой 1: Source Marketplace Data

Слой 1 - полная read-only копия текущего состояния карточек Ozon/WB и
связанных производных индексов.

Хранит:

- текущие названия Ozon/WB;
- текущие описания Ozon/WB;
- все текущие фото и ссылки на них;
- текущие Ozon attributes;
- текущие WB characteristics;
- размеры, вес, упаковку, цвет, состав, материал, ТН ВЭД, если они есть в
  snapshot/API;
- текущую группировку Ozon/WB;
- счетчики фото/описаний/атрибутов;
- поисковую видимость, продажи, остатки и другие сигналы, если они нужны для
  приоритизации аудита.

Основная папка:

```text
data/catalog/content/
```

Ключевые файлы:

```text
data/catalog/content/ozon_card_content.json
data/catalog/content/wb_card_content.json
data/catalog/content/card_content_index.csv
data/catalog/content/content_master.csv
data/catalog/content/card_content_audit_backlog.csv
data/catalog/content/card_audit_packages/<run_id>/
data/catalog/content/parameter_inventory/
```

Слой 1 не содержит рекомендаций и не является финальным паспортом. Он только
фиксирует, что сейчас есть в магазинах и в derived-индексах проекта.

## Слой 2: Agent Card Audit

Слой 2 - результат личного аудита карточек агентом по данным слоя 1.

Хранит:

- какие фото агент реально посмотрел;
- что изображено на каждом фото;
- что сейчас заполнено по Ozon и WB;
- что не совпадает между Ozon и WB;
- какие параметры неверны, неполны или требуют подтверждения;
- рекомендации `сейчас -> рекомендую -> почему`;
- SEO-находки, parser visibility и релевантные запросы;
- диагностику группировки;
- задачи дизайнеру;
- потенциальные группы для пакетных изменений.

Основная папка:

```text
data/catalog/card_audits/
```

Слой 2 не является согласованным мастер-паспортом и не может использоваться
для загрузки на Ozon/WB без owner review. Он должен явно хранить статус:

```text
agent_audited
owner_review_needed
owner_approved
owner_rejected
superseded
```

Если агент не открыл все фото и не прочитал название, описание и
характеристики, строка не может получить статус `agent_audited`.

## Слой 3: Owner-Approved Master Product Passport

Слой 3 - финальный согласованный вариант товара, который становится источником
для будущих marketplace-specific dry-run и загрузок.

Хранит:

- `internal_sku` и identity-связи;
- единое `canonical_title`;
- отдельные, но на первом этапе равные `ozon_title` и `wb_title`;
- единое `canonical_description`;
- отдельные, но на первом этапе равные `ozon_description` и `wb_description`;
- `ozon_attributes` с конкретными Ozon IDs/values;
- `wb_attributes` с конкретными WB IDs/values;
- целевой набор и порядок фото;
- размеры товара и упаковки;
- вес;
- материал, состав, крепление;
- ТН ВЭД;
- себестоимость и минимальные ценовые поля;
- SEO-поля и Ozon-хештеги;
- целевую группировку;
- owner approval status и историю изменений.

Основная папка:

```text
data/catalog/master_passport/
```

Слой 3 создается только после согласования владельцем результата аудита.
Именно из него должны формироваться:

- Ozon content dry-run;
- WB content dry-run;
- фото dry-run;
- grouping dry-run;
- будущий master catalog для Telegram-бота и отчетов.

## Переходы между слоями

```text
Layer 0 identity/mapping
  -> Layer 1 source marketplace data
  -> Layer 2 agent audit
  -> owner review
  -> Layer 3 approved master passport
  -> marketplace-specific dry-run
  -> approved
  -> apply
  -> verify
```

Запрещено:

- записывать рекомендации сразу в слой 3 без owner approval;
- применять изменения на Ozon/WB из слоя 2;
- считать generated `card-content-audit-packages` готовым аудитом;
- смешивать Ozon attributes и WB characteristics в один общий список;
- терять native IDs площадок при наличии `internal_sku`.

## Минимальный критерий готовности перед продолжением массового аудита

Перед продолжением карточного аудита должны быть выполнены условия:

- слой 0 актуален: `products.csv` содержит все owner-approved `internal_sku`;
- слой 1 актуален: `content_master.csv`, `card_content_audit_backlog.csv` и
  `card_audit_packages` пересобраны после owner-review overlay;
- слой 2 имеет стабильное место хранения и schema/README;
- слой 3 имеет стабильное место хранения и schema/README;
- runbook и project map ссылаются на эти слои.
