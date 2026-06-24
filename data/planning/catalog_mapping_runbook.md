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
data/catalog/unified/products.json
data/catalog/unified/products.csv
data/catalog/unified/future_seller_sku_plan.csv
data/catalog/content/content_master.json
data/catalog/content/content_master.csv
data/catalog/content/content_audit.json
data/catalog/content/content_audit.csv
data/catalog/content/card_content_index.json
data/catalog/content/card_content_index.csv
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

## Сборка внутреннего общего каталога

После owner review confirmed mapping общий product-level каталог собирается
отдельной read-only командой:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli build-unified-catalog
```

Входы по умолчанию:

```text
data/catalog/mapping/ozon_wb_internal_sku_confirmed.csv
data/catalog/ozon/processed/ozon_catalog.csv
data/catalog/wb/processed/wb_catalog.csv
```

Выходы:

```text
data/catalog/unified/products.json
data/catalog/unified/products.csv
data/runs/<date>/<run_id>/unified_catalog_report.md
data/runs/<date>/<run_id>/unified_catalog_issues.json
```

`products.csv/json` - внутренний слой проекта. Он не меняет Ozon `offer_id` и
WB `vendorCode`, не является разрешением на переименование артикулов продавца
и не должен использоваться для marketplace write без проверки native IDs.

## Использование в отчетах и боте

С 2026-06-24 `daily-morning-report --seller-v3` читает
`data/catalog/unified/products.json`, если файл существует, и выводит:

- общее количество unified products;
- количество товаров с внутренним `internal_sku`;
- связанные Ozon+WB товары;
- товары только на Ozon и только на WB;
- активные товары по Ozon/WB;
- internal SKU в товарных примерах остатков, если он есть в unified catalog.

Telegram `/catalog` показывает последний `catalog-build-unified` из
`data/runs/index.jsonl` и ключевые цифры из безопасного `summary` artifact.
Если нужен свежий каталог, сначала выполнить `build-unified-catalog`.

Telegram `/catalog <запрос>` ищет по `data/catalog/unified/products.json` и
возвращает карточку товара или короткий список совпадений. Поддержанные ключи:
`internal_sku`, `internal_product_id`, название, Ozon `offer_id`,
`product_id`, `sku`, Ozon barcode, WB `vendorCode`, `nmID`, WB barcode.
Barcode подтягивается из `data/catalog/ozon/processed/ozon_catalog.csv` и
`data/catalog/wb/processed/wb_catalog.csv`, если файлы доступны.

Это read-only слой: он не переименовывает Ozon `offer_id`, WB `vendorCode` и
не выполняет cross-marketplace write-операции.

## Единый контентный слой

После сборки `data/catalog/unified/products.csv` можно собрать read-only
card content snapshot и content master:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli fetch-card-content

PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli build-content-master
```

Входы по умолчанию:

```text
data/catalog/unified/products.csv
data/catalog/ozon/processed/ozon_catalog.csv
data/catalog/wb/processed/wb_catalog.csv
data/pricing/pricing_status.csv
data/catalog/content/card_content_index.csv
```

`data/pricing/pricing_status.csv` и
`data/catalog/content/card_content_index.csv` являются optional: если их нет,
команда строит контентный слой без action-price и без полей карточного
snapshot.

Выходы:

```text
data/catalog/content/content_master.csv
data/catalog/content/content_master.json
data/catalog/content/content_audit.csv
data/catalog/content/content_audit.json
data/runs/<date>/<run_id>/catalog_content_master_report.md
```

`fetch-card-content` дополнительно пишет:

```text
data/catalog/content/card_content_index.csv
data/catalog/content/card_content_index.json
data/catalog/content/ozon_card_content.json
data/catalog/content/wb_card_content.json
data/runs/<date>/<run_id>/raw/
```

Назначение content master:

- держать рядом текущие Ozon/WB названия одного внутреннего товара;
- показывать `title_alignment_status`, `marketplace_presence`,
  `full_snapshot_status`, `transfer_direction`, `content_review_priority` и
  `next_content_step`;
- подхватывать счетчики фото, наличие описания, количество характеристик,
  Ozon-хештеги и WB-теги из `card_content_index`;
- отделять товары, которые есть только на одной площадке, от подтвержденных
  Ozon+WB товаров;
- подготавливать очередь для будущего unified SEO/content draft.

