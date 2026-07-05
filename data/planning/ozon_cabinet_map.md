# Ozon Cabinet Map

Дата актуализации: 2026-06-18.

## Назначение

Карта ЛК Ozon нужна fresh-агенту без контекста: по поставленной задаче он
должен быстро понять, в какой раздел ЛК идти, что там лежит, какие ссылки уже
известны, какие действия опасны и где сначала искать API-метод.

Карту обновлять в том же рабочем цикле, если найден новый раздел, вкладка,
ссылка, API-метод, paywall, ограничение, ошибка или способ восстановления.

## Источники и статус проверки

Проверено 2026-06-13 в Vital Shevron:

- `status-preflight`: Ozon Seller API `ok`, Performance API `ok`, CDP `ok`;
- Ozon CDP: `127.0.0.1:9544`;
- магазин-marker: `Vital Shevron`;
- открывались dashboard, analytics, products, prices;
- из текущего dashboard DOM подтверждены верхние пункты меню:
  `Главная`, `Товары`, `Цены и акции`, `FBO`, `FBS`, `Финансы`, `Аналитика`,
  `Покупатели`, `Продвижение`, `Банк`.

Дополнительные источники:

- `data/planning/lk_connection_runbook.md`;
- `data/planning/session_manager_runbook.md`;
- `data/planning/pricing_runbook.md`;
- `data/planning/search_queries_runbook.md`;
- `data/planning/ozon_elastic_runbook.md`;
- `data/planning/ozon_cpc_efficiency_runbook.md`;
- `data/planning/ozon_messenger_runbook.md`;
- read-only опыт TAKTERRA:
  `/home/pavel/projects/seller_takterra/data/planning/ozon_cabinet_map.md`.

Ограничение: не все внутренние вкладки Ozon были заново прокликаны 2026-06-13.
Если свежая задача требует write-операции или финансового решения, агент обязан
открыть нужный раздел заново, подтвердить актуальность UI/API и зафиксировать
отличия в этой карте.

## Safety

Разрешено без approval:

- открыть раздел;
- прочитать данные;
- сделать screenshot/DOM summary без секретов;
- выгрузить read-only отчет, если выгрузка не меняет настройки;
- подготовить dry-run.

Запрещено без явного approval владельца:

- менять цены, минимальные цены, акции, скидки;
- менять рекламные ставки, бюджеты, статусы и состав кампаний;
- отвечать покупателям;
- редактировать карточки, фото, остатки, поставки;
- менять доступы, API-ключи, настройки магазина;
- подтверждать финансовые или операционные действия.

Для опасных операций обязательна цепочка:

```text
read-only -> dry-run -> review -> approved -> apply -> verify -> result
```

## Подключение к ЛК

Рабочий контур Vital Shevron:

```text
OZON_REMOTE_DEBUGGING_PORT=9544
OZON_CDP_URL=http://127.0.0.1:9544
OZON_EXPECTED_STORE=Vital Shevron
.sessions/ozon/chrome-profile/
.sessions/ozon/ozon_seller_storage_state.json
```

Проверка:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli sessions status

PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli status-preflight
```

Не выводить cookies, storage state, auth headers, API tokens и коды входа.

## Быстрый маршрутизатор задач

| Задача | Куда идти в ЛК | Основной URL | Сначала проверить API |
| --- | --- | --- | --- |
| Проверить вход, магазин, общую сводку | `Главная` | `https://seller.ozon.ru/app/dashboard/main` | Нет, это LK health-check |
| Список товаров, карточки, статусы карточек | `Товары` | `https://seller.ozon.ru/app/products` | Seller API products |
| Создание/редактирование карточки и карточный контент | `Товары -> Список товаров -> карточка товара` | `https://seller.ozon.ru/app/products` | `product/info/attributes`, `product/info/description`, `product/pictures/info`, `description-category/attribute`; инструкция: `ozon_product_card_content_runbook.md` |
| Цены, скидочные цены, минимальные цены | `Цены и акции -> Цены на товары` | `https://seller.ozon.ru/app/prices/control` | Seller API prices |
| Акции Ozon / участие товаров в акциях | `Цены и акции -> Акции` | `https://seller.ozon.ru/app/highlights/list` | Проверить доступные Seller API методы |
| Заявки на скидку | `Цены и акции -> Заявки на скидку` | `https://seller.ozon.ru/app/prices/discount-requests` | Проверить API, если задача массовая |
| FBO остатки и доступность | `FBO` | `https://seller.ozon.ru/app/fbo-stocks/stocks-management` | Seller API stocks |
| FBO доступность товаров | `FBO` | `https://seller.ozon.ru/app/analytics/supply/goods-availability/index` | Seller API stocks / analytics |
| FBS заказы | `FBS` | `https://seller.ozon.ru/app/postings/fbs` | Не основной режим Vital; уточнить задачу |
| Баланс и выплаты | `Финансы -> Баланс` | `https://seller.ozon.ru/app/finances/balance` | Finance API, если подключен |
| Юнит-экономика / начисления | `Финансы -> Начисления` | `https://seller.ozon.ru/app/finances/accruals?tab=UNIT_ECONOMY` | Finance/API отчеты, если доступны |
| Продажи, графики, динамика | `Аналитика` | `https://seller.ozon.ru/app/analytics/graphs` | Seller API analytics, если доступен |
| Поисковые запросы Ozon | `Аналитика -> Что продавать -> Поисковые запросы` | `https://seller.ozon.ru/app/analytics/what-to-sell/all-queries` | Search queries API может требовать Premium Pro |
| Позиция в категории | `Аналитика -> Что продавать -> Конкурентная позиция` | `https://seller.ozon.ru/app/analytics/what-to-sell/competitive-position` | Обычно LK |
| Отзывы | `Отзывы` | `https://seller.ozon.ru/app/reviews` | Review API; при `403` использовать LK/CDP fallback |
| Вопросы покупателей | `Отзывы -> Вопросы` | `https://seller.ozon.ru/app/reviews/questions` | Review/Questions API, если доступен |
| Уведомления и сообщения Ozon | `Сообщения` | `https://seller.ozon.ru/app/messenger?group=customers_v2` | Ozon Seller API `/v3/chat/list`, `/v3/chat/history`; детали: `ozon_messenger_runbook.md`; LK websocket fallback |
| Поддержка Ozon | `Сообщения -> Поддержка` | `https://seller.ozon.ru/app/messenger?group=support_v2` | LK |
| Продвижение общий вход | `Продвижение` | `https://seller.ozon.ru/app/promotion-info` | Performance API |
| CPC `Оплата за клик` | `Продвижение -> Трафареты / Товарная реклама` | `https://seller.ozon.ru/app/advertisement/product/cpc` | Performance API campaigns/products |
| Покупатели, сегменты, рассылки | `Покупатели` | `https://seller.ozon.ru/app/crm/customer-segments` | CRM API не считать подтвержденным |
| Возвраты | `Возвраты` | `https://seller.ozon.ru/app/returns/main?tab=40` | Seller API returns/postings |
| Сотрудники и доступы | `Настройки -> Сотрудники` | `https://seller.ozon.ru/app/settings/employees` | Критичный раздел, read-only |
| Seller API keys | `Настройки -> API-ключи` | `https://seller.ozon.ru/app/settings/api-keys` | Секреты не выводить |
| Performance API | `Настройки -> Performance API` | `https://seller.ozon.ru/app/settings/performance-api` | Секреты не выводить |
| Premium/Premium Plus | `Подписки` | `https://seller.ozon.ru/app/subscriptions/premium` | Нужен для части аналитики |
| Ozon Банк | `Банк` | `https://seller.ozon.ru/app/fintech/bank` | Финансовый раздел |

## Верхнее меню

