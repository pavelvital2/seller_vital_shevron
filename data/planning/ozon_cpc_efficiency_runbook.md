# Ozon CPC Efficiency Runbook

## Итог

Отчет по продвижению Ozon `Оплата за клик` выполняется только read-only через
Ozon Performance API.

Используемые источники:

- `GET /api/client/campaign` - список рекламных кампаний;
- `POST /api/client/statistics/json` - генерация статистического отчета;
- `GET /api/client/statistics/report?UUID=<uuid>` - скачивание готового отчета.

Для Vital Shevron в отчет включать только кампании с `PaymentType = CPC`.

## Метрики

Минимальный набор:

```text
period
campaign_id
campaign title
state
spend
views
clicks
to_cart
orders
orders_money
avg_cpc
ctr_percent
cart_rate_percent
order_cr_percent
drr_percent
roas
```

## Optimization dry-run

После read-only отчета можно подготовить план рекомендаций без изменений в
Ozon:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m takterra_agent.cli plan-ozon-cpc-optimization \
  --source-run-id <ozon_cpc_efficiency_run_id>
```

Команда агрегирует статистику по `campaign_id + sku` и выпускает только
review-артефакты:

- `scale_candidate` - кандидаты на аккуратное усиление;
- `keep_monitor` - оставить и наблюдать;
- `reduce_bid_or_review` - высокая ДРР, нужна проверка/снижение;
- `review_card_then_reduce_or_pause` - расход без заказов, но есть корзины;
- `pause_or_exclude` - расход без заказов и без корзин.

Пороговые значения по умолчанию:

```text
zero_orders_spend: 50
high_drr_percent: 12
high_drr_min_spend: 100
scale_min_orders: 8
scale_max_drr_percent: 5
card_review_reduce_percent: 30
max_reduce_percent: 50
scale_low_drr_percent: 20
scale_mid_drr_percent: 15
scale_high_drr_percent: 10
```

План не является разрешением на apply. Он только готовит review-таблицу для
владельца.

В плане обязательно указывать размер изменения ставок:

- `bid_reference_type` - источник базовой ставки;
- `bid_reference` - фактический средний CPC из отчета за период;
- `proposed_bid_change_percent` - предлагаемый процент изменения;
- `target_bid` - расчетный ориентир после изменения.

Важно: `bid_reference = avg_cpc_from_report` не является подтвержденной текущей
ставкой в кабинете. Перед apply нужно отдельно получить текущие ставки через
API/ЛК, сделать fresh dry-run и drift-check.

## Current bids

Актуальные ставки товаров в CPC-кампании получать через Ozon Performance API:

```text
GET /api/client/campaign/<campaign_id>/v2/products?page=<page>&pageSize=100
```

Поле `bid` приходит как целое значение в микро-рублях; для отчета ставка
делится на `1000000`.

Для плана с текущими ставками запускать:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m takterra_agent.cli plan-ozon-cpc-optimization \
  --source-run-id <ozon_cpc_efficiency_run_id> \
  --current-bids-json <current_bids.json>
```

Полный список строк, где предлагается изменение ставки, сохраняется в:

```text
ozon_cpc_bid_changes.csv
```

## Apply bids

Применение ставок выполняется отдельной командой только после явного
согласования владельца:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m takterra_agent.cli apply-ozon-cpc-bids \
  --plan-run-id <ozon_cpc_optimization_plan_run_id> \
  --confirmed-by-user
```

Команда использует Ozon Performance API и делает:

- API-only preflight: Seller API, Performance API, WB API и каталог; LK
  проверки пропускаются, потому что операция не требует ЛК;
- свежий snapshot текущих ставок через
  `GET /api/client/campaign/<campaignId>/v2/products?page=<page>&pageSize=100`;
- fresh dry-run с текущими ставками;
- drift-check состава строк и ставок относительно согласованного плана;
- apply через `PUT /api/client/campaign/<campaignId>/products`;
- verify повторным чтением ставок после apply.

Ozon принимает `bid` в raw-единицах, совпадающих с чтением из API: ставка в
рублях умножается на `1000000`.

Защитные правила:

- строки с `target_bid <= 0` не применяются как ставка; для них нужен отдельный
  disable/exclude-контур;
- строки ниже безопасного минимума `--min-bid` пропускаются, не округляются
  молча;
- skipped rows сохраняются в `skipped_rows.csv`.

## Single bid apply

Точечное изменение одной CPC-ставки допускается только после явной команды
владельца вида:

```text
<sku> сделай <ставка>р
```

Даже для одной строки сохранять цепочку:

```text
API-only preflight -> current bid read -> dry-run artifact -> apply -> verify -> result
```

Обязательные артефакты в `data/runs/<date>/ozon_cpc_single_bid_apply_<timestamp>/`:

- `processed/dry_run.csv` - согласованная строка с текущей и целевой ставкой;
- `raw/update_response.json` - ответ Ozon Performance API без секретов;
- `processed/verify.json` - фактическая ставка после повторного чтения;
- `summary.json`;
- `ozon_cpc_single_bid_apply_result.md`.

Apply выполняется через:

```text
PUT /api/client/campaign/<campaignId>/products
```

`bid` передается в raw-единицах: рубли `* 1000000`.

## Формат отчета владельцу

Сохранять короткий формат:

```text
Краткий вывод: <эффективность/проблема>.

Отчет:
run_id: <run_id>
period: <date_from> - <date_to>
mode: read_only
source: Ozon Performance API

Сводка:
кампаний CPC всего: <n>
кампаний с активностью: <n>
расход: <money>
показы: <n>
клики: <n>
средний CPC: <money>
заказы: <n>
выручка по заказам: <money>
ДРР: <percent>
ROAS: <value>

Кампании:
<campaign summaries>

Проблемные строки:
<top spend without orders>

Лучшие строки:
<top orders/revenue>

Файлы отчета:
<paths>
```

Для optimization dry-run добавлять:

```text
К действиям:
pause_or_exclude: <n>
review_card_then_reduce_or_pause: <n>
reduce_bid_or_review: <n>
scale_candidate: <n>
keep_monitor: <n>

Размер ставки:
<sku>: <bid_reference> -> <target_bid> (<proposed_bid_change_percent>%)
```

## Safety

Изменение ставок, бюджетов, статусов кампаний и товаров в рекламе является
опасной операцией и требует:

```text
read-only -> dry-run -> review -> approved -> apply -> verify -> result
```