Ограничение: `card_content_index` содержит производные поля и raw snapshot, но
не заменяет визуальный просмотр фото. Перед рекомендациями по конкретной
карточке нужно выполнить фото-аудит по `product_card_work_runbook.md`.

Smoke-проверка 2026-06-24 на текущих локальных данных:

```text
input_products: 710
content_master_rows: 710
confirmed_rows: 269
ozon_only_rows: 279
wb_only_rows: 162
both_marketplaces_rows: 269
title_mismatch_rows: 205
missing_cost_rows: 441
audit_rows: 1087
```

Smoke-проверка `fetch-card-content` 2026-06-24 на 3 товарах через реальные
read-only API:

```text
products: 3
content_index_rows: 6
ozon_rows: 3
wb_rows: 3
found_rows: 6
missing_rows: 0
description_present_rows: 6
photo_lt5_rows: 0
ozon_attributes: 3
ozon_descriptions: 3
wb_cards: 431
errors: none
```

Минимальные поля общего каталога:

```text
internal_product_id
internal_sku
product_name
product_group
pack_qty
cost_total
cost_per_unit
ozon_offer_id
ozon_product_id
ozon_sku
wb_vendor_code
wb_nm_id
mapping_status
active_ozon
active_wb
notes
```

Правила сборки:

- в `confirmed` попадают только строки confirmed/approved mapping;
- товары из Ozon/WB catalog, которых нет в confirmed mapping, остаются
  `ozon_only` или `wb_only`, а не считаются ошибкой;
- дубли `internal_sku`, `ozon_offer_id`, `wb_vendor_code` в confirmed mapping
  попадают в `unified_catalog_issues.json`;
- ссылки mapping на отсутствующие строки локальных каталогов попадают в issues;
- `pack_qty` берется из `kitN` в `internal_sku`, иначе равен `1`;
- текущая базовая себестоимость для расчета `cost_total` - `85` рублей за
  единицу/штуку по зафиксированному правилу владельца; при изменении
  себестоимости нужно обновить расчетный слой отдельно.

Smoke-проверка 2026-06-21 на текущих локальных данных:

```text
ozon_catalog_rows: 548
wb_catalog_rows: 431
mapping_rows: 269
confirmed_mapping_rows: 269
unified_products: 710
confirmed_products: 269
ozon_only_products: 279
wb_only_products: 162
issue_count: 0
```

Важное правило статусов: для внутреннего unified catalog подтвержденными
считать не только ровно `owner_confirmed`, но и все owner-approved batch/status
варианты вида `owner_confirmed_*` и `owner_corrected_*`. Эти статусы появляются
после пакетного согласования владельцем и не являются черновиком.

## План внутренних артикулов для Ozon-only/WB-only

После сборки `data/catalog/unified/products.csv` marketplace-only товарам нужно
присвоить нормальные внутренние `internal_sku`, но без изменения реальных
артикулов продавца на Ozon/WB.

