# Ozon Supply Order API

Дата проверки: 2026-06-29
Статус: `verified_official_openapi_and_live_api`
Официальный источник:

- `https://dev.ozon.ru/start/443-Sozdanie-postavki-FBO-cherez-API-ot-chernovika-do-otgruzki-na-sklad/`
- `https://dev.ozon.ru/news/643-Izmeneniia-v-metodakh-Seller-API-dlia-raboty-s-zaiavkami-na-postavku/`
- `https://docs.ozon.ru/api/seller/`
- локальная копия Swagger:
  `data/reference/api_docs/ozon/openapi/seller_swagger_20260629.json`

Ограничение проверки: прямой shell-доступ к `docs.ozon.ru` и `dev.ozon.ru` из
текущей среды возвращает redirect loop (`HTTP 307`), но через браузерную
CDP-сессию Vital Shevron документация открылась, а Swagger загружен браузером
с `HTTP 200`. Поэтому official-doc recheck выполнен через ЛК/браузерный
контур, а не через shell.

Проверенные URL через CDP Vital Shevron:

- `https://dev.ozon.ru/start/443-Sozdanie-postavki-FBO-cherez-API-ot-chernovika-do-otgruzki-na-sklad/`
- `https://dev.ozon.ru/news/643-Izmeneniia-v-metodakh-Seller-API-dlia-raboty-s-zaiavkami-na-postavku/`
- `https://docs.ozon.ru/api/seller/`

Используется в проекте: `supply_planning_runbook.md`, будущий
`plan-supply-workbooks`, будущий блок поставок ежедневного отчета.

## Назначение

Получить список заявок на поставку Ozon FBO, статусы заявок/поставок,
кластер/склад размещения, drop-off warehouse, timeslot и состав поставки.

## Официальные методы FBO-поставок

По Swagger Ozon Seller API `2.1` в разделе `Доставка FBO` подтверждены:

- `POST /v1/supply-order/status/counter` - количество заявок по статусам;
- `POST /v1/supply-order/bundle` - состав поставки или заявки на поставку;
- `POST /v3/supply-order/list` - список заявок на поставку на склад Ozon;
- `POST /v3/supply-order/get` - информация о заявке на поставку;
- `POST /v1/supply-order/timeslot/get` - интервалы поставки;
- `POST /v1/supply-order/timeslot/update` - обновить интервал поставки;
- `POST /v1/supply-order/timeslot/status` - статус интервала поставки;
- `POST /v1/supply-order/pass/create` - указать данные о водителе и автомобиле;
- `POST /v1/supply-order/pass/status` - статус ввода данных о водителе и автомобиле;
- `POST /v1/supply-order/details` - подробная информация о заявке;
- `GET /v1/supplier/available_warehouses` - загруженность складов Ozon.

Официальная статья Ozon `Создание поставки FBO через API` также описывает
write-цепочку создания поставки:

```text
/v1/draft/direct/create | /v1/draft/crossdock/create | /v1/draft/multi-cluster/create
  -> /v2/draft/create/info
  -> /v2/draft/timeslot/info
  -> /v2/draft/supply/create
  -> /v2/draft/supply/create/status
  -> /v3/supply-order/get
  -> /v1/cargoes/create
  -> /v2/cargoes/create/info
  -> /v1/cargoes-label/create
  -> /v1/cargoes-label/get
  -> /v1/supply-order/timeslot/update
  -> /v1/supply-order/pass/create
```

Создание/изменение поставок, cargoes, labels, pass и timeslot - опасные
write-операции. Для них действует полный safety-контур проекта.

## Подтвержденная цепочка методов

### Список заявок

```text
POST /v3/supply-order/list
```

Назначение: получить `order_ids` заявок по фильтру состояний.

Официальный request:

```json
{
  "filter": {
    "states": ["READY_TO_SUPPLY", "IN_TRANSIT", "ACCEPTANCE_AT_STORAGE_WAREHOUSE"]
  },
  "limit": 10,
  "last_id": "",
  "sort_by": "ORDER_CREATION",
  "sort_dir": "DESC"
}
```

Проверенный response:

```json
{
  "order_ids": [113854438, 112412082],
  "last_id": "..."
}
```

Официальные ограничения:

- `limit` обязателен, диапазон `1..100`;
- `filter.states` обязателен;
- `last_id` - пагинация, при первом запросе пустая строка;
- `sort_by` обязателен;
- `sort_dir` опционален.

Официальные `filter.states`:

