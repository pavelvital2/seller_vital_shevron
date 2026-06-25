# Ozon Elastic Runbook

## Итог

Ozon Elastic Boosting относится к опасным операциям, потому что меняет участие
товаров в акции и action price. Apply разрешен только после dry-run, review,
явного подтверждения владельца, fresh preflight, drift-check и verify.

## Dry-run

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python -m seller_agent.cli plan-ozon-elastic
```

Команда только читает Ozon API и сохраняет расчет:

```text
data/runs/<date>/ozon_elastic_plan_<timestamp>/
```

## Сравнение с Ozon Супербустинг

`Супербустинг` в Ozon API является отдельной акцией типа `STOCK_DISCOUNT`, а
не продолжением `Эластичного бустинга`
`MARKETPLACE_MULTI_LEVEL_DISCOUNT_ON_AMOUNT`. Поэтому товары для
супербустинга нельзя применять через elastic apply-контур.

Read-only анализ супербустинга должен отдельно проверить:

- наличие самой акции через `GET /v1/actions`;
- активные товары через `POST /v1/actions/products`;
- кандидатов через `POST /v1/actions/candidates`;
- текущие `price`, `old_price`, `min_price` через
  `POST /v5/product/info/prices`;
- FBO-остаток через `POST /v4/product/info/stocks`;
- разницу цены супербустинга против последнего Ozon Elastic dry-run.

Рабочее правило по решению:

- товар может быть кандидатом на тест только если `superboost_action_price >=
  min_price` и есть положительный FBO-остаток;
- если цена супербустинга ниже цены эластика, снижение цены считать платой за
  дополнительный бустинг и показывать владельцу в рублях по каждой позиции;
- массово включать все товары нельзя: сначала dry-run, review списка товаров,
  затем отдельный approved apply-контур для Ozon `STOCK_DISCOUNT`;
- товары с нулевым FBO-остатком, отсутствующей минимальной ценой или ценой
  супербустинга ниже `min_price` не включать без отдельного решения владельца.

Подтвержденный read-only снимок 2026-06-19:

- акция `Супербустинг`, `action_id=3876484`, тип `STOCK_DISCOUNT`;
- активных товаров: `0`;
- кандидатов: `421`;
- прошли базовый фильтр `min_price + FBO stock`: `91`;
- не прошли по `min_price`: `320`;
- без FBO-остатка: `10`;
- у `90` из `91` подходящего товара цена супербустинга ниже актуального
  elastic dry-run;
- медианное снижение цены относительно эластика: `54` рубля за единицу.

Отчет:
`data/runs/2026-06-19/ozon_superboosting_probe_20260619T2020/ozon_superboosting_analysis.md`.

Подтвержденный apply 2026-06-19:

- owner approval: включить все подходящие `91` товара в `Супербустинг`;
- approved_id: `ozon_superboosting_20260619T2033_owner_approved_91`;
- preflight: `status_preflight_20260619T203209`, статус `ok`;
- apply: `ozon_superboosting_apply_20260619T203448`;
- fresh snapshot перед записью: `91` строк, drift/skip `0`;
- smoke-запись первой строки прошла успешно, затем применен остальной пакет;
- Ozon API принял `91` товар, rejected `0`;
- verify: `ok`, активных товаров в акции `91`, расхождений по цене `0`;
- follow-up контроль зафиксирован на `2026-06-20` и `2026-06-21` в
  `data/planning/followups.md`.

После этого apply не повторять по тому же approved_id: использовать idempotency
marker `data/approved/applied/`. Для отключения неэффективных товаров после
контроля продаж нужен отдельный dry-run/review/approval.

Baseline продаж для контроля результативности:

- baseline run: `ozon_superboosting_baseline_20260619T203748`;
- источник: Ozon Seller API `/v1/analytics/data`, dimensions `sku + day`,
  metrics `revenue + ordered_units`;
- для двухдневного контроля использовать заказанные штуки, а не выкупы,
  потому что выкупы запаздывают и не подходят для оперативной оценки
  супербустинга;
- основной период: `2026-06-12..2026-06-18`, 7 полных дней до apply;
- дополнительный период для низкооборачиваемых товаров:
  `2026-06-05..2026-06-18`, 14 дней;
- 7d baseline: `184` шт., `128452.00` руб., среднесуточно `26.2857` шт. по
  всему списку `91` товаров, `35` товаров с нулем заказов;
- 14d baseline: `364` шт., `256302.00` руб., среднесуточно `26` шт. по всему
  списку `91` товаров, `20` товаров с нулем заказов;
- сверку по каждому товару делать по CSV:
  `data/runs/2026-06-19/ozon_superboosting_baseline_20260619T203748/processed/ozon_superboosting_baseline_rows.csv`;
- помимо итогов по товару обязательно хранить дневную детализацию по каждому
  товару, включая нулевые дни:
  `data/runs/2026-06-19/ozon_superboosting_baseline_20260619T203748/processed/ozon_superboosting_baseline_daily_by_product.csv`;
- для владельца сохранять Excel workbook с вкладками `summary`, `by_product`
  и `daily_by_product`:
  `data/runs/2026-06-19/ozon_superboosting_baseline_20260619T203748/ozon_superboosting_baseline_91_products.xlsx`;
- правило контроля: на `2026-06-20` сравнить первый день после apply с
  `baseline_7d_avg_units_per_day`; на `2026-06-21` сравнить среднее за два
  дня после apply с `baseline_7d_avg_units_per_day`. `baseline_14d` использовать
  только как контекст для товаров с редкими продажами.

Первый контроль 2026-06-20:

- контрольный run: `ozon_superboosting_control_20260620T074113`;
- режим: read-only/control, изменений в Ozon не выполнялось;
- активных товаров в `Супербустинг`: `91` из `91`;
- расхождений по action price: `0`;
- ранний неполный день 2026-06-20 на 07:41 MSK: `7` заказанных штук,
  `4253.00` руб. выручки;
- вывод: это только ранний сигнал, потому что apply был 2026-06-19 в 20:34
  MSK. Решения `keep/remove` принимать после второго контроля 2026-06-21,
  сравнивая каждый товар индивидуально с `baseline_7d_avg_units_per_day`.

Проверка после корректировки Ozon Elastic 2026-06-20:

- проверочный run:
  `ozon_superboosting_verify_after_elastic_20260620T075506`;
- режим: read-only/verify, изменений в Ozon не выполнялось;
- проверялся `Супербустинг`, `action_id=3876484`, после apply Ozon Elastic
  `ozon_elastic_apply_20260620T074653`;
- ожидаемых товаров: `91`;
- активных товаров в `Супербустинг`: `91`;
- пропавших ожидаемых товаров: `0`;
- лишних активных товаров: `0`;
- расхождений по action price: `0`;
- вывод: корректировка `Эластичного бустинга` не изменила состав товаров и
  цены `Супербустинга`. Эти акции нужно продолжать контролировать как разные
  механики Ozon с разными `action_id`.

Проверка после корректировки Ozon Elastic 2026-06-21:

- проверочный run:
  `ozon_superboosting_verify_after_elastic_20260621T170803`;
- режим: read-only/verify, изменений в Ozon не выполнялось;
- проверялся `Супербустинг`, `action_id=3876484`, после apply Ozon Elastic
  `ozon_elastic_apply_20260621T170628`;
- ожидаемых товаров: `91`;
- активных товаров в `Супербустинг`: `91`;
- пропавших ожидаемых товаров: `0`;
- лишних активных товаров: `0`;
- расхождений по action price: `0`;
- вывод: корректировка `Эластичного бустинга` 2026-06-21 не изменила состав
  товаров и цены `Супербустинга`.

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
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python -m seller_agent.cli apply-ozon-elastic \
  --plan-run-id <ozon_elastic_plan_run_id> \
  --confirmed-by-user
```

