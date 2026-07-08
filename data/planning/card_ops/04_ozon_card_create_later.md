# 04. Создание карточки на Ozon

Дата: 2026-07-02

## Итог

Цель операции: создать новую карточку Ozon для товара, который уже есть на WB,
прошел аудит, согласован владельцем в HTML и сохранен в Layer 3 master
passport.

Статус автоматизации: штатный маршрут готов и подключен к batch-команде
`apply-approved-cards`.

Готовые команды:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli plan-ozon-card-create \
  --internal-sku <internal_sku> \
  --min-price <min_price>
```

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli apply-ozon-card-create \
  --plan-run-id <plan_run_id> \
  --confirmed-by-user
```

Источник цены в `plan-ozon-card-create`:

1. точная Ozon-цена из `data/pricing/pricing_status.json`;
2. если ее нет - наиболее частая Ozon-цена существующих товаров той же
   внутренней SKU-группы, например `chev_pz_ng_text*`;
3. если и ее нет - WB fallback только по явному флагу.

Любой fallback-источник цены оставляет `manual_review_items > 0`, потому что
цена новой Ozon-карточки не была прямо подтверждена в marketplace state этой
карточки.

Минимальная цена Ozon обязательна для штатного создания карточки. Ее нужно
передавать в dry-run явно:

```bash
--min-price 400
```

Технически `min_price` не входит в payload `/v3/product/import`, поэтому штатный
apply выполняет два write-шага:

1. `POST /v3/product/import` - создать карточку с `price` и `old_price`;
2. после появления `offer_id` в Ozon отправить
   `POST /v1/product/import/prices` с `price`, `old_price`, `min_price`,
   `currency_code=RUB`, `min_price_for_auto_actions_enabled=true`.

Verify по ценам выполняется через `POST /v5/product/info/prices` по
`offer_id`.

Если нет Ozon-цены и группового Ozon-шаблона, dry-run можно собрать с явным
fallback от WB-цен:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli plan-ozon-card-create \
  --internal-sku <internal_sku> \
  --min-price <min_price> \
  --allow-wb-price-fallback
```

Такой план получает `manual_review_items > 0`. Apply с ним разрешен только если
владелец явно согласовал источник цены:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli apply-ozon-card-create \
  --plan-run-id <plan_run_id> \
  --confirmed-by-user \
  --allow-manual-review
```

## Источники

- Owner-approved HTML / Telegram approval.
- Layer 3 passport:

```text
data/catalog/master_passport/approved/<internal_sku>.json
```

- Карточный стандарт:

```text
data/planning/ozon_product_card_content_runbook.md
data/planning/product_card_editor_field_map_runbook.md
```

## Safety

Создание карточки Ozon - опасная write-операция. Обязательная цепочка:

```text
read-only -> dry-run -> review -> approved -> apply -> verify -> result
```

HTML является approval-пакетом только если там явно показано:

- создание новой Ozon-карточки;
- будущий `offer_id` / internal SKU;
- `title`;
- `description`;
- Ozon category/type;
- габариты, вес, фото;
- цена/старая цена или отдельное согласование fallback-цены;
- атрибуты Ozon, хештеги и группировка, если применяются.

## API-маршрут

Подтвержденный базовый метод Ozon Seller API:

```text
POST /v3/product/import
POST /v1/product/import/info
POST /v1/product/import/prices
POST /v5/product/info/prices
```

Для шевронов Vital Shevron сейчас используется:

```text
description_category_id=17028963
type_id=970886657
```

Перед plan штатная команда проверяет:

- owner-approved Layer 3 passport;
- что `ozon_offer_id` / `ozon_product_id` еще не заполнены;
- что `offer_id=<internal_sku>` еще не существует на Ozon;
- название, описание, цвет, название цвета, модель, размер, упаковку, вес;
- фото из `media.target_assets`;
- цену и старую цену;
- минимальную цену для Ozon;
- Ozon schema/dictionary значения, если API доступен.

Обязательные технические атрибуты для текущей категории шевронов:

- `23536` / `Нужен код маркировки` = `false`. На текущий момент по всем
  изделиям Vital Shevron маркировка не требуется. Если не передать это поле,
  Ozon может импортировать карточку с `product_id`, но оставить ее с ошибкой
  валидации `error_attribute_values_empty`.

Подтвержденная внештатная ситуация 2026-07-02:

- при проверке будущего `offer_id=<internal_sku>` метод
  `/v4/product/info/attributes` может вернуть HTTP `404` с текстом
  `item not found`;
- для Ozon-create это нормальный результат, означающий, что карточки с таким
  `offer_id` еще нет;
- планировщик должен записать warning
  `ozon_offer_id_not_found_ok_for_create`, а не падать.

Подтвержденные внештатные ситуации apply 2026-07-02:

- `/v3/product/import` асинхронный. Сразу после `status=imported` фильтр
  `/v4/product/info/attributes` по `offer_id` может временно вернуть HTTP
  `404 item not found`. Apply/verify должен продолжать polling или проверять
  по `product_id`, а не повторять create вслепую.
- Если Ozon вернул product_id, но ошибка карточки указывает на обязательный
  атрибут `23536` / `Нужен код маркировки`, нужно повторно отправить полный
  `/v3/product/import` для тех же `offer_id` с `23536=false`, затем проверить
  `/v1/product/import/info` и `/v3/product/info/list`.
