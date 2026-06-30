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

## Ozon Keeper

Keeper Ozon запускает отдельный CDP на `127.0.0.1:9544` и перед открытием ЛК
засевает persistent profile из
`.sessions/ozon/ozon_seller_storage_state.json`, если этот файл существует.
Это нужно, чтобы импортированные cookies/storage state были доступны Chrome
keeper-процессу, а не только разовому Playwright-контексту.

Проверка после запуска:

```bash
node scripts/sessions/ozon_session_keepalive_cdp.js
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
node scripts/sessions/ozon_session_keepalive_cdp.js
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

## WB Watchdog

WB watchdog запускается через:

```bash
scripts/sessions/start_wb_session_watchdog.sh
```

Он вызывает `scripts/sessions/wb_daily_session_refresh.sh` с интервалом `3600`
секунд. Refresh-скрипт должен работать внутри текущего проекта, а не содержать
hardcoded путь к другому контуру.

`WB_EXPECTED_SELLER` содержит пробелы, поэтому shell-скриптам нельзя напрямую
`source .env`. Нужно читать только нужные ключи безопасным parser-ом или
передавать переменные окружения явно.

Проверка:

```bash
node scripts/sessions/wb_session_keepalive.js
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
   node scripts/sessions/wb_session_keepalive.js
   scripts/sessions/start_wb_session_watchdog.sh
   PYTHONPATH=src python3 -m seller_agent.cli sessions status --marketplace wb
   PYTHONPATH=src python3 -m seller_agent.cli status-preflight
   ```

Проверка результата:

- `node scripts/sessions/wb_session_keepalive.js`: `ok: true`,
  `stateExported: true`, `valuesPrinted: false`;
- `sessions status --marketplace wb`: `overall_status: ok`;
- полный `status-preflight`: `overall_status: ok`;
- секреты, cookies, storage state и значения токенов не выводились.
