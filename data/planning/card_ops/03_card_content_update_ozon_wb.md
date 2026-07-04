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
   фото и ошибки по этим карточкам. Для отдельной проверки уже примененной
   карточки или пачки использовать verify-only маршрут:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli verify-card-content-update \
  --internal-sku <internal_sku>
```

`verify-card-content-update` работает read-only: читает owner-approved Layer 3
passport, проверяет фактическое состояние Ozon/WB по текущим
`offer_id`/`vendorCode` после смены артикулов и сохраняет отдельный verify run
с `overall_status`. Команда сверяет title/name, описание, цвета, габариты,
вес, фото, Ozon-хештеги, `23536=false`, `4497` и blocking product
errors/status.

Не использовать `plan-card-content-update` как финальную проверку после apply:
это новый dry-run, он показывает потенциальные diffs нового плана, а не
доказывает фактическое состояние карточки после операции.

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

Если после карточного apply нужно обновить только затронутые строки локального
контентного слоя, использовать targeted merge-refresh:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli fetch-card-content \
  --internal-sku <internal_sku> \
  --merge-existing
```

Флаг `--merge-existing` сохраняет существующий полный
`data/catalog/content/card_content_index.*`, `ozon_card_content.json` и
`wb_card_content.json`, заменяя только выбранные строки.

## API-маршруты

Ozon:

```text
POST /v3/product/import
POST /v1/product/import/info
```

Для Ozon используется только полный safe payload из текущей карточки:
attributes, category/type, barcode, фото, габариты, вес, price, old_price,
currency и VAT. Если этих данных нет, команда блокирует Ozon update.

## Подтвержденные recovery-ситуации Ozon

Штатная диагностика Ozon `PARTIAL_APPROVED`:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli ozon-partial-approved-diagnose
```

Команда читает `/v3/product/list` с `visibility=PARTIAL_APPROVED`,
`/v3/product/info/list`, `/v4/product/info/attributes`, классифицирует строки
как `active_errors`, `stale_visibility` или `needs_manual_review` и сохраняет
exact dry-run recovery. Применение только после approval владельца:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli apply-ozon-partial-approved-recovery \
  --plan-run-id <diagnose_run_id> \
  --confirmed-by-user
```

- Если Ozon отклоняет позывные с `DESCRIPTION_DECLINE` по атрибуту
  `4180 / Название`, не возвращать кавычки без отдельного решения владельца.
  Актуальная последовательность владельца:
  1. основной формат без кавычек и без запятых:
     `Шеврон на липучке позывной X нагрудный олива`;
  2. если Ozon отклоняет основной формат, fallback без кавычек, но с
     запятыми:
     `Шеврон на липучке позывной X, нагрудный, олива`.
  Менять только Ozon title/атрибут `4180`, если общий owner-approved title и
  WB уже согласованы в другом формате.
- Для категории шевронов обязательный Boolean-атрибут
  `23536 / Нужен код маркировки` должен присутствовать в payload как
  `false`. Проверять не только `/v1/product/import/info`, но и фактическое
  наличие атрибута в `/v4/product/info/attributes`.
- Для Ozon `#Хештеги` (`23171`) API-формат отличается от удобного
  owner-facing списка. В payload каждый хештег должен начинаться с `#`,
  хештеги разделяются пробелом, внутри хештега нельзя использовать пробелы,
  запятые и дефисы: фразы приводить к `_`, например
  `шеврон на липучке` -> `#шеврон_на_липучке`. Длина одного хештега с `#` -
  не больше `30` символов. Если передать через запятую или длинный хештег,
  Ozon блокирует весь `/v3/product/import` ошибками
  `BR_hashtags_symbols_limit` или сообщением
  `Хештег: укажите только буквы, цифры, знак # и нижнее подчеркивание`.
- Для существующих Ozon-карточек перед полным `/v3/product/import` нужно
  отправлять owner-approved набор атрибутов через
  `/v1/product/attributes/update` и дождаться его `task_id`. Этот endpoint
  может вернуть `task_id` в корне ответа, а `/v3/product/import` - в
  `result.task_id`; обработчик должен поддерживать оба формата.