Перед отправкой изменений команда выполняет:

- полный `status-preflight`;
- свежий `plan-ozon-elastic`;
- partial drift-check между согласованным и свежим планом;
- apply только по строкам, где write-payload не изменился:
  `planned_action`, `product_id`, `offer_id` и для activate/update также
  `calculated_action_price`;
- строки, где изменились действие, расчетная цена, состав строк или товар
  появился/исчез из payload, не применяются автоматически, сохраняются в
  `processed/skipped_drift_rows.json` и выводятся в итоговом отчете как
  требующие нового согласования;
- verify активных товаров и снятых строк после apply.

Команда не затрагивает WB.

## Partial drift-check

С 2026-06-19 применяется частичная логика Ozon Elastic:

- если владелец согласовал apply, агент не должен останавливать весь пакет
  из-за изменения одной или нескольких строк;
- неизменившиеся строки применяются автоматически;
- изменившиеся строки исключаются из apply и остаются непримененными;
- в итоговом отчете обязательно указывать количество примененных строк,
  количество непримененных строк и список товаров, по которым был drift;
- для непримененных строк нужен новый fresh dry-run/review/approval.

Жесткими стопами остаются:

- изменился `action_id`;
- `status-preflight` не `ok`;
- нет подтверждения владельца;
- повторный apply уже примененного approved plan заблокирован idempotency guard;
- verify после записи показывает расхождения.

