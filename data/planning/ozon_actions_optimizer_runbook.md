# Ozon Actions Optimizer Runbook

## Итог

`plan-ozon-actions-optimizer` / `apply-ozon-actions-optimizer` - отдельный
контур `Ozon все акции`. Он не заменяет и не смешивается с контуром
`Ozon Elastic`.

Цель dry-run - сравнить `Эластичный бустинг` и остальные доступные Ozon акции
по каждому товару и выбрать бизнес-оптимальное действие: оставить текущую
акцию, добавить товар, обновить цену, подготовить переключение или пропустить.

`plan-ozon-actions-optimizer` ничего не меняет в Ozon. `apply` разрешен только
после owner approval конкретного dry-run.

## Два Ozon-контура

- `Ozon Elastic` / `/elastic` - старый стабильный контур только для
  `Эластичного бустинга`.
- `Ozon все акции` / `/ozon-actions` - новый контур, который сравнивает
  Elastic и все доступные акции Ozon.

Кнопки и apply-контуры отдельные:

- `oe_apply:<plan_run_id>` применяет только Ozon Elastic.
- `oza_apply:<plan_run_id>` применяет только Ozon all-actions optimizer.

## Когда использовать

Использовать, когда нужно сравнить `Эластичный бустинг`, `Супербустинг` и
другие Ozon акции между собой, а не считать только один заранее выбранный
`action_id`.

Не использовать как apply. Переключение товара между акциями, изменение цены
акции или добавление в акцию - опасные операции и требуют отдельного
owner-approved apply-контура.

## Команда

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python -m seller_agent.cli plan-ozon-actions-optimizer
```

Опционально:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python -m seller_agent.cli plan-ozon-actions-optimizer \
  --run-id <stable_run_id>
```

Если уже есть сохраненный LK snapshot с числовым бустингом обычных акций,
передать его явно:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python -m seller_agent.cli plan-ozon-actions-optimizer \
  --lk-boost-summary-json data/runs/YYYY-MM-DD/ozon_actions_boost_probe_<timestamp>/processed/boost_source_summary.json
```

Если флаг не указан, команда пытается найти последний локальный
`boost_source_summary.json` в `data/runs/*/ozon_actions_boost_probe_*/`.
Это read-only источник: он нужен только для подтверждения процента бустинга,
а не для write-действий.

Артефакты сохраняются в:

```text
data/runs/<date>/ozon_actions_optimizer_plan_<timestamp>/
```

Apply-команда:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python -m seller_agent.cli apply-ozon-actions-optimizer \
  --plan-run-id <ozon_actions_optimizer_plan_...> \
  --confirmed-by-user
```

Через Telegram:

```text
/ozon-actions
```

Если dry-run содержит строки `add/update/switch`, бот показывает кнопку
`Применить Ozon все акции`. Нажатие кнопки является owner approval только для
показанного `plan_run_id`.

## Источники данных

Команда читает Ozon Seller API:

- `GET /v1/actions` - список доступных акций;
- `POST /v1/actions/products` - товары, уже участвующие в акции;
- `POST /v1/actions/candidates` - кандидаты на участие в акции;
- `POST /v1/actions/products/activate` - write-метод для добавления товара в
  акцию или обновления action price в целевой акции; использовать только в
  apply-контуре после approval;
- `POST /v1/actions/products/deactivate` - write-метод для удаления товара из
  исходной акции при `switch`; использовать только в apply-контуре после
  approval;
- `POST /v5/product/info/prices` через adapter `fetch_product_info_prices` -
  `price`, `old_price`, `min_price`;
- `POST /v3/product/info/list` через adapter `fetch_product_info` - название
  и `offer_id`, если их не хватило в строках акций;
- `POST /v4/product/info/stocks` через adapter `fetch_product_stocks` -
  FBO-остаток.

Актуальный локальный источник API-документации:

```text
data/reference/api_docs/ozon/openapi/seller_swagger_20260629.json
```

Дополнительный read-only источник из ЛК для связи `action price -> boost`
проверен через CDP Vital Shevron 2026-07-05:

- `GET /api/site/seller-actions/v1/seller-actions/<action_id>` - detail
  акции. Для части `STOCK_DISCOUNT` акций в `action.description` указан
  фиксированный акционный бустинг, например `55%` или `50%`.
