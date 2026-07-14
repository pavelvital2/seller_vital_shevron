# 05. Ozon Product Remove / Archive

Дата: 2026-07-04

## Итог

Цель операции: безопасно убрать проблемную Ozon-карточку после owner approval:

- если карточка не создана, без SKU или висит после ошибочного импорта,
  использовать `POST /v2/products/delete` по `offer_id`;
- если карточка уже создана и получила SKU, использовать
  `POST /v1/product/archive` по `product_id`, предварительно проверив, что
  остаток нулевой.

Источник API: `data/reference/api_docs/ozon/openapi/seller_swagger_20260629.json`.

## Команды

Dry-run:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli plan-ozon-product-remove \
  --offer-id <offer_id> \
  --product-id <product_id> \
  --action auto \
  --reason "<owner-approved reason>"
```

Apply:

```bash
PYTHONPATH=src /home/Codex/agent-tools/python/bin/python \
  -m seller_agent.cli apply-ozon-product-remove \
  --plan-run-id <plan_run_id> \
  --confirmed-by-user
```

## Логика выбора

`--action auto`:

- `delete`, если `statuses.is_created=false`, `sku=0` или SKU отсутствует;
- `archive`, если карточка создана;
- `archive` блокируется, если у карточки есть остаток.

Для ручной диагностики можно указать `--action delete` или `--action archive`,
но apply все равно требует готовый план без ошибок и явное подтверждение
владельца.

## Проверка

Apply сохраняет:

```text
data/runs/<date>/<run_id>/ozon_product_remove_response.json
data/runs/<date>/<run_id>/ozon_product_info_after.json
data/runs/<date>/<run_id>/summary.json
```

Успешный delete считается подтвержденным, если Ozon вернул
`is_deleted=true`, а повторная проверка не показывает созданную карточку.

Успешный archive считается подтвержденным, если `/v3/product/info/list`
возвращает `is_archived=true`.

Для crash recovery TaskRegistry использует `ozon-product-remove-verify`:
для delete он проверяет отсутствие `offer_id` в attributes и
`statuses.is_created=false`, для archive - `is_archived=true`. Verify не
повторяет `/v2/products/delete` или `/v1/product/archive`.

## Подтвержденный кейс

2026-07-04: `chev_pz_ng_text0074` / `Сталкер`, product_id `5330427391`.
Ozon вернул `FB_UNWANTED`, `is_created=false`, `sku=0`. Владелец согласовал
`Сталкер в архив`. По документации и фактическому статусу применен не archive,
а `delete` через `/v2/products/delete`. Verify: `deleted_response_and_not_created`.
