# WB actions/discounts runbook

Дата создания: 2026-06-10.

Назначение: постоянный порядок подготовки dry-run по акциям и скидкам
Wildberries, включая схему `65-50-50`.

## Safety

Скидки и участие в акциях влияют на цены, поэтому относятся к опасным операциям.

Обязательная цепочка:

```text
status-preflight -> read-only snapshot -> dry-run -> review -> explicit approve -> apply -> verify -> result
```

Текущий сценарий реализован только как read-only/dry-run. Upload скидок в WB не
выполняется без явного подтверждения владельца.

## Подготовка

Перед расчетом выполнить:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli status-preflight
```

Продолжать можно только если:

- WB API имеет статус `ok`;
- WB LK keepalive имеет статус `ok`;
- активный продавец в ЛК WB подтвержден как `ИП Рантусова`;
- master catalog имеет статус `ok`.

## Dry-run 65-50-50

Команда:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli plan-wb-actions-discounts --scheme 65-50-50
```

Что делает задача:

- открывает WB ЛК через локальный persistent profile проекта;
- читает необходимые LK-токены только внутри браузерного контекста и не выводит
  их в логи;
- скачивает список активных и будущих промо;
- скачивает Excel-файлы по товарам, уже участвующим в активных акциях;
- скачивает текущий список цен и скидок;
- строит расчет по каждому товару;
- сохраняет отчет, CSV/XLSX и preview payload для WB upload task;
- не отправляет upload в WB.

## Схема 65-50-50

Текущая интерпретация схемы:

```text
65 - порог максимальной допустимой расчетной скидки;
50 - финальная скидка для товара вне активных акций;
50 - финальная скидка для товара, у которого расчет до порога выше 65%.
```

Для товара в активных акциях задача берет:

- минимальную `Recommended promo discount` из файлов акций;
- максимальную `Target promo price` из файлов акций;
- расчетную скидку от текущей базовой цены до максимальной плановой цены;
- меньшую из двух скидок как `Скидка до порога`.

Если `Скидка до порога > 65`, финальная скидка ставится `50`. Если товар не
найден в активных акциях, финальная скидка также ставится `50`.

## Артефакты

Каждый запуск сохраняется в:

```text
data/runs/YYYY-MM-DD/wb_actions_discount_plan_65-50-50_YYYYMMDDTHHMMSS/
```

Основные файлы:

```text
wb-discount-calculation-active-actions-65-50-50.md
wb-discount-calculation-active-actions-65-50-50.csv
wb-discount-calculation-active-actions-65-50-50.xlsx
wb-upload-payload-preview-65-50-50.json
wb-upload-changed-rows-65-50-50.csv
summary.json
raw/actions/cabinet-actions-snapshot.json
raw/actions/excel/
raw/prices/
```

Если снимок уже скачан, расчет можно повторить без повторного входа в ЛК:

```bash
PYTHONPATH=src python3 -m takterra_agent.cli plan-wb-actions-discounts \
  --scheme 65-50-50 \
  --actions-dir data/runs/2026-06-10/wb_actions_discount_plan_65-50-50_20260610T230625/raw/actions \
  --prices-json data/runs/2026-06-10/wb_actions_discount_plan_65-50-50_20260610T230625/raw/prices/current-prices-list-goods-filter-20260610T200625Z.json
```

## Последний dry-run

Актуальный запуск:

```text
run_id: wb_actions_discount_plan_65-50-50_20260610T231441
scheme: 65-50-50
total_goods: 205
in_promos: 136
outside_promos: 69
multiple_promos: 136
to_change: 153
increase: 20
decrease: 133
no_change: 52
active_promos: 4
future_promos: 2
excel_files: 4
apply_performed: false
```

Итоговый Excel не пустой:

```text
wb-discount-calculation-active-actions-65-50-50.xlsx: 206 непустых строк с заголовком
```

Артефакты:

```text
data/runs/2026-06-10/wb_actions_discount_plan_65-50-50_20260610T231441/wb-discount-calculation-active-actions-65-50-50.md
data/runs/2026-06-10/wb_actions_discount_plan_65-50-50_20260610T231441/wb-discount-calculation-active-actions-65-50-50.xlsx
data/runs/2026-06-10/wb_actions_discount_plan_65-50-50_20260610T231441/wb-upload-payload-preview-65-50-50.json
```

## Внештатная ситуация: Excel WB выглядит пустым

2026-06-10 WB выгрузил Excel-файлы с английскими заголовками и некорректной
размерностью листа для `openpyxl` в `read_only`-режиме. Визуально и при обычном
чтении файлы были заполнены:

```text
49, 137, 137 и 137 непустых строк в 4 Excel-файлах активных акций
```

Решение:

- поддерживать русские и английские алиасы заголовков;
- нормализовать пробелы и `NBSP` в заголовках;
- читать WB promo Excel в обычном режиме `openpyxl`, а не `read_only=True`.