- Recovery 2026-07-04: если карточка Ozon находится в `PARTIAL_APPROVED`
  только из-за отдельных атрибутов, не запускать полный карточный apply без
  необходимости. Сначала сделать точечный `/v1/product/attributes/update` по
  конкретным `offer_id` и `attribute_id`, затем проверить
  `/v4/product/info/attributes`, `/v3/product/info/list` и счетчик
  `PARTIAL_APPROVED`. В кейсе `chev_kit2_pz_text0013` и
  `chev_nr_voisk_pict0012` были закрыты `23536 / Нужен код маркировки=false`
  и `10096 / Цвет товара`; после исправления цвета у ВВУ ПВО Ozon дополнительно
  показал пустой `23536`, который также был закрыт точечным обновлением. Если
  после точечного patch остается только `OFFER_ID_EDIT_WRONG_SOURCE`, это уже
  отдельный блок смены seller SKU, а не проблема содержания карточки.
- Recovery 2026-07-04 для `OFFER_ID_EDIT_WRONG_SOURCE`: если
  `/v1/product/update/offer-id` уже успешно сменил `offer_id`, проверка
  `/v3/product/info/list` по `product_id` показывает новый `offer_id`, карточка
  продается, а в `PARTIAL_APPROVED` остался warning
  `OFFER_ID_EDIT_WRONG_SOURCE / editing offerID is prohibited for this upload
  source`, не повторять смену артикула. Подтвержденный маршрут на
  `chev_kit2_pz_text0043`: точечно пересохранить текущий `offer_id` через
  `/v1/product/attributes/update` с обязательным атрибутом
  `23536 / Нужен код маркировки=false`. После apply проверить
  `/v4/product/info/attributes`, `/v3/product/info/list` и счетчик
  `PARTIAL_APPROVED`. В тесте warning ушел, счетчик снизился `15 -> 14`,
  карточка осталась в продаже. Этот маршрут можно масштабировать на аналогичные
  строки только через batch dry-run с exact payload и owner approval.
- Batch recovery 2026-07-04: после one-SKU recovery Ozon перестал показывать
  `OFFER_ID_EDIT_WRONG_SOURCE` в `/v3/product/info/list`, но
  `/v3/product/list` с `visibility=PARTIAL_APPROVED` еще возвращал `14`
  товаров. После owner-approved batch dry-run повторное сохранение только
  текущего `23536=false` через `/v1/product/attributes/update` очистило stale
  видимость: счетчик `PARTIAL_APPROVED` снизился `14 -> 0`, `23536=false`
  подтвердился у всех `14`.
- Если `/v1/product/import/info` сначала возвращает `status=imported`, но
  карточка остается `Не обновлен`, нужно повторно прочитать import info после
  задержки: Ozon может позднее показать детальную ошибку, которой не было в
  первой быстрой проверке.
- Если Seller API не показывает причину, но `/v3/product/info/list` возвращает
  `status_description=Не обновлен`, открыть в ЛК
  `Товары -> Загрузить товары через шаблон -> История обновлений`:
  `https://seller.ozon.ru/app/products/import-history/file?currentType=full`.
  Проверенная ситуация 2026-07-04: API `/v1/product/import/info` вернул
  `imported` без ошибок, а ЛК показал реальный блокер по формату хештегов.
- Ошибка `BR_hashtag_brand` может быть вызвана не брендом карточки, а
  отдельным Ozon-хештегом, который Ozon распознал как чужой бренд. В кейсе
  `chev_pz_ng_text0040 / Урал` бренд карточки остался `VitalEmb`, а recovery
  состоял в удалении только хештегов `#позывной_урал`, `#шеврон_урал`,
  `#шевроны_урал`; после этого payload с `23536=false` применился.
- При policy-блокере `FB_UNWANTED` не архивировать карточку и не маскировать
  название без отдельного решения владельца.

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
