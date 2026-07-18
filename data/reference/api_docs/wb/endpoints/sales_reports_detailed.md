# WB Finance: детализация отчётов реализации за период

Дата проверки: 2026-07-17.

Официальная документация:
`https://dev.wildberries.ru/docs/openapi/financial-reports-and-accounting`

## Endpoint

```text
POST https://finance-api.wildberries.ru/api/finance/v1/sales-reports/detailed
```

Используется токен категории `Finance`. Метод read-only. Данные доступны с
29.01.2024.

## Request

```json
{
  "dateFrom": "2026-07-01",
  "dateTo": "2026-07-16",
  "limit": 100000,
  "rrdId": 0,
  "period": "daily"
}
```

- `dateFrom`, `dateTo` - RFC3339, московское время;
- `limit` - не более 100000;
- первая страница использует `rrdId=0`;
- следующая страница использует `rrdId` последней строки;
- повторять до пустого/204 ответа;
- `fields` можно передать для ограничения состава ответа.

Официальный лимит: один запрос в минуту на кабинет. Поэтому адаптер запрашивает
максимальную страницу и ждёт не менее 61 секунды только если она заполнена и
нужна следующая страница.

Для расчёта изделий используются `nmId`, `vendorCode`, `docTypeName`,
`sellerOperName`, `quantity`, `retailAmount`, `forPay`, `saleDt` и `rrDate`.
Физическое количество равно `quantity * pack_qty`; неразрезанная пара петлиц
имеет `pack_qty=1`.
