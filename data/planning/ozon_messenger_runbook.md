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
from seller_agent.config import load_credentials
from seller_agent.marketplaces.ozon.adapter import OzonSellerAdapter

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

Подтвержденное наблюдение 2026-06-19 по текущей форме ответа API:

- в `/v3/chat/list` идентификатор чата может лежать не в верхнем поле
  `chat_id`, а во вложенном объекте `chat.chat_id`;
- `unread_count` по строкам чатов и общий `total_unread_count` могут
  расходиться с фактическими `is_read=false` в `/v3/chat/history`; для
  ежедневного triage считать приоритетным постраничный обход историй и
  фактические `is_read=false`;
- текст сообщения в `/v3/chat/history` может приходить не в поле `text`, а в
  массиве `data`; элементы `data` могут быть строками, поэтому парсер должен
  поддерживать обе формы: строка и объект;
- истории возвращаются от новых сообщений к старым; последнее актуальное
  сообщение для классификации - первый элемент списка, если порядок
  подтвержден по `created_at`;
- для вопросов покупателей отдельно разделять:
  - новые/непрочитанные обращения (`user.type=Customer` и `is_read=false`);
  - старые прочитанные диалоги, где последним осталось сообщение покупателя;
    это не смешивать с текущими новыми вопросами, а выносить отдельным
    backlog-хвостом.

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

Подтвержденная внештатная ситуация 2026-06-19:

- официальный Seller API `/v1/chat/send/message` снова вернул `HTTP 403`:
  `method is allowed starting from the premium plus subscription`;
- по одному покупательскому чату официальный API вернул `chat blocked by user`,
  а LK/CDP fallback не нашел поле ввода; такой чат считать недоступным для
  ответа и не ретраить вслепую;
- 4 согласованных ответа были успешно отправлены через
  `scripts/messenger/ozon_send_messages_cdp.js`;
- verify через `/v3/chat/history` подтвердил, что последние сообщения в этих
  4 чатах имеют `user.type = Seller` и совпадают с согласованными текстами.

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

Подтвержденный apply 2026-06-19:

- после approval владельца `18` площадочных уведомлений Ozon в `11` чатах
  были отмечены прочитанными через `/v2/chat/read`;
- по каждой строке использовался payload
  `{"chat_id": "<chat_id>", "from_message_id": <newest_unread_notification_message_id>}`;
- verify полным обходом `/v3/chat/history` показал `0` чатов с
  непрочитанными `NotificationUser` и `0` чатов с непрочитанными `Customer`;
- результат сохранен в
  `data/runs/2026-06-19/ozon_messenger_notifications_mark_read_20260619T082141/`.

## Fallback через LK/CDP

Fallback нужен, если:

- Seller API chat methods вернули `403` из-за подписки/прав;
- API не даёт нужный UI-фильтр или состояние;
- нужно сверить, что видит владелец в ЛК;
- нужно исследовать изменение интерфейса Ozon.

Перед любым LK/CDP fallback обязательно сверить контур:

- текущий проект: `/home/pavel/projects/seller_vital_shevron`;
- Ozon CDP Vital Shevron: `http://127.0.0.1:9544`;
- профиль браузера должен быть
  `/home/pavel/projects/seller_vital_shevron/.sessions/ozon/chrome-profile`;
- expected store: `Vital Shevron`;
- не использовать CDP/профиль другого проекта, даже если рядом работает
  агент TAKTERRA или другого магазина. Если `ss`, `ps` или CDP page list
  показывают чужой `user-data-dir`, операцию остановить и восстановить
  правильный keeper.

Probe-скрипт:

```bash
RUN_DIR="data/runs/$(date +%F)/ozon_messenger_probe_$(date +%Y%m%dT%H%M%S)"
node scripts/research/ozon_messenger_page_probe_cdp.js --run-dir "$RUN_DIR"
```

Apply согласованных ответов через LK/CDP fallback:

```bash
node scripts/messenger/ozon_send_messages_cdp.js \
  --approved-path data/approved/<approved_dir>/approved_apply_plan.json \
  --run-dir data/runs/<date>/<apply_run_id>
```

После apply обязательно проверить результат официальным Seller API
`/v3/chat/history`: последнее сообщение по каждому успешно обработанному
`chat_id` должно иметь `user.type = Seller` и текст должен совпадать с
approved package.

