# Product Card Work Checkpoint

Дата чекпойнта: 2026-07-15

## Назначение

Этот файл фиксирует текущее состояние работы с карточками Vital Shevron, чтобы
после переключения на другую задачу агент мог вернуться ровно к тому же месту,
с тем же форматом HTML, теми же правилами аудита и тем же порядком apply.

`AGENTS.md` остается источником истины. Этот checkpoint - быстрый слой
восстановления контекста по текущей карточной работе.

## Что прочитать перед продолжением

Перед любым новым действием с карточками открыть в таком порядке:

1. `AGENTS.md`
2. `data/planning/product_card_work_checkpoint.md`
3. `data/planning/card_ops/quick_access.md`
4. `data/planning/product_card_work_runbook.md`
5. `data/planning/product_card_fill_template_runbook.md`
6. `data/planning/product_card_editor_field_map_runbook.md`
7. `data/planning/ozon_product_card_content_runbook.md`
8. `data/planning/wb_card_create_runbook.md`
9. `data/planning/product_card_designer_tasks.md`

Если после чтения этих файлов агент не может подтвердить, на каком SKU
остановились и какой статус у последней пачки, он обязан сначала проверить
локальные статусы, а не продолжать по памяти.

## Текущая ветка и состояние проекта

На момент чекпойнта работа идет в ветке:

```text
checkpoint/card-runtime-20260708
```

В рабочем дереве могут быть незакоммиченные изменения по карточкам,
инструкциям, run artifacts и локальным catalog-layer файлам. Нельзя откатывать
эти изменения без отдельного указания владельца. Перед коммитом нужна
отдельная ревизия `git diff` и проверка `git status --ignored`, потому что
часть карточных паспортов и run artifacts может не попадать в обычный
tracked-status.

## Точка продолжения 2026-07-15: `chev_kp_prikol_text0014`

Владелец согласовал Layer 2 с четырьмя уточнениями и разрешил создание
паспорта. Marketplace write отдельно не разрешен.

Выполнено:

- в целевой комплект добавлен стандартный слайд вариантов ношения с военным;
- зафиксирован полный комплект из `5` фото для будущей карточки Ozon;
- Ozon-хештеги расширены до `30`: сначала точные тематические запросы, затем
  подтвержденные популярные релевантные запросы по убыванию;
- модель для группировки: `Прикол`;
- Layer 3 создан:
  `data/catalog/master_passport/approved/chev_kp_prikol_text0014.json`;
- promotion run:
  `data/runs/2026-07-15/promote_chev_kp_prikol_text0014_20260715T0716_fixed/`;
- статус паспорта: `owner_approved_pending_batch_apply` /
  `marketplace_apply.status=not_applied`;
- всего owner-approved паспортов после promotion: `211`.

Карточка Ozon еще не создана. В паспорте сохранен будущий Ozon create с
`offer_id=chev_kp_prikol_text0014`, но `apply_authorized=false`. Следующий
write-шаг допустим только после отдельного dry-run, показа владельцу точного
пакета создания и команды `применяй`.

При promotion выявлена и исправлена ошибка обработки list-valued хештегов:
список больше не превращается в строку со скобками и кавычками. Добавлен тест
на сохранение списка, поисковых запросов и safety-флага `ozon_card_create`.

## Точка продолжения 2026-07-15: `chev_kp_prikol_text0015`

Владелец согласовал остальные поля и уточнил фото, модель, хештеги и SEO
описания. Marketplace write отдельно не разрешен.

Выполнено:

- стандартный слайд вариантов ношения с военным добавлен четвертым;
- целевой комплект содержит `5` фото, сервисный слайд оставлен пятым;
- модель для группировки: `Прикол`;
- Ozon-хештеги расширены до `30` по принципу предыдущей карточки: сначала
  точные тематические, затем релевантные популярные запросы по убыванию;
- описание усилено естественными фразами `шеврон прикол`, `прикольная
  нашивка на липучке`, `шеврон на кепку`, `нашивка на рюкзак`, `патч на
  липучке`; служебных SEO-фраз и перечисления ключей нет;
- Layer 3 создан:
  `data/catalog/master_passport/approved/chev_kp_prikol_text0015.json`;
- promotion run:
  `data/runs/2026-07-15/promote_chev_kp_prikol_text0015_20260715T0727/`;
- статус: `owner_approved_pending_batch_apply` /
  `marketplace_apply.status=not_applied`;
- всего owner-approved паспортов после promotion: `212`.

Обновленный HTML проверен в Chromium через локальный HTTP: mobile
`390/390`, desktop `1366/1366`, встроено одно изображение-коллаж, битых
изображений нет. Создание Ozon-карточки для этого SKU не согласовано и не
включено в dangerous actions паспорта.

## Точка продолжения 2026-07-15: `chev_kit2_nr_mvd_pict0004`

Владелец согласовал остальные поля и разрешил создать Layer 3 паспорт с
уточнениями по названию, фото и SEO. Marketplace write отдельно не разрешен.

Выполнено:

- сохранено полное название `Шевроны на липучке Полиция МВД ГИБДД ДПС,
  комплект 2 шт.`: длина `56` символов, сокращение не требуется;
- описание усилено релевантными SEO-фразами естественно и сохранено в трех
  тематических блоках без служебного перечисления ключей;
- для Ozon зафиксированы все текущие `5` фото без изменений;
- для WB зафиксирован целевой набор из `4` текущих фото: удаляется только
  полный дубль WB №2, остальные фото и обязательные водяные знаки сохраняются;
- создана задача дизайнеру `VS-DESIGN-034`: подготовить отдельный WB-слайд с
  размером `75*100 мм` и обязательными водяными знаками;
- модель для группировки сохранена как `МВД`;
- физические параметры комплекта разделены корректно: одно изделие `10 г`,
  комплект/упаковка `20 г`, размер каждого шеврона `75*100 мм`;
- Layer 3 создан:
  `data/catalog/master_passport/approved/chev_kit2_nr_mvd_pict0004.json`;
- итоговый promotion run:
  `data/runs/2026-07-15/promote_chev_kit2_nr_mvd_pict0004_20260715T0830_fixed/`;
- статус: `owner_approved_pending_batch_apply` /
  `marketplace_apply.status=not_applied`;
- всего owner-approved паспортов после promotion: `213`.

При promotion исправлен общий контракт раздельных наборов фото Ozon и WB:
паспорт теперь сохраняет `target_ozon_photo_set` и `target_wb_photo_set`.
Это исключает перенос удаления WB-дубля на неизменяемый набор Ozon. Любые
изменения карточек и медиа допустимы только после отдельного dry-run и команды
владельца `применяй`.

## Точка продолжения 2026-07-15: SEO quality gate и следующая тройка

После повторных замечаний владельца по слабому SEO описаний и коротким наборам
хештегов усилен постоянный пакет fresh-аудитора:

- prompt теперь обязательно передает
  `data/planning/ozon_hashtag_frequency_table.md`;
- до написания описания аудитор строит `description_semantic_plan`, после -
  `description_seo_coverage`;
- описание должно естественно покрывать точную тему, тип и крепление,
  подтвержденные свойства, primary/secondary/broad запросы из входного пакета;
- обычный Ozon-набор должен содержать `20-30` уникальных релевантных
  хештегов, цель - `30`; меньше `20` допустимо только с проверяемым
  `hashtag_shortfall_reason`;
- порядок хештегов: точная тематика/вариант, тип и крепление, релевантное место
  ношения, затем широкие релевантные по убыванию частотности;
- валидатор обязан вернуть слабое описание или необоснованно короткий набор в
  `needs_rework`.

Изменены:

```text
data/planning/card_audit_agent_docs/fresh_single_card_auditor_prompt_v2.md
data/planning/card_audit_agent_docs/card_audit_minimal_rules.md
data/planning/card_audit_agent_docs/card_validator_minimal_prompt.md
data/planning/card_audit_agent_docs/card_audit_output_contract.md
data/planning/card_audit_agent_docs/README.md
data/planning/product_card_audit_orchestration_runbook.md
```

После обновления prompt три независимых одноразовых fresh-аудитора подготовили
следующую тройку Layer 2 без правок оркестратора:

1. `chev_kit2_nr_mvd_pict0005`:
   `data/catalog/card_audits/fresh_triples_stale_layer1_20260715/0056_chev_kit2_nr_mvd_pict0005/`;
2. `chev_kit2_nr_mvd_pict0006`:
   `data/catalog/card_audits/fresh_triples_stale_layer1_20260715/0057_chev_kit2_nr_mvd_pict0006/`;
3. `chev_kit2_nr_rg_pict0003`:
   `data/catalog/card_audits/fresh_triples_stale_layer1_20260715/0058_chev_kit2_nr_rg_pict0003/`.

У всех трех:

- `agent_audit.status=agent_audited`;
- все доступные фото просмотрены;
- `30` уникальных Ozon-хештегов;
- заполнены `description_semantic_plan` и `description_seo_coverage`;
- `layout_validation_status=passed`, mobile `390/390`, desktop `1366/1366`;
- на момент первичного аудита marketplace write и Layer 3 не выполнялись;
- на момент первичной отправки `owner_review.status=not_submitted`.

HTML отправлены владельцу через общий `telegram-ai-agent` в топик `42336` без
изменения файлов fresh-аудиторов:

- `chev_kit2_nr_mvd_pict0006.html` - `message_id=94536`;
- `chev_kit2_nr_mvd_pict0005.html` - `message_id=94543`;
- `chev_kit2_nr_rg_pict0003.html` - `message_id=94544`.

Первичный запуск через Codex `workspace-write` был заблокирован неисправностью
локального `bwrap` (`RTM_NEWADDR`). Результаты этого запуска не создавались.
Тройка повторно запущена новыми ephemeral fresh-сессиями с прямым запретом
писать вне индивидуального `WRITE_SCOPE`; итоговые процессы завершены.

## Точка продолжения 2026-07-16: `chev_kit2_nr_mvd_pict0006`

Владелец окончательно согласовал Layer 2 с последней корректировкой описания:
`Оливковая основа и оливковая окантовка разных оттенков`. Marketplace write
не разрешен.

Выполнено:

- формулировка заменена в canonical/Ozon/WB описании и owner-review HTML;
- сохранено согласованное название длиной `56` символов;
- цвета: `оливковый, чёрный`;
- Layer 2 закрыт со статусом
  `owner_approved_pending_batch_apply`;
- Layer 3 создан и проверен:
  `data/catalog/master_passport/approved/chev_kit2_nr_mvd_pict0006.json`;
- итоговый promotion run:
  `data/runs/2026-07-16/promote_approved_card_passport_chev_kit2_nr_mvd_pict0006_corrected_20260716T0645/`;
- паспорт содержит `30` Ozon-хештегов, размер изделия `75*100 мм`, упаковку
  Ozon `100*100*20 мм`, WB `10*10*2 см`, вес комплекта `20 г`;
- `marketplace_apply.status=not_applied`.

Во время promotion устранена несовместимость нового fresh-аудиторского
контракта `proposed_final_card.physical` со старым конвертером Layer 2 ->
Layer 3. Конвертер теперь поддерживает оба формата, отдельные массивы фото с
позициями и игнорирует служебную подпись вместо списка хештегов. Регрессионные
тесты: `7 passed`.

## Точка продолжения 2026-07-16: `chev_kit2_nr_mvd_pict0005`

Владелец полностью согласовал карточку и отдельно утвердил её параметры
названия и описания как эталон структуры для следующих комплектов МВД.
Marketplace write не разрешен.

Выполнено:

- Layer 2 закрыт со статусом `owner_approved_pending_batch_apply`;
- Layer 3 создан и проверен:
  `data/catalog/master_passport/approved/chev_kit2_nr_mvd_pict0005.json`;
- итоговый promotion run:
  `data/runs/2026-07-16/promote_chev_kit2_nr_mvd_pict0005_20260716_model_fix/`;
- title: `Шевроны на липучке МВД Полиция ГИБДД ДПС, комплект 2 шт.`
  (`56` символов);
- модель группировки: `МВД`;
- паспорт содержит `30` Ozon-хештегов, размер каждого изделия `75*100 мм`,
  упаковку Ozon `100*100*20 мм`, WB `10*10*2 см`, вес комплекта `20 г`;
- `marketplace_apply.status=not_applied`.

Шаблон МВД закреплен в
`data/planning/card_content_standards_runbook.md` и минимальных правилах
fresh-аудиторов. Факты конкретного товара - надписи, цвета и размеры - нельзя
копировать в другие комплекты без проверки их Layer 1 и фото.

Во время promotion устранена ещё одна несовместимость Layer 2 -> Layer 3:
добавлена поддержка `proposed_final_card.physical_parameters`, очистка слова
`каждый` из машинного размера изделия и чтение модели из `9048_model`.
Регрессионные тесты promotion: `8 passed`.

## Точка продолжения 2026-07-16: `chev_kit2_nr_rg_pict0003`

