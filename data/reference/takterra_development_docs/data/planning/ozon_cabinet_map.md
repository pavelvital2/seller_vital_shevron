# Карта ЛК Ozon

Назначение: постоянная карта найденных разделов личного кабинета Ozon, ссылок,
API-документации и рабочих сценариев, связанных с Ozon.

Карту обновлять при каждом обнаружении новых ссылок, разделов, API-методов или
значимых особенностей ЛК Ozon.

## Статус

Начальная карта создана. Полное исследование ЛК Ozon для TAKTERRA еще не
выполнялось в этом проекте. Модель подключения к ЛК перенесена как инструкция из
read-only проектов. 2026-06-10 по явному разрешению владельца выполнен перенос
полного Ozon-профиля из read-only проекта в локальную секретную зону
`seller_takterra`; старый проект не изменялся.

- `data/planning/lk_connection_runbook.md`
- `data/runs/2026-06-10/lk_connection_ozon_transfer_20260610T173809Z/summary.md`

## Известные источники опыта

- `/home/pavel/projects/seller_ozon` - read-only источник Ozon-процессов.
- `/home/pavel/projects/seller_ozon_vitalsewing` - read-only источник зрелой
  структуры, workflow и safety-подхода.

## Известные сценарии Ozon для будущей автоматизации

- Эластичный бустинг.
- Акции.
- Цены.
- Остатки.
- Отзывы.
- Вопросы.
- Карточки товаров.
- Реклама / CPC / ставки.

## Модель подключения к ЛК

Проверенный подход из `seller_ozon` и `seller_ozon_vitalsewing`:

- отдельный persistent Chrome profile для TAKTERRA;
- отдельный Playwright `storageState` только как резервный снимок;
- ручной OTP-вход только в тот же persistent profile;
- отдельный CDP-порт для агента, рекомендуемый TAKTERRA-порт: `9444`;
- активный TAKTERRA keeper: `scripts/sessions/start_ozon_keeper.sh`, CDP
  `http://127.0.0.1:9444`;
- keepalive/watchdog через CDP после успешного входа;
- проектный refresh по таймеру: `scripts/sessions/ozon_session_refresh.sh` и
  `scripts/sessions/start_ozon_session_watchdog.sh`;
- если CDP `9444` живой, refresh сначала выполняет `cdp_keepalive`; при
  отсутствии CDP и наличии `tmp/auth/ozon_user_cookies.json` импортирует свежие
  cookies, затем экспортирует storage state;
- desktop Chrome user-agent, locale `ru-RU`, timezone `Europe/Moscow`;
- запрет копировать `.ozon-session/`, cookies или storage state из старых
  проектов без явного разрешения владельца и отдельного безопасного отчета;
- URL с `__rr=1` фиксировать как `rrMode`, но не считать блокировкой сам по
  себе. Блокировка подтверждается только при отсутствии оболочки кабинета или
  явных текстах ошибок.
- Для cookie-only восстановления после `rrMode` с ошибкой предпочтителен полный
  request header `Cookie` из Network-запроса успешной открытой вкладки
  dashboard.
- Старый живой CDP `127.0.0.1:9222` относится к проекту `seller_ozon` и магазину
  `Vital Shevron`; его успешная работа объясняется полным persistent profile и
  watchdog, а не одним cookie-файлом. Не использовать как TAKTERRA-контур без
  явного разрешения владельца.

Целевые локальные пути:

```text
.sessions/ozon/chrome-profile/
.sessions/ozon/ozon_seller_storage_state.json
.sessions/ozon/session_refresh_logs/
```

## Известные API-адаптеры в seller_takterra

- `src/takterra_agent/marketplaces/ozon/adapter.py`

Текущие методы:

- `POST /v3/product/list`
- `POST /v3/product/info/list`
- `POST /v4/product/info/attributes`

## Разделы ЛК Ozon

Найденные стабильные URL из read-only проектов. Проверить в TAKTERRA после
подключения сессии:

```text
2026-06-10 | Главная / Dashboard | https://seller.ozon.ru/app/dashboard/main | проверка входа и магазина | status/lk | read-only
2026-06-10 | Товары | https://seller.ozon.ru/app/products | список товаров и карточки | карточки/каталог | write-риски при редактировании
2026-06-10 | Цены | https://seller.ozon.ru/app/prices/control | контроль цен | цены | финансовый риск
2026-06-10 | Акции продавца | https://seller.ozon.ru/app/highlights/list | акции и подборки | акции | финансовый риск
2026-06-10 | Заявки на скидку | https://seller.ozon.ru/app/prices/discount-requests | скидки/заявки | скидки | финансовый риск
2026-06-10 | Остатки FBO | https://seller.ozon.ru/app/fbo-stocks/stocks-management | остатки | остатки | операционный риск
2026-06-10 | Заказы FBS | https://seller.ozon.ru/app/postings/fbs | заказы | заказы | операционный риск
2026-06-10 | Баланс | https://seller.ozon.ru/app/finances/balance | финансы | финансы | read-only
2026-06-10 | Начисления / Unit Economy | https://seller.ozon.ru/app/finances/accruals?tab=UNIT_ECONOMY | юнит-экономика | аналитика | read-only
2026-06-10 | Аналитика | https://seller.ozon.ru/app/analytics | аналитика | аналитика | read-only
2026-06-10 | Графики аналитики | https://seller.ozon.ru/app/analytics/graphs | графики продаж | аналитика | read-only
2026-06-10 | Что продавать | https://seller.ozon.ru/app/analytics/what-to-sell/all-queries | поисковые запросы | SEO/ассортимент | read-only
2026-06-10 | Отзывы | https://seller.ozon.ru/app/reviews | отзывы | отзывы | write-риск ответов
2026-06-10 | Вопросы/чат покупателей | https://seller.ozon.ru/app/messenger?group=customers_v2 | сообщения покупателей | вопросы/чат | write-риск ответов
2026-06-10 | Поддержка | https://seller.ozon.ru/app/messenger?group=support_v2 | поддержка | поддержка | write-риск
2026-06-10 | Продвижение | https://seller.ozon.ru/app/promotion-info | продвижение | реклама/акции | финансовый риск
2026-06-10 | CPC-реклама | https://seller.ozon.ru/app/advertisement/product/cpc | товарная реклама | CPC/ставки | финансовый риск
2026-06-10 | Сотрудники | https://seller.ozon.ru/app/settings/employees | доступы | настройки | критичный риск
2026-06-10 | API-ключи Seller | https://seller.ozon.ru/app/settings/api-keys | API-ключи | интеграции | секреты
2026-06-10 | Performance API | https://seller.ozon.ru/app/settings/performance-api | Performance API | реклама/API | секреты
```

При обнаружении нового раздела добавлять строку:

```text
Дата | Название раздела | URL/путь | Для чего нужен | Связанный сценарий | Риски
```

## Правило обновления

Если агент при работе обнаружил новую ссылку, раздел, API-метод или нюанс ЛК
Ozon, он обязан дополнить эту карту в том же рабочем цикле.