Подтвержденный блокер 2026-06-20:

- официальный Seller API `/v1/chat/send/message` вернул `HTTP 403`:
  `method is allowed starting from the premium plus subscription`;
- LK/CDP fallback не смог отправить approved-сообщение, потому что страница
  `https://seller.ozon.ru/app/messenger?group=customers_v2` не загрузила
  список чатов: websocket
  `wss://ws.seller.ozon.ru/chat-notification/ws/v3/web/seller` падал с
  `HTTP Authentication failed; no valid credentials available`, UI показывал
  ошибку `ws1006`;
- обычный `ozon_session_keepalive_cdp.js` и `sessions status --marketplace
  ozon` при этом могли быть `ok`, потому что dashboard/products/prices
  открывались. Это не подтверждает работоспособность Messenger websocket;
- если `ozon_send_messages_cdp.js` вернул `message_input_not_found`, нужно
  выполнить read-only Messenger probe и проверить websocket frames/консоль, а
  не ретраить отправку вслепую;
- если после keepalive/restart Messenger websocket остается в `ws1006`, ответ
  покупателю считать неотправленным, проверить `/v3/chat/history` и запросить
  отдельное восстановление Ozon LK-сессии с интерактивным входом. Нельзя
  отмечать такой чат закрытым.

Подтвержденное восстановление и apply 2026-06-20:

- После owner approval был выполнен интерактивный restore Ozon LK-сессии:
  `restore_ozon_session_for_messenger_retry_20260620T0845`.
- Перед повторным LK/CDP fallback проверен контур: Vital Shevron на
  `127.0.0.1:9544`, профиль
  `/home/pavel/projects/seller_vital_shevron/.sessions/ozon/chrome-profile`;
  TAKTERRA работала отдельно на `127.0.0.1:9444`.
- Правило: разные проекты не должны делить Chrome profile. Если появляется
  Chrome `ProcessSingleton`, сначала проверить `ps`/`ss`; в подтвержденной
  ситуации 2026-06-20 это был конфликт двух процессов Vital Shevron за один
  профиль Vital, а не общий профиль с TAKTERRA.
- Повторная отправка через официальный `/v1/chat/send/message` снова вернула
  `HTTP 403` Premium Plus, поэтому применен LK/CDP fallback:
  `ozon_messenger_retry_apply_after_restore_20260620T0848`.
- Результат fallback: `sent_ok=1`, `skipped=0`, `blocker=""`.
- Verify через официальный `/v3/chat/history` подтвердил: последнее сообщение
  в целевом чате имеет `user.type = Seller`, текст совпадает с approved
  package, непрочитанных сообщений покупателя в проверенном окне нет.
- После отправки выполнен `ozon_session_keepalive_cdp.js`; итоговый
  `sessions_status_after_keepalive_messenger_retry_20260620T0849` вернул
  `overall_status: ok`.

Подтвержденный apply 2026-06-21:

- После owner approval по run `ozon_messenger_triage_20260621T172053`
  выполнен apply `ozon_messenger_apply_20260621T173056`.
- Официальный `/v1/chat/send/message` для ответа покупателю снова вернул
  `HTTP 403`: `method is allowed starting from the premium plus subscription`;
  это штатно переводит отправку ответа на LK/CDP fallback.
- `scripts/messenger/ozon_send_messages_cdp.js` отправил 1 согласованный ответ
  покупателю через CDP-контур Vital Shevron `127.0.0.1:9544`; результат
  `sent_ok=1`, `skipped=0`, `blocker=""`.
- 3 информационных уведомления Ozon отмечены прочитанными через
  `/v2/chat/read` с payload `{"chat_id": "...", "from_message_id": ...}`.
- Verify через `/v3/chat/history` подтвердил: последнее сообщение в
  покупательском чате имеет `user.type = Seller`, текст совпадает с approved
  package, непрочитанных сообщений покупателя нет.
- Полный обход `/v3/chat/list` после apply показал `0` строк с
  `unread_count > 0` и `0` фактически непрочитанных сообщений в историях; общий
  `total_unread_count` при этом остался `1`, поэтому этот общий счетчик
  считать нестрогим и сверять по строкам/историям.
