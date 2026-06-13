# WB Price Quarantine Runbook

Дата создания: 2026-06-11.

Назначение: постоянная инструкция по диагностике и выводу товаров WB из Price
Quarantine после резкого изменения цены/скидки.

## Safety

Price Quarantine относится к опасному контуру, потому что связан с ценами и
скидками.

Обязательная цепочка:

```text
API docs check -> read-only quarantine snapshot -> dry-run/review -> explicit approve -> apply -> verify -> result
```

Без явного указания владельца не нажимать `Apply New Price` и не отправлять
write-запросы в карантин.

## API-first

Перед ЛК проверять официальный WB API:

```text
https://dev.wildberries.ru/en/docs/openapi/work-with-products
```

Проверенные методы:

```text
GET  https://discounts-prices-api.wildberries.ru/api/v2/quarantine/goods
POST https://discounts-prices-api.wildberries.ru/api/v2/upload/task
```

Вывод 2026-06-11:

- официальный API умеет получить список товаров в карантине;
- официальный API умеет менять цену/скидку через upload task;
- отдельный официальный метод release из карантина не найден;
- документация указывает: цену/скидку можно изменить через API, либо вывести
  товар из карантина в личном кабинете;
- попытка восстановить старую цену и скидку `0` через API была отклонена WB:
  `Specified prices and discounts are already set`.

Поэтому для этой внештатной ситуации переход в ЛК соответствует правилу
API-first.

## Пороги карантина по документации WB

Актуальная инструкция WB Partners:

```text
https://seller.wildberries.ru/instructions/ru/ru/material/price-quarantine
обновлено: 18.05.2026
```

Практические пороги из инструкции ЛК:

- по умолчанию товар попадает в карантин при резком снижении цены на `33,3%`,
  то есть в `1,5` раза;
- для категории можно выбрать порог от `33,3%` до `47,5%`;
- доступные пороги: `33,3%`, `37,5%`, `41,5%`, `43,5%`, `47,5%`;
- снижение на `50%` и больше WB не дает оформить как обычный карантинный порог:
  в окне управления ценой появляется ошибка, а цену нужно снижать поэтапно;
- если цена снизилась в диапазоне от `1,5` до `1,9` раза, изменение можно
  подтвердить на вкладке `Карантин`;
- если снижение больше, шаги снижения должны зависеть от порога категории.

Официальная API-документация `Prices and Discounts` дополнительно содержит
формулировку про попадание в карантин, если новая цена со скидкой минимум в 3
раза ниже предыдущей. Для рабочих сценариев ЛК и скидок TAKTERRA использовать
актуальную инструкцию WB Partners и фактическое поведение ЛК, потому что оно
совпало с нашей операцией `0 -> 50`: прямой upload `50%` был принят как task, но
строки остались в `Error`, скидка не применилась.

## ЛК WB

Стабильный раздел:

```text
https://seller.wildberries.ru/discount-and-prices/quarantine
```

В ЛК после выбора строк доступны две команды:

```text
Keep Current Price - оставить текущую цену/скидку и отменить quarantined-изменение
Apply New Price    - применить новую цену/скидку из карантина
```

Выбор действия зависит от цели:

- если нужно отменить ошибочное изменение и оставить старую текущую
  цену/скидку, использовать `Keep Current Price`;
- если нужно вывести товар из карантина с сохранением новой скидки/цены,
  использовать `Apply New Price`.

Для схемы WB `65-50-50`, когда владелец требует оставить скидку, использовать
`Apply New Price`, а не `Keep Current Price`.

## Внутренние LK endpoints

Фронт ЛК `discounts-prices-v2-front@v3.2.2` использует:

```text
GET  https://discounts-prices.wildberries.ru/ns/dp-api/discounts-prices/suppliers/api/v1/quarantine/goods?limit=1000&offset=0
POST https://discounts-prices.wildberries.ru/ns/dp-api/discounts-prices/suppliers/api/v1/quarantine/goods/delete
POST https://discounts-prices.wildberries.ru/ns/dp-api/discounts-prices/suppliers/api/v1/quarantine/goods
```

