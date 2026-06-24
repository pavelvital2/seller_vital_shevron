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

Внештатная ситуация `400 Request Header Or Cookie Too Large`:

- причина: cookie header раздут дублями cookies по доменам после импорта;
- способ решения: сделать backup `storage_state`, оставить один рабочий набор
  cookies для Ozon-доменов, переименовать старый Chrome profile в backup,
  запустить keeper заново и проверить `ozon_session_keepalive_cdp.js`;
- значения cookies в диагностику и отчеты не выводить, допустимы только счетчики
  cookies/доменов и статус `valuesPrinted=false`.

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
