# WB Parser Positions Runbook

Дата: 2026-06-13

## Назначение

Read-only анализ позиций Vital Shevron в выдаче Wildberries по данным парсера
WB через Parser Data API.

## Источники

- Parser Data API: `http://127.0.0.1:8787`, dataset `wb`.
- Env с token/base URL: `/home/pavel/.parser-data-api.env`.
- Основной маршрут после 2026-07-05 - WB warehouse endpoints Parser Data API:
  - `/warehouse/wb/summary`;
  - `/warehouse/wb/query-positions`;
  - `/warehouse/wb/daily-changes`;
  - `/warehouse/wb/top-movers`;
  - `/warehouse/wb/seller-changes`;
  - `/warehouse/wb/run-quality`.
- Legacy raw/marts sources оставлены только как справочный слой Parser VPS:
  `marts/serp/latest/products_daily.csv`,
  `marts/sellers/latest/seller_query_product_bridge.csv`,
  `marts/sellers/latest/sellers_daily.csv`.
- Локальный WB-каталог проекта:
  `data/catalog/wb/processed/wb_catalog.csv`.

Parser Data API используется только read-only. Полные датасеты парсера не
копировать в проект.

Перед анализом свежего top-query прохода обязательно подтвердить актуальность
среза Parser Data API через `/warehouse/wb/summary` и `/warehouse/wb/run-quality`:

- `built_at_utc`;
- `min_run_date` / `max_run_date`;
- количество `query_position_rows`;
- количество уникальных запросов;
- статус последних `serp` и `sellers` прогонов.

Нельзя переиспользовать старый отчет из `data/runs/`, если владелец сообщил,
что parser только что закончил новый проход. Сначала нужно проверить latest
датасет в Parser Data API.

## Идентификация наших товаров

Наши товары нельзя определять по бренду `VitalEmb`: бренд встречается не только
в нашем магазине.

Базовый порядок идентификации:

1. Взять `nm_id` из локального WB-каталога Vital Shevron.
2. Сопоставить эти `nm_id` с `seller_query_product_bridge.csv`.
3. Подтвердить `supplier_id` в `sellers_daily.csv`.
4. Для отчета считать нашими только строки SERP, где `nmId` есть в локальном
   каталоге, либо где `supplier_id` отдельно подтвержден владельцем.

На срезе 2026-06-13 подтверждено:

- наш `supplier_id`: `4516781`;
- seller name: `Vital Shevron`;
- чужой seller с названием/брендом `VitalEmb`: `supplier_id=4234078`.

## Рабочая папка

Для промежуточных файлов можно использовать:

```text
tmp/wb_parser_positions_analysis/
```

Папка находится внутри проекта, исключена из git через `tmp/` и должна быть
очищена после завершения анализа. Финальные производные отчеты сохранять в
  `data/runs/<date>/...`; raw-данные парсера туда не сохранять.

## Штатная команда

После согласования владельцем 2026-07-05 используется команда:

```bash
python -m seller_agent.cli wb-parser-warehouse-analytics \
  --supplier-id 4516781 \
  --limit 500 \
  --report-limit 50
```

Команда:

- читает Parser Data API только read-only;
- учитывает текущее ограничение Parser Data API `limit <= 500`;
- не копирует полные parser datasets в seller project;
- дополнительно фильтрует все строки по `supplier_id=4516781`, даже если
  отдельный endpoint вернул общий рыночный набор;
- сохраняет производные файлы в
  `data/runs/<date>/wb_parser_warehouse_analytics_<timestamp>/`;
- пишет `summary.json`, Markdown report, CSV по query positions, daily
  changes, top movers, seller changes и слабым видимым кандидатам;
- регистрирует запуск в `RunManifest` как task
  `wb-parser-warehouse-analytics`;
- доступна в Telegram через `/wb-analytics` и кнопку `WB аналитика`.

## Минимальный состав отчета

- источник и timestamp parser-среза;
- подтвержденный способ идентификации наших товаров;
- количество товаров в локальном WB-каталоге;
- сколько наших `nm_id` найдено в SERP;
- распределение позиций: топ-1, топ-3, топ-10, топ-30, топ-100, вне топ-100;
- позиции по каждому запросу;
- лучшие и слабые карточки;
- конкурентный срез по `supplier_id`, а не только по имени продавца;
- отдельная проверка строк `brand=VitalEmb`, которые не относятся к нашему
  `supplier_id`.
- кандидаты с остатком вне top-30 для очереди SEO/карточек.

## Ограничения

- SERP-парсер показывает только собранные запросы и страницы, а не всю выдачу
  WB.
- Позиции WB могут меняться в течение дня.
- Если локальный WB-каталог устарел, анализ надо повторить после обновления
  каталога.
