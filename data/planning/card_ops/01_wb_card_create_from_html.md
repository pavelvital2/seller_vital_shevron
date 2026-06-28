# 01. Создание карточки на WB из owner-approved HTML

Дата: 2026-06-28

## Итог

Цель операции: создать новую карточку WB для товара, который уже есть на Ozon,
прошел аудит, согласован владельцем в HTML и сохранен в Layer 3 master
passport.

Статус автоматизации: готово для owner-approved Layer 3 passport.

Готовые команды:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli plan-wb-card-create
```

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli apply-wb-card-create \
  --plan-run-id <plan_run_id> \
  --confirmed-by-user \
  --allow-manual-review
```

Подтверждено 2026-06-28: при передаче `--internal-sku` штатный
`plan-wb-card-create` строит план из owner-approved Layer 3 passport и не
требует legacy `data/catalog/raw/*/*`. Если `--internal-sku` не передан,
команда может работать по legacy `master_catalog.csv`, и перед apply нужно
сверять источник плана.

## Источники

- HTML, просмотренный владельцем.
- Layer 3 passport:

```text
data/catalog/master_passport/approved/<internal_sku>.json
```

- Быстрый общий runbook:

```text
data/planning/wb_card_create_runbook.md
```

## Safety

Создание карточки WB - write-операция. Обязательная цепочка:

```text
read-only -> dry-run -> review -> approved -> apply -> verify -> result
```

HTML является approval-пакетом только если там явно показано:

- создание новой WB-карточки;
- будущий `vendorCode`;
- `title`;
- `description`;
- `dimensions`;
- `characteristics`;
- фото/медиа;
- seller SKU, если он меняется.

## Быстрый порядок

1. Проверить preflight:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli status-preflight
```

2. Проверить свежий source snapshot:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli fetch-card-content
```

3. Убедиться, что WB-карточки с таким `vendorCode` еще нет:

```text
WB /content/v2/get/cards/list by textSearch=<vendorCode>
WB /content/v2/get/cards/trash by textSearch=<vendorCode>
```

4. Собрать dry-run plan.

Для owner-approved карточек использовать точечный запуск:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli plan-wb-card-create \
  --internal-sku <internal_sku>
```

Если штатный `plan-wb-card-create` не совпадает с Layer 3 паспортом, остановить
apply и исправить штатную команду или паспорт; не собирать одноразовый write
скрипт, если проблему можно закрыть в проектном CLI.

5. Apply только после owner approval:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli apply-wb-card-create \
  --plan-run-id <plan_run_id> \
  --confirmed-by-user
```

`--allow-manual-review` добавлять только если в плане действительно есть
`needs_manual_review=true` и владелец отдельно подтвердил применение такого
плана.

6. Verify:

- карточка найдена по `vendorCode`;
- получен `nmID`;
- barcode записан в `sizes.skus`;
- `/content/v3/media/save` вернул успешный ответ;
- `/content/v2/cards/error/list` не содержит ошибок по `vendorCode`;
- локальные слои обновлены через `fetch-card-content` и `build-content-master`.

Важное ограничение текущей реализации `fetch-card-content`: запуск с
`--limit-products` или частичным `--marketplace` при стандартном `output-dir`
перезаписывает общий `data/catalog/content/card_content_index.*` только
частичным набором строк. После создания WB-карточки не использовать такой
частичный refresh для постоянного `data/catalog/content/`, пока не реализован
merge-режим. Безопасные варианты:

- полный read-only refresh `fetch-card-content` после серии apply;
- частичный refresh только во временный `--output-dir` с последующим ручным
  сравнением, не подменяющим общий индекс;
- будущая штатная команда targeted merge-refresh.

Если частичный refresh случайно перезаписал общий индекс, восстановить его
полным:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli fetch-card-content

PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli build-content-master
```

## Подтвержденный пример

```text
data/runs/2026-06-28/wb_card_create_apply_owner_approved_chev_nr_bpla_pict0023_20260628T1622
```

Результат: `chev_nr_bpla_pict0023`, WB `nmID=1212515625`,
barcode `2052807975386`, media upload без ошибок.

## Подтвержденные внештатные ситуации

### 2026-06-28: WB `Цвет` больше 5 значений

Симптом: `/content/v2/cards/upload` вернул `error=false`, но затем
`/content/v2/cards/error/list` показал ошибку по группе:

```text
Поле Цвет имеет слишком много значений. Разрешено не более 5
```

Причина: в WB у поля `Цвет` для одной карточки было 6 значений. Из-за одной
ошибочной строки WB отклонил всю группу upload.

Восстановление:

1. Остановить повторный apply.
2. В Layer 3 passport проблемной карточки оставить для WB не более 5 основных
   цветов в порядке визуальной значимости.
3. Пересобрать `plan-wb-card-create --internal-sku ...`.
4. Проверить `manual_review_items=0`.
5. Повторить `apply-wb-card-create`.
6. Проверить `found_after_apply`, `pending_media_uploads`,
   `wb_card_errors_relevant`.

Подтвержденный результат:

```text
data/runs/2026-06-28/wb_card_create_apply_20260628T212740
```

`submitted_items=3`, `found_after_apply=3`, `media_upload_errors=0`,
`relevant_error_batches=0`.

### Внештатная ситуация: marketplace обновлен, но legacy master_catalog остался старым

Симптом: после успешного apply карточек `processed/master_catalog.*` не
обновился по новым internal SKU, потому что строки legacy-слоя еще находились
под старыми Ozon/WB артикулами.

Причина: локальный updater искал только новые SKU и не учитывал алиасы:
старый Ozon `offer_id`, старый WB `vendorCode`, `product_id`, `nmID`.

Восстановление и постоянное решение:

1. Не делать ручную правку marketplace.
2. Для batch-маршрута использовать `apply-approved-cards`.
3. Команда после apply выполняет финальный catalog-sync по алиасам.
4. Проверить, что `products.*`, `content_master.*`,
   `processed/master_catalog.*` и Layer 3 passport обновлены.
5. Проверить, что старые WB-only дубли удалены, а `wb_barcode` сохранен.

Тест защиты:

```bash
/home/Codex/agent-tools/python/bin/pytest tests/test_approved_cards_apply_catalog_sync.py
```
