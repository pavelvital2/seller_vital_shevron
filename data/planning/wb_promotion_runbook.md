# WB Promotion Runbook

## Итог

Отчет по WB продвижению выполняется read-only через официальный WB Promotion
API. Изменение ставок, бюджетов, статусов кампаний и состава карточек является
опасной операцией и требует отдельной цепочки:

```text
read-only -> dry-run -> review -> approved -> apply -> verify -> result
```

## Источники

Используемые API:

- `GET /adv/v1/promotion/count` - список кампаний по типам и статусам;
- `GET /api/advert/v2/adverts` - настройки кампаний, payment type, ставки и
  nmID;
- `GET /adv/v3/fullstats` - статистика кампаний за период, максимум 31 день;
- `GET /adv/v1/balance` - баланс продвижения.

Документация WB: `https://dev.wildberries.ru/docs/openapi/promotion`.

## Read-only report

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli wb-promotion-report \
  --date-from <YYYY-MM-DD> \
  --date-to <YYYY-MM-DD>
```

По умолчанию команда берет последние 14 дней.

Можно ограничить тип оплаты:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli wb-promotion-report \
  --payment-type cpc
```

Команда делает API-only preflight, собирает campaigns/statistics/balance,
сохраняет raw/processed артефакты и не меняет ставки, бюджеты, статусы или
состав кампаний.

## Bid optimization dry-run

Перед dry-run ставок нужно получить свежий read-only отчет:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli wb-promotion-report \
  --payment-type cpc
```

Затем построить план изменений ставок:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli plan-wb-promotion-bids \
  --source-run-id <wb_promotion_report_run_id>
```

Команда использует `wb_promotion_products.csv` и `raw/campaigns.json` из
read-only отчета. Текущая ставка берется из `nm_settings[].bids_kopecks`
официального WB Promotion API и переводится из копеек в рубли.

По умолчанию dry-run ограничен активными CPC-кампаниями. Строки `keep_monitor`
сохраняются в полном CSV с изменением `0.00`; строки с рекомендацией к действию
дополнительно сохраняются в `wb_promotion_bid_changes.csv`.

Базовые пороги:

```text
zero_orders_spend: 10
high_drr_percent: 5
high_drr_min_spend: 30
scale_min_orders: 3
scale_max_drr_percent: 1
card_review_reduce_percent: 20
zero_no_cart_reduce_percent: 30
max_reduce_percent: 30
scale_low_drr_percent: 20
scale_mid_drr_percent: 15
scale_high_drr_percent: 10
min_bid: 1.00
```

Поля dry-run:

```text
advert_id
campaign_name
nm_id
name
recommended_action
reason
current_bid
current_bid_place
current_bid_source
requested_bid_change_percent
target_bid
bid_change_amount
actual_bid_change_percent
target_adjustment_note
views
clicks
atbs
orders
spend
revenue
drr_percent
apply_allowed
```

Если расчетное снижение уходит ниже `min_bid`, target фиксируется на `1.00`,
а строка получает `target_adjustment_note = min_bid_floor`. Dry-run не меняет
ставки и не является разрешением на apply.

## Bid apply

Применение ставок WB promotion - опасная операция. Перед apply должен быть
согласованный dry-run plan и явное подтверждение владельца.

Текущий API-метод:

- `PATCH /api/advert/v1/bids` - изменение ставок карточек в кампаниях;
- payload содержит `advert_id`, `nm_id`, `bid_kopecks`, `placement`;
- WB указывает, что изменение ставки происходит с интервалом до 30 секунд.

Документация WB: `https://dev.wildberries.ru/docs/openapi/promotion`.

Безопасная команда для первого прохода повышения:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli apply-wb-promotion-bids \
  --plan-run-id <wb_promotion_bid_plan_run_id> \
  --actions scale_candidate \
  --confirmed-by-user
