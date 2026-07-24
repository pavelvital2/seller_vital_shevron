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
  /home/Codex/agent-tools/python/bin/python -m seller_agent.cli plan-wb-actions-discounts
```

Если владелец отдельно попросил другую схему:

```bash
PYTHONPATH=src NODE_PATH=/home/Codex/agent-tools/node/node_modules \
  /home/Codex/agent-tools/python/bin/python -m seller_agent.cli plan-wb-actions-discounts --scheme 70-55-55
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
Сейчас:
- всего товаров в магазине: <n>
- подходят под доступные активные акции: <n>
- участвуют в акциях: <n>
- не участвуют в акциях: <n>
- проходят заданный порог: <n>

После применения целевых параметров:
- останутся или будут участвовать: <n>
- исключатся из текущих акций: <n>
- начнут участвовать: <n>
- не будут участвовать: <n>
- скидка изменится / не изменится: <n> / <n>

Итоговые скидки:
- скидка <percent>%: <n> товаров

Если есть step-limit, отдельно показать распределение после ближайшего
безопасного upload и число товаров, не достигших цели за один шаг.
```

Определения считать строго по CSV:

- `подходят под доступные активные акции` - `Акций > 0`, то есть товар есть
  хотя бы в одной свежей выгрузке активной акции WB;
- `участвуют сейчас` - поле `Статусы в файлах акций` содержит `Да` хотя бы
  для одной акции; статус `Да, Нет` считается участием;
- `не участвуют сейчас` - все остальные товары, включая товары со статусом
  только `Нет` и товары, отсутствующие в активных акциях;
- `останутся или будут участвовать` - товары с причиной
  `скидка до порога <= <threshold>%`;
- `исключатся` - только текущие участники со скидкой до порога выше threshold;
  товары со статусом только `Нет` повторно исключенными не считаются;
- `не будут участвовать после` - все товары минус товары, проходящие threshold;
- распределение целевых скидок брать из `Финальная скидка`, а ближайшего
  upload - из `Скидка к загрузке`.

Технические пути CSV/XLSX не перечислять в chat-summary. Полный report
прикреплять к сообщению по общему Telegram attachment policy.

В отчетах владельцу по WB акциям техническую колонку `Причина` нужно
дополнительно переводить в понятные бизнес-причины:

- `превышение порога 70%` - расчетная скидка до порога выше разрешенного
  порога схемы, поэтому применяется fallback `55%`;
- `участие в акции с меньшей требуемой скидкой` - товар есть в активной акции
  WB, но для участия достаточно меньшей скидки, чем стоит сейчас, поэтому
  расчет предлагает снизить скидку;
- `отсутствие в активных акциях` - товар не найден в активных акциях WB; если
  текущая скидка уже равна fallback `55%`, строка должна быть показана как
  `не менять`, а не как изменение.

Если причина `отсутствие в активных акциях` не создает строк к изменению, это
все равно нужно явно показать в отчете: сколько таких товаров найдено и почему
они не попали в payload.

## Telegram bot

С 2026-06-30 WB actions подключены к Telegram-боту:

```text
/wb-actions
```

Команда всегда строит fresh dry-run по схеме `70-55-55` через
`run_wb_actions_discount_plan`, выводит краткую сводку, бизнес-причины
изменений и прикрепляет report-файл. Если в dry-run есть строки к применению,
бот показывает inline-кнопку:

```text
✅ Применить WB 70-55-55
```

Нажатие кнопки отправляет callback:

```text
wba_apply:<wb_actions_discount_plan_run_id>
```

Это считается явным подтверждением владельца только для этого `plan_run_id`.
Callback не может применять произвольный run: бот принимает только id,
начинающиеся с `wb_actions_discount_plan_`.

### Ручная акция

С 2026-07-17 рядом с неизменной кнопкой `WB акции` по схеме `70-55-55`
добавлена отдельная кнопка `Ручная акция`.

Порядок работы:

1. Бот запрашивает одной строкой три целых значения от `0` до `99`:
   `порог`, `скидка после превышения порога`, `скидка вне активных акций`.
2. После корректного ввода бот показывает каждое значение с названием и
   просит отдельно подтвердить параметры или отменить ввод.
3. Подтверждение параметров запускает только fresh dry-run через штатный
   `run_wb_actions_discount_plan`; скидки в WB не изменяются.
4. После расчета бот показывает сводку, report и две кнопки:
   `Применить скидки` и `Отклонить`.
5. `Применить скидки` передает конкретный
   `wb_actions_discount_plan_<scheme>_<timestamp>` в тот же safety-контур,
   что и фиксированная схема: fresh preflight, fresh plan, partial
   drift-check, apply и verify.
6. `Отменить` и `Отклонить` являются no-op: никаких запросов на изменение
   скидок WB не выполняется.

Сводка Job Worker перед подтверждением обязана показывать бизнес-переходы, а
не только число строк payload:

- сколько товаров фактически участвует сейчас и распределение их текущих скидок;
- сколько товаров снимется, с отдельной причиной и количеством;
- сколько товаров начнет участвовать;
- сколько будет участвовать после применения и распределение их целевых скидок;
- сколько останется вне акций и распределение их целевых скидок.

Товар считается текущим участником только при статусе `Да` в файле акции.
Наличие товара в списке доступной акции само по себе участием не считается.

Ввод пользователя имеет бизнес-порядок:

```text
порог -> после порога -> вне акций
```

Внутренняя `Scheme` задачи исторически хранит значения в порядке:

```text
порог -> вне акций -> после порога
```

Bot command layer обязан явно преобразовывать порядок. Для защиты от
перестановки значений есть тест с различающимися параметрами
`57 52 48 -> internal scheme 57-48-52`.

Временный этап ожидания ввода хранится в polling state под `.sessions/telegram`
отдельно для пары `chat_id + thread_id`. После корректного ввода, отмены или
любой другой команды состояние очищается. Marketplace credentials, cookies и
другие секреты в этом state не хранятся.

Apply из кнопки использует штатный контур CLI:

- `confirmed_by_user=True`;
- fresh WB-scoped API/catalog `status-preflight` без проверки Ozon LK/CDP;
- fresh `plan-wb-actions-discounts` по схеме утвержденного плана;
- partial drift-check по payload `nmID + price + discount`;
- upload только неизменившихся строк;
- verify через WB history/buffer;
- отчет и attachment в Telegram.

С 2026-07-14 расчет и apply должны ограничивать снижение итоговой цены за один
официальный WB upload величиной `33%`, то есть ниже стандартного порога
карантина WB `33,3%`. Дополнительно сохраняется старый предел изменения скидки
`35 п.п.`. Для увеличения скидки применяется более строгий из двух пределов.
Если целевая скидка не достигается за один шаг, dry-run сохраняет:

- `Финальная скидка` - целевая скидка по схеме `70-55-55`;
- `Скидка к загрузке` - скидка, которую нужно отправить в WB сейчас;
- `Дельта загрузки, п.п.` - фактический шаг текущего upload;
- `Осталось до целевой, п.п.` - сколько нужно довести следующим запуском;
- `Ограничение шага` - `цена не более -33%; скидка не более 35 п.п.`.

Apply использует в payload именно `Скидка к загрузке`, а не скрытое прямое
значение `Финальная скидка`. Следующий `/wb-actions` после успешной проверки
должен продолжить доведение товара к целевой скидке, если `Осталось до
целевой, п.п.` не равно `0`.

Старый quarantine/staged-контур через `49% -> Apply New Price -> target`
оставлен только как fallback/исторический recovery для уже попавших в
карантин строк или legacy-пакетов. Штатно новые WB actions dry-run не должны
намеренно заводить товары в `Карантин цен`.

### Параллельный запуск `/wb-actions`

Подтвержденный сценарий 2026-07-04:

- из CLI уже выполнялся fresh dry-run
  `wb_actions_discount_plan_70-55-55_20260704T092906`;
- владелец параллельно отправил `/wb-actions` в Telegram;
- второй процесс попытался открыть тот же WB persistent profile
  `.sessions/wb/browser-profile`;
- Chrome/Playwright вернул длинную техническую ошибку с аргументами запуска
  браузера, бот показал ее как причину;
- marketplace write не выполнялся, расчет из CLI завершился штатно:
  `overall_status=ok`, `total_goods=448`, `to_change=10`.

Постоянный fix:

- `run_wb_actions_discount_plan` ставит файловый lock
  `.sessions/locks/wb_actions_discount_plan.lock` перед запуском WB LK
  snapshot;
- если lock занят, второй dry-run не трогает WB browser profile и возвращает
  понятную ошибку "дождитесь завершения текущего расчета";
- Chrome profile / `user-data-dir` ошибки нормализуются, чтобы не выводить в
  Telegram длинную командную строку Chrome.

Правило работы:

1. Не запускать два `/wb-actions`/`plan-wb-actions-discounts` параллельно.
2. Если бот сообщил, что расчет уже выполняется, дождаться завершения текущего
   run и повторить `/wb-actions`.
3. После любого изменения кода Telegram-бота или задач, вызываемых ботом,
   перезапустить `vital-shevron-telegram-bot.service`, иначе polling-процесс
   продолжит работать со старым импортированным кодом.

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
  /home/Codex/agent-tools/python/bin/python -m seller_agent.cli apply-wb-actions-discounts \
  --plan-run-id <approved_wb_actions_discount_plan_run_id> \
  --confirmed-by-user
```

