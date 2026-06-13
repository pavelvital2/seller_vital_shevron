# SEO Audit Runbook

Дата: 2026-06-13

## Назначение

Read-only SEO-аудит карточек Ozon/WB на основе спроса, фактической выдачи,
контента карточек, остатков и будущих рекламных/продажных метрик.

Цель аудита - не менять карточки сразу, а сформировать review-list и будущий
dry-run правок названий, описаний, характеристик и рекламных приоритетов.

## Safety

SEO-аудит является read-only операцией.

Любые изменения карточек, названий, описаний, характеристик, фото, цен,
рекламы или остатков считаются опасными write-операциями и требуют цепочки:

```text
read-only -> dry-run -> review -> approved -> apply -> verify -> result
```

Нельзя сохранять или выводить API-ключи, cookies, storage state, auth headers и
коды входа.

## Источники Ozon

- `data/runs/<date>/search_queries_shevron_*/ozon_top_queries_shevron.json` -
  топ поисковых запросов Ozon по слову `шеврон`.
- `data/runs/<date>/ozon_parser_analytics_*/query_visibility.csv` - позиции
  магазина по запросам из Ozon-парсера.
- `data/runs/<date>/ozon_parser_analytics_*/our_products_visibility.csv` -
  видимость Ozon SKU магазина в выдаче.
- `data/catalog/ozon/processed/ozon_catalog.csv` - локальный Ozon-каталог.
- Ozon Seller API `POST /v4/product/info/attributes` - read-only карточные
  attributes: описание, бренд, тип, модель, размер, фото и характеристики.
- Ozon product info snapshot:
  `data/catalog/ozon/raw/<date>/<run_id>/ozon_product_info.json` - цена,
  остатки, статус, изображения и служебные поля.

## Источники WB

WB-аудит делать после свежего прохода WB parser по top-30 запросам.

Базовые источники:

- WB top queries из `search_queries_shevron_*`;
- WB parser positions через Parser Data API dataset `wb`;
- `data/catalog/wb/processed/wb_catalog.csv`;
- WB Content API `POST /content/v2/get/cards/list` - read-only карточки:
  название, описание, фото, характеристики, размеры, даты обновления;
- WB prices API `GET /api/v2/list/goods/filter` - текущие цены и скидки;
- WB statistics API `GET /api/v1/supplier/stocks`,
  `GET /api/v1/supplier/orders`, `GET /api/v1/supplier/sales` - остатки,
  заказы и продажи за выбранный период;
- WB Feedbacks/Questions API `GET /api/v1/feedbacks`,
  `GET /api/v1/questions` - история отзывов, оценок и вопросов;
- последний read-only WB promotion report, если он доступен и период/смысл
  метрик явно указаны.

## Методика

1. Сопоставить запросы со спросом:
   - популярность;
   - добавления в корзину;
   - заказы;
   - GMV;
   - конверсия в заказ;
   - период.
2. Сопоставить parser-позиции:
   - лучшая позиция магазина;
   - количество карточек магазина в выдаче;
   - top-10, top-30, top-100;
   - запросы без подтвержденной позиции.
3. Получить карточный контент:
   - название;
   - описание;
   - характеристики;
   - тип/модель/размер;
   - изображения;
   - статус;
   - остаток.
4. Разложить запросы и карточки по кластерам:
   - `липучка`;
   - `кепка`;
   - `позывной/именной`;
   - `бпла/дроны`;
   - `фсб`;
   - `мвд/полиция`;
   - `росгвардия`;
   - `вдв`;
   - `фсин/уис`;
   - `россия/флаг`;
   - `тактические/сво`;
   - другие подтвержденные кластеры спроса.
5. Для выбора целевых запросов карточки использовать кластеры из названия.
   Описание использовать только как диагностический слой: в Ozon-описаниях
   может быть универсальный текст про кепку, рюкзак, одежду и аксессуары, и он
   не должен автоматически превращать карточку в целевую под все эти запросы.
   Для WB действует то же правило: title-clusters являются основой SEO
   matching, description-clusters - только диагностикой.
6. Сформировать:
   - `query_opportunities` - приоритеты по запросам;
   - `card_audit` - карточки на review;
   - `cluster_summary` - спрос и покрытие по кластерам;
   - `cannibalization` - запросы, где много карточек магазина конкурируют друг
     с другом.

## Приоритеты

Высокий приоритет:

- карточка имеет остаток;
- позиция примерно `11-60` по релевантному запросу;
- запрос имеет подтвержденный спрос;
- карточка близка к top-30 или top-10;
- изменение может дать эффект без массовой перестройки каталога.

Средний приоритет:

- позиция `61-100`;
- карточка не найдена parser-ом, но название явно покрывает кластер спроса;
- запрос пока не проверен parser-ом, но есть спрос в Ozon/WB top queries.

Низкий приоритет:

- нет остатка;
- карточка не продается;
- запрос нерелевантен карточке;
- нет подтвержденного спроса;
- карточка глубже top-100 и нет близкого органического сигнала.

## Ограничения

- Нельзя оценивать будущие продажи только по количеству запросов без CTR,
  конверсии карточки, цены, рекламы, остатков и фактических заказов.
- Нельзя механически добавлять нерелевантные ключи в название.
- Если parser-позиции есть только по части запросов, остальные запросы
  помечать как `check_parser`, а не как подтвержденную SEO-проблему.
- Для запросов, где найдено много карточек магазина, сначала выбрать 1-3
  целевые карточки. Массовое продвижение всех карточек по одному запросу
  создает внутреннюю конкуренцию.
- В WB SEO-аудите нельзя смешивать разные источники оценок:
  - parser SERP rating - видимый рейтинг в поисковой выдаче parser-а;
  - Feedbacks API valuation - оценки из истории отзывов;
  - официальный рейтинг карточки - отдельный источник, если он подтвержден.
  Если официальный рейтинг карточки недоступен или endpoint не подтвердился,
  это нужно писать как ограничение отчета.

## Артефакты

Для Ozon SEO-аудита сохранять в:

```text
data/runs/<date>/ozon_seo_audit_<timestamp>/
```

Минимальные файлы:

- `summary.json`;
- `ozon_seo_audit_report.md`;
- `ozon_seo_query_opportunities.csv`;
- `ozon_seo_card_audit.csv`;
- `ozon_seo_cluster_summary.csv`;
- `ozon_seo_cannibalization.csv`.

Если read-only attributes API использовался успешно, можно сохранять
`ozon_product_attributes.json` как runtime-артефакт. Он не должен попадать в
git.

Для WB SEO-аудита сохранять в:

```text
data/runs/<date>/wb_full_seo_audit_<timestamp>/
```

Минимальные файлы:

- `summary.json`;
- `wb_full_seo_audit_report.md`;
- `processed/wb_full_seo_card_audit.csv`;
- `processed/wb_full_seo_query_opportunities.csv`;
- `processed/wb_full_seo_cluster_summary.csv`;
- `processed/wb_full_seo_cannibalization.csv`;
- `processed/wb_seo_quick_wins.csv`;
- `processed/wb_seo_content_problems.csv`;
- `processed/wb_seo_stock_blocked.csv`;
- `processed/wb_seo_ads_problems.csv`.

Если использовались read-only API, runtime raw snapshots можно сохранять в
`raw/` внутри run-директории. Raw parser dataset не копировать в постоянный
отчет; сохранять только metadata parser-среза и производные таблицы.
