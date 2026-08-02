# Session Manager Runbook

## Команды

```bash
PYTHONPATH=src python3 -m seller_agent.cli sessions status
PYTHONPATH=src python3 -m seller_agent.cli sessions start --marketplace ozon
PYTHONPATH=src python3 -m seller_agent.cli sessions stop --marketplace ozon
PYTHONPATH=src python3 -m seller_agent.cli sessions restart --marketplace ozon
```

## Systemd

Dry-run:

```bash
PYTHONPATH=src python3 -m seller_agent.cli install-session-systemd
```

Apply после проверки:

```bash
PYTHONPATH=src python3 -m seller_agent.cli install-session-systemd --apply --switch
```

Unit names:

```text
vital-shevron-ozon-keeper.service
vital-shevron-ozon-session-refresh.service
vital-shevron-ozon-session-refresh.timer
vital-shevron-wb-session-refresh.service
vital-shevron-wb-session-refresh.timer
```

## Таймеры

- Ozon refresh: 30 минут.
- WB refresh: 60 минут.

Обе oneshot refresh-службы захватывают только на время операции канонический
runtime lease через `scripts/systemd/with_resource_lease.py --lk-profile ...`:

```text
Ozon: lk:ozon:profile:chrome-profile
WB:   lk:wb:profile:browser-profile
```

Те же ключи используют Job Worker consumers соответствующего профиля. Ozon
keeper остается постоянно работающим browser host и не оборачивается в lease:
иначе все короткие CDP operations были бы заблокированы на весь срок жизни
Chrome. Ручные `ozon_session_refresh.sh` и `wb_daily_session_refresh.sh` сами
используют тот же короткий wrapper. Изменение unit-файлов в репозитории не
означает deployment; установка и перезапуск выполняются отдельным этапом.
Raw `*_keepalive*.js` остаются внутренней реализацией и напрямую не
запускаются: для ручной операции использовать только lease-aware shell
wrapper или зарегистрированную Job Worker задачу.

## Ozon Keeper

Keeper Ozon запускает отдельный CDP на `127.0.0.1:9544` и перед открытием ЛК
засевает persistent profile из
`.sessions/ozon/ozon_seller_storage_state.json`, если этот файл существует.
Это нужно, чтобы импортированные cookies/storage state были доступны Chrome
keeper-процессу, а не только разовому Playwright-контексту.

Проверка после запуска:

```bash
scripts/sessions/ozon_session_refresh.sh
PYTHONPATH=src python3 -m seller_agent.cli sessions status --marketplace ozon
```

С 2026-06-20 Ozon CDP-сценарии Vital Shevron используют обязательный guard
`scripts/lib/ozon_cdp_guard.js`. Перед `chromium.connectOverCDP` он проверяет,
что `OZON_CDP_URL` указывает на локальный порт `9544`, а слушающий Chrome
запущен с `--user-data-dir=/home/pavel/projects/seller_vital_shevron/.sessions/ozon/chrome-profile`.
Если агент случайно передаст порт TAKTERRA `9444` или чужой профиль, сценарий
должен завершиться с `OZON_CDP_CONTOUR_MISMATCH` до любых действий в ЛК.

## Восстановление Ozon-сессии

Dry-run:

```bash
PYTHONPATH=src python3 -m seller_agent.cli restore-ozon-session --dry-run
```

Фактическое восстановление:

```bash
PYTHONPATH=src python3 -m seller_agent.cli restore-ozon-session
```

Сценарий интерактивно запрашивает email и коды входа. Коды не сохранять в
файлы проекта, отчеты и сообщения.

Если проект еще не переведен на `systemd --user` и команда завершилась
`overall_status: warning` из-за отсутствующих unit-файлов
`vital-shevron-ozon-*`, но сам интерактивный вход вернул `LOGIN_SUCCESS`, нужно
поднять legacy keeper/watchdog вручную:

```bash
PYTHONPATH=src python3 -m seller_agent.cli sessions start --marketplace ozon
scripts/sessions/ozon_session_refresh.sh
PYTHONPATH=src python3 -m seller_agent.cli sessions status --marketplace ozon
```

Критерий готовности после восстановления:

- `expectedStoreFound: true`;
- `stateExported: true`;
- `sessions status --marketplace ozon` возвращает `overall_status: ok`;
- `refresh.ok: true`;
- `values_printed: false`.

### Проверенное восстановление 2026-06-18

- Restore run: `restore_ozon_session_20260618T0647`.
- Результат интерактивного входа: `LOGIN_SUCCESS`, `expectedStoreFound: true`,
  `stateExported: true`.
- Restore завершился `warning`, потому что `systemd --user` units
  `vital-shevron-ozon-*` не установлены. Это штатно для текущего legacy-контура.
- После restore нужно было вручную выполнить legacy-подъем:
  `sessions start --marketplace ozon`, затем
  `node scripts/sessions/ozon_session_keepalive_cdp.js`.
- Итоговая проверка: `sessions_status_20260618T064847` вернул
  `overall_status: ok`; `status_preflight_20260618T0649` вернул
  `overall_status: ok`.
- Секреты, cookies, storage state и код входа в документы не записывались.

### Проверенное восстановление 2026-06-20 для Ozon Messenger

- Restore run: `restore_ozon_session_for_messenger_retry_20260620T0845`.
- Результат интерактивного входа: `LOGIN_SUCCESS`, `expectedStoreFound: true`,
  `stateExported: true`.
- Restore завершился `warning`, потому что `systemd --user` units
  `vital-shevron-ozon-*` не установлены. Для текущего legacy-контура это
  штатно: после restore нужно вручную поднять keeper/watchdog.
- После `sessions start --marketplace ozon` watchdog сразу запустил refresh, а
  keeper уже держал Chrome profile
  `.sessions/ozon/chrome-profile`. Поэтому refresh получил ошибку Chrome
  `Failed to create a ProcessSingleton for your profile directory`: это
  означает, что профиль Vital Shevron занят уже работающим процессом Vital
  keeper. Это не ошибка магазина и не смешивание с TAKTERRA: у TAKTERRA должен
  быть и был отдельный CDP-порт и отдельный профиль браузера.
- Правильное восстановление после такой ситуации: подождать, пока keeper
  начнет слушать `127.0.0.1:9544`, проверить `ps`/`ss`, что порт и
  `user-data-dir` принадлежат Vital Shevron, затем выполнить
  `node scripts/sessions/ozon_session_keepalive_cdp.js`.
- Итоговая проверка:
  `sessions_status_after_keepalive_messenger_retry_20260620T0849` вернул
  `overall_status: ok`; `refresh.ok: true`; `keeper.status: ok`;
  `watchdog.status: ok`; `cdp.status: ok`.

Текущий рабочий контур может держаться legacy watchdog-процессом
`scripts/sessions/start_ozon_session_watchdog.sh` с интервалом `1800` секунд.
Перевод на `systemd --user` timers остается отдельным техническим этапом после
подключения WB-сессии.

### Внештатная ситуация после reboot 2026-07-04

Симптом:

- после перезагрузки Seller VPS Telegram-бот Vital Shevron поднялся сам, но
  Ozon CDP `127.0.0.1:9544` не слушал;
- `sessions status --marketplace ozon` показывал stale pid-файлы keeper и
  watchdog: `process not running`;
- `systemctl --user list-unit-files 'vital-shevron-*'` показывал enabled только
  `vital-shevron-telegram-bot.service`;
- `vital-shevron-ozon-keeper.service`,
  `vital-shevron-ozon-session-refresh.timer` и
  `vital-shevron-wb-session-refresh.timer` отсутствовали (`not-found`);
- cron-записей, которые поднимали бы LK-сессии Vital Shevron после reboot, не
  было.

Причина:

- Ozon/WB LK-сессии Vital Shevron были запущены legacy фоновыми процессами
  через pid-файлы, а не установленными enabled `systemd --user` units/timers;
