# Постоянная инструкция по загрузке карточек WB

Назначение: единый порядок создания и доведения карточек Wildberries из данных
Ozon в проекте TAKTERRA.

Эта инструкция постоянная. Ее нужно использовать для следующих загрузок карточек
WB, а не только для сценария от 2026-06-10.

## Главные правила

1. Работа выполняется только из проекта:

```text
/home/pavel/projects/seller_takterra
```

2. Старые проекты не менять. Они только read-only источники опыта:

```text
/home/pavel/projects/seller_wb
/home/pavel/projects/seller_ozon
/home/pavel/projects/seller_ozon_vitalsewing
```

3. Целевое правило артикула:

```text
master_sku == Ozon offer_id == WB vendorCode
```

4. Для одного товара Ozon/WB должны совпадать:

- артикул продавца;
- название, насколько позволяет лимит WB;
- описание без смысловых потерь;
- характеристики;
- размеры и вес;
- фото.

5. Отличия допускаются только из-за ограничений WB. Такие отличия обязательно
фиксируются в dry-run/apply отчете.

6. Любая загрузка карточек - опасная write-операция. Порядок обязателен:

```text
catalog_check -> dry-run -> review -> explicit approve -> apply -> verify -> report
```

## Что запрещено

- Нельзя запускать apply без явного подтверждения владельца.
- Нельзя записывать API-ключи, токены, cookies или содержимое секретных файлов в
  код, отчеты и planning.
- Нельзя менять цены, остатки, скидки, акции, рекламу или ответы покупателям в
  рамках сценария загрузки карточек.
- Нельзя повторять failed apply без проверки точного `vendorCode`, ошибок WB и
  корзины WB.
- Нельзя вручную править старые проекты ради загрузки карточек TAKTERRA.

## Подготовка

Перед загрузкой карточек выполнить актуальный read-only снимок каталога:

```bash
TAKTERRA_WB_TOKEN_FILE=/path/to/wb_token \
TAKTERRA_OZON_SELLER_CREDENTIALS_FILE=/path/to/ozon_seller_credentials \
PYTHONPATH=src python3 -m takterra_agent.cli fetch-catalog
```

Проверить отчет:

```text
data/runs/YYYY-MM-DD/catalog_fetch_*/catalog_check_report.md
```

В отчете должны быть понятны:

- сколько товаров на Ozon;
- сколько карточек на WB;
- какие строки `matched`;
- какие строки `ozon_only`;
- какие строки `wb_only`;
- нет ли дублей `offer_id` / `vendorCode`;
- нет ли barcode mismatch.

Загружать на WB нужно только подтвержденные `ozon_only`, если владелец подтвердил,
что эти товары действительно должны появиться на WB.

## Dry-run

Создать план загрузки:

```bash
TAKTERRA_WB_TOKEN_FILE=/path/to/wb_token \
TAKTERRA_OZON_SELLER_CREDENTIALS_FILE=/path/to/ozon_seller_credentials \
PYTHONPATH=src python3 -m takterra_agent.cli plan-wb-card-create
```

Планировщик создает run:

```text
data/runs/YYYY-MM-DD/wb_card_create_plan_*/
```

Главные файлы dry-run:

```text
wb_card_create_dry_run.md
wb_card_create_plan.json
wb_cards_upload_payload_draft.json
wb_cards_upload_add_payload_draft.json
wb_media_upload_plan.json
ozon_only_attributes.json
wb_subject_characteristics.json
summary.json
```

Перед apply проверить:

- все `vendorCode` совпадают с Ozon `offer_id`;
- `subjectID` выбран правильно;
- для вариаций существующих групп правильно выбран `imtID`;
- название WB не превышает лимит;
- описание очищено от HTML и запрещенных WB-символов;
- `dimensions.length/width/height` в сантиметрах;
- `dimensions.weightBrutto` в килограммах;
- фото есть в `wb_media_upload_plan.json`;
- missing characteristics понятны и не являются обязательными бизнес-полями;
- цены в payload не использовать как самостоятельный сценарий управления ценами.

## Правила формирования WB payload

Для новой отдельной карточки используется:

```text
POST /content/v2/cards/upload
```

