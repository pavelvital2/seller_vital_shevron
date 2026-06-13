# Session Manager Runbook

## Команды

```bash
PYTHONPATH=src python3 -m takterra_agent.cli sessions status
PYTHONPATH=src python3 -m takterra_agent.cli sessions start --marketplace ozon
PYTHONPATH=src python3 -m takterra_agent.cli sessions stop --marketplace ozon
PYTHONPATH=src python3 -m takterra_agent.cli sessions restart --marketplace ozon
```

## Systemd

Dry-run:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli install-session-systemd
```

Apply после проверки:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli install-session-systemd --apply --switch
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
PYTHONPATH=src python3 -m takterra_agent.cli sessions status --marketplace ozon
```

## Восстановление Ozon-сессии

Dry-run:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli restore-ozon-session --dry-run
```

Фактическое восстановление:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli restore-ozon-session
```

Сценарий интерактивно запрашивает email и коды входа. Коды не сохранять в
файлы проекта, отчеты и сообщения.

Если проект еще не переведен на `systemd --user` и команда завершилась
`overall_status: warning` из-за отсутствующих unit-файлов
`vital-shevron-ozon-*`, но сам интерактивный вход вернул `LOGIN_SUCCESS`, нужно
поднять legacy keeper/watchdog вручную:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli sessions start --marketplace ozon
node scripts/sessions/ozon_session_keepalive_cdp.js
PYTHONPATH=src python3 -m takterra_agent.cli sessions status --marketplace ozon
```

Критерий готовности после восстановления:

- `expectedStoreFound: true`;
- `stateExported: true`;
- `sessions status --marketplace ozon` возвращает `overall_status: ok`;
- `refresh.ok: true`;
- `values_printed: false`.

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
PYTHONPATH=src python3 -m takterra_agent.cli sessions status --marketplace wb
```
