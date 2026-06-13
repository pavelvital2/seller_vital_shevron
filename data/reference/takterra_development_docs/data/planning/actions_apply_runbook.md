# Actions Apply Runbook

Дата создания: 2026-06-11.

Назначение: постоянная инструкция по применению согласованного пакета Ozon
Elastic Boosting и WB скидок `65-50-50`.

## Safety

Операция относится к опасным: меняет участие в акции и скидки/цены.

Обязательная цепочка:

```text
status-preflight -> dry-run -> review -> pending package -> explicit approve -> apply -> verify -> result
```

Без явной команды владельца с подтверждением apply запуск запрещен.

## Команда Apply

Для согласованного pending-пакета:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli apply-actions \
  --pending-id actions_apply_pending_20260610T232837 \
  --confirmed-by-user
```

Что делает команда:

1. Проверяет явный флаг `--confirmed-by-user`.
2. Выполняет свежий `status-preflight`.
3. Выполняет свежий Ozon Elastic dry-run.
4. Выполняет свежий WB dry-run по схеме `65-50-50`.
5. Сравнивает свежие counts с pending manifest.
6. Формирует минимальный Ozon apply:
   - только строки с изменившейся ценой акции;
   - строки деактивации без остатка.
7. Формирует WB guarded payload:
   - только изменившиеся строки;
   - только строки, присутствующие в master catalog.
8. Если WB guarded payload пустой, фиксирует WB как успешный no-op и не
   отправляет пустой upload-запрос в API WB.
9. Применяет Ozon.
10. Проверяет Ozon active list.
11. Загружает WB task, если в guarded payload есть строки.
12. Опрашивает WB status/details, если WB task был создан.
13. Сохраняет result artifacts.

Если drift-check не совпадает с pending manifest, команда должна завершиться до
write-операций.

## Результат первого Apply

2026-06-10 владелец явно подтвердил применение Ozon и WB.

Результат:

```text
run_id: actions_apply_20260610T234323
overall_status: ok
preflight: status_preflight_20260610T234323 ok
```

Ozon:

```text
fresh dry-run: ozon_elastic_plan_20260610T234407
activate/update rows applied: 12
deactivate rows applied: 5
verify status: ok
rejected: none
```

WB:

```text
fresh dry-run: wb_actions_discount_plan_65-50-50_20260610T234410
guarded payload rows applied: 151
excluded rows: 2
upload_id: 138255979
http_status: 200
history status: 5
overAllGoodsNumber: 151
successGoodsNumber: 131
```

Исключенные WB-only строки:

```text
chev_kit2_mvd_pict0004
nash_kit2_mvd_pict0004
```

Причина исключения: строки есть в WB price snapshot/точечном поиске WB, но
отсутствуют в master catalog и не находятся на Ozon.

## Артефакты

```text
data/runs/2026-06-10/actions_apply_20260610T234323/summary.json
data/runs/2026-06-10/actions_apply_20260610T234323/actions_apply_result.md
data/runs/2026-06-10/actions_apply_20260610T234323/processed/wb_guarded_rows.csv
data/runs/2026-06-10/actions_apply_20260610T234323/processed/wb_excluded_rows.csv
data/runs/2026-06-10/actions_apply_20260610T234323/raw/wb_upload_status_polls.json
```

## После Apply

После apply обязательно:

1. Выполнить `status-preflight`.
2. Проверить, что `overall_status: ok`.
3. Обновить этот runbook, `marketplace_control_bot_discussion.md`,
   `project_map.md` и `recommendations_index.md`.
4. Если была внештатная ситуация и она решена, добавить ее в профильный
   runbook.

Контроль после apply и после перехода на systemd:

```text
status_preflight_20260611T000512: overall_status ok
```

## Внештатная ситуация WB Price Quarantine

2026-06-11 повторный apply WB `65-50-50` на 22 строки привел к частичному
результату: 2 строки применились, 20 строк с переходом `0 -> 50` WB отклонил.
Попытка recovery `0 -> 49` перевела эти 20 товаров в Price Quarantine.

Разбор и успешное восстановление описаны в:

```text
data/planning/wb_price_quarantine_runbook.md
data/runs/2026-06-11/wb_price_quarantine_lk_release_20260611T101601Z/
data/runs/2026-06-11/wb_discount_stage_49_apply_then_50_20260611T104150Z/
```

Уточнение: `Keep Current Price` отменяет новую скидку. Если цель - оставить
скидку по схеме, в карантине использовать `Apply New Price`.

Рабочая корректирующая последовательность для этих 20 строк:

```text
0 -> upload 49 -> quarantine -> Apply New Price -> current 49 -> upload 50 -> current 50
```

Правило для будущих WB apply: при сообщении WB о слишком резком снижении цены не
подбирать случайные промежуточные скидки без фиксации сценария. Сначала
остановить upload, снять quarantine/status snapshot и рассчитать staged-переход
с учетом порогов WB.