Владелец окончательно согласовал исправленные название, название цвета и
упаковку. Layer 3 создан; marketplace write не выполнялся.

Исправлено:

- title: `Шевроны на липучке Росгвардия ОН ЦО, комплект 2 шт` (`50`
  символов); полный вариант с `чёрно-серый` имеет `63` символа, поэтому цвет
  убран по правилу лимита `60`;
- Ozon `Название цвета`: `Комплект Росгвардия НО ЦО 2 шт., чёрно-серый`;
- упаковка: Ozon `100*100*20 мм`, WB `10*10*2 см`;
- подтвержденная причина прежней ошибки: fresh-аудитор перенес физическую
  ширину изделия `75 мм` в упаковку вместо стандартного основания
  нарукавного шеврона `100*100 мм`;
- prompt, минимальные правила, validator и постоянные runbook усилены
  обязательным package quality gate;
- исправленный self-contained HTML проверен в Chromium: mobile `390/390`,
  desktop `1366/1366`, `1` встроенное изображение, битых изображений `0`;
- HTML отправлен в Telegram message `94601`, коллаж - `94602`.
- Layer 2 закрыт со статусом `owner_approved_pending_batch_apply`;
- Layer 3 создан и проверен:
  `data/catalog/master_passport/approved/chev_kit2_nr_rg_pict0003.json`;
- итоговый promotion run:
  `data/runs/2026-07-16/promote_chev_kit2_nr_rg_pict0003_20260716/`;
- паспорт содержит `30` Ozon-хештегов, модель `Росгвардия`, размер каждого
  изделия `75*100 мм`, вес комплекта `20 г`, Ozon-фотонабор `1-6` и
  WB-фотонабор `1-5`;
- `marketplace_apply.status=not_applied`.

Promotion дополнен поддержкой fresh-полей `product_size_mm_each`,
`package_size_mm` и структурированных WB `dimensions_cm`. Регрессионные тесты:
`8 passed`.

## Точка продолжения 2026-07-14

Контекст карточной работы повторно проверен по Layer 2, Layer 3, статусному
snapshot и актуальному backlog. Текущая точка продолжения:

- approved passports: `210`;
- `209` паспортов закрыты как `owner_approved_applied_verified` и
  `applied_verified`;
- один особый паспорт `chev_pz_ng_text0074` имеет статус
  `owner_approved` / `wb_applied_ozon_removed_policy`: карточка работает
  только на WB, восстанавливать ее на Ozon без отдельного решения владельца
  нельзя;
- согласованных паспортов, ожидающих apply, сейчас нет;
- последняя согласованная пачка из 10 карточек полностью прошла
  `apply -> verify -> card_status_sync`; повторно применять ее нельзя;
- старые Layer 2 записи `not_submitted` и исторические pending-разделы ниже
  не являются текущей очередью: перед повторным использованием их нужно
  пересобрать из свежего Layer 1.

Актуальный статусный источник:
`data/catalog/card_status/latest.json`, сформирован
`2026-07-13T19:49:38`.

Текущий backlog содержит `674` строки / `608` уникальных SKU. Без approved
passport остаются примерно `415` уникальных SKU; из них `65` относятся к
`business_priority=now` и `audit_priority=high`. Сам backlog сформирован
2026-07-05. Владелец 2026-07-15 отдельно подтвердил, что изменений в выбранных
карточках не было, и распорядился продолжить без refresh Layer 1. Это
owner-approved исключение для текущей тройки, а не отмена общего правила
проверки свежести.

Следующая тройка по текущему `backlog_rank` после исключения уже approved SKU:

1. `chev_kp_prikol_text0014` - WB-only, шеврон «Я все могу но не буду»;
2. `chev_kp_prikol_text0015` - WB-only, шеврон «Не ходи за мной я сам
   заблудился»;
3. `chev_kit2_nr_mvd_pict0004` - Ozon/WB, комплект шевронов
   «Полиция МВД ГИБДД/ДПС».

Тройка выдана fresh-аудиторам без refresh Layer 1 по решению владельца:

1. использован row-level Layer 1 package от 2026-06-28;
2. каждая карточка обработана отдельным одноразовым fresh-аудитором;
3. все доступные фото просмотрены, self-contained HTML и `audit.json` созданы;
4. HTML проверены на `390x844` и `1366x1000`, горизонтального overflow и
   битых встроенных изображений нет;
5. файлы отправлены владельцу сразу по готовности и ожидают review;
6. `согласовано` означает только Layer 2 corrections и создание Layer 3;
   marketplace write начинается только после отдельного `применяй` по точно
   показанному пакету.

Layer 2 текущей тройки:

- `data/catalog/card_audits/fresh_triples_stale_layer1_20260715/0053_chev_kp_prikol_text0014/`;
- `data/catalog/card_audits/fresh_triples_stale_layer1_20260715/0054_chev_kp_prikol_text0015/`;
- `data/catalog/card_audits/fresh_triples_stale_layer1_20260715/0055_chev_kit2_nr_mvd_pict0004/`.

Все три аудита прошли layout-проверку. После owner review для всех трех созданы
Layer 3 паспорта со статусом `owner_approved_pending_batch_apply`; marketplace
write не выполнялся.

HTML отправлены владельцу как реальные Telegram-документы через общий бот в
рабочий топик `42336`:

- `chev_kp_prikol_text0014.html` - `message_id=94451`;
- `chev_kp_prikol_text0015.html` - `message_id=94452`;
- `chev_kit2_nr_mvd_pict0004.html` - `message_id=94453`.

Актуальное правило петлиц: одна неразрезанная пара с двумя видимыми петлицами
считается одной физической и одной товарной единицей. Поэтому количество в
упаковке и единиц в товаре равно `1`, комплектация -
`неразрезанная пара петлиц на липучке 1 шт.`

## Короткий текущий статус 2026-07-13

Это актуальный верхний checkpoint. Раздел 2026-07-08 ниже оставлен как
история и не должен использоваться как текущая очередь pending.

2026-07-12 владелец подтвердил `Применяй все 10` по накопленной пачке:

```text
chev_back_fsb_text0002
chev_back_fsin_text0002
chev_back_mvd_text0002
chev_back_mvd_text0003
chev_back_mvd_text0006
chev_back_rg_text0003
chev_nr_bpla_pict0011
chev_nr_sht_pict0022
nash_back_mvd_text0001
nash_back_mvd_text0002
```

Выполнено:

- preflight 2026-07-12: Ozon API, Ozon Performance API и WB API `ok`; Ozon
  LK/CDP browser keepalive вернул `Похоже, нет соединения`, поэтому ЛК Ozon
  остается отдельным runtime-риском, но карточный apply шел API-маршрутом;
- dry-run package:
  `data/runs/2026-07-12/apply_10_approved_cards_20260712T0822`;
- apply package:
  `data/runs/2026-07-12/apply_10_approved_cards_20260712T0824`;
- seller SKU update применен и проверен для `10/10`: Ozon `10/10`, WB
  existing cards `4/4`;
- content update отправлен для `10/10`: Ozon `10/10`, WB existing cards
  `4/4`;
- повторный verify:
  `data/runs/2026-07-12/verify_10_approved_cards_20260712T0827`;
- тесты после исправления apply-кода:
  `tests/test_wb_card_create_plan.py`,
  `tests/test_approved_cards_apply_catalog_sync.py`,
  `tests/test_card_content_update.py` -> `21 passed`.

Хвост batch apply доведен 2026-07-13:

- Ozon recovery по `chev_back_fsb_text0002`, `chev_back_mvd_text0006`,
  `nash_back_mvd_text0002` выполнен штатно:
  `data/runs/2026-07-13/ozon_tail_recovery_plan_20260713T1851`,
  `data/runs/2026-07-13/ozon_tail_recovery_apply_20260713T1853`;
- повторный verify Ozon/WB:
  `data/runs/2026-07-13/ozon_tail_recovery_verify_20260713T1854`;
  Ozon `ok` по всем трем, WB `ok` по `nash_back_mvd_text0002`;
- WB create по готовой Ozon-only карточке `chev_back_rg_text0003` выполнен:
  `data/runs/2026-07-13/wb_create_tail_rg_only_plan_20260713T1858`,
  `data/runs/2026-07-13/wb_create_tail_rg_only_apply_20260713T1859`;
  WB `nmID=1261548212`, barcode `2053383093488`, media upload `ok`;
- финальный verify `chev_back_rg_text0003`:
  `data/runs/2026-07-13/verify_chev_back_rg_text0003_after_wb_create_20260713T1901`;
  Ozon `ok`, WB `ok`.

Оставшиеся пять `content_applied` карточек закрыты 2026-07-13 после решения
владельца использовать для WB create те же фото, что уже стоят на Ozon. Перед
WB create Ozon photos были read-only получены через Ozon Seller API и записаны
в Layer 3 `media.target_assets` / `media.target_marketplace_photo_set` с
меткой `owner_media_decision.status=approved_use_ozon_photos_for_wb_create`.

Dry-run:

```text
data/runs/2026-07-13/wb_create_remaining5_ozon_photos_plan_20260713T1925
```

Apply:

```text
data/runs/2026-07-13/wb_create_remaining5_ozon_photos_apply_20260713T1926
```

Итог apply: `overall_status=ok`, `submitted_items=5`,
`found_after_apply=5`, `missing_after_apply=0`, `media_upload_attempts=5`,
`media_upload_errors=0`, `pending_media_uploads=0`,
`relevant_error_batches=0`.

Созданы WB-карточки:

| internal_sku | WB nmID | barcode |
| --- | ---: | --- |
| `chev_back_fsb_text0002` | `1261579149` | `2053383507206` |
| `chev_back_fsin_text0002` | `1261579150` | `2053383507213` |
| `chev_back_mvd_text0002` | `1261579151` | `2053383507220` |
| `chev_back_mvd_text0003` | `1261579152` | `2053383507237` |
| `chev_back_mvd_text0006` | `1261579153` | `2053383507244` |

Финальный verify:

```text
data/runs/2026-07-13/verify_remaining5_after_wb_create_20260713T1928
```

Итог verify: `overall_status=ok`, Ozon `rows=5/status=ok`, WB
`rows=5/status=ok`. Ozon checks для всех пяти: title/name, description,
colors, dimensions, hashtags, `23536=false`, package weight, photo count,
product status/errors. WB checks для всех пяти: title, description,
dimensions, colors.

После verify выполнена локальная синхронизация слоев и закрытие статусов через
`card_status_sync`: Layer 2 audit, Layer 3 passport,
`data/catalog/card_status/latest.json` и SQLite `card_work_items` больше не
держат эти пять SKU как pending/content-applied.

Полностью закрыты как `owner_approved_applied_verified` /
`applied_verified` все 10 карточек пачки:

```text
chev_back_fsb_text0002
chev_back_fsin_text0002
chev_back_mvd_text0002
chev_back_mvd_text0003
chev_back_mvd_text0006
chev_back_rg_text0003
chev_nr_bpla_pict0011
chev_nr_sht_pict0022
nash_back_mvd_text0001
nash_back_mvd_text0002
```

Текущие счетчики Layer 3 approved passports после синхронизации 2026-07-13:

- всего approved passports: `210`;
- по верхнему `approval.status`: `209`
  `owner_approved_applied_verified`, `1` `owner_approved` для особого
  WB-only/Ozon-removed паспорта `chev_pz_ng_text0074`;
- по `approval.marketplace_apply.status`: `209` `applied_verified`, `1`
  `wb_applied_ozon_removed_policy`;
- актуальный статусный snapshot:
  `data/catalog/card_status/latest.json`.

Нормализация верхнего `approval.status` выполнена 2026-07-13:

```text
data/runs/2026-07-13/normalize_card_approval_status_20260713T1952
```

До нормализации `184` паспорта имели
`approval.marketplace_apply.status=applied_verified`, но верхний
`approval.status` оставался старым (`owner_approved`,
`owner_approved_pending_batch_apply`, `owner_approved_pending_apply` или
`owner_approved_for_dry_run`). После нормализации все такие паспорта переведены
в `owner_approved_applied_verified`; особый статус
`wb_applied_ozon_removed_policy` не тронут. Runtime `card_work_items` по
последней пачке из 10 SKU переведен из `applied` в `closed`.

Важное исправление кода 2026-07-12:

- `_sync_approved_card_catalog_layers` больше не ставит
  `marketplace_apply.status=applied_verified` до финальной verify; закрытие в
  `applied_verified` должно выполняться только через `card_status_sync` после
  успешной проверки всех обязательных stages.
- В `wb_card_create_plan` исправлен report-format crash на
  `missing_characteristics` без `id`.

Следующий безопасный шаг после закрытия пачки 2026-07-13:

1. Не брать повторно 10 закрытых карточек из текущей пачки: они уже применены,
   проверены и закрыты локально.
2. Следующую карточную работу начинать с нового приоритетного SKU из
   актуального `card_content_audit_backlog.csv` или с отдельного owner-approved
   списка, а не из старого хвоста `content_applied`.
