# Search Queries Runbook

Дата: 2026-06-13

## Назначение

Read-only сбор топа поисковых запросов из ЛК Ozon и WB по заданному слову или
фразе.

## Safety

Операция только read-only. Нельзя сохранять или выводить cookies, storage state,
API tokens, внутренние auth headers ЛК и коды входа.

## SEO query pack для карточных аудиторов

Для массового аудита карточек поисковые запросы собирает оркестратор, а не
fresh-аудиторы. Fresh-аудитор не должен самостоятельно ходить в ЛК Ozon/WB,
искать запросы или проверять частотность.

Перед запуском карточных аудиторов оркестратор должен подготовить
`seo_query_pack`:

- top-50 запросов Ozon за выбранный период с частотностью/популярностью,
  источником и временем выгрузки;
- top-50 запросов WB за выбранный период с частотностью/популярностью,
  источником и временем выгрузки;
- целевая разметка запросов по ролям:
  `primary_target`, `secondary_target`, `broad_identity`, `placement`,
  `exclude`;
- при наличии - parser-позиции по конкретным запросам: `query`,
  `marketplace`, native ID товара, `internal_sku`, позиция, run id/время
  сбора;
- релевантность запроса конкретной карточке и причина включения/исключения.

Этот пакет используется аудиторами для `seo_demand_terms`, названия, описания,
Ozon-хештегов и WB тегов. Первый проход карточек строится от целевого
SEO-кластера товара, а parser-позиции используются как baseline и будущий
контроль результата после изменений. Если в пакете есть только агрегаты вида
`видимых запросов: N`, но нет конкретных запросов или top-query таблиц,
спросовой SEO-блок карточки должен получить статус `blocked_no_query_list`.

### Сбор и сборка пакета

Свежие top-query источники Ozon/WB можно собрать read-only helper-ом:

```bash
NODE_PATH=/home/Codex/agent-tools/node/node_modules \
node scripts/search_queries/collect_seo_query_pack_sources.js \
  --output-dir data/runs/YYYY-MM-DD/<run-id> \
  --limit 50
```

Ozon collector обязан использовать штатный CDP-контур Vital Shevron:
порт `9544`, профиль `.sessions/ozon/chrome-profile` и guard
`scripts/lib/ozon_cdp_guard.js`. WB collector использует отдельный
persistent profile `.sessions/wb/browser-profile`. В артефакты нельзя
сохранять cookies, tokens, storage state или auth headers.

Из собранных источников оркестратор строит итоговый `seo_query_pack`:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli seo-query-pack \
  --query-source data/runs/YYYY-MM-DD/<source-run>/ozon_top_queries.json \
  --query-source data/runs/YYYY-MM-DD/<source-run>/wb_top_queries.json
