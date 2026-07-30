# Ozon CPC Efficiency Runbook

## Итог

Отчет по продвижению Ozon `Оплата за клик` выполняется только read-only через
Ozon Performance API.

Используемые источники:

- `GET /api/client/campaign` - список рекламных кампаний;
- `POST /api/client/statistics/json` - генерация статистического отчета;
- `GET /api/client/statistics/report?UUID=<uuid>` - скачивание готового отчета.

Для Vital Shevron в отчет включать только кампании с `PaymentType = CPC`.

## Штатный свежий сборщик

С 2026-07-19 повторяемый read-only сбор выполняется командой:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  scripts/analytics/ozon_cpc_efficiency_report.py \
  --date-to <последний завершенный день YYYY-MM-DD>
```

Сборщик запрашивает один 30-дневный отчет с `groupBy=DATE`, затем без второго
API-запроса рассчитывает:

- последние 15 завершенных дней;
- предыдущие сопоставимые 15 дней;
- полный 30-дневный период;
- агрегаты по SKU: spend, views, clicks, CTR, CPC, orders, CPA, revenue, DRR,
  ROAS;
- join с текущим составом/ставками активной CPC-кампании и последним
  подтвержденным Elastic/parser портфелем.

Ozon допускает только один активный запрос статистического отчета. После
успешного `POST /api/client/statistics/json` ответ `404 report not found` на
маршруте скачивания означает, что отчет еще формируется. Нужно опрашивать тот
же UUID; нельзя создавать параллельный запрос. Для продолжения ожидания:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  scripts/analytics/ozon_cpc_efficiency_report.py \
  --date-to <YYYY-MM-DD> \
  --report-uuid <UUID>
```

Результат сохраняется в
`data/runs/<date>/ozon_cpc_efficiency_<timestamp>/`: `report.html`, Markdown,
`processed/daily_rows.csv`, `processed/by_sku_30d.csv`, безопасные raw JSON и
закрытый read-only RunManifest. Отсутствие остатка/необходимость пополнения
имеет приоритет над исторической рекламной эффективностью SKU.

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
  -m seller_agent.cli plan-ozon-cpc-optimization \
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

Добавление товара и обновление ставки являются разными операциями:

```text
POST /api/client/campaign/<campaign_id>/products
PUT  /api/client/campaign/<campaign_id>/products
POST /api/client/campaign/<campaign_id>/products/delete
```

- `POST` добавляет отсутствующие SKU в кампанию, до 500 товаров;
- `PUT` обновляет ставки уже добавленных товаров;
- `POST .../products/delete` удаляет переданный массив `sku` из кампании;
- нельзя использовать `PUT` как способ добавления: Ozon может вернуть
  `404 Объект не найден`;
- перед добавлением проверять недельный лимит: обязательный минимум Ozon равен
  `2 000 руб. × количество SKU` после добавления;
- если лимита хватает только на часть owner-approved пакета, можно применить
  только помещающуюся часть с сохранением исходного приоритета и отдельным
  verify; увеличение `weeklyBudget` требует нового явного owner approval;
- перед `POST` обязателен свежий `GET`, после него - повторный `GET` и verify
  членства и ставки каждого SKU.
- удаление выполнять отдельным точным пакетом; сначала проверить отсутствие
  удалённых SKU и неизменность остального состава, только затем добавлять
  другие товары.

Источник: актуальная документация Ozon Performance API `2.0`, проверена
2026-07-30 через прямую ссылку из ЛК.

Постоянное правило владельца от 2026-07-30: позывные не включать в CPC
независимо от исполнения - ни одиночные нагрудные, ни комплекты. Для кампании
`20233460` подтверждён apply
`ozon_cpc_non_callsign_apply_20260730T111100`: удалено `17/17` позывных,
добавлено `11/11` обычных товаров со ставкой `1 руб.`, итоговый состав
`416` товаров, позывных `0`, недельный бюджет сохранён.

Поле `bid` приходит как целое значение в микро-рублях; для отчета ставка
делится на `1000000`.

Для плана с текущими ставками запускать:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli plan-ozon-cpc-optimization \
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
  -m seller_agent.cli apply-ozon-cpc-bids \
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

### Связанный apply цен и CPC

Когда повышение CPC согласовано только вместе с повышением `price/old_price`,
не использовать обычный `apply-ozon-cpc-bids`: он не проверяет зависимость
ставки от цены. Для exact package использовать:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  scripts/pricing/apply_ozon_price_cpc_growth.py \
  --plan-run-id <approved_exact_plan_run_id> \
  --confirmed-by-user