Если ситуация повторится, сначала проверить фактическое число строк в исходных
Excel, а не считать акцию пустой.

Рекомендация по улучшению:

- сделать гибридный парсер `read_only -> normal mode -> diagnostic report`;
- считать `read_only` успешным только если найдены все обязательные колонки и
  больше одной строки;
- при fallback фиксировать в отчете имя файла, найденные заголовки и количество
  строк;
- отдельно исследовать JSON-эндпоинт WB ЛК для товаров акции и в перспективе
  сделать его основным источником, а Excel оставить резервным вариантом.

## Перед apply

Перед будущей загрузкой скидок обязательно:

- повторить свежий `status-preflight`;
- получить свежий WB snapshot;
- сверить все `vendorCode` из WB price/payload с master catalog;
- заблокировать или явно вынести на подтверждение владельца строки WB price,
  которые есть в ценах/точечном поиске WB, но отсутствуют в master catalog;
- проверить строки `increase` и `decrease`;
- отдельно просмотреть товары вне активных акций;
- получить явное подтверждение владельца;
- после upload проверить статус WB task и фактические скидки.

## Внештатная ситуация: WB price видит больше товаров, чем master catalog

2026-06-10 WB price snapshot вернул 205 уникальных `vendorCode`, а свежий
`fetch-catalog` через полный список WB Content API вернул 203 карточки.

Две строки были в WB prices и находились точечным WB Content API `textSearch`,
но отсутствовали в полном обходе каталога и не находились на Ozon:

```text
chev_kit2_mvd_pict0004
nash_kit2_mvd_pict0004
```

Для таких строк правило apply:

- по умолчанию исключать из payload;
- сохранять отдельный excluded CSV;
- применять только после явного подтверждения владельца или после исправления
  master catalog;
- добавить проверку `payload vendorCode -> master_sku` в будущий apply guard.

## Первый apply

2026-06-10 выполнен первый apply WB скидок по схеме `65-50-50` после явного
подтверждения владельца.

Общий runbook:

```text
data/planning/actions_apply_runbook.md
```

Результат WB:

```text
run_id: actions_apply_20260610T234323
fresh dry-run: wb_actions_discount_plan_65-50-50_20260610T234410
guarded payload rows applied: 151
excluded rows: 2
upload_id: 138255979
http_status: 200
history status: 5
overAllGoodsNumber: 151
successGoodsNumber: 131
```

Исключенные строки:

```text
chev_kit2_mvd_pict0004
nash_kit2_mvd_pict0004
```

Артефакты:

```text
data/runs/2026-06-10/actions_apply_20260610T234323/actions_apply_result.md
data/runs/2026-06-10/actions_apply_20260610T234323/processed/wb_guarded_rows.csv
data/runs/2026-06-10/actions_apply_20260610T234323/processed/wb_excluded_rows.csv
data/runs/2026-06-10/actions_apply_20260610T234323/raw/wb_upload_response.json
data/runs/2026-06-10/actions_apply_20260610T234323/raw/wb_upload_status_polls.json
```

## Внештатная ситуация: WB keepalive не нашел маркер продавца

2026-06-11 перед read-only проверкой акций fresh `status-preflight` вернул
ошибку WB-сессии:

```text
Expected seller marker not found
```

При этом WB API был `ok`, логин в ЛК был активен, блокировки и запроса логина не
было. Повторный ручной запуск:

```bash
node scripts/sessions/wb_session_keepalive.js
```

успешно подтвердил продавца `ИП Рантусова` на `cmp.wildberries.ru`, экспортировал
state и позволил продолжить read-only dry-run.

Правило на будущее:

- если WB keepalive один раз не нашел маркер продавца, не запускать WB
  apply/write-операции;
- выполнить повторный keepalive;
- продолжать read-only snapshot/dry-run только если повторный keepalive вернул
  `ok` и `stateExported: true`;
- если повторная проверка снова не видит продавца, остановиться и вручную
  проверить активного продавца в ЛК WB.

Рекомендация: добавить в WB keepalive автоматический retry seller-marker перед
финальным статусом `error` и сохранять диагностический status без секретов.

## Проверка акций 2026-06-11

Read-only проверка сохранена:

```text
data/runs/2026-06-11/actions_check_20260611T082143/actions_check_report.md
```

WB dry-run:

```text
run_id: wb_actions_discount_plan_65-50-50_20260611T082143
total_goods: 205
in_promos: 136
outside_promos: 69
to_change: 22
increase: 20
decrease: 2
no_change: 183
active_promos: 4
future_promos: 2
excel_files: 7
```

Интерпретация:

- схема `65-50-50` дала 22 строки к изменению;
- 20 товаров вне активных акций имеют текущую скидку `0`, схема предлагает
  `50`;
