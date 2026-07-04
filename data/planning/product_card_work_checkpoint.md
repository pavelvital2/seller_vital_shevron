# Product Card Work Checkpoint

Дата чекпойнта: 2026-06-30

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
feature/card-content-apply-optimization
```

В рабочем дереве есть незакоммиченные изменения по нескольким направлениям:
карточки, поставки, отзывы/уведомления, акции и новые helper-команды. Нельзя
откатывать эти изменения без отдельного указания владельца. Перед коммитом
нужна отдельная ревизия `git diff`, потому что часть изменений сделана в ходе
других задач.

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

## Текущие статусы Layer 2

По папке `data/catalog/card_audits/seo_priority_20260628` на момент
чекпойнта:

| N | internal SKU | Статус |
| --- | --- | --- |
| 0001 | `chev_kp_chvk_pict0001` | `owner_approved`, ранее согласован; перед apply проверить, нужен ли в текущей пачке |
| 0002 | `chev_nr_bpla_pict0023` | `owner_approved`, ранее согласован; перед apply проверить, нужен ли в текущей пачке |
| 0003 | `chev_nr_bpla_pict0026` | `owner_approved`, ранее согласован; перед apply проверить, нужен ли в текущей пачке |
| 0004 | `chev_nr_bpla_pict0028` | `owner_approved_applied_verified` |
| 0005 | `chev_nr_prikol_pict0009` | `owner_approved_with_correction_pending_apply` |
| 0006 | `chev_nr_voisk_pict0012` | `owner_approved_with_correction_pending_apply` |
| 0007 | `chev_back_form_text0003` | `owner_approved_pending_batch_apply`; по post-state в паспорте отмечен `applied_verified`, перед повторной подачей проверить |
| 0008 | `chev_nr_oborg_pict0003` | `owner_approved_pending_batch_apply`; по post-state в паспорте отмечен `applied_verified`, перед повторной подачей проверить |
| 0009-0014 | см. выше | применены и проверены |
| 0015 | `chev_pz_ng_text0012` | `not_submitted`; следующий кандидат на подготовку HTML, если приоритет не изменен |
| 0016 | `chev_pz_ng_text0018` | `not_submitted`; следующий кандидат после 0015 |

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
- три визуальных блока:
  1. описание самого товара и изображения;
  2. материал, качество, вышивка, крепление, использование;
  3. короткий блок о производителе/магазине Vital Shevron;
- писать от лица производителя для покупателя;
- не писать `на фото видно`, `по инфографике`, `карточка показывает`,
  `snapshot/API`, `в одной товарной единице...`;
- не писать формулировки аудитора в customer-facing описании: `состав
  указан как`, `текущая карточка`, `рекомендую`, `по данным карточки` и
  аналогичные фразы. Вместо этого писать как производитель покупателю:
  `состав изделия - полиэстер и нейлон`, `шеврон изготовлен...`;
- не вставлять упаковку и вес в customer-facing описание;
- релевантные SEO-запросы из `seo_query_pack` должны естественно входить в
  описание, не только в title/hashtags.

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
- материал: габардин, лицевая часть с вышивкой полиэстеровыми нитями,
  обратная сторона с липучкой для шевронов/петлиц;
- состав: полиэстер и нейлон;
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

Текущие явные следующие неподанные карточки в папке
`seo_priority_20260628`: `0015_chev_pz_ng_text0012`,
`0016_chev_pz_ng_text0018`. Но приоритет может быть изменен владельцем:
сейчас предпочтительны карточки с остатком, плохим SEO и слабой продажей; товары
без остатка и хорошо продающиеся карточки идут ниже.

## Что нельзя делать

- Не менять HTML-шаблон без просьбы владельца.
- Не отправлять HTML без встроенных изображений.
- Не писать `на рукав` в title/hashtags для неформенных тематических `nr`.
- Не делать полный refresh каталога перед каждой одной карточкой без причины.
- Не применять карточки по одной, если согласована пачка.
- Не делать marketplace write из Layer 1 или несогласованного Layer 2.
- Не считать `approval.status` в Layer 3 единственным источником apply-статуса.
- Не откатывать чужие изменения в рабочем дереве.