Apply выполняет:

- свежий WB-scoped API/catalog `status-preflight`
  (`marketplaces=("wb",)`, `include_lk=False`);
- свежий dry-run WB по схеме из утвержденного плана;
- partial drift-check payload строк `nmID + price + discount`;
- upload только строк, где `nmID + price + discount` совпали между
  согласованным и свежим расчетом;
- payload строится по колонке `Скидка к загрузке`, если она есть; увеличение
  скидки ограничивается так, чтобы итоговая цена снижалась не более чем на
  `33%` за upload, и дополнительно не более чем на `35 п.п.` скидки. Колонка
  `Финальная скидка` остается целевой и может быть достигнута последующими
  запусками;
- legacy quarantine/staged fallback применяется только если строка не имеет
  step-limit колонок и требует старого recovery-сценария;

- строки, где изменились цена, скидка или состав payload, не загружаются
  автоматически, сохраняются в `processed/skipped_drift_rows.json` и
  выводятся в итоговом отчете как требующие нового согласования;
- upload в официальный WB endpoint
  `https://discounts-prices-api.wildberries.ru/api/v2/upload/task`;
- проверку статуса upload через history/buffer endpoints;
- для staged-строк - LK endpoint `Apply New Price` в карантине через
  `scripts/actions/wb_quarantine_apply_new_price.js`; секретные токены берутся
  только из браузерного профиля и не пишутся в отчеты;
