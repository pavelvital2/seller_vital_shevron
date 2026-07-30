# Card Operations Quick Access

Дата: 2026-06-28

## Назначение

Быстрый вход для агентов, которые применяют owner-approved карточные изменения
Vital Shevron. Этот файл не заменяет `AGENTS.md` и safety-цепочку, а дает
короткий маршрут: какую инструкцию открыть и какую команду запускать.

Перед любой write-операцией обязательно:

```text
read-only -> dry-run -> review -> approved -> apply -> verify -> result
```

Перед продолжением карточной работы после переключения на другие задачи
обязательно открыть текущий checkpoint:

```text
data/planning/product_card_work_checkpoint.md
```

Owner-approved HTML по карточке является review-пакетом только для действий,
которые в нем явно показаны или дополнительно подтверждены владельцем.

## Быстрые инструкции

| Операция | Инструкция | Статус команды |
| --- | --- | --- |
| Создание карточки на WB из owner-approved HTML / Layer 3 passport | `data/planning/card_ops/01_wb_card_create_from_html.md` | частично автоматизировано: `plan-wb-card-create`, `apply-wb-card-create`; есть ограничение legacy plan |
| Смена артикулов продавца Ozon/WB на внутренний артикул | `data/planning/card_ops/02_seller_sku_update_ozon_wb.md` | автоматизировано: `plan-seller-sku-update`, `apply-seller-sku-update` |
| Изменение параметров существующих карточек Ozon/WB | `data/planning/card_ops/03_card_content_update_ozon_wb.md` | быстрый путь: `apply-approved-card`; batch-путь: `plan-approved-cards`, затем `apply-approved-cards --plan-run-id`; verify-only: `verify-card-content-update`; ручной debug-путь: `plan-card-content-update`, `apply-card-content-update` |
| Изоляция одного отклоняемого Ozon-хештега | `data/planning/ozon_product_card_content_runbook.md` | `scripts/cards/ozon_hashtag_probe.py`; один кандидат, одна контрольная карточка, только атрибут `23171`, checksummed plan/apply и delayed verify |
| Точечный recovery top-level габаритов/веса Ozon после принятого, но не применившегося API import | `data/planning/ozon_product_card_content_runbook.md` | `scripts/cards/ozon_lk_dimensions_apply.js`; только после read-only baseline и перехваченного dry-run, с `--confirmed-by-user`, exact target guard и delayed verify |
| Promotion owner-approved Layer 2 audit/HTML в Layer 3 passport | этот файл | автоматизировано: `promote-approved-card-passport`; также встроено как preflight в `apply-approved-cards` |
| Создание карточки на Ozon из owner-approved HTML / Layer 3 passport | `data/planning/card_ops/04_ozon_card_create_later.md` | автоматизировано: `plan-ozon-card-create`, `apply-ozon-card-create`; встроено в `apply-approved-cards` только при явном `safety.dangerous_actions=ozon_card_create` и `--ozon-create-min-price`; WB price fallback требует manual review |
| Удаление не созданной Ozon-карточки без SKU или архивирование созданной карточки | `data/planning/card_ops/05_ozon_product_remove.md` | автоматизировано: `plan-ozon-product-remove`, `apply-ozon-product-remove` |

## Общие read-only команды перед карточными write-операциями

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli status-preflight
```

Полный refresh каталога не является обязательным шагом для каждой карточки.
Для owner-approved apply по конкретному SKU штатные `plan-*` команды сами
должны читать текущую карточку точечно через API.

Полный refresh запускать только отдельной задачей: перед массовым аудитом,
перед большой пачкой изменений, после серии apply или при найденном рассинхроне.

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli fetch-card-content
```

Точечный refresh по SKU запускать только с merge-режимом, чтобы не
перезаписать общий content index частичной выборкой:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli fetch-card-content \
  --internal-sku <internal_sku> \
  --merge-existing
```

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli build-content-master
```

## Штатные команды смены артикулов продавца

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli plan-seller-sku-update \
  --internal-sku <internal_sku>
```

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli apply-seller-sku-update \
  --plan-run-id <plan_run_id> \
  --confirmed-by-user
```

## Быстрый путь изменения существующих карточек

Для owner-approved HTML/passport по одной или нескольким карточкам:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli apply-approved-card \
  --internal-sku <internal_sku> \
  --confirmed-by-user
```

Команда сама делает точечный `plan -> apply -> targeted verify -> result`.
Не запускать перед ней или после нее полный refresh каталога, если нет
отдельной причины.

Если нужно только проверить уже примененную карточку или пачку без нового
dry-run/apply, использовать verify-only маршрут:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli verify-card-content-update \
  --internal-sku <internal_sku>
```

`verify-card-content-update` читает Layer 3 passport, проверяет фактическое
состояние Ozon/WB по текущим `offer_id`/`vendorCode` и ничего не меняет в
магазинах. Не заменять эту проверку командой `plan-card-content-update`:
`plan-*` показывает новый dry-run и не является доказательством результата
после apply.

## Batch-путь после согласования пачки карточек