Структура:

```json
[
  {
    "subjectID": 2367,
    "variants": [
      {
        "vendorCode": "master_sku",
        "title": "Название",
        "description": "Описание",
        "brand": "",
        "dimensions": {
          "length": 10,
          "width": 8,
          "height": 1,
          "weightBrutto": 0.006
        },
        "characteristics": [],
        "sizes": [
          {
            "techSize": "0",
            "wbSize": "",
            "price": 1100,
            "skus": ["WB_BARCODE"]
          }
        ]
      }
    ]
  }
]
```

Для добавления вариации в существующую группу используется:

```text
POST /content/v2/cards/upload/add
```

Структура:

```json
{
  "imtID": 1464304998,
  "cardsToAdd": [
    {
      "vendorCode": "master_sku",
      "title": "Название",
      "description": "Описание",
      "dimensions": {
        "length": 10,
        "width": 8,
        "height": 1,
        "weightBrutto": 0.006
      },
      "characteristics": [],
      "sizes": [
        {
          "techSize": "0",
          "wbSize": "",
          "price": 1100,
          "skus": ["WB_BARCODE"]
        }
      ]
    }
  ]
}
```

WB barcode генерируется только во время apply:

```text
POST /content/v2/barcodes
```

В dry-run вместо реального barcode используется:

```text
GENERATE_AT_APPLY
```

## Особенности WB, которые обязательно учитывать

### Вес и габариты

WB отклоняет характеристику `88952` в `characteristics`:

```text
Вес товара с упаковкой
```

Вес должен передаваться только так:

```json
"dimensions": {
  "weightBrutto": 0.006
}
```

Единицы:

- габариты - сантиметры;
- вес - килограммы.

Характеристики упаковки `88952`, `90745`, `90846`, `90849` не дублировать в
`characteristics`; они считаются заполненными через `dimensions`.

### Описание

Описание берется с Ozon, но для WB:

- HTML переводится в plain text;
- `<br>`, `<li>`, `<p>` превращаются в переносы строк;
- emoji удаляются;
- смысловой текст сохраняется.

Причина: WB отклоняет описания с emoji, например:

```text
⚡ 🔇 🧵 🎛️ 🌧️ 🧹 🎒
```

### Название

Название WB берется из Ozon. Если превышен лимит WB, сокращать по аналогии с
существующими карточками TAKTERRA, без потери ключевого смысла.

### Фото

Фото берутся из Ozon:

- сначала `primary_image`;
- затем остальные `images`;
- дубли не отправлять;
- медиа загружать только после появления WB `nmID`.

Загрузка фото по ссылкам:

```text
POST /content/v3/media/save
```

Структура:

```json
{
  "nmId": 1141618630,
  "data": [
    "https://...",
    "https://..."
  ]
}
```

После успешного ответа WB нужно подождать синхронизацию и повторно проверить
количество фото в карточке.

## Apply

Apply запускать только после подтверждения владельца:

```bash
TAKTERRA_WB_TOKEN_FILE=/path/to/wb_token \
PYTHONPATH=src python3 -m takterra_agent.cli apply-wb-card-create \
  --plan-run-id wb_card_create_plan_YYYYMMDDTHHMMSS \
  --confirmed-by-user \
  --allow-manual-review \
  --wait-seconds 600 \
  --poll-interval 30
```

Apply делает:

1. Загружает dry-run plan.
2. Проверяет существующие карточки WB по точному `vendorCode`.
3. Проверяет корзину WB по точному `vendorCode`.
4. Пропускает уже существующие активные карточки.
5. Генерирует WB barcodes для новых карточек.
6. Собирает payload для `upload` и `upload_add`.
7. Отправляет write-запросы с паузой по лимитам WB.
8. Ждет появления `nmID`.
9. Проверяет `/content/v2/cards/error/list`.
10. Загружает фото через `/content/v3/media/save`.
11. Сохраняет все request/response файлы.
12. Создает apply report.

Основные артефакты apply:

