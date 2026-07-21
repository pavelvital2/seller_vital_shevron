# Card Audit Output Contract

Формат результата fresh-аудитора одной карточки Vital Shevron.

## Файлы

Минимальный результат:

```text
audit.json
<internal_sku>.html
```

Если одного адаптивного HTML недостаточно:

```text
<internal_sku>_desktop.html
<internal_sku>_mobile.html
```

Если `internal_sku` отсутствует, использовать безопасный fallback:

```text
<internal_product_id>.html
```

## HTML

HTML должен быть понятен владельцу без открытия JSON.

Для Vital Shevron использовать owner-approved шаблон:

```text
data/planning/card_audit_agent_docs/card_audit_html_template.html
```

Актуальная версия шаблона повторно подтверждена владельцем 2026-07-21 на
карточке `chev_kit4_fssp_pict0001` и обязательна для всех последующих
паспортов. Допускаются только точечные адаптации под данные карточки. Нельзя
менять оформление, сокращать таблицы или переставлять блоки без прямого
указания владельца. Фиксированный порядок: шапка, краткий вывод,
идентификаторы, один contact sheet, текстовый фото-аудит, SEO query pack,
семантический план и Ozon-хештеги с evidence, описание 3 блоками, итоговый
предлагаемый вариант, полный набор целевых параметров Ozon/WB, рекомендации,
все текущие параметры Ozon/WB в `details`, зафиксированное решение владельца.

Если HTML передается владельцу через Telegram как один документ, он должен быть
self-contained: коллаж и ключевые изображения встраивать в HTML через
`data:image/...;base64,...`. Относительные пути к изображениям разрешены только
для локального просмотра рядом с файлами отчета; в одиночном Telegram-документе
они не откроются. Дополнительно коллаж фото нужно отправлять отдельным
изображением, если владелец согласовывает карточку с телефона.

Требования к мобильной читаемости:

- на viewport `390x844` страница не должна иметь горизонтального overflow;
- `document.documentElement.scrollWidth` должен быть равен `clientWidth` или
  отличаться только в пределах технической погрешности;
- таблицы на мобильном должны превращаться в карточки или находиться внутри
  локального scroll-контейнера, который не расширяет всю страницу;
- длинные пути, SKU, URL, названия файлов и значения характеристик должны
  переноситься (`overflow-wrap:anywhere`, `word-break:break-word` или
  эквивалент);
- если HTML содержит таблицы, проверяющий обязан отдельно проверить mobile
  viewport и не принимать отчет, который расширяет страницу.

Перед отправкой HTML владельцу в Telegram проверяющий или оркестратор обязан
сам открыть отчет в браузере и визуально проверить минимум два viewport-а:
мобильный `390x844` и десктопный `1366x1000`. Нужно сохранить/зафиксировать
результат проверки в `audit.json`: `layout_validation_status`,
`scrollWidth/clientWidth` для mobile/desktop и путь к скриншотам, если они
сохранялись. Нельзя отправлять владельцу HTML, который агент сам не посмотрел
после последней правки разметки.

Обязательные блоки:

1. **Шапка**
   - internal SKU;
   - Ozon IDs;
   - WB IDs;
   - тип товара;
   - наличие на площадках;
   - статус аудита;
   - основные риски.
2. **Фото**
   - коллаж или все фото;
   - описание каждого фото;
   - что подтверждено фото;
   - чего не хватает.
   - для ведомственного товара: какие WB-слайды содержат видимый герб или
     геральдический ведомственный знак и защищены ретушью/водяным знаком,
     какие содержат только текстовую надпись или нейтральны и не требуют
     защиты;
   - `wb_departmental_symbol_policy.media_apply_status`: `allowed_verified`,
     `blocked_pending_watermarked_assets` или `not_applicable`.
3. **Текущее состояние**
   - Ozon: название, описание, характеристики, фото, хештеги, цвет,
     название цвета, группировка;
   - WB: название, описание, характеристики, фото, теги/поисковые поля,
     группировка;
   - каждое marketplace-поле имеет источник именно своей площадки; отсутствие
     поля в Layer 1 обозначается `not_confirmed`, а не заполняется значением
     другой площадки;
   - размер, упаковка, вес, материал, состав, ТН ВЭД.
   - ссылки на карточки в ЛК Ozon/WB, если они есть во входном пакете;
   - все заполненные параметры из snapshot/API: для Ozon все текущие
     attributes плюс dimensions/weight/model_info, для WB все current
     characteristics плюс dimensions/sizes/barcodes.
4. **Рекомендации**
   - `сейчас -> рекомендую -> почему`;
   - статус рекомендации: `recommended`, `needs_owner_review`,
     `needs_source_check`, `not_recommended`;
   - batch group.
