# Ozon Messenger Runbook

Дата проверки: 2026-06-14.

## Назначение

Инструкция описывает страницу Ozon Seller Messenger:

```text
https://seller.ozon.ru/app/messenger?group=customers_v2
```

Раздел является страницей уведомлений и сообщений Ozon. Туда приходят:

- вопросы/сообщения покупателей, на которые нужно отвечать;
- сервисные уведомления по работе площадки;
- информационные сообщения Ozon об изменениях, правилах, функциях, сроках,
  доступах, продвижении и других важных событиях.

Задача агента при ежедневном просмотре - отделить мусор/шум от важных
сообщений, передать владельцу в Telegram значимые изменения по работе
площадки и отдельно вынести покупательские вопросы/чаты, требующие ответа.

Ответы покупателям являются опасной write-операцией и выполняются только через
цепочку:

```text
read-only -> dry-run -> review -> approved -> apply -> verify -> result
```

## Краткий вывод по API

Гипотеза `данные мессенджера не доступны по API` не подтвердилась полностью.
На текущем ключе Vital Shevron официальный Ozon Seller API для чатов доступен:

- `POST /v3/chat/list` - успешно вернул список чатов;
- `POST /v3/chat/history` - успешно вернул историю по реальному `chat_id`;
- `POST /v3/chat/history` с фиктивным `chat_id=0` вернул ожидаемую ошибку
  `400 invalid chat guid`, то есть метод существует и валидирует ID.

Проверка 2026-06-14:

- `POST /v3/chat/list`, payload `{"filter": {"chat_status": "All"}, "limit": 5}`;
- ответ: `chats`, `cursor`, `has_next`, `total_unread_count`;
- возвращено `5` чатов, `has_next=true`, `total_unread_count=1`;
- `POST /v3/chat/history` по первому реальному `chat_id` вернул `4`
  сообщения;
- shape сообщения: `message_id`, `created_at`, `is_read`, `is_image`,
  `user.id`, `user.type`, `data[]`, `context.order_number`, `context.sku`.

Вывод: для read-only списка и истории чатов сначала использовать официальный
Seller API. ЛК/CDP и websocket нужны как fallback или для UI-специфичных
фильтров/проверок, если API не даст нужного состояния.

## Актуальные внешние источники

- Ozon for dev: `https://dev.ozon.ru/news/587-Izmeneniia-raboty-chatov-v-Seller-API/`
  - Ozon сообщал, что методы Seller API для переписки с покупателями работают
    по подписке Premium Plus.
- Ozon for dev: `https://dev.ozon.ru/news/593-Izmeneniia-raboty-chatov-v-Seller-API-2-0/`
  - Ozon описывал изменения работы чатов в Seller API 2.0.
- Ozon Seller API notifications:
  `https://t.me/s/OzonSellerAPI?after=566`
  - Ozon сообщал о переходе с `/v2/chat/list` и `/v2/chat/history` на
    `/v3/chat/list` и `/v3/chat/history`.

Перед реализацией write-операций по чатам проверить актуальную документацию
Ozon Seller API заново.

## Страница ЛК

URL:

```text
https://seller.ozon.ru/app/messenger?group=customers_v2
```

Проверенные видимые элементы 2026-06-14:

- вкладки:
  - `customers_v2` - уведомления/чаты покупателей и сообщения, связанные с
    покупателями;
  - `support_v2` - поддержка;
  - `main` - общий раздел сообщений;
- поиск по сообщениям;
- кнопка `Стать Premium Plus`;
- `Создать рассылку` - рекламный/CRM-баннер на странице, а не обязательная
  операция ежедневного просмотра;
- фильтры:
  - `Только новые`;
  - `По товару`;
  - `По заказу`;
  - `Без вашего ответа`;
  - `Без ответа клиента`;
  - `Сбросить фильтры`;
- `Помощь`.

В ежедневном мониторинге баннер `Создать рассылку` игнорировать. Если когда-то
появится отдельная задача по рассылкам/CRM, это новая write-related операция:
сначала read-only исследование, затем dry-run/review/approval.

## LK/CDP endpoints

Probe 2026-06-14 через Ozon CDP `127.0.0.1:9544` показал:

### HTTP оболочка

- `GET /app/messenger?group=customers_v2`
- `GET /api/site/chat/v2/chat/chatGroupsUnreadCount?company_mode=...`
- `POST /api/site/chat/v2/chat/getCrmBannerRequest`
- `GET /api/composer-api.bx/page/json/v2?url=...`
- `POST /api/composer-api.bx/widget/json/v2`
- `POST /api/v2/company/finance-info`
- `POST /api/premium/status`
- `POST /api/role/list`
- `POST /api/site/notice/list`
- `POST /api/v2/resolve`

Эти HTTP endpoints дают оболочку, счётчики, роли, premium/banner state и
composer layout. Основной список чатов пришёл не через эти HTTP endpoints.