- `POST /v1/product/import/prices` может вернуть `updated=true`, но
  `min_price` появляется в `/v5/product/info/prices` с задержкой. Проверять
  повторно; если после задержки `min_price` пустой/0 только у отдельных строк,
  повторить `/v1/product/import/prices` точечно по ним.
- Для `chev_pz_ng_text0056` Ozon отклонил название
  `Шеврон на липучке позывной Шкет нагрудный мох`; рабочий вариант:
  `Шеврон на липучке с позывным Шкет мох`.
- При массовом создании/восстановлении карточек проверка
  `/v4/product/info/attributes` и наличие `product_id` недостаточны для
  финального статуса. После apply обязательно проверять
  `/v3/product/info/list`: `statuses.is_created`, `status_description`,
  `status_failed`, `validation_status`, `status_tooltip` и `errors`.
  `status_description=На модерации` без ошибок считается ожидающим состоянием,
  а не финальным успехом; его нужно вынести в follow-up контроль.
- Если `/v1/product/import/prices` сразу после create вернул `NOT_FOUND` или
  `min_price=0` в verify, но карточка уже появилась по `offer_id`, повторить
  отправку того же price payload после короткой задержки и снова проверить
  `/v5/product/info/prices`. В кейсе `pz_ng_olive` повторная отправка
  подтвердила `min_price=400` для 31/31 карточек.
- Для новых Ozon-карточек с `DESCRIPTION_DECLINE` по названию позывного
  использовать актуальную последовательность владельца: сначала формат без
  кавычек и без запятых
  `Шеврон на липучке позывной X нагрудный олива`; если Ozon отклоняет его,
  fallback без кавычек, но с запятыми
  `Шеврон на липучке позывной X, нагрудный, олива`. Кавычки не использовать
  без отдельного решения владельца. Менять только Ozon-title/атрибут `4180`,
  если WB и общий паспорт уже согласованы в другом формате.
- Ошибка `FB_UNWANTED` является policy-блокером. Не архивировать карточку и не
  маскировать название без отдельного решения владельца, потому что это может
  изменить смысл товара. Зафиксировать SKU, product_id, текст ошибки и вынести
  в ручной backlog.

Подтвержденное правило словарей 2026-07-02:

- Ozon value-search по справочнику может не найти `dictionary_value_id` для
  уже используемых значений `VitalEmb`, `Габардин`, `5810999000 ...`;
- перед блокировкой нужно добрать `dictionary_value_id` из локального
  `data/catalog/content/ozon_card_content.json` по карточкам той же
  `description_category_id/type_id`;
- для текущей категории подтверждены значения из существующих карточек:
  `VitalEmb=972753462`, `Габардин=61801`,
  `5810999000 - Прочие вышивки из прочих текстильных материалов=971398384`.

## Быстрый порядок

1. Проверить preflight:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli status-preflight
```

2. Собрать dry-run по конкретным SKU:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli plan-ozon-card-create \
  --internal-sku <internal_sku_1> \
  --internal-sku <internal_sku_2> \
  --min-price <min_price>
```

3. Проверить в отчете:

```text
data/runs/<date>/<run_id>/ozon_card_create_dry_run.md
data/runs/<date>/<run_id>/ozon_card_create_plan.json
data/runs/<date>/<run_id>/ozon_product_import_payload_draft.json
data/runs/<date>/<run_id>/ozon_product_import_prices_payload_draft.json
```

4. Если строка заблокирована, не применять. Исправить паспорт/цену/фото и
   пересобрать plan.

5. Если есть `manual_review_items` из-за Ozon group price template или WB
   price fallback, применять только после отдельного согласования владельца.

6. Apply:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli apply-ozon-card-create \
  --plan-run-id <plan_run_id> \
  --confirmed-by-user
```

Batch-вариант после owner approval пачки:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli apply-approved-cards \
  --internal-sku <internal_sku_1> \
  --internal-sku <internal_sku_2> \
  --ozon-create-min-price <min_price> \
  --confirmed-by-user
```

Если в пачке есть WB-only карточки и `--ozon-create-min-price` не передан,
стадия Ozon-create будет заблокирована отдельно с причиной
`ozon_create_min_price_required`. Остальные готовые стадии пачки могут быть
выполнены и будут отражены в общем отчете.

7. Verify:

- `/v1/product/import/info` вернул результат по `task_id`;
- новая карточка найдена через `/v4/product/info/attributes` по
  `offer_id=<internal_sku>`;
- цена, старая цена и минимальная цена проверены через
  `/v5/product/info/prices`;
- в Layer 3 passport записаны `ozon_offer_id`, `ozon_product_id`/`ozon_sku`,
  если они вернулись API;
- создан saved run/report без секретов.

8. После серии созданий отдельной задачей выполнить полный refresh:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli fetch-card-content

PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli build-content-master
```

## Ограничения текущей реализации

- Команда не проставляет остатки и не создает поставку. Это отдельный контур.
- Цены берутся из `data/pricing/pricing_status.json`: сначала точный Ozon
  price, затем Ozon-шаблон той же SKU-группы, затем WB fallback только по
  явному флагу и manual review. Минимальная цена передается отдельным
  параметром `--min-price`.
- Группировка Ozon через отдельные поля должна быть видна в паспорте; массовое
  merge/split группировок остается отдельной опасной операцией.
- Если Ozon вернет ошибку модерации или reject словаря, нужно добавить
  внештатную ситуацию в эту инструкцию после успешного восстановления.
