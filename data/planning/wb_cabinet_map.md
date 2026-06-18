# Wildberries Cabinet Map

Дата актуализации: 2026-06-18.

## Назначение

Карта ЛК Wildberries нужна fresh-агенту без контекста: по задаче он должен
сразу понять, какой раздел WB открыть, что там лежит, какие вкладки важны,
какие действия опасны и где сначала использовать официальный API.

Карту обновлять в том же рабочем цикле, если найден новый раздел, вкладка,
ссылка, API-метод, paywall, ограничение, ошибка или способ восстановления.

## Источники и статус проверки

Проверено 2026-06-13 в Vital Shevron:

- `status-preflight`: WB API `ok`;
- WB keepalive `ok`;
- expected seller: `ИП Витальская И. П.`;
- `seller.wildberries.ru` открыт и залогинен;
- `cmp.wildberries.ru/campaigns/list` открыт и подтвердил продавца;
- из текущего CMP DOM подтверждены вкладки продвижения:
  `Кампании`, `Статистика`, `Финансы`, `Мои задания`, `Новости`.

Дополнительные источники:

- `data/planning/lk_connection_runbook.md`;
- `data/planning/session_manager_runbook.md`;
- `data/planning/pricing_runbook.md`;
- `data/planning/search_queries_runbook.md`;
- `data/planning/wb_actions_runbook.md`;
- `data/planning/wb_promotion_runbook.md`;
- `data/planning/wb_parser_positions_runbook.md`;
- read-only опыт TAKTERRA:
  `/home/pavel/projects/seller_takterra/data/planning/wb_cabinet_map.md`.

Ограничение: часть разделов WB SPA не отдала левое меню как обычные ссылки при
headless чтении DOM. Поэтому карта объединяет текущую проверку с уже
проверенными проектными runbook. Если задача требует write-операции, fresh-агент
обязан открыть конкретный раздел заново, подтвердить UI/API и обновить карту.

## Safety

Разрешено без approval:

- открыть раздел;
- прочитать данные;
- собрать read-only отчет;
- скачать read-only шаблон/отчет, если это не отправляет изменения;
- подготовить dry-run.

Запрещено без явного approval владельца:

- менять цены, скидки, минимальные цены;
- применять акции;
- менять рекламные ставки, бюджеты, статусы и состав кампаний;
- отвечать на отзывы, вопросы и чаты;
- редактировать карточки, фото, остатки, поставки;
- менять доступы, API-ключи и настройки продавца.

Для опасных операций обязательна цепочка:

```text
read-only -> dry-run -> review -> approved -> apply -> verify -> result
```

## Подключение к ЛК

Рабочий контур Vital Shevron:

```text
WB_EXPECTED_SELLER=ИП Витальская И. П.
.sessions/wb/browser-profile/
.sessions/wb/wb_storage_state.json
seller.wildberries.ru
cmp.wildberries.ru
```

Проверка:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m takterra_agent.cli sessions status

PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m takterra_agent.cli status-preflight
```

Не выводить cookies, storage state, auth headers, API tokens и коды входа.

## Быстрый маршрутизатор задач

| Задача | Куда идти в ЛК | Основной URL | Сначала проверить API |
| --- | --- | --- | --- |
| Проверить вход и новости | `Главная` | `https://seller.wildberries.ru/` | Нет, это LK health-check |
| Все карточки WB | `Товары -> Все товары` | `https://seller.wildberries.ru/new-goods/all-goods` | Content API |
| Создание/редактирование карточек | `Товары -> Новые товары` | `https://seller.wildberries.ru/new-goods` | Content API |
| Цены и скидки | `Цены и скидки` | `https://seller.wildberries.ru/discount-and-prices/main-table` | Prices/Discounts API |
| Карантин цен | `Цены и скидки -> Карантин` | `https://seller.wildberries.ru/discount-and-prices/quarantine` | Prices API quarantine |
| Календарь акций WB | `Цены и скидки -> Календарь акций` | `https://seller.wildberries.ru/dp-promo-calendar?` | ЛК endpoints + API prices |
| Минимальная цена WB | `Цены и скидки -> XLSX/шаблон` | `https://seller.wildberries.ru/discount-and-prices/main-table` | API не подтвержден в проекте |
| Отзывы | `Отзывы и вопросы -> Отзывы` | `https://seller.wildberries.ru/feedbacks/feedbacks-tab` | Feedbacks API |
| Вопросы | `Отзывы и вопросы` | `https://seller.wildberries.ru/feedbacks` | Feedbacks API |
| Чат с клиентами | `Чат с клиентами` | `https://seller.wildberries.ru/chat-with-clients` | API не считать подтвержденным |
| Поставки | `Поставки` | `https://seller.wildberries.ru/supplies-management` | Supply API, если задача поддержана |
| Все поставки | `Поставки -> Все поставки` | `https://seller.wildberries.ru/supplies-management/all-supplies` | Supply API |
| Лимиты складов | `Поставки -> Лимиты складов` | `https://seller.wildberries.ru/supplies-management/warehouses-limits` | Read-only |
| Остатки | `Остатки` | `https://seller.wildberries.ru/stock-control` | Statistics/warehouse API |
| Контент-аналитика | `Аналитика -> Аналитика карточек` | `https://seller.wildberries.ru/content-analytics` | Analytics/API, если доступен |
| Интерактивный отчет | `Аналитика -> Интерактивный отчет` | `https://seller.wildberries.ru/content-analytics/interactive-report/main` | Read-only |
| История остатков | `Аналитика -> История остатков` | `https://seller.wildberries.ru/content-analytics/history-remains` | Statistics API |
| Доставки по регионам | `Аналитика -> Доставки по регионам` | `https://seller.wildberries.ru/content-analytics/deliveries-by-region` | Read-only |
| Поисковая аналитика | `Аналитика поиска` | `https://seller.wildberries.ru/search-analytics` | LK endpoint |
| Популярные запросы | `Аналитика поиска -> Поисковые запросы на WB` | `https://seller.wildberries.ru/search-analytics/popular-search-queries` | LK endpoint |
| Отчеты аналитики | `Отчеты` | `https://seller.wildberries.ru/analytics-reports` | Read-only |
| Финансовые отчеты | `Финансы -> Финансовые отчеты` | `https://seller.wildberries.ru/suppliers-mutual-settlements` | Finance API/отчеты |
| История платежей | `Финансы -> История платежей` | `https://seller.wildberries.ru/payment-history` | Finance API/отчеты |
| Продвижение WB | `WB Продвижение -> Кампании` | `https://cmp.wildberries.ru/campaigns/list` | Promotion API |
| Статистика продвижения | `WB Продвижение -> Статистика` | `https://cmp.wildberries.ru/campaigns/statistics` | Promotion API |
| Финансы продвижения | `WB Продвижение -> Финансы` | `https://cmp.wildberries.ru/campaigns/finances` | Promotion API balance |
| Промо-задания/бонусы | `WB Продвижение -> Мои задания` | `https://cmp.wildberries.ru/campaigns/promotions` | Promotion API не считать подтвержденным |
| API-интеграции | `Настройки -> API-интеграции` | `https://seller.wildberries.ru/api-integrations` | Секреты не выводить |
| Пользователи | `Настройки -> Пользователи` | `https://seller.wildberries.ru/supplier-settings/supplier-users` | Критичный раздел |
| Безопасность | `Настройки -> Безопасность` | `https://seller.wildberries.ru/supplier-settings/supplier-security` | Критичный раздел |

## Основной ЛК Seller

### Главная

```text
https://seller.wildberries.ru/
```

Что лежит:

- новости WB;
- быстрые финансовые ссылки;
- переходы в сервисы;
- проверка, что сессия залогинена.

Подтверждено 2026-06-13:

- страница открывается;
- видны новости WB;
- видны ссылки `Финансовые отчеты`, `История платежей`,
  `Каталог решений для бизнеса`, `WB Банк для бизнеса`, `Новости`.

Fresh-агенту:

- начинать отсюда при любых LK-задачах WB;
- если expected seller marker не найден на главной, дополнительно проверить
  `cmp.wildberries.ru/campaigns/list`, потому что в текущем preflight seller
  marker подтверждается именно в CMP.

## Товары и карточки

### Все товары

```text
https://seller.wildberries.ru/new-goods/all-goods
```

Что лежит:

- список карточек WB;
- `vendorCode`;
- `nmID`;
- баркоды и размеры, если доступны в UI;
- статусы карточек;
- переходы к редактированию.

API-first:

- `POST /content/v2/get/cards/list`;
- `POST /content/v2/get/cards/trash`;
- `POST /content/v2/cards/error/list`;
- `POST /content/v2/cards/recover`.

