# 03. Изменение параметров существующих карточек Ozon/WB

Дата: 2026-06-28

## Итог

Цель операции: применить owner-approved Layer 3 passport к уже существующим
карточкам Ozon и WB: название, описание, размеры, вес, материал, состав,
комплектацию, цвета, хештеги/теги и другие разрешенные поля.

Статус автоматизации: штатные команды есть в CLI и `TaskRegistry`.

Быстрый путь по одному или нескольким owner-approved паспортам:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli apply-approved-card \
  --internal-sku <internal_sku> \
  --confirmed-by-user
```

Команда делает точечный `plan -> apply -> targeted verify -> result`.

Debug-путь с отдельным dry-run и apply:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli plan-card-content-update \
  --internal-sku <internal_sku>
```

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli apply-card-content-update \
  --plan-run-id <plan_run_id> \
  --confirmed-by-user
```

Команда `apply-approved-card` является штатным быстрым маршрутом. Команды
`plan-card-content-update` и `apply-card-content-update` использовать, когда
нужно отдельно посмотреть dry-run, разобрать блокировку или отладить API.

Ограничение текущего слоя: Ozon apply разрешается только если dry-run смог
собрать полный safe payload с текущими фото, габаритами, ценой, старой ценой и
НДС. Если коммерческих полей нет, Ozon-часть блокируется. WB-часть строится с
сохранением `nmID`, `vendorCode`, `sizes` и barcode.

## Источники

- owner-approved HTML;
- Layer 3 passport:

```text
data/catalog/master_passport/approved/<internal_sku>.json
```

- карта полей редакторов:

```text
data/planning/product_card_editor_field_map_runbook.md
```

## Что можно менять в этом маршруте

Ozon:

- название;
- аннотация/описание;
- габариты и вес упаковки;
- материал;
- размеры изделия;
- количество в упаковке;
- единиц в одном товаре;
- цвет товара;
- название цвета;
- ТН ВЭД;
- страна;
- вид выпуска;
- хештеги.

WB:

- наименование;
- описание;
- габариты и вес;
- цвет;
- вид декора;
- состав;
- страна производства;
- количество предметов в упаковке;
- комплектация;
- ТНВЭД.

## Что не смешивать с этой операцией

Не смешивать без отдельного action в HTML/plan:

- смену seller SKU;
- создание новой карточки;
- группировку карточек;
- фото/медиа;
- цены, скидки, акции;
- остатки.

## Быстрый штатный порядок

Цель маршрута - быстро применить уже согласованный owner-approved пакет, не
пересобирая весь каталог без необходимости.

1. Запустить быстрый путь по нужным SKU:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli apply-approved-card \
  --internal-sku <internal_sku> \
  --confirmed-by-user
```

2. Команда сама останавливается, если dry-run не `ready`.

3. Verify должен быть точечным: читать только затронутые Ozon `offer_id` /
   `product_id` и WB `vendorCode` / `nmID`, проверять только измененные поля,
   фото и ошибки по этим карточкам.

4. Владелец получает короткий result: `что было -> что стало`, что применилось,
   что не применилось и почему.

## Что больше не делать после каждой карточки

Не запускать автоматически после одной-двух карточек:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli fetch-card-content
```

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli build-content-master
```

Полный refresh 710 карточек делать только отдельной задачей: перед массовым
аудитом, перед большой пачкой изменений, после серии apply или если точечный
verify показывает рассинхрон, который нельзя проверить по конкретным SKU.

## API-маршруты

Ozon:

```text
POST /v3/product/import
POST /v1/product/import/info
```

Для Ozon используется только полный safe payload из текущей карточки:
attributes, category/type, barcode, фото, габариты, вес, price, old_price,
currency и VAT. Если этих данных нет, команда блокирует Ozon update.

WB:

```text
POST /content/v2/cards/update
```

Для WB update нужно сохранять `nmID`, `vendorCode`, `sizes`, barcode и
изменять только согласованные поля.

## Ближайшая автоматизация

Доработать:

- dictionary value lookup для Ozon-атрибутов со справочниками;
- точечный drift-check before apply по title/description/dimensions/photos;
- обновление Layer 3 apply status;
- owner-facing краткий report по `before -> after`;
- отдельную команду пакетного source refresh для 20+ карточек или завершенной
  серии apply, без привязки к каждой одной карточке.