- После apply выполнен `ozon_session_keepalive_cdp.js`; контур Vital Shevron
  подтвержден, `stateExported=true`, `needsLogin=false`.

Подтвержденный apply/cleanup 2026-07-01:

- После owner approval по run `ozon_messenger_triage_20260701T0708`
  подготовлен approved-пакет
  `data/approved/ozon_messenger_triage_20260701T0708_pending_approved_20260701T0719/approved_apply_plan.json`.
- Официальный `/v1/chat/send/message` для ответа покупателю вернул
  `HTTP 403`: `method is allowed starting from the premium plus subscription`;
  это штатно переводит отправку ответа на LK/CDP fallback.
- `5` площадочных уведомлений Ozon отмечены прочитанными через официальный
  `/v2/chat/read` с payload
  `{"chat_id": "...", "from_message_id": ...}`.
- `scripts/messenger/ozon_send_messages_cdp.js` отправил `1` согласованный
  ответ покупателю через CDP-контур Vital Shevron `127.0.0.1:9544`; результат
  `sent_ok=1`, `skipped=0`, `blocker=""`.
- Verify `ozon_messenger_verify_after_apply_20260701T0721` через
  `/v3/chat/history` подтвердил: последнее сообщение в покупательском чате
  имеет `user.type = Seller`, текст совпадает с approved package, в
  примененных чатах `0` непрочитанных сообщений.
- Broad verify проверил первые `300` чатов: `0` строк с `unread_count > 0` и
  `0` фактически непрочитанных сообщений в историях. Общий
  `total_unread_count=1` остался нестрогим счетчиком Ozon и не считается
  блокером при чистых строках/историях.
- Важные уведомления из apply: договор Ozon с 1 июля 2026 года, новый
  инструмент `Сбор первых отзывов`, показатель FBO `Точность отгрузок`, акция
  `Максимальный бустинг` с 8 июля и автоматические изменения характеристик
  карточек за 28-29.06.2026. После отметки прочитанным такие темы нужно
  переносить в follow-up/recommendations, если требуют действий.

Подтвержденный apply/cleanup 2026-06-29:

- После owner approval по run `ozon_messenger_triage_20260629T0815`
  выполнена отметка `1` площадочного уведомления Ozon прочитанным через
  официальный `/v2/chat/read` с payload
  `{"chat_id": "...", "from_message_id": ...}`.
- Тип уведомления: `NotificationUser`, тема `FBO: создали автозаявку на вывоз`.
- Apply: `ozon_messenger_apply_20260629T0823`, результат `applied=1`,
  `ok_count=1`, `verify_unread_after=0`, `overall_status=ok`.
- Контрольный read-only: `ozon_messenger_verify_after_apply_20260629T0824`.
- Verify показал `320` проверенных чатов, `0` строк с `unread_count > 0` и
  `0` фактически непрочитанных сообщений в историях. Общий
  `total_unread_count_api=1` остался нестрогим счетчиком Ozon и не считается
  блокером при чистых строках/историях.
- Pending `ozon_messenger_triage_20260629T0815_pending` закрыт как
  `applied_verified_cleanup`.
- Итоговый отчет
  `data/runs/2026-06-29/inbox_reviews_questions_notifications_apply_20260629T0826/inbox_reviews_questions_notifications_apply_result.md`
  отправлен владельцу в Telegram вместе с кратким итогом.

Подтвержденный apply/cleanup 2026-06-26:

- После owner approval по run `ozon_messenger_triage_20260626T1042`
  выполнен apply `ozon_messenger_apply_20260626T1052`.
- 11 площадочных уведомлений Ozon отмечены прочитанными через официальный
  `/v2/chat/read` с payload
  `{"chat_id": "...", "from_message_id": ...}`; verify по
  `/v3/chat/history` показал `0` непрочитанных `NotificationUser`.
- По одному покупательскому сообщению-благодарности владелец согласовал
  закрытие без ответа. Официальный `/v2/chat/read` вернул `HTTP 403`
  Premium Plus для `Customer`-чата.
- Без отправки сообщения применен LK/CDP fallback: открыть целевой чат в
  правильном контуре Vital Shevron `127.0.0.1:9544`,
  `.sessions/ozon/chrome-profile`. После открытия повторный
  `/v3/chat/history` показал `0` непрочитанных `Customer`.