Риски:

- редактирование карточки, характеристик, описания и фото является
  write-операцией;
- для Vital Shevron не определять "наши товары" только по бренду `VitalEmb`,
  потому что бренд есть не только в нашем магазине. Использовать `nmID` из
  локального WB-каталога и подтвержденный supplier.

### Новые товары

```text
https://seller.wildberries.ru/new-goods
```

Что лежит:

- создание карточек;
- импорт/загрузка карточек;
- ошибки карточек.

Риски:

- создание карточек и восстановление из корзины требуют approval;
- WB может частично создать карточки даже при ошибке API, перед повтором нужно
  проверять active/trash/errors.

## Цены, скидки и акции

### Цены и скидки

```text
https://seller.wildberries.ru/discount-and-prices/main-table
```

Что лежит:

- базовая цена;
- скидка;
- цена со скидкой;
- массовые действия;
- XLSX-выгрузки/загрузки;
- настройки минимальной цены и блокировки автоакций через XLSX, если доступны.

Подтвержденный путь для минимальной цены и блокировки автоакций:

```text
Товары и цены -> Цены и скидки
Обновить через Excel -> Минимальные цены и блокировки для автоакций
Действия -> Снять ограничения
```

Смысл по инструкции WB: минимальная цена и блокировка работают только для
автоакций и не влияют на обычные акции. Минимальная цена не запрещает продавцу
самостоятельно поставить текущую цену ниже нее. Если текущая цена уже подходит
под акционную, товар может попасть в автоакцию без дополнительного снижения.

API-first:

- read-only: `GET /api/v2/list/goods/filter`;
- точечная выборка: `POST /api/v2/list/goods/filter`;
- write после approval: `POST /api/v2/upload/task`.

Важно:

- текущий проектный API-контур меняет `price` и `discount`;
- минимальная цена WB через текущий `upload/task` не подтверждена;
- перед автоматизацией минимальной цены скачать шаблон, зафиксировать колонки,
  проверить официальный API и обновить `pricing_runbook.md`.
- по официальной WB Product Management API на 2026-06-18 метод
  `POST /api/v2/upload/task` принимает `nmID`, `price`, `discount`; поля
  минимальной цены/блокировки автоакций в нем не подтверждены.

### Карантин цен

```text
https://seller.wildberries.ru/discount-and-prices/quarantine
```

Что лежит:

- товары, попавшие в Price Quarantine;
- решения по цене;
- команды, аналогичные `Keep Current Price` и `Apply New Price`.

Семантика:

- `Keep Current Price` оставляет старую цену/скидку;
- `Apply New Price` применяет новую цену/скидку из карантина.

Риск: финансовый. Не применять без owner approval.

### Календарь акций WB

```text
https://seller.wildberries.ru/dp-promo-calendar?
```

Что лежит:

- активные акции;
- будущие акции;
- товары в акциях;
- условия участия;
- данные для схемы скидок.

Проектный сценарий:

```text
data/planning/wb_actions_runbook.md
```

Текущая схема Vital Shevron по умолчанию:

```text
70-55-55
```

Read-only helper:

```text
scripts/actions/wb_download_active_actions.js
```

Проверенные LK endpoints из helper:

```text
https://discounts-prices.wildberries.ru/ns/calendar-api/dp-calendar
https://discounts-prices.wildberries.ru/ns/dp-api/discounts-prices/suppliers/api/v1/list/goods/filter
```

## Отзывы, вопросы и чат

### Отзывы

```text
https://seller.wildberries.ru/feedbacks/feedbacks-tab
```

Что лежит:

- отзывы покупателей;
- оценки;
- текст/фото;
- форма ответа;
- статусы обработки.

API-first:

- `feedbacks-api.wildberries.ru`;
- проектный adapter: `src/takterra_agent/marketplaces/wb/communications_adapter.py`.

Риск:

- ответ на отзыв является write-операцией;
- нужен отчет, черновик ответа, approval, apply и verify.

### Вопросы

```text
https://seller.wildberries.ru/feedbacks
```

Что лежит:

- вопросы покупателей;
- форма ответа;
- фильтры и статусы.

Проверенное payload-правило проекта для ответа на вопрос:

```json
{"id": "...", "answer": {"text": "..."}, "state": "wbRu"}
```