| Вкладка | URL | Что лежит | Когда идти | Риск |
| --- | --- | --- | --- | --- |
| Главная | `/app/dashboard/main` | Сводка заказов, продаж, задач, сообщений, рекомендаций, быстрые ссылки на акции, отзывы, продвижение, баланс | Health-check ЛК, быстрый обзор магазина, входная точка fresh-агента | Read-only |
| Товары | `/app/products` | Список товаров, карточки, статусы, фильтры, переходы к редактированию | Каталог, карточки, сверка SKU/product_id/sku, статусы публикации | Write-риск карточек |
| Цены и акции | `/app/prices/control` | Цены, скидки, минимальные цены, акции, заявки на скидку | Все задачи по цене, марже, акциям, min_price | Финансовый риск |
| FBO | `/app/analytics/supply/goods-availability/index` | Доступность товаров, поставки/остатки FBO | Vital Shevron продает Ozon через FBO; проверка доступности перед рекламой и ценами | Операционный риск |
| FBS | `/app/postings/fbs` | FBS-заказы и операции | Не основной режим Vital; использовать только если владелец явно говорит про FBS | Операционный риск |
| Финансы | `/app/finances/balance` | Баланс, выплаты, начисления, юнит-экономика, отчеты | Маржинальность, взаиморасчеты, сверка расходов/комиссий | Read-only/финансовый |
| Аналитика | `/app/analytics` | Графики, спрос, поисковые запросы, категории, конкуренты | Отчеты, SEO, спрос, сравнение с WB, ассортимент | Read-only |
| Покупатели | `/app/crm/customer-segments` | Сегменты, рассылки, CRM | Повторные продажи, рассылки, аудитории | Write-риск рассылок |
| Продвижение | `/app/promotion-info` | Реклама, CPC, товарное продвижение, отчеты | CPC efficiency, ставки, бюджеты | Финансовый риск |
| Банк | `/app/fintech/bank` | Банковские продукты Ozon | Обычно не нужен агенту для маркетплейс-операций | Финансовый/критичный |

## Главная

```text
https://seller.ozon.ru/app/dashboard/main
```

Что смотреть:

- подтверждение, что открыт магазин `Vital Shevron`;
- нет ли `registration/signin`, блокировки, captcha, no-connection;
- карточки задач: отзывы, сообщения, возвраты, акции, продвижение;
- быстрые ссылки на аналитику, финансы, рекламу;
- общие показатели заказов и продаж.

Fresh-агенту:

- начинать отсюда при любой LK-задаче Ozon;
- не считать цифры dashboard бухгалтерским источником истины без отдельного
  отчета/API;
- если открыт `?__rr=1`, это не автоматическая блокировка. Блокировку
  подтверждать только по текстам ошибок или отсутствию оболочки кабинета.

## Товары

```text
https://seller.ozon.ru/app/products
```

Назначение:

- список карточек Ozon;
- статусы публикации и модерации;
- переход к карточке товара;
- сверка `offer_id`, `product_id`, `sku` через ЛК и API;
- массовые операции с карточками, если они доступны в UI.

Когда идти:

- карточка не видна на витрине;
- нужно проверить название, фото, описание, характеристики;
- нужно сопоставить Ozon товар с WB товаром;
- нужно проверить товар перед продвижением или акцией.

API-first:

- `POST /v3/product/list`;
- `POST /v3/product/info/list`;
- `POST /v4/product/info/attributes`.
- `POST /v1/product/info/description`;
- `POST /v2/product/pictures/info`;
- `POST /v1/product/rating-by-sku`;
- `POST /v1/description-category/attribute`;
- `POST /v1/description-category/attribute/values`.

Риски:

- редактирование карточки, фото, описания и характеристик является опасной
  операцией;
- cross-marketplace операции требуют confirmed mapping Ozon/WB;
- унификация seller SKU запрещена без отдельного review и approval.

## Цены и акции

### Цены на товары

```text
https://seller.ozon.ru/app/prices/control
```

Что лежит:

- базовая цена / цена до скидки;
- цена со скидкой / текущая цена продавца;
- цена для покупателя с учетом скидок Ozon;
- минимальная цена;
- настройка учета минимальной цены при автодобавлении в акции;
- предупреждения по цене;
- массовые инструменты работы с ценами.

Для Vital Shevron:

- Ozon продажи считать FBO, если владелец отдельно не сказал другое;
- настройка учета минимальной цены при автодобавлении в акции действует 30
  дней, поэтому нужен регулярный контроль и refresh до деактивации;
- себестоимость хранится не в ЛК как источник истины, а во внутреннем слое
  проекта: `85 ₽` за один шеврон, комплект `N * 85 ₽`.

Где искать настройки минимальной цены:

```text
Цены и акции -> Цены на товары
Цены и акции -> Цены на товары -> клик по цене -> Инструменты цен
Цены и акции -> Обновить цены -> шаблон
```

Важно: минимальная цена не является абсолютным запретом на любую цену ниже нее.
Она работает как порог для автодобавления/автоприменения и стратегии
ценообразования, если соответствующая настройка активна. Ручная акционная цена
или отдельные исключения Ozon могут дать цену ниже минимальной.