- Полный обход `/v3/chat/list` после cleanup: `318` чатов, `0` строк с
  `unread_count > 0`, фактических непрочитанных по историям `0`. Общий
  `total_unread_count=1` остался нестрогим счетчиком Ozon и не считается
  блокером, если строки списка и истории чистые.
- Pending `ozon_messenger_triage_20260626T1042_pending` закрыт как
  `applied_verified_cleanup`.

Подтвержденный apply/cleanup 2026-06-27:

- После owner approval по run `ozon_messenger_triage_20260627T1644`
  выполнена отметка `7` площадочных уведомлений Ozon прочитанными через
  официальный `/v2/chat/read` с payload
  `{"chat_id": "...", "from_message_id": ...}`.
- Локальный apply-скрипт завершился технической ошибкой при сохранении
  `summary.json` из-за JSON-сериализации счетчика с tuple-ключами уже после
  API-вызовов. В такой ситуации нельзя повторять write вслепую: сначала нужно
  выполнить read-only verify через `/v3/chat/list` и `/v3/chat/history`.
- Verify `ozon_messenger_verify_after_apply_20260627T165011` показал
  `319` проверенных чатов, `0` строк с `unread_count > 0` и `0` фактически
  непрочитанных сообщений в историях. Общий `total_unread_count=1` остался
  нестрогим счетчиком Ozon и не считается блокером при чистых строках/историях.
- Pending `ozon_messenger_triage_20260627T1644_pending` закрыт как
  `applied_verified_cleanup`; applied-marker сохранен в `data/approved/applied/`.

Важное ограничение LK-страницы Messenger: общий `document.body.innerText`
содержит не только открытый диалог, но и список соседних чатов. Поэтому нельзя
определять блокировку или статус целевого чата поиском фраз вроде
`Чат заблокирован` по всему body. Для apply проверять фактическое поле ввода
в открытом диалоге, состояние кнопки отправки и затем делать API-verify.

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

Штатный entrypoint уже зарегистрирован:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python -m seller_agent.cli \
  ozon-messenger-workflow --stage triage
```

На 2026-06-29 команда является безопасным task-runner каркасом и честно
возвращает `blocked: workflow_adapter_not_implemented`, пока не подключены
адаптеры triage/apply/verify/cleanup. Ее назначение - быть единым местом
дальнейшей реализации, а не продолжать ручные одноразовые скрипты.

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
5. Для старого хвоста диалогов, где последнее сообщение покупателя уже
   прочитано, не отправлять ответы автоматически. Сначала разделить:
   благодарности/завершенные диалоги, вопросы под заказ, вопросы наличия,
   претензии/заказы и правовые риски. Претензии, заказы и правовые вопросы
   разбирать вручную, без шаблонных ответов.
6. После owner approval ежедневная операция должна быть доведена до закрытия:
   отправить согласованные ответы, отметить согласованные уведомления
   прочитанными, выполнить verify и сохранить состояние обработанного хвоста.
   В следующем отчете уже обработанные уведомления и закрытые хвосты не
   показывать как новые задачи.

## Состояние обработанного хвоста

Если в daily triage обнаружены старые прочитанные диалоги, где последним
осталось сообщение покупателя, их нельзя бесконечно повторять в каждом новом
отчете. После разбора и согласования владельцем нужно сохранить state-файл без
секретов и без raw-переписки в `data/approved/closed/`.

Текущий формат:

```text
data/approved/closed/ozon_messenger_tail_<date>_<hash>.json
```

Минимальные поля:

- `source_run_id`, `refined_run_id`;
- `closed_without_answer`;
- `standard_reply_possible_but_old`;
- `needs_stock_check_before_reply`;
- `manual_order_claim_review`;
- `manual_legal_review`;
- `manual_unclear_context`;
- `reporting_rule`.

Правило для следующих отчетов:

- `closed_without_answer` и `standard_reply_possible_but_old` не выводить как
  новые задачи;
- `needs_stock_check_before_reply`, `manual_order_claim_review`,
  `manual_legal_review`, `manual_unclear_context` выводить только как
  отдельный backlog/ручной риск, а не как новые непрочитанные вопросы;
- новые `Customer is_read=false` всегда показывать независимо от state-файла.

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