Старый payload с top-level `text` может вернуть технический `ok`, но вопрос
останется неотвеченным. Это уже зафиксированная внештатная ситуация.

### Чат с клиентами

```text
https://seller.wildberries.ru/chat-with-clients
```

Что лежит:

- сообщения покупателей;
- переписка;
- операционные вопросы.

Ответы в чате считать write-операцией.

## Поставки, склады и остатки

### Поставки

```text
https://seller.wildberries.ru/supplies-management
```

Что лежит:

- поставки;
- создание/управление поставками;
- статусы поставок.

### Все поставки

```text
https://seller.wildberries.ru/supplies-management/all-supplies
```

Что лежит:

- список поставок;
- статусы;
- история.

### Лимиты складов

```text
https://seller.wildberries.ru/supplies-management/warehouses-limits
```

Что лежит:

- лимиты складов;
- доступность приемки;
- ограничения по складам.

### Остатки

```text
https://seller.wildberries.ru/stock-control
```

Что лежит:

- остатки;
- товары с дефицитом;
- контроль доступности.

Для Vital Shevron:

- перед повышением ставок WB продвижения проверять остатки и доступность товара;
- продажи WB считать FBW, если владелец отдельно не подтвердит другое.

## Аналитика

### Аналитика карточек

```text
https://seller.wildberries.ru/content-analytics
```

Что лежит:

- аналитика карточек;
- качество контента;
- переходы к отчетам.

### Интерактивный отчет

```text
https://seller.wildberries.ru/content-analytics/interactive-report/main
```

Что лежит:

- динамика продаж и метрик;
- фильтры;
- интерактивные отчеты.

### Доставки по регионам

```text
https://seller.wildberries.ru/content-analytics/deliveries-by-region
```

Что лежит:

- география доставок;
- региональная аналитика.

### История остатков

```text
https://seller.wildberries.ru/content-analytics/history-remains
```

Что лежит:

- история остатков;
- изменения по складам/товарам.

### Отчеты аналитики

```text
https://seller.wildberries.ru/analytics-reports
```

Что лежит:

- выгрузки отчетов;
- сохраненные отчеты.

## Поисковая аналитика

### Общий раздел

```text
https://seller.wildberries.ru/search-analytics
```

Что лежит:

- вход в аналитику поиска;
- переходы к поисковым запросам.

### Популярные поисковые запросы

```text
https://seller.wildberries.ru/search-analytics/popular-search-queries
```

Раздел UI:

```text
Аналитика поиска -> Поисковые запросы на WB
```

Проверено 2026-06-13:

- вкладка `Поисковые запросы на WB`;
- вкладка `Поисковые запросы: ваши товары`;
- поиск `Поиск по поисковым запросам`;
- периоды `Вчера`, `Неделя`, `Месяц`, `Квартал`;
- кнопка `Фильтры`;
- таблица с внутренним scroll;
- внизу кнопка `Показать ещё запросы`;
- справа иконка `Создать Excel`;
- справа иконка `Загрузки`.

Endpoint страницы:

```text
seller-content.wildberries.ru/ns/analytics-api/content-analytics/api/v2/search-analysis/search-texts
```

Excel:

```text
seller-content.wildberries.ru/ns/analytics-api/content-analytics/api/v1/file-manager/download
seller-content.wildberries.ru/ns/analytics-api/content-analytics/api/v1/file-manager/downloads?report_types=SEARCH_ANALYSIS_REPORT
```

Профильная инструкция:

```text
data/planning/search_queries_runbook.md
```

## Финансы

### Финансовые отчеты

```text
https://seller.wildberries.ru/suppliers-mutual-settlements
```

Что лежит:

- финансовые отчеты;
- реализации;
- удержания;
- комиссии;
- логистика;
- данные для маржинальности.

### История платежей

```text
https://seller.wildberries.ru/payment-history
```

Что лежит:

- платежи;
- выплаты;
- история операций.

Для расчетов минимальной цены:

- не использовать приблизительные расходы, если можно взять прошлые финансовые
  документы;
- источник комиссии/логистики явно указывать в отчете.

## WB Продвижение

Основной домен:

```text
https://cmp.wildberries.ru/
```

Проверено 2026-06-13:

- `https://cmp.wildberries.ru/campaigns/list` открыт;
- title: `WB Продвижение | Кампании`;
- expected seller найден;
- подтверждены вкладки `Кампании`, `Статистика`, `Финансы`, `Мои задания`,
  `Новости`.

