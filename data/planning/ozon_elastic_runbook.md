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
