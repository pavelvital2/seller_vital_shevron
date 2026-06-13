# Подключение к ЛК Ozon и WB

Назначение: постоянный порядок подключения `seller_takterra` к личным кабинетам
Ozon Seller и Wildberries. Инструкция собрана по проверенным схемам из
read-only проектов:

- `/home/pavel/projects/seller_ozon/AGENTS.md`
- `/home/pavel/projects/seller_ozon/SESSION.md`
- `/home/pavel/projects/seller_ozon/LOGIN_INSTRUCTION.md`
- `/home/pavel/projects/seller_ozon_vitalsewing/AGENTS.md`
- `/home/pavel/projects/seller_ozon_vitalsewing/SESSION.md`
- `/home/pavel/projects/seller_ozon_vitalsewing/LOGIN_INSTRUCTION.md`
- `/home/pavel/projects/seller_wb/AGENTS.md`
- `/home/pavel/projects/seller_wb/00_raw_snapshots/wb-persistent-browser.md`
- `/home/pavel/projects/seller_wb/scripts/common/wb_preflight.py`

Содержимое cookies, токенов, storage state, OTP/SMS/email-кодов и API-ключей в
эту инструкцию не записывать.

## Принцип

ЛК подключается не ради ручной работы в магазинах, а как инфраструктура будущего
проекта управления TAKTERRA:

```text
browser session -> read-only проверка -> dry-run -> approval -> apply -> verify
```

Приоритет остается за официальными API. ЛК использовать там, где API не покрывает
операцию, требует визуальной проверки или нужен браузерный токен кабинета.

Если URL Ozon содержит `__rr=1`, это фиксировать как `rrMode`, но не считать
самостоятельным доказательством блокировки. Блокировкой считать только сочетание
`rrMode` с отсутствием нормальной оболочки кабинета или явными текстами ошибок:
`Похоже, нет соединения`, `Доступ ограничен`, `Инцидент`, `captcha`,
`access denied`.

## Что важно из старых проектов

### Ozon

Проверенная модель:

- отдельный persistent Chrome profile;
- отдельный Playwright `storageState` как резервный снимок;
- ручной OTP-вход только в этот persistent profile;
- отдельный CDP-порт для агента;
- keepalive/watchdog, который продлевает сессию и не закрывает общий браузер;
- desktop Chrome user-agent, locale `ru-RU`, timezone `Europe/Moscow`;
- запрет на повторное "долбление" OTP при блокировке или сетевой защите Ozon.

В старых проектах ссылки на эту модель находятся в корневых `AGENTS.md`:

- `SESSION.md` - подключение, persistent profile, CDP, keepalive;
- `LOGIN_INSTRUCTION.md` - ручной вход и правила работы с OTP.

### Wildberries

Проверенная модель:

- persistent Chromium profile;
- экспортированный Playwright `storageState`;
- первый вход или восстановление через headful-окно и `--keep-open`;
- обновление state без перезаписи, если открылась страница авторизации;
- refresh отдельно для `seller.wildberries.ru` и `cmp.wildberries.ru`;
- preflight без вывода токенов.

В старом WB-проекте ссылки находятся в корневом `AGENTS.md`:

- `00_raw_snapshots/wb-persistent-browser.md` - сессия и вход;
- `scripts/sessions/wb_persistent_session.js` - обновление/экспорт сессии;
- `scripts/sessions/wb_daily_session_refresh.sh` - ежедневный refresh;
- `scripts/common/wb_preflight.py` - проверка токена, state, профиля и tooling.

## Целевые локальные пути TAKTERRA

В `seller_takterra` сессионные файлы должны жить только в локальной секретной
зоне проекта:

```text
.sessions/
  ozon/
    chrome-profile/
    ozon_seller_storage_state.json
    session_refresh_logs/
  wb/
    browser-profile/
    wb_storage_state.json
    session_refresh_logs/
tmp/
  auth/
```

Эти пути должны быть исключены из git. В них нельзя сохранять отчеты, которые
будут отправляться в чат. Отчеты и безопасные статусы сохранять в:

```text
data/runs/YYYY-MM-DD/
data/reports/YYYY-MM-DD/
```

## Что хранится в storage state