3. Отдельно восстановить/проверить браузерный ЛК Ozon: preflight показал
   API ok, но seller.ozon.ru через CDP возвращает страницу
   `Похоже, нет соединения`.

## Исторический статус 2026-07-08

Раздел ниже оставлен как контекст по уже выполненным пачкам.

Состояние карточного контура:

- Layer 3 approved passports: `200` файлов;
- статусы Layer 3 по `approval.marketplace_apply.status`: `199`
  `applied_verified`, `1` особый статус
  `wb_applied_ozon_removed_policy`; текущих `not_applied` паспортов нет;
- единственный особый паспорт: `chev_pz_ng_text0074`, WB-only, Ozon удален или
  не должен восстанавливаться без отдельного решения владельца;
- последняя согласованная пачка `chev_ng_bpla_text0001`,
  `chev_ng_fsb_text0003`, `chev_ng_fsb_text0004`,
  `chev_nr_bpla_pict0004` применена и проверена 2026-07-08:
  `data/runs/2026-07-08/apply_approved_cards_20260708T1912`,
  финальный `post_verify_status=ok`;
- `data/catalog/content/content_master.csv`: `710` строк, `644` уникальных
  `internal_sku`, из них `199` покрыты approved-passport, примерно `445`
  уникальных SKU еще без паспорта;
- `data/catalog/content/card_content_audit_backlog.csv`: `674` строки, `608`
  уникальных `internal_sku`, из них `183` покрыты approved-passport, примерно
  `425` уникальных SKU еще без паспорта;
- в backlog сейчас `178` строк с `business_priority=now`, `221` строк с
  `audit_priority=high`.

Последние закрытые проверки и apply:

- owner-approved пачка `chev_ng_bpla_text0001`, `chev_ng_fsb_text0003`,
  `chev_ng_fsb_text0004`, `chev_nr_bpla_pict0004` применена штатной
  `apply-approved-cards`:
  `data/runs/2026-07-08/apply_approved_cards_20260708T1912`; итог
  `overall_status=ok`, seller SKU update `4/4`, content update `4/4` с
  промежуточным warning из-за асинхронной проверки, WB/Ozon create `skipped`,
  catalog sync `ok`, финальный post-verify `ok` по Ozon `4/4` и WB `4/4`;
  Layer 2/Layer 3 статусы синхронизированы на
  `owner_approved_applied_verified` / `applied_verified`;
- `chev_kp_chvk_pict0001` проверен live verify
  `verify_chev_kp_chvk_pict0001_20260708T_check`: Ozon `ok`, WB `ok`,
  `overall_status=ok`; локальные Layer 2/Layer 3 статусы исправлены на
  `owner_approved_applied_verified` / `applied_verified`;
- по 9 карточкам старого `seo_priority_20260628` выполнен Ozon attrs recovery
  `23536=false` и `4497`, затем отдельный recovery порядка цветов `10096`;
- финальный verify по этим 9:
  `data/runs/2026-07-08/ozon_color_order_recovery_verify_final_20260708T1200`,
  `overall_status=ok`, Ozon `ok`, WB `ok`;
- все Layer 2 `audit.json` по `seo_priority_20260628/0001-0016` сейчас
  закрыты как `owner_approved_applied_verified`; `marketplace_apply.status` у
  них равен `applied_verified`.

Правила, которые нельзя потерять:

- для всех текущих изделий Vital Shevron маркировка/KIZ не требуется; Ozon
  `23536 / Нужен код маркировки` должен быть `false`;
- текущий вес: обычный шеврон/нашивка/петлица - минимум `10 г`, задний шеврон
  - `30 г`, комплект - сумма физических изделий;
- порядок цветов в паспорте значим: Ozon/WB должны совпадать с owner-approved
  Layer 3 по составу и порядку, если владелец не разрешил исключение;
- описания карточек должны быть написаны естественно, от лица производителя
  для покупателя: понятно описывать товар, характеристики, применение и
  преимущества, быть привлекательными и не звучать как учебник, справка о
  составе или внутренний аудит;
- описание должно состоять из трех тематических блоков с точными названиями:
  `Описание товара`, `Преимущества и характеристики товара`,
  `О производителе`;
- Ozon-хештеги/WB-теги выбирать только из релевантных запросов и
  приоритизировать по максимальной подтвержденной частотности; не добивать
  список низкорелевантными тегами ради количества;
- если фон шеврона - камуфляж `мох`, а в справочниках/полях Ozon/WB нет цвета
  `мох`, в marketplace-поле `Цвет`/`Цвет товара` указывать `зеленый` без `ё`;
  `мох` можно оставлять только в человекочитаемом названии варианта или
  описании, если это нужно для различения товара;
- `зеленый` как fallback не применять к обычному оливковому фону: если фон
  оливковый, цвет карточки должен быть `оливковый`;
- размеры изделия в карточках и паспортах указывать в порядке
  `ширина x высота`;
- после Ozon `attributes/update` нельзя полагаться на первый
  `/v1/product/import/info`, если он вернул неполный список строк; poll до
  ожидаемого количества, явных ошибок или timeout;
- после любого успешного `apply -> verify` сразу синхронизировать Layer 2,
  Layer 3 и этот checkpoint.

Следующий безопасный шаг:

1. Не брать повторно `seo_priority_20260628/0001-0016` в apply: они уже
   закрыты.
2. Если продолжаем массовую карточную работу, брать следующий незакрытый SKU
   из актуального `card_content_audit_backlog.csv`, а не из старых pending
   заметок.
3. Текущие согласованные без применения паспорта:
   `chev_nr_bpla_pict0011`, `chev_nr_sht_pict0022`.
4. Карточка `chev_nr_bpla_pict0004`,
   `Шеврон на липучке БПЛА Воздушная разведка, оливковый`, согласована
   владельцем и применена в пачке
   `data/runs/2026-07-08/apply_approved_cards_20260708T1912`; HTML review:
   `data/catalog/card_audits/seo_priority_20260708/0042_chev_nr_bpla_pict0004/chev_nr_bpla_pict0004_owner_review_fast.html`;
   Telegram `summary_message_id=90352`, `document_message_id=90353`.
5. Карточка `chev_nr_bpla_pict0011`,
   `Шеврон на липучке БПЛА Оператор`, согласована владельцем 2026-07-08 с
   правками: Ozon-хештеги не сокращать, а сохранять/расширять популярными
   релевантными; Ozon `offer_id` и WB `vendorCode` при будущем apply менять
   на внутренний артикул `chev_nr_bpla_pict0011`. Marketplace write не
   выполнялся.
   - Layer 2:
     `data/catalog/card_audits/fresh_v2_pilot_20260708/0043_chev_nr_bpla_pict0011/audit.json`
     со статусом `owner_approved_pending_batch_apply`;
   - согласованный HTML:
     `data/catalog/card_audits/fresh_v2_pilot_20260708/0043_chev_nr_bpla_pict0011/chev_nr_bpla_pict0011.html`;
   - исходный файл fresh-аудитора без правок оркестратора сохранен отдельно:
     `data/catalog/card_audits/fresh_v2_pilot_20260708/0043_chev_nr_bpla_pict0011/chev_nr_bpla_pict0011_agent_original.html`;
   - Layer 3:
     `data/catalog/master_passport/approved/chev_nr_bpla_pict0011.json`;
   - promotion run:
     `data/runs/2026-07-08/promote_approved_card_passport_20260708T205623/`.
6. 2026-07-08 запущен owner-review поток тройками через отдельных
   fresh-аудиторов. Текущая тройка отправлена владельцу в чат; marketplace
   write, Layer 3 и dry-run apply не выполнялись:
   - `chev_nr_sht_pict0022`: владелец написал `Шторм z согласовано`;
     Layer 2 переведен в `owner_approved_pending_batch_apply`, Layer 3 создан
     и проверен, marketplace write не выполнялся.
     `data/catalog/card_audits/fresh_triples_20260708/0046_chev_nr_sht_pict0022/audit.json`,
     `data/catalog/card_audits/fresh_triples_20260708/0046_chev_nr_sht_pict0022/chev_nr_sht_pict0022.html`,
     `data/catalog/master_passport/approved/chev_nr_sht_pict0022.json`;
     promotion run:
     `data/runs/2026-07-08/promote_approved_card_passport_20260708T211800/`;
   - `nash_back_mvd_text0002`: владелец согласовал с правкой Ozon
     `Название цвета = нашивка МВД России, на спину, серый`;
     Layer 2 переведен в `owner_approved_pending_batch_apply`, Layer 3 создан
     и проверен, marketplace write не выполнялся.
     `data/catalog/card_audits/fresh_triples_20260708/0212_nash_back_mvd_text0002/audit.json`,
     `data/catalog/card_audits/fresh_triples_20260708/0212_nash_back_mvd_text0002/nash_back_mvd_text0002.html`,
     `data/catalog/master_passport/approved/nash_back_mvd_text0002.json`;
     promotion run:
     `data/runs/2026-07-08/promote_approved_card_passport_20260708T215908/`;
   - `nash_back_mvd_text0001`: владелец согласовал с правками Ozon
     `Название модели = МВД` и
     `Название цвета = нашивка МВД России, на спину, чёрно-белый`;
     Layer 2 переведен в `owner_approved_pending_batch_apply`, Layer 3 создан
     и проверен, marketplace write не выполнялся.
     `data/catalog/card_audits/fresh_triples_20260708/0211_nash_back_mvd_text0001/audit.json`,
     `data/catalog/card_audits/fresh_triples_20260708/0211_nash_back_mvd_text0001/nash_back_mvd_text0001.html`,
     `data/catalog/master_passport/approved/nash_back_mvd_text0001.json`;
     promotion run:
     `data/runs/2026-07-09/promote_approved_card_passport_20260709T070734/`.
   2026-07-08 владелец указал дефекты первичного HTML
   `nash_back_mvd_text0001`: в Telegram-документе не было фото, в описании
   была внутренняя фраза `если владелец подтвердит текущий тип крепления по
   партии`, аудит был неполным по цвету товара и модели/группировке.
   Оркестратор исправил Layer 2: HTML стал self-contained с 7 встроенными
   `data:image` фото, описание очищено, добавлены рекомендации по Ozon/WB
   цвету, Ozon `Название цвета`, Ozon `Название модели`, grouping
   `manual_review`, вес пришивной нашивки без липучки исправлен на 10 г.
   Проверка: JSON валиден; Chrome headless render прошел для mobile
   `390x844` и desktop `1366x1000`; screenshots:
   `layout_mobile_fixed.png`, `layout_desktop_fixed.png`.
   После повторной проверки владелец согласовал карточку с правками по модели
   и названию цвета; следующий шаг для нее - только будущий approved batch
   apply через dry-run/plan/apply/verify.
7. Продолжение должно идти от ответа владельца по конкретному HTML:
   `согласовано` -> внести owner corrections в Layer 2 и создать Layer 3
   passport; `применяй` -> только после Layer 3 через безопасный
   dry-run/plan/apply/verify. Marketplace write только после точного owner
   approval `применяй` по показанному пакету. Следующая тройка после
   фильтрации существующих Layer 2/Layer 3 взята из row-level package index:
   `chev_back_mvd_text0006`, `chev_back_fsb_text0002`,
   `chev_back_fsin_text0002`.
8. Следующая тройка fresh-аудитов создана и отправлена владельцу в чат
   2026-07-08; marketplace write и dry-run apply не выполнялись:
   - `chev_back_fsin_text0002`:
     `data/catalog/card_audits/fresh_triples_20260708/0019_chev_back_fsin_text0002/audit.json`,
     `data/catalog/card_audits/fresh_triples_20260708/0019_chev_back_fsin_text0002/chev_back_fsin_text0002.html`.
     2026-07-09 владелец согласовал HTML без дополнительных правок; Layer 2
     переведен в `owner_approved_pending_batch_apply`, создан Layer 3 passport
     `data/catalog/master_passport/approved/chev_back_fsin_text0002.json`,
     promotion run:
     `data/runs/2026-07-09/promote_approved_card_passport_20260709T071148/`.
     Карточка Ozon-only: текущий Ozon offer_id `back0025`, целевой внутренний
     артикул продавца `chev_back_fsin_text0002`, WB-карточка отсутствует и
     остается future create later. Следующий шаг - только будущий approved batch
     apply через dry-run/plan/apply/verify;
   - `chev_back_mvd_text0006`:
     `data/catalog/card_audits/fresh_triples_20260708/0011_chev_back_mvd_text0006/audit.json`,
     `data/catalog/card_audits/fresh_triples_20260708/0011_chev_back_mvd_text0006/chev_back_mvd_text0006.html`.
     Оркестратор сделал только техническую упаковку коллажа в `data:image`
     для Telegram HTML, без изменения содержательных рекомендаций fresh-
     аудитора. 2026-07-09 владелец согласовал HTML с правкой Ozon
     `Название цвета = Шеврон МВД России, на спину, чёрно-серый`; Layer 2
     переведен в `owner_approved_pending_batch_apply`, создан Layer 3 passport
     `data/catalog/master_passport/approved/chev_back_mvd_text0006.json`,
     promotion run:
     `data/runs/2026-07-09/promote_approved_card_passport_20260709T072013/`.
     Карточка Ozon-only: текущий Ozon offer_id `back0004`, целевой внутренний
     артикул продавца `chev_back_mvd_text0006`, WB-карточка отсутствует и
     остается future create later. Следующий шаг - только будущий approved batch
     apply через dry-run/plan/apply/verify;
   - `chev_back_fsb_text0002`:
     `data/catalog/card_audits/fresh_triples_20260708/0018_chev_back_fsb_text0002/audit.json`,
     `data/catalog/card_audits/fresh_triples_20260708/0018_chev_back_fsb_text0002/chev_back_fsb_text0002.html`.
     2026-07-09 владелец согласовал HTML с правкой Ozon
     `Название цвета = Шеврон ФСБ на спину, мох`; Layer 2 переведен в
     `owner_approved_pending_batch_apply`, создан Layer 3 passport
     `data/catalog/master_passport/approved/chev_back_fsb_text0002.json`,
     promotion run:
     `data/runs/2026-07-09/promote_approved_card_passport_20260709T073615/`.
     Карточка Ozon-only: текущий Ozon offer_id `back0011`, целевой внутренний
     артикул продавца `chev_back_fsb_text0002`, WB-карточка отсутствует и
     остается future create later. Цвет товара сохранен по правилу `мох`:
     marketplace `зеленый, черный, бежевый`, а `мох` остается в названии цвета.
     Следующий шаг - только будущий approved batch apply через
     dry-run/plan/apply/verify.
