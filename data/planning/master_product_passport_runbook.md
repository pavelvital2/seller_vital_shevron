# Master Product Passport Runbook

Дата создания: 2026-06-24

## Назначение

`master product passport` - целевой внутренний паспорт товара Vital Shevron.
Он нужен перед массовым аудитом карточек, SEO-унификацией, группировкой и
будущими dry-run загрузками на Ozon/WB.

Паспорт не заменяет marketplace snapshots и не меняет карточки. Это контракт
данных, куда аудит карточки должен сохранять подтвержденный единый вариант
товара и отдельные площадочные представления.

## Место в трехслойной архитектуре карточек

Подробный контракт слоев: `data/planning/product_card_data_layers_runbook.md`.

Кратко:

- слой 1 `data/catalog/content/` - исходные данные Ozon/WB без выводов;
- слой 2 `data/catalog/card_audits/` - личный аудит агента, рекомендации,
  группировка будущих пакетных правок и статус согласования;
- слой 3 `data/catalog/master_passport/` - только согласованный владельцем
  окончательный паспорт товара.

`master product passport` относится к слою 3. Нельзя записывать в него
рекомендации, которые владелец еще не согласовал.

## Правильная последовательность

```text
fetch-card-content
-> card-content-parameter-inventory
-> doc-review / card_content_standards_runbook
-> design-product-passport
-> saved card audit packages
-> agent audit saved to layer 2
-> owner review
-> owner-approved passport saved to layer 3
-> marketplace-specific dry-run
-> approved
-> apply
-> verify
```

Если пропустить `design-product-passport`, массовый карточный аудит начнет
сохранять рекомендации в нестабильных отчетах без единого формата. Это
затруднит последующую загрузку изменений на Ozon/WB и сравнение `было ->
стало`.

## CLI

Сформировать read-only дизайн паспорта:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli design-product-passport
```

Стабильный smoke/run:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli design-product-passport \
  --run-id product_passport_design_YYYYMMDD
```

Команда читает:

```text
data/catalog/content/parameter_inventory/ozon_schema_attributes.csv
data/catalog/content/parameter_inventory/wb_schema_characteristics.csv
```

Команда пишет:

```text
data/catalog/content/product_passport/passport_fields.csv
data/catalog/content/product_passport/passport_attribute_mapping.csv
data/catalog/content/product_passport/master_product_passport.schema.json
data/runs/<date>/<run_id>/master_product_passport_design_report.md
data/runs/<date>/<run_id>/summary.json
```

Все файлы в `data/catalog/content/product_passport/` являются generated
read-only artifacts и по умолчанию не коммитятся. Постоянный источник правил -
этот runbook, `card_content_standards_runbook.md` и свежий run report.

## Структура паспорта

Согласованные паспорта сохраняются в:

```text
data/catalog/master_passport/
```

Контрольная схема:

```text
data/catalog/master_passport/master_product_passport_approved.schema.json
```

Рабочие паспорта товаров в этой папке не коммитятся. В git хранится только
README и схема, чтобы не смешивать операционные данные карточек с постоянной
документацией проекта.

Паспорт должен иметь разделы:

- `identity` - внутренний товар, `internal_sku`, presence, marketplace IDs;
- `core` - тип изделия, место ношения, тема, `text/pict`, комплектность;
- `content` - единое название, описание, что изображено;
- `physical` - размеры изделия, размеры упаковки, вес;
- `materials` - материал, состав, крепление/липучка;
- `classification` - ТН ВЭД, страна производства и похожие классификаторы;
- `pricing` - себестоимость и поля для дальнейшей маржинальности;
- `seo` - поисковые запросы, хештеги, parser visibility;
- `media` - фото, роли фото, коллаж, задачи дизайнеру;
- `grouping` - целевая группировка и решение `keep/split/merge/manual_review`;
- `ozon_attributes` - конкретные Ozon `attribute_id` и значения;
- `wb_attributes` - конкретные WB `characteristic_id` и значения.

Нельзя сводить `ozon_attributes` и `wb_attributes` в один общий список:
названия, типы, обязательность, единицы измерения и API-поля отличаются.

## Правило стартовой унификации названия и описания

Правило владельца от 2026-06-25: первый этап контентной унификации строится
по самому строгому общему ограничению WB, чтобы убрать путаницу между
паспортом, Ozon и WB.