## Если все строки остановлены drift-check

Если `apply-ozon-elastic` останавливается с ошибкой
`Ozon Elastic drift-check failed`, значит запись в Ozon не выполнялась. Для
актуальной версии это должно происходить только при жестком стопе, например
при изменении `action_id`, ошибке preflight или технической ошибке. Нужно
сравнить согласованный dry-run и свежий dry-run, который команда создала перед
apply.

Порядок действий:

- если изменились только часть строк payload, проверить, что partial apply
  действительно применил неизменившиеся строки и сохранил изменившиеся в
  `skipped_drift_rows.json`;
- если запись не выполнялась совсем, классифицировать причину жесткого стопа;
- владельцу нужно вывести короткий список изменившихся товаров: `offer_id`,
  название, старая и новая расчетная акционная цена, изменение остатка, если
  оно есть;
- после нового подтверждения запускать apply уже по свежему плану только для
  оставшихся строк.

Причина: цены и остатки Ozon могут измениться между review и apply, а акция
Elastic меняет финансовые условия продажи. Partial drift-check защищает от
записи по устаревшим строкам, но не блокирует весь пакет из-за нескольких
изменившихся товаров.

## Штатный apply 2026-06-19

Подтвержденный сценарий с предварительным drift-stop:

- первично подтвержденный dry-run: `ozon_elastic_plan_20260619T_actions_check`;
- первая попытка apply: `ozon_elastic_apply_20260619T_actions_check`;
- результат первой попытки: остановка до записи на `Ozon Elastic drift-check
  failed`;
- fresh plan после остановки: `ozon_elastic_plan_20260619T071426`;
- причина нового подтверждения: `pict0001` изменился с `update_action_price`
  на `deactivate_from_action` из-за остатка `1 -> 0`, `pict0144` изменил
  расчетную action price `414 -> 413`;
- после отдельного подтверждения владельца fresh plan применен;
- успешный apply: `ozon_elastic_apply_20260619T0718`;
- fresh preflight: `status_preflight_20260619T071758`, статус `ok`;
- fresh dry-run перед записью: `ozon_elastic_plan_20260619T071839`;
- drift-check: состав activate/deactivate строк совпал;
- применено: `95` строк activate/update и `2` строки deactivate;
- verify: `ok`, расхождений по ценам `0`, снятых строк, оставшихся в акции,
  `0`;
- отчет результата:
  `data/runs/2026-06-19/ozon_elastic_apply_20260619T0718/ozon_elastic_apply_result.md`.

Если часть строк ушла в drift, старый payload по этим строкам не применять. Для
оставшихся непримененных товаров всегда брать fresh plan и отдельное
подтверждение.

## Штатный apply 2026-06-20

Подтвержденный сценарий:

- approved dry-run: `ozon_elastic_plan_20260620T073712`;
- apply: `ozon_elastic_apply_20260620T074653`;
- fresh preflight: `status_preflight_20260620T074653`, статус `ok`;
- fresh dry-run перед записью: `ozon_elastic_plan_20260620T074732`;
- drift-check: `partial_apply_unchanged_rows`, skipped/drift `0`;
- применено: `37` строк activate/update и `1` строка deactivate;
- Ozon API rejected: `0`;
- verify: `ok`, расхождений по ценам `0`, снятых строк, оставшихся в акции,
  `0`;
- отчет результата:
  `data/runs/2026-06-20/ozon_elastic_apply_20260620T074653/ozon_elastic_apply_result.md`.

Повторный apply по `ozon_elastic_plan_20260620T073712` не выполнять:
idempotency marker сохранен в `data/approved/applied/`.

## Штатный apply 2026-06-21

Подтвержденный сценарий:

- approved dry-run: `ozon_elastic_plan_20260621T165916`;
- apply: `ozon_elastic_apply_20260621T170628`;
- fresh preflight: `status_preflight_20260621T170628`, статус `ok`;
- fresh dry-run перед записью: `ozon_elastic_plan_20260621T170708`;
- drift-check: `partial_apply_unchanged_rows`, skipped/drift `0`;
- применено: `46` строк activate/update и `2` строки deactivate;
- Ozon API rejected: `0`;
- verify: `ok`, расхождений по ценам `0`, снятых строк, оставшихся в акции,
  `0`;
