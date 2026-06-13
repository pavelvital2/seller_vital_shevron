# Ozon Elastic Boosting runbook

Дата создания: 2026-06-10.

Назначение: постоянный порядок подготовки dry-run по Ozon акции
`Эластичный бустинг` без загрузки изменений в Ozon.

## Safety

Эластичный бустинг влияет на цену участия товара в акции, поэтому относится к
опасным операциям.

Обязательная цепочка:

```text
status-preflight -> dry-run -> review -> explicit approve -> apply -> verify -> result
```

Текущий сценарий реализован только как read-only/dry-run. Apply запрещен без
явного подтверждения владельца и отдельной проверки drift перед применением.

## Подготовка

Перед расчетом выполнить общий preflight:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli status-preflight
```

Продолжать можно только если:

- Ozon Seller API имеет статус `ok`;
- Ozon Performance API имеет статус `ok`;
- master catalog имеет статус `ok`;
- Ozon ЛК доступен, если дальнейший сценарий потребует браузерную проверку.

## Dry-run

Команда:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli plan-ozon-elastic
```

Что делает задача:

- получает список акций Ozon Seller API;
- выбирает акцию с типом `MARKETPLACE_MULTI_LEVEL_DISCOUNT_ON_AMOUNT` и названием
  с маркером `Эластичный бустинг`;
- скачивает активные товары акции и кандидатов;
- получает остатки товаров через Ozon Seller API;
- считает локальное решение по каждой строке;
- сохраняет отчет, CSV/XLSX и preview будущего upload payload;
- не отправляет upload в Ozon.

## Правила расчета

Текущие reason codes:

- `use_max_boost_price` - использовать максимальную цену бустинга как финальную
  цену участия;
- `use_min_price` - использовать минимально допустимую цену;
- `missing_min_price` - нет минимально допустимой цены;
- `no_stock` - нет положительного остатка;
- `no_boost_prices` - нет цен бустинга;
- `below_min_price_threshold` - цена бустинга ниже минимально допустимой;
- `insufficient_ozon_input_data` - данных Ozon недостаточно для решения.

Если активный товар получает причину из группы деактивации, dry-run помечает его
как `deactivate_from_action`, но не деактивирует без approval.

## Артефакты

Каждый запуск сохраняется в:

```text
data/runs/YYYY-MM-DD/ozon_elastic_plan_YYYYMMDDTHHMMSS/
```

Основные файлы:

```text
ozon_elastic_dry_run.md
ozon_elastic_dry_run.csv
ozon_elastic_dry_run.xlsx
ozon_elastic_upload_payload_preview.json
summary.json
raw/
processed/
```

## Последний dry-run

Актуальный запуск:

```text
run_id: ozon_elastic_plan_20260610T230610
action_id: 1977747
action_name: Эластичный бустинг. Без ограничения срока действия
active_rows: 203
candidate_rows: 0
merged_unique_products: 203
update_action_price: 198
update_action_price_with_changed_price: 12
deactivate_from_action: 5
blocked: 0
apply_performed: false
```

Артефакты:

```text
data/runs/2026-06-10/ozon_elastic_plan_20260610T230610/ozon_elastic_dry_run.md
data/runs/2026-06-10/ozon_elastic_plan_20260610T230610/ozon_elastic_dry_run.xlsx
data/runs/2026-06-10/ozon_elastic_plan_20260610T230610/ozon_elastic_upload_payload_preview.json
```

## Перед apply

Перед будущей загрузкой обязательно:

- повторить `status-preflight`;
- повторить dry-run свежими данными;
- сравнить текущий payload с reviewed payload;
- проверить товары `deactivate_from_action`;
- получить явное подтверждение владельца;
- после apply выполнить verify и сохранить result.

## Первый apply

2026-06-10 выполнен первый apply после явного подтверждения владельца.

Общий runbook:

```text
data/planning/actions_apply_runbook.md
```

Результат Ozon:

```text
run_id: actions_apply_20260610T234323
fresh dry-run: ozon_elastic_plan_20260610T234407
activate/update rows applied: 12
deactivate rows applied: 5
verify status: ok
rejected: none
```

Артефакты:

```text
data/runs/2026-06-10/actions_apply_20260610T234323/actions_apply_result.md
data/runs/2026-06-10/actions_apply_20260610T234323/raw/ozon_activate_response.json
data/runs/2026-06-10/actions_apply_20260610T234323/raw/ozon_deactivate_response.json
data/runs/2026-06-10/actions_apply_20260610T234323/raw/ozon_verify/
```