Интерпретация:

- `POST /quarantine/goods/delete` соответствует `Keep Current Price`;
- `POST /quarantine/goods` соответствует `Apply New Price`;
- payload для `delete`:

```json
{
  "data": [1274301831]
}
```

Где `data` - внутренние `id` строк карантина из LK GET, не `nmID`.

Секретные LK-токены брать только внутри браузерного контекста persistent
profile. В отчеты и логи токены, cookies и storage state не выводить.

## Операция 2026-06-11

Причина: после попытки staged recovery `0 -> 49` WB перевел 20 товаров в Price
Quarantine.

API-first попытка:

```text
run_id: wb_price_quarantine_recovery_20260611T130806
target rows: 20
found before: 20
API upload result: HTTP 400
error: Specified prices and discounts are already set
remaining after API: 20
```

ЛК release:

```text
run_id: wb_price_quarantine_lk_release_20260611T101601Z
operation: Keep Current Price / cancel quarantined change
before_count: 20
payload internal ids: 20
release_http_status: 200
release_error: false
after_count: 0
overall_status: ok
```

Визуальная проверка после reload ЛК:

```text
No items in Quarantine
```

Важно: эта операция была неверной для цели владельца "оставить скидку". Она
отменила quarantined-изменение и вернула скидку к `0%`. Для сохранения скидки
нужно было использовать `Apply New Price`.

Корректирующая операция:

```text
run_id: wb_discount_stage_49_apply_then_50_20260611T104150Z
initial discount: 0
upload 49: 138407420
wait49_state: quarantine
Apply New Price 49: HTTP 200
wait49_applied_state: applied
upload 50: 138407499
wait50_state: applied
final discount: 50
final quarantine rows: 0
overall_status: ok
```

Вывод: если прямой upload `50%` отклоняется WB без создания карантина, рабочая
последовательность для этих товаров:

```text
0 -> upload 49 -> quarantine -> Apply New Price -> current 49 -> upload 50 -> current 50
```

Правило для таких товаров: если текущая скидка `0%`, а целевая скидка по схеме
`50%`, не отправлять прямой `0 -> 50` как единственный шаг. Применять скидку в
два этапа:

```text
step 1: 0 -> 49, при карантине нажать/вызвать Apply New Price
step 2: 49 -> 50, проверить фактическую скидку и отсутствие карантина
```

Если для категории в ЛК установлен другой порог, первый шаг должен быть ниже
или равен безопасному staged-уровню для этой категории, а второй шаг доводит до
целевой скидки.

Артефакты:

```text
data/runs/2026-06-11/wb_price_quarantine_recovery_20260611T130806/
data/runs/2026-06-11/wb_price_quarantine_lk_inspect_20260611T101138Z/
data/runs/2026-06-11/wb_price_quarantine_lk_read_20260611T101450Z/
data/runs/2026-06-11/wb_price_quarantine_lk_release_20260611T101601Z/
data/runs/2026-06-11/wb_quarantine_apply_new_price_50_20260611T103532Z/
data/runs/2026-06-11/wb_discount_stage_49_apply_then_50_20260611T104150Z/
```

## Правило на будущее

Если WB отклоняет резкое снижение скидки/цены сообщением про поэтапное
снижение, не пробовать случайные шаги `49`, `48` и т.п. без отдельного
исследования порогов. Сначала проверить:

- официальный API карантина;
- фактический LK quarantine snapshot;
- пороги карантина в ЛК (`Set Threshold`);
- возможность staged-перехода через безопасный расчет порогов.

Для автоматизации нужен отдельный CLI-сценарий:

```text
wb-quarantine status
wb-quarantine release --mode keep-current --confirmed-by-user
```
