# Master Product Passport Runbook

Дата создания: 2026-06-24

## Назначение

`master product passport` - целевой внутренний паспорт товара Vital Shevron.
Он нужен перед массовым аудитом карточек, SEO-унификацией, группировкой и
будущими dry-run загрузками на Ozon/WB.

Паспорт не заменяет marketplace snapshots и не меняет карточки. Это контракт
данных, куда аудит карточки должен сохранять подтвержденный единый вариант
товара и отдельные площадочные представления.

## Правильная последовательность

```text
fetch-card-content
-> card-content-parameter-inventory
-> doc-review / card_content_standards_runbook
-> design-product-passport
-> saved card audit packages
-> owner review
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

## Важные ограничения

- `content_master.csv` остается индексом и очередью, а не финальным паспортом.
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
generator сохраненных карточных отчетов. Он должен:

1. Читать `card_content_audit_backlog.csv`.
2. Для каждой выбранной строки подтягивать Ozon/WB snapshots и текущие фото.
3. Создавать draft `master_product_passport` по JSON Schema.
4. Заполнять `ozon_attributes` и `wb_attributes` отдельно.
5. Отмечать каждое поле как:
   - `confirmed`;
   - `recommended`;
   - `needs_owner_review`;
   - `not_confirmed`.
6. Сохранять отчет карточки и коллаж фото.
7. Не готовить apply без отдельного dry-run и approval.

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
  настоящий визуальный аудит без повторного проектирования структуры.
