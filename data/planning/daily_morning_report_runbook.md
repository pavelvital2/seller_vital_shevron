# Daily Morning Report Runbook

## Команды

Технический отчет:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli daily-morning-report
```

Селлерский v2:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli daily-morning-report --seller-v2
```

## Правило до унификации SKU

Отчет должен показывать Ozon и WB как отдельные контуры. Объединение по товару
разрешено только там, где есть подтвержденный mapping.

