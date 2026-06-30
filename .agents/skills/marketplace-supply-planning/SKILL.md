---
name: marketplace-supply-planning
description: "Use for Ozon/Wildberries stock and supply planning: current stocks, 90-day or quarterly sales, average daily sales, localization, destination clusters, production constraints, multiples of 8 and Excel supply workbooks."
---

# Marketplace Supply Planning

## Core Rules

- Read `AGENTS.md`, `data/planning/supply_planning_runbook.md`, and
  `data/planning/chat_report_templates.md`.
- Supply creation or marketplace upload is a dangerous operation and requires
  review/approval.
- Keep Ozon and WB local IDs separate until mapping is confirmed.
- Do not treat stock warehouse or shipment warehouse as destination cluster.
  Separate `stock_warehouse`, `ship_from_warehouse`, buyer region/city, and
  `destination_cluster`.
- For Ozon `/v4/product/info/stocks`, parse FBO/FBS stock type from either
  `type` or `source`; some responses use `type=fbo`, and reading only `source`
  can falsely zero out all FBO stock.
- For Vital Shevron production planning include only currently manufactured
  goods: chevrons, patches, petlitcy, and kits made from them.
- Exclude hats, pouches, panamas, aprons, false epaulettes, and other
  non-manufactured goods from production/supply workbooks.
- Production quantity per article must be a multiple of 8. Current production
  capacity is 200 physical pieces per day until the owner changes it.
- For WB supply-creation workbooks, include WB `barcode` plus quantity. If an
  external file has only `vendorCode` and quantity, enrich it from fresh WB
  Content API cards first; use `data/catalog/wb/processed/wb_catalog.csv` only
  as an explicit fallback. Do not invent missing barcodes.
- For WB warehouse/FBW supplies, use `https://supplies-api.wildberries.ru`:
  `POST /api/v1/supplies`, `GET /api/v1/supplies/{supplyID}`,
  `GET /api/v1/supplies/{supplyID}/goods`, and
  `GET /api/v1/supplies/{supplyID}/package`. Do not use FBS
  `marketplace-api /api/v3/supplies` as the source for warehouse inbound.
- For Ozon FBO supply orders, use the `supply-order` chain documented in
  `data/reference/api_docs/ozon/endpoints/supply_order.md`:
  `/v3/supply-order/list`, `/v3/supply-order/get`,
  `/v1/supply-order/details`, `/v1/supply-order/bundle`.
- Use the штатный CLI entrypoint before one-off scripts:
  `PYTHONPATH=src /home/Codex/agent-tools/python/bin/python -m seller_agent.cli plan-supply-workbooks`.
  As of 2026-06-29 it has live read-only adapters for Ozon stocks/FBO
  postings/supply-order and WB stocks/sales/content/FBW supplies, writes raw
  snapshots, processed CSV, Markdown report, RunManifest and Excel workbooks.
- Ozon Seller API official Swagger is saved at
  `data/reference/api_docs/ozon/openapi/seller_swagger_20260629.json` after a
  CDP/LK docs check. For `/v3/supply-order/list`, use official string enums
  such as `READY_TO_SUPPLY`, `IN_TRANSIT`, `ORDER_CREATION`, `DESC`; do not
  carry forward numeric enum values from older live probes into permanent code.
- Ozon `/v1/supply-order/timeslot/get` is officially deprecated and scheduled
  for shutdown on 2026-08-19; before implementing timeslot logic, review
  `/v2/supply-order/timeslot/list`.
- API-first does not mean API-only. If Ozon/WB API lacks active supply status,
  destination cluster, acceptance state or supply composition, use the correct
  project LK session as fallback and record which API was insufficient.
- Work from official Ozon/WB API documentation, OpenAPI/Swagger, or an existing
  project adapter before trying payload/enum/endpoint guesses. Parameter
  probing is allowed only after documenting that the docs are missing,
  unavailable, stale, or contradict the live API; probing must stay read-only.
- Never infer item-level supply composition from total quantity alone. Use LK/API
  detail export, or a previously owner-approved workbook only when marketplace,
  region/cluster, quantity and date/supply id match; otherwise report
  `composition_not_confirmed`.

## Workbook Shape

Owner-facing supply workbooks should be `.xlsx`:

1. `Артикулы` - combined Ozon/WB article totals.
2. `Озон кластеры` - Ozon destination clusters, articles and quantities.
3. `ВБ кластеры` - WB destination clusters, articles and quantities.

For separate WB regional supply files intended for WB supply creation, owner
expects `.xlsx` files with clear columns:

- `Регион`
- `Артикул продавца`
- `Баркод ВБ`
- `Количество`

CSV files are supporting artifacts only.

## Output

Reports must answer:

- что производить;
- куда отправить;
- сколько отправить;
- какие ограничения/допущения;
- что уже учтено из предыдущих поставок;
- какой следующий контроль нужен.

Update `supply_planning_runbook.md` after confirmed source, formula or recovery
changes.