В паспорте должны быть отдельные поля:

```text
canonical_title
ozon_title
wb_title
canonical_description
ozon_description
wb_description
```

Но на стартовом этапе значения должны совпадать:

```text
ozon_title = wb_title = canonical_title
ozon_description = wb_description = canonical_description
```

Правила для `canonical_title`:

- максимум `60` символов;
- структура: `тип изделия + способ крепления + тематика/структура + место
  ношения`;
- размер не указывать по умолчанию, если он уже заполнен в характеристиках,
  инфографике, описании и паспорте;
- размер можно добавить только если он нужен для различения вариантов,
  группировки или подтвержденного поискового спроса.

Правила для `canonical_description`:

- максимум `2000` символов на стартовом этапе;
- три блока: подробное описание товара; из чего и как сделан/качество;
  короткий блок о магазине;
- без спама ключами, чужих брендов, неподтвержденных claims и перечисления
  нерелевантных вариантов.

Позже `ozon_title` и `ozon_description` можно расширять относительно
`canonical_*` только как отдельную SEO-оптимизацию по parser/поисковым данным,
через review и dry-run.

## Маппинг ключевых полей

Текущая Ozon категория/тип:

```text
description_category_id=17028963
type_id=970886657
```

Текущий основной WB subject:

```text
subjectID=2367
subjectName=Декор для одежды
```

Ключевые соответствия:

| Внутреннее поле | Ozon | WB |
| --- | --- | --- |
| `brand` | `85 Бренд` | `14177446 Бренд` |
| `product_type` | `8229 Тип` | `384944 Вид декора для одежды` |
| `canonical_title` | `4180 Название` | `15000000 Наименование` / title |
| `canonical_description` | `4191 Аннотация` | `14177452 Описание` / description |
| `product_size_mm` | `4382 Размеры, мм` | нет прямого поля в текущем потоке |
| `package_dimensions` | Ozon top-level dimensions | WB `dimensions.length/width/height` в см |
| `package_weight_g` | `4497 Вес с упаковкой, г` | WB `dimensions.weightBrutto` в кг |
| `material` | `7405 Материал` | если нет отдельного поля, в описание |
| `composition` | в Ozon аннотацию, если нет отдельного атрибута | `14177450 Состав` |
| `color_values` | `10096 Цвет товара` | `14177449 Цвет` |
| `variant_color_name` | `10097 Название цвета` | только как variant/grouping label, если применимо |
| `tnved` | `22232 ТН ВЭД коды ЕАЭС` | `15000001 ТНВЭД` |
| `country_of_origin` | нет подтвержденного поля в текущей Ozon-схеме | `14177451 Страна производства` |
| `pack_qty` | title/description при необходимости | `179792 Количество предметов в упаковке` |
| `kit_contents` | description | `378533 Комплектация` |
| `ozon_hashtags` | `23171 #Хештеги` | нет подтвержденного WB-аналога |
| `ozon_model_group_key` | `9048 Название модели...` | WB grouping через `imtID`/`moveNm` |
| `ozon_similar_group_key` | `22390 Объединить в похожие товары` | нет прямого WB-аналога |

## Правило упаковки в паспорте

В разделе `physical` хранить:

```text
product_size_mm
package_dimensions_ozon_mm
package_dimensions_wb_cm
package_dimension_rule
package_dimension_review_status
```

Стандартные упаковки Vital Shevron:

| Тип/место | Ozon, мм | WB, см |
| --- | --- | --- |
| Нарукавный | `100*100*10` | `10*10*1` |
| На кепку | `100*60*10` | `10*6*1` |
| Нагрудный | `130*50*10` | `13*5*1` |
| На спину | `300*100*10` | `30*10*1` |
| Петлицы | `100*40*10` | сначала `10*4*1`, fallback `10*5*1`, затем `10*6*1` |

Петлицы: 1 товарная единица - это две неразрезанные петлицы размером
`80*30*5 мм`; для упаковки считать как один физический слой толщиной `10 мм`.

Комплекты: длина/ширина упаковки = максимальные длина/ширина среди изделий
комплекта; толщина = количество физических изделий * `10 мм`. WB-габариты
получать переводом Ozon мм в см и проверять на dry-run/API или в ЛК. Для
петлиц использовать fallback только если WB не принимает минимальный размер.

