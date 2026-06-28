# WB Card Create Runbook

Дата создания: 2026-06-28

## Назначение

Инструкция фиксирует безопасный маршрут создания WB-карточки для товара,
который уже есть на Ozon и согласован в Layer 3 master passport.

Операция опасная: создает покупательскую карточку на маркетплейсе и может
повлиять на продажи, модерацию и контент. Обязательная цепочка:

```text
read-only -> dry-run -> review -> approved -> apply -> verify -> result
```

Для покарточной работы Vital Shevron HTML-аудит является review-пакетом только
для тех действий, которые в нем явно показаны. Если в HTML не показан точный
`vendorCode`, создание новой WB-карточки, атрибуты, фото или другой write-шаг,
агент обязан остановиться и подготовить отдельный dry-run.

## Подтвержденный маршрут 2026-06-28

Проверено на карточке:

```text
internal_sku: chev_nr_bpla_pict0023
Ozon offer_id: pict0085
WB vendorCode: chev_nr_bpla_pict0023
WB nmID: 1212515625
WB barcode: 2052807975386
```

Preflight:

```text
data/runs/2026-06-28/status_preflight_20260628T161745
```

Dry-run:

```text
data/runs/2026-06-28/wb_card_create_plan_owner_approved_chev_nr_bpla_pict0023_20260628T162128
```

Apply/verify:

```text
data/runs/2026-06-28/wb_card_create_apply_owner_approved_chev_nr_bpla_pict0023_20260628T1622
```

Результат: `submitted_items=1`, `found_after_apply=1`,
`media_upload_errors=0`, `relevant_error_batches=0`.

## API

Используемые WB Content API методы:

```text
POST /content/v2/get/cards/list
POST /content/v2/get/cards/trash
POST /content/v2/barcodes
POST /content/v2/cards/upload
POST /content/v3/media/save
POST /content/v2/cards/error/list
```

Создание новой отдельной карточки идет через:

```text
POST /content/v2/cards/upload
```

Barcode генерируется только во время apply:

```text
POST /content/v2/barcodes
```

В dry-run использовать placeholder:

```text
GENERATE_AT_APPLY
```

## Источник данных

Для Vital Shevron создавать WB-карточку нужно не из legacy
`master_catalog.csv`, а из согласованного Layer 3 паспорта:

```text
data/catalog/master_passport/approved/<internal_sku>.json
```

Минимальные поля:

- `identity.internal_sku` -> будущий WB `vendorCode`;
- `content.wb_title` -> WB `title`;
- `content.wb_description` -> WB `description`;
- `physical.package_dimensions_wb_cm` -> WB `dimensions`;
- `physical.package_weight_g` -> `dimensions.weightBrutto`;
- `classification.tnved` -> WB `ТНВЭД`;
- `materials.composition` -> WB `Состав`;
- `media.target_assets[].url` -> фото для `/content/v3/media/save`.

Если товар уже есть на WB, новую карточку не создавать. Для него нужен
отдельный content-update маршрут существующей карточки.

## Payload для шеврона

Для предмета `2367 / Декор для одежды` базовый payload:

```json
[
  {
    "subjectID": 2367,
    "variants": [
      {
        "vendorCode": "chev_nr_bpla_pict0023",
        "title": "Шеврон на липучке БПЛА Смерть сходящая с небес",
        "description": "...",
        "brand": "VitalEmb",
        "dimensions": {
          "length": 10,
          "width": 10,
          "height": 1,
          "weightBrutto": 0.01
        },
        "characteristics": [
          {"id": 14177449, "value": ["оливковый", "черный", "красный"]},
          {"id": 384944, "value": ["шеврон"]},
          {"id": 179792, "value": ["1 шт."]},
          {"id": 378533, "value": ["шеврон на липучке 1 шт."]},
          {"id": 14177450, "value": ["полиэстер", "нейлон"]},
          {"id": 15001405, "value": ["0"]},
          {"id": 14177451, "value": ["Россия"]},
          {"id": 15000001, "value": ["5810999000"]}
        ],
        "sizes": [
          {
            "techSize": "0",
            "wbSize": "",
            "price": 1100,
            "skus": ["GENERATE_AT_APPLY"]
          }
        ]
      }
    ]
  }
]
```

Цена в `sizes.price` нужна WB для создания карточки, но это не заменяет
отдельный ценовой контур. Скидки, остатки, акции, реклама и цены не должны
меняться этим сценарием.

## Apply

Штатная команда проекта:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli apply-wb-card-create \
  --plan-run-id <plan_run_id> \
  --confirmed-by-user
