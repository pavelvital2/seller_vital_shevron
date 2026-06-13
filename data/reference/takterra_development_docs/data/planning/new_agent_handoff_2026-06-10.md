# Инструкция для нового агента TAKTERRA

Дата: 2026-06-10.

## Первое действие

Перед любой работой прочитать:

1. `data/planning/marketplace_control_bot_discussion.md`
2. этот файл
3. последний итоговый отчет:
   `data/runs/2026-06-10/wb_card_create_completion_report.md`
4. Для любых операций с WB-карточками:
   `data/planning/wb_card_upload_runbook.md`

Не начинать с повторного анализа старых проектов, если задача явно не требует
этого. Старые проекты являются read-only источниками опыта:

- `/home/pavel/projects/seller_wb`
- `/home/pavel/projects/seller_ozon`
- `/home/pavel/projects/seller_ozon_vitalsewing`

Рабочий проект, где можно вносить изменения:

```text
/home/pavel/projects/seller_takterra
```

## Главный принцип

Основная задача - создавать проект управления магазинами TAKTERRA на Ozon и WB.
Работа с реальными магазинами допустима только как сопутствующий сценарий,
который развивает task-runner, safety-контур, отчеты и будущий бот.

Целевое правило каталога:

```text
master_sku == Ozon offer_id == WB vendorCode
```

Для одного товара на Ozon и WB параметры, характеристики, описание, размеры и
фото должны быть максимально идентичны. Отличия допускаются только из-за
ограничений конкретного маркетплейса и должны фиксироваться в отчете.

## Секреты

Не записывать токены, ключи, cookies, storage state или содержимое файлов с
секретами в код, отчеты или planning-файлы.

Код загружает доступы через `src/takterra_agent/config.py` из переменных
окружения или внешних файлов. Использовать только env/file-env подход:

```text
TAKTERRA_WB_TOKEN_FILE
TAKTERRA_OZON_SELLER_CREDENTIALS_FILE
TAKTERRA_OZON_PERFORMANCE_CREDENTIALS_FILE
WB_API_TOKEN
OZON_SELLER_CLIENT_ID
OZON_SELLER_API_KEY
```

Если нужны реальные значения или пути к секретным файлам, брать их только из
текущего защищенного контекста/сообщений владельца, не копировать в документы.

## Что уже сделано

Создан минимальный каркас agent/task-runner:

```text
src/takterra_agent/
  cli.py
  config.py
  http.py
  catalog/
  marketplaces/ozon/adapter.py
  marketplaces/wb/adapter.py
  tasks/catalog_fetch.py
  tasks/wb_card_create_plan.py
  tasks/wb_card_create_apply.py
  safety/
  bot/
```

Есть тесты:

```text
tests/test_catalog_loader.py
tests/test_wb_card_create_plan.py
```

Проверка на момент handoff:

```text
python3 -m compileall -q src tests scripts
python3 -m pytest -q

4 passed
```

## Каталог

Финальный актуальный read-only catalog check:

```text
run_id: catalog_fetch_20260610T151249
report: data/runs/2026-06-10/catalog_fetch_20260610T151249/catalog_check_report.md
master CSV: data/catalog/processed/master_catalog.csv
master JSON: data/catalog/processed/master_catalog.json
```

Итог:

```text
Ozon products: 203
WB cards: 203
matched rows: 203
Ozon-only rows: 0
WB-only rows: 0
barcode mismatch rows: 0
```

Важно: Ozon возвращает платформенные баркоды вида `OZN...`. Это фиксируется как
`ozon_platform_barcode`, не как ошибка сопоставления.

## Создание 5 WB-карточек

Изначально было 5 Ozon-only товаров:

```text
chev_raz_pict0015
chev_raz_pict0023
chev_rusob_pict0001
chev_svo_pict0020
Patronash0001
```

Они доведены до активного состояния WB и проверены:

| vendorCode | WB nmID | WB imtID | photos | status |
| --- | ---: | ---: | ---: | --- |
| chev_raz_pict0015 | 976316288 | 2027847108 | 6 | active verified |
| chev_raz_pict0023 | 1141608253 | 1464304998 | 6 | active verified |
| chev_rusob_pict0001 | 952681695 | 2292683765 | 6 | active verified |
| chev_svo_pict0020 | 1141600147 | 1492532894 | 6 | active verified |
| Patronash0001 | 1141618630 | 2454453456 | 5 | active verified |