```

По умолчанию apply применяет только `scale_candidate`. Снижения
`reduce_bid_or_review` и строки `review_card_then_reduce` не применяются, если
они не указаны явно в `--actions`.

Контур apply:

```text
approved plan -> API-only preflight -> fresh wb-promotion-report ->
fresh plan with approved thresholds -> drift-check by advert_id/nm_id/action/current_bid/target_bid ->
PATCH /api/advert/v1/bids -> wait -> fetch campaigns -> verify target bids
```

Строки пропускаются, если:

- action не выбран в `--actions`;
- нет `current_bid` или `target_bid`;
- текущая ставка не была получена через API;
- `target_bid` ниже `min_bid`;
- `target_bid` равен `current_bid`;
- не определен placement.

## Метрики

Минимальный набор:

```text
period
campaigns_total
campaigns_with_stats
spend
views
clicks
avg_cpc
atbs
orders
revenue
drr_percent
roas
```

По кампаниям и товарам сохранять:

```text
advert_id
campaign_name
status
type
payment_type
bid_type
nm_id
current bids
views
clicks
atbs
orders
spend
revenue
drr_percent
roas
```

## Формат отчета владельцу

Telegram-вывод строить по общему стандарту
`data/planning/chat_report_templates.md`. Для WB promotion использовать:

```text
Краткий вывод: WB продвижение <эффективно/требует внимания>.

Отчет:
run_id: <run_id>
period: <date_from> - <date_to>
mode: read_only
source: WB Promotion API

Сводка:
кампаний всего: <n>
кампаний со статистикой: <n>
товарных строк: <n>
расход: <money>
показы: <n>
клики: <n>
средний CPC: <money>
добавления в корзину: <n>
заказы: <n>
выручка: <money>
ДРР: <percent>
ROAS: <value>

Топ кампаний по расходу:
<campaign rows>

Расход без заказов:
<nm rows>

Лучшие товары:
<nm rows>

Файлы отчета:
<paths>
```

## Safety

До apply по WB promotion нужно спроектировать отдельный контур для конкретного
типа операции:

- изменение ставок;
- изменение бюджета;
- запуск/пауза/остановка кампании;
- изменение карточек в кампании;
- минус-фразы и поисковые кластеры.

Нельзя выполнять write-операции WB promotion по одному read-only отчету без
fresh dry-run, drift-check и явного подтверждения владельца.

## Parser-enriched bid recommendations

Подтвержденный сценарий 2026-06-28: для увеличения продаж можно расширять
штатный dry-run ставок данными WB parser и WB API stock/sales signals.

Источники для такого расчета:

- свежий `wb-promotion-report` за период 14 дней;
- штатный `plan-wb-promotion-bids` как базовый расчет эффективности;
- последний WB parser positions/warehouse run - только видимость, позиции,
  buyer-visible price/quantity и поисковый контекст;
- свежие WB stock/sales signals через `collect-card-signals` без
  `--skip-api`; продажи должны приходить из WB Statistics API
  `/api/v1/supplier/sales`, parser не является источником продаж.

Штатная команда parser-enriched dry-run:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli plan-wb-promotion-bids-parser-enriched \
  --base-plan-run-id <wb_promotion_bid_plan_run_id>
```

Если `sales_signals.csv`/`all_signals.csv` не содержит строк из WB API sales,
строки получают флаг `missing_wb_statistics_sales_signal`. Это не значит, что продаж не
было; это значит, что в текущем расчете нет отдельного подтверждения продаж из
WB Statistics API. Перед финальным owner-review ставок нужно обновить signals
через API:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli collect-card-signals \
  --marketplace wb \
  --parser-source latest
```

Правило сегментации:

- `apply-ready` - есть рекламная эффективность, приемлемый ДРР, текущая ставка
  из API и подтвержденный положительный остаток;
- `review-only/test` - есть parser-сигнал, но нет заказов в рекламе, либо
  остаток низкий;
- `blocked/reduce` - нет текущей ставки, нет подтвержденного остатка, высокий
  ДРР или расход без заказов.

Штатная apply-команда parser-enriched ставок:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli apply-wb-promotion-bids-parser-enriched \
  --plan-run-id <wb_promotion_bid_parser_enriched_plan_run_id> \
  --confirmed-by-user
```

Команда принимает только owner-approved строки из
`wb_promotion_bid_parser_enriched_apply_preview.csv`. Если владелец явно
согласовал все кандидаты, включая review-only/test строки, сначала нужно
использовать отдельный apply-запуск с явным списком разрешенных действий:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli apply-wb-promotion-bids-parser-enriched \
  --plan-run-id <wb_promotion_bid_parser_enriched_plan_run_id> \
  --approved-actions review_only \
  --confirmed-by-user