### Кампании

```text
https://cmp.wildberries.ru/campaigns/list
```

Что лежит:

- список рекламных кампаний;
- активные/архивные кампании;
- переходы к редактированию;
- баланс единого счета;
- промобонусы.

API-first:

- `GET /adv/v1/promotion/count`;
- `GET /api/advert/v2/adverts`.

Риск:

- изменение ставок, бюджета, статуса или состава карточек является финансовой
  write-операцией.

### Статистика

```text
https://cmp.wildberries.ru/campaigns/statistics
```

Что лежит:

- статистика кампаний;
- расходы;
- показы;
- клики;
- заказы;
- эффективность.

API-first:

- `GET /adv/v3/fullstats`;
- для проекта использовать `wb-promotion-report`.

### Финансы продвижения

```text
https://cmp.wildberries.ru/campaigns/finances
```

Что лежит:

- баланс продвижения;
- движение средств;
- пополнение/списания.

API-first:

- `GET /adv/v1/balance`.

### Мои задания

```text
https://cmp.wildberries.ru/campaigns/promotions
https://cmp.wildberries.ru/campaigns/promotions/bonus-history
```

Что лежит:

- задания/промоактивности WB;
- промобонусы;
- история бонусов.

### Новости продвижения

```text
https://cmp.wildberries.ru/campaigns/news
```

Что лежит:

- новости рекламного кабинета;
- изменения инструментов продвижения.

### Другие рекламные разделы

```text
https://cmp.wildberries.ru/influence/products
https://cmp.wildberries.ru/cmpf/list
https://cmp.wildberries.ru/outdoor/campaigns
https://cmp.wildberries.ru/bz/
```

Назначение:

- `Influence` - продвижение через influence-инструменты;
- `Media` / `cmpf` - медийная реклама;
- `Outdoor` - реклама в ПВЗ/наружная;
- `Brand Zone` - бренд-зона.

Риск: финансовый. Не менять без отдельной задачи и approval.

Профильная инструкция:

```text
data/planning/wb_promotion_runbook.md
```

## Настройки, доступы и API

### Профиль пользователя

```text
https://seller.wildberries.ru/supplier-settings/user-profile
```

Что лежит:

- профиль пользователя;
- контактные настройки.

### Карточка продавца

```text
https://seller.wildberries.ru/supplier-settings/supplier-card
```

Что лежит:

- данные продавца;
- публичная/юридическая информация.

### Пользователи

```text
https://seller.wildberries.ru/supplier-settings/supplier-users
```

Что лежит:

- пользователи;
- роли;
- доступы.

Критичный риск: не менять доступы без отдельного approval.

### Безопасность

```text
https://seller.wildberries.ru/supplier-settings/supplier-security
```

Что лежит:

- безопасность аккаунта;
- параметры защиты.

### API-интеграции

```text
https://seller.wildberries.ru/api-integrations
```

Что лежит:

- API tokens;
- интеграции;
- управление ключами.

Секреты:

- не выводить значения;
- не сохранять в docs/git/memory;
- допустимо фиксировать только факт проверки и путь к внешнему secret file без
  содержимого.

## Новости и сервисы

### Новости

```text
https://seller.wildberries.ru/news-v2
```

Что лежит:

- новости WB для продавцов;
- изменения тарифов, правил, оферты, логистики.

### Карточка новости

```text
https://seller.wildberries.ru/news-v2/news-details?id=<id>
```

Что лежит:

- конкретная новость;
- дата и условия изменения.

Если правило WB могло измениться, сначала проверять актуальную новость/документ
и только потом делать вывод.

### Каталог решений для бизнеса

```text
https://seller.wildberries.ru/auth-services
```

Что лежит:

- сервисы и решения для продавцов.

### WB Банк для бизнеса

```text
https://seller.wildberries.ru/dbo-legals
```

Что лежит:

- банковские сервисы WB.

Критичный финансовый раздел, не трогать без отдельной задачи.

## Шаблон добавления нового раздела

```text
Дата | Раздел | URL | Что лежит | Когда использовать | API-first | Риск | Источник проверки
```

Пример:

```text
2026-06-13 | Популярные поисковые запросы | https://seller.wildberries.ru/search-analytics/popular-search-queries | спрос и SEO | сбор топа запросов | LK endpoint | read-only | search_queries_runbook + LK
```