- `DATA_FILLING` - заполнение данных;
- `READY_TO_SUPPLY` - готова к отгрузке;
- `ACCEPTED_AT_SUPPLY_WAREHOUSE` - принята на точке отгрузки;
- `IN_TRANSIT` - в пути;
- `ACCEPTANCE_AT_STORAGE_WAREHOUSE` - приемка на складе;
- `REPORTS_CONFIRMATION_AWAITING` - согласование актов;
- `REPORT_REJECTED` - спор;
- `COMPLETED` - завершена;
- `REJECTED_AT_SUPPLY_WAREHOUSE` - отказано в приемке;
- `CANCELLED` - отменена;
- `OVERDUE` - просрочена.

Официальные `sort_by`:

- `ORDER_CREATION` - по дате создания заявки;
- `ORDER_STATE_UPDATED_AT` - по обновлению статуса заявки;
- `TIMESLOT_FROM_UTC` - по таймслоту в UTC;
- `TIMESLOT_FROM_LOCAL` - по таймслоту в локальном времени.

Официальные `sort_dir`: `ASC`, `DESC`.

Примечание: ранний live smoke-test принимал числовые значения `states` и
`sort_by`, но постоянный код проекта должен использовать строковые enum из
официального OpenAPI.

### Детали заявок пачкой

```text
POST /v3/supply-order/get
```

Проверенный request:

```json
{
  "order_ids": [112412080, 112412081]
}
```

Официальное ограничение: `order_ids` - до `50` идентификаторов за запрос.

Ключевые поля:

- `orders[].order_id`;
- `orders[].order_number`;
- `orders[].created_date`;
- `orders[].state`;
- `orders[].state_updated_date`;
- `orders[].drop_off_warehouse`;
- `orders[].timeslot`;
- `orders[].supplies[]`;
- `orders[].supplies[].supply_id`;
- `orders[].supplies[].state`;
- `orders[].supplies[].storage_warehouse`;
- `orders[].supplies[].is_crossdock`;
- `orders[].supplies[].bundle_id`;
- `orders[].supplies[].macrolocal_cluster_id`.

Официальные поля заявки:

- `created_date`;
- `data_filling_deadline_utc`;
- `dropoff_warehouse.address/name/warehouse_id`;
- `order_id`;
- `order_number`;
- `order_tags`;
- `state`;
- `state_updated_date`;
- `supplies[]`;
- `timeslot`.

Официальные поля `supplies[]`:

- `supply_id`;
- `state`;
- `bundle_id`;
- `is_crossdock`;
- `macrolocal_cluster_id`;
- `storage_warehouse.address/name/warehouse_id/arrival_date`;
- `supply_tags`.

### Детали одной заявки

```text
POST /v1/supply-order/details
```

Проверенный request:

```json
{
  "order_id": 112412081
}
```

Ключевые поля:

- `order_id`;
- `order_number`;
- `state`;
- `dropoff_warehouse_id`;
- `order_tags`;
- `vehicle`;
- `timeslot`;
- `supplies[]`;
- `supplies[].supply_id`;
- `supplies[].supply_state`;
- `supplies[].storage_warehouse`;
- `supplies[].content.bundle_id`;
- `supplies[].macrolocal_cluster_id`;
- `supplies[].cancellation_allowability`.

Дополнительные официальные поля:

- `data_filling_deadline_utc`;
- `state_updated_date`;
- `supplies[].content.can_set`;
- `supplies[].content.can_not_set_reasons`;
- `supplies[].overdue_reason`;
- `supplies[].ettn_info`;
- `supplies[].supply_tags`;
- `timeslot.can_set`;
- `timeslot.can_not_set_reasons`;
- `vehicle.can_set`;
- `vehicle.can_not_set_reasons`.

### Состав bundle/поставки

```text
POST /v1/supply-order/bundle
```

Назначение: получить состав товаров по `bundle_id`.

Проверенный request:

```json
{
  "bundle_ids": ["<bundle_id>"],
  "limit": 100
}
```

Проверенный response top-level:

- `items[]`;
- `total_count`;
- `last_id`;
- `has_next`.

Ключевые поля строки `items[]`:

- `sku`;
- `quantity`;
- `offer_id`;
- `name`;
- `barcode`;
- `product_id`;
- `volume_in_litres`;
- `total_volume_in_litres`;
- `quant`;
- `shipment_type`;
- `placement_zone`.

Пагинация: если `has_next=true`, повторять запрос с теми же `bundle_ids`,
`limit` и полученным `last_id`.

Важные ограничения по live API:

- `limit` обязателен, диапазон `1..100`;
- `bundle_ids` должен содержать от `1` до `100` элементов.

Официальные optional request fields:

- `last_id` - пагинация;
- `query` - поиск по названию, артикулу или SKU;
- `is_asc` - сортировка по возрастанию;
- `sort_field` - `SKU`, `NAME`, `QUANTITY`, `TOTAL_VOLUME_IN_LITRES`;
- `item_tags_calculation.dropoff_warehouse_id`;
- `item_tags_calculation.storage_warehouse_ids` - до `25` складов.