Локальный `storage_state` - это Playwright-снимок браузерной авторизации.
Обычно внутри:

- cookies по доменам маркетплейса;
- localStorage/session-like данные по origin;
- служебные признаки авторизованного состояния;
- иногда короткоживущие браузерные токены кабинета, если сайт хранит их в
  web storage.

Обычно там нет пароля в открытом виде, но файл все равно считается секретным:
его достаточно, чтобы восстановить сессию или выполнять действия от имени
авторизованного пользователя, пока маркетплейс принимает эти cookies/state.

Проектные файлы:

```text
.sessions/ozon/ozon_seller_storage_state.json
.sessions/wb/wb_storage_state.json
```

Правила:

- права файла `600`;
- не отправлять в чат;
- не коммитить;
- не включать содержимое в отчеты;
- обновлять после успешного входа или keepalive;
- при компрометации считать сессию скомпрометированной и делать logout/новый
  вход.

## Порядок действий

### 1. Подготовить сессионный контур

1. Проверить, что работа идет из `/home/pavel/projects/seller_takterra`.
2. Убедиться, что старые проекты используются только для чтения.
3. Добавить в `.gitignore` локальные сессионные зоны:

```text
.sessions/
tmp/
```

4. Создать или подготовить к созданию папки:

```text
.sessions/ozon/
.sessions/wb/
tmp/auth/
scripts/sessions/
scripts/common/
```

5. Выбрать отдельные CDP-порты, чтобы не конфликтовать со старыми проектами:

```text
Ozon TAKTERRA: 9444
WB TAKTERRA: при необходимости 9445
```

Перед запуском проверять, что порт свободен:

```bash
ss -ltnp | rg ':9444|:9445'
```

### 2. Перенести не сессии, а скриптовую модель

По умолчанию нельзя копировать из старых проектов:

- `.ozon-session/`;
- `.wb-storage-state.json`;
- cookies;
- API-токены;
- браузерные профили;
- файлы с кодами входа.

Исключение возможно только по явному разрешению владельца, с отдельным отчетом,
без вывода секретов и без изменений в старом проекте.

Можно адаптировать код и структуру скриптов под новые пути TAKTERRA:

```text
scripts/sessions/open_ozon_seller_agent.sh
scripts/sessions/ozon_seller_persistent_session.js
scripts/sessions/ozon_seller_interactive_login.js
scripts/sessions/ozon_import_cookies_check.js
scripts/sessions/ozon_session_refresh.sh
scripts/sessions/ozon_session_keepalive_cdp.js
scripts/sessions/ozon_keep_dashboard_open.js
scripts/sessions/start_ozon_keeper.sh
scripts/sessions/stop_ozon_keeper.sh
scripts/sessions/start_ozon_session_watchdog.sh
scripts/sessions/wb_persistent_session.js
scripts/sessions/wb_daily_session_refresh.sh
scripts/common/lk_preflight.py
```

Рекомендация: сначала сделать единый `lk_preflight.py`, который проверяет:

- наличие секретных файлов без вывода содержимого;
- наличие `.sessions/ozon` и `.sessions/wb`;
- доступность CDP-портов;
- наличие Playwright/tooling;
- статус Ozon dashboard;
- статус WB seller/cmp;
- что виден именно магазин TAKTERRA.

### 3. Подключить Ozon Seller

Перед подготовкой или запуском подключения обязательно перечитать корневой
`AGENTS.md`. Если в процессе подключения меняются скрипты, карты или правила,
обновить постоянные документы в том же рабочем цикле.

1. Запустить persistent Chrome profile TAKTERRA с отдельным user-data-dir:

```text
.sessions/ozon/chrome-profile
```

2. Открывать Ozon не с дефолтным `HeadlessChrome`, а как обычный desktop Chrome:

```text
locale: ru-RU
timezone: Europe/Moscow
desktop Chrome user-agent
```

3. Проверенная точка входа:

```text
https://seller.ozon.ru/app/dashboard/main
```

Если сессии нет, идти на:

```text
https://seller.ozon.ru/app/registration/signin
```

4. Вход по коду выполнять строго так:

- агент доходит до видимого поля OTP;
- только после этого пишет владельцу, что нужен свежий код;
- вводится только код, полученный после этого запроса;
- если почта/логин были введены заново, старый код не использовать.

5. После входа проверить:

- открыт `https://seller.ozon.ru/app/dashboard/main`;
- в кабинете виден магазин/аккаунт TAKTERRA;
- нет признаков логина, блокировки, `Доступ ограничен`, `Похоже, нет соединения`
  или `Инцидент`;
- открываются минимум разделы `Товары`, `Цены`, `Аналитика`.

6. Экспортировать резервный state:

```text
.sessions/ozon/ozon_seller_storage_state.json
```

7. Оставить активный persistent browser на CDP:

```text
http://127.0.0.1:9444
```

8. Настроить keepalive/watchdog только после успешного входа и проверки магазина.
Watchdog не должен закрывать общий браузер и не должен повторять OTP при
блокировке.

9. Если нужен постоянный открытый CDP-контур, запустить keeper:

```bash
scripts/sessions/start_ozon_keeper.sh
```

Проверка:

```bash
OZON_CDP_URL=http://127.0.0.1:9444 OZON_EXPECTED_STORE=TAKTERRA \
  node scripts/sessions/ozon_session_keepalive_cdp.js
```

Остановка:

```bash
scripts/sessions/stop_ozon_keeper.sh
```

10. Включить автоматический refresh по проектному таймеру:

```bash
scripts/sessions/start_ozon_session_watchdog.sh
```

По умолчанию watchdog запускает `scripts/sessions/ozon_session_refresh.sh`
каждые 30 минут. Интервал можно изменить переменной:

```bash
OZON_WATCHDOG_INTERVAL_SECONDS=1800 scripts/sessions/start_ozon_session_watchdog.sh
```

Остановка:

```bash
scripts/sessions/stop_ozon_session_watchdog.sh
```

Refresh работает так:

- если CDP `127.0.0.1:9444` живой, выполняет `cdp_keepalive` через
  `scripts/sessions/ozon_session_keepalive_cdp.js`;
- если CDP не живой и есть `tmp/auth/ozon_user_cookies.json`, импортирует cookies в
  `.sessions/ozon/chrome-profile` и после успеха удаляет временный файл;
- если CDP и cookie-файла нет, пробует продлить текущую persistent-сессию;
- после каждого успешного входа/импорта экспортирует свежий
  `.sessions/ozon/ozon_seller_storage_state.json`;

### Восстановление Ozon по почте

Если `status-preflight` показывает `ozon_keepalive: error` и `Login required`,
вход восстанавливать через штатный интерактивный скрипт:

```bash
xvfb-run -a node scripts/sessions/ozon_seller_interactive_login.js \
  --email '<owner_ozon_email>' \
  --expected-store TAKTERRA \
  --max-codes 3 \
  --precheck-dashboard
```

Порядок:

1. Перед входом остановить Ozon watchdog и keeper, чтобы они не держали тот же
   Chrome profile.
2. Запустить интерактивный вход по почте.
3. Дождаться состояния `READY_FOR_CODE`.
4. Запросить у владельца свежий код и ввести только его.
5. После `LOGIN_SUCCESS` убедиться, что `expectedStoreFound: true` и
   `stateExported: true`.
6. Запустить Ozon keeper и Ozon watchdog обратно.
7. Выполнить `status-preflight`.

Если на странице появляется баннер cookies, нажать видимую кнопку
`Принять`/`Accept` до продолжения входа. Это не marketplace write-операция, но
факт такого действия нужно фиксировать в отчете/инструкции, если оно влияло на
успешность входа.

### Внештатная успешная ситуация: перенос полного Ozon-профиля

2026-06-10 владелец явно разрешил перенести рабочую Ozon-сессию из
read-only проекта `/home/pavel/projects/seller_ozon` и затем переключить магазин
на TAKTERRA.

Порядок, который сработал:

1. Перечитать `AGENTS.md` и этот runbook.
2. Проверить, что старый проект не изменяется.
3. Скопировать только полный Chrome profile в локальную секретную зону
   `seller_takterra`, исключая lock-файлы:

```bash
rsync -a --delete \
  --exclude='Singleton*' \
  --exclude='DevToolsActivePort' \
  --exclude='LOCK' \
  --exclude='*.lock' \
  /home/pavel/projects/seller_ozon/.ozon-session/chrome-profile/ \
  .sessions/ozon/chrome-profile/
```

4. Если есть старый storage state, скопировать его только как локальный
   секретный резерв в `.sessions/ozon/ozon_seller_storage_state.json` и поставить
   права `600`.
5. Запустить keeper на отдельном TAKTERRA-порту:

```bash
OZON_REMOTE_DEBUGGING_PORT=9444 scripts/sessions/start_ozon_keeper.sh
```

6. Если перенесенная сессия открыла старый магазин, переключить выбранную
   компанию на TAKTERRA без вывода значения идентификатора в логи. Проверенный
   способ: выставить cookie `sc_company_id` для доменов Ozon значением Ozon
   Seller Client-Id TAKTERRA из секретного API-файла и перезагрузить dashboard.
7. Проверить через CDP:

```bash
OZON_CDP_URL=http://127.0.0.1:9444 OZON_EXPECTED_STORE=TAKTERRA \
  node scripts/sessions/ozon_session_keepalive_cdp.js
```

8. Успешная проверка должна подтвердить dashboard, analytics, products и prices:
   `expectedStoreFound=true`, `blocked=false`, `needsLogin=false`,
   `stateExported=true`.
9. После этого включить watchdog:

```bash
OZON_WATCHDOG_INTERVAL_SECONDS=2700 \
OZON_REMOTE_DEBUGGING_PORT=9444 \
OZON_EXPECTED_STORE=TAKTERRA \
  scripts/sessions/start_ozon_session_watchdog.sh
```

Нюанс по браузеру: обычный Google Chrome из `open_ozon_seller_agent.sh` полезен
для ручного визуального входа, но в проверенном переносе устойчивее оказался
keeper на `chrome-headless`/CDP, как в старом рабочем проекте.

### Ошибки входа и cookie-only восстановление

Скрипт подключения:

- при требовании нового OTP/SMS останавливается со статусом `needsLogin`, а не
  пытается подбирать или повторно вводить коды;
- при `Похоже, нет соединения`, `Доступ ограничен`, `Инцидент`, `captcha`,
  `access denied` или `rrMode` без оболочки кабинета останавливается и ждет
  восстановления сессии.

Если Ozon до ввода почты или кода показывает `Похоже, нет соединения`,
`Доступ ограничен`, `Инцидент` или `rrMode` без оболочки кабинета, не продолжать
OTP-сценарий. Нужны свежие cookies владельца из уже открытого рабочего браузера
или полный рабочий persistent profile по отдельному разрешению владельца. Для
TAKTERRA использовать только временный файл:

```text
tmp/auth/ozon_user_cookies.json
```

Импортировать cookies вручную можно так:

```bash
xvfb-run -a node scripts/sessions/ozon_import_cookies_check.js --cookie-file tmp/auth/ozon_user_cookies.json --headful
```

Скрипт не должен печатать значения cookies. После успешного импорта он экспортирует
state в `.sessions/ozon/ozon_seller_storage_state.json` и удаляет cookie-файл,
если файл лежит в `tmp/auth/`.

Если после импорта cookies страница открывается с `rrMode` и текстом ошибки или
без нормальной оболочки кабинета, cookies считать недостаточными или устаревшими
для текущего браузерного контура. В этом случае удалить временный cookie-файл и
нерабочий локальный профиль TAKTERRA, а у владельца запросить cookies из
браузера, где dashboard Ozon Seller уже открыт штатно.

Важное наблюдение от 2026-06-10: старый проект `seller_ozon` может спокойно
открывать ЛК через уже живой CDP `127.0.0.1:9222`, потому что там работает
полный прогретый persistent profile:

```text
/home/pavel/projects/seller_ozon/.ozon-session/chrome-profile
```

и watchdog/keeper, запущенный с 2026-06-07. Это не равно импорту одних cookies
в новый профиль TAKTERRA. Полный профиль содержит состояние браузера, которое
cookie-файл не переносит: local storage, IndexedDB, service workers, device
state и прогретый антифрод-контур. Поэтому cookie-only восстановление нового
профиля может получать `__rr=1`, даже если старый CDP открывает ЛК.