```

Без `--approved-actions review_only` штатный контур применяет только
`apply_ready`. Для `review_only` owner-approved package считается отдельным от
`apply_ready` idempotency-пакетом: `plan_id:actions=review_only`.

Apply выполняется только через fresh-проверку:

```text
approved parser-enriched package -> API-only preflight ->
fresh wb-promotion-report -> fresh collect-card-signals from WB API/parser ->
fresh plan-wb-promotion-bids -> fresh plan-wb-promotion-bids-parser-enriched ->
partial drift-check by advert_id/nm_id/placement/current_bid/final_target_bid/action ->
PATCH /api/advert/v1/bids -> wait -> fetch campaigns -> verify target bids
```

Строки с drift должны быть пропущены, а не применены автоматически. Новые
строки свежего плана, которых не было в owner-approved пакете, тоже не
применяются без нового согласования.

Подтвержденный apply:

```text
data/runs/2026-06-28/wb_promotion_bids_apply_all23_20260628T221859
```

Результат:

- approved rows: `23`;
- applied rows: `23`;
- skipped rows: `0`;
- drift rows: `0`;
- сумма ставок: `24.72 -> 28.04`;
- прирост ставок: `+3.32`;
- verify: `ok`, mismatches `0`.

Штатный контур с 2026-07-05:

- dry-run: `plan-wb-promotion-bids-parser-enriched`;
- apply: `apply-wb-promotion-bids-parser-enriched`;
- task id: `wb-promotion-bids-parser-enriched-apply`;
- обязательный owner approval: `--confirmed-by-user`;
- отдельное применение `review_only`: только через `--approved-actions review_only`
  после owner approval файла/отчета с этими строками;
- idempotency: approved parser-enriched plan нельзя применить повторно;
- verify: ставки перечитываются из настроек кампаний WB после ожидания.

Подтвержденный apply `review_only` от 2026-07-05:

```text
data/runs/2026-07-05/wb_promotion_bid_parser_enriched_apply_20260705T170235
```

Результат:

- approved selected rows: `122`;
- unchanged rows: `122`;
- drift rows: `0`;
- applied rows: `107`;
- skipped rows: `15`;
- причина skip: `target_bid equals current_bid` у низкоостаточных строк;
- сумма ставок примененных строк: `109.74 -> 120.72`;
- прирост ставок: `+10.98`;
- verify: `ok`, mismatches `0`.

## Проверка после изменения скидок 2026-07-17

Для анализа ставок после массового снижения скидок подтвержден следующий
read-only порядок:

1. Снимок акций и цен из ЛК до/после изменения.
2. Promotion API за 30 дней: кампании, ставки, показы, клики, корзины, заказы,
   расход, выручка, ДРР и ROAS.
3. Текущие остатки через
   `POST /api/analytics/v1/stocks-report/wb-warehouses`.
4. Продажи Statistics API за период с `flag=0`; `flag=1` возвращает только
   календарную дату `dateFrom` и не подходит для 30-дневного сигнала.
5. Parser Data API как baseline позиций и видимости, но не как источник продаж.
6. Parser-enriched dry-run без apply.

Подтвержденный run:

```text
data/runs/2026-07-17/wb_bid_analysis_20260717T2116
```

Факты запуска:

- участие в акциях: `356 -> 126`, вышло `230` товаров;
- Promotion API: только CPC-кампании (`287` строк, `16` кампаний), поэтому
  официальный `GET /api/advert/v0/bids/recommendations` применять нельзя: он
  работает только для CPM;
- parser baseline имеет дату `2026-07-16`, то есть до изменения скидок;
- точечное повышение: `5` строк;
- снижение/review: `2` строки;
- массовое изменение ставок не рекомендовано.

После изменения скидок нельзя оценивать влияние на позиции по parser-срезу,
сделанному до изменения. Нужен следующий успешный parser run и сравнение
одинаковых запросов/товаров до и после; ставки в этот период менять только
узким тестом, иначе эффект цены, акции и ставки нельзя разделить.

### Корректировка бизнес-плана роста

Строгий parser-enriched список из `5` строк является техническим
`apply_ready`, но не является достаточным планом роста магазина. Для
компенсации выхода `230` товаров из акций нужен портфельный подход по всей
воронке.

Актуальный read-only portfolio plan:

```text
data/runs/2026-07-17/wb_portfolio_growth_plan_20260717T2201
```

Первая волна содержит `146` товаров, из них `117` вышли из акций:

- `10` сильных победителей: ставка `+20%`;
- `46` подтвержденных победителей: ставка `+10%`;
- `50` товаров с недостатком показов: контролируемый visibility-test `+15%`;
- `20` товаров с органическим спросом без кампании: стартовая CPC-ставка
  `1,10 руб.`;
- `20` discovery-товаров без рекламной истории: стартовая CPC-ставка
  `1,00 руб.`.

Отдельно исключены из повышения:

- `146` товаров без текущего остатка;
- `14` товаров с остатком менее `4`;
- `1` победитель с покрытием остатка менее `14` дней;
- `2` строки без подтвержденной текущей ставки;
- `11` карточек с кликами/корзинами, но без заказов: сначала исправление
  конверсии;
- `5` эффективных товаров уже в parser top-30: ставка сохраняется.

Контроль первой волны:

- фактический расход сначала не более `100 руб./день` на портфель;
- после трех дней при ДРР `<=5%` и CPA `<=15 руб.` контрольный уровень можно
  повысить до `150 руб./день`;
- hard-stop строки при ДРР `>7%` или CPA `>25 руб.` после достаточного трафика;
- apply только отдельным owner-approved пакетом с fresh preflight, drift-check
  и verify. Создание/добавление новых товаров в кампании требует отдельного
  write-контура и не должно подменяться apply существующих ставок.

### Apply первой волны 2026-07-17

Первоначальная классификация `40` строк как новых подключений была частично
ошибочной: расчет использовал строки товарной статистики кампаний, а не полный
`nm_settings` всех активных CPC-кампаний. Товар без показов/кликов за период
мог отсутствовать в статистике, хотя уже находился в кампании.

Обязательное правило: перед классификацией `вне кампании` объединять
статистику с полным свежим составом всех активных CPC-кампаний из
`GET /api/advert/v2/adverts`. Частичный список кампаний или строки fullstats не
являются доказательством отсутствия товара в рекламе.

Фактический apply:

- `wb_portfolio_existing_bids_apply_20260717T222157`: применено и проверено
  `106/106`, drift `0`, сумма ставок `120,52 -> 136,86 руб.`;
- `wb_portfolio_existing_bids_apply_20260717T222816`: после сверки полного
  membership применено и проверено еще `19/19`, drift `0`, сумма ставок
  `19,00 -> 20,90 руб.`;
- `20` строк уже имели целевую ставку и не требовали записи;
- итоговая свежая сверка: `145/146` строк на целевых ставках, mismatch `0`;
- единственная незакрытая строка: WB `nmID 436578563`, рабочий фартук. Он не
  входит ни в одну активную CPC-кампанию; добавлять его в кампанию шевронов
  нельзя. Нужен отдельный owner-approved пакет создания/выбора подходящей
  кампании со стартовой ставкой `1,10 руб.`.

Финальный reconciliation:

```text
data/runs/2026-07-17/wb_portfolio_final_reconciliation_20260717T223148
```

### Первый parser-контроль 2026-07-18

Сопоставлены одинаковые 24 запроса и полные пары `query + nmID` за
`2026-07-16 -> 2026-07-17`. Новый SERP начат `2026-07-18 00:15 МСК`, примерно
через 1 час 54 минуты после apply ставок, поэтому это ранний совместный сигнал
цены, выхода из акций и рекламы, а не доказанный эффект ставки.

Ключевые факты:

- видимых товаров: `87 -> 80`, связок товар-запрос: `148 -> 123`;
- товары с лучшей позицией top-30: `7 -> 7`, top-100: `21 -> 21`;
- рекламная волна: top-30 `1 -> 3`, top-100 `7 -> 9`;
- товары вне волны: top-30 `6 -> 4`, top-100 `14 -> 12`;
- вышедшие из акций: видимость `61 -> 51`, top-100 `18 -> 14`;
- сохранившие участие: видимость `26 -> 29`, top-100 `3 -> 7`;
- у 70 сопоставимых товаров медианный рост parser final price `50,2%`;
  при росте цены более 40% медианное изменение лучшей позиции `-10`, при
  росте до 10% — `+9`.

Вывод: рекламная волна частично удержала верх выдачи, но не компенсировала
потерю ширины после повышения цены/выхода из акций. Не повышать ставки повторно
по одному раннему срезу; следующий контроль — 3 и 7 полных дней с Promotion
API, заказами, ДРР/CPA и parser.

Отчет:

```text
data/runs/2026-07-18/wb_parser_change_analysis_20260718T063414/report.html
```

### Контроль через три полных дня и план при марже 50 рублей

Сопоставление Promotion API за `2026-07-14..2026-07-16` до изменения ставок и
`2026-07-18..2026-07-20` после изменения подтвердило положительный эффект:

- показы: `1 566 -> 3 642`;
- клики: `52 -> 115`;
- заказы: `3 -> 18`;
- выручка: `1 610 -> 8 198 руб.`;
- расход: `66,06 -> 175,23 руб.`;
- ДРР: `4,10% -> 2,14%`.

Для следующего margin-aware dry-run зафиксирована модель: себестоимость
`85 руб.` и защищенная маржа `50 руб.` на физическое изделие, `pack_qty`
умножает обе величины, общие расходы и логистика относятся к одному проданному
товару. Текущая ставка берется только из полного состава активных CPC-кампаний
`GET /api/advert/v2/adverts`.

Результат read-only анализа: повысить `88` ставок, снизить `3`, удержать `71`
из-за остатка ниже `8 шт.`, не масштабировать `105` позывных. Минимальная
расчетная маржа повышаемого пула — `154,44 руб.` на изделие. Fresh parser за
`2026-07-20` полный: видимых товаров `74 -> 84`, связок товар-запрос
`108 -> 133`, top-30 `3 -> 5`, top-100 `15 -> 17`.

В целевой категории `449` уникальных карточек: `365` уже состоят в активных
CPC-кампаниях, `84` находятся вне CPC. У всех 84 остаток ниже `8 шт.` и нет
продаж за 30 дней; из них `63` позывных и `21` обычная карточка. Новые
подключения до пополнения остатков не рекомендованы. Один комплект фальшпогон
исключен из повышения, потому что его себестоимость не подтверждена моделью
для шевронов.

Артефакты:

```text
data/runs/2026-07-21/wb_promotion_margin50_analysis_20260721T0720
```

Apply не выполнялся. Перед записью обязателен свежий Promotion API snapshot,
проверка цен и остатков, drift-check точного согласованного файла и verify.

### Apply margin-aware пакета 2026-07-21

Владелец согласовал точный пакет из `88` повышений и `3` снижений. Для него
использован отдельный exact-plan контур:

```text
scripts/analytics/apply_wb_margin50_bids.py
```

Safety-порядок:

1. checksum согласованных CSV и Markdown;
2. API-only status preflight;
3. fresh `nm_settings` активных CPC-кампаний;
4. fresh цены и остатки каждой строки;
5. partial drift-check по `advert_id + nmID + placement + current_bid`, цене и
   остатку не менее `8 шт.` для повышений;
6. `PATCH /api/advert/v1/bids` только для неизменившихся строк;
7. ожидание `45 секунд` и verify по campaign settings.

Результат:

- run: `wb_margin50_bids_apply_20260721T074342`;
- согласовано и применено: `91/91`;
- drift: `0`;
- сумма ставок: `108,07 -> 174,65 руб.`;
- verify: `ok`, mismatches `0`.

Первый apply-проход до write остановился на read-only запросе остатков с
`HTTP 429`: отдельный preflight вызвал endpoint менее чем за 20 секунд до
apply. Запрос `PATCH /api/advert/v1/bids` тогда еще не отправлялся. После
выдержки лимита выполнен полный повторный fresh-check и успешный apply.
Обязательное правило: между последовательными вызовами
`stocks-report/wb-warehouses` одного продавца выдерживать не менее 20 секунд;
после `429` сначала подтвердить отсутствие update response/apply marker, затем
повторять весь fresh-check, а не только write.

Следующий контроль: утром `2026-07-25` за три полных дня и утром
`2026-07-29` за семь полных дней. До первого контроля ставки повторно не
изменять.