- после reboot такие процессы исчезают, pid-файлы остаются, но автозапуска
  нет;
- `deploy/systemd/user/` содержит нужные unit-файлы, но они не были
  установлены в `~/.config/systemd/user`.

Дополнительный нюанс при переводе на systemd:

- значения `Environment=` с пробелами должны быть в кавычках;
- неверно: `Environment=OZON_EXPECTED_STORE=Vital Shevron`;
- верно: `Environment="OZON_EXPECTED_STORE=Vital Shevron"`;
- иначе systemd режет значение до `Vital`, а `ozon_session_keepalive_cdp.js`
  падает с `Expected Vital Shevron store marker not found`.

Ручное восстановление:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli sessions start --marketplace ozon

scripts/sessions/ozon_session_refresh.sh

PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli sessions status --marketplace ozon
```

Проверенный результат 2026-07-04:

- `127.0.0.1:9544` слушает Chrome Vital Shevron;
- `contourGuard.ok: true`;
- profile:
  `/home/pavel/projects/seller_vital_shevron/.sessions/ozon/chrome-profile`;
- `expectedStoreFound: true`;
- `stateExported: true`;
- `sessions_status_20260704T085911` вернул `overall_status: ok`.

Постоянное исправление:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli install-session-systemd --apply --switch

systemctl --user list-unit-files 'vital-shevron-*'
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli sessions status
```

После этого Ozon keeper и Ozon/WB refresh timers должны подниматься через
`systemd --user` после reboot. До выполнения `--apply --switch` автоматический
подъем LK-сессий после перезагрузки не гарантируется.

Проверенный результат после исправления unit quoting и `--apply --switch`
2026-07-04:

- `vital-shevron-ozon-keeper.service`: `enabled`, active/running;
- `vital-shevron-ozon-session-refresh.timer`: `enabled`, active/waiting;
- `vital-shevron-wb-session-refresh.timer`: `enabled`, active/waiting;
- `sessions_status_20260704T090654`: `overall_status: ok`, sources
  Ozon/WB watchdog `systemd`;
- `status_preflight_20260704T090654`: `overall_status: ok`;
- Ozon CDP guard подтвердил `127.0.0.1:9544` и профиль
  `.sessions/ozon/chrome-profile`;
- WB keepalive подтвердил продавца `ИП Витальская И. П.`.

### Внештатная ситуация с user systemd linger 2026-07-08

Симптом:

- unit-файлы `vital-shevron-ozon-keeper.service`,
  `vital-shevron-ozon-session-refresh.timer` и
  `vital-shevron-wb-session-refresh.timer` есть в
  `~/.config/systemd/user` и имеют состояние `enabled`;
- после закрытия пользовательского user manager или после reboot
  `systemctl --user` возвращает ошибку подключения к bus, `/run/user/1000/bus`
  отсутствует, а `sessions status --marketplace ozon` показывает
  `CDP port is not listening`;
- в `journalctl` видно, что `user@1000.service` остановил
  `vital-shevron-ozon-keeper.service` и session refresh timers.

Причина:

- для пользователя `pavel` не был включен linger;
- `systemd --user` завершался вместе с пользовательской сессией, поэтому
  enabled user units не являлись постоянным автоподъемом.

Проверка:

```bash
loginctl show-user pavel -p Linger -p State -p RuntimePath
ls -ld /run/user/1000 /run/user/1000/bus
XDG_RUNTIME_DIR=/run/user/1000 \
DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
  systemctl --user list-unit-files 'vital-shevron-*'
```

Постоянное исправление:

```bash
loginctl enable-linger pavel
loginctl show-user pavel -p Linger -p State -p RuntimePath
```

После этого user manager должен переходить в `State=lingering`, создавать
`/run/user/1000/bus`, а enabled Ozon/WB user units должны подниматься без
активной SSH/терминальной сессии. После восстановления авторизованной Ozon
сессии нужно снова запустить Ozon units/timers и проверить:

```bash
XDG_RUNTIME_DIR=/run/user/1000 \
DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
  systemctl --user start vital-shevron-ozon-keeper.service \
  vital-shevron-ozon-session-refresh.timer

scripts/sessions/ozon_session_refresh.sh
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli sessions status --marketplace ozon
```

### Внештатная ситуация Ozon web ABT challenge 2026-07-13

Симптом:

- `status-preflight` показывает Ozon Seller API, Ozon Performance API и WB API
  `ok`;
- `127.0.0.1:9544` слушает Chrome Vital Shevron, CDP guard подтверждает
  правильный порт и профиль
  `.sessions/ozon/chrome-profile`;
- `ozon_session_keepalive_cdp.js` на dashboard, analytics, products и prices
  возвращает title `Похоже, нет соединения`, `expectedStoreFound=false`,
  `needsLogin=false`, `stateExported=false`;
- `restore-ozon-session --email ...` останавливается до ввода кода на
  `BLOCKED_BEFORE_LOGIN`, а прямой
  `ozon_seller_interactive_login.js` без dashboard-precheck останавливается на
  `BLOCKED_BEFORE_EMAIL`;
- smoke на свежем временном профиле тоже получает
  `BLOCKED_BEFORE_EMAIL`, поэтому проблема не в поврежденном Chrome profile.

Подтверждение:

- browser/network smoke по свежему профилю получил `HTTP 403` на
  `https://seller.ozon.ru/app/registration/signin?__rr=1`;
- страница показывает Ozon ABT challenge: `Похоже, нет соединения`,
  рекомендацию выключить VPN/сменить сеть и incident id;
- `valuesPrinted=false`, cookies/storage state/коды не выводились.

Вывод:

- это блокировка Ozon web/ABT challenge для текущей сети или IP Seller VPS;
- повторный ввод кода не поможет, пока страница signin не открывается до поля
  email;
- перезапуск keeper/timer и смена user-agent не устраняют проблему.

Безопасный порядок действий:

1. Не путать этот случай с падением API: карточные API-операции могут быть
   доступны, но ЛК Ozon через browser/CDP недоступен.
2. Проверить контур:

   ```bash
   loginctl show-user pavel -p Linger -p State -p RuntimePath
   ss -ltnp | grep 9544
   PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
     -m seller_agent.cli sessions status --marketplace ozon
   ```

3. Если CDP/profile правильные, но signin/dashboard дают `HTTP 403` и
   `Похоже, нет соединения`, не запускать повторные login-коды. Если владелец
   дал свежие cookies, сначала проверить их на временном профиле через
   `ozon_import_cookies_check.js --profile ... --state ...`; рабочий профиль
   перезаписывать только после `COOKIE_IMPORT_SUCCESS` и
   `expectedStoreFound: true`.
4. Если свежие cookies тоже не открывают dashboard, нужен другой сетевой
   маршрут для Ozon web или разблокировка у Ozon.
5. После появления доступного сетевого маршрута повторить:

   ```bash
   PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
     -m seller_agent.cli restore-ozon-session --email <email>
   scripts/sessions/ozon_session_refresh.sh
   PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
     -m seller_agent.cli sessions status --marketplace ozon
   ```

6. После неудачного restore всегда вернуть systemd units в поднятое состояние:

   ```bash
   XDG_RUNTIME_DIR=/run/user/1000 \
   DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
     systemctl --user start vital-shevron-ozon-keeper.service \
     vital-shevron-ozon-session-refresh.timer
   ```

### Проверенное восстановление Ozon cookies 2026-07-13

Ситуация:

- старый Ozon web state показывал `Похоже, нет соединения`/ABT и refresh
  возвращал `Ozon blocked/no-connection page detected`;
- владелец предоставил свежий cookie header;
- значения cookies не выводились и не сохранялись в отчеты.

Безопасный порядок:

1. Скопировать cookie-файл только во временный `tmp/auth` с правами `600`.
2. Проверить на временном профиле:

   ```bash
   xvfb-run -a node scripts/sessions/ozon_import_cookies_check.js \
     --cookie-file tmp/auth/ozon_user_cookies_check.txt \
     --profile tmp/ozon-cookie-check-profile \
     --state tmp/ozon-cookie-check-state.json \
     --expected-store "Vital Shevron" \
     --headful
   ```

3. Если результат `COOKIE_IMPORT_SUCCESS`, `expectedStoreFound: true`,
   `loggedIn: true`, остановить Ozon keeper/timer и импортировать cookies в
   рабочий profile/state штатным скриптом.
4. Запустить `vital-shevron-ozon-keeper.service` и
   `vital-shevron-ozon-session-refresh.timer`.
5. Очистить старый failed-state refresh service, если он остался от прошлой
   ошибки, и вручную выполнить один refresh:

   ```bash
   XDG_RUNTIME_DIR=/run/user/1000 \
   DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
     systemctl --user reset-failed vital-shevron-ozon-session-refresh.service

   XDG_RUNTIME_DIR=/run/user/1000 \
   DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
     systemctl --user start vital-shevron-ozon-session-refresh.service
   ```

6. Проверить `mtime` и права
   `.sessions/ozon/ozon_seller_storage_state.json`, затем выполнить:

   ```bash
   PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
     -m seller_agent.cli sessions status --marketplace ozon
   PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
     -m seller_agent.cli status-preflight
   ```

Проверенный результат 2026-07-13:

- keeper active/running, timer active/waiting и enabled;
- refresh service завершился `status=0/SUCCESS`;
- `storage_state` обновился после refresh и имеет права `600`;
- `sessions_status_20260713T201213`: `overall_status: ok`;
- `status_preflight_20260713T201219`: `overall_status: ok`;
- Ozon dashboard, analytics, products и prices открылись без login/ABT,
  `expectedStoreFound: true`, `stateExported: true`, `valuesPrinted: false`.

### Постоянная нормализация Ozon cookies 2026-07-27

Подтверждённая причина повторного сбоя:

- `ozon_import_cookies_check.js` размножал cookies без явного домена сразу на
  четыре aliases: `.ozon.ru`, `ozon.ru`, `seller.ozon.ru`,
  `.seller.ozon.ru`;
- keepalive экспортировал этот набор обратно в `storage_state`;
- state вырос до `68` cookies при `16` уникальных именах, после чего Ozon
  показывал `Похоже, нет соединения`/`__rr`;
- работа VitalSewing с того же сервера, IP и аккаунта опровергла вывод о
  блокировке общего IP.

Исправление:

- `scripts/lib/ozon_cookie_state.js` хранит один канонический cookie среди
  root/seller aliases, сохраняя отдельные сервисные домены Ozon;
- импорт cookies без домена использует только `.ozon.ru`;
- import, keeper, CDP keepalive, persistent и interactive login экспортируют
  state атомарно с mode `600`;
- keeper/persistent перед `addCookies` автоматически ремонтируют раздутый
  state и фиксируют только безопасные счётчики `inputCount`, `outputCount`,
  `removedCount`, `aliasDuplicates`, `valuesPrinted=false`;
- тесты: `tests/test_ozon_cookie_state.py`.

Live-проверка:

- исправленный import нового cookie header: `COOKIE_IMPORT_SUCCESS`,
  `expectedStoreFound=true`, итоговый state `20` cookies;
- keeper после restart увидел `22`, удалил `2` alias-копии и сохранил `20`;
- ручной `vital-shevron-ozon-session-refresh.service` завершился
  `Result=success`, `ExecMainStatus=0`;
- `status_preflight_20260727T205140`: `overall_status=ok`.

Правило диагностики:

- `__rr` или `Похоже, нет соединения` не считать доказательством блокировки
  IP, пока не сравнён другой рабочий контур на том же сервере/аккаунте;
- сначала сравнить CDP port/profile, cookie counts/domains без значений,
  selected company и временный import свежих cookies.