Штатная read-only команда:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli plan-internal-skus
```

Вход:

```text
data/catalog/unified/products.csv
```

Выходы:

```text
data/catalog/unified/internal_sku_assignment_plan.csv
data/catalog/unified/internal_sku_assignment_plan.json
data/runs/<date>/<run_id>/internal_sku_assignment_report.md
```

План не является apply. Он только предлагает `internal_sku` для строк со
статусами `ozon_only` и `wb_only`.

Статусы строк плана:

- `auto_candidate` - правило смогло уверенно предложить внутренний артикул,
  но строка все равно требует owner review перед записью;
- `needs_owner_review` - артикул предложен, но есть риск: неизвестно место
  ношения, не указано количество в комплекте, найдено совпадение названия с
  уже подтвержденным товаром или другая неоднозначность;
- `unsupported_product_type` - текущая схема `chev/nash/loop` не покрывает
  товар. Нельзя насильно присваивать такому товару шевронный артикул;
- `conflict` - предложенный артикул конфликтует с уже занятым.

Статус owner-review для отложенного товара:

- `owner_deferred_internal_sku` - владелец решил не присваивать внутренний
  артикул сейчас. Использовать для товаров вне текущего ассортимента
  шевронов/нашивок/петлиц или товаров, которые пока не планируются к
  поставкам на Ozon/WB. Такая строка фиксируется в
  `data/catalog/unified/internal_sku_assignment_owner_review.csv` без
  `approved_internal_sku`, чтобы она не возвращалась в активный owner-review,
  но могла быть поднята позже отдельным решением.

Правила безопасности:

- если название marketplace-only товара совпадает с уже confirmed товаром,
  строка уходит в `needs_owner_review`, даже если SKU можно предложить;
- если товар не похож на шеврон, нашивку или петлицу, он получает
  `unsupported_product_type`;
- для комплектов с неизвестным количеством не делать auto-решение;
- для товаров без понятного места ношения не делать auto-решение;
- предложенные `internal_sku` проверяются на конфликт с уже существующими
  `internal_sku` и между собой.

Smoke-проверка 2026-06-21 на текущем `products.csv`:

```text
input_products: 710
marketplace_only_products: 441
ozon_only_products: 279
wb_only_products: 162
auto_candidate_rows: 97
needs_owner_review_rows: 293
unsupported_product_type_rows: 51
conflict_rows: 0
proposed_internal_skus: 379
```

Решение owner-review 2026-06-23:

- текущий актуальный ассортимент - шевроны, нашивки, петлицы и комплекты из
  них - получил внутренние `internal_sku` или был явно отклонен владельцем;
- `62` товара вне текущего ассортимента записаны как
  `owner_deferred_internal_sku`: `51` строка `unsupported_product_type` и `11`
  строк `needs_owner_review` с неизвестным типом товара;
- решение владельца: присвоение новых артикулов этим товарам отложить, на
  Ozon/WB этот товар пока поставляться не будет;
- если эти товары позже станут актуальны, сначала добавить новые типы/префиксы
  в `data/planning/seller_sku_rules.md`, затем пересобрать
  `plan-internal-skus` и провести отдельный owner-review.

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
internal_sku
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

На этапе ручного сопоставления Ozon/WB нужно присваивать подтвержденной
товарной сущности единый внутренний артикул `internal_sku` по правилу
`data/planning/seller_sku_rules.md`. Этот `internal_sku` становится целевым
будущим артикулом продавца для Ozon и WB, но текущие marketplace seller SKU
пока не меняются.

Для каждой подтвержденной строки сохранять:

- `internal_sku`;
- текущие Ozon identifiers: `offer_id`, `product_id`, `sku`;
- текущие WB identifiers: `vendorCode`, `nmID`, barcode;
- статус подтверждения владельцем;
- источник кандидата и основание сопоставления.

## Пакетное подтверждение однотипных кандидатов

Обычный режим owner review: агент отправляет владельцу строго одну пару Ozon/WB
за раз: первое фото, название, marketplace identifiers и предварительный
`internal_sku`. После ответа владельца `да` строка фиксируется в mapping, после
`нет` строка отклоняется или переносится на ручной разбор.

Для пакетных review-листов с изображениями использовать компактный
contact-sheet формат, удобный в Telegram:

- формат подтвержден владельцем после пачки 4 2026-06-20 как удобный для
  просмотра и достаточно качественный; использовать его как стандарт для
  следующих пачек;
- ширина около `1200-1260 px`, не растягивать до широких двухколоночных
  листов;
- в каждой строке две первые фотографии Ozon/WB ставить рядом слева;
- текст Ozon/WB располагать справа от фотографий, без большого промежутка
  между маркетплейсами;
- использовать читаемый шрифт и высокое качество JPEG/PNG;
- не отправлять владельцу растянутые листы, где фото далеко друг от друга,
  а текст мелкий или плохо читается.

Начиная с 2026-06-21 основной принцип подготовки новых кандидатов:

- сопоставлять по трем признакам вместе: первое фото, название и размер;
- размер считать без учета порядка сторон: `100*75 мм`, `75*100 мм`,
  `100х75`, `75х100` - один и тот же размерный ключ `75x100`;
- если размер найден только у одной стороны, строку можно показать только как
  пониженную уверенность с пометкой `size_missing`, а не как строгий
  автоматический кандидат;
- если размеры найдены у обеих сторон и нормализованные ключи разные, строку
  не считать строгим кандидатом даже при похожем названии;
- фото использовать как обязательный визуальный фактор owner review: в
  Telegram/Excel показывать первые изображения Ozon и WB рядом;
- технический фото-score может помогать сортировке, но не заменяет визуальное
  подтверждение владельца.

Жесткий semantic gate перед scoring обязателен. Агент сначала извлекает
смысловой ключ товара, и только после этого смотрит фото, размер и технический
score. Если смысловой ключ не совпадает, пару нельзя показывать как кандидата
даже с похожим фото, размером или общей группой товара.

Обязательные смысловые ключи:

- для ведомств/структур ключ должен совпадать точно: `ФСИН` нельзя сравнивать
  с `Росгвардия`, `МВД`, `ФСБ`, `ФСО` и другими структурами;
- для позывных ключом является сам позывной/текст: `Урал` нельзя сравнивать с
  `Кабан`, даже если это оба нагрудные позывные одного цвета;
- для петлиц ключом является род/служба/структура: петлицы `медицинская
  служба` нельзя сравнивать с петлицами `сухопутные войска`;
- для комплектов сравнивается состав комплекта и структура/тема всех значимых
  элементов; общий признак `комплект шевронов` недостаточен;
- для нашивок и шевронов с текстом текст/надпись является первичным ключом;
- для картинок/эмблем первичным ключом является конкретная эмблема, структура,
  подразделение или явно выраженная тематика, а не только форма и цвет.

Фото-хеш, визуальная похожесть, `size_family` и размер упаковки являются
вторичными признаками сортировки после semantic gate. Они не могут
перекрывать несовпадение структуры, позывного, рода петлиц или состава
комплекта.

Если смысловой ключ не удалось надежно извлечь хотя бы с одной стороны, строку
нужно переносить в `manual_search`/`unmatched`, а не добавлять в пачку
кандидатов. Низкая уверенность не является оправданием для сравнения явно
разных товаров.

Перед отправкой кандидата владельцу агент обязан выполнить собственную ручную
проверку:

- прочитать оба названия полностью, а не только общие ключевые слова;
- сверить смысловой ключ, структуру/тематику, позывной или текст;
- открыть и визуально посмотреть первые фото Ozon и WB;
- убедиться, что фото показывают один и тот же товар, а не только близкую
  тему, цвет или форму;
- если фото или смысл различаются, не отправлять пару владельцу на
  сопоставление, а сохранить ее как `agent_rejected` с причиной;
- владельцу отправлять только изображение кандидата, который агент сам
  считает допустимым после чтения названий и визуального просмотра фото.

Пачки кандидатов готовить отдельно:

1. комплекты, если в остатке есть `qty > 1`;
2. петлицы (`type=loop`);
3. нашивки (`type=nash`);
4. одиночные шевроны (`type=chev`, `qty == 1`).

Не смешивать эти группы в одном review-листе, даже если название похоже.

Для повторяемых однотипных групп допускается пакетный dry-run, но не прямое
молчаливое подтверждение. Сначала агент формирует файл dry-run со всеми
строками, критериями отбора, предлагаемыми `internal_sku`, проверкой дублей и
конфликтов. Пакет фиксируется в mapping только после явного подтверждения
владельца.

Для owner-review присвоения внутренних артикулов marketplace-only товарам
каждый согласованный пакет нужно сразу сохранять в рабочий CSV:

```text
data/catalog/unified/internal_sku_assignment_owner_review.csv
```

Минимальные поля: источник, batch/номер строки, native ID площадки, название,
исходный предложенный `internal_sku`, утвержденный `internal_sku`, статус
`owner_confirmed_internal_sku` или `owner_corrected_internal_sku`, источник
решения и примечание. Перед записью агент обязан проверить дубли утвержденных
`internal_sku` внутри пакета и конфликты с уже подтвержденным mapping. Если
правка владельца меняет префикс и старый номер начинает конфликтовать с другой
согласованной строкой, нужно сохранить следующий свободный номер в новом
префиксе и явно отметить это в `notes`.

Рабочий формат owner-review для оставшихся `needs_owner_review` строк:

- согласовывать пачками до `20` строк;
- в одной пачке не смешивать разные группы товаров;
- рекомендуемый порядок групп: петлицы, комплекты, нашивки, одиночные шевроны,
  затем спорные строки;
- в чате показывать номер, площадку, native ID, название, текущий черновой
  `internal_sku` и рекомендацию агента;
- владелец отвечает по номерам: `да`, `нет` или `N -> corrected_internal_sku`;
- после согласования агент сразу записывает результат в
  `data/catalog/unified/internal_sku_assignment_owner_review.csv`, пересчитывая
  свободные номера и проверяя дубли.

Дополнительный ручной режим для остатка WB-only: владелец может запросить
изображения несопоставленных WB-товаров без подбора пары Ozon агентом. В этом
режиме агент:

- берет только текущие несопоставленные WB `vendorCode` после вычитания
  подтвержденного mapping;
- фильтрует нужную товарную группу, например `type=chev` для шевронов;
- формирует изображения списков по `20` позиций: фото, номер позиции,
  глобальный номер, `vendorCode`, название и короткие признаки `qty/position/theme`;
- сохраняет CSV всего списка и CSV каждого батча в `data/runs/...`;
- отправляет владельцу изображения списков в Telegram;
- владелец отвечает по каждой позиции: `номер = offer_id Ozon` или
  `номер = нет`, если товара на Ozon нет;
- агент не записывает mapping по такому списку до последующей проверки
  указанного Ozon `offer_id`, сверки фото/названия и явного подтверждения
  результата записи.

## Будущая работа с ассортиментными пробелами

В будущей работе с ассортиментом остаток каталога можно разделять не как ошибку
mapping, а как отдельный ассортиментный backlog:

- товары, которые есть на Ozon, но отсутствуют на WB;
- товары, которые есть на WB, но отсутствуют на Ozon.

Такие товары не нужно автоматически считать дублями или заводить фиктивный
mapping. Когда владелец поставит задачу на этот анализ, для них нужно
подготовить read-only отчет:

1. текущий marketplace, где товар уже продается;
2. native identifiers: Ozon `offer_id/product_id/sku` или WB
   `vendorCode/nmID/barcode`;
3. название, фото, тип товара, размер, комплектность и тематика;
4. продажи, выкупы, остатки и маржинальность за выбранный период;
5. SEO-видимость и поисковые запросы, если данные доступны;
6. рекомендация: переносить на другой marketplace, отложить, снять с продажи
   или оставить только на текущем marketplace.

Кандидаты на перенос выбирать не по факту отсутствия на второй площадке, а по
практическому потенциалу: товар хорошо продается, имеет остатки/производственную
возможность, не несет правовых или контентных рисков и может быть корректно
оформлен на второй площадке.

Перенос карточки на другой marketplace является опасной карточной операцией и
выполняется только через:

```text
read-only -> transfer dry-run -> owner review -> approved -> apply -> verify -> result
```

При переносе нужно сразу адаптировать карточку под SEO второй площадки и
заполнить идентичные характеристики между Ozon и WB настолько, насколько это
позволяют категории и обязательные поля площадок:

- название;
- описание;
- характеристики;
- материал и состав;
- размер, вес и упаковка;
- комплектация;
- фото и их порядок;
- цвет/название цвета или ближайшие аналоги полей;
- хештеги/ключевые слова, если поле есть на площадке.

Если Ozon и WB требуют разные форматы, единым источником смысла считается
внутренний карточный brief, а marketplace payload адаптируется под ограничения
конкретной площадки. Различия фиксировать в dry-run отчете до согласования.

Для тематических review-листов, где один товар Ozon может повторяться с
несколькими альтернативными WB-вариантами, owner approval по одному номеру
означает подтверждение только этой пары. Остальные альтернативы из этого
review-листа нужно сохранить в `not_applied*.csv` с причиной отклонения, чтобы
они не возвращались как подтвержденные кандидаты в следующих проходах. При
записи подтвержденной пары `internal_sku` пересчитывать по следующему
свободному номеру внутри фактически подтвержденного префикса, а не копировать
черновой номер из review-листа, если предыдущие альтернативы отклонены.

Подтвержденный кандидат на автоматизацию: комплекты позывных `kit2_pz`.
Строгий критерий пакетного dry-run:

- `ozon_type == wb_type == позывной`;
- `ozon_qty == wb_qty > 1`;
- `ozon_candidate_rank == 1`;
- `score == 1.0`;
- `match_basis` содержит `vendor_code_contains_ozon_offer_id`, `name`,
  `quantity`, `photo_very_strong`;
- строка еще не подтверждена в mapping;
- внутри dry-run нет дублей по `ozon_offer_id` и `wb_vendor_code`;
- для количества, отличного от `2`, нужен отдельный review префикса `kitN`.

Для таких строк предварительный `internal_sku` формируется по правилу:

```text
chev_kit2_pz_textNNNN
```

Второй подтвержденный кандидат на автоматизацию: одиночные нагрудные
позывные `pz_ng` в цветах `олива` и `мох`. Строгий критерий пакетного
dry-run:

- `ozon_type == wb_type == позывной`;
- `ozon_qty == wb_qty == 1`;
- цвет Ozon/WB совпадает и входит в набор `олива`, `мох`;
- строка похожа на нагрудный позывной: `нагруд` в названии или `pzng` в
  `wb_vendor_code`;
- `ozon_candidate_rank == 1`;
- `score == 1.0`;
- `match_basis` содержит `vendor_code_contains_ozon_offer_id`, `name`,
  `quantity`, `color`, `photo_very_strong`;
- строка еще не подтверждена в mapping;
- внутри dry-run нет дублей по `ozon_offer_id` и `wb_vendor_code`.

Для таких строк предварительный `internal_sku` формируется по правилу:

```text
chev_pz_ng_textNNNN
```

Подтвержденный кандидат на пакетный dry-run: товары тематики `sht` ("Шторм").
Пакетная запись в mapping допускается только после вывода owner-review списка
и явного подтверждения владельца. Строгий критерий batch-list:

- строка еще не подтверждена в mapping;
- `ozon_type == wb_type == шеврон`;
- `ozon_qty == wb_qty == 1`;
- `score == 1.0`;
- `match_basis` содержит `vendor_code_contains_ozon_offer_id`, `name`,
  `quantity` и `photo_very_strong` или `photo_strong`;
- Ozon/WB title или WB `vendorCode` явно указывают на "Шторм" / `sht`;
- внутри dry-run нет дублей по `ozon_offer_id`, `wb_vendor_code` и
  предлагаемому `internal_sku`.

Для таких строк предварительный `internal_sku` формируется по правилу:

```text
chev_nr_sht_<text|pict>NNNN
```

Если размер явно `80*50 мм`, использовать место ношения `kp` вместо `nr`.

Подтвержденный кандидат на пакетный dry-run: товары тематики `voisk`
("войсковые" / рода войск). Пакетная запись в mapping допускается только после
вывода owner-review списка и явного подтверждения владельца. Строгий критерий
batch-list:

- строка еще не подтверждена в mapping;
- `ozon_type == wb_type == шеврон`;
- `ozon_qty == wb_qty == 1`;
- `ozon_candidate_rank == 1`;
- `score == 1.0`;
- `match_basis` содержит `vendor_code_contains_ozon_offer_id`, `name`,
  `quantity` и `photo_very_strong` или `photo_strong`;
- Ozon/WB title или WB `vendorCode` явно указывают на войсковую тематику,
  род войск или близкий подтверждаемый владельцем военный тип товара;
- внутри dry-run нет дублей по `ozon_offer_id`, `wb_vendor_code` и
  предлагаемому `internal_sku`.

Из пакетного `voisk` dry-run исключать товары, для которых уже есть или нужна
отдельная тематическая группа: `raz` ("Рыболовные войска"), `prikol`
("Войска тыла" и аналогичные юмористические товары), `bpla` ("Беспилотные
войска"), `sht` ("Шторм"), `gv` ("группировка войск"), а также товары структур
`fsb/fso/fsin/fssp/mvd/rg`, если владелец не подтвердил обратное.

Для таких строк предварительный `internal_sku` формируется по правилу:

```text
chev_<nr|ng|kp|back>_voisk_<text|pict>NNNN
```

Если размер явно `80*50 мм`, использовать место ношения `kp`. Если товар
нагрудный или узкий `125х25-40 мм`, использовать `ng` и обычно `text`.
Круглые/квадратные эмблемы родов войск обычно получают `nr` и `pict`.
Военная прокуратура при присвоении внутренних артикулов относится к
войсковой тематике `voisk`.
Военная полиция при присвоении внутренних артикулов также относится к
войсковой тематике `voisk`, а не к структуре `mvd`.

Подтвержденная тематическая группа для ручного mapping: `gv` ("группировка
войск"). Применять для товаров с названием или смыслом "Группировка войск",
например "Группировка войск Север". Это отдельная тематика: не записывать такие
товары как общий `svo` и не смешивать с `voisk` ("войсковые" / рода войск).

Для таких строк предварительный `internal_sku` формируется по правилу:

```text
chev_<nr|ng|kp|back>_gv_<text|pict>NNNN
```

В ускоренном ручном согласовании владелец может переопределить тематическую
или структурную группу по номеру строки review-списка. Такое переопределение
считается owner approval для локального mapping, но не является разрешением
переименовывать seller SKU на Ozon/WB.

Подтвержденные дополнительные структурные блоки:

- `chvk` - ЧВК;
- `oborg` - общественные организации.

Подтвержденная тематическая группа для ручного mapping: `brig` ("бригады").
Применять, если владелец явно относит товар к тематике бригад. Такие товары не
смешивать с общей группой `raz` и не записывать как общий `svo`, если владелец
выделил бригады отдельно.

Для таких строк предварительный `internal_sku` формируется по правилу:

```text
chev_<nr|ng|kp|back>_brig_<text|pict>NNNN
```

Для петлиц использовать отдельный тип товара `loop`, а не `chev` и не `nash`.
Подтвержденный формат для петлиц ФСИН:

```text
loop_fsin_0001
```

Если в review-списке петлица была предварительно классифицирована как шеврон,
после подтверждения владельца нужно заменить `chev_*` на `loop_<structure>_NNNN`
до записи в mapping.

Подтвержденная тематическая группа для ручного mapping: `form`
("форменные" / служебные надписи без структуры). Применять к шевронам с
назначениями press/пресса, охрана, кадет, security/секьюрити, staff/стафф и
аналогичными служебными надписями, если у товара нет более точной структуры
`mvd/fsin/rg/fso/fsb/fssp` или отдельной согласованной темы.

Для таких строк предварительный `internal_sku` формируется по правилу:

```text
chev_<nr|ng|kp|back>_form_<text|pict>NNNN
```

Примеры:

```text
chev_ng_form_text0001
chev_back_form_text0001
```

Подтвержденная тематическая группа для ручного mapping: `fan` ("фанатские"
шевроны). Применять, если владелец явно относит товар к фанатской тематике или
если это подтверждается названием/старым seller SKU и не конфликтует с более
точной структурой или темой.

Для таких строк предварительный `internal_sku` формируется по правилу:

```text
chev_<nr|ng|kp|back>_fan_<text|pict>NNNN
```

Подтвержденная тематическая группа для ручного mapping: `berserk` ("Берсерк").
Применять, если владелец явно относит товар к теме Берсерк или это прямо
следует из названия. Не смешивать с общей группой `raz`, если владелец выделил
Берсерк отдельно.

Для таких строк предварительный `internal_sku` формируется по правилу:

```text
chev_<nr|ng|kp|back>_berserk_<text|pict>NNNN
```

Особое правило для нашивок: названия вида `Комплект нашивок ...` относятся к
типу товара `nash`, а не `chev`, даже если старый marketplace seller SKU
содержит `kit` или похож на шевронный комплект. Комплектность сохраняется как
`kitN`, например:

```text
nash_kit2_nr_mvd_pict0001
nash_kit4_mvd_pict0001
```

Особое правило для силовых структур:

- нарукавные шевроны МВД, ФСБ, ФСИН, Росгвардии, ФССП, ФСО и аналогичных
  структур считать `pict`, а не `text`;
- комплекты шевронов или нашивок силовых структур по текущей схеме также
  считать `pict`;
- блок `text/pict` пока не удалять из `internal_sku`, потому что это изменение
  базовой схемы и требует отдельного согласования;
- если в комплекте больше 2 штук, не указывать место ношения `nr/ng/kp/back`:
  комплект может включать нагрудные, наспинные и нарукавные шевроны. Например:
  `chev_kit4_fssp_pict0001`, `nash_kit4_mvd_pict0001`;
- для `kit2` место ношения можно оставить, если комплект явно состоит из двух
  однотипных нарукавных/нагрудных/других шевронов.
- для `kit2` нарукавных комплектов силовых структур использовать ранее
  принятую форму `chev_kit2_nr_<structure>_pictNNNN`;
- для смешанных `kit2`, например нагрудный + нарукавный, место ношения не
  указывать.

В name-first пачках это правило проверять до записи в mapping: сначала
определить тип товара по словам `шеврон/нашивка/петлица`, затем комплектность,
место ношения, тему/структуру и только после этого пересчитать следующий
свободный `internal_sku`.

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