- `GET /api/site/global-seller-products/v1/action/<action_id>/products/active?offset=0&limit=20`
  - товары, уже участвующие в акции. Поля: `actionPrice`,
  `maxDiscountPrice`, `priceReferenceForBoosting`, `boostingInSearch`.
- `GET /api/site/global-seller-products/v1/action/<action_id>/products/candidate?offset=0&limit=20`
  - кандидаты для добавления в акцию. Для `STOCK_DISCOUNT` у кандидатов
  `boostingInSearch` может приходить как `0`, поэтому процент бустинга нужно
  брать из detail-описания акции, если он указан числом.
- `POST /api/site/action-explanation-api/v1/intersections-by-skus` - текущие
  пересечения акций по SKU и labels. Использовать как вспомогательный источник
  для конфликтов/текущего участия, не как основной источник бустинга.

Подтвержденный probe:

```text
data/runs/2026-07-05/ozon_actions_boost_probe_20260705T060551/
```

Краткий отчет:

```text
data/runs/2026-07-05/ozon_actions_boost_probe_20260705T060551/ozon_actions_boost_source_probe_report.md
```

## Логика расчета

Для каждой строки `action_id + product_id` команда определяет:

- `current_action_price` - текущую цену товара в акции из строки Ozon, если
  товар уже участвует в акции;
- для активного `Эластичного бустинга` целевая пара берется как
  `action_price -> current_boost`;
- для кандидата в `Эластичный бустинг` целевая пара берется как
  `price_max_elastic -> max_boost`, потому что по Ozon Swagger
  `price_max_elastic` - цена для максимального размера бустинга, а
  `price_min_elastic` - цена для минимального размера бустинга;
- для остальных акций `target_action_price` берется из полей Ozon в порядке
  приоритета: `max_action_price`, `price_max_elastic`, `action_price`,
  `price_min_elastic`, `alert_max_action_price`;
- для `STOCK_DISCOUNT`, если LK detail-описание акции содержит числовой
  акционный бустинг, товарная пара считается как
  `candidate.maxDiscountPrice -> action.description boost percent` для
  кандидатов и `active.actionPrice -> action.description boost percent` для
  активных товаров;
- `boost_score`, `boost_source`, `boost_known` и `boost_note`: рекомендация
  считается валидной только если бустинг подтвержден для выбранной цены;
- `min_price` из `/v5/product/info/prices`;
- FBO-остаток из `/v4/product/info/stocks`;
- `price_loss` как `seller_price - target_action_price`, если seller price
  доступна.

Строка блокируется, если:

- нет положительного FBO-остатка;
- отсутствует `min_price`;
- отсутствует action price;
- action price ниже `min_price`.
- отсутствует подтвержденный бустинг для выбранной action price.

Из валидных предложений по одному товару выбирается лучшее по согласованной
бизнес-логике:

1. новая акция должна иметь цену не ниже `min_price`, FBO-остаток и
   подтвержденный бустинг;
2. если товар уже участвует в валидной акции, новая акция выбирается только
   когда дает больший бустинг, либо сопоставимый бустинг при цене не ниже
   текущей;
3. при сравнении валидных предложений сортировка: выше `boost_score`, затем
   выше `target_action_price`, затем ниже потеря цены относительно seller
   price;
4. варианты с меньшей ценой без улучшения бустинга не применяются молча и
   должны оставаться в review/test, а не в автоматическом apply.

## Статусы рекомендаций

- `add_to_best_action` - товар не участвует в акции, найдено лучшее валидное
  предложение;
- `keep_current_action` - текущая активная акция уже лучшая;
- `update_current_action_price` - текущая активная акция лучшая, но action
  price отличается от целевой;
- `switch_to_better_action_review` - товар уже участвует в акции, но другая
  акция выглядит лучше; это только review, apply отдельно;
- `skip` - валидных предложений нет.

## Артефакты

- `ozon_actions_optimizer_report.md` - короткий отчет;
- `ozon_actions_optimizer.xlsx` - workbook с вкладками `recommendations` и
  `offers`;
- `ozon_actions_optimizer_recommendations.csv` - итоговые рекомендации;
- `ozon_actions_optimizer_offers.csv` - все предложения по всем акциям;
- `ozon_actions_optimizer_payload_preview.json` - предварительный payload
  только для review, не для apply;
