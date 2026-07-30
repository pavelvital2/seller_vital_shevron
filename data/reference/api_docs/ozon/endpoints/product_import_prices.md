# Ozon Seller API: обновить цену

Дата проверки: 2026-07-30
Статус: verified
Официальный источник: https://docs.ozon.ru/api/seller/#operation/ProductAPI_ImportProductsPrices

## Назначение

Обновление `price`, `old_price`, `min_price` и связанных настроек цены товара.

## Метод

- method: `POST`
- path: `/v1/product/import/prices`
- auth: Ozon Seller API

## Ограничения и нюансы

- `min_price` является минимальной ценой товара после применения акций, а не
  целевой ценой конкретной акции.
- Ошибка `min_auto_price_too_small` означает, что `min_price` должна быть не
  меньше `50%` от текущей цены продавца.
- `price` должна быть выше `min_price`.
- Состояние учёта минимальной цены в автоакциях передаётся отдельно через
  `min_price_for_auto_actions_enabled`.
- Таймер актуальности минимальной цены действует 30 дней и продлевается через
  `POST /v1/product/action/timer/update`.

## Проверка 2026-07-30

Контракт подтверждён по актуальной Swagger-схеме Seller API `2.1`, загруженной
по прямой ссылке из документации Ozon через CDP Vital Shevron. Live API
дополнительно вернул `min_auto_price_too_small` для значений `312` при
`price=650` и `537` при `price=1100`; допустимые нижние границы составляют
`325` и `550`.
