# Wildberries FBW Supplies API

Дата проверки: 2026-06-28
Статус: `verified_live_api`
Официальный источник: `https://dev.wildberries.ru/docs/openapi/orders-fbw`
Используется в проекте: `supply_planning_runbook.md`, будущий
`plan-supply-workbooks`, будущий блок поставок ежедневного отчета.

## Назначение

Получить список складских поставок WB/FBW, статус поставки, склад приемки,
состав товаров в поставке и раскладку по коробам/упаковкам.

Важно: это не FBS endpoint `https://marketplace-api.wildberries.ru/api/v3/supplies`.
Для поставок на склады WB нужно использовать FBW Supplies API:

```text
https://supplies-api.wildberries.ru
```

## Методы

### Доступные склады для формирования поставки

```text
POST /api/v1/acceptance/options
```

Назначение: получить склады и типы упаковки, доступные для поставки по
баркоду и количеству товара.

Официальный request body: массив до `5000` строк:

- `barcode` - баркод товара;
- `quantity` - планируемое количество `1..999999`.

Ключевые поля response:

- `result[].barcode`;
- `result[].warehouses[]`;
- `result[].warehouses[].warehouseID`;
- `result[].warehouses[].canBox`;
- `result[].warehouses[].canMonopallet`;
- `result[].warehouses[].canSupersafe`;
- `result[].warehouses[].isBoxOnPallet`;
- `result[].error`;
- `result[].isError`.

Использовать перед созданием/планированием поставки, чтобы не предлагать
недоступный склад или тип упаковки.

### Список складов WB

```text
GET /api/v1/warehouses
```

Назначение: получить список складов WB.

Ключевые поля:

- `ID`;
- `name`;
- `address`;
- `workTime`;
- `isActive`;
- `isTransitActive`.

### Список поставок

```text
POST /api/v1/supplies
```

Проверенный минимальный request:

```json
{}
```

Официальные query parameters:

- `limit` - `1..1000`, по умолчанию `1000`;
- `offset` - с какого элемента начинать выдачу, по умолчанию `0`.

Официальные request filters:

- `dates[]` - фильтр по датам;
- `statusIDs[]` - фильтр по статусам поставок.

Проверенный response: массив поставок.

Ключевые поля:

- `supplyID` - ID поставки WB;
- `preorderID` - ID предзаказа;
- `createDate` - дата создания;
- `supplyDate` - плановая дата поставки;
- `factDate` - фактическая дата, если уже была отгрузка/приемка;
- `updatedDate` - дата обновления;
- `statusID` - статус поставки;
- `boxTypeID` - тип упаковки;
- `isBoxOnPallet` - короба на паллете.

### Детали поставки

```text
GET /api/v1/supplies/{supplyID}
```

Официальный query parameter:

- `isPreorderID` - `true`, если в path передан ID предзаказа; `false`, если
  передан `supplyID`.

Ключевые поля:

- `statusID`;
- `warehouseID`;
- `warehouseName`;
- `actualWarehouseID`;
- `actualWarehouseName`;
- `transitWarehouseID`;
- `transitWarehouseName`;
- `quantity`;
- `readyForSaleQuantity`;
- `acceptedQuantity`;
- `unloadingQuantity`;
- `depersonalizedQuantity`;
- `acceptanceCost`;
- `paidAcceptanceCoefficient`;
- `rejectReason`.

### Товары в поставке

```text
GET /api/v1/supplies/{supplyID}/goods
```

Официальные query parameters:

- `limit` - `1..1000`, по умолчанию `100`;
- `offset` - с какого элемента начинать выдачу, по умолчанию `0`;
- `isPreorderID` - `true`, если в path передан ID предзаказа; `false`, если
  передан `supplyID`.

Ключевые поля строки товара:

- `barcode`;
- `vendorCode`;
- `nmID`;
- `tnved`;
- `techSize`;
- `color`;
- `supplierBoxAmount`;
- `quantity`;
- `readyForSaleQuantity`;
- `unloadingQuantity`;
- `acceptedQuantity`.

Это основной endpoint для вычитания confirmed inbound из расчета новой
поставки по артикулам.

### Короба/упаковки поставки

```text
GET /api/v1/supplies/{supplyID}/package
```

Ключевые поля:

- `packageCode`;
- `quantity`;
- `barcodes[]`;
- `barcodes[].barcode`;
- `barcodes[].quantity`.

Использовать, если нужно сверить короб/грузоместо или подготовить/проверить
файл отгрузки.

## Статусы

Официальные значения `statusID`:

- `1` - не запланировано;
- `2` - запланировано;
- `3` - отгрузка разрешена;
- `4` - приемка;
- `5` - принято;
- `6` - выгружено на воротах.

На проверке Vital Shevron 2026-06-28 подтверждены в ЛК:

- `statusID=3` - в ЛК отображается как `Отгрузка разрешена`;
- `statusID=4` - в ЛК отображается как `Идет приемка`;
- `statusID=5` - в ЛК отображается как `Принято`.

## Проверка на Vital Shevron

Read-only smoke-test 2026-06-28:

- `POST /api/v1/supplies` вернул поставки WB, включая `40269110` и `40269284`.
- `GET /api/v1/supplies/40269110` вернул `statusID=3`,
  `warehouseName=Новосемейкино`, `quantity=62`.
- `GET /api/v1/supplies/40269110/goods` вернул товары поставки с
  `vendorCode`, `barcode`, `quantity`.
- `GET /api/v1/supplies/40269284` вернул `statusID=4`,
  `warehouseName=Краснодар (Тихорецкая)`, `quantity=68`,
  `acceptedQuantity=67`.
- `GET /api/v1/supplies/40269284/goods` вернул товары поставки с
  `acceptedQuantity` и `readyForSaleQuantity`.

## Вывод для проекта

Для WB поставок больше не использовать FBS `/api/v3/supplies` как источник
складских поставок. Он может возвращать `0` active, хотя в ЛК есть активные
FBW-поставки.

Правильный источник для текущих поставок WB:

```text
POST https://supplies-api.wildberries.ru/api/v1/supplies
GET  https://supplies-api.wildberries.ru/api/v1/supplies/{supplyID}
GET  https://supplies-api.wildberries.ru/api/v1/supplies/{supplyID}/goods
GET  https://supplies-api.wildberries.ru/api/v1/supplies/{supplyID}/package
```

## Что обновлять при изменении

- `data/planning/supply_planning_runbook.md`
- `.agents/skills/marketplace-supply-planning/SKILL.md`
- будущий adapter/task для `plan-supply-workbooks`
- ежедневный отчет: блок поставок WB