- после apply отдельно проверен `Супербустинг`:
  `ozon_superboosting_verify_after_elastic_20260621T170803`, активных товаров
  `91/91`, расхождений по action price `0`;
- отчет результата:
  `data/runs/2026-06-21/ozon_elastic_apply_20260621T170628/ozon_elastic_apply_result.md`.

Повторный apply по `ozon_elastic_plan_20260621T165916` не выполнять:
idempotency marker сохранен в `data/approved/applied/`.

## Штатный apply 2026-06-22

Подтвержденный сценарий с partial drift:

- approved dry-run: `ozon_elastic_plan_actions_check_20260622`;
- apply: `ozon_elastic_apply_20260622_actions_check`;
- fresh preflight: `status_preflight_20260622T081605`, статус `ok`;
- fresh dry-run перед записью: `ozon_elastic_plan_20260622T081644`;
- drift-check: `partial_apply_unchanged_rows`;
- применено: `10` строк activate/update и `3` строки deactivate;
- Ozon API rejected: `0`;
- verify: `ok`, расхождений по ценам `0`, снятых строк, оставшихся в акции,
  `0`;
- skipped/drift: `1` свежая несогласованная строка `shtpict0016`,
  `product_id=3297582054`, `Шеврон на липучке Шторм Z бежевый`, action price
  `419 -> 417`;
- строку `shtpict0016` не применять без отдельного fresh review/approval;
- после apply отдельно проверен `Супербустинг` через read-only API:
  активных товаров `91/91`, отсутствующих `0`, лишних `0`, расхождений по
  action price `0`;
- отчет результата:
  `data/runs/2026-06-22/ozon_elastic_apply_20260622_actions_check/ozon_elastic_apply_result.md`.

Повторный apply по `ozon_elastic_plan_actions_check_20260622` не выполнять:
idempotency marker сохранен в `data/approved/applied/`.

## Штатный apply 2026-06-23

Подтвержденный сценарий:

- approved dry-run: `ozon_elastic_plan_actions_check_20260623`;
- apply: `ozon_elastic_apply_actions_check_20260623`;
- fresh preflight: `status_preflight_20260623T064724`, статус `ok`;
- fresh dry-run перед записью: `ozon_elastic_plan_20260623T064804`;
- drift-check: `partial_apply_unchanged_rows`, skipped/drift `0`;
- применено: `4` строки activate/update и `4` строки deactivate;
- Ozon API rejected: `0`;
- verify: `ok`, расхождений по ценам `0`, снятых строк, оставшихся в акции,
  `0`;
- после apply отдельно проверен `Супербустинг`:
  `ozon_superboosting_verify_after_elastic_20260623T064724`, активных товаров
  `91/91`, отсутствующих `0`, лишних `0`, расхождений по action price `0`;
- отчет результата:
  `data/runs/2026-06-23/ozon_elastic_apply_actions_check_20260623/ozon_elastic_apply_result.md`.

Повторный apply по `ozon_elastic_plan_actions_check_20260623` не выполнять:
idempotency marker сохранен в `data/approved/applied/`.

## Штатный apply 2026-06-24

Подтвержденный сценарий с partial drift:

- approved dry-run: `ozon_elastic_plan_actions_check_20260624`;
- apply: `ozon_elastic_apply_actions_check_20260624`;
- fresh preflight: `status_preflight_20260624T082726`, статус `ok`;
- fresh dry-run перед записью: `ozon_elastic_plan_20260624T082806`;
- drift-check: `partial_apply_unchanged_rows`;
- применено: `32` строки activate/update и `5` строк deactivate;
- Ozon API rejected: `0`;
- verify: `ok`, расхождений по ценам `0`, снятых строк, оставшихся в акции,
  `0`;
- skipped/drift: `7` строк по `6` товарам:
  `pict0008`, `back0013`, `pzmh0039`, `pzmh0082`, `pict0060`,
  `pict0152`;
- skipped/drift строки не применять без отдельного fresh review/approval;
- после apply отдельно проверен `Супербустинг`:
  `ozon_superboosting_verify_after_elastic_20260624T0829`, активных товаров
  `91/91`, отсутствующих `0`, лишних `0`, расхождений по action price `0`;
- отчет результата:
  `data/runs/2026-06-24/ozon_elastic_apply_actions_check_20260624/ozon_elastic_apply_result.md`;
- после отдельного review владелец подтвердил fresh-пакет
  `ozon_elastic_plan_drift_review_20260624`;
