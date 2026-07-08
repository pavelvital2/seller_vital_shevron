# Product Card Work Checkpoint

Дата чекпойнта: 2026-07-08

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
feature/runtime-job-store
```

В рабочем дереве могут быть незакоммиченные изменения по карточкам,
инструкциям, run artifacts и локальным catalog-layer файлам. Нельзя откатывать
эти изменения без отдельного указания владельца. Перед коммитом нужна
отдельная ревизия `git diff` и проверка `git status --ignored`, потому что
часть карточных паспортов и run artifacts может не попадать в обычный
tracked-status.

## Короткий текущий статус 2026-07-08

Это актуальный верхний checkpoint. Исторические разделы ниже оставлены как
контекст по уже выполненным пачкам.

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
   - `nash_back_mvd_text0002`:
     `data/catalog/card_audits/fresh_triples_20260708/0212_nash_back_mvd_text0002/audit.json`,
     `data/catalog/card_audits/fresh_triples_20260708/0212_nash_back_mvd_text0002/nash_back_mvd_text0002.html`;
   - `nash_back_mvd_text0001`:
     `data/catalog/card_audits/fresh_triples_20260708/0211_nash_back_mvd_text0001/audit.json`,
     `data/catalog/card_audits/fresh_triples_20260708/0211_nash_back_mvd_text0001/nash_back_mvd_text0001.html`.
   Проверка fresh-аудиторов: JSON валиден, layout validation passed. Для
   `nash_back_mvd_text0001` HTML использует sidecar
   `images/contact_sheet.jpg`; коллаж был отправлен владельцу отдельным
   изображением. Следующее действие - ждать правки/согласование владельца по
   первому HTML, параллельно владелец проверяет второй и третий.
7. Продолжение должно идти от ответа владельца по конкретному HTML:
   `согласовано` -> внести owner corrections в Layer 2 и создать Layer 3
   passport; `применяй` -> только после Layer 3 через безопасный
   dry-run/plan/apply/verify. Marketplace write только после точного owner
   approval `применяй` по показанному пакету.

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

Для ведомственных/форменных шевронов с символикой на WB перед media apply
нужны версии с водяным знаком/логотипом VitalEmb на защищенной символике. Если
таких фото нет, добавить задачу дизайнеру и не придумывать, что фото готово.

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

- Не менять HTML-шаблон без просьбы владельца.
- Не отправлять HTML без встроенных изображений.
- Не писать `на рукав` в title/hashtags для неформенных тематических `nr`.
- Не делать полный refresh каталога перед каждой одной карточкой без причины.
- Не применять карточки по одной, если согласована пачка.
- Не делать marketplace write из Layer 1 или несогласованного Layer 2.
- Не считать `approval.status` в Layer 3 единственным источником apply-статуса.
- Не откатывать чужие изменения в рабочем дереве.
