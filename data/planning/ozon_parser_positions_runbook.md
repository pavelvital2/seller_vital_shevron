# Ozon Parser Positions Runbook

Дата: 2026-06-13

## Назначение

Read-only анализ видимости товаров магазина в поисковой выдаче Ozon по данным
Ozon-парсера через Parser Data API.

## Источники

- Parser Data API: `http://127.0.0.1:8787`, dataset `ozon`.
- Env с base URL/token: `/home/pavel/.parser-data-api.env`.
- SERP: `marts/serp/latest/products_daily.csv`.
- Seller bridge: `marts/sellers/latest/seller_query_product_bridge.csv`.
- Sellers: `marts/sellers/latest/sellers_daily.csv`.
- Локальный Ozon-каталог проекта:
  `data/catalog/ozon/processed/ozon_catalog.csv`.

Parser Data API используется только read-only. Полные датасеты парсера не
копировать в постоянные проектные документы или `data/runs/`.

## Рабочая папка

Для промежуточных файлов можно использовать:

```text
tmp/ozon_parser_analytics/
```

Папка находится внутри проекта, исключена из git через `tmp/` и должна быть
очищена после завершения анализа. Финальные производные отчеты сохранять в
`data/runs/<date>/ozon_parser_analytics_<timestamp>/`; raw-данные парсера туда
не сохранять.

## Формат CSV

Ozon parser CSV на срезе 2026-06-13 разделены `;`. Если использовать общий
preview endpoint `/csv/head`, заголовки могут отображаться как одна колонка.
Для точного анализа нужно читать `/file` и парсить CSV с delimiter `;` и
encoding `utf-8-sig`.

## Идентификация товаров магазина

На срезе 2026-06-13 поля Ozon parser `productId` и `sku` совпали с локальным
полем `ozon_catalog.sku`, а не с локальным API `product_id`.

Базовый порядок идентификации:

1. Взять локальный каталог `data/catalog/ozon/processed/ozon_catalog.csv`.
2. Сопоставлять parser `sku` с локальным `sku`.
3. Если нет совпадения, пробовать parser `productId` с локальным `sku`.
4. Локальный API `product_id` использовать только как дополнительный fallback.
5. Проверить seller bridge по найденным товарам: `seller_name` и `seller_url`.

На срезе 2026-06-13 подтверждено:

- seller name найденных локальных товаров: `Vital Shevron`;
- seller URL: `https://www.ozon.ru/seller/vital-shevron/`;
- поле `seller_id` в текущем Ozon parser-срезе пустое, поэтому стабильный
  seller ID по этим данным подтвердить нельзя.

## Минимальный состав отчета

- источник и timestamp parser-среза;
- подтвержденный способ идентификации товаров магазина;
- количество строк локального Ozon-каталога;
- количество собранных запросов и SERP-строк;
- сколько локальных Ozon SKU найдено в выдаче;
- сколько локальных Ozon SKU не найдено в текущем наборе запросов;
- распределение позиций: top-1, top-3, top-10, top-30, top-100, дальше top-100;
- позиции по каждому запросу;
- лучшие видимые товары;
- слабые видимые товары;
- конкурентный срез по `seller_name`;
- ограничения анализа.

## Ограничения

- SERP-парсер показывает только собранные запросы и глубину выдачи, а не всю
  выдачу Ozon.
- Позиции Ozon могут меняться в течение дня.
- Если локальный Ozon-каталог устарел, анализ нужно повторить после обновления
  каталога.
- Количество продаж из количества запросов напрямую не считать без CTR,
  конверсии карточки, цены, рекламы, остатков и фактических заказов.