5. **Итоговый предлагаемый вариант**
   - `canonical_title`;
   - `ozon_title`;
   - `wb_title`;
   - `canonical_description`;
   - `ozon_description`;
   - `wb_description`;
   - целевые физические параметры;
   - материал/состав;
   - SEO;
   - Ozon attributes;
   - WB attributes;
   - группировка как рекомендация.

В HTML должен быть отдельный блок `Целевые параметры для пачного заполнения`.
Он строится по карте:

```text
data/planning/product_card_editor_field_map_runbook.md
```

В блоке показывать только параметры, относящиеся к шевронам, нашивкам,
петлицам и комплектам:

Полный релевантный список обязателен. Нельзя выводить сокращённую выборку даже
если значения уже присутствуют в `audit.json` или не меняются.

- Ozon: название, габариты/вес упаковки, бренд, модель, ТН ВЭД, хештеги,
  аннотация, материал, размер, количество в упаковке, единиц в одном товаре,
  цвет, название цвета, целевая аудитория, вид выпуска, страна, количество
  заводских упаковок;
- WB: наименование, категория, 18+, текущие vendorCode/nmID/barcode,
  целевой seller SKU отдельно как `vendorCode -> internal_sku` при будущем
  owner-approved apply, бренд, цвет, ТНВЭД, КИЗ, описание, габариты/вес, вид
  декора, состав, страна, количество предметов, комплектация, НДС как
  unchanged/review. `nmID` и barcode сохраняются, но `vendorCode` не считать
  вечным `do_not_touch`: по правилу владельца артикул продавца должен
  переходить на внутренний артикул в безопасном write-шаге.
- Для карточки на обеих площадках отдельно показывать переход Ozon seller SKU
  и WB vendorCode на `internal_sku`; native ID, `nmID` и barcode отделять и по
  умолчанию не менять.

Формат каждой строки: `поле -> сейчас -> рекомендую -> статус -> почему`.
Поля чужих предметов WB/Ozon не показывать.

Рекомендованное описание должно быть визуально разбито на 3 отдельных
абзаца/блока с заголовками `Описание товара`,
`Преимущества и характеристики товара`, `О производителе`. Размеры упаковки,
marketplace-specific размеры упаковки и вес не вставлять в продающее
описание; показывать их в параметрах карточки, паспорте товара и таблице
`сейчас -> рекомендую -> почему`.

Рекомендованное описание должно быть написано как текст производителя для
покупателя. Запрещено переносить в продающее описание язык аудита:
`на фото видно`, `по инфографике`, `карточка показывает`,
`подтверждено snapshot/API`, `по текущему snapshot`, `источник для
рекомендации`. Эти формулировки должны оставаться только в фото-аудите,
пояснениях рекомендаций, `not_confirmed` и технических блоках. В описании
указываются сами свойства изделия, материалы, крепление, комплектность,
назначение, релевантные SEO-формулировки и короткий блок о производителе.
Если смысл изображения не подтвержден, описывать только форму, надписи, цвета
и видимые элементы без догадок.

Перед итоговым описанием аудитор обязан сформировать machine-readable
`description_semantic_plan`, а после написания - `description_seo_coverage`.
В HTML показать кратко, какие точные и широкие релевантные фразы распределены
по трем блокам. Общий текст без точной тематики, типа/крепления и релевантных
SEO-фраз не считается готовым owner-review результатом.

Целевой набор Ozon-хештегов для обычной карточки - `20-30` уникальных
релевантных элементов, при наличии достаточной семантики цель - `30`.
В HTML нужно показывать полный целевой список и его количество. Список меньше
`20` допустим только с явным `hashtag_shortfall_reason`. Порядок: точная
тематика и вариант, тип/крепление, релевантное место ношения, затем широкие
релевантные варианты по убыванию частотности. `#патч` не может стоять раньше
точных шевронных запросов.

Для текущих изделий Vital Shevron код маркировки не требуется: в целевых
параметрах показывать Ozon `23536 / Нужен код маркировки = false` и WB
`КИЗ / маркировка = false`, если владелец отдельно не изменил правило.

Для тематических неформенных шевронов не использовать `на рукав` в title и не
делать рукавный акцент в Ozon hashtags/WB tags. Рукав может быть указан в
описании только как один из вариантов крепления. `На рукав` в title допустимо
для ведомственных и форменных шевронов. Для шевронов на кепку `на кепку` в
title обязательно. `Название цвета` должно различать конкретный товар внутри
группы, например `Смерть сходящая с небес, хаки`, а не повторять название
группы `БПЛА`.