### Websocket

Основной realtime-канал страницы:

```text
wss://ws.seller.ozon.ru/chat-notification/ws/v3/web/seller
```

Query keys:

- `company_id`;
- `language`;
- `namespace`;
- `selectedCompanyMode`.

Безопасно зафиксированные команды UI:

```json
{"namespace":"sc_chat","method":"initializeChat","params":{"withTheme":true}}
{"namespace":"sc_chat","method":"getChats","params":{"filter":{"chatType":"customers_v2","onlyUnread":false},"limit":30,"offset":0,"withFirstPageInfo":true}}
{"namespace":"sc_chat","method":"getChats","params":{"filter":{"chatType":"customers_v2","filters":["unread_only"],"onlyUnread":false},"limit":30,"offset":0,"withFirstPageInfo":false}}
{"namespace":"sc_chat","method":"getChats","params":{"filter":{"chatType":"customers_v2","filters":["unread_only","with_products"],"onlyUnread":false},"limit":30,"offset":0,"withFirstPageInfo":false}}
{"namespace":"sc_chat","method":"getChats","params":{"filter":{"chatType":"customers_v2","filters":["unread_only","with_orders"],"onlyUnread":false},"limit":30,"offset":0,"withFirstPageInfo":false}}
{"namespace":"sc_chat","method":"getChats","params":{"filter":{"chatType":"customers_v2","filters":["unread_only","with_orders","without_my_response"],"onlyUnread":false},"limit":30,"offset":0,"withFirstPageInfo":false}}
```

Полученные response shapes:

- initial `getChats`: `items.length=30`, есть `hasItemsAfter`,
  `nextPageCursor`, `firstPageInfo`, `chatGroups`, `showOnlyUnreadFilter`;
- `unread_only`: `items.length=3`;
- `unread_only + with_products`: `items.length=4`;
- `unread_only + with_orders`: `items.length=1`;
- `unread_only + with_orders + without_my_response`: `items.length=1`.

Содержимое сообщений, имена покупателей, тексты и вложения в probe не
сохранялись. Были сохранены только shape/счётчики/технические keys.

## Read-only API-first маршрут

Базовый список чатов:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python - <<'PY'
from takterra_agent.config import load_credentials
from takterra_agent.marketplaces.ozon.adapter import OzonSellerAdapter

