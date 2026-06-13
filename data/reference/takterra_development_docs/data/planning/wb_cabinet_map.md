# Карта ЛК Wildberries

Назначение: постоянная карта найденных разделов личного кабинета Wildberries,
ссылок, API-документации и рабочих сценариев, связанных с WB.

Карту обновлять при каждом обнаружении новых ссылок, разделов, API-методов или
значимых особенностей ЛК WB.

## Статус

Начальная карта создана. По WB уже выполнен сценарий доведения 5 карточек из
Ozon-only до активного состояния WB.

2026-06-10 выполнено подключение ЛК WB в локальном persistent profile
`seller_takterra`; активный продавец переключен и проверен как `ИП Рантусова`.

Модель подключения к ЛК перенесена как инструкция из read-only проекта
`seller_wb` без переноса сессий и секретов:

- `data/planning/lk_connection_runbook.md`
- `data/runs/2026-06-10/lk_connection_wb_20260610T190836Z/summary.md`

## Известные источники опыта

- `/home/pavel/projects/seller_wb` - read-only источник WB-процессов.

## Постоянная инструкция по загрузке карточек

- `data/planning/wb_card_upload_runbook.md`

## Модель подключения к ЛК

Проверенный подход из `seller_wb`:

- отдельный persistent Chromium profile;
- экспортированный Playwright `storageState`;
- вход по модели старого проекта: один persistent profile, один SMS-код, при
  необходимости email-код в той же форме без перезапуска браузера;
- не перезаписывать state, если открыт auth/login/passport/signin;
- проверять два домена: `seller.wildberries.ru` и `cmp.wildberries.ru`;
- refresh выполнять отдельно для основного ЛК и кабинета продвижения;
- preflight не должен печатать токены или данные storage state.
- целевой активный продавец для WB: `ИП Рантусова`.

Целевые локальные пути:

```text
.sessions/wb/browser-profile/
.sessions/wb/wb_storage_state.json
.sessions/wb/session_refresh_logs/
```

## Известные сценарии WB для будущей автоматизации

- Загрузка/создание карточек.
- Фото карточек.
- Акции.
- Скидки/цены.
- Остатки.
- Отзывы.
- Вопросы.

## Известные API-адаптеры в seller_takterra

- `src/takterra_agent/marketplaces/wb/adapter.py`

Текущие методы:

- `POST /content/v2/get/cards/list`
- `POST /content/v2/get/cards/trash`
- `POST /content/v2/barcodes`
- `POST /content/v2/cards/upload`
- `POST /content/v2/cards/upload/add`
- `POST /content/v2/cards/error/list`
- `POST /content/v2/cards/recover`
- `POST /content/v3/media/save`
- `GET /content/v2/object/all`
- `GET /content/v2/object/charcs/{subject_id}`

Официальная документация:

```text
https://dev.wildberries.ru/en/docs/openapi/work-with-products
```

Проверенные API/LK endpoints Price Quarantine:

```text
GET  https://discounts-prices-api.wildberries.ru/api/v2/quarantine/goods
POST https://discounts-prices-api.wildberries.ru/api/v2/upload/task
GET  https://discounts-prices.wildberries.ru/ns/dp-api/discounts-prices/suppliers/api/v1/quarantine/goods
POST https://discounts-prices.wildberries.ru/ns/dp-api/discounts-prices/suppliers/api/v1/quarantine/goods/delete
POST https://discounts-prices.wildberries.ru/ns/dp-api/discounts-prices/suppliers/api/v1/quarantine/goods
```

Профильная инструкция:

```text
data/planning/wb_price_quarantine_runbook.md
```

## Известные особенности WB

- Упаковочный вес не дублировать в `characteristics` как `88952`; использовать
  `dimensions.weightBrutto` в килограммах.
- Emoji в описаниях WB запрещены; описание нужно очищать.
- WB может частично создать карточки даже при HTTP 400. Перед повтором apply
  проверять active cards, trash и errors по точному `vendorCode`.
- Для общего экспорта карточек использовать стабильную пагинацию
  `sort.ascending=true`.
- Карточки из корзины восстанавливать через `/content/v2/cards/recover`, а не
  создавать заново с тем же `vendorCode`.

## Разделы ЛК WB

Найденные стабильные URL из read-only проекта `seller_wb`. Проверить в TAKTERRA
после подключения сессии:

```text
2026-06-10 | Главная WB Seller | https://seller.wildberries.ru/ | проверка входа и магазина | status/lk | read-only
2026-06-10 | Все товары | https://seller.wildberries.ru/new-goods/all-goods | список карточек | карточки/каталог | write-риск при редактировании
2026-06-10 | Новые товары | https://seller.wildberries.ru/new-goods | создание и работа с карточками | карточки | write-риск
2026-06-10 | Отзывы | https://seller.wildberries.ru/feedbacks/feedbacks-tab | отзывы покупателей | отзывы | write-риск ответов
2026-06-10 | Вопросы/отзывы общий раздел | https://seller.wildberries.ru/feedbacks | обратная связь | отзывы/вопросы | write-риск ответов
2026-06-10 | Чат с клиентами | https://seller.wildberries.ru/chat-with-clients | сообщения покупателей | чат | write-риск ответов
2026-06-10 | Поставки | https://seller.wildberries.ru/supplies-management | поставки | поставки | операционный риск
2026-06-10 | Все поставки | https://seller.wildberries.ru/supplies-management/all-supplies | поставки | поставки | операционный риск
2026-06-10 | Лимиты складов | https://seller.wildberries.ru/supplies-management/warehouses-limits | лимиты | поставки | read-only/операционный
2026-06-10 | Остатки | https://seller.wildberries.ru/stock-control | остатки | остатки | операционный риск
2026-06-10 | Аналитика карточек | https://seller.wildberries.ru/content-analytics | аналитика контента | аналитика/карточки | read-only
2026-06-10 | Интерактивный отчет | https://seller.wildberries.ru/content-analytics/interactive-report/main | аналитика | аналитика | read-only
2026-06-10 | Доставки по регионам | https://seller.wildberries.ru/content-analytics/deliveries-by-region | логистика/аналитика | аналитика | read-only
2026-06-10 | История остатков | https://seller.wildberries.ru/content-analytics/history-remains | остатки | аналитика/остатки | read-only
2026-06-10 | Поисковая аналитика | https://seller.wildberries.ru/search-analytics | поисковая аналитика | SEO/ассортимент | read-only
2026-06-10 | Популярные поисковые запросы | https://seller.wildberries.ru/search-analytics/popular-search-queries | спрос/SEO | SEO | read-only
2026-06-10 | Отчеты аналитики | https://seller.wildberries.ru/analytics-reports | отчеты | аналитика | read-only
2026-06-11 | Карантин цен | https://seller.wildberries.ru/discount-and-prices/quarantine | товары WB в Price Quarantine, команды Keep Current Price / Apply New Price | цены/скидки/карантин | финансовый риск
2026-06-10 | Продвижение WB | https://cmp.wildberries.ru/campaigns/list | рекламные кампании | реклама/акции | финансовый риск
2026-06-10 | Influence | https://cmp.wildberries.ru/influence/products | продвижение | реклама | финансовый риск
2026-06-10 | Media | https://cmp.wildberries.ru/cmpf/list | медийная реклама | реклама | финансовый риск
2026-06-10 | Outdoor | https://cmp.wildberries.ru/outdoor/campaigns | реклама в ПВЗ/наружная | реклама | финансовый риск
2026-06-10 | Brand Zone | https://cmp.wildberries.ru/bz/ | бренд-зона | бренд/реклама | финансовый риск
2026-06-10 | Профиль пользователя | https://seller.wildberries.ru/supplier-settings/user-profile | профиль | настройки | критичный риск
2026-06-10 | Карточка продавца | https://seller.wildberries.ru/supplier-settings/supplier-card | данные продавца | настройки | критичный риск
2026-06-10 | Пользователи | https://seller.wildberries.ru/supplier-settings/supplier-users | доступы | настройки | критичный риск
2026-06-10 | Безопасность | https://seller.wildberries.ru/supplier-settings/supplier-security | безопасность | настройки | критичный риск
2026-06-10 | API-интеграции | https://seller.wildberries.ru/api-integrations | API-ключи | интеграции | секреты
```

Семантика команд Price Quarantine:

```text
Keep Current Price - отменяет quarantined-изменение и оставляет старую цену/скидку.
Apply New Price - применяет новую цену/скидку из карантина.
```

Для сохранения скидки по схеме `65-50-50` использовать `Apply New Price`.

При обнаружении нового раздела добавлять строку:

```text
Дата | Название раздела | URL/путь | Для чего нужен | Связанный сценарий | Риски
```

## Правило обновления

Если агент при работе обнаружил новую ссылку, раздел, API-метод или нюанс ЛК WB,
он обязан дополнить эту карту в том же рабочем цикле.