- сохранение `summary.json`, `wb_actions_discount_apply_result.md`,
  `drift_check.json`, отправленного payload, staged artifacts и WB upload
  response.

Если после fresh dry-run изменились только отдельные строки, нельзя
останавливать весь пакет: неизменившиеся строки применяются, изменившиеся
строки пропускаются и остаются на новый review/approval.

### Scoped preflight для WB actions

Подтверждено 2026-07-04 после перезагрузки Seller VPS:

- Ozon CDP/keeper Vital Shevron на `127.0.0.1:9544` был не запущен, потому что
  LK-сессии работали legacy pid-процессами, а не enabled `systemd --user`
  units;
- общий full `status-preflight` вернул `error` по Ozon LK, хотя WB API и WB
  keepalive были `ok`;
- попытка `apply-wb-actions-discounts` остановилась до upload с ошибкой
  `preflight is not ok: error`.

Правило:

1. WB actions apply не должен блокироваться из-за unrelated Ozon LK/CDP.
2. Перед WB upload выполнять WB-scoped API/catalog preflight:
   `marketplaces=("wb",)`, `include_lk=False`, `include_ozon_performance=False`.
3. Актуальность WB ЛК, активных акций и цен проверять следующим обязательным
   fresh dry-run `plan-wb-actions-discounts`; если он не может получить WB
   snapshot, upload не выполняется.
4. Full `status-preflight` остается общей проверкой здоровья проекта и может
   показывать инфраструктурные проблемы Ozon/WB, но не должен быть
   единственным gate для marketplace-local API apply.

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
     /home/Codex/agent-tools/python/bin/python -m seller_agent.cli plan-wb-actions-discounts --scheme 70-55-55
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

## Штатный apply 2026-06-20

Подтвержденный сценарий по схеме `70-55-55`:

- approved dry-run: `wb_actions_discount_plan_70-55-55_20260620T073712`;
- apply: `wb_actions_discount_apply_70-55-55_20260620T075506`;
- fresh preflight: `status_preflight_20260620T075506`, статус `ok`;
- fresh dry-run:
  `wb_actions_discount_plan_70-55-55_20260620T075546`;
- drift-check: `partial_apply_unchanged_rows`, `approved_payload_rows=276`,
  `fresh_payload_rows=276`, `eligible_payload_rows=276`, skipped/drift `0`;
