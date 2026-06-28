# 02. Смена артикулов продавца Ozon/WB

Дата: 2026-06-28

## Итог

Цель операции: заменить seller SKU маркетплейсов на внутренний артикул
Vital Shevron после owner-approved карточного HTML или отдельного явного
подтверждения владельца.

Статус автоматизации: штатные команды есть в CLI и `TaskRegistry`.

Dry-run по одному или нескольким внутренним артикулам из
`data/catalog/unified/products.csv`:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli plan-seller-sku-update \
  --internal-sku <internal_sku>
```

Dry-run по JSON-пакету:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli plan-seller-sku-update \
  --input data/pending/<package>.json
```

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli apply-seller-sku-update \
  --plan-run-id <plan_run_id> \
  --confirmed-by-user
```

Команда `apply-seller-sku-update` сама пишет request/response/verify artifacts,
RunManifest, закрывает approval marker и после успешной верификации обновляет
локальные CSV/JSON слои каталога.

## Когда применять

Применять только если:

- карточка owner-approved;
- владелец явно написал, что артикул тоже под замену;
- старый и новый артикул показаны в пакете или отдельном плане;
- `product_id` Ozon и `nmID` WB совпали с согласованной карточкой;
- новый артикул не занят.

## Ozon

API:

```text
POST /v1/product/update/offer-id
```

Payload:

```json
{
  "update_offer_id": [
    {
      "offer_id": "OLD_OFFER_ID",
      "new_offer_id": "INTERNAL_SKU"
    }
  ]
}
```

Verify:

- ответ API `errors=[]`;
- проверить карточку через `/v3/product/info/list` по `product_id`;
- в ответе поле `offer_id` должно быть равно `INTERNAL_SKU`.

Нюанс: сразу после apply поиск по новому `offer_id` может вернуть 404. Это не
достаточно для вывода об ошибке, если проверка по `product_id` показывает
новый `offer_id`.

## WB

API:

```text
POST /content/v2/cards/update
```

Payload должен сохранить текущие:

- `nmID`;
- `brand`;
- `title`;
- `description`;
- `dimensions`;
- `characteristics`;
- `sizes`;

и заменить только:

```json
{
  "vendorCode": "INTERNAL_SKU"
}
```

Verify:

- карточка находится по новому `vendorCode`;
- `nmID` не изменился;
- barcode в `sizes.skus` сохранился;
- `/content/v2/cards/error/list` не содержит ошибки по этой карточке.

## После успешной операции

Команда обновляет:

- `data/catalog/unified/products.*`;
- `data/catalog/content/content_master.*`;
- `data/catalog/processed/master_catalog.*`, пока legacy слой используется.

Если операция была связана с owner-approved паспортом карточки, дополнительно
проверить и при необходимости обновить:

- `data/catalog/master_passport/approved/<internal_sku>.json`.

Source snapshots не править вручную. После write-операции обновить их
командами:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli fetch-card-content
```

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli build-content-master
```

## Подтвержденный пример

```text
data/runs/2026-06-28/seller_sku_unify_apply_two_cards_20260628T163418
```

Применено:

- Ozon `text0001` -> `chev_kp_chvk_pict0001`;
- Ozon `pict0085` -> `chev_nr_bpla_pict0023`;
- WB `oopict0005_text0001_222094` -> `chev_kp_chvk_pict0001`;
- WB `chev_nr_bpla_pict0023` уже был создан с правильным `vendorCode`.