Дополнительные официальные поля `items[]`:

- `icon_path`;
- `contractor_item_code`;
- `sfbo_attribute`: `ITEM_SFBO_ATTRIBUTE_NONE`,
  `ITEM_SFBO_ATTRIBUTE_SUPER_FBO`, `ITEM_SFBO_ATTRIBUTE_ANTI_FBO`;
- `is_quant_editable`;
- `tags`;
- `placement_zone`.

## Официальные статусы

Статусы заявки `orders[].state` и поставки `supplies[].state` по OpenAPI:

- `UNSPECIFIED` - не определен;
- `DATA_FILLING` - заполнение данных;
- `READY_TO_SUPPLY` - готова к отгрузке;
- `ACCEPTED_AT_SUPPLY_WAREHOUSE` - принята на точке отгрузки;
- `IN_TRANSIT` - в пути;
- `ACCEPTANCE_AT_STORAGE_WAREHOUSE` - приемка на складе;
- `REPORTS_CONFIRMATION_AWAITING` - согласование актов;
- `REPORT_REJECTED` - спор;
- `COMPLETED` - завершена;
- `REJECTED_AT_SUPPLY_WAREHOUSE` - отказано в приемке;
- `CANCELLED` - отменена;
- `OVERDUE` - просрочена.

В `details.supplies[].supply_state` есть нюанс: OpenAPI также содержит
`ACCEPTED_AT_STORAGE_WAREHOUSE` - принята на складе хранения. В расчетах
confirmed inbound этот статус считать уже принятым/размещенным, а не товаром
в пути.

## Устаревающие методы

`POST /v1/supply-order/timeslot/get` по официальной документации устаревает и
будет отключен `2026-08-19`; Ozon рекомендует перейти на
`/v2/supply-order/timeslot/list`.

Новость Ozon от `2025-11-20`: `/v2/supply-order/get` и
`/v2/supply-order/list` устаревают, вместо них использовать
`/v3/supply-order/get` и `/v3/supply-order/list`; старые методы должны быть
отключены `2025-12-11`.

## Проверка на Vital Shevron

Read-only smoke-test 2026-06-28:

- `/v3/supply-order/list` вернул активные и исторические `order_ids`.
- `/v3/supply-order/get` по нескольким `order_ids` вернул Ozon
  `order_number`, state, drop-off warehouse, timeslot, supplies, `supply_id`,
  `bundle_id`, `macrolocal_cluster_id`.
- `/v1/supply-order/details` вернул детали одной заявки и `content.bundle_id`.
- `/v1/supply-order/bundle` по `bundle_id` вернул `24` строки состава с
  `offer_id`, `sku`, `product_id`, `barcode`, `quantity`, `name`.

Official-doc check через CDP Vital Shevron 2026-06-29:

- CDP guard подтвердил контур:
  `127.0.0.1:9544` и профиль
  `.sessions/ozon/chrome-profile`.
- `dev.ozon.ru` статьи по FBO-поставкам открылись с `HTTP 200`.
- `docs.ozon.ru/api/seller/` открылся с `HTTP 200`.
- Swagger `docs.ozon.ru/api/seller/swagger.json?...` загружен браузером с
  `HTTP 200` и сохранен в
  `data/reference/api_docs/ozon/openapi/seller_swagger_20260629.json`.

## Вывод для проекта

Для Ozon поставок использовать API-цепочку:

```text
/v3/supply-order/list
  -> /v3/supply-order/get
  -> /v1/supply-order/details
  -> /v1/supply-order/bundle
```

При расчете confirmed inbound:

- статус брать из `orders[].state` и `supplies[].state`/`supply_state`;
- номер заявки в ЛК соответствует `order_number` или `supply_id`;
- `macrolocal_cluster_id` нужно замаппить на человекочитаемый кластер Ozon;
- состав товаров брать через bundle/details, а не из общей суммы.

## Открытые вопросы

- Найти или построить mapping `macrolocal_cluster_id -> название кластера`
  из официального источника или ЛК.
- Добавить в проектный adapter/CLI read-only сбор Ozon supply orders по
  официальным строковым enum и пагинации `last_id`.
- Отдельно изучить `/v2/supply-order/timeslot/list`, так как
  `/v1/supply-order/timeslot/get` официально устаревает.

## Что обновлять при изменении

- `data/planning/supply_planning_runbook.md`
- `.agents/skills/marketplace-supply-planning/SKILL.md`
- будущий adapter/task для `plan-supply-workbooks`
- ежедневный отчет: блок поставок Ozon