API-first:

- read-only: `POST /v5/product/info/prices`;
- read-only таймер учета минимальной цены:
  `POST /v1/product/action/timer/status`;
- write после approval: `POST /v1/product/import/prices`;
- write после approval для продления срока учета минимальной цены:
  `POST /v1/product/action/timer/update`.

### Акции Ozon

```text
https://seller.ozon.ru/app/highlights/list
```

Что лежит:

- акции Ozon;
- текущие и будущие акции;
- товары в акции;
- акционные цены и условия участия;
- быстрые переходы из dashboard по доле продаж в акциях.

Когда идти:

- проверить, почему товар продается дешевле обычной цены;
- собрать список товаров в акции;
- подготовить Elastic/акционный dry-run;
- проверить, какие товары автодобавлены или могут быть добавлены.

Риски:

- участие в акции и акционная цена имеют прямой финансовый эффект;
- нельзя применять изменения без fresh dry-run, review, approval и verify.

Read-only источники:

- Seller API: `GET /v1/actions`, `POST /v1/actions/products`,
  `POST /v1/actions/candidates`;
- LK/CDP `GET /api/site/seller-actions/v1/seller-actions/<action_id>` -
  detail акции, включая `action.description`. Для части `STOCK_DISCOUNT`
  акций в описании есть фиксированный акционный бустинг числом, например
  `55%` или `50%`;
- LK/CDP
  `GET /api/site/global-seller-products/v1/action/<action_id>/products/active?offset=0&limit=20`
  - активные товары акции; поля `actionPrice`, `maxDiscountPrice`,
  `priceReferenceForBoosting`, `boostingInSearch`;
- LK/CDP
  `GET /api/site/global-seller-products/v1/action/<action_id>/products/candidate?offset=0&limit=20`
  - кандидаты на добавление; для `STOCK_DISCOUNT` `boostingInSearch` может
  приходить как `0`, поэтому action-level boost нужно брать из
  `action.description`, если он указан числом;
- LK/CDP `POST /api/site/action-explanation-api/v1/intersections-by-skus` -
  пересечения акций и текущие labels по SKU; это вспомогательный источник
  конфликтов, не основной источник бустинга.

Probe Vital Shevron 2026-07-05:
`data/runs/2026-07-05/ozon_actions_boost_probe_20260705T060551/`.

### Заявки на скидку

```text
https://seller.ozon.ru/app/prices/discount-requests
```

Что лежит:

- заявки покупателей/платформы на скидку;
- решения по скидкам;
- история или текущий список заявок, если доступен.

Риск: финансовый. Любое одобрение/отклонение массовых скидок согласовывать.

## FBO

Основные ссылки:

```text
https://seller.ozon.ru/app/analytics/supply/goods-availability/index
https://seller.ozon.ru/app/fbo-stocks/stocks-management
```

Что лежит:

- доступность товаров на складах Ozon;
- остатки FBO;
- дефициты;
- данные для проверки перед повышением рекламных ставок;
- данные для планирования поставок.

Когда идти:

- перед усилением продвижения;
- перед акциями;
- при падении продаж;
- при проверке товара с хорошим спросом и низкими заказами.

Риск:

- изменение поставок/остатков является операционной операцией;
- read-only проверка остатков разрешена.

## FBS

```text
https://seller.ozon.ru/app/postings/fbs
```

Для Vital Shevron это не основной рабочий режим. Текущая бизнес-модель для
расчетов: Ozon FBO. Раздел держать на карте, потому что ссылка есть в меню, но
не использовать в расчетах логистики, если владелец явно не подтвердил FBS для
конкретной задачи.

## Финансы

### Баланс

```text
https://seller.ozon.ru/app/finances/balance
```

Что лежит:

- текущий баланс;
- выплаты;
- финансовые уведомления;
- быстрые ссылки на финансовые документы.

### Выплаты

```text
https://seller.ozon.ru/app/finances/invoices
```

Что лежит:

- запланированные выплаты;
- счета/реестры, если доступны.

### Начисления и юнит-экономика

```text
https://seller.ozon.ru/app/finances/accruals?tab=UNIT_ECONOMY
```

Что лежит:

- начисления;
- комиссии;
- логистика;
- unit economy;
- данные для проверки маржинальности.