```

`--allow-manual-review` использовать только если в плане есть
`needs_manual_review=true` и владелец отдельно подтвердил применение такого
плана.

Для точечных owner-approved переносов допускается вручную собрать plan-dir из
Layer 3 паспорта, если штатный `plan-wb-card-create` строит план из legacy
`master_catalog.csv` и поэтому не совпадает с согласованным паспортом.

## Verify

После apply обязательно проверить:

- карточка найдена по `vendorCode`;
- получен `nmID`;
- barcode записан в `sizes.skus`;
- `media_save_*` вернул `error=false`;
- `wb_card_errors_relevant.json` пустой;
- `pending_media_uploads.json` пустой;
- `summary.json` имеет `overall_status=ok`.

После успешного verify обновить локальный контур:

- Layer 3 passport: `identity.wb_vendor_code`, `identity.wb_nm_id`,
  `wb.barcode`, `wb.export_status`;
- `data/catalog/unified/products.*`;
- `data/catalog/content/content_master.*` как промежуточное состояние
  `needs_fetch_after_create`;
- `data/catalog/processed/master_catalog.*`, если этот legacy слой еще
  используется в отчетах.

Для batch-маршрута `apply-approved-cards` это не ручной шаг: команда после
content update, seller SKU replacement и WB create запускает финальный
catalog-sync. Синхронизация ищет строки не только по новому internal SKU, но и
по старому Ozon `offer_id`, старому WB `vendorCode`, `product_id` и `nmID`.
Если карточка была склеена из Ozon-only и WB-only строк, WB-only дубль
удаляется, а barcode сохраняется в объединенной строке и Layer 3 passport.

`data/catalog/content/wb_card_content.json` вручную не подменять как источник
истины. Его нужно обновить отдельным read-only `fetch-card-content`.

## Ограничения

- Штатный `plan-wb-card-create` на 2026-06-28 не использует Layer 3 passport и
  может собрать пакет из старого `master_catalog.csv`; перед apply сверять
  источник плана.
- Штатного content-update контура для уже существующих Ozon/WB карточек на
  2026-06-28 нет. Обновление title/description/attributes существующих
  карточек нужно реализовать отдельным dry-run/apply/verify слоем.
- Seller SKU существующих карточек не менять в рамках обычного WB create или
  content-update, если это не показано в HTML как отдельное dangerous action и
  владелец не дал отдельное подтверждение. Уточнение владельца 2026-06-28:
  для owner-approved карточек `артикул тоже под замену`. После этого
  `chev_kp_chvk_pict0001` был успешно переведен на новый WB `vendorCode`
  отдельным seller SKU update маршрутом:
  `data/runs/2026-06-28/seller_sku_unify_apply_two_cards_20260628T163418`.
  Создание новой WB-карточки и переименование существующей WB-карточки не
  смешивать в одном сценарии без явного approved-пакета.

## Подтвержденные внештатные ситуации

### 2026-06-28: owner-approved plan не должен требовать legacy raw

Симптом: batch `apply-approved-cards` дошел до стадии WB-create и упал:

```text
FileNotFoundError: No catalog raw directories found
```

Причина: `plan-wb-card-create` раньше всегда искал
`data/catalog/raw/*/*`, хотя в owner-approved режиме `--internal-sku` все
нужные поля уже есть в Layer 3 passport. Это лишняя legacy-зависимость.

Исправление: `plan-wb-card-create --internal-sku ...` теперь строит payload из
`data/catalog/master_passport/approved/<internal_sku>.json`, подтягивает Ozon
цену из API/`pricing_status.json` и принимает статусы:

```text
owner_approved
owner_approved_pending_apply
owner_approved_pending_batch_apply
applied_verified
```

### 2026-06-28: WB `Цвет` больше 5 значений

Симптом: `/content/v2/cards/upload` вернул `error=false`, но затем в
`/content/v2/cards/error/list` появилась ошибка:

```text
Поле Цвет имеет слишком много значений. Разрешено не более 5
```

Причина: у одной карточки в группе было 6 цветов. WB отклонил всю группу
upload, поэтому остальные карточки из этой группы тоже не создались.

Восстановление:

1. Не повторять тот же apply-plan.
2. В WB-полях Layer 3 passport оставить максимум 5 основных цветов.
3. Пересобрать `plan-wb-card-create`.
4. Проверить `manual_review_items=0`.
5. Выполнить `apply-wb-card-create`.
6. Проверить `found_after_apply`, `pending_media_uploads` и
   `wb_card_errors_relevant`.

Подтвержденный успешный apply:

```text
data/runs/2026-06-28/wb_card_create_apply_20260628T212740
```

Подтвержденная доработка проекта после этого apply:

```text
src/seller_agent/tasks/approved_cards_apply.py
tests/test_approved_cards_apply_catalog_sync.py
```

Регрессионный тест проверяет сценарий, из-за которого `master_catalog.*` ранее
не обновлялся: старая Ozon-only строка находится по прежнему `offer_id`,
старая WB-only строка находится по прежнему `vendorCode`, barcode переносится,
дубль удаляется, итоговая строка становится `ozon_wb`.