По правилам проекта нельзя копировать старый `.ozon-session/` или подключать его
как рабочий контур TAKTERRA без отдельного явного разрешения владельца.

Лучший cookie-формат для восстановления после `rrMode` с ошибкой - полный
request header `Cookie` из Network-запроса успешной вкладки:

```text
GET https://seller.ozon.ru/app/dashboard/main
Request Headers -> Cookie
```

Допустимы также JSON-форматы, но отдельные 1-2 cookie-объекта могут быть
недостаточны. Если передается JSON, импортёр TAKTERRA поддерживает:

- один JSON-объект с cookie map;
- несколько JSON-документов подряд;
- массив browser-extension cookie objects;
- object с полем `cookies`;
- raw cookie header строку.

После трех попыток 2026-06-10 cookie-only восстановление нового чистого профиля
TAKTERRA считать нерабочим, если оно снова дает `rrMode` с ошибкой или без
оболочки кабинета. Следующие варианты:

```text
1. Полноценный ручной вход в новый TAKTERRA persistent profile.
2. Создание долгоживущего TAKTERRA keeper/profile и дальнейшее продление через
   watchdog.
3. Перенос или использование старого живого профиля/CDP только после отдельного
   явного разрешения владельца, потому что это перенос секретной сессии.
```

### 4. Подключить Wildberries

1. Запустить WB persistent profile:

```text
.sessions/wb/browser-profile
```

2. Проверенная точка входа:

```text
https://seller.wildberries.ru/
```

3. Отдельно проверить домен продвижения:

```text
https://cmp.wildberries.ru/campaigns/list
```

4. Если WB просит авторизацию, работать только в одном открытом браузерном
процессе:

- ввести телефон один раз;
- дождаться SMS;
- ввести SMS один раз;
- если после SMS нужен email-код, не закрывать браузер и не перезапускать
  скрипт;
- ввести email-код в ту же открытую форму;
- после входа сразу экспортировать state.

Проверенный проектный сценарий:

```bash
node scripts/sessions/wb_auth_once_file_code.js <10_цифр_номера_без_7>
```

Скрипт ждет SMS-код в:

```text
tmp/auth/wb-auth-once/sms-code.txt
```

Если после SMS WB просит код с почты, скрипт остается в той же открытой форме и
ждет второй код в:

```text
tmp/auth/wb-auth-once/email-code.txt
```

Коды не записывать в отчеты и постоянные документы.

5. Экспортировать state только если текущий URL относится к `seller.wildberries.ru`
или `cmp.wildberries.ru` и не является страницей auth/login/passport/signin:

```text
.sessions/wb/wb_storage_state.json
```

6. Проверить:

- виден нужный продавец TAKTERRA/WB: `ИП Рантусова`;
- открывается `seller.wildberries.ru`;
- открывается `cmp.wildberries.ru/campaigns/list`;
- state не перезаписывается, если открылась страница авторизации.

Если после входа активен другой продавец, открыть меню продавца в правом верхнем
углу и выбрать `ИП Рантусова`. После переключения снова экспортировать state и
проверить, что в верхней кнопке активного продавца виден `ИП Рантусова`.

7. Настроить refresh для двух доменов:

```text
seller.wildberries.ru
cmp.wildberries.ru
```

Проверенный refresh:

```bash
scripts/sessions/wb_daily_session_refresh.sh
```

Логи:

```text
.sessions/wb/session_refresh_logs/
```

### 5. Зафиксировать результат подключения

После успешного подключения каждого ЛК создать безопасный отчет без секретов:

```text
data/runs/YYYY-MM-DD/lk_connection_check_<run_id>/summary.md
```

В отчете фиксировать только:

- дата и время проверки;
- маркетплейс;
- статус входа;
- видимый магазин;
- проверенные URL;
- наличие state/profile без содержимого;
- найденные ошибки;
- следующие действия.

### 6. Обновить постоянные документы

После подключения или обнаружения новых разделов обновить:

```text
AGENTS.md
data/planning/project_map.md
data/planning/ozon_cabinet_map.md
data/planning/wb_cabinet_map.md
data/planning/marketplace_control_bot_discussion.md
```