- отправлено в WB: `276` строк;
- WB upload ID: `167765884`;
- verify: `ok`, history показал `276/276` successful goods;
- отчет результата:
  `data/runs/2026-06-20/wb_actions_discount_apply_70-55-55_20260620T075506/wb_actions_discount_apply_result.md`.

Перед apply по этому плану владелец запросил отчет с бизнес-причинами
изменения скидки. Подтвержденные причины по payload:

- превышение порога `70%`, привести к fallback `55%`: `82`;
- участие в акции с меньшей требуемой скидкой: `194`;
- отсутствие в активных акциях: `0` строк к изменению, потому что найденные
  `74` товара уже имели fallback-скидку `55%`.

Повторный apply по `wb_actions_discount_plan_70-55-55_20260620T073712` не
выполнять: idempotency marker сохранен в `data/approved/applied/`.

## Штатный apply 2026-06-23

Подтвержденный сценарий по схеме `70-55-55`:

- approved dry-run:
  `wb_actions_discount_plan_70-55-55_actions_check_20260623`;
- apply:
  `wb_actions_discount_apply_70-55-55_actions_check_20260623`;
- fresh preflight: `status_preflight_20260623T064823`, статус `ok`;
- fresh dry-run:
  `wb_actions_discount_plan_70-55-55_20260623T064903`;
- drift-check: `partial_apply_unchanged_rows`, `approved_payload_rows=77`,
  `fresh_payload_rows=77`, `eligible_payload_rows=77`, skipped/drift `0`;
- отправлено в WB: `77` строк;
- WB upload ID: `168439528`;
- verify: `ok`, history показал `77/77` successful goods;
- отчет результата:
  `data/runs/2026-06-23/wb_actions_discount_apply_70-55-55_actions_check_20260623/wb_actions_discount_apply_result.md`.

Подтвержденные причины по payload:

- превышение порога `70%`, привести к fallback `55%`: `8`;
- участие в акции с меньшей требуемой скидкой: `69`;
- отсутствие в активных акциях: `0` строк к изменению, потому что найденные
  `74` товара уже имели fallback-скидку `55%`.

Повторный apply по
`wb_actions_discount_plan_70-55-55_actions_check_20260623` не выполнять:
idempotency marker сохранен в `data/approved/applied/`.

## Штатный apply 2026-06-26

Подтвержденный сценарий по схеме `70-55-55`:

- approved dry-run:
  `wb_actions_discount_plan_70-55-55_actions_check_20260626`;
- apply:
  `wb_actions_discount_apply_70-55-55_actions_check_20260626`;
- fresh preflight: `status_preflight_20260626T102102`, статус `ok`;
- fresh dry-run:
  `wb_actions_discount_plan_70-55-55_20260626T102141`;
- drift-check: `partial_apply_unchanged_rows`, `approved_payload_rows=183`,
  `fresh_payload_rows=183`, `eligible_payload_rows=183`, skipped/drift `0`;
- отправлено в WB: `183` строки;
- WB upload ID: `169512510`;
- verify: `ok`, history показал `183/183` successful goods;
- отчет результата:
  `data/runs/2026-06-26/wb_actions_discount_apply_70-55-55_actions_check_20260626/wb_actions_discount_apply_result.md`.

Подтвержденные причины по исходному payload:

- превышение порога `70%`, привести к fallback `55%`: `55`;
- участие в акции с меньшей требуемой скидкой: `128`;
- отсутствие в активных акциях: `0` строк к изменению, потому что найденные
  `62` товара уже не требовали загрузки нового payload.

Повторный apply по
`wb_actions_discount_plan_70-55-55_actions_check_20260626` не выполнять:
idempotency marker сохранен в `data/approved/applied/`.

## Штатный apply 2026-06-27

Подтвержденный сценарий по схеме `70-55-55`:

- approved dry-run:
  `wb_actions_discount_plan_70-55-55_20260627T161836`;
- apply:
  `wb_actions_discount_apply_70-55-55_20260627T162808`;
- fresh preflight: `status_preflight_20260627T162808`, статус `ok`;
- fresh dry-run:
  `wb_actions_discount_plan_70-55-55_20260627T162847`;
- drift-check: `partial_apply_unchanged_rows`, `approved_payload_rows=193`,
  `fresh_payload_rows=193`, `eligible_payload_rows=193`, skipped/drift `0`;
