# Status/preflight runbook

Дата создания: 2026-06-10.

Назначение: единая read-only проверка состояния проекта перед любыми операциями
с Ozon/WB.

## Что проверяет

Сценарий `status-preflight` проверяет:

- Ozon Seller API;
- Ozon Performance API для продвижения;
- Wildberries Content API;
- актуальность `data/catalog/processed/master_catalog.json`;
- Ozon keeper pid;
- Ozon watchdog pid или `systemd --user` timer;
- Ozon CDP `127.0.0.1:9444`;
- Ozon LK keepalive;
- возраст последнего успешного Ozon keepalive;
- WB watchdog pid или `systemd --user` timer;
- WB LK keepalive и активного продавца `ИП Рантусова`.
- возраст последнего успешного WB keepalive.

Текущие интервалы таймеров:

```text
Ozon watchdog: 1800 секунд, 30 минут
WB watchdog: 3600 секунд, 60 минут
```

После перехода на `systemd --user` watchdog source в отчетах должен быть
`systemd`. Старые pid-файлы watchdog при этом могут отсутствовать, это не
ошибка, если соответствующий timer активен.

Сценарий не выполняет write-операции в маркетплейсах. При успешной проверке ЛК
он может обновлять локальный storage state сессии.

## Команды

Полная проверка:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli status-preflight
```

Только API и каталог, без браузерных ЛК-сессий:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli status-preflight --skip-lk
```

## Секреты

API-доступы должны подключаться через переменные окружения или локальный `.env`
с путями к внешним секретным файлам. Содержимое ключей не записывать в отчеты,
инструкции, код и сообщения.

Поддерживаемые переменные:

```text
TAKTERRA_OZON_SELLER_CREDENTIALS_FILE
TAKTERRA_OZON_PERFORMANCE_CREDENTIALS_FILE
TAKTERRA_WB_TOKEN_FILE
```

## Артефакты

Каждый запуск сохраняет:

```text
data/runs/YYYY-MM-DD/status_preflight_YYYYMMDDTHHMMSS/summary.json
data/runs/YYYY-MM-DD/status_preflight_YYYYMMDDTHHMMSS/status_preflight_report.md
```

## Как читать результат

`overall_status: ok`:

- API доступны;
- master catalog согласован;
- ЛК-сессии доступны;
- последние keepalive не просрочены;
- можно переходить к следующему read-only/dry-run сценарию.

`overall_status: warning`:

- критических ошибок нет, но часть проверок пропущена;
- пример: запуск с `--skip-lk`;
- write-операции не начинать, если пропущена проверка нужного канала.

`overall_status: error`:

- есть блокер;
- write-операции запрещены до устранения причины;
- безопасные API-only read-only задачи допустимы, если соответствующая API
  проверка имеет статус `ok`.

## Текущий известный результат

Последний полный запуск:

```text
run_id: status_preflight_20260611T000512
overall_status: ok
```

Состояние:

```text
Ozon Seller API: ok
Ozon Performance API: ok, Bearer token received, expires_in 1800
WB API: ok
master catalog: ok, 203 rows, 203 matched, Ozon-only 0, WB-only 0
Ozon keeper: ok, systemd service + pid file
Ozon watchdog: ok, systemd timer
Ozon CDP 127.0.0.1:9444: ok
Ozon LK keepalive: ok, TAKTERRA подтверждена
Ozon refresh age: 0 seconds at run time
WB watchdog: ok, systemd timer
WB LK keepalive: ok, активный продавец ИП Рантусова
WB refresh age: 0 seconds at run time
```

Вывод: проектный `status-preflight` работает, Ozon Seller API, Ozon Performance
API, WB API, master catalog, Ozon ЛК и WB ЛК готовы. Bearer-токен Performance
API не сохраняется и не выводится.

## Если не найден файл WB API-токена

2026-06-11 во время операции WB Price Quarantine `status-preflight` завершился
до проверок с ошибкой `FileNotFoundError`: переменная `TAKTERRA_WB_TOKEN_FILE`
указывала на временный файл Telegram attachment, которого уже не было на диске.

Это не означает, что WB ЛК-сессия сломана. В тот же момент отдельный
`wb_session_keepalive.js` подтвердил ЛК, `cmp.wildberries.ru` и продавца
`ИП Рантусова`.

Правило:

- не выводить путь вместе с содержимым токена;
- не создавать новый секретный файл в проектных отчетах;
- перенести WB token file в постоянную локальную секретную зону;
- обновить env/file-env так, чтобы `status-preflight` не зависел от временных
  вложений Telegram.

Рекомендация записана как `REC-015`.

## Если Ozon LK показывает Login required

1. Не выполнять браузерные Ozon write-операции.
2. API-only read-only задачи можно выполнять, если `ozon_api` имеет статус `ok`.
3. Восстановить вход по инструкции:

```text
data/planning/lk_connection_runbook.md
```

4. После успешного входа выполнить:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli status-preflight
```

5. Если `ozon_keepalive` стал `ok`, можно продолжать Ozon-сценарии через
read-only/dry-run.

## Восстановление Ozon по почте 2026-06-10

Сессия Ozon была восстановлена через `ozon_seller_interactive_login.js` с входом
по почте. После ввода свежего кода владельца:

```text
dashboard: ok
expectedStore: TAKTERRA
stateExported: true
```

После восстановления перезапущены:

```text
Ozon keeper: ok
Ozon watchdog: ok, interval 1800 seconds
```

Контрольный `status-preflight`:

```text
status_preflight_20260610T225037
overall_status: ok
```

## Session Manager и systemd

Постоянная инструкция:

```text
data/planning/session_manager_runbook.md
```

Основные команды:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli sessions status
PYTHONPATH=src python3 -m takterra_agent.cli restore-ozon-session --dry-run
PYTHONPATH=src python3 -m takterra_agent.cli install-session-systemd
```

Переключение на `systemd --user` выполнено 2026-06-11:

```text
sessions_status_20260611T000502: overall_status ok
status_preflight_20260611T000512: overall_status ok
```

Проверка timers:

```bash
systemctl --user list-timers 'takterra-*' --no-pager
```