Если перед apply нужно вручную проверить или восстановить Layer 3 passport из
уже согласованного audit/HTML:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli promote-approved-card-passport \
  --internal-sku <internal_sku>
```

Без `--write` команда делает dry-run и пишет план. Для записи:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli promote-approved-card-passport \
  --internal-sku <internal_sku> \
  --write
```

Команда пишет passport только из `owner_approved*` Layer 2 `audit.json` и не
перезаписывает существующий Layer 3 без `--overwrite`.

Если владелец согласовал сразу несколько HTML/passport карточек и сказал
`применяй`, сначала собрать единый dry-run package. Он фиксирует checksum
Layer 3 passport по каждому SKU и stage run ids для seller SKU/content/create:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli plan-approved-cards \
  --internal-sku <sku_1> \
  --internal-sku <sku_2> \
  --internal-sku <sku_3> \
  --ozon-create-min-price <min_price_if_needed>
```

После проверки package применять только по `plan_run_id`. Apply перед write
сверяет checksum плана и текущих Layer 3 passport; если паспорт изменился
после плана, операция блокируется до нового `plan-approved-cards`.

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli apply-approved-cards \
  --plan-run-id <plan_run_id> \
  --confirmed-by-user
```

Что делает batch-команда:

- на dry-run проверяет наличие Layer 3 passport по каждому SKU и автоматически
  восстанавливает отсутствующие паспорта из owner-approved Layer 2 audit/HTML;
- строит и применяет seller SKU replacement пачкой;
- после успешной смены seller SKU строит единый targeted content plan уже по
  финальным internal SKU;
- применяет Ozon/WB content update пачкой уже по финальным `offer_id` /
  `vendorCode`;
- строит WB create plan из owner-approved Layer 3 passport только по
  указанным SKU;
- создает недостающие WB-карточки, загружает фото и проверяет результат;
- создает недостающие Ozon-карточки из WB-only owner-approved passports,
  если передан `--ozon-create-min-price`; без минимальной цены стадия
  блокируется отдельно и не мешает готовым WB/content/SKU стадиям;
- в конце делает обязательный финальный catalog-sync по старым и новым
  идентификаторам: старый Ozon `offer_id`, старый WB `vendorCode`, новый
  internal SKU, `product_id` и `nmID`;
- обновляет локальные `products`, `content_master`, `processed/master_catalog`
  и approved passports, удаляет склеенные WB-only дубли и сохраняет WB barcode;
- запускает нормализованный post-verify по финальным internal SKU после
  content/SKU/create/catalog-sync;
- после post-verify вызывает `card_status_sync` для каждой карточки, у которой
  все требуемые Ozon/WB строки подтверждены `ok`: закрывает её Layer 2 audit,
  Layer 3 passport, пишет `data/catalog/card_status/latest.json` и переводит
  её `card_work_items` в `closed`; warning одной карточки не оставляет
  остальные подтвержденные строки pending;
- пишет один общий run/report для пачки.

Подтверждено 2026-07-12 на batch apply `apply_10_approved_cards_20260712T0824`:
локальный catalog-sync не является финальной проверкой и не должен переводить
паспорт в `applied_verified`. До `card_status_sync` допустимые промежуточные
состояния - `content_applied` или stage-specific pending. Если `wb_card_create`
заблокирован, Ozon-only SKU нельзя закрывать как `applied_verified`, даже если
Ozon content verify уже `ok`: карточка остается pending до отдельного WB create
review/apply или до owner decision не создавать WB-карточку.

Если одна карточка или отдельная стадия блокируется на dry-run, batch должен
продолжить по ready карточкам и вынести blocked строки в общий отчет. Для
больших пачек начинать с 5 SKU; после стабильной серии можно переходить к
10 SKU.

Подтверждено 2026-07-13 на хвосте `chev_back_rg_text0003`: если
`apply-wb-card-create` запускается отдельно, вне `apply-approved-cards`, после
успешного WB create нужно вручную синхронизировать локальные слои тем же
контуром, который использует batch: обновить `products.*`, `content_master.*`,
`processed/master_catalog.*`, Layer 3 passport, затем запустить
`verify-card-content-update` и только после `overall_status=ok` закрывать
Layer 2/Layer 3 через `card_status_sync`. Без этого созданная WB-карточка
останется в маркетплейсе, но локальный паспорт может продолжать выглядеть как
`ozon_only`.

