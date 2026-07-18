# Отчёт Ozon/WB за период

Дата актуализации: 2026-07-17.

## Назначение

Read-only отчёт по одному маркетплейсу за выбранный владельцем период. Доступен
в Telegram-меню Ozon и Wildberries и через CLI `marketplace-period-report`.

Варианты:

- `Краткий` - заказы, выкупы, возвраты, продажи, расходы и сумма к выплате;
- `Финансовый` - краткая сводка и расходы по статьям;
- `Полный` - финансовая сводка, товары и динамика по дням.

## Telegram Flow

1. `Ozon -> Отчёт за период` или `Wildberries -> Отчёт за период`.
2. Выбор вида отчёта.
3. Выбор периода: вчера, 7 дней, 30 дней, текущий месяц, прошлый месяц или
   собственный диапазон.
4. Для собственного диапазона даты вводятся как `ДД.ММ.ГГГГ` или
   `ГГГГ-ММ-ДД`.
5. Бот показывает маркетплейс, вид и даты. Только кнопка
   `Сформировать отчёт` запускает чтение API.
6. В чат приходит достаточная для решения сводка и Markdown-файл. Excel и JSON
   сохраняются в run-каталоге.

Временное состояние собственного диапазона хранится по `chat_id + thread_id`.
Callback обязан сохранить новый `conversation_state`; иначе следующее
текстовое сообщение с датой потеряет контекст.

## CLI

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python -m seller_agent.cli \
  marketplace-period-report \
  --marketplace ozon \
  --report-type financial \
  --date-from 2026-07-01 \
  --date-to 2026-07-16
```

## Формулы

### Ozon

- источник денег: `/v3/finance/transaction/list` по `operation_date`;
- выкуп: `OperationAgentDeliveredToCustomer`;
- сумма к выплате: сумма `amount` всех операций периода;
- продажи до расходов: сумма `accruals_for_sale`;
- физические изделия: количество item-строк/`quantity`, умноженное на
  `pack_qty` из `data/catalog/unified/products.csv`.

### Wildberries

- финансовая сводка: `/api/finance/v1/sales-reports/list`;
- товарные строки выкупов: `/api/finance/v1/sales-reports/detailed`;
- сумма к выплате: `bankPaymentSum` минус реклама `/adv/v3/fullstats`;
- физические изделия: `quantity` строк `Продажа`, умноженное на `pack_qty`;
- неразрезанная пара петлиц имеет `pack_qty=1` и считается одним изделием.

Предварительный WB Statistics Sales API нельзя использовать как точный
исторический знаменатель физических выкупов. Общие расходы без `nmId` не
распределяются по товарам без отдельного правила аллокации.

## Артефакты

```text
data/runs/YYYY-MM-DD/marketplace_period_report_<marketplace>_<timestamp>/
  marketplace_period_report.md
  marketplace_period_report.xlsx
  marketplace_period_report.json
  summary.json
  manifest.json
```

Telegram прикрепляет только безопасный `artifacts.report` Markdown. Excel
остаётся доступен в run-каталоге.

## Ограничения

- текущий день может быть финансово неполным;
- WB detailed endpoint имеет лимит один запрос в минуту на продавца; первая
  страница запрашивается с лимитом 100000 строк, следующая только при
  необходимости и после паузы;
- если marketplace ID отсутствует в unified catalog, отчёт использует
  `pack_qty=1`, явно показывает warning и список несопоставленных ID;
- общие расходы WB не приписываются конкретным товарам;
- task не меняет карточки, цены, акции, остатки и другие данные кабинета.

## Проверка

- unit-тесты должны отдельно проверять комплекты `pack_qty=2` на Ozon и WB;
- Telegram-тест должен проходить весь custom-date flow до callback
  подтверждения;
- после изменения bot code требуется перезапуск
  `vital-shevron-telegram-bot.service` и проверка active status.
