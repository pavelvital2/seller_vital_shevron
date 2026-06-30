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
| Изменение параметров существующих карточек Ozon/WB | `data/planning/card_ops/03_card_content_update_ozon_wb.md` | быстрый путь: `apply-approved-card`; batch-путь: `apply-approved-cards`; ручной debug-путь: `plan-card-content-update`, `apply-card-content-update` |
| Promotion owner-approved Layer 2 audit/HTML в Layer 3 passport | этот файл | автоматизировано: `promote-approved-card-passport`; также встроено как preflight в `apply-approved-cards` |
| Создание карточки на Ozon | `data/planning/card_ops/04_ozon_card_create_later.md` | позже, пока не применять |

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
`применяй`, использовать batch-команду, а не запускать одноштучный сценарий
по кругу:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli apply-approved-cards \
  --internal-sku <sku_1> \
  --internal-sku <sku_2> \
  --internal-sku <sku_3> \
  --confirmed-by-user
```

Что делает batch-команда:

- проверяет наличие Layer 3 passport по каждому SKU и автоматически
  восстанавливает отсутствующие паспорта из owner-approved Layer 2 audit/HTML;
- строит единый targeted content plan по пачке;
- применяет Ozon/WB content update пачкой;
- строит и применяет seller SKU replacement пачкой;
- строит WB create plan из owner-approved Layer 3 passport только по
  указанным SKU;
- создает недостающие WB-карточки, загружает фото и проверяет результат;
- в конце делает обязательный финальный catalog-sync по старым и новым
  идентификаторам: старый Ozon `offer_id`, старый WB `vendorCode`, новый
  internal SKU, `product_id` и `nmID`;
- обновляет локальные `products`, `content_master`, `processed/master_catalog`
  и approved passports, удаляет склеенные WB-only дубли и сохраняет WB barcode;
- запускает нормализованный post-verify по новым internal SKU после seller SKU
  replacement и catalog-sync;
- пишет один общий run/report для пачки.

Если одна карточка блокируется на dry-run, batch должен продолжить по ready
карточкам и вынести blocked строки в общий отчет. Для больших пачек начинать с
5 SKU; после стабильной серии можно переходить к 10 SKU.

Подтверждено 2026-06-29 на пачке `0009-0014`: перед batch apply нужно
проверить, что для каждого owner-approved HTML/Layer 2 audit уже есть файл:

```text
data/catalog/master_passport/approved/<internal_sku>.json
```

Если HTML согласован, но Layer 3 passport отсутствует, marketplace write не
запускать неполной пачкой. Сначала восстановить passport из согласованного
`audit.json`/HTML без новых бизнес-решений, затем запускать batch на полный
согласованный список.

После batch, где сначала меняется content, а затем seller SKU, первичная
content-verify может дать ложные warning по старым `offer_id`/`vendorCode` или
по форматам (`100*100*10` против `100*100*10 мм`, WB `isValid`, схлопнутые
переносы строк в описании). Начиная с 2026-06-29 `apply-approved-cards`
сам делает повторный read-only targeted verify по новым internal SKU и
нормализует сравнение размеров и переносов перед итоговым статусом.

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

## Команды, которые еще нужно сделать штатными

Следующие команды пока не реализованы. Пока их нет, агент обязан явно написать
в отчете: `штатной CLI-команды нет`, использовать профильную инструкцию и
сохранять полный run с request, response, verify и summary без секретов.

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli plan-ozon-card-create --input data/pending/<package>.json
```
