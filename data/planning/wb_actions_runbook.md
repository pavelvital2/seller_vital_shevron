# WB Actions Runbook

## Итог

Для Vital Shevron схема WB акций/скидок по умолчанию:

```text
70-55-55
```

Расшифровка:

- `70` - порог скидки до fallback;
- `55` - fallback для товаров вне активных акций;
- `55` - fallback для товаров, где расчетная скидка выше порога.

## Dry-run

```bash
PYTHONPATH=src NODE_PATH=/home/Codex/agent-tools/node/node_modules \
  /home/Codex/agent-tools/python/bin/python -m takterra_agent.cli plan-wb-actions-discounts
```

Если владелец отдельно попросил другую схему:

```bash
PYTHONPATH=src NODE_PATH=/home/Codex/agent-tools/node/node_modules \
  /home/Codex/agent-tools/python/bin/python -m takterra_agent.cli plan-wb-actions-discounts --scheme 70-55-55
```

Команда выполняет read-only/dry-run: читает активные акции и текущие цены,
строит XLSX/CSV/JSON preview и не загружает скидки в WB.

Важно для последующей аналитики и сводных отчетов: технический CSV
`wb-discount-calculation-active-actions-<scheme>.csv` формируется с
разделителем `;`, а не запятой. При чтении через Python использовать:

```python
csv.DictReader(file, delimiter=";")
```

Если прочитать этот CSV с разделителем по умолчанию `,`, колонки склеятся:
`Причина`, `Действие`, `Акций`, скидки и артикулы будут разобраны неверно.
Для машинной сводки также можно брать `summary.json`; для ручной проверки
предпочтителен XLSX.

## Формат отчета владельцу

Telegram-вывод строить по общему стандарту
`data/planning/chat_report_templates.md`. Для WB акций сохранять короткий
формат:

```text
Краткий вывод: по схеме <scheme> <итог>. Dry-run, скидки не загружались.

Отчет:
run_id: <run_id>
mode: dry-run
scheme: <scheme>
apply_performed: false

Сводка:
всего товаров в ценах WB: <n>
в активных акциях: <n>
вне активных акций: <n>
в нескольких акциях: <n>
изменить скидку: <n>
повысить скидку: <n>
снизить скидку: <n>
не менять: <n>

Причины по строкам:
<reason>: <count>

Акции:
активные: <n>
будущие: <n>

Файлы отчета:
<paths>
```

## Apply

Изменение скидок WB - опасная операция. Требуется цепочка:

```text
read-only -> dry-run -> review -> approved -> apply -> verify -> result
```

Нельзя использовать старую схему `65-50-50` для Vital Shevron, если владелец не
попросил ее явно.

После согласования владельцем применять отдельной командой WB, а не legacy
`apply-actions`, потому что `apply-actions` оставлен для старого combined
сценария и пересчитывает WB по старой схеме.

```bash
PYTHONPATH=src NODE_PATH=/home/Codex/agent-tools/node/node_modules \
  /home/Codex/agent-tools/python/bin/python -m takterra_agent.cli apply-wb-actions-discounts \
  --plan-run-id <approved_wb_actions_discount_plan_run_id> \
  --confirmed-by-user
```

Apply выполняет:

- свежий `status-preflight`;
- свежий dry-run WB по схеме из утвержденного плана;
- drift-check payload строк `nmID + price + discount`;
- upload в официальный WB endpoint
  `https://discounts-prices-api.wildberries.ru/api/v2/upload/task`;
- проверку статуса upload через history/buffer endpoints;
- сохранение `summary.json`, `wb_actions_discount_apply_result.md`,
  `drift_check.json`, отправленного payload и WB upload response.

## Штатный apply 2026-06-16

Последний подтвержденный штатный сценарий по схеме `70-55-55`:

- approved dry-run: `wb_actions_discount_plan_70-55-55_20260616T084545`;
- apply: `wb_actions_discount_apply_70-55-55_20260616T085150`;
- fresh preflight: `status_preflight_20260616T085150`, статус `ok`;
- fresh dry-run: `wb_actions_discount_plan_70-55-55_20260616T085229`;
- drift-check: payload совпал, added/removed пустые;
- отправлено в WB: `2` строки;
- WB upload ID: `166451487`;
- verify: `ok`, WB history показал `2/2` successful goods;
- затронутые vendorCode: `111046`, `111052`, скидка приведена к `70%`;
- отчет результата:
  `data/runs/2026-06-16/wb_actions_discount_apply_70-55-55_20260616T085150/wb_actions_discount_apply_result.md`.

Повторный apply по этому же плану без нового dry-run/review не выполнять.

## Внештатные ситуации

### Fresh snapshot WB падает с `Failed to fetch`

Подтвержденный сценарий 2026-06-18:

- `apply-wb-actions-discounts` остановился до upload на fresh dry-run;
- ошибка была в скачивании snapshot активных акций через ЛК:
  `page.evaluate: TypeError: Failed to fetch`;
- upload в WB при такой ошибке не выполняется, скидки не меняются;
- отдельный повторный `plan-wb-actions-discounts --scheme 70-55-55` прошел
  успешно и подтвердил тот же расчет;
- повторный штатный `apply-wb-actions-discounts` после успешного fresh plan
  выполнил upload и verify.

Правило восстановления:

1. Не загружать старый payload вручную в обход fresh-check.
2. Проверить, что нет зависших WB-процессов с профилем
   `.sessions/wb/browser-profile`.
3. Повторить fresh dry-run:

   ```bash
   PYTHONPATH=src NODE_PATH=/home/Codex/agent-tools/node/node_modules \
     /home/Codex/agent-tools/python/bin/python -m takterra_agent.cli plan-wb-actions-discounts --scheme 70-55-55
   ```

4. Если fresh dry-run успешен и расчет не изменился критично, повторить apply
   штатной командой с новым или подтвержденным plan run id.
5. Успешной операцией считать только результат, где WB upload получил id, а
   verify показал `successful goods` по всем строкам.

Проверенный результат 2026-06-18:

- approved plan после восстановления:
  `wb_actions_discount_plan_70-55-55_20260618T082707`;
- apply: `wb_actions_discount_apply_70-55-55_20260618T083122`;
- fresh plan внутри apply: `wb_actions_discount_plan_70-55-55_20260618T083207`;
- drift-check: `approved_payload_rows=248`, `fresh_payload_rows=248`,
  `added=[]`, `removed=[]`;
- upload ID: `167128951`;
- verify: `248/248` successful goods.