Подтверждено 2026-07-22 на пачке позиций `9-19`: строка
`assigned_by_marketplace_after_create` в Layer 3 является только placeholder,
а не реальным WB `nmID`. До ответа официального WB API с числовым `nmID`
карточку считать `ozon_only`; нельзя переводить catalog layers в `ozon_wb` по
placeholder. Текущий `plan-wb-card-create` получает изображения только из
URL-полей `media.target_assets`; с 2026-07-25 при пустом `target_assets`
используется упорядоченный fallback из URL-полей
`media.target_wb_photo_set`, кроме явных `keep_current*`/`do_not_touch`.
Официальный WB API также поддерживает
прямую загрузку локального файла через `POST /content/v3/media/file` с
заголовками `X-Nm-Id` и `X-Photo-Number`. Для owner-approved watermarked-файлов
из `target_wb_photo_set[].local_path` нужно сохранить exact media plan с
позицией и SHA-256, создать карточку, получить реальный числовой `nmID`, затем
загрузить каждый файл multipart-запросом в согласованную позицию. Запрещено
подменять защищенные файлы исходными Ozon URL. После загрузки проверить ответы
каждой позиции, точное количество/порядок фото через Cards API и визуально
проверить WB CDN-версии. Затем использовать штатную локальную синхронизацию из
`_update_wb_create_local_layers`, итоговый verify и только после этого закрывать
Layer 2/Layer 3. Подтвержденный run:
`apply_wb_create_remaining3_owner_approved_20260722T1526`, загрузки `9/9`,
карточки `3/3`, фото `4/4`, `5/5`, `3/3`.

Подтверждено 2026-06-29 на пачке `0009-0014`: перед batch apply нужно
проверить, что для каждого owner-approved HTML/Layer 2 audit уже есть файл:

```text
data/catalog/master_passport/approved/<internal_sku>.json
```

Если HTML согласован, но Layer 3 passport отсутствует, marketplace write не
запускать неполной пачкой. Сначала восстановить passport из согласованного
`audit.json`/HTML без новых бизнес-решений, затем запускать batch на полный
согласованный список.

С 2026-07-05 `apply-approved-cards` сначала выполняет seller SKU replacement,
а затем content update. Это обязательный порядок для пачек, где меняется
артикул продавца: иначе WB seller SKU update может перезаписать часть content
старым payload, а Ozon/WB verify будет временно сверять старые и новые
идентификаторы. Если после apply появляется warning, считать результат только
по финальному `post_apply_content_verify` на новых internal SKU.

Подтверждено 2026-07-25 на пачке из 12 карточек:

- placeholder `wb_nm_id` не блокирует WB create; реальным считается только
  числовой `nmID` и нужен явный `safety.dangerous_actions=wb_card_create`;
- если old Ozon offer уже отсутствует, а new offer найден с тем же ожидаемым
  `product_id`, seller SKU считается `already_applied`, повторный write
  запрещён, выполняются verify и локальная синхронизация;
- после warning/error стадии нельзя оптимистично менять passport status;
- при частичном post-verify закрываются только подтвержденные SKU, остальные
  остаются `content_applied`/`applied` с точной причиной;
- фактические WB-фото проверяются в `cards/list[].photos`, а не в
  `mediaFiles`.

Подтверждено 2026-07-26 перед пачкой из 13 карточек: Layer 3 может хранить
точные owner-approved статусы медиаплана
`watermarked_assets_1_2_3_owner_approved` и
`owner_approved_copy_ozon_1_2_3_4_5_as_is_without_watermark`. Они являются
разрешающими эквивалентами `allowed_verified`, а не блокировками. Safety
preflight обязан принять их, но по-прежнему блокировать
`blocked_pending_watermarked_assets`. После изменения allowlist нужно
пересобрать новый `plan-approved-cards`, чтобы получить новый checksum и
чистый `wb_media_blocked_skus=[]`; старый план не применять.

Подтверждено 2026-07-26 на этой пачке:

- Ozon-only recovery существующих карточек:
  `plan-card-content-update --marketplace ozon`;
- при owner-approved варианте без хештегов Layer 3 должен иметь пустой
  `seo.ozon_hashtags` и constraint, запрещающий включать `23171` в payload;
- для persistent top-level drift после принятого import использовать
  `scripts/cards/ozon_lk_dimensions_apply.js` только с перехватом и проверкой
  `/api/v1/item/update` до сети, свежими ценами и точными guards фото/barcode;
- итог закрывать только после `verify-card-content-update` и
  `card_status_sync`; подтвержденная пачка:
  `final_verify_13_approved_cards_no_ozon_hashtags_20260726T1702`.

## Debug-путь изменения существующих карточек

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli plan-card-content-update \
  --internal-sku <internal_sku>
```

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli apply-card-content-update \
  --plan-run-id <plan_run_id> \
  --confirmed-by-user
```

## Ozon create

Для owner-approved WB-only карточек:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli plan-ozon-card-create \
  --internal-sku <internal_sku>
```

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli apply-ozon-card-create \
  --plan-run-id <plan_run_id> \
  --confirmed-by-user
```

Если Ozon-цена отсутствует и владелец согласовал использовать WB price fallback,
сначала строить plan с `--allow-wb-price-fallback`, затем apply с
`--allow-manual-review`.

## Ozon remove/archive

Для policy-блокеров и ошибочных Ozon-карточек использовать штатный маршрут из
`data/planning/card_ops/05_ozon_product_remove.md`, не ручные API-скрипты:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli plan-ozon-product-remove \
  --offer-id <offer_id> \
  --product-id <product_id> \
  --action auto \
  --reason "<owner-approved reason>"
```

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli apply-ozon-product-remove \
  --plan-run-id <plan_run_id> \
  --confirmed-by-user
```