```

Основные выходы:

```text
data/catalog/content/seo_query_pack/source_queries.json
data/catalog/content/seo_query_pack/card_seo_targets.csv
data/catalog/content/seo_query_pack/card_seo_targets.json
data/catalog/content/seo_query_pack/seo_query_pack.json
```

`data/catalog/content/seo_query_pack/` - generated слой. Источником истины
остаются run artifacts и исходные данные `content_master`.

С 2026-06-28 `card_seo_targets.json` и `seo_query_pack.json` обязаны
передавать fresh-аудиторам не только агрегаты `ozon_frequency_sum` и
`wb_frequency_sum`, но и строковую таблицу `confirmed_query_rows`:

- `query`;
- `marketplace`;
- `matched_term`;
- `role` (`primary_target`, `secondary_targets`, `broad_identity_terms`,
  `placement_terms`);
- `seed_query`;
- `rank`;
- `frequency`/`popularity`;
- `period`;
- `source`;
- `collected_at`.

Агрегированные суммы без `confirmed_query_rows` не являются достаточным
источником для спросового SEO-блока карточного аудита. Fresh-аудитор не должен
ставить `partial` или `blocked_no_query_list`, если в переданном
`seo_query_pack.confirmed_query_rows` есть релевантные строки с источником,
периодом и частотностью.

### Статусы `card_seo_targets`

- `ready` - есть целевой кластер и подтверждение точного/релевантного запроса
  из Ozon/WB top-query таблиц.
- `ready_broad_only` - карточка распознана, но подтверждение есть только по
  широким запросам. Аудитор может использовать broad terms осторожно, не
  выдумывая точную частотность тематического запроса.
- `needs_manual_review` - не распознана тема, место ношения или отсутствует
  внутренний артикул. Такие строки нельзя отдавать в автоматический SEO-аудит
  без ручного уточнения.
- `excluded_non_patch_assortment` - товар не относится к текущему рабочему
  ассортименту шевронов/нашивок/петлиц или не классифицирован как такой
  товар. Не передавать карточным аудиторам шевронного контура.
- `target_cluster_without_frequency` - целевой кластер сформирован, но в
  переданных top-query таблицах не найдено подтвержденной частотности.
- `blocked_no_query_tables` - источники запросов не загружены.

При генерации `card-content-audit-packages` статусы
`needs_manual_review` и `excluded_non_patch_assortment` должны исключаться из
массового audit flow. Они остаются в `seo_query_pack` и отдельном
`excluded_package_index.*` как контрольный список, но fresh-аудиторам не
выдаются.

## WB

ЛК:

```text
https://seller.wildberries.ru/search-analytics/popular-search-queries
```

Раздел: `Аналитика поиска -> Поисковые запросы на WB`.

Проверено 2026-06-13 через WB ЛК:

- первая вкладка: `Поисковые запросы на WB`;
- вторая вкладка: `Поисковые запросы: ваши товары`;
- строка поиска: `Поиск по поисковым запросам`;
- быстрые периоды: `Вчера`, `Неделя`, `Месяц`, `Квартал`;
- кнопка `Фильтры`;
- таблица имеет собственный внутренний scroll, это не scroll всей страницы;
- внизу таблицы появляется строка-кнопка `Показать ещё запросы`;
- справа над таблицей есть иконка `Создать Excel` и иконка `Загрузки`.

Основной endpoint, который использует страница:

```text
seller-content.wildberries.ru/ns/analytics-api/content-analytics/api/v2/search-analysis/search-texts
```

Проверенный payload страницы:

```json
{
  "limit": 50,
  "offset": 0,
  "subjectIDs": [],
  "itemIDs": [],
  "searchText": "шеврон",
  "interval": "week",
  "orderBy": {
    "field": "frequency",
    "mode": "desc"
  }
}
```

Значения `interval`:

| UI | interval |
| --- | --- |
| `Вчера` | `yesterday` |
| `Неделя` | `week` |
| `Месяц` | `month` |
| `Квартал` | `quarter` |

Пагинация:

- первая загрузка таблицы идет с `limit=50`, `offset=0`;
- чтобы увидеть `Показать ещё запросы`, нужно прокрутить внутренний контейнер
  таблицы до низа, иногда дважды, потому что высота таблицы пересчитывается
  после виртуализации строк;
- клик по `Показать ещё запросы` отправляет следующую пачку:

```json
{
  "limit": 50,
  "offset": 50,
  "subjectIDs": [],
  "itemIDs": [],
  "searchText": "",
  "interval": "yesterday",
  "orderBy": {
    "field": "frequency",
    "mode": "desc"
  }
}
```

Для сбора по слову `шеврон` использовать UI-поиск или штатный запрос страницы с
`searchText=шеврон`. Не делать вывод "запросов всего 21" без указания периода:
на 2026-06-13 ЛК показал `21` строку только для периода `Вчера`, а для
`Неделя`, `Месяц`, `Квартал` вернул по `50` строк на первой странице.

Колонки таблицы:

- `Поисковый запрос`;
- `Количество запросов`;
- `Динамика запросов`;
- `Запросов в среднем за день`;
- `Больше всего заказов в предмете`;
- `Перешли в карточку товара`;
- `Добавили в корзину`;
- `Конверсия в корзину`;
- `Заказали товаров`;
- `Конверсия в заказ`;
- `Предметов с заказами по запросу`.

### WB Excel

Иконка `Создать Excel` открывает модальное окно:

- выбор размера файла: `100 тыс.` или `300 тыс.`;
- поле `Название файла`;
- кнопка `Сформировать`.

Проверенный read-only workflow 2026-06-13:

1. Открыть `Создать Excel`.
2. Выбрать минимальный размер `100 тыс.` для тестовой выгрузки.
3. Указать имя файла.
4. Нажать `Сформировать`.
5. Открыть иконку `Загрузки`.
6. Проверить статус файла в менеджере загрузок.

Страница отправляет создание файла в:

```text
seller-content.wildberries.ru/ns/analytics-api/content-analytics/api/v1/file-manager/download
```

Проверенный payload без auth headers:

```json
{
  "userReportName": "vital_search_queries_probe_20260613",
  "reportType": "SEARCH_ANALYSIS_REPORT",
  "params": {
    "items": [],
    "subjectIDs": [],
    "searchText": "",
    "interval": "yesterday",
    "orderBy": {
      "field": "frequency",
      "mode": "desc"
    },
    "limit": 100000
  }
}
```

Менеджер загрузок читает список через:

```text
seller-content.wildberries.ru/ns/analytics-api/content-analytics/api/v1/file-manager/downloads?report_types=SEARCH_ANALYSIS_REPORT
```

В отчеты и инструкции не сохранять `downloadUrl`, auth headers, cookies,
storage state и токены. Допустимо сохранять только имя файла, статус, размер,
дату периода и факт успешного формирования.

## Ozon

ЛК:

```text
https://seller.ozon.ru/app/analytics/what-to-sell/all-queries
```

Раздел: `Аналитика -> Что продавать -> Поисковые запросы`.

Проверено 2026-06-13 через восстановленную Ozon CDP-сессию:

- отдельный headless-контекст только со storage state может попасть на Ozon
  antibot/challenge; для исследования ЛК использовать keeper/CDP `127.0.0.1:9544`;
- страница доступна для магазина `Vital Shevron`;
- по умолчанию открыт период `Период: 7 дней`;
- клик по `Период: 7 дней` на текущем доступе открывает Premium/Premium Plus
  paywall, а не список периодов;
- кнопка `Скачать` есть, но на текущем доступе также открывает Premium/Premium
  Plus paywall; Excel-файл не скачивается;
- фильтр `Группа запросов` раскрывается и подгружает список групп;
- таблица имеет обычную постраничную пагинацию: `1-50 из 10000`, страницы
  `1 2 3 4 5 ... 200`;
- клик по странице `2` отправляет следующий запрос с `offset=50`;
- внизу есть выбор `Строк на странице: 50`.

Основной endpoint ЛК:

```text
seller.ozon.ru/api/site/searchteam/Stats/queries/search/v2
```

Проверенный payload страницы для `шеврон`, период `7 дней`:

```json
{
  "text": "шеврон",
  "limit": "50",
  "offset": "0",
  "sort_by": "count",
  "sort_dir": "desc",
  "period": "days_7"
}
```

Проверенная пагинация:

```json
{
  "text": "шеврон",
  "limit": "50",
  "offset": "50",
  "sort_by": "count",
  "sort_dir": "desc",
  "period": "days_7"
}
```

Группы запросов загружаются через:

```text
seller.ozon.ru/api/site/searchstat/Stats/queries/groups
```

Payload:

```json
{}
```

Примеры групп, подтвержденные 2026-06-13:

- `Без предсказания`;
- `Ozon Premium`;
- `Ozon fresh`;
- `Ozon Объявления`;
- `Автомобили`;
- `Автотовары`;
- `Одежда`;
- `Спорт и отдых`;
- `Туризм рыбалка охота`;
- `Хобби и творчество`;
- `Электроника`.

Вкладки/режимы таблицы:

- `Сезонность`;
- `Конкуренция`;
- `Конверсия`;
- `Все показатели`.

В режиме `Все показатели` подтверждены колонки:

- `Поисковый запрос`;
- `Популярность запроса`;
- `Динамика за 28 дней`;
- `Динамика за 7 дней`;
- `Добавления в корзину`;
- `Конверсия в корзину`;
- `Заказано товаров`;
- `Конверсия в заказ`;
- `Заказано на сумму`;
- `Средняя цена для покупателя`;
- `Показано товаров`;
- `Конкуренты`;
- `Запросы без действий`;
- `Доля запросов без действий`;
- `Запросы с похожими результатами`;
- `Доля запросов с похожими результатами`;
- `Запросы без результата`;
- `Доля запросов без результата`.

Поля ответа endpoint:

| Поле | Смысл по UI |
| --- | --- |
| `query` | поисковый запрос |
| `count` | популярность запроса |
| `uniqQueriesWCa` | добавления в корзину |
| `ca` | конверсия в корзину, % |
| `ord` | заказано товаров |
| `searchUsersToOrdUsers` | конверсия в заказ, % |
| `gmv` | заказано на сумму |
| `avgCaRub` | средняя цена для покупателя |
| `itemsViews` | показано товаров |
| `uniqSellers` | конкуренты |
| `usersWithoutInterectionCount` | запросы без действий |
| `usersWithoutInterectionShare` | доля запросов без действий, % |
| `softQueryCount` | запросы с похожими результатами |
| `softQueryShare` | доля запросов с похожими результатами, % |
| `zrCount` | запросы без результата |
| `zrShare` | доля запросов без результата, % |

Если ЛК Ozon разлогинен, данные из ЛК не подтверждать. Официальные Seller API
методы `/v1/search-queries/text` и `/v1/search-queries/top` могут вернуть
`Method available with Premium Pro subscription`; в этом случае нужно прямо
писать, что Ozon-часть не подтверждается без Premium Pro или повторного входа в
ЛК.

## Артефакты

Финальные производные отчеты сохранять в:

```text
data/runs/<date>/search_queries_shevron_<timestamp>/
```

Минимальный состав:

- `summary.json`;
- `search_queries_shevron_report.md`;
- `wb_top_queries_shevron.csv`;
- `wb_top_queries_shevron.json`;
- `wb_pagination_checked.json`;
- `ozon_top_queries_shevron.csv`, если Ozon доступен.

Временные рабочие файлы хранить в `tmp/` и очищать после завершения.