9. Следующая тройка fresh-аудитов создана отдельными worker-аудиторами
   2026-07-09; marketplace write, Layer 3 и dry-run apply не выполнялись.
   После первой ошибочной выдачи с неверными путями Layer 1 оркестратор
   перезапустил аудиторов по точным `package_index.json` путям
   `0020_ozon_back0007`, `0021_ozon_back0008`, `0022_ozon_back0021`.
   Все три итоговых HTML self-contained и прошли browser/layout validation:
   - `chev_back_mvd_text0002`:
     `data/catalog/card_audits/fresh_triples_20260709/0020_chev_back_mvd_text0002/audit.json`,
     `data/catalog/card_audits/fresh_triples_20260709/0020_chev_back_mvd_text0002/chev_back_mvd_text0002.html`;
     Ozon-only `back0007`, recommended title
     `Шеврон на липучке ДПС на спину`, recommended color name
     `ДПС на спину, черно-серый`, layout `passed`;
     owner approved 2026-07-09 with corrections: title
     `Шеврон на липучке ДПС на спину, чёрно-серый`, color name
     `Шеврон ДПС на спину, чёрно-серый`; Layer 2 status
     `owner_approved_pending_batch_apply`, Layer 3 passport written to
     `data/catalog/master_passport/approved/chev_back_mvd_text0002.json`,
     promotion run
     `data/runs/2026-07-09/promote_approved_card_passport_20260709T082517/`.
     Marketplace apply remains `not_applied`; current Ozon offer_id
     `back0007`, target internal seller SKU `chev_back_mvd_text0002`,
     WB remains future create later;
   - `chev_back_mvd_text0003`:
     `data/catalog/card_audits/fresh_triples_20260709/0021_chev_back_mvd_text0003/audit.json`,
     `data/catalog/card_audits/fresh_triples_20260709/0021_chev_back_mvd_text0003/chev_back_mvd_text0003.html`;
     Ozon-only `back0008`, recommended title
     `Шеврон на липучке Спецназ МВД на спину`, recommended color name
     `Спецназ МВД на спину, черно-серый`, layout `passed`;
     owner approved 2026-07-09 with corrections: title
     `Шеврон на липучке Спецназ МВД на спину, чёрно-серый`
     (`51` chars, within 60), Ozon hashtags rewritten with `_` separators,
     color name `Шеврон Спецназ МВД на спину, черно-серый`; Layer 2 status
     `owner_approved_pending_batch_apply`, Layer 3 passport written to
     `data/catalog/master_passport/approved/chev_back_mvd_text0003.json`,
     promotion run
     `data/runs/2026-07-09/promote_approved_card_passport_20260709T085031/`.
     Marketplace apply remains `not_applied`; current Ozon offer_id
     `back0008`, target internal seller SKU `chev_back_mvd_text0003`,
     WB remains future create later;
   - `chev_back_rg_text0003`:
     `data/catalog/card_audits/fresh_triples_20260709/0022_chev_back_rg_text0003/audit.json`,
     `data/catalog/card_audits/fresh_triples_20260709/0022_chev_back_rg_text0003/chev_back_rg_text0003.html`;
     Ozon-only `back0021`, recommended title
     `Шеврон на липучке ОМОН Росгвардия на спину`, recommended color name
     `ОМОН Росгвардия, на спину, черно-серый`, layout `passed`;
     owner approved 2026-07-09 with corrections: title
     `Шеврон на липучке ОМОН на спину, чёрно-серый`
     (`44` chars, within 60), color name
     `Шеврон ОМОН, на спину, чёрно-серый` (owner `<b>` tags treated as
     chat formatting, not marketplace field content), Ozon hashtags expanded
     to 30 relevant/popular joined tags without spaces or `_`; Layer 2 status
     `owner_approved_pending_batch_apply`, Layer 3 passport written to
     `data/catalog/master_passport/approved/chev_back_rg_text0003.json`,
     promotion run
     `data/runs/2026-07-09/promote_approved_card_passport_20260709T093057/`.
     Marketplace apply remains `not_applied`; current Ozon offer_id
     `back0021`, target internal seller SKU `chev_back_rg_text0003`,
     WB remains future create later.
   Оркестратор сделал только техническую JSON-синхронизацию alias-полей
   `proposed_final_card.color_name` и `target_physical_params.color_name`
   из уже показанных в HTML/рекомендациях значений для `0020` и `0022`;
   содержательные рекомендации аудиторов не менялись. Следующее действие -
   owner review HTML по очереди.

## Актуальное правило batch apply

С 2026-07-05 `apply-approved-cards` должен выполнять стадии в таком порядке:

```text
Layer 3 preflight -> seller SKU update -> content update -> WB create ->
Ozon create -> catalog-sync -> targeted post-verify
```

Причина: порядок `content update -> seller SKU update` дал повторяемый риск:
часть WB-карточек могла быть перезаписана старым payload во время смены
`vendorCode`, а Ozon/WB verify временно сверял старые и новые идентификаторы.
Если в пакете есть смена артикула продавца, артикул меняется первым, затем все
контентные изменения, фото, создание недостающих карточек и verify идут уже по
финальным internal SKU.

## Последняя примененная пачка 2026-07-05

Пачка `0011-0014` из `seo_priority_20260705` применена после owner approval
владельца:

```text
run: data/runs/2026-07-05/apply_approved_cards_20260705T220522
report: data/runs/2026-07-05/apply_approved_cards_20260705T220522/apply_approved_cards_report.md
post-verify: data/runs/2026-07-05/apply_approved_cards_20260705T220522_post_verify
```

Итог:

- overall status: `ok`;
- passport preflight: `ok`;
- seller SKU update: `ok`, 4/4;
- content update: промежуточный `warning` из-за асинхронной Ozon-проверки,
  но финальный post-verify `ok`;
- WB create: `ok`, создана 1 карточка `chev_back_rg_text0001`;
- Ozon create: `skipped`, не требовалось;
- catalog sync: `ok`;
- final post-verify: `ok`, Ozon 4/4 и WB 4/4.

Примененные и проверенные карточки:

| N | internal SKU | Итог |
| --- | --- | --- |
| 0011 | `chev_kp_prikol_pict0002` | Ozon/WB content, seller SKU, media, verify ok |
| 0012 | `chev_kit2_nr_rg_pict0010` | Ozon/WB content, seller SKU, media, verify ok |
| 0013 | `chev_back_rg_text0001` | Ozon content, seller SKU, новая WB-карточка, media, verify ok |
| 0014 | `chev_nr_svo_pict0019` | Ozon/WB content, seller SKU, media, verify ok |

Правка по `chev_nr_svo_pict0019`, согласованная владельцем перед apply:
название `Шеврон на липучке СВО АК Моя вера`, модель Ozon `СВО`, стандартное
фото с военным и вариантами ношения добавлено в целевой фотосет, служебная
фраза про SEO-запросы удалена из описания.

Если следующий агент видит `content_status=warning` в run summary, не считать
это открытым хвостом без проверки финального
`data/runs/2026-07-05/apply_approved_cards_20260705T220522_post_verify`.
Финальная проверка подтвердила фактическое состояние.

Пачка `0006-0010` из `seo_priority_20260704` применена после согласования
владельца:

```text
main apply: data/runs/2026-07-05/apply_approved_cards_0006_0010_20260705T1944
recovery apply: data/runs/2026-07-05/recovery_content_apply_0006_0007_20260705T2005
final verify: data/runs/2026-07-05/verify_recovery_0006_0007_retry2_20260705T2007
```

Итоговый recovery verify: `overall_status=ok`, Ozon `2/2`, WB `2/2` по
оставшимся предупреждениям. Карточки пачки:

| N | internal SKU | Итог |
| --- | --- | --- |
| 0006 | `nash_kit2_nr_mvd_pict0005` | Ozon/WB content, seller SKU, verify ok после recovery |
| 0007 | `chev_back_fsin_text0001` | Ozon/WB content, seller SKU, verify ok после recovery |
| 0008 | `chev_kit2_nr_rg_pict0002` | Ozon/WB content, seller SKU, verify ok |
| 0009 | `chev_kit2_nr_rg_pict0005` | Ozon/WB content, seller SKU, verify ok |
| 0010 | `chev_kp_bpla_pict0003` | Ozon/WB content, seller SKU, verify ok |

Если следующий агент видит старый `content_status=warning` в основном apply,
не считать это открытым хвостом без проверки финального recovery verify выше.
Причина предупреждений была асинхронность/порядок стадий; итоговая проверка
после точечного recovery подтвердила фактическое состояние.

## Последняя примененная пачка карточек

Последняя owner-approved пачка применена штатной batch-командой
`apply-approved-cards`:

```text
run: data/runs/2026-06-29/apply_approved_cards_0009_0014_20260629T2325
post-verify: data/runs/2026-06-29/verify_after_apply_6_cards_20260629T2328
report: data/runs/2026-06-29/apply_approved_cards_0009_0014_20260629T2325/post_apply_verify_report.md
Telegram message: 75895
```

Итог по пачке:

- content update: 6/6;
- seller SKU replacement Ozon: 6/6;
- seller SKU replacement WB: 5/5 существующих WB;
- WB create: 1/1;
- WB media upload: без ошибок;
- catalog sync: `ok`;
- post-verify: `overall_status=ok`, `ready_rows=6`, `blocked_rows=0`.

Примененные и проверенные карточки:

| N | internal SKU | Что сделано |
| --- | --- | --- |
| 0009 | `chev_nr_oborg_pict0001` | Ozon/WB content, seller SKU, media, verify |
| 0010 | `chev_nr_oborg_pict0005` | Ozon/WB content, seller SKU, media, verify |
| 0011 | `chev_nr_bpla_pict0020` | Ozon content, seller SKU, новая WB-карточка, media, verify |
| 0012 | `chev_back_fsin_pict0001` | Ozon/WB content, seller SKU, media, verify |
| 0013 | `chev_kit2_nr_fsin_pict0004` | Ozon/WB content, seller SKU, media, verify |
| 0014 | `chev_kit2_nr_rg_pict0009` | Ozon/WB content, seller SKU, согласованный WB media subset, verify |

Важно: в `data/catalog/master_passport/approved/*.json` поле
`approval.status` у части файлов может оставаться старым
`owner_approved_pending_batch_apply`. Для факта marketplace apply по последней
пачке смотреть:

- `data/catalog/card_audits/seo_priority_20260628/*/audit.json`
  `owner_review.status=owner_approved_applied_verified`;
- `marketplace_apply.status=applied_verified`;
- post-verify report выше.

## Комплекты Позывных Мох

Текущий шаблон серии: `chev_kit2_pz_text0022` / `Турист`.

Статус на 2026-07-04:

- `chev_kit2_pz_text0022` применен и проверен через
  `apply_approved_cards_20260704T165430`;
- по шаблону `Турист` создано `76` новых Layer 3 approved-passport файлов для
  остальных комплектов позывных `мох`;
- всего в `data/catalog/master_passport/approved/chev_kit2_pz_text*.json`
  сейчас `77` валидных JSON: `77` примененных и проверенных паспортов;
- run генерации:
  `data/runs/2026-07-04/kit_pz_passports_from_tourist_template_20260704T1702`;
- owner-review первых 5:
  `data/runs/2026-07-04/kit_pz_first5_owner_review_20260704T1720/report.html`,
  Telegram document `82076`;
