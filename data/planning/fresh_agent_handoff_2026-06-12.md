# Fresh Agent Handoff - Vital Shevron

Дата подготовки: 2026-06-12 20:42 MSK

## Итог

Проект Vital Shevron готов к передаче fresh-агенту.

Рабочая папка:

```text
/home/pavel/projects/seller_vital_shevron
```

GitHub:

```text
https://github.com/pavelvital2/seller_vital_shevron
visibility: PRIVATE
branch: main
latest verified commit before this handoff: 8883678 Connect Vital LK sessions
```

Telegram agent routing подтвержден в `telegram-ai-agent`:

```text
topic: seller_vital_shevron
thread_id: 42336
cwd: /home/pavel/projects/seller_vital_shevron
```

## С чего начать fresh-агенту

1. Перейти в проект:

```bash
cd /home/pavel/projects/seller_vital_shevron
```

2. Прочитать правила:

```bash
sed -n '1,260p' AGENTS.md
```

3. Прочитать этот handoff и профильные инструкции:

```bash
sed -n '1,260p' data/planning/fresh_agent_handoff_2026-06-12.md
sed -n '1,220p' data/planning/session_manager_runbook.md
sed -n '1,220p' data/planning/lk_connection_runbook.md
sed -n '1,220p' data/planning/catalog_mapping_runbook.md
```

4. Проверить состояние:

```bash
git status --short --ignored
PYTHONPATH=src python3 -m takterra_agent.cli sessions status
PYTHONPATH=src python3 -m takterra_agent.cli status-preflight
PYTHONPATH=src pytest -q
```

## Проверенное состояние на момент передачи

Проверено 2026-06-12 20:41-20:42 MSK.

```text
pytest: 34 passed
sessions status: ok
status-preflight: ok
```

Последний `status-preflight`:

```text
run_id: status_preflight_20260612T204125
overall_status: ok
Ozon Seller API: ok
Ozon Performance API: ok
WB API: ok
master_catalog: ok
Ozon LK/CDP/keepalive: ok
WB LK/keepalive: ok
```

Каталог:

```text
seller_sku_mode: separate
rows: 881
matched_rows: 98
ozon_only_rows: 450
wb_only_rows: 333
barcode_mismatch_rows: 0
mapping rows requiring owner review: 783
```

## Сессии ЛК

Сессии Vital Shevron не смешивать с TAKTERRA.

Ozon:

```text
expected store: Vital Shevron
CDP: 127.0.0.1:9544
refresh interval: 1800 seconds
current source: legacy watchdog pid
storage_state: .sessions/ozon/ozon_seller_storage_state.json
```

WB:

```text
expected seller marker: ИП Витальская И. П.
refresh interval: 3600 seconds
current source: legacy watchdog pid
storage_state: .sessions/wb/wb_storage_state.json
```