- отправлено в WB: `193` строки;
- WB upload ID: `169956946`;
- verify: `ok`, history показал `193/193` successful goods;
- отчет результата:
  `data/runs/2026-06-27/wb_actions_discount_apply_70-55-55_20260627T162808/wb_actions_discount_apply_result.md`.

Подтвержденные причины по исходному payload:

- превышение порога `70%`, привести к fallback `55%`: `22`;
- участие в акции с меньшей требуемой скидкой: `171`;
- отсутствие в активных акциях: `0` строк к изменению, потому что найденные
  `62` товара уже не требовали загрузки нового payload.

Повторный apply по
`wb_actions_discount_plan_70-55-55_20260627T161836` не выполнять:
idempotency marker сохранен в `data/approved/applied/`.

## Карантин цен WB при резком снижении цены

Подтвержденный сценарий 2026-06-29:

- approved dry-run:
  `wb_actions_discount_plan_70-55-55_20260629T071859`;
- apply:
  `wb_actions_discount_apply_70-55-55_20260629T072729`;
- fresh preflight: `status_preflight_20260629T072729`, статус `ok`;
- fresh dry-run:
  `wb_actions_discount_plan_70-55-55_20260629T072808`;
- drift-check: `partial_apply_unchanged_rows`, `approved_payload_rows=62`,
  `fresh_payload_rows=62`, `eligible_payload_rows=62`, skipped/drift `0`;
- WB upload ID: `170436799`;
- WB принял upload, но в buffer/details показал `overAllGoodsNumber=62`,
  `successGoodsNumber=56`;
- `6` строк получили статус `3` и ошибку WB:
  `Changes weren't saved: New prices are more than twice lower than the current ones. Please lower them gradually`;
- отказанные строки сохранены:
  `data/runs/2026-06-29/wb_actions_discount_apply_70-55-55_20260629T072729/wb_actions_discount_apply_failed_rows.md`.

Это не новая неизвестная ошибка, а срабатывание механики WB `Карантин цен`.
Раздел ЛК описан в `data/planning/wb_cabinet_map.md`:

```text
Цены и скидки -> Карантин
https://seller.wildberries.ru/discount-and-prices/quarantine
```

Историческое правило штатного apply 2026-07-05, заменено правилом шага
`35 п.п.` с 2026-07-06:

1. Если fresh+approved eligible строка требует резкого перехода `0% -> 55%`
   или другого перехода, где новая цена со скидкой становится в `2+` раза ниже
   текущей, не отправлять ее прямым target upload.
2. Разделить payload:
   - обычные строки - прямой официальный WB upload;
   - staged-строки - пошаговая скидка.
3. Для staged-строк применять:

   ```text
   upload 49% -> Apply New Price в Карантине -> verify 49% -> upload target -> verify target
   ```

4. `Apply New Price` в карантине является write-операцией с финансовым
   эффектом, но если владелец подтвердил WB actions apply по конкретному
   dry-run, staged-действие разрешено только для точных строк этого approved
   payload. Нельзя добавлять новые товары или менять target discount.
5. `Keep Current Price` для цели "оставить утвержденную скидку" не использовать:
   он отменяет quarantined-изменение.
6. В итоговом отчете по apply обязательно показывать:
   `regular rows`, `staged rows`, `successful rows / expected rows`, `failed
   rows`, staged status, `confirmed49_rows`, `final_verified_rows` и пути к
   staged artifacts.
7. Если staged-контур вернул `partial` или `blocked`, не повторять кнопку
   вслепую: смотреть `processed/staged_discount_result.json`, текущие скидки
   WB и остаток строк в карантине.

Подтвержденное восстановление Vital Shevron 2026-06-29:

- recovery run:
  `data/runs/2026-06-29/wb_price_quarantine_staged_recovery_20260629T0758/`;
- прямой upload `0% -> 55%` для `6` товаров не применился: WB вернул статус
  строки `3` и текст про цену ниже текущей более чем в два раза;
- официальный quarantine API и LK quarantine endpoint сначала вернули `0`
  строк, фактическая скидка по WB prices API оставалась `0%`;
- промежуточный upload `49%` перевел все `6` товаров в `Карантин цен`;
- LK `Apply New Price` по внутренним ID строк карантина применил `49%`;
- финальный upload `55%` применился штатно: `successGoodsNumber=6`,
  `overAllGoodsNumber=6`, все `6` строк в details со статусом `2`;
- финальная проверка WB prices API подтвердила скидку `55%` у `6/6` товаров;
- финальная проверка LK quarantine endpoint подтвердила `0` целевых строк в
  карантине.