Для расчетов минимальной цены:

- не использовать приблизительные тарифы, если можно взять прошлые финансовые
  документы или актуальный отчет;
- источник тарифа/комиссии явно указывать в отчете.

## Аналитика

### Общая аналитика

```text
https://seller.ozon.ru/app/analytics
```

Что лежит:

- вход в аналитические отчеты;
- продажи, заказы, динамика;
- переходы к графикам и ассортиментной аналитике.

### Графики аналитики

```text
https://seller.ozon.ru/app/analytics/graphs
```

Что лежит:

- графики заказов, продаж и других метрик;
- фильтр периода;
- переходы из dashboard по заказам.

### Что продавать: поисковые запросы

```text
https://seller.ozon.ru/app/analytics/what-to-sell/all-queries
```

Что лежит:

- поисковые запросы покупателей Ozon;
- популярность запроса;
- добавления в корзину;
- заказано товаров;
- конверсии;
- конкуренты;
- запросы без результата.

Проверено 2026-06-13:

- по умолчанию период `7 дней`;
- смена периода и скачивание могут открывать Premium/Premium Plus paywall;
- таблица имеет пагинацию `50` строк на странице;
- endpoint ЛК:
  `seller.ozon.ru/api/site/searchteam/Stats/queries/search/v2`;
- группы запросов:
  `seller.ozon.ru/api/site/searchstat/Stats/queries/groups`.

Профильная инструкция:

```text
data/planning/search_queries_runbook.md
```

### Конкурентная позиция

```text
https://seller.ozon.ru/app/analytics/what-to-sell/competitive-position
```

Что лежит:

- позиция магазина/категории;
- сравнение с конкурентами;
- переходы из dashboard по топ-категории.

Использовать read-only для ассортимента и конкурентного анализа.

## Отзывы, вопросы и сообщения

### Отзывы

```text
https://seller.ozon.ru/app/reviews
```

Что лежит:

- отзывы покупателей;
- оценки;
- фото/текст;
- статусы просмотра;
- форма ответа.

API-first:

- Review API проверять первым;
- если Ozon API возвращает `HTTP 403: not available with existing subscription`,
  использовать LK/CDP fallback и явно писать это как ограничение.

Риск:

- ответ покупателю является write-операцией и требует approval.

### Вопросы

```text
https://seller.ozon.ru/app/reviews/questions
```

Что лежит:

- вопросы покупателей по товарам;
- форма ответа;
- статусы обработанности.

### Уведомления и сообщения Ozon

```text
https://seller.ozon.ru/app/messenger?group=customers_v2
```

Что лежит:

- покупательские вопросы/чаты, на которые может требоваться ответ;
- обращения и сообщения по заказам;
- площадочные уведомления Ozon;
- важная информация об изменениях работы площадки;
- информационный шум: баннеры, промо, повторяющиеся подсказки.

Ежедневная задача:

- просматривать страницу в read-only режиме;
- отделять вопросы покупателей от уведомлений площадки;
- отсеивать мусор/шум;
- важные сообщения об изменениях работы Ozon передавать владельцу в Telegram;
- вопросы покупателей выносить в отдельный review с черновиком ответа и
  approval.

API-first:

- `POST /v3/chat/list` - список чатов, `cursor`, `has_next`,
  `total_unread_count`;
- `POST /v3/chat/history` - история по реальному `chat_id`;
- `/v2/chat/read`, `/v1/chat/send/message`, `/v1/chat/send/file`,
  `/v1/chat/start` - потенциальные write-операции, использовать только после
  approved-пакета.

Проверка 2026-06-14 на Vital Shevron:

- официальный Seller API `/v3/chat/list` доступен на текущем ключе;
- `/v3/chat/history` доступен по реальному `chat_id`;
- страница ЛК дополнительно использует websocket
  `wss://ws.seller.ozon.ru/chat-notification/ws/v3/web/seller`;
- namespace websocket: `sc_chat`;
- основные UI-команды: `initializeChat`, `getChats`;
- фильтры UI: `unread_only`, `with_products`, `with_orders`,
  `without_my_response`.
- `Создать рассылку` на странице считать рекламным/CRM-баннером, а не
  обязательной операцией ежедневного просмотра.

Детали, команды и ограничения: `data/planning/ozon_messenger_runbook.md`.

