# LK Connection Runbook

## Итог

ЛК Ozon и WB подключать только через отдельные Vital Shevron persistent-сессии.
Сессии TAKTERRA не переносить и не смешивать.

## Ozon

Настройки:

```text
OZON_REMOTE_DEBUGGING_PORT=9544
OZON_EXPECTED_STORE=Vital Shevron
.sessions/ozon/chrome-profile
.sessions/ozon/ozon_seller_storage_state.json
```

API credentials разрешено читать только из переменных текущего контура:

```text
OZON_SELLER_CLIENT_ID
OZON_SELLER_API_KEY
OZON_PERFORMANCE_CLIENT_ID
OZON_PERFORMANCE_CLIENT_SECRET
VITAL_SHEVRON_OZON_SELLER_CREDENTIALS_FILE
VITAL_SHEVRON_OZON_PERFORMANCE_CREDENTIALS_FILE
SELLER_OZON_SELLER_CREDENTIALS_FILE
SELLER_OZON_PERFORMANCE_CREDENTIALS_FILE
```

`TAKTERRA_OZON_*` нельзя использовать как fallback: это может смешать API-
контуры разных магазинов.

Восстановление:

```bash
PYTHONPATH=src python3 -m seller_agent.cli restore-ozon-session --dry-run
PYTHONPATH=src python3 -m seller_agent.cli restore-ozon-session --email <email>
```

Импорт cookies:

```bash
install -m 600 <cookie-file> tmp/auth/ozon_user_cookies.json
xvfb-run -a node scripts/sessions/ozon_import_cookies_check.js \
  --cookie-file tmp/auth/ozon_user_cookies.json \
  --expected-store "Vital Shevron" \
  --headful
```

После успешного импорта:

- `tmp/auth/ozon_user_cookies.json` должен быть удален штатным скриптом;
- `.sessions/ozon/ozon_seller_storage_state.json` должен иметь права `600`;
- keeper должен запускаться на чистом/текущем профиле и засевать профиль из
  `storage_state`;
- проверка через CDP должна открывать dashboard, analytics, products и prices
  без `registration/signin`.

Проверка автопродления cookies после импорта:

```bash
XDG_RUNTIME_DIR=/run/user/1000 \
DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
  systemctl --user start vital-shevron-ozon-session-refresh.service

PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli sessions status --marketplace ozon
```

Критерии готовности:

- `vital-shevron-ozon-keeper.service` и
  `vital-shevron-ozon-session-refresh.timer` enabled/active;
- `.sessions/ozon/ozon_seller_storage_state.json` обновляет `mtime` после
  refresh и имеет права `600`;
- `refresh.ok: true`, `state_exported: true`, `values_printed: false`;
- полный `status-preflight` возвращает `overall_status: ok`.

Проверенное восстановление 2026-07-13 через cookie header:

- сначала cookie-файл проверен на временном профиле через
  `ozon_import_cookies_check.js --profile ... --state ...`;
- после подтверждения `COOKIE_IMPORT_SUCCESS` cookies импортированы в рабочий
  профиль/state;
- штатный refresh через systemd обновил `storage_state`, после чего
  `sessions status --marketplace ozon` и `status-preflight` вернули `ok`;
- значения cookies/storage state в вывод не печатались.

Внештатная ситуация `400 Request Header Or Cookie Too Large`:

- причина: cookie header раздут дублями cookies по доменам после импорта;
- с 2026-07-27 исправление автоматизировано общим модулем
  `scripts/lib/ozon_cookie_state.js`: импорт не создаёт четыре копии cookie для
  `.ozon.ru`, `ozon.ru`, `seller.ozon.ru`, `.seller.ozon.ru`, а import,
  keeper, keepalive, persistent и interactive export атомарно нормализуют
  `storage_state`;
- перед засевом profile keeper автоматически ремонтирует уже раздутый state,
  сохраняя backup только при фактическом изменении;
- безопасный ручной recovery: сделать backup `storage_state`, установить
  проверенный нормализованный state, переименовать старый Chrome profile в
  backup, запустить keeper и проверить `ozon_session_keepalive_cdp.js`;
- значения cookies в диагностику и отчеты не выводить, допустимы только счетчики
  cookies/доменов и статус `valuesPrinted=false`.

Проверенное восстановление 2026-07-27:

- рабочий VitalSewing на том же сервере, IP и Ozon-аккаунте подтвердил, что
  симптом Vital Shevron не был доказательством блокировки общего IP;
- у Vital Shevron было `68` cookies при `16` уникальных именах из-за
  четырёхкратных доменных копий, а авторизационные значения уже устарели;
- новый owner-provided cookie header сначала прошёл временный
  `COOKIE_IMPORT_SUCCESS` с точным маркером `Vital Shevron`;
- после нормализации рабочий state содержит `20` cookies на `.ozon.ru`,
  `.ozone.ru` и `.xapi.ozon.ru`, без вредных seller/root aliases;
- keeper и timer active/enabled, ручной systemd refresh завершился
  `Result=success`, `ExecMainStatus=0`;
- `sessions_status_20260727T205140` и
  `status_preflight_20260727T205140` вернули `overall_status: ok`;
- dashboard, analytics, products и prices открываются, значения cookies не
  выводились.

## WB

Настройки:

```text
WB_EXPECTED_SELLER=ИП Витальская И. П.
.sessions/wb/browser-profile
.sessions/wb/wb_storage_state.json
```

WB API token разрешено читать только из:

```text
WB_API_TOKEN
VITAL_SHEVRON_WB_TOKEN_FILE
SELLER_WB_TOKEN_FILE
```

`TAKTERRA_WB_TOKEN_FILE` нельзя использовать как fallback: при наличии чужой
переменной окружения проект Vital Shevron должен вернуть отсутствие токена, а
не подключиться к другому магазину.

Если `WB_EXPECTED_SELLER` пустой, keepalive проверяет вход в seller/cmp, но не
подтверждает конкретного продавца. После первого входа marker нужно заполнить.

Первый вход:

```bash
xvfb-run -a node scripts/sessions/wb_auth_once_file_code.js <phone_without_+7>
```

Скрипт ждет SMS-код в:

```text
tmp/auth/wb-auth-once/sms-code.txt
```

После успешного входа:

- `.sessions/wb/wb_storage_state.json` должен иметь права `600`;
- временный файл `sms-code.txt` должен быть удален;
- `WB_EXPECTED_SELLER` нужно заполнить marker продавца из ЛК;
- `node scripts/sessions/wb_session_keepalive.js` должен открыть
  `seller.wildberries.ru` и `cmp.wildberries.ru/campaigns/list`.

## Проверка

```bash
PYTHONPATH=src python3 -m seller_agent.cli sessions status
PYTHONPATH=src python3 -m seller_agent.cli status-preflight
```

Секреты, cookies, storage state и коды входа в отчеты не выводить.
