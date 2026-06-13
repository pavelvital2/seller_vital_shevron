# Search Queries Runbook

Дата создания: 2026-06-12.

Назначение: read-only сбор топа поисковых запросов из ЛК Ozon и WB.

## Safety

Операция read-only. Write-действия в магазинах не выполняются.

Запрещено сохранять или выводить:

- cookies;
- storage state;
- API tokens;
- OTP/SMS/email-коды.

## Источники

WB:

```text
https://seller.wildberries.ru/search-analytics/popular-search-queries
```

Раздел ЛК: `Search Analytics -> WB search queries`.

Для запроса `шеврон` рабочий UI-фильтр:

```text
Search by search query = шеврон
interval = yesterday
sort = frequency desc
limit = 20
```

Ozon:

```text
https://seller.ozon.ru/app/analytics/what-to-sell/all-queries
```

Раздел ЛК: `Аналитика -> Поисковые запросы`.

Для запроса `шеврон` рабочий UI-фильтр:

```text
Поисковый запрос = шеврон
Период = 7 дней
sort = Популярность запроса desc
limit = 20
```

## API-first проверка

Для Ozon поисковых запросов проверялись read-only Seller API методы:

```text
POST /v1/search-queries/text
POST /v1/search-queries/top
```

На 2026-06-12 текущий доступ TAKTERRA вернул:

```text
Method available with Premium Pro subscription
```

Поэтому для Ozon источник текущего отчета - ЛК, а не Seller API.

Для WB текущий отчет снят через ЛК. Внутренний endpoint ЛК был обнаружен
read-only сетевым снимком, но прямой `fetch` вне UI-контекста вернул `401`.
Рабочий способ - вводить фильтр в UI и читать сформированный ЛК ответ/таблицу.

## Порядок выполнения

1. Прочитать `AGENTS.md`.
2. Проверить сессии:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli sessions status
```

3. Если Ozon разлогинен, восстановить штатно:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli restore-ozon-session --email <email>
```

4. Открыть WB ЛК в отдельном временном Playwright context через
   `.sessions/wb/wb_storage_state.json`.
5. Открыть Ozon ЛК через CDP `127.0.0.1:9444`.
6. Ввести фильтр `шеврон` в UI маркетплейса.
7. Сохранить raw UI/network артефакты и processed JSON.
8. Сформировать markdown-отчет в `data/runs/<date>/search_queries_<run_id>/`.

## Результат 2026-06-12

Отчет:

```text
data/runs/2026-06-12/search_queries_20260612T220200/search_queries_shevron_report.md
```

WB:

```text
period: yesterday
rows: 20
source: LK Search Analytics
```

Ozon:

```text
period: 7 days
rows: 20
source: LK Analytics / What to sell / All queries
```