- владелец согласовал всю серию без просмотра остальных HTML и сказал:
  `Применяй все. Все комплекты позывных.`;
- marketplace apply выполнен пачками:
  `kit_pz_apply_all_batch01_20260704T1732`,
  `kit_pz_apply_all_batch02_20260704T1735`,
  `kit_pz_apply_all_batch03_20260704T1738`,
  `kit_pz_apply_all_batch04_20260704T1740` ...
  `kit_pz_apply_all_batch08_20260704T1740`;
- итог apply: `76/76` pending-паспортов получили
  `marketplace_apply.status=applied_verified`;
- WB-create: `54` новых WB-карточки, relevant WB card errors `0`,
  pending media uploads `0`;
- seller SKU/content/catalog-sync: `ok` по всем 8 пачкам;
- итоговый runner summary:
  `data/runs/2026-07-04/kit_pz_apply_all_remaining_20260704T1740/summary.json`;
- проверка: названия до `60` символов, описание в `3` блока, целевой фотосет
  `5` фото, marketplace-цвет `зеленый, черный`, `мох` сохранен в названии,
  названии цвета и описании.

Во всех пачках `content_status=warning` из-за промежуточной Ozon-проверки по
старым `offer_id` до смены seller SKU. Это не считается блокером, если
последующие стадии `seller_sku_status=ok`, `catalog_sync_status=ok` и
`post_verify_status=ok`. По этой серии финальный `post_verify_status=ok` во
всех 8 пачках.

Исключения из пачки:

- `chev_kit2_pz_text0024` / `pzmh0024` - старый дубль `Турист`, владелец
  перенес его в архив Ozon, в паспорта и будущий apply не включать;
- `chev_kit2_pz_text0076` / `pzmh0095` - `Сталкер`; владелец отправил в
  архив, но Ozon dry-run `ozon_product_remove_plan_20260704T170808` заблокировал
  archive ошибкой `ozon_archive_requires_zero_stock`. Пока товар не применять,
  не переносить на WB и не создавать паспорт. Следующий шаг по нему: убрать или
  обнулить остаток, затем повторить `plan-ozon-product-remove` /
  `apply-ozon-product-remove`.

Для этой серии владелец согласовал нейтральное описание малого шеврона:

```text
меньший шеврон дополнен тематическим изображением
```

Не нужно вручную описывать конкретную картинку каждого малого шеврона в этой
шаблонной пачке. Это не отменяет полный фото-аудит для других серий и новых
шаблонов.

## Текущие статусы Layer 2

По папке `data/catalog/card_audits/seo_priority_20260628` на момент
чекпойнта:

| N | internal SKU | Статус |
| --- | --- | --- |
| 0001 | `chev_kp_chvk_pict0001` | `owner_approved_applied_verified`; 2026-07-08 live verify `verify_chev_kp_chvk_pict0001_20260708T_check` подтвердил Ozon/WB match Layer 3 |
| 0002 | `chev_nr_bpla_pict0023` | `owner_approved_applied_verified` |
| 0003 | `chev_nr_bpla_pict0026` | `owner_approved_applied_verified` |
| 0004 | `chev_nr_bpla_pict0028` | `owner_approved_applied_verified` |
| 0005 | `chev_nr_prikol_pict0009` | `owner_approved_applied_verified` |
| 0006 | `chev_nr_voisk_pict0012` | `owner_approved_applied_verified` |
| 0007 | `chev_back_form_text0003` | `owner_approved_applied_verified` |
| 0008 | `chev_nr_oborg_pict0003` | `owner_approved_applied_verified` |
| 0009-0014 | см. выше | применены и проверены |
| 0015 | `chev_pz_ng_text0012` | `owner_approved_applied_verified` |
| 0016 | `chev_pz_ng_text0018` | `owner_approved_applied_verified`; Ozon identity зафиксирован как `pzol0006` |

Перед продолжением не полагаться только на эту таблицу. Быстрая проверка:

```bash
/home/Codex/agent-tools/python/bin/python - <<'PY'
import json
from pathlib import Path
base = Path('data/catalog/card_audits/seo_priority_20260628')
for p in sorted(base.glob('*/audit.json')):
    d = json.loads(p.read_text(encoding='utf-8'))
    sku = (d.get('identity') or {}).get('internal_sku') or d.get('internal_sku') or (d.get('proposed_final_card') or {}).get('internal_sku')
    owner = (d.get('owner_review') or {}).get('status')
    apply = (d.get('marketplace_apply') or {}).get('status')
    print(p.parent.name, sku, owner, apply)
PY
```

## Status resync 2026-07-08 по старым Layer 2/Layer 3 рассинхронам

Выполнена read-only проверка 9 карточек из `seo_priority_20260628`, где Layer 2
и Layer 3 расходились по статусам:

- `chev_nr_bpla_pict0023`;
- `chev_nr_bpla_pict0026`;
- `chev_nr_bpla_pict0028`;
- `chev_nr_prikol_pict0009`;
- `chev_nr_voisk_pict0012`;
- `chev_back_form_text0003`;
- `chev_nr_oborg_pict0003`;
- `chev_pz_ng_text0012`;
- `chev_pz_ng_text0018`.

Итог после полного цикла 2026-07-08:

- WB verify `ok` по всем 9;
- Ozon нашел карточки и подтвердил основные поля без product errors:
  title/name, description, hashtags, colors, dimensions, weight, photo count,
  product status;
- Ozon-only recovery `23536=false` и `4497` выполнен по owner approval;
- отдельный Ozon-only recovery порядка цветов `10096` выполнен по owner
  approval;
- финальный verify:
  `data/runs/2026-07-08/ozon_color_order_recovery_verify_final_20260708T1200`,
  `overall_status=ok`, Ozon `ok`, WB `ok`;
- Layer 2 и Layer 3 по этим 9 карточкам синхронизированы в `applied_verified`;
- подробный итоговый отчет:
  `data/runs/2026-07-08/ozon_color_order_recovery_apply_20260708T1157/ozon_color_order_recovery_apply_report.md`.

По `chev_pz_ng_text0018` исправлен только локальный Layer 3 identity:

- `identity.ozon_offer_id_after_seller_sku_update`:
  `chev_pz_ng_text0018` -> `pzol0006`;
- `ozon.offer_id_after_seller_sku_update`:
  `chev_pz_ng_text0018` -> `pzol0006`.

Основание: run artifacts 2026-07-02 показывают, что Ozon product
`2122051727` после recovery фактически остался на offer id `pzol0006`.
Проверка по несуществующему `chev_pz_ng_text0018` давала Ozon 404.

Следующее действие по этим 9 карточкам:

1. Никакого повторного apply не требуется.
2. Не отправлять их повторно в обычный `apply-approved-cards` как pending.
3. Если позже возникнет новый drift, начинать только с
   `verify-card-content-update --internal-sku ...` и отдельного recovery
   dry-run.

### Ozon attrs recovery apply 2026-07-08

Владелец дал write approval: `Применяй`.

Выполнен Ozon-only apply через `/v1/product/attributes/update`, без WB:

- exact dry-run:
  `data/runs/2026-07-08/ozon_attrs_recovery_exact_dry_run_20260708T1143`;
- apply:
  `data/runs/2026-07-08/ozon_attrs_recovery_apply_20260708T1144`;
- Ozon task id: `5028201037`;
- применены только `23536 / Нужен код маркировки=false` и
  `4497 / вес товара в упаковке`;
- Ozon import info вернул `imported`, `errors=[]` по всем 9;
- verify:
  `data/runs/2026-07-08/ozon_attrs_recovery_verify_retry_20260708T1146`;
- полный отчет:
  `data/runs/2026-07-08/ozon_attrs_recovery_apply_20260708T1144/ozon_attrs_recovery_apply_report.md`.

Результат verify:

- целевые `23536` и `4497` исправлены по всем 9;
- WB остался `ok` по всем 9;
- Ozon title/name, description, hashtags, dimensions, weight, photo count,
  product status и product errors не блокируют;
- итоговый `verify-card-content-update` пока `warning`, потому что Ozon после
  attributes/update переставил порядок цветов у 7 SKU. Набор цветов совпадает
  с паспортом, порядок - нет.

Владелец затем подтвердил: `Применяй изменения порядка цветов`.

Выполнен отдельный Ozon-only apply по `10096 / Цвет товара`, без WB:

- exact dry-run:
  `data/runs/2026-07-08/ozon_color_order_recovery_exact_dry_run_20260708T1156`;
- apply:
  `data/runs/2026-07-08/ozon_color_order_recovery_apply_20260708T1157`;
- Ozon task id: `5028337997`;
- final verify:
  `data/runs/2026-07-08/ozon_color_order_recovery_verify_final_20260708T1200`;
- full report:
  `data/runs/2026-07-08/ozon_color_order_recovery_apply_20260708T1157/ozon_color_order_recovery_apply_report.md`.

Финальный `verify-card-content-update` по 9 SKU вернул `overall_status=ok`:
Ozon и WB совпадают с Layer 3 passport по проверяемым полям, включая
`23536=false`, `4497`, `10096 / Цвет товара`, title/name, description,
hashtags, dimensions, weight, photo count, product status и product errors.

Layer 2 `audit.json` и Layer 3 `master_passport/approved/*.json` по этим 9 SKU
синхронизированы в `applied_verified` с ссылками на apply/verify artifacts.

## Фиксированный формат HTML на review

Шаблон owner-review HTML зафиксирован владельцем. Не менять без отдельного
запроса владельца.

Обязательные элементы HTML:

- один встроенный contact-sheet/коллаж текущих фото Ozon/WB вверху;
- не добавлять второй визуальный блок с теми же отдельными фото;
- ниже текстовый фото-аудит по номерам: `Ozon 1...`, `WB 1...`;
- отдельная таблица целевого комплекта фото: номер, назначение, источник;
- блок `сейчас -> рекомендую -> почему` по названию, описанию, SEO,
  цветам, названию цвета, размерам, упаковке, материалу, составу,
  комплектации, фото, группировке;
- все релевантные текущие параметры Ozon и WB из snapshot/API;
- рекомендованное описание визуально разбито на 3 абзаца/блока;
- HTML должен быть удобен на desktop и mobile;
- перед отправкой открыть в браузере и проверить mobile `390x844`,
  desktop `1366x1000`, отсутствие горизонтального скролла.

Прототипы/источники формата:

```text
data/runs/2026-06-24/card_agent_audit_20260624/cards/0001_chev_back_fsb_text0001/audit_mobile.html
data/catalog/card_audits/seo_priority_20260628/*/*_owner_review_fast.html
```

Для подготовки HTML из существующего Layer 2 `audit.json` использовать
скрипт, а не ручную сборку:

```bash
/home/Codex/agent-tools/python/bin/python \
  scripts/card_reviews/prepare_owner_review.py \
  data/catalog/card_audits/seo_priority_20260628/<NNNN_internal_sku> \
  --validate \
  --write-json
```

Если нужно указать файл результата:

```bash
/home/Codex/agent-tools/python/bin/python \
  scripts/card_reviews/prepare_owner_review.py \
  data/catalog/card_audits/seo_priority_20260628/<NNNN_internal_sku> \
  --output data/catalog/card_audits/seo_priority_20260628/<NNNN_internal_sku>/<internal_sku>_owner_review_fast.html \
  --validate \
  --write-json
```

## Как отправлять HTML в Telegram

Если владелец просит прислать сам файл в текущий Telegram-топик, использовать
безопасный проектный helper, если он доступен, либо Bot API. Токен не писать в
команды и документы; брать только из внешнего token-file/env.

Текущий топик из ссылки владельца:

```text
chat_id=-1003683440820
message_thread_id=42336
```

Важно: для отправки файлов именно в рабочий Codex-топик `42336` использовать
токен общего `telegram-ai-agent` из `/home/pavel/projects/telegram-ai-agent/.env`,
а не токен `Vital Shevron Manager`. Магазинный бот может не состоять в этом
топике и вернуть `Bad Request: chat not found`.

Шаблон Bot API без секрета:

```bash
curl -sS -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendDocument" \
  -F "chat_id=-1003683440820" \
  -F "message_thread_id=42336" \
  -F "document=@/absolute/path/to/file.html" \
  -F "caption=Файл карточки на согласование"
```

Если используется проектный helper `send-telegram-report`, сначала проверить
его `--help` и ограничения безопасных путей.

## Правило owner approval

Если владелец посмотрел HTML и написал `согласовано`, `ок`, `применяй` или
дал конкретные правки и сказал применять, это является полным approval для
точно тех действий, которые показаны в HTML и дополнительных правках владельца:

- изменение master passport;
- изменение карточек Ozon/WB;
- смена seller SKU;
- создание новой WB-карточки;
- фото/media;
- Ozon hashtags/WB tags;
- характеристики;
- группировка только если она явно включена в пакет.