Проверка:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli sessions status
node scripts/sessions/ozon_session_keepalive_cdp.js
node scripts/sessions/wb_session_keepalive.js
```

## Секреты и runtime

Не выводить, не коммитить и не записывать в документы:

- API keys;
- tokens;
- cookies;
- `storage_state`;
- SMS/email-коды;
- содержимое файлов из `.sessions/` и `tmp/auth/`.

Файлы секретов существуют только локально и должны оставаться ignored:

```text
.env
.sessions/
tmp/auth/
data/runs/
data/catalog/*/raw/
data/catalog/*/processed/
data/catalog/mapping/*.csv
data/catalog/mapping/*review*.md
```

Перед любым commit обязательно:

```bash
git status --short --ignored
```

В tracked status не должно быть `.env`, `.sessions`, `tmp/auth`, raw/processed
catalog snapshots или runtime reports.

## Важные уже решенные внештатные ситуации

### Ozon cookie import

Cookies были импортированы из файла, затем одноразовый файл был удален.
После импорта возникал `400 Request Header Or Cookie Too Large` на внутренних
разделах Ozon из-за дублей cookies по доменам.

Решение:

- сделать backup `storage_state`;
- дедуплицировать cookies;
- переименовать старый Chrome profile в backup;
- запустить keeper заново;
- проверить dashboard, analytics, products и prices через CDP.

Инструкция: `data/planning/lk_connection_runbook.md`.

### Ozon keeper

Keeper должен перед открытием ЛК засевать persistent profile из
`.sessions/ozon/ozon_seller_storage_state.json`. Это уже реализовано в:

```text
scripts/sessions/ozon_keep_dashboard_open.js
```

### WB refresh

`scripts/sessions/wb_daily_session_refresh.sh` раньше содержал hardcoded путь к
TAKTERRA и ломался на `source .env`, потому что `WB_EXPECTED_SELLER` содержит
пробелы. Сейчас скрипт работает внутри текущего проекта и безопасно читает
только нужный ключ.

## Текущие правила по SKU и mapping

У Vital Shevron артикулы продавца на Ozon и WB не совпадают.

До унификации:

- Ozon-сценарии используют `offer_id`, `product_id`, `sku`;
- WB-сценарии используют `vendorCode`, `nmID`, barcode;
- mapping нужен для объединенных отчетов и cross-marketplace операций;
- marketplace-local сценарии не должны блокироваться отсутствием полного
  mapping;
- cross-marketplace write-операции требуют подтвержденного mapping по
  затрагиваемым строкам;
- унификация seller SKU является опасной операцией и требует отдельного
  `read-only -> dry-run -> review -> approved -> apply -> verify -> result`.

Mapping draft:

```text
data/catalog/mapping/ozon_wb_product_mapping.csv
data/catalog/mapping/ozon_wb_product_mapping_review.md
```

Эти файлы являются runtime/business data и не коммитятся по умолчанию.

## Safety

Для опасных операций обязательна цепочка:

```text
read-only -> dry-run -> review -> approved -> apply -> verify -> result
```

Опасные операции:

- цены;
- скидки;
- акции;
- рекламные ставки и бюджеты;
- карточки;
- фото;
- ответы покупателям;
- поставки;
- остатки;
- изменение seller SKU;
- любые действия с финансовыми последствиями.

API-first: если операция доступна и через API, и через ЛК, сначала использовать
API. ЛК использовать только если API недостаточно; причину фиксировать в
инструкции/отчете.

## Следующие рекомендуемые шаги

1. Перевести Ozon/WB refresh с legacy watchdog на `systemd --user` timers:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli install-session-systemd
PYTHONPATH=src python3 -m takterra_agent.cli install-session-systemd --apply --switch
PYTHONPATH=src python3 -m takterra_agent.cli sessions status
PYTHONPATH=src python3 -m takterra_agent.cli status-preflight
```

2. Подготовить единый безопасный loader `.env` для shell-скриптов.

3. Вернуться к owner review mapping: `783` строк требуют проверки.

4. После стабилизации LK/systemd продолжить бизнес-сценарии:

- daily morning report;
- отзывы/вопросы;
- акции Ozon/WB;
- сопоставление каталогов;
- будущая унификация seller SKU.

5. Отдельным этапом переименовать Python package `takterra_agent` в
нейтральное имя (`seller_agent` или `vital_shevron_agent`) только после
проверенного плана, чтобы не сломать рабочий контур.

## Документы для fresh-агента

Обязательные:

```text
AGENTS.md
data/planning/fresh_agent_handoff_2026-06-12.md
data/planning/project_map.md
data/planning/recommendations_index.md
data/planning/session_manager_runbook.md
data/planning/lk_connection_runbook.md
data/planning/status_preflight_runbook.md
data/planning/catalog_mapping_runbook.md
```

По задачам:

```text
data/planning/daily_morning_report_runbook.md
data/planning/reviews_questions_runbook.md
```