## Важные ограничения

- `content_master.csv` остается индексом и очередью, а не финальным паспортом.
- Перед генерацией backlog/audit packages нужно пересобрать
  `build-unified-catalog` и `build-content-master`, чтобы owner-approved
  `internal_sku` из
  `data/catalog/unified/internal_sku_assignment_owner_review.csv` попали в
  `products.csv` и `content_master.csv`.
- У marketplace-only товаров `internal_product_id` остается native-ключом
  `ozon:<offer_id>` или `wb:<vendorCode>`, а утвержденный владельцем
  внутренний артикул хранится в `internal_sku`.
- `card_content_index.csv` хранит производные snapshot-счетчики, но не
  заменяет визуальный просмотр фото.
- `design-product-passport` не дает рекомендаций по конкретной карточке и не
  создает payload для Ozon/WB.
- Если schema CSV устарели или отсутствуют, сначала запустить
  `card-content-parameter-inventory`.
- Если Ozon/WB schema изменилась, нужно пересобрать паспорт и проверить
  validation issues.
- Группировка остается отдельной write-опасной операцией даже если поле
  группировки хранится в паспорте.

## Как использовать в следующем слое

Следующий слой реализован командой `card-content-audit-packages` - read-only
generator сохраненных карточных исходников. Он должен:

1. Читать `card_content_audit_backlog.csv`.
2. Для каждой выбранной строки подтягивать Ozon/WB snapshots и текущие фото.
3. Создавать draft структуры будущего `master_product_passport` по JSON
   Schema.
4. Заполнять `ozon_attributes` и `wb_attributes` отдельно.
5. Отмечать каждое поле как:
   - `confirmed`;
   - `recommended`;
   - `needs_owner_review`;
   - `not_confirmed`.
6. Сохранять исходный отчет карточки и коллаж/HTML фото.
7. Не считать карточку проверенной без личного просмотра фото агентом.
8. Не готовить apply без отдельного dry-run и approval.

После личного аудита агент сохраняет результат в слой 2:

```text
data/catalog/card_audits/
data/catalog/card_audits/card_audit_result.schema.json
```

Только после согласования владельцем принятые изменения переносятся в слой 3:

```text
data/catalog/master_passport/
data/catalog/master_passport/master_product_passport_approved.schema.json
```

Marketplace-specific dry-run можно готовить только из слоя 3, либо из
отдельного owner-approved пакета, который явно ссылается на слой 3.

Если нужно включить не только `business_priority=now`, но и уже начатые или
watch-строки, запускать с `--business-priority all`. При пересборке 2026-06-25
owner-sync all-пакет сохранил 710 строк, включая `back0012`, `back0016` и
`back0018` с owner-approved `internal_sku`.

Команда:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli card-content-audit-packages \
  --run-id card_content_audit_packages_YYYYMMDD \
  --limit 20
```

Пишет:

```text
data/catalog/content/card_audit_packages/<run_id>/package_index.csv
data/catalog/content/card_audit_packages/<run_id>/package_index.json
data/catalog/content/card_audit_packages/<run_id>/<rank>_<product>/audit_package.json
data/catalog/content/card_audit_packages/<run_id>/<rank>_<product>/audit_report.md
data/catalog/content/card_audit_packages/<run_id>/<rank>_<product>/photos.html
data/runs/<date>/<run_id>/card_content_audit_packages_report.md
```

Ограничение: generator только собирает материал и draft паспорта. Он не
считает фото просмотренными и не формирует карточные рекомендации. Статусы
`visual_audit_status=pending_agent_review` и
`recommendation_status=not_prepared` должны сохраняться до ручного просмотра
всех фото агентом.

## Критерии готовности этапа

Этап считается готовым, когда:

- команда `design-product-passport` есть в CLI и TaskRegistry;
- команда `card-content-audit-packages` есть в CLI и TaskRegistry;
- JSON Schema и CSV-маппинг генерируются без ошибок;
- маппинг проверяется по свежим Ozon/WB schema CSV;
- run report сохраняется в `data/runs`;
- полный pytest проходит;
- следующий агент может открыть сохраненный карточный audit package и начать
  настоящий визуальный аудит без повторного проектирования структуры;
- результат настоящего аудита можно сохранить в слой 2, а согласованный
  владельцем вариант - в слой 3.