Правило 2026-07-04: если владелец пишет `согласовано`, но не пишет
`применяй`, агент обязан сразу зафиксировать owner approval в Layer 2,
создать или обновить Layer 3 master passport со статусом
`owner_approved_pending_batch_apply` и остановиться без marketplace write.
Это нужно, чтобы согласованные карточки не терялись между задачами и будущий
batch apply брал уже готовые паспорта. Если владелец дал правки, сначала
внести правки в Layer 2/HTML-цель, затем создавать паспорт.

`применяй` означает отдельное разрешение на write в маркетплейсы по уже
согласованным паспортам. Не смешивать `согласовано` и `применяй`.

Не запрашивать повторное подтверждение одного и того же. Остановиться нужно
только если:

- marketplace state изменился и план отличается от согласованного HTML;
- в HTML не было опасного действия, которое теперь нужно выполнить;
- API/LK вернул новый риск или отказ;
- не хватает обязательного Layer 3 passport;
- есть конфликт идентификаторов или seller SKU.

Пачки применять batch-командой, а не по одной карточке. Нормальный размер
пачки сейчас: 5 карточек, после стабильной работы можно 10.

## Команды apply

Перед marketplace write открыть `data/planning/card_ops/quick_access.md`.

Promotion из согласованного Layer 2 в Layer 3:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli promote-approved-card-passport \
  --internal-sku <internal_sku> \
  --write
```

Batch apply:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli apply-approved-cards \
  --internal-sku <sku_1> \
  --internal-sku <sku_2> \
  --internal-sku <sku_3> \
  --ozon-create-min-price <min_price_if_needed> \
  --confirmed-by-user
```

`apply-approved-cards` сейчас должен:

- восстановить отсутствующий Layer 3 passport из owner-approved Layer 2;
- сделать content update;
- сменить seller SKU;
- создать WB-карточку, если это согласовано и нужно;
- создать Ozon-карточку для WB-only owner-approved passports, если это
  согласовано и передан `--ozon-create-min-price`;
- загрузить media;
- сделать catalog-sync;
- выполнить targeted post-verify по новым internal SKU;
- сохранить общий отчет.

Если `--ozon-create-min-price` не передан, блокируется только стадия
Ozon-create; ready стадии по content/SKU/WB могут продолжить работу.

Ozon policy cleanup:

- для `FB_UNWANTED`/ошибочного импорта использовать
  `plan-ozon-product-remove` -> `apply-ozon-product-remove`;
- если карточка не создана или без SKU, команда выбирает `/v2/products/delete`;
- если карточка создана, команда выбирает `/v1/product/archive` и блокирует
  archive при наличии остатка.

После apply обязательно прислать владельцу краткий итог и файл отчета.

## Согласованные правила названий

Единое название для master passport, Ozon и WB на текущем этапе:

- целевой лимит: до 60 символов;
- структура: вид товара + способ крепления + тематика/структура + место
  крепления только если это реальное назначение товара;
- размер в название не добавлять по умолчанию, если он есть в фото,
  характеристиках и описании;
- для неформенных/тематических `nr` шевронов не использовать `на рукав` в
  названии и SEO-фокусе;
- `на рукав` допустимо для ведомственных, форменных и войсковых шевронов,
  где это реальное назначение;
- `на кепку` обязательно оставлять для карточек формата на кепку.

## Согласованные правила описания

Единое описание для master passport, Ozon и WB на текущем этапе:

- целевой лимит: до 2000 символов;
- три тематических блока:
  1. `Описание товара`;
  2. `Преимущества и характеристики товара`;
  3. `О производителе`;
- писать от лица производителя для покупателя;
- не писать `на фото видно`, `по инфографике`, `карточка показывает`,
  `snapshot/API`, `в одной товарной единице...`;
- не писать внутренние пояснения правил заполнения в customer-facing
  описании: `сначала указываем ширину, затем высоту`, `по правилу владельца`,
  `для SEO`, `в marketplace-поле` и аналогичные фразы;
- если смысл отдельного элемента изображения не подтвержден, не выдумывать
  трактовку: писать только подтвержденное по фото - форму, надписи, цвета,
  общий тип рисунка;
- не писать формулировки аудитора в customer-facing описании: `состав
  указан как`, `текущая карточка`, `рекомендую`, `по данным карточки` и
  аналогичные фразы. Вместо этого писать как производитель покупателю:
  `состав изделия - полиэстер и нейлон`, `шеврон изготовлен...`;
- не вставлять упаковку и вес в customer-facing описание;
- релевантные SEO-запросы из `seo_query_pack` должны естественно входить в
  описание, не только в title/hashtags.
- Ozon-хештеги/WB-теги выбирать только из релевантных запросов и
  приоритизировать по максимальной подтвержденной частотности; не добивать
  список низкорелевантными тегами ради количества.
- точные тематические хештеги, которые прямо описывают изображение, можно
  ставить первыми даже без подтвержденной частотности; после них идут
  релевантные подтвержденные частотные запросы по убыванию. Не ставить
  `#патч` первым для шеврона только из-за количества товаров в подсказке Ozon.
- для массовых паспортов комплектов позывных `мох` по owner-approved шаблону
  `Турист` использовать согласованную фразу `меньший шеврон дополнен
  тематическим изображением`; отдельное описание картинки на каждом малом
  шевроне в этой пачке не требуется.

## SEO и хештеги

SEO строить по согласованной логике:

- сначала общий слой спроса: `шеврон`, `шевроны`, `шеврон на липучке`,
  `шевроны на липучке`, `патч`, `нашивка`, `нашивки`, если релевантно;
- затем точный тематический запрос: `шеврон ФСБ`, `шеврон БПЛА`,
  `шеврон СВО`, `шеврон ФСИН`, `шеврон Росгвардия` и т.д.;
- не размазывать карточку нерелевантными запросами;
- учитывать, что на Ozon единственное/множественное число могут быть разными
  запросами;
- для неформенных `nr` не использовать `на рукав` в title/hashtags/WB tags;
- Ozon `Название цвета` должно идентифицировать вариант внутри группы, а не
  просто повторять общий цвет фона.

## Фото и дизайнерские задачи

Перед рекомендациями агент обязан посмотреть все фото карточки.

Для owner-review показывать:

- один коллаж текущих Ozon/WB фото;
- текстовый список, что на каждом фото;
- целевой комплект фото таблицей: номер, назначение, источник.

Для marketplace target обычно стремиться к 5 полезным фото:

1. главное фото;
2. размер;
3. липучка/комплектация или отсутствие липучки для нашивок;
4. варианты ношения;
5. сервисная инфографика.

По каждой карточке обязательно проверять и фиксировать:

- количество фото отдельно на Ozon и WB;
- качество главного фото и всех полезных слайдов;
- полноту комплекта: главное, размер, липучка/комплектность, варианты
  ношения, сервисная инфографика;
- можно ли перенести фото с Ozon на WB или с WB на Ozon;
- если перенести нельзя, почему именно: мало фото, сырое главное фото,
  низкая читаемость, нет нужных слайдов, нужен watermark для WB, фото не
  соответствует товару или нужна отдельная съемка.

Если карточка есть только на одной площадке, создание на второй площадке с
текущими фото допустимо только после полного визуального просмотра всех
слайдов. Если комплект фото неполный или слабый, сначала добавить/обновить
задачу дизайнеру, а перенос/создание второй площадки пометить как
заблокированный до фото-доработки.

Если фото не хватает, обновить:

```text
data/planning/product_card_designer_tasks.md
```

Для ведомственных/форменных товаров перед WB media apply нужно классифицировать
каждый целевой слайд:

- `symbol_bearing`: видна государственная или ведомственная символика; нужна
  ретушь либо водяной знак поверх символики;
- `neutral_no_symbol`: символики нет; водяной знак не требуется, перенос между
  площадками разрешен;
- `service_no_product`: нейтральная сервисная инфографика; водяной знак не
  требуется.

Если защищенной версии `symbol_bearing`-слайда нет, добавить задачу дизайнеру,
записать `media_apply_status=blocked_pending_watermarked_assets` и не выполнять
WB media update. Это ограничение не блокирует отдельно согласованные изменения
текста, SEO и характеристик.

## Материал, состав, липучка

Постоянные факты владельца:

- шевроны и петлицы идут с липучкой велкро: крючок пришит с обратной стороны,
  петля в комплекте;
- нашивки без липучки, пришиваются;
- правило владельца от 2026-07-08: в поле `Материал` для текущих шевронов и
  петлиц из габардина указывать только `Габардин`, без полиэстера и нейлона;
- состав для шевронов и петлиц с липучкой: `полиэстер, нейлон`, потому что
  габардин состоит из полиэстера, а липучка состоит из полиэстера и нейлона;
- комплектация для одного шеврона на липучке: `шеврон на липучке 1 шт.`;
- страна производства: Россия;
- ТН ВЭД: `5810999000` - прочие вышивки из прочих текстильных материалов.

## Размеры и упаковка

Согласованные размеры упаковки:

| Тип | Ozon упаковка | WB упаковка |
| --- | --- | --- |
| Нарукавный | `100*100*10 мм` | `10*10*1 см` |
| На кепку | `100*60*10 мм` | `10*6*1 см` |
| Нагрудный | `130*50*10 мм` | `13*5*1 см` |
| На спину | `300*100*10 мм` | `30*10*1 см` |
| Комплекты | максимальные длина/ширина среди изделий, толщина = количество физических изделий * 10 мм | аналогично в см |
| Петлицы | товар: неразрезанная пара `80*30*5 мм`; упаковка `100*40*10 мм` | пробовать `10*4*1 см`, fallback `10*5*1` или `10*6*1` |

## Артикулы

Правила новых внутренних артикулов:

```text
data/planning/seller_sku_rules.md
```

До marketplace apply внутренний артикул должен быть уже согласован в
Layer 2/Layer 3. Смена seller SKU на Ozon/WB - опасная операция и выполняется
только в owner-approved apply-пакете с verify.

## Следующий безопасный шаг

Если владелец возвращается к карточкам без нового приоритета:

1. Проверить статусы Layer 2/Layer 3 командой из раздела выше.
2. Не повторять уже примененные `0009-0014`.
3. Выбрать следующий приоритетный `not_submitted` или явно указанный
   владельцем SKU.
4. Сначала подготовить и отправить owner-review HTML, одну карточку за раз.
5. Копить согласованные карточки в пачку.
6. После 5 согласованных карточек применять batch-командой
   `apply-approved-cards`, если владелец сказал `применяй`.

## Recovery 2026-07-04: Пачка 0001-0005 `seo_priority_20260704`

Пачка из 5 карточек:

- `chev_back_fsb_text0001`;
- `chev_back_fsb_pict0001`;
- `chev_back_form_text0001`;
- `chev_back_form_text0002` / ГБР;
- `chev_back_mvd_pict0001`.

Итог:

- WB content verified `ok` по всем 5;
- Ozon content verified `ok` по всем 5;
- финальный read-only verify:
  `data/runs/2026-07-04/card_batch_0001_0005_final_readonly_verify_20260704T2055/summary.json`;
- Layer 3 паспорта помечены `owner_approved_applied_verified`.

Что пошло не так:

1. Первый batch применил content до seller SKU, затем seller SKU stage для WB
   перезаписал часть карточек старым payload. После смены seller SKU нужно
   делать финальный verify по новым internal SKU и при необходимости
   повторять content update по новым SKU.
2. `plan-card-content-update` с `ready_rows=5` - это не успешный verify, а
   наличие готового dry-run пакета. Нельзя сообщать владельцу, что карточки
   проверены, если был только plan.
3. Ozon `/v1/product/import/info` возвращал `imported` без ошибок, но
   `/v3/product/info/list` показывал `Не обновлен`. Реальная причина была
   видна только в ЛК Ozon import history:
   хештеги были переданы через запятые и с пробелами внутри фраз.
4. Ozon `#Хештеги` (`23171`) должны идти в API как
   `#тег #тег_с_подчеркиванием`, без запятых; один хештег с `#` не длиннее
   `30` символов. Длинные хештеги отфильтровывать.
5. Для Ozon перед полным `/v3/product/import` подтверждено полезно отправлять
   owner-approved attributes через `/v1/product/attributes/update`, включая
   `23536=false` и `4497` вес с упаковкой, и ждать `task_id`.

Проверенные run artifacts:

```text
data/runs/2026-07-04/ozon_import_history_probe_click_20260704T2028/
data/runs/2026-07-04/card_batch_0001_0005_content_recovery_apply6_20260704T2045/
data/runs/2026-07-04/card_batch_0001_0005_final_readonly_verify_20260704T2055/
```

Текущие явные следующие неподанные карточки в папке
`seo_priority_20260628`: `0015_chev_pz_ng_text0012`,
`0016_chev_pz_ng_text0018`. Но приоритет может быть изменен владельцем:
сейчас предпочтительны карточки с остатком, плохим SEO и слабой продажей; товары
без остатка и хорошо продающиеся карточки идут ниже.

## Текущая очередь `seo_priority_20260704`