```

Правила:

- проверить checksum CSV относительно approved summary;
- сделать fresh partial drift-check цен и ставок;
- сначала применить и подтвердить цены;
- ростовую ставку применять только для SKU с подтвержденной целевой ценой;
- снижение высокой ДРР можно применять независимо от ценового шага;
- `target_bid <= 0`, pause и исключение товара не подменять числовой ставкой:
  для них нужен отдельный проверенный API-маршрут.

Подтвержденный live run 2026-07-19:
`ozon_price_cpc_growth_apply_20260719T092740`, цены `283/283`, числовые ставки
`70/70`, drift/skipped `0`.

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

## Контроль после массового изменения ставок

После массового apply оценивать не весь CPC-портфель, а точную когорту SKU из
`bid_apply_rows.csv`. Сравнивать ее с теми же SKU до изменения и отдельно с
контрольной группой, которой ставки не повышались.

Минимальный ранний контроль:

1. два одинаковых календарных окна до/после по Ozon Performance API;
2. расход, показы, клики, CTR, средний CPC, заказы, конверсия, CPA и ДРР;
3. Parser Data API `store-period-comparison` по срезу до apply и первому срезу
   после apply;
4. позиции точной когорты повышенных ставок и контрольной группы;
5. отдельный список SKU с расходом около/выше `50 руб.` без заказа.

День apply считается смешанным, а текущий день - неполным. Такой контроль
показывает ранний сигнал, но не является основанием для второго массового
изменения ставок. Первое бизнес-решение принимать минимум после трех полных
дней, устойчивое - после семи. Если одновременно менялись цена и CPC, нельзя
приписывать весь результат одной ставке.

Подтвержденный пример: `ozon_cpc_post_apply_review_20260720T2130`. Через сутки
после `ozon_price_cpc_growth_apply_20260719T092740` у 63 усиленных SKU
parser-видимость улучшилась, но рост заказов еще не подтвердился. Поэтому
решение - не повышать ставки повторно и продолжить наблюдение.

Повторяемый трехдневный контроль выполняется через
`scripts/analytics/ozon_cpc_post_apply_review.py`. Помимо ближайших трех полных
дней до apply нужно показывать контрольное окно тех же дней недели за
предыдущую неделю: короткий период чувствителен к календарному составу.
Перед выводами проверять свежий состав кампании: текущие ставки точной когорты
должны совпадать с `target_bid` исходного apply, иначе результат смешан с более
поздним изменением. Для итогового семидневного контроля использовать
`--final-control`; передавать свежий
`raw/current_campaign_products.json` через
`--current-campaign-products-json`. Результат проверки ставок сохраняется в
`processed/bid_drift.csv`.

Подтвержденный трехдневный контроль 2026-07-23:
`ozon_cpc_post_apply_review_20260723T224406`.

- полные дни до/после: `16-18.07` против `20-22.07`;
- контроль тех же дней недели: `13-15.07` против `20-22.07`;
- у 63 повышенных ставок расход `586,90 -> 846,06 руб.`, заказы `59 -> 34`,
  CPA `9,95 -> 24,88 руб.`, ДРР `2,22% -> 4,40%`;
- контроль тех же дней недели также подтвердил снижение заказов:
  `70 -> 34`;
- parser `19.07 -> 22.07`: видимые товары `34 -> 25`, средняя позиция
  `224,26 -> 263,63`; контрольная группа просела сильнее:
  `189,76 -> 245,64`;
- у 7 сниженных ставок расход уменьшился на `81,5%`, заказы сохранились
  `5 -> 5`, ДРР улучшилась `20,44% -> 2,96%`;
- все 70 текущих ставок совпали с verified apply, drift не найден;
- решение: не повышать повторно, сохранить 18 эффективных SKU, два SKU с
  расходом `30-50 руб.` без заказа проверить через 24 часа; dry-run снижения
  готовить только после достижения guardrail `50 руб.` без заказа.

Подтвержденный итоговый семидневный контроль 2026-07-27:
`ozon_cpc_post_apply_review_20260727T091743`.

- окно после apply: `20-26.07`, календарно сопоставимый baseline `06-12.07`;
- у 63 повышенных ставок расход вырос на `34,9%`, заказы снизились на `42,5%`,
  CPC вырос `1,59 -> 2,43 руб.`, ДРР `3,35% -> 5,38%`;
- в performance-контроле без всех 70 измененных SKU заказы снизились на
  `40,8%`, но расход вырос только на `4,4%`: спад конверсии был общим, а
  усиленная когорта получила непропорциональный рост расхода;
- parser-видимость `34 -> 35`, средняя позиция сопоставимых пар ухудшилась
  `249,68 -> 271,93`;
- 23 ставки оставить, 3 SKU вынести только в отдельный dry-run снижения,
  2 SKU проверить через 24 часа;
- снижение 7 прежних высокозатратных ставок подтверждено эффективным:
  расход `-83,4%`, заказы `7 -> 10`, ДРР `30,45% -> 2,92%`;
- drift-check: `70/70` ставок совпадают с apply, drift и missing отсутствуют.

## Формат отчета владельцу

Telegram-вывод строить по общему стандарту
`data/planning/chat_report_templates.md`. Для Ozon CPC сохранять короткий
формат:

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
