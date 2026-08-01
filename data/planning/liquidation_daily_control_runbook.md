# Ежедневный контроль распродажи Ozon и WB

Статус: `implemented`, 2026-07-31.

## Назначение

Штатная задача `liquidation-daily-control` ежедневно контролирует точные
owner-approved когорты:

- Ozon: `138` SKU из сохраненного liquidation plan;
- Wildberries: `163` nmID из сохраненного liquidation plan.

Таймер ставит задачу в SQLite Job Worker, а не отправляет текстовое
напоминание. Ключ даты дедуплицирует повторную постановку.

## Контракт

Отчет использует только завершенные дни и для каждого товара сохраняет строку,
включая нулевые показы, клики и заказы. Seller/Statistics API заказы считаются
отдельно от рекламной атрибуции.

Hard stop:

- Ozon: CPC-расход `50 руб. * pack_qty` без Seller API заказа;
- WB: `10` кликов или `20 руб.` CPC-расхода без Statistics API заказа.

Достижение порога создает `stop_review.json` с `actions_checksum`. Это не
разрешение на изменение кампании. Автоматическое отключение CPC, изменение
цен, minimum, акций или ставок запрещено.

## Запуск

```bash
PYTHONPATH=src:. python scripts/monitoring/enqueue_liquidation_daily_control.py
```

Systemd:

```text
vital-shevron-liquidation-daily-reminder.timer
```

Имя legacy unit сохранено для совместимости, но его действие теперь - queue
фактического контроля в `09:00 МСК`.

## Артефакты

- `report.md` и `report.html`;
- `processed/ozon_by_product.csv`;
- `processed/wb_by_product.csv`;
- `processed/daily_by_product.csv`;
- `processed/stop_review.json`;
- `summary.json` и `manifest.json`.

## WB второй ценовой шаг

`wb-liquidation-stage2-plan` читает свежие WB цены только для `18` строк с
`requires_second_price_stage=true`, проверяет текущую скидку, цену и
распродажный floor и выпускает `pending_approval.json`. Upload не выполняется.