Риски:

- тексты чатов могут содержать персональные данные, не сохранять raw без
  отдельного решения;
- LK websocket является внутренним интерфейсом Ozon и должен быть fallback, а
  не основным контрактом;
- отправка ответов покупателям является write-риском;
- задача по рассылкам/CRM, если появится, должна идти отдельным
  read-only/dry-run/approval контуром.

### Общие сообщения

```text
https://seller.ozon.ru/app/messenger?group=main
```

Что лежит:

- общий центр сообщений;
- уведомления Ozon.

### Поддержка

```text
https://seller.ozon.ru/app/messenger?group=support_v2
```

Что лежит:

- обращения в поддержку Ozon;
- переписка с поддержкой.

Правило: тексты ответов сначала готовить в отчете, затем применять только после
явного согласования.

## Продвижение

### Общий раздел

```text
https://seller.ozon.ru/app/promotion-info
```

Что лежит:

- вход в рекламные инструменты;
- подсказки по продвижению;
- переходы к кампаниям.

### CPC / Оплата за клик

```text
https://seller.ozon.ru/app/advertisement/product/cpc
```

Что лежит:

- CPC-кампании;
- товары в кампаниях;
- ставки;
- расходы;
- ДРР/эффективность через отчеты.

API-first:

- `GET /api/client/campaign`;
- `POST /api/client/statistics/json`;
- `GET /api/client/statistics/report?UUID=<uuid>`;
- `GET /api/client/campaign/<campaign_id>/v2/products`;
- apply ставок через Performance API, не через UI, если API покрывает задачу.

Профильная инструкция:

```text
data/planning/ozon_cpc_efficiency_runbook.md
```

## Покупатели / CRM

Основные ссылки:

```text
https://seller.ozon.ru/app/crm/customer-segments
https://seller.ozon.ru/app/crm/mailings/create-trigger
https://seller.ozon.ru/app/crm/mailings/create?preset=dfBonuses
```

Что лежит:

- сегменты покупателей;
- рассылки;
- триггерные коммуникации;
- промо-коммуникации.

Риск:

- создание рассылки или изменение сегмента является write-операцией;
- без approval только read-only анализ.

## Возвраты

```text
https://seller.ozon.ru/app/returns/main?tab=40
```

Что лежит:

- возвраты;
- задачи по возвратам;
- статусы возвратов.

Операционный риск: не подтверждать действия по возвратам без владельца.

## Настройки, доступы и API

### Сотрудники

```text
https://seller.ozon.ru/app/settings/employees
```

Что лежит:

- пользователи;
- роли;
- доступы.

Критичный риск: не менять доступы без отдельного явного approval.

### Seller API keys

```text
https://seller.ozon.ru/app/settings/api-keys
```

Что лежит:

- Seller API credentials;
- интеграционные ключи.

Секреты:

- не выводить значения;
- не сохранять в docs/git/memory;
- допустимо фиксировать только факт проверки и путь к внешнему secret file без
  содержимого.

### Performance API

```text
https://seller.ozon.ru/app/settings/performance-api
```

Что лежит:

- доступы Performance API;
- рекламные API credentials.

Критичный раздел. Не создавать, не отзывать и не показывать ключи без отдельной
задачи и safety-подхода.

## Подписки и paywall

```text
https://seller.ozon.ru/app/subscriptions/premium
```

Что лежит:

- Premium/Premium Plus;
- paywall для части аналитики.

Если раздел аналитики или выгрузка открывает Premium/Premium Plus paywall,
писать в отчете:

```text
Я не могу подтвердить данные/выгрузку без Premium/Premium Plus или другого
доступа.
```

## Банк

```text
https://seller.ozon.ru/app/fintech/bank
```

Что лежит:

- банковские продукты Ozon;
- финансовые сервисы.

Fresh-агенту обычно не нужен для операций Ozon/WB. Любые действия считать
критичными.

## Шаблон добавления нового раздела

```text
Дата | Раздел | URL | Что лежит | Когда использовать | API-first | Риск | Источник проверки
```

Пример:

```text
2026-06-13 | Поисковые запросы | https://seller.ozon.ru/app/analytics/what-to-sell/all-queries | спрос и SEO | сбор топа запросов | API может требовать Premium Pro | read-only | search_queries_runbook + LK/CDP
```