После завершения пачки комплектов позывных `мох` следующая карточка выбрана не
из устаревших статусов `seo_priority_20260628`, а из текущего
`card_content_audit_backlog.csv`. Очередь идет по карточкам с остатком,
плохим/неполным SEO, видимостью в parser и возможностью получить эффект от
оптимизации.

### 0001-0005 `seo_priority_20260704` - применено и проверено

Корректировка 2026-07-05: владелец указал, что эта пачка уже была применена
2026-07-04. Повторная сверка подтвердила: Layer 3 master passports уже имели
`approval.status=owner_approved_applied_verified` и
`approval.marketplace_apply.status=applied_verified`, но Layer 2 `audit.json`
и старый текст checkpoint ошибочно оставались в `pending`. Статусы Layer 2
синхронизированы с Layer 3 и финальным verify.

Карточки:

| N | internal SKU | Ozon | WB | Telegram HTML | статус |
|---|---|---|---|---|---|
| 0001 | `chev_back_fsb_text0001` | `back0013` / `2158335091` / `2430054760` | `fsbback0001_back0013_222365` / `593399830` | message `82267` | `owner_approved_applied_verified` |
| 0002 | `chev_back_fsb_pict0001` | `back0014` / `2158364923` / `2430081567` | `fsbback0003_back0014_222031` / `593405742` | message `82348` | `owner_approved_applied_verified` |
| 0003 | `chev_back_form_text0001` | `back0002` / `2148711862` / `2422699878` | `nevnback0001_back0002_222621` / `589069492` | message `82400` | `owner_approved_applied_verified` |
| 0004 | `chev_back_form_text0002` | `back0001` / `2131612956` / `2409731430` | `nevnback0001_back0001_0101228255` / `589057650` | message `82438` | `owner_approved_applied_verified` |
| 0005 | `chev_back_mvd_pict0001` | `back0003` / `2149206752` / `2423061067` | `mvdback0001_back0003_222396` / `591206142` | message `82472` | `owner_approved_applied_verified` |

Фактические run artifacts:

```text
data/runs/2026-07-04/card_batch_0001_0005_apply_20260704T1907/
data/runs/2026-07-04/card_batch_0001_0005_content_recovery_apply6_20260704T2045/
data/runs/2026-07-04/card_batch_0001_0005_final_readonly_verify_20260704T2055/
```

Итоговый статус:

- `audit.json`: `owner_review.status=owner_approved_applied_verified`;
- `audit.json`: `marketplace_apply.status=applied_verified`;
- Layer 3 passport: `approval.status=owner_approved_applied_verified`;
- Layer 3 passport: `approval.marketplace_apply.status=applied_verified`;
- финальная проверка: `verified_at=2026-07-04T19:47:58+03:00`.

Правило на будущее: после каждого успешного `apply -> verify` по карточкам
обязательно сразу обновлять Layer 2, Layer 3 и этот checkpoint. Уже примененные
карточки не должны оставаться в `pending` и не должны повторно попадать в
batch apply без отдельной recovery-задачи, свежего drift-check и подтверждения
владельца.

### 0006 `nash_kit2_nr_mvd_pict0005`

```text
data/catalog/card_audits/seo_priority_20260704/0006_nash_kit2_nr_mvd_pict0005/
```

- internal SKU: `nash_kit2_nr_mvd_pict0005`;
- Ozon: `mvdkit2nr0013` / `3178621088` / `3196416631`;
- WB: `mvdkit2nr0013` / `684972453`;
- backlog rank: `4`;
- причина выбора: есть остаток, продаж нет/мало, parser-видимость слабая,
  Ozon/WB названия расходятся, Ozon 3 фото, WB 4 фото, Ozon hashtags и WB tags
  пустые.

Owner-review HTML:

```text
data/catalog/card_audits/seo_priority_20260704/0006_nash_kit2_nr_mvd_pict0005/nash_kit2_nr_mvd_pict0005_owner_review_fast.html
data/reports/card_reviews/2026-07-05/nash_kit2_nr_mvd_pict0005_owner_review_fast.html
```

Статус:

- владелец согласовал карточку с правками 2026-07-05;
- `audit.json` обновлен до
  `owner_review.status=owner_approved_pending_batch_apply`;
- HTML валидирован: mobile `390x844`, desktop `1366x1000`, битых изображений
  нет, горизонтального скролла нет, встроен 1 коллаж;
- HTML отправлен в правильный Telegram topic `-1003683440820/42336` через
  общий `telegram-ai-agent`, message `86289`;
- предыдущая отправка в личный чат бота `867144591`, summary message `184`,
  document message `185`, была ошибкой маршрута и не считается правильной
  отправкой на согласование;
- Layer 3 passport создан и проверен:
  `data/catalog/master_passport/approved/nash_kit2_nr_mvd_pict0005.json`;
- promotion run:
  `data/runs/2026-07-05/promote_passport_nash_kit2_nr_mvd_pict0005_20260705T1815_fix1/`;
- marketplace write не выполнялся.

В HTML явно показаны будущие опасные действия: изменение контента Ozon/WB,
seller SKU Ozon/WB на `nash_kit2_nr_mvd_pict0005`, хештеги/теги, упаковка/вес,
фото и поля карточек.

Owner-approved target:

- title: `Нашивки без липучки Полиция МВД ООП, комплект 2 шт.`;
- описание в 3 блоках от лица производителя, без ссылок на карточку/фото;
- цвет: `черный, серый`; белый не указывать;
- состав: `полиэстер`; для нашивок без липучки не указывать нейлон;
- Ozon/WB SEO: фокус на `комплект нашивок`, `нашивка`, `нашивки`,
  `нашивка без липучки`, `Полиция МВД ООП`, `МВД`, `патч`;
- Ozon/WB seller SKU -> `nash_kit2_nr_mvd_pict0005`;
- изделие `75*100 мм`; упаковка Ozon `100*100*20 мм`, `20 г`; WB
  `10*10*2 см`, `0.02 кг`;
- WB `Вид декора для одежды`: `нашивка`;
- комплектация: `нашивка без липучки пришивная 2 шт.`;
- фото: текущий approved набор 4 фото: Ozon 1 главное, Ozon 2 размер, WB 3
  варианты ношения/размещения, Ozon 3 или WB 4 сервисный слайд. Фото липучки
  не добавлять, потому что нашивка пришивная без липучки. Дизайнерская задача
  `VS-DESIGN-024` остается открытой на отдельную инфографику/фото
  `пришивная без липучки`.

### 0007 `chev_back_fsin_text0001`

```text
data/catalog/card_audits/seo_priority_20260704/0007_chev_back_fsin_text0001/
```

- internal SKU: `chev_back_fsin_text0001`;
- Ozon: `back0009` / `2152960046` / `2425924979`;
- WB: `fsinback0002_back0009_222616` / `593441430`;
- backlog rank in current content package: `28` in row-level package, current
  next logical candidate after 0006 in active owner-review flow;
- причина выбора: есть остаток, parser-видимость, рассинхрон Ozon/WB title,
  4 фото вместо целевых 5, WB tags пустые.

Owner-review HTML:

```text
data/catalog/card_audits/seo_priority_20260704/0007_chev_back_fsin_text0001/chev_back_fsin_text0001_owner_review_fast.html
data/reports/card_reviews/2026-07-05/chev_back_fsin_text0001_owner_review_fast.html
```

Статус:

- владелец согласовал карточку 2026-07-05, отдельно подтвердил
  `Название модели` для группировки Ozon: `ФСИН`;
- `audit.json` обновлен до
  `owner_review.status=owner_approved_pending_batch_apply`;
- HTML валидирован: mobile `390x844`, desktop `1366x1000`, битых изображений
  нет, горизонтального скролла нет, встроен 1 коллаж;
- HTML отправлен в правильный Telegram topic `-1003683440820/42336` через
  общий `telegram-ai-agent`, message `86471`;
- Layer 3 passport создан и проверен:
  `data/catalog/master_passport/approved/chev_back_fsin_text0001.json`;
- promotion run:
  `data/runs/2026-07-05/promote_passport_chev_back_fsin_text0001_20260705T1856/`;
- marketplace write не выполнялся.

Ключевые рекомендации, ожидающие согласования владельца:

- title: `Шеврон на липучке ФСИН России на спину, черно-желтый`;
- описание в 3 блоках от лица производителя;
- цвет: `черный, желтый`;
- состав: `полиэстер, нейлон`;
- Ozon/WB SEO: фокус на `шеврон ФСИН`, `шеврон на липучке ФСИН`,
  `шеврон на спину`, `шеврон`, `шевроны`;
- Ozon/WB seller SKU -> `chev_back_fsin_text0001`;
- изделие `275*85 мм`; упаковка Ozon `300*100*10 мм`, `30 г`; WB
  `30*10*1 см`, `0.03 кг`;
- фото: текущий target 4 существующих фото; дизайнерская задача
  `VS-DESIGN-026` остается открытой на 5-е фото с вариантом ношения на спине.

### 0008 `chev_kit2_nr_rg_pict0002`

```text
data/catalog/card_audits/seo_priority_20260704/0008_chev_kit2_nr_rg_pict0002/
```

- internal SKU: `chev_kit2_nr_rg_pict0002`;
- Ozon: `rosgkit20005` / `3210648133` / `3220787927`;
- WB: `rosgkit20005` / `707833048`;
- причина выбора: есть остаток и продажи, parser-видимость, рассинхрон
  Ozon/WB title, Ozon hashtags пустые, WB tags пустые, WB имеет 4 фото против
  5 на Ozon.

Owner-review HTML:

```text
data/catalog/card_audits/seo_priority_20260704/0008_chev_kit2_nr_rg_pict0002/chev_kit2_nr_rg_pict0002_owner_review_fast.html
data/reports/card_reviews/2026-07-05/chev_kit2_nr_rg_pict0002_owner_review_fast.html
```

Статус:

- HTML валидирован: mobile `390x844`, desktop `1366x1000`, битых изображений
  нет, горизонтального скролла нет, встроен 1 коллаж;
- HTML отправлен в правильный Telegram topic `-1003683440820/42336` через
  общий `telegram-ai-agent`, message `86535`;
- владелец согласовал карточку с правками 2026-07-05;
- `audit.json` обновлен до
  `owner_review.status=owner_approved_pending_batch_apply`;
- Layer 3 passport создан и проверен:
  `data/catalog/master_passport/approved/chev_kit2_nr_rg_pict0002.json`;
- promotion run:
  `data/runs/2026-07-05/promote_passport_chev_kit2_nr_rg_pict0002_20260705T_ownerfix/`;
- marketplace write не выполнялся.

Owner-approved target:

- title: `Шевроны на липучке Росгвардия ОН ЦО, комплект 2 шт., красный`;
- описание в 3 блоках от лица производителя; в описании раскрыть `ОН ЦО` как
  `Оперативного назначения Центрального округа`;
- цвет: `красный, белый, желтый`;
- название цвета: `Комплект ОН ЦО, красный`;
- Ozon model/grouping: `Росгвардия`;
- состав: `полиэстер, нейлон`;
- Ozon/WB SEO: фокус на `комплект шевронов Росгвардия`,
  `шеврон на липучке Росгвардия`, `шеврон`, `шевроны`, `шеврон на рукав`
  как форменный/ведомственный placement-запрос;
- Ozon/WB seller SKU -> `chev_kit2_nr_rg_pict0002`;
- изделие `75*100 мм, 2 шт.`; упаковка Ozon `100*100*20 мм`, `20 г`; WB
  `10*10*2 см`, `0.02 кг`;
- фото: Ozon оставить текущие 5 фото; на WB добавить Ozon 4 как слайд
  вариантов ношения и оставить текущий сервисный WB-слайд пятым.

### 0009 `chev_kit2_nr_rg_pict0005`

```text
data/catalog/card_audits/seo_priority_20260704/0009_chev_kit2_nr_rg_pict0005/
```

- internal SKU: `chev_kit2_nr_rg_pict0005`;
- Ozon: `rosgkit20010` / `3216702016` / `3225421220`;
- WB: `rosgkit20010` / `707892598`;
- причина выбора: есть остаток, слабая parser-видимость, Ozon/WB title
  расходятся, Ozon hashtags пустые, WB tags пустые, WB имеет 4 фото против
  5 на Ozon.

Owner-review HTML:

```text
data/catalog/card_audits/seo_priority_20260704/0009_chev_kit2_nr_rg_pict0005/chev_kit2_nr_rg_pict0005_owner_review_fast.html
data/reports/card_reviews/2026-07-05/chev_kit2_nr_rg_pict0005_owner_review_fast.html
```

Статус:

- HTML валидирован: mobile `390x844`, desktop `1366x1000`, битых изображений
  нет, горизонтального скролла нет, встроен 1 коллаж;
- HTML отправлен в правильный Telegram topic `-1003683440820/42336` через
  общий `telegram-ai-agent`, message `86577`;
- владелец согласовал карточку с правками 2026-07-05;
- `audit.json` обновлен до
  `owner_review.status=owner_approved_pending_batch_apply`;
