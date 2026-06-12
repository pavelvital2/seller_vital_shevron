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

Восстановление:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli restore-ozon-session --dry-run
PYTHONPATH=src python3 -m takterra_agent.cli restore-ozon-session --email <email>
```

## WB

Настройки:

```text
WB_EXPECTED_SELLER=<уточнить после первого входа>
.sessions/wb/browser-profile
.sessions/wb/wb_storage_state.json
```

Если `WB_EXPECTED_SELLER` пустой, keepalive проверяет вход в seller/cmp, но не
подтверждает конкретного продавца. После первого входа marker нужно заполнить.

## Проверка

```bash
PYTHONPATH=src python3 -m takterra_agent.cli sessions status
PYTHONPATH=src python3 -m takterra_agent.cli status-preflight
```

Секреты, cookies, storage state и коды входа в отчеты не выводить.