- 2 строки `nash_kit2_mvd_pict0004` и `chev_kit2_mvd_pict0004` снова видны в
  WB prices, но отсутствуют в master catalog.

Правило для review/pending: показывать все 22 строки как результат схемы. Две
строки вне master catalog помечать `blocked_by_master_catalog_guard` и применять
только после отдельного решения владельца или исправления master catalog.

Уточнение владельца 2026-06-11: для операций WB схема и актуальные данные WB ЛК
имеют приоритет над master catalog. Если строка есть в актуальных файлах акций
или WB price snapshot, она релевантна для расчета и применения WB-схемы. Master
catalog в такой ситуации является диагностикой качества каталога, а не
автоматическим блокером WB-схемы. Расхождение нужно разбирать после apply.

## Apply WB `65-50-50` 2026-06-11

После явного указания владельца применить по схеме выполнен свежий dry-run:

```text
run_id: wb_actions_discount_plan_65-50-50_20260611T092255
to_change: 22
increase: 20
decrease: 2
```

Затем отправлен WB upload на все 22 строки схемы без исключений:

```text
run_id: wb_actions_discount_apply_65-50-50_20260611T092528
upload_id: 138350059
overAllGoodsNumber: 22
successGoodsNumber: 2
```

Результат:

- 2 строки `54 -> 53` применились успешно:
  - `nash_kit2_mvd_pict0004`;
  - `chev_kit2_mvd_pict0004`.
- 20 строк `0 -> 50` WB отклонил с ошибкой:

```text
Changes weren't saved: New prices are more than twice lower than the current ones.
Please lower them gradually
```

Была выполнена попытка recovery первым постепенным шагом `0 -> 49`:

```text
run_id: wb_actions_discount_apply_recovery_65-50-50_20260611T092644
upload_id: 138350340
overAllGoodsNumber: 20
successGoodsNumber: 0
```

WB отклонил и этот шаг:

```text
New price is several times lower than the current price. Item has been moved to Price Quarantine
```

После этого дальнейшие upload по этим 20 строкам остановлены, чтобы не ухудшать
состояние товаров. Следующий шаг - разобраться с Price Quarantine в ЛК WB/API и
добавить отдельный сценарий выхода из карантина или безопасной staged-стратегии.

## Решение Price Quarantine 2026-06-11

Профильная инструкция:

```text
data/planning/wb_price_quarantine_runbook.md
```

Сначала проверен официальный WB API:

```text
GET  /api/v2/quarantine/goods
POST /api/v2/upload/task
```

API подтвердил 20 товаров в карантине, но попытка восстановить старую цену и
скидку `0` через upload task была отклонена:

```text
Specified prices and discounts are already set
```

Так как отдельного официального API release не найдено, операция выполнена через
ЛК WB:

```text
ЛК: https://seller.wildberries.ru/discount-and-prices/quarantine
действие: Keep Current Price
внутренний LK endpoint: POST /quarantine/goods/delete
run_id: wb_price_quarantine_lk_release_20260611T101601Z
before_count: 20
after_count: 0
overall_status: ok
```

Уточнение: это действие отменило quarantined-изменение и вернуло скидку к `0%`.
Для цели владельца "оставить скидку по схеме" правильным действием является
`Apply New Price`, а не `Keep Current Price`.

Корректирующая операция:

```text
run_id: wb_discount_stage_49_apply_then_50_20260611T104150Z
0 -> upload 49 -> quarantine -> Apply New Price -> current 49 -> upload 50 -> current 50
final_unique_discounts: 50
final_quarantine_count: 0
overall_status: ok
```

Правило: в карантине всегда сначала определить цель:

- отменить изменение - `Keep Current Price`;
- сохранить новую скидку/цену - `Apply New Price`.

## Правило staged-скидки для `0 -> 50`

По инструкции WB Partners по карантину цен:

```text
https://seller.wildberries.ru/instructions/ru/ru/material/price-quarantine
```

Порог карантина по умолчанию - снижение цены на `33,3%` (`1,5` раза), а
настраиваемые пороги по категории доступны до `47,5%`. Снижение на `50%` и
больше WB просит выполнять поэтапно.

Для товаров, у которых текущая скидка `0%`, а схема `65-50-50` требует `50%`,
прямой upload `0 -> 50` не использовать как единственный шаг. Рабочий порядок,
проверенный 2026-06-11:

```text
1. upload 49
2. если товар попал в карантин - Apply New Price
3. после фактической скидки 49 выполнить upload 50
4. проверить: текущая скидка 50, карантин 0
```

Артефакты проверки:

```text
data/runs/2026-06-11/wb_discount_stage_49_apply_then_50_20260611T104150Z/
data/runs/2026-06-11/wb_current_discounts_final_check_20260611T104605Z/
```