- apply drift-пакета: `ozon_elastic_apply_drift_review_20260624`;
- fresh preflight: `status_preflight_20260624T083811`, статус `ok`;
- fresh dry-run перед записью: `ozon_elastic_plan_20260624T083851`;
- применено: `4` строки activate/update и `0` строк deactivate;
- Ozon API rejected: `0`;
- verify: `ok`, расхождений по ценам `0`, снятых строк, оставшихся в акции,
  `0`;
- skipped/drift после второго apply: `2` новые fresh-строки вне
  согласованного пакета: `pzmh0081`, `bplapict0024`; они не применялись;
- отчет второго apply:
  `data/runs/2026-06-24/ozon_elastic_apply_drift_review_20260624/ozon_elastic_apply_result.md`;
- после второго apply отдельно проверен `Супербустинг`:
  `ozon_superboosting_verify_after_elastic_drift_20260624T0840`, активных
  товаров `91/91`, отсутствующих `0`, лишних `0`, расхождений по action price
  `0`;

Повторный apply по `ozon_elastic_plan_actions_check_20260624` не выполнять:
idempotency marker сохранен в `data/approved/applied/`.

## Проверка акций 2026-06-25

Read-only/dry-run проверка без применения изменений:

- preflight: `status_preflight_20260625T074528`, статус `ok`;
- Ozon Elastic dry-run: `ozon_elastic_plan_actions_check_20260625`;
- action: `Эластичный бустинг. Без ограничения срока действия`,
  `action_id=1977747`;
- результат Ozon Elastic: активных строк `504`, кандидатов `41`, добавить
  `4`, обновить цену `499`, из них с изменившейся ценой `57`, снять `5`,
  skip candidates `37`, blocked `0`;
- WB actions dry-run: `wb_actions_discount_plan_70-55-55_actions_check_20260625`;
- результат WB: товаров `432`, изменений к загрузке `0`, в акциях `358`,
  вне акций `74`, threshold triggered `82`;
- Superboosting check: `ozon_superboosting_check_20260625T0746`, активных
  товаров `91/91`, отсутствующих `0`, лишних `0`, расхождений по цене `0`;
- Superboosting за полный день `2026-06-24`: заказано `19` шт.,
  выручка `10911.00` руб.; `2026-06-25` частичный день на момент проверки:
  `3` шт., `1377.00` руб.;
- предварительная классификация Superboosting: `keep` - `15`, `watch` - `2`,
  `remove_candidate` - `74`, `blocked` - `0`;
- сводный отчет:
  `data/runs/2026-06-25/actions_check_20260625T0758/actions_check_report_2026-06-25.md`.

Технический нюанс Ozon Analytics: при проверке Superboosting 2026-06-25
запрос `/v1/analytics/data` с фильтром `{"key":"sku","op":"IN","value":[...]}`
вернул `400` из-за массива в строковом поле. Для контроля Superboosting
использовать постраничную выгрузку `dimensions=["sku","day"]` без фильтра и
локальную фильтрацию нужных SKU, пока в проекте не подтвержден корректный
формат фильтра списка SKU для этого метода.

Подтвержденный apply Ozon Elastic 2026-06-25:

- approved dry-run: `ozon_elastic_plan_actions_check_20260625`;
- apply: `ozon_elastic_apply_actions_check_20260625`;
- fresh preflight: `status_preflight_20260625T080500`, статус `ok`;
- fresh dry-run перед записью: `ozon_elastic_plan_20260625T080539`;
- drift-check: `partial_apply_unchanged_rows`;
- применено: `54` строки activate/update и `5` строк deactivate;
- Ozon API rejected: `0`;
- verify: `ok`, расхождений по ценам `0`, снятых строк, оставшихся в акции,
  `0`;
- skipped/drift: `12` строк по `9` товарам:
  `pzol0009`, `back0009`, `loop0017`, `pzmh0046`, `pzmh0057`,
  `pzmh0058`, `pict0096`, `pzmh0078`, `loop0030`;
- skipped/drift строки не применять без отдельного fresh review/approval;
- после apply отдельно проверен `Супербустинг`:
  `ozon_superboosting_verify_after_elastic_20260625T0807`, активных товаров
  `91/91`, отсутствующих `0`, лишних `0`, расхождений по action price `0`;
- отчет результата:
  `data/runs/2026-06-25/ozon_elastic_apply_actions_check_20260625/ozon_elastic_apply_result.md`.

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

Если в будущем Ozon Elastic несколько раз подряд дает микро-дрейф расчетных
цен, не обходить проверку вручную. Неизменившиеся строки должны применяться
через partial drift-check, а изменившиеся строки должны уходить в новый
review/approval по последнему `plan-run-id`.