```text
data/runs/YYYY-MM-DD/wb_card_create_apply_*/
  source_plan_items.json
  existing_cards_before_apply.json
  trash_cards_before_apply.json
  generated_barcodes.json
  barcode_by_vendor.json
  wb_cards_upload_payload_applied.json
  wb_cards_upload_add_payloads_applied.json
  operations.json
  found_cards_after_apply.json
  media_operations.json
  pending_media_uploads.json
  wb_card_errors_after_apply.json
  wb_card_errors_relevant.json
  wb_card_create_apply_report.md
  summary.json
```

## Проверка после apply

Минимальная проверка:

1. `found_after_apply` равно количеству карточек в плане.
2. `missing_after_apply` равно `0`.
3. `operation_errors` равно `0`.
4. `media_upload_errors` равно `0`.
5. `pending_media_uploads` равно `0`.
6. `/content/v2/cards/error/list` не содержит ошибок по этим `vendorCode`.
7. В точечном поиске WB у каждой карточки есть `nmID`.
8. У каждой карточки есть ожидаемое количество фото.

После этого запустить read-only catalog check:

```bash
TAKTERRA_WB_TOKEN_FILE=/path/to/wb_token \
TAKTERRA_OZON_SELLER_CREDENTIALS_FILE=/path/to/ozon_seller_credentials \
PYTHONPATH=src python3 -m takterra_agent.cli fetch-catalog
```

Целевой результат:

```text
Ozon-only rows: 0
WB-only rows: 0
matched rows == Ozon products == WB cards
```

## Если WB вернул ошибку

Нельзя сразу повторять тот же apply.

Порядок:

1. Открыть `operations.json`.
2. Открыть response-файл конкретного запроса.
3. Проверить `/content/v2/cards/error/list`.
4. Точечно проверить active cards по `vendorCode`.
5. Точечно проверить trash cards по `vendorCode`.
6. Если карточка частично создана, не создавать повторно.
7. Если ошибка в payload, исправить генератор, пересобрать dry-run и только
   потом повторять apply.

Типовые ошибки:

```text
Weight with packaging should now be specified in variants[i].dimensions[j].weightBrutto
```

Решение: убрать `88952` из `characteristics`, передавать вес в
`dimensions.weightBrutto`.

```text
Поле Описание не должно содержать запрещенные символы
```

Решение: удалить emoji и пересобрать dry-run.

```text
Unexpected the specified card's vendor code is used in other cards
```

Решение: проверить active/trash по `vendorCode`. Возможно, карточка уже частично
создана или лежит в корзине.

## Корзина WB

Если карточка найдена в корзине, не создавать новую с тем же `vendorCode`.

Проверить корзину точечным поиском через adapter:

```python
wb.find_trash_cards_by_vendor_codes({"vendorCode"})
```

Восстановление:

```text
POST /content/v2/cards/recover
```

Структура:

```json
{
  "nmIDs": [976316288]
}
```

После восстановления подождать синхронизацию и проверить:

- карточка исчезла из корзины;
- карточка появилась в active search;
- фото на месте или догружены через media save.

## Финальный отчет

После завершения сценария создать сводный отчет:

```text
data/runs/YYYY-MM-DD/wb_card_create_completion_report.md
```

В отчете должны быть:

- список `vendorCode`;
- WB `nmID`;
- WB `imtID`;
- количество фото;
- финальный статус;
- итоговый catalog check;
- важные ошибки/исправления;
- список главных артефактов;
- подтверждение, что цены/остатки/скидки/реклама не менялись.

## Что обновить после успешной загрузки

1. `data/planning/marketplace_control_bot_discussion.md`
2. `data/catalog/wb_onboarding/ozon_only_to_add_to_wb.csv`
3. `data/catalog/processed/master_catalog.csv`
4. `data/catalog/processed/master_catalog.json`
5. Сводный completion report.

## Официальные WB API методы

Проверять актуальную документацию WB перед изменением реализации:

```text
https://dev.wildberries.ru/en/docs/openapi/work-with-products
```

Используемые методы:

```text
POST /content/v2/barcodes
POST /content/v2/cards/upload
POST /content/v2/cards/upload/add
POST /content/v2/cards/error/list
POST /content/v3/media/save
POST /content/v2/get/cards/list
POST /content/v2/get/cards/trash
POST /content/v2/cards/recover
```
