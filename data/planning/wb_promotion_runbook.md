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
