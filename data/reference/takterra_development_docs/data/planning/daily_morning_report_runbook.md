# Daily Morning Report Runbook

Дата создания: 2026-06-11.

Назначение: ежедневный read-only управленческий отчет по проекту TAKTERRA,
Ozon и Wildberries.

## Цель

Каждое утро получать короткую картину:

- живы ли API и ЛК-сессии;
- не просрочен ли refresh Ozon/WB;
- согласован ли master catalog;
- какие операции агент выполнял последними;
- есть ли pending-пакеты;
- какие рекомендации и решения требуют внимания;
- где лежат артефакты.

## Команда

Обычный запуск со свежим `status-preflight`:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli daily-morning-report
```

Селлерский отчет v2 со свежим `status-preflight`:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli daily-morning-report --seller-v2
```

Быстрая пересборка отчета по последнему сохраненному preflight:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli daily-morning-report --skip-preflight-refresh
```

Быстрая пересборка v2 по последнему preflight:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli daily-morning-report --seller-v2 --skip-preflight-refresh
```

## Что входит в MVP v1

1. Executive summary.
2. Project health:
   - `status-preflight`;
   - Ozon Seller API;
   - Ozon Performance API;
   - WB API;
   - Ozon/WB session freshness.
3. Catalog health:
   - rows;
   - matched rows;
   - Ozon-only/WB-only;
   - barcode mismatch.
4. Marketplace operations:
   - последний `actions_apply`;
   - последний Ozon Elastic plan;
   - последний WB actions plan.
5. Sessions:
   - Ozon/WB refresh status;
   - age seconds;
   - watchdog source.
6. Pending packages.
7. Today's action list.
8. Recent runs.
9. Artifact links.

## Артефакты

Каждый запуск сохраняет:

```text
data/runs/YYYY-MM-DD/daily_morning_report_YYYYMMDDTHHMMSS/summary.json
data/runs/YYYY-MM-DD/daily_morning_report_YYYYMMDDTHHMMSS/daily_morning_report.md
```

## Safety

Сценарий read-only:

- не меняет цены;
- не меняет скидки;
- не меняет карточки;
- не отвечает покупателям;
- не меняет рекламные кампании;
- не записывает секреты.

Обычный запуск может выполнять `status-preflight`, который обновляет только
локальный storage state ЛК после успешного keepalive.

## Расширение v2

2026-06-11 добавлен режим `--seller-v2`. Его назначение - показывать сначала
то, что важно селлеру, а техническое состояние держать в конце как приложение.

В v2 входят read-only блоки:

- Ozon заказы за вчера/сегодня через Seller API `POST /v1/analytics/data`;
- Ozon остатки/out-of-stock/low-stock через `POST /v4/product/info/stocks`;
- WB заказы за вчера/сегодня через `GET /api/v1/supplier/orders`;
- WB предварительные продажи/возвраты через `GET /api/v1/supplier/sales`;
- WB остатки через legacy `GET /api/v1/supplier/stocks`;
- WB неотвеченные отзывы и вопросы через Feedbacks API;
- последние примененные акции из локальных `actions_apply_*`;
- короткий список действий на сегодня;
- техническое приложение: preflight, API, сессии, каталог.

Ограничения v2:

- WB `GET /api/v1/supplier/stocks` помечен Wildberries как deprecated и должен
  быть заменен на warehouse remains report до 2026-06-23.
- Ozon отзывы/вопросы пока не включены: нужен отдельный read-only счетчик через
  официальный API или проверенный ЛК/CDP контур.
- Реклама и расход продвижения пока не включены: нужен отдельный read-only блок
  Ozon Performance/WB ads.
- Полные строки заказов/продаж не сохраняются в артефакты v2; сохраняются
  агрегированные показатели, чтобы не разносить лишние операционные данные.

Каждый следующий блок должен иметь свой источник данных, runbook и тесты.

## Первый запуск

2026-06-11 сформирован первый отчет:

```text
run_id: daily_morning_report_20260611T065840
overall_status: ok
fresh preflight: status_preflight_20260611T065840 ok
```

После правки формата Markdown пересобрана read-only версия по последнему
preflight:

```text
run_id: daily_morning_report_20260611T070112
overall_status: ok
```

Актуальный Markdown:

```text
data/runs/2026-06-11/daily_morning_report_20260611T070112/daily_morning_report.md
```

## Первый запуск v2

2026-06-11 реализован и протестирован режим `--seller-v2`.

Проверка:

```text
PYTHONPATH=src pytest -q
26 passed
```

Актуальный v2-отчет создается в:

```text
data/runs/YYYY-MM-DD/daily_morning_report_v2_YYYYMMDDTHHMMSS/daily_morning_report_v2.md
```

Первый живой v2-запуск:

```text
run_id: daily_morning_report_v2_20260611T072124
status_preflight: status_preflight_20260611T072124 ok
overall_status: warning
reason: WB sales endpoint returned HTTP 429 rate limit; WB stocks uses legacy endpoint
report: data/runs/2026-06-11/daily_morning_report_v2_20260611T072124/daily_morning_report_v2.md
summary: data/runs/2026-06-11/daily_morning_report_v2_20260611T072124/summary.json
```
