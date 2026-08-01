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

## Источники участия в продвижении

- Ozon Elastic определяется по актуальному массиву
  `marketing_actions.actions`; поле `marketing_actions.current` используется
  только как fallback для старого формата ответа.
- WB CPC определяется по составу кампаний: `adverts[].nm_settings[]` и
  положительная поисковая ставка. Нельзя определять участие только по строкам
  рекламной статистики: товар без показов может отсутствовать в статистике, но
  оставаться в кампании.
- Отчет обязан показывать покрытие точной когорты: Ozon Elastic, Ozon CPC и WB
  CPC. Нулевые рекламные строки сохраняются.

Правило подтверждено повторным read-only запуском
`liquidation_daily_control_20260801T_corrected_analysis`: Ozon Elastic
`120/138`, Ozon CPC `122/138`, WB CPC `163/163`.

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

После owner approval используется только Job Worker задача
`wb-liquidation-stage2-apply`. Она выполняет scoped WB preflight, строит новый
fresh-план и требует полного совпадения checksum и payload с согласованным
планом. При любом drift upload запрещён. Разрешён один официальный upload
скидок; повторная запись без нового плана и approval запрещена. Базовая цена и
минимальная цена сохраняются без изменения. Успех подтверждается как историей
upload, так и свежим Prices API по каждой строке; отдельная read-only задача
`wb-liquidation-stage2-verify` доступна для recovery.

Подтверждённый запуск 2026-08-01:

- plan: `wb_liquidation_stage2_plan_20260801T150359`;
- apply: `wb_liquidation_stage2_apply_20260801T150526`;
- Job: `job_wb-liquidation-stage2-apply_20260801T120427Z_866af36d`;
- upload ID: `181544452`;
- drift: `0`; WB принял `17/17`; Prices API подтвердил `17/17`;
- у всех 17 строк изменена только скидка, базовая цена `2100 руб.` и minimum
  не менялись;
- runtime approval закрыт после `apply -> verify`.

Новый контрольный отсчёт для этих 17 товаров начинается
`2026-08-01 15:05:26 МСК`. Не принимать решение об остановке CPC по ранним
неполным суткам. Первый вывод после двух полных дней `2026-08-02` и
`2026-08-03` формируется ежедневным контролем `2026-08-04 09:00 МСК`.