Сводный отчет:

```text
data/runs/2026-06-10/wb_card_create_completion_report.md
```

Успешный apply:

```text
data/runs/2026-06-10/wb_card_create_apply_20260610T150752/wb_card_create_apply_report.md
```

Восстановление из корзины:

```text
data/runs/2026-06-10/wb_card_recover_20260610T151143/wb_card_recover_report.md
```

Очередь WB-onboarding обновлена как закрытая:

```text
data/catalog/wb_onboarding/ozon_only_to_add_to_wb.csv
```

## Найденные особенности WB API

1. WB отвергает packed weight characteristic `88952` в `characteristics`.
   Упаковочный вес нужно передавать только через
   `dimensions.weightBrutto` в килограммах.

2. WB отвергает emoji в описаниях. Нормализатор WB-текста теперь удаляет emoji и
   сохраняет смысловой текст и переносы строк.

3. WB может частично создать карточки даже при HTTP 400 по одному из запросов.
   Любой повтор apply должен сначала проверять точный `vendorCode`, ошибки и
   корзину.

4. Для общего экспорта карточек WB использовать стабильную пагинацию:

```json
{
  "settings": {
    "sort": {"ascending": true},
    "cursor": {"limit": 100},
    "filter": {"withPhoto": -1}
  }
}
```

Без `sort.ascending=true` общий снимок может пропускать карточки, хотя точечный
поиск по `vendorCode` их находит.

5. Две карточки были в корзине WB и восстановлены через
`POST /content/v2/cards/recover`:

```text
chev_raz_pict0015
chev_rusob_pict0001
```

## Команды CLI

Read-only catalog check:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli fetch-catalog
```

Dry-run плана создания WB-карточек:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli plan-wb-card-create
```

Apply создания WB-карточек защищен флагами:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli apply-wb-card-create \
  --plan-run-id <RUN_ID> \
  --confirmed-by-user \
  --allow-manual-review
```

Без явного `--confirmed-by-user` и `--allow-manual-review` write-операция не
должна запускаться.

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
- карточки товаров;
- фото;
- ответы покупателям;
- поставки;
- остатки.

Нельзя делать новые write-операции без явного подтверждения владельца.

## Что не менялось

Во время создания WB-карточек не менялись:

- цены;
- остатки;
- скидки;
- акции;
- реклама;
- ответы на отзывы/вопросы;
- поставки.

## Рекомендуемый следующий шаг

Не начинать сразу с кнопок Telegram. Следующий рациональный шаг - оформить
task-runner как основу будущего бота:

1. Довести `tasks/registry.py` до реального реестра задач.
2. Описать универсальную модель task result:
   `run_id`, `status`, `risk`, `inputs`, `artifacts`, `summary`, `errors`.
3. Доработать `safety/approvals.py` и `safety/locks.py` под реальные pending /
   approved файлы.
4. Добавить read-only `/status` или CLI `status`, который показывает:
   актуальность каталога, последние run-отчеты, расхождения Ozon/WB, ошибки API.
5. После этого выбирать первый бизнес-сценарий:
   цены/скидки, акции, Ozon elastic boosting или отзывы/вопросы.

Самый безопасный следующий сценарий после каталога:

```text
status/dashboard read-only
```

Он даст основу для первой Telegram-команды без риска внешних изменений.

## Обновление 2026-06-11

После явного подтверждения владельца применены акции Ozon/WB:

```text
actions_apply_20260610T234323: overall_status ok
Ozon: 12 update + 5 deactivate, verify ok
WB: guarded upload 151 rows, upload_id 138255979
```

Постоянная инструкция:

```text
data/planning/actions_apply_runbook.md
```

Session/status слой укреплен:

```text
src/takterra_agent/sessions/
data/planning/session_manager_runbook.md
deploy/systemd/user/
```

Сессии переведены на `systemd --user`:

```text
takterra-ozon-keeper.service
takterra-ozon-session-refresh.timer
takterra-wb-session-refresh.timer
```

Контроль:

```text
sessions_status_20260611T000502: overall_status ok
status_preflight_20260611T000512: overall_status ok
```

Перед продолжением работы новому агенту читать:

```text
AGENTS.md
data/planning/recommendations_index.md
data/planning/session_manager_runbook.md
data/planning/actions_apply_runbook.md
data/planning/marketplace_control_bot_discussion.md
```