Для тематических неформенных `nr`-шевронов внутренний код `nr` не равен
разрешению на SEO `на рукав`. В HTML/JSON запрещено показывать `на рукав` или
`нарукав` как recommended title, Ozon hashtag, WB tag или demand SEO term для
групп вроде `bpla`, `svo`, `prikol`, `chvk`, `fan`, `sht`, `raz`, `pz`.
Если такие строки пришли во входном `seo_query_pack`, они должны быть
помечены как исключенные/ошибочные, а не рекомендованные.
6. **Блок решения владельца**
   - `да`;
   - `нет`;
   - `поменять ...`;
   - `отложить`;
   - `дизайнеру`;
   - `ручная проверка`.

Если HTML готовится для позиции, которую после согласования нужно сразу
внедрять в Ozon/WB, он должен быть полноценным owner-review/apply пакетом:
показать все `before -> after`, seller SKU, создание новой карточки, фото,
атрибуты, хештеги/теги, группировку и marketplace-specific ограничения. Если
владелец после такого HTML пишет `применяй`, это считается полным approval для
всех явно показанных действий. Повторно спрашивать подтверждение по тем же
действиям нельзя; перед apply можно остановиться только из-за drift,
недостающего обязательного поля, нового риска или расхождения машинного
apply-пакета с HTML.

Для Ozon-only или WB-only позиции это обязательный, а не опциональный режим:
если владелец явно не запретил создание второй карточки, HTML/JSON должны сразу
включать создание на отсутствующей площадке и переход seller SKU обеих площадок
на `internal_sku`. Отсутствие выданных площадкой `nmID`, barcode или product ID
не является основанием исключать create из owner-review: такие идентификаторы
помечаются `assigned_by_marketplace`/`generate_at_apply` и проверяются после
создания. Неготовые защищенные WB-фото блокируют только media upload, а не
паспорт, seller SKU, создание карточки, текст и характеристики. В результате
владелец просматривает позицию один раз; технические стадии выполняются внутри
одного последующего end-to-end цикла.

## JSON

`audit.json` должен быть машинно читаемым и не должен содержать секреты.

Минимальная структура:

```json
{
  "identity": {},
  "source_refs": {},
  "agent_audit": {},
  "current_state": {},
  "recommendations": [],
  "proposed_final_card": {},
  "seo": {},
  "media": {},
  "grouping": {},
  "batch_groups": [],
  "risks": [],
  "not_confirmed": [],
  "designer_tasks": [],
  "validator_status": {},
  "owner_review": {}
}
```

Минимальная структура `seo`:

```json
{
  "description_semantic_plan": [
    {
      "phrase": "шеврон ФСБ",
      "role": "primary_target",
      "source": "seo_query_pack.confirmed_query_rows",
      "relevance": "confirmed"
    }
  ],
  "description_seo_coverage": [
    {
      "phrase": "шеврон ФСБ",
      "used_form": "шеврон ФСБ России",
      "description_block": "Описание товара",
      "covered": true
    }
  ],
  "hashtag_selection_evidence": {
    "target_count": 30,
    "actual_count": 30,
    "hashtag_shortfall_reason": null,
    "ordered_hashtags": [
      {
        "hashtag": "#ШевронФСБ",
        "source": "confirmed query + content",
        "frequency": 123,
        "frequency_type": "query_frequency_days_7",
        "relevance": "exact"
      },
      {
        "hashtag": "#шеврон",
        "source": "ozon_hashtag_frequency_table",
        "frequency": 219600,
        "frequency_type": "products_with_hashtag",
        "relevance": "broad"
      }
    ],
    "excluded_candidates": []
  },
  "source_tables": [
    {
      "name": "ozon_top_queries",
      "marketplace": "ozon",
      "source": "lk_export",
      "path": "data/...",
      "period": "last_7_days",
      "exported_at": "YYYY-MM-DDTHH:MM:SS",
      "top_n": 50
    }
  ],
  "target_query_clusters": [
    {
      "query": "шеврон ФСБ",
      "marketplace": "ozon",
      "role": "primary_target",
      "period": "last_7_days",
      "frequency": 123,
      "relevance": "confirmed",
      "reason": "точно соответствует тематике товара"
    },
    {
      "query": "шеврон на липучке",
      "marketplace": "wb",
      "role": "broad_identity",
      "period": "last_7_days",
      "frequency": 1234,
      "relevance": "confirmed",
      "reason": "широкий базовый запрос по типу и креплению"
    }
  ],
  "parser_positions_by_query": [
    {
      "query": "комплект шевронов",
      "marketplace": "ozon",
      "position": 77,
      "internal_sku": "example",
      "native_id": "offer_id/product_id/sku/nmID/vendorCode",
      "parser_run_id": "example",
      "collected_at": "YYYY-MM-DDTHH:MM:SS"
    }
  ],
  "marketplace_top_queries": [
    {
      "query": "шеврон",
      "marketplace": "wb",
      "period": "last_7_days",
      "frequency": 1234,
      "popularity": null,
      "source": "lk_export"
    }
  ],
  "confirmed_query_rows": [
    {
      "query": "шеврон ФСБ",
      "marketplace": "ozon",
      "role": "primary_target",
      "seed_query": "шеврон фсб",
      "rank": 1,
      "period": "days_7",
      "frequency": 123,
      "popularity": 123,
      "source": "ozon_lk_ui_response",
      "collected_at": "YYYY-MM-DDTHH:MM:SS"
    }
  ],
  "content_terms": [
    {
      "term": "комплект",
      "source": "current_title/photo/attributes",
      "reason": "слово описывает товар"
    }
  ],
  "seo_demand_terms": [
    {
      "query": "комплект шевронов",
      "marketplace": "ozon",
      "period": "last_7_days",
      "frequency": 1234,
      "source": "lk_export/parser",
      "relevance": "confirmed"
    }
  ],
  "seo_recommendation_status": "ready",
  "blocked_reason": ""
}
```

