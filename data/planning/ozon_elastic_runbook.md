# Ozon Elastic Runbook

## Итог

Ozon Elastic Boosting относится к опасным операциям, потому что меняет участие
товаров в акции и action price. Apply разрешен только после dry-run, review,
явного подтверждения владельца, fresh preflight, drift-check и verify.

## Dry-run

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python -m takterra_agent.cli plan-ozon-elastic
```

Команда только читает Ozon API и сохраняет расчет:

```text
data/runs/<date>/ozon_elastic_plan_<timestamp>/
```

## Формат предварительного отчета владельцу

Telegram-вывод строить по общему стандарту
`data/planning/chat_report_templates.md`. Для Ozon Elastic сохранять короткий
формат:

```text
Краткий вывод: по Ozon Elastic <итог>. Dry-run, apply не выполнялся.

Отчет:
run_id: <run_id>
mode: dry-run
action_id: <action_id>
action_name: <action_name>
apply_performed: false

Сводка:
товаров в активной акции: <n>
кандидатов: <n>
уникальных товаров в расчете: <n>
добавить в акцию: <n>
обновить action price: <n>
из них с реальным изменением цены: <n>
снять с акции: <n>
пропустить кандидатов: <n>
blocked: <n>

Причины решений:
<reason_code>: <count>

К действиям:
добавить: <short rows>
изменить цены: <short summary/sample>
снять: <short rows>

Файлы отчета:
<paths>
```

## Apply

После согласования конкретного dry-run:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python -m takterra_agent.cli apply-ozon-elastic \
  --plan-run-id <ozon_elastic_plan_run_id> \
  --confirmed-by-user
```

Перед отправкой изменений команда выполняет:

- полный `status-preflight`;
- свежий `plan-ozon-elastic`;
- drift-check между согласованным и свежим планом;
- apply только если action id, состав строк и расчетные цены совпали;
- verify активных товаров и снятых строк после apply.

Команда не затрагивает WB.

## Если apply остановлен drift-check

Если `apply-ozon-elastic` останавливается с ошибкой
`Ozon Elastic drift-check failed`, значит запись в Ozon не выполнялась.
Нужно сравнить согласованный dry-run и свежий dry-run, который команда создала
перед apply.

Порядок действий:

- если изменились только непишущиеся справочные поля, все равно зафиксировать
  diff в отчете и проверить, что payload не меняется;
- если изменились `calculated_action_price`, состав товаров, `planned_action`
  или строки снятия из акции, применять нельзя без нового подтверждения
  владельца по свежему `plan-run-id`;
- владельцу нужно вывести короткий список изменившихся товаров: `offer_id`,
  название, старая и новая расчетная акционная цена, изменение остатка, если
  оно есть;
- после нового подтверждения запускать apply уже по свежему плану.

Причина: цены и остатки Ozon могут измениться между review и apply, а акция
Elastic меняет финансовые условия продажи. Drift-check защищает от записи по
устаревшему согласованию.

## Штатный apply 2026-06-16

Последний подтвержденный штатный сценарий:

- approved dry-run: `ozon_elastic_plan_20260616T084545`;
- apply: `ozon_elastic_apply_20260616T085051`;
- fresh preflight: `status_preflight_20260616T085051`, статус `ok`;
- fresh dry-run: `ozon_elastic_plan_20260616T085133`;
- drift-check: состав строк совпал;
- применено: `196` строк activate/update и `1` строка deactivate;
- verify: `ok`, расхождений по ценам `0`, снятых строк, оставшихся в акции,
  `0`;
- отчет результата:
  `data/runs/2026-06-16/ozon_elastic_apply_20260616T085051/ozon_elastic_apply_result.md`.

Повторный apply по этому же плану без нового dry-run/review не выполнять.

## Apply 2026-06-18 с восстановлением после drift-check

Операция 2026-06-18 подтвердила важный recovery-сценарий: Ozon может менять
расчетные цены и остатки между review и apply, поэтому первая попытка apply
может штатно остановиться до записи.

Ход операции:

- план `ozon_elastic_plan_20260618T092828` был подтвержден владельцем;
- apply `ozon_elastic_apply_20260618T093702` остановился до записи на
  `Ozon Elastic drift-check failed`;
- fresh plan `ozon_elastic_plan_20260618T093747` отличался от подтвержденного
  плана в `2` строках: `back0022` action price `416 -> 414`, `loop0001`
  stock `9 -> 8`;
- после отдельного подтверждения владельца fresh plan
  `ozon_elastic_plan_20260618T093747` был применен;
- успешный apply: `ozon_elastic_apply_20260618T093932`;
- fresh preflight: `status_preflight_20260618T093932`, статус `ok`;
- fresh dry-run перед записью: `ozon_elastic_plan_20260618T094019`;
- drift-check: состав activate/deactivate строк совпал;
- применено: `147` строк activate/update и `3` строки deactivate;
- verify: `ok`, расхождений по ценам `0`, снятых строк, оставшихся в акции,
  `0`;
- отчет результата:
  `data/runs/2026-06-18/ozon_elastic_apply_20260618T093932/ozon_elastic_apply_result.md`.

Если в будущем Ozon Elastic несколько раз подряд останавливается из-за
микро-дрейфа расчетных цен, не обходить проверку вручную. Нужен свежий review
изменившихся строк и явное подтверждение владельца по последнему `plan-run-id`.