creds = load_credentials().ozon_seller
adapter = OzonSellerAdapter(creds)
data = adapter.post("/v3/chat/list", {
    "filter": {"chat_status": "All"},
    "limit": 5,
})
print({
    "keys": sorted(data.keys()),
    "count": len(data.get("chats") or []),
    "has_next": data.get("has_next"),
    "total_unread_count": data.get("total_unread_count"),
})
PY
```

История чата:

```python
adapter.post("/v3/chat/history", {"chat_id": chat_id, "limit": 50})
```

Для ежедневного мониторинга уведомлений сначала собрать:

- `total_unread_count`;
- список новых/непрочитанных чатов;
- чаты/уведомления с контекстом `sku` и `order_number`;
- сообщения, где нужен ответ владельца/агента;
- площадочные уведомления, которые не являются вопросом покупателя.

Если официальный API не позволяет надежно отделить площадочные уведомления от
покупательских вопросов, использовать LK/CDP websocket fallback и зафиксировать
это как ограничение источника.

Подтвержденное наблюдение 2026-06-14 по Ozon Seller API chat history:

- `user.type = Customer` - сообщение покупателя, может требовать ответа;
- `user.type = NotificationUser` - площадочное уведомление Ozon, не готовить
  покупательский ответ;
- `user.type = ChatBot` - бот/подсказки, обычно шум или сервисная навигация;
- `user.type = Seller` - сообщение продавца или внешний собеседник в чате,
  проверять контекст вручную; может быть важным, если содержит претензию,
  спор, юридическое требование или другое бизнес-рисковое сообщение.

Нельзя классифицировать все непрочитанные чаты как вопросы покупателей:
Ozon помечает площадочные уведомления непрочитанными так же, как клиентские
сообщения. Перед подготовкой ответа обязательно проверять `user.type` и
содержание последнего сообщения.

Отметка прочитанным и отправка сообщений/файлов - write-операции. Даже если
метод технически доступен, использовать только после approved-пакета:

- `/v2/chat/read`;
- `/v1/chat/send/message`;
- `/v1/chat/send/file`;
- `/v1/chat/start`.

Подтвержденная внештатная ситуация 2026-06-14:

- официальный Seller API `/v1/chat/send/message` вернул `HTTP 403`:
  `method is allowed starting from the premium plus subscription`;
- официальный Seller API `/v2/chat/read` для покупательских чатов вернул
  `HTTP 403`: метод доступен с Premium Plus;
- отправка согласованных ответов покупателям была успешно выполнена через
  Ozon ЛК/CDP fallback;
- после отправки verify через `/v3/chat/history` показал, что последние
  сообщения в трех чатах имеют `user.type = Seller` и совпадают с
  согласованными текстами.

Для площадочных уведомлений `/v2/chat/read` сработал через API, но только если
передавать не один `chat_id`, а payload:

```json
{"chat_id": "<chat_id>", "from_message_id": <last_message_id>}
```

Если отправить только `chat_id`, Ozon возвращает `HTTP 400`:
`message_id is empty`.

После успешной отметки всех найденных `unread_count` строк API
`/v3/chat/list` может еще показывать общий `total_unread_count=1`, хотя при
постраничном обходе все чаты имеют `unread_count=0`. Такой счетчик считать
нестрогим до повторной проверки ЛК или других групп (`main`, `support_v2`).

## Fallback через LK/CDP

Fallback нужен, если:

- Seller API chat methods вернули `403` из-за подписки/прав;
- API не даёт нужный UI-фильтр или состояние;
- нужно сверить, что видит владелец в ЛК;
- нужно исследовать изменение интерфейса Ozon.

Probe-скрипт:

```bash
RUN_DIR="data/runs/$(date +%F)/ozon_messenger_probe_$(date +%Y%m%dT%H%M%S)"
node scripts/research/ozon_messenger_page_probe_cdp.js --run-dir "$RUN_DIR"
```

Скрипт сохраняет только redacted artifacts:

- `raw/requests_redacted.json`;
- `raw/responses_shape_redacted.json`;
- `raw/websockets_redacted.json`;
- `raw/ui_summary_redacted.json`;
- `summary.json`.

Запрещено сохранять в проект:

- cookies;
- auth headers;
- storage state;
- тексты сообщений покупателей;
- имена/телефоны/адреса/прочие персональные данные;
- файлы и изображения из переписки без отдельного решения по хранению PII.

## Будущая автоматизация

Рекомендуемый порядок:

1. Реализовать ежедневный read-only CLI `ozon-notifications-report` или
   `ozon-messenger-report` через официальный Seller
   API:
   - список чатов;
   - непрочитанные;
   - без ответа продавца, если можно вычислить по истории;
   - связанные `sku` и `order_number`;
   - площадочные уведомления Ozon;
   - классификацию `требует ответа`, `важное изменение площадки`,
     `информационный шум/мусор`;
   - краткий Telegram-отчет без вывода персональных данных.
2. Добавить fallback через LK/CDP websocket только для тех фильтров, которых не
   хватает в API.
3. Для ответов подготовить отдельный dry-run:
   - текст покупателя;
   - контекст заказа/товара;
   - предлагаемый ответ;
   - риски;
   - owner approval.
4. Apply ответов делать отдельной командой с verify.

## Ежедневный просмотр уведомлений

Ежедневно проверять страницу в read-only режиме.

Минимальный отчет в Telegram:

- сколько новых/непрочитанных уведомлений;
- сколько вопросов/чатов покупателей требуют ответа;
- сколько площадочных сообщений признаны важными;
- какие важные изменения Ozon нужно знать владельцу;
- что отсеяно как шум: массовые баннеры, реклама, повторяющиеся
  промо-уведомления, нерелевантные подсказки;
- какие ответы покупателям нужно согласовать, если есть обращения.

Критерии важности площадочного сообщения:

- меняются правила работы Ozon;
- меняются комиссии, логистика, хранение, тарифы, выплаты или документы;
- меняются правила карточек, модерации, маркировки, фото, контента;
- меняются акции, реклама, продвижение, условия участия или сроки;
- есть предупреждение о блокировке, ограничении, ошибке, претензии,
  проблеме качества или сроках ответа;
- требуется действие владельца или агента.

К мусору относить:

- рекламные баннеры без обязательного действия;
- повторяющиеся промо-сообщения без влияния на текущие операции;
- общие подсказки Ozon, если они не меняют правила и не требуют действия;
- уже обработанные уведомления без нового содержания.

Если есть вопрос/сообщение покупателя, на которое нужно отвечать, не отвечать
сразу. Сначала подготовить черновик ответа в отчете и получить approval.

## Риски

- Чаты содержат персональные данные и тексты покупателей. Отчеты в чат должны
  быть минимально достаточными; полные тексты использовать только там, где это
  нужно для согласования ответа.
- Площадочные уведомления могут быть важны для бизнеса даже без ответа
  покупателю, поэтому их нельзя автоматически отбрасывать только потому, что
  это не чат клиента.
- Официальный API может быть завязан на Premium Plus. Сегодня метод доступен,
  но перед production-автоматизацией нужно повторить preflight.
- LK websocket является внутренним интерфейсом Ozon и может измениться без
  предупреждения. Использовать как fallback, не как основной контракт.