`ordered_hashtags` должен быть массивом объектов, а не строк. Итоговый HTML
обязан показывать отдельную построчную таблицу всех хештегов с полями
`source`, `frequency`, `frequency_type` и `relevance`. Поисковую частотность
нельзя смешивать с количеством товаров в выпадающей подсказке Ozon; при
отсутствии подтвержденной частотности сохранять `null` и явный тип
`content_confirmed_no_frequency`.

Допустимые `seo_recommendation_status`:

```text
ready
partial
blocked_no_query_list
blocked_no_frequency
blocked_not_relevant
```

Если есть только агрегат parser без конкретных запросов, например
`4 видимых запроса, лучшая позиция 77`, заполнять `content_terms` можно, но
`seo_demand_terms` должен быть пустым, а статус должен быть
`blocked_no_query_list`. Нельзя готовить новые WB tags, Ozon hashtags или
SEO-расширение названия/описания по спросу без списка запросов и источника
частотности.

Если во входном пакете есть `seo_query_pack.confirmed_query_rows`, эти строки
считать строковой таблицей спроса, подготовленной оркестратором. Тогда
`target_query_clusters`, `seo_demand_terms` и `marketplace_top_queries` можно
заполнять на основании этих строк, не помечая SEO как `partial` только из-за
того, что priority CSV содержит агрегированные суммы.

Для первичного заполнения карточки `target_query_clusters` важнее parser-
позиций: карточка заряжается под релевантный широкий базовый запрос и точный
тематический запрос. `parser_positions_by_query` используется как baseline и
будущий контроль движения после изменений, если эти данные доступны.

Fresh-аудитор не должен сам собирать запросы в ЛК. Если SEO-рекомендация
основана на спросе, в `source_tables`, `target_query_clusters`,
`marketplace_top_queries`, `confirmed_query_rows` или
`parser_positions_by_query` должен быть источник, подготовленный
оркестратором.

## Обязательные статусы

После аудитора:

```json
{
  "agent_audit": {
    "status": "agent_audited",
    "all_photos_inspected": true
  },
  "owner_review": {
    "status": "not_submitted"
  }
}
```

Если аудит заблокирован:

```json
{
  "agent_audit": {
    "status": "blocked"
  },
  "not_confirmed": [
    {
      "field": "example",
      "reason": "source missing"
    }
  ]
}
```

После проверяющего:

```json
{
  "validator_status": {
    "status": "validated",
    "checked_at": "YYYY-MM-DD",
    "critical_issues": [],
    "minor_issues": []
  }
}
```

Допустимые `validator_status.status`:

```text
validated
needs_rework
blocked
```

## Recommendation item

Каждая рекомендация:

```json
{
  "field": "ozon_title",
  "current": "текущее значение",
  "recommended": "рекомендуемое значение",
  "reason": "почему",
  "status": "recommended",
  "batch_group_key": "title_unification"
}
```

## Не подтверждено

Если факт не подтвержден источником:

```json
{
  "field": "product_size",
  "reason": "not visible in photos and absent in source package",
  "impact": "cannot calculate package confidently"
}
```

Нельзя заменять `not_confirmed` догадкой.

## Batch groups

Batch group нужен для будущих пакетных правок:

```text
title_unification
description_three_blocks
package_fix_sleeve
weight_fix_single_chevron
material_composition_standard
ozon_hashtags_missing
photo_designer_task
grouping_manual_review
```

Группы можно добавлять, если они понятны и повторяемы.