Для цели "оставить утвержденную скидку" использовать staged recovery:

```text
0% -> upload 49% -> Apply New Price в карантине -> verify 49% -> upload 55% -> verify 55%
```

`Keep Current Price` для такой цели не использовать: он отменяет quarantined
изменение.

### Правило 35 п.п. с 2026-07-06

Подтвержденная причина изменения:

- apply run:
  `data/runs/2026-07-06/wb_actions_discount_apply_70-55-55_20260706T064719/`;
- approved plan:
  `wb_actions_discount_plan_70-55-55_20260706T064433`;
- товар `1235888513 / chev_back_rg_text0001` был отправлен старым staged-шагом
  `49%`;
- WB вернул по строке:
  `New price is several times lower than the current price. Item has been moved to Price Quarantine`;
- следующий LK-шаг `Apply New Price` не завершился из-за занятого WB browser
  profile (`ProcessSingleton`), apply получил `staged_discount_blocked`.

Новое правило владельца: не менять скидку WB за один upload больше чем на
`35 п.п.`. Пример:

```text
0% -> цель 55% => сейчас загрузить 35%, следующий успешный запуск доведет 35% -> 55%
```

Если даже шаг `35 п.п.` попадет в карантин по фактической цене WB, это нужно
обработать как отдельную внештатную ситуацию: разобрать details/status, не
повторять кнопку вслепую, предложить владельцу уменьшить шаг или обработать
конкретные карантинные строки.

Подтвержденный сценарий 2026-07-14:

- WB применил `300` из `306` строк upload `175597257`;
- `6` новых карточек с переходом `0% -> 35%` получили статус `3` и текст
  `Item has been moved to Price Quarantine`;
- свежая read-only проверка подтвердила `300` совпадений и `6` карточек со
  скидкой `0%` вместо `35%`;
- формулировку `moved to Price Quarantine` нужно учитывать в счетчике
  карантинных отказов наравне с legacy-текстами `more than twice lower` и
  `lower them gradually`;
- recovery по этим строкам нельзя запускать повторной кнопкой общего apply:
  сначала нужен отдельный exact-target план для шести `nmID`, owner approval,
  затем `apply -> verify -> quarantine cleanup`.

Recovery 2026-07-14 завершен:

- exact plan:
  `wb_price_quarantine_exact_recovery_plan_20260714T204106`;
- read-only quarantine preflight нашел ровно `6/6` совпадений по
  `nmID + price 1100 + discount 35`, без missing/extra targets;
- `Apply New Price` применил `35%`, после чего карантин стал пустым по
  целевым строкам, а WB Prices API подтвердил `35%` у `6/6`;
- следующий exact upload `35% -> 55%`, upload ID `175599630`, завершился
  `6/6`, failed rows `0`;
- финальный WB Prices API verify подтвердил `55%` и базовую цену `1100` у
  всех шести карточек.

Для preflight helper
`scripts/actions/wb_quarantine_apply_new_price.js` поддерживает `--dry-run`:
режим читает и сопоставляет карантинные строки, но не отправляет POST. Перед
каждым exact recovery сначала использовать этот режим и останавливать apply
при любой missing-строке.

### Актуальное правило после проверки документации 2026-07-14

Официальная инструкция WB `Карантин цен`, обновленная 18.05.2026, указывает:

- система учитывает итоговую цену после скидок;
- стандартный порог карантина - снижение цены на `33,3%`;
- порог можно настроить по категории в диапазоне `33,3-47,5%`, но только
  владельцу ЛК;
- снижение на `50%` и больше нужно выполнять поэтапно;
- если изменение корректное и уже попало в карантин, его можно подтвердить
  действием `Применить новую цену`.

Источник:
`https://seller.wildberries.ru/instructions/ru/by/material/price-quarantine`.

API-документация `/api/v2/quarantine/goods` пока содержит более старую
формулировку про снижение цены минимум в `3 раза`. Для safety-расчета проекта
использовать более свежую и более строгую инструкцию ЛК плюс подтвержденное
фактическое поведение: переход `0% -> 35%` попал в карантин.

Штатный безопасный пример при неизменной базовой цене:

```text
0% -> цель 55% => первый upload 33%, следующий upload 55%
```

Не повышать порог карантина в ЛК ради обхода проверки: текущая защита остается
включенной, а task-runner подбирает шаг ниже стандартного порога.