## WB Watchdog

WB watchdog запускается через:

```bash
scripts/sessions/start_wb_session_watchdog.sh
```

Все прямые mutable session-команды используют те же canonical profile leases,
что и Job Worker:

```text
lk:ozon:profile:chrome-profile
lk:wb:profile:browser-profile
```

- `sessions start/stop/restart --marketplace all` получает оба ключа одной
  SQLite-транзакцией до запуска любого script;
- `restore-ozon-session` получает Ozon key до `systemctl`, остановки keeper,
  очистки процессов/`Singleton*` и interactive login;
- `install-session-systemd --apply --switch` получает оба ключа атомарно до
  switch и освобождает их после короткой операции; только после освобождения
  обе refresh-службы синхронно запускаются со своими exact leases, а ошибка
  любой службы переводит итог установки в `error`;
- прямой вызов start/stop/watchdog/refresh shell scripts также проходит через
  canonical wrapper;
- фоновые keeper/watchdog процессы не наследуют признак уже взятого lease:
  постоянный browser host не удерживает lease, а каждый refresh берет его
  заново;
- `sessions status`, dry-run restore/install и install без `--switch` не
  получают profile lease и не создают ложный конфликт.

При занятом exact key maintenance-команда возвращает
`blocked_reason=resource_lease_busy` до изменения профиля. Обход через другой
legacy key (`lk:wb:browser-profile`, `lk:ozon:session-check`) запрещен.

Он вызывает `scripts/sessions/wb_daily_session_refresh.sh` с интервалом `3600`
секунд. Refresh-скрипт должен работать внутри текущего проекта, а не содержать
hardcoded путь к другому контуру.

`WB_EXPECTED_SELLER` содержит пробелы, поэтому shell-скриптам нельзя напрямую
`source .env`. Нужно читать только нужные ключи безопасным parser-ом или
передавать переменные окружения явно.

Проверка:

```bash
scripts/sessions/wb_daily_session_refresh.sh
PYTHONPATH=src python3 -m seller_agent.cli sessions status --marketplace wb
```

### Внештатная ситуация WB 2026-06-30

Симптом:

- полный `status-preflight` показывал `wb_keepalive: error` и
  `wb_refresh_state: error`;
- ошибка: `WB blocked/error page detected`;
- при ручной проверке seller и cmp страницы были фактически открыты, вход в ЛК
  был активен.

Причина:

- `scripts/sessions/wb_session_keepalive.js` считал любое слово `ошибка` в
  тексте страницы признаком blocked/error page;
- на странице `cmp.wildberries.ru/campaigns/list` WB показал обычное
  информационное уведомление про отображение бюджета кампаний со строкой
  `Ошибка только в отображении`;
- это не было блокировкой, капчей и не требовало повторного входа.

Восстановление:

1. Остановить legacy watchdog на время диагностики:

   ```bash
   scripts/sessions/stop_wb_session_watchdog.sh
   ```

2. Проверить seller/cmp страницы через текущий профиль без вывода cookies и
   storage state.
3. Уточнить классификатор blocked/error page: не считать любое слово `ошибка`
   блокировкой; блокировать только явные признаки `captcha`, `access denied`,
   `доступ ограничен`, `что-то пошло не так`, `произошла ошибка`,
   `страница недоступна`, `service unavailable`.
4. Запустить:

   ```bash
   scripts/sessions/wb_daily_session_refresh.sh
   scripts/sessions/start_wb_session_watchdog.sh
   PYTHONPATH=src python3 -m seller_agent.cli sessions status --marketplace wb
   PYTHONPATH=src python3 -m seller_agent.cli status-preflight
   ```

Проверка результата:

- `wb_daily_session_refresh.sh` (внутренний keepalive): `ok: true`,
  `stateExported: true`, `valuesPrinted: false`;
- `sessions status --marketplace wb`: `overall_status: ok`;
- полный `status-preflight`: `overall_status: ok`;
- секреты, cookies, storage state и значения токенов не выводились.