- Layer 3 passport создан и проверен:
  `data/catalog/master_passport/approved/chev_kit2_nr_rg_pict0005.json`;
- promotion run:
  `data/runs/2026-07-05/promote_passport_chev_kit2_nr_rg_pict0005_20260705T_ownerfix/`;
- marketplace write не выполнялся.

Owner-approved target:

- title: `Шевроны на липучке Росгвардия ОН ЦО, комплект 2 шт., синий`;
- описание в 3 блоках от лица производителя; в описании раскрыть `ОН ЦО` как
  `Оперативного назначения Центрального округа`, фон описать как камуфляж
  `синяя цифра`, белый цвет не указывать;
- цвет: `синий, серый`;
- название цвета: `Комплект ОН ЦО, синий`;
- Ozon model/grouping: `Росгвардия`;
- состав: `полиэстер, нейлон`;
- Ozon/WB SEO: фокус на `комплект шевронов Росгвардия`,
  `шеврон на липучке Росгвардия`, `шеврон`, `шевроны`, `шеврон на рукав`
  как форменный/ведомственный placement-запрос;
- Ozon/WB seller SKU -> `chev_kit2_nr_rg_pict0005`;
- изделие `75*100 мм, 2 шт.`; упаковка Ozon `100*100*20 мм`, `20 г`; WB
  `10*10*2 см`, `0.02 кг`;
- фото: Ozon оставить текущие 5 фото; на WB добавить Ozon 4 как слайд
  вариантов ношения и оставить текущий сервисный WB-слайд пятым.

### 0010 `chev_kp_bpla_pict0003`

```text
data/catalog/card_audits/seo_priority_20260704/0010_chev_kp_bpla_pict0003/
```

- internal SKU: `chev_kp_bpla_pict0003`;
- Ozon: `pict0071` / `2246301616` / `2498309342`;
- WB: `bplapict0012_pict0071_223217` / `593270984`;
- причина выбора: есть продажи, остаток и parser-видимость; Ozon/WB title
  расходятся, WB tags пустые, Ozon hashtags требуют доработки без запрещенного
  слова `БПЛА`.

Owner-review HTML:

```text
data/catalog/card_audits/seo_priority_20260704/0010_chev_kp_bpla_pict0003/chev_kp_bpla_pict0003_owner_review_fast.html
data/reports/card_reviews/2026-07-05/chev_kp_bpla_pict0003_owner_review_fast.html
```

Статус:

- HTML валидирован: mobile `390x844`, desktop `1366x1000`, битых изображений
  нет, горизонтального скролла нет, встроен 1 коллаж;
- HTML отправлен в правильный Telegram topic `-1003683440820/42336` через
  общий `telegram-ai-agent`, message `86605`;
- `audit.json` обновлен до
  `owner_review.status=submitted_for_owner_review`;
- Layer 3 passport еще не создан, marketplace write не выполнялся.

Ключевые рекомендации, ожидающие решения владельца:

- title: `Шеврон на липучке БПЛА Дрон в прицеле на кепку`;
- описание в 3 блоках от лица производителя;
- Ozon hashtags: без `БПЛА`, потому что ранее зафиксировано, что Ozon не
  пропускает это слово в хештегах;
- WB tags: с `БПЛА`;
- цвет: `черный, красный, белый`;
- название цвета: `Дрон в прицеле, черный`;
- Ozon model/grouping: `БПЛА`;
- состав: `полиэстер, нейлон`;
- Ozon/WB seller SKU -> `chev_kp_bpla_pict0003`;
- изделие `80*50 мм`; упаковка Ozon `100*60*10 мм`, `10 г`; WB
  `10*6*1 см`, `0.01 кг`;
- фото: Ozon и WB уже имеют полный набор из 5 фото, переносы не нужны.

## Что нельзя делать

### 2026-07-16 — `chev_kit2_nr_rg_pict0006`

- исправленный HTML окончательно согласован владельцем;
- Layer 2: `owner_approved_pending_batch_apply`;
- Layer 3:
  `data/catalog/master_passport/approved/chev_kit2_nr_rg_pict0006.json`;
- promotion run:
  `data/runs/2026-07-16/promote_approved_card_passport_chev_kit2_nr_rg_pict0006_20260716T1857_fix_wb_identity/`;
- Ozon фото 1-6: `keep/do_not_touch`;
- WB фотоплан: сохранить текущие фото и добавить Ozon 5 как слайд вариантов
  ношения;
- marketplace write не выполнялся.

- Для Ozon JPEG в CMYK нельзя делать визуальные выводы по результату
  `Pillow.convert("RGB")` или простого ImageMagick-преобразования. Перед
  сборкой HTML исходный URL нужно отрендерить через Chromium и сохранить
  браузерный результат в sRGB.
- Искаженный CMYK-коллаж не является основанием менять фотографии Ozon.
  Если на странице Ozon фото отображаются корректно, использовать
  `keep/do_not_touch`.
- Не менять HTML-шаблон без просьбы владельца.
- Не отправлять HTML без встроенных изображений.
- Не писать `на рукав` в title/hashtags для неформенных тематических `nr`.
- Не делать полный refresh каталога перед каждой одной карточкой без причины.
- Не применять карточки по одной, если согласована пачка.
- Не делать marketplace write из Layer 1 или несогласованного Layer 2.
- Не считать `approval.status` в Layer 3 единственным источником apply-статуса.
- Не откатывать чужие изменения в рабочем дереве.

## Проверка WB-фотопланов перед следующим apply 2026-07-16

Проверены все `7` Layer 3 паспортов со статусом
`owner_approved_pending_batch_apply` и `marketplace_apply.status=not_applied`.

- `chev_kit2_nr_mvd_pict0004`: WB-слайды с символикой защищены; нейтральные
  варианты ношения и сервисный слайд допустимы без водяного знака;
- `chev_kit2_nr_mvd_pict0005`: WB 1-3 защищены, WB 4-5 нейтральны;
- `chev_kit2_nr_mvd_pict0006`: WB 1-3 защищены, WB 4-5 нейтральны;
- `chev_kit2_nr_rg_pict0003`: WB 1, 3, 4 защищены, WB 2 и 5 нейтральны;
- `chev_kit2_nr_rg_pict0006`: Ozon 5 с вариантами ношения не содержит
  символики и остается в целевом WB-фотоплане; текущие WB-слайды 1, 3 и 4
  содержат незащищенную символику, поэтому только WB media update имеет статус
  `blocked_pending_watermarked_assets`, задача дизайнеру `VS-DESIGN-035`;
- `chev_kp_prikol_text0014` и `chev_kp_prikol_text0015`: правило не применимо.

В Layer 2 и Layer 3 записан `media.wb_departmental_symbol_policy`. Пакетный
preflight теперь отдельно выводит `wb_media_policy` и
`wb_media_blocked_skus`, не блокируя текстовые/SEO/атрибутные изменения.

## Согласование `chev_kit2_nr_rg_pict0007` 2026-07-16

Владелец согласовал карточку с двумя исправлениями:

- текущие фото Ozon и WB оставить без изменений; в WB добавить нейтральный
  Ozon 5 с вариантами ношения без символики;
- в описании заменить ошибочное `скрещенные клинки` на
  `скрещенные булавы`.

Layer 2 закрыт со статусом `owner_approved_pending_batch_apply`. Создан Layer 3
passport `data/catalog/master_passport/approved/chev_kit2_nr_rg_pict0007.json`;
`marketplace_apply.status=not_applied`. Целевой WB-фотоплан:
`WB 1-4 -> Ozon 5 -> WB 5`, media policy `allowed_verified`.

Promotion дополнительно исправлен для fresh-контракта: placeholder
`same_as_canonical_description` теперь раскрывается в фактическое canonical
description, а `physical.item_weight_g_each` сохраняется как вес одного изделия.
Финальный run:
`data/runs/2026-07-16/promote_approved_card_passport_chev_kit2_nr_rg_pict0007_20260716T1930_final/`.

## Согласование `chev_kit2_nr_rg_pict0011` 2026-07-16

Владелец согласовал карточку для создания паспорта без применения на
маркетплейсы:

- размер каждого изделия подтвержден как `75*100 мм`;
- на Ozon добавить только текущий WB 4 с вариантами ношения без ведомственной
  символики;
- остальные Ozon 1-6 и WB 1-5 оставить без изменения;
- текущие WB 1-3 пока остаются с серой ретушью;
- дизайнеру создана задача `VS-DESIGN-036`: заменить серые маски на защитные
  водяные знаки в виде нашего логотипа и исправить WB-размерный слайд с
  `80*100 мм` на `75*100 мм`;
- готовые дизайнерские WB-фото нельзя применять без отдельного согласования.

Layer 2:
`data/catalog/card_audits/fresh_quad_stale_layer1_20260716/0343_chev_kit2_nr_rg_pict0011/audit.json`.

Layer 3:
`data/catalog/master_passport/approved/chev_kit2_nr_rg_pict0011.json`.

Статус: `owner_approved_pending_batch_apply`,
`marketplace_apply.status=not_applied`.

Promotion run:
`data/runs/2026-07-16/promote_approved_card_passport_chev_kit2_nr_rg_pict0011_20260716T2220_final/`.

## Согласование `chev_kp_prikol_text0002` 2026-07-16

Владелец полностью согласовал аудит карточки:

- название: `Шеврон прикол на липучке Мозги не делайте, на кепку`;
- единое описание из трех тематических блоков;
- цвета `оливковый, черный`;
- название цвета `Мозги мне с самого утра не делайте, оливковый`;
- размер изделия `80*50 мм`, вес `10 г`;
- упаковка Ozon `100*60*10 мм`, WB `10*6*1 см`;
- целевая модель `Шевроны прикол на кепку, текст`;
- группировка остается отдельной ручной опасной операцией после проверки
  состава текущих групп;
- Ozon 1-5 оставить; для WB подготовить адаптированный Ozon 4 как пятый
  слайд между WB 3 и WB 4.

Создана задача дизайнеру `VS-DESIGN-037`. До готовности адаптированного файла
WB media apply не выполнять.

Layer 2:
`data/catalog/card_audits/fresh_quad_stale_layer1_20260716/0344_chev_kp_prikol_text0002/audit.json`.

Layer 3:
`data/catalog/master_passport/approved/chev_kp_prikol_text0002.json`.

Статус: `owner_approved_pending_batch_apply`,
`marketplace_apply.status=not_applied`.

Promotion run:
`data/runs/2026-07-16/promote_approved_card_passport_chev_kp_prikol_text0002_20260716T2223_final/`.

## Apply 10 согласованных карточек 2026-07-16

Применены и финально проверены:

- `chev_kit2_nr_mvd_pict0004`;
- `chev_kit2_nr_mvd_pict0005`;
- `chev_kit2_nr_mvd_pict0006`;
- `chev_kit2_nr_rg_pict0003`;
- `chev_kit2_nr_rg_pict0006`;
- `chev_kit2_nr_rg_pict0007`;
- `chev_kit2_nr_rg_pict0011`;
- `chev_kp_prikol_text0002`;
- `chev_kp_prikol_text0014`;
- `chev_kp_prikol_text0015`.

Результат:

- seller SKU: Ozon `8/8`, WB `10/10`;
- контент: Ozon `9/9`, WB `10/10`, final verify `ok`;
- `chev_kp_prikol_text0014` создана на Ozon:
  `product_id=5527535955`, `price=550`, `old_price=1100`,
  `min_price=400`;
- `chev_kp_prikol_text0015` осталась WB-only, создание Ozon не выполнялось;
- WB media для `chev_kit2_nr_rg_pict0006` не отправлялись из-за
  `blocked_pending_watermarked_assets`; текущие WB-фото сохранены;
- Layer 2 и Layer 3 закрыты для `10/10` со статусом
  `owner_approved_applied_verified`;
- итоговый snapshot:
  `data/catalog/card_status/latest.json`.

Основные runs:

- approved dry-run:
  `data/runs/2026-07-16/plan_10_approved_cards_final_20260716T2233/`;
- основной apply:
  `data/runs/2026-07-16/apply_10_approved_cards_20260716T2234/`;
- recovery content apply:
  `data/runs/2026-07-16/recovery_5_cards_apply_nomedia_20260716T2243/`;
- финальный verify:
  `data/runs/2026-07-16/verify_10_approved_cards_final_20260716T2244/`.

Подтвержденные recovery-правила:

- WB/Ozon dictionary-поля цвета используют технические значения
  `черный`, `желтый`; owner-facing текст можно оставлять с `ё`;
- WB отклоняет всю связанную группу карточек, если одна строка содержит
  недопустимое dictionary-значение;
- WB CDN URL старой позиции фото нельзя повторно использовать после
  перестановки слайдов: позиционные URL меняются и могут вернуть `409`;
- Ozon-create в batch разрешен только при явном
  `safety.dangerous_actions=ozon_card_create`;
- post-verify не должен считать
  `ozon_offer_id_after_seller_sku_update` доказательством существования
  Ozon-карточки.