Если во время подключения появилась внештатная ситуация и ее удалось решить,
обязательно дополнить эту инструкцию.

## Единый session manager

После стабилизации первичных входов управление сессиями перенесено в CLI:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli sessions status
PYTHONPATH=src python3 -m takterra_agent.cli sessions start --marketplace ozon
PYTHONPATH=src python3 -m takterra_agent.cli sessions stop --marketplace wb
PYTHONPATH=src python3 -m takterra_agent.cli sessions restart --marketplace all
```

Подробная инструкция:

```text
data/planning/session_manager_runbook.md
```

## Восстановление Ozon через единый сценарий

Для восстановления Ozon больше не дергать keeper/watchdog вручную. Использовать
сценарий:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli restore-ozon-session --dry-run
PYTHONPATH=src python3 -m takterra_agent.cli restore-ozon-session --email <email>
```

Сценарий:

- ставит локальный lock;
- останавливает Ozon watchdog;
- останавливает Ozon keeper;
- запускает интерактивный вход;
- после завершения запускает keeper/watchdog обратно;
- сохраняет безопасный summary без cookies, storage state и кодов.

## Systemd User Timers

2026-06-11 session refresh переведен с ручных `nohup` bash-loop процессов на
`systemd --user`:

```text
takterra-ozon-keeper.service
takterra-ozon-session-refresh.timer
takterra-wb-session-refresh.timer
```

Unit-файлы находятся в:

```text
deploy/systemd/user/
```

Текущая установка выполнена в:

```text
/home/pavel/.config/systemd/user/
```

Проверка:

```bash
systemctl --user list-timers 'takterra-*' --no-pager
PYTHONPATH=src python3 -m takterra_agent.cli sessions status
PYTHONPATH=src python3 -m takterra_agent.cli status-preflight
```

Контрольный результат после переключения:

```text
sessions_status_20260611T000502: overall_status ok
status_preflight_20260611T000512: overall_status ok
```

## Минимальный критерий готовности

Подключение считается готовым, когда:

- Ozon dashboard открывается через TAKTERRA persistent profile;
- WB seller dashboard открывается через TAKTERRA persistent profile;
- WB promotion domain `cmp.wildberries.ru` открывается через тот же контур;
- оба кабинета подтверждены как TAKTERRA;
- state-файлы экспортированы в `.sessions/`;
- сессионные файлы исключены из git;
- создан безопасный отчет;
- карты ЛК обновлены новыми ссылками.

## Восстановление работоспособности 2026-06-12

Контур ЛК восстановлен после истечения Ozon-сессии и сбоя интерактивного входа
на сервере без `$DISPLAY`.

Что проверено:

```text
Ozon Seller API: ok
Ozon Performance API: ok
WB API: ok
master catalog: ok, 203 rows, 203 matched rows
Ozon LK/CDP: ok, TAKTERRA найден, stateExported true
WB LK: ok, cmp.wildberries.ru подтверждает ИП Рантусова, stateExported true
```

Контрольные запуски:

```text
restore_ozon_session_20260612T174933
sessions_status_20260612T175227
status_preflight_20260612T175227
```

Файл безопасного отчета:

```text
data/runs/2026-06-12/project_restore_20260612T175400/project_restore_report.md
```

Важное правило после инцидента: если при Ozon restore появляется
`ProcessSingleton` или профиль занят, не удалять файлы профиля вручную. Нужно
использовать штатный `restore-ozon-session`, потому что он сначала останавливает
`systemd --user`/legacy keeper, затем проверяет живые PID на TAKTERRA Ozon
profile и только после этого удаляет stale `Singleton*`.

## Рекомендации по автоматизации

1. Сделать общий `lk_preflight.py` до первого полноценного подключения.
2. Вынести все пути и порты в `.env.example`, реальные значения хранить в `.env`.
3. Добавить команду task-runner:

```text
python -m takterra_agent.cli lk-preflight
```

4. После стабилизации сессий добавить read-only команду будущего бота:

```text
/status lk
```

Она должна показывать только безопасные статусы: Ozon/WB logged-in, последний
refresh, видимый магазин, ошибки подключения, без cookies/token/storageState.