- `processed/lk_boost_sources.json` - безопасная выжимка числовых процентов
  бустинга из LK snapshot по `action_id`;
- `processed/*.json` - безопасные обработанные snapshots;
- `raw/*.json` - сырые API-ответы без секретов.

## Safety

Команда не вызывает write API и не должна иметь флаг apply.

Apply-контур выполняет:

```text
read-only -> dry-run -> review -> approved -> fresh dry-run -> drift-check -> apply -> verify -> result
```

Особенности apply:

- берет только строки `add_to_best_action`, `update_current_action_price` и
  `switch_to_better_action_review` из owner-approved dry-run;
- перед записью строит fresh dry-run и применяет только строки, которые
  совпали по `recommended_action + product_id + offer_id + action_id +
  target_action_price + current_active_action_id`;
- строки с drift пропускает и выносит в отчет;
- для `switch_to_better_action_review` сначала вызывает
  `/v1/actions/products/deactivate` по исходной акции, затем
  `/v1/actions/products/activate` по целевой акции;
- verify перечитывает active products по затронутым акциям и проверяет
  membership/action price.

## Ограничения

- Если Ozon не отдает структурированный бустинг по акции/товару, команда не
  должна выдумывать его. Допустим только явный процент из названия акции с
  `буст/boost`, и это нужно считать менее надежным источником. Если в Ozon API
  для `STOCK_DISCOUNT` пришли нули в `current_boost/min_boost/max_boost`, такую
  акцию нельзя сравнивать с Elastic как `0%`: ее нужно пометить
  `missing_confirmed_boost_at_action_price` и добрать источник бустинга из ЛК
  или другого documented API.
- После probe 2026-07-05 для части `STOCK_DISCOUNT` источник найден в
  `action.description` ЛК. Если в описании есть фраза вида
  `акционный бустинг ... 55%`, этот процент можно использовать как
  action-level boost. Если описание говорит только `Бустинг акционных товаров`
  без числа, бустинг остается неподтвержденным.
- FBO-остаток на первом этапе берется из `/v4/product/info/stocks`. Если по
  факту Ozon начнет отдавать неполную FBO-картину, заменить источник на
  более точный documented endpoint и обновить этот runbook.
- Команда не учитывает продажи, маржу, CPC и изменение конверсии. Это
  оптимизатор участия в акциях по условиям акции, а не финальная оценка
  прибыльности.

## Отдельный owner-override для распродажи залежавшихся остатков

Подтверждено владельцем 2026-07-30: для явно выделенных групп залежавшегося
ассортимента может быть согласован режим абсолютного максимального бустинга
без ограничения `min_price`.

Этот режим не меняет стандартный оптимизатор и не разрешает ему пропускать
проверку `target_action_price >= min_price`. Он оформляется только отдельным
checksummed dry-run, где по каждой строке явно показаны:

- owner-approved группа;
- `price_max_elastic -> max_boost`;
- текущий `min_price` и величина снижения ниже него;
- отключение `min_price_for_auto_actions_enabled`;
- консервативная экономика до CPC;
- точная CPC-ставка и hard stop;
- срок распродажи и окончательное решение по остатку.

Свежий probe Vital Shevron от 2026-07-30 подтвердил:

- Elastic: максимальный бустинг `75%` по товарному
  `price_max_elastic`;
- акции `Максимальный бустинг`: фиксированный бустинг `55%` из
  `action.description`;
- при выборе абсолютного максимума сначала сравнивается подтверждённый
  процент бустинга, затем более высокая цена при одинаковом проценте.

Первый review-пакет:

```text
data/runs/2026-07-30/ozon_dormant_reset_plan_20260730T0908/
```

Apply из обычного `apply-ozon-actions-optimizer` для такого пакета запрещён:
он не управляет owner override минимальной цены и CPC как одной зависимой
операцией. Нужен отдельный apply-контур:

```text
checksum -> API-only preflight -> fresh prices/actions/CPC ->
full-scope drift-check -> disable min-price accounting for approved rows ->
verify -> action activate/update -> verify -> CPC update/add -> verify ->
schedule controls
```

Если любой этап verify не подтверждён, последующие write-этапы не выполнять.
