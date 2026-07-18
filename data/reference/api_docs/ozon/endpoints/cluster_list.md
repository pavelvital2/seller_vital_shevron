# Ozon `/v2/cluster/list`

Дата live-проверки: 2026-07-18.

## Назначение

Read-only список макролокальных кластеров Ozon и складов размещения внутри
каждого кластера. Используется для кластерного расчета поставок.

```http
POST https://api-seller.ozon.ru/v2/cluster/list
Content-Type: application/json

{}
```

Ответ содержит `result[]`:

- `macrolocal_cluster_id` - идентификатор кластера назначения;
- `data.macrolocal_cluster.name` - название кластера;
- `data.fulfillments[].warehouse_id/name` - склады Ozon в кластере.

## Проверенный контракт

Live API Vital Shevron вернул `27` кластеров. Например,
`4002 -> Дальний Восток -> ХАБАРОВСК_2_РФЦ`,
`4071 -> Ростов -> РОСТОВ-НА-ДОНУ_РФЦ / РОСТОВ_НА_ДОНУ_2_РФЦ`.

Метод связывает три независимых источника:

1. `financial_data.cluster_to` FBO posting-а - спрос покупателя.
2. `warehouse_name` складского остатка - остаток внутри кластера.
3. `macrolocal_cluster_id` supply-order - confirmed inbound в кластер.

Нельзя заменять этот mapping складом фактической отгрузки posting-а или общим
FBO-остатком. Метод ничего в Ozon не изменяет.

Источник: официальная Swagger-схема
`data/reference/api_docs/ozon/openapi/seller_swagger_20260629.json`, operation
`DraftClusterList`, и live read-only smoke 2026-07-18.
