---
name: marketplace-supply-planning
description: "Use for Ozon/Wildberries stock and supply planning: current stocks, 90-day or quarterly sales, average daily sales, localization, destination clusters, product-type production multiples and Excel supply workbooks."
---

# Marketplace Supply Planning

## Core Rules

- Read `AGENTS.md`, `data/planning/supply_planning_runbook.md`, and
  `data/planning/chat_report_templates.md`.
- Supply creation or marketplace upload is a dangerous operation and requires
  review/approval.
- Keep Ozon and WB local IDs separate until mapping is confirmed.
- For an external Ozon shipment workbook that contains Ozon SKU, resolve the
  current seller article (`offer_id`) through a fresh `/v3/product/list` join
  by SKU. Do not match by title or trust an older `offer_id`; stop on missing
  or ambiguous SKU. Confirmed by run
  `ozon_shipment_sku_enrichment_20260719T1415` (102/102 rows matched).
- Preserve the row structure of external headerless workbooks. Put the current
  Ozon `offer_id` beside the old article without adding a synthetic header row
  or cell comments, and visually inspect the longest sheet after rendering.
  This prevents preview compression confirmed and fixed for the `Ростов` sheet
  in run `ozon_shipment_sku_enrichment_fix_20260719T1430`.
- When a shipment workbook has both planned and actual quantity columns, do
  not silently sum the plan. Identify the final shipment column from the file
  structure/differences, state the chosen source, and calculate physical units
  as actual goods quantity times confirmed `pack_qty`. Two uncut petlitcy count
  as one physical item.
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
- For the WB `В работу` workflow, use production multiples by product type:
  sleeve chevrons `8`, call-sign kits `6` kits, chest chevrons `12`, back
  chevrons `2`, cap chevrons `9`. If the type cannot be determined, keep the
  row in the plan with the owner-approved default multiple `8` and show a
  classification warning. Capacity input is measured in physical pieces;
  kits consume `marketplace_units * pack_qty` physical pieces.
- One marketplace unit of petlitcy is one physical production item: the two
  visible petlitcy are embroidered as one uncut pair. Use `pack_qty=1` for
  physical supply and production counts, even if a marketplace title says
  `2 шт.`.
- For WB supply-creation workbooks, include WB `barcode` plus quantity. If an
  external file has only `vendorCode` and quantity, enrich it from fresh WB
  Content API cards first; use `data/catalog/wb/processed/wb_catalog.csv` only
  as an explicit fallback. Do not invent missing barcodes.
- For WB warehouse/FBW supplies, use `https://supplies-api.wildberries.ru`:
  `POST /api/v1/supplies`, `GET /api/v1/supplies/{supplyID}`,
  `GET /api/v1/supplies/{supplyID}/goods`, and
  `GET /api/v1/supplies/{supplyID}/package`. Do not use FBS
  `marketplace-api /api/v3/supplies` as the source for warehouse inbound.
- For a recently delivered WB supply, do not answer "stock at warehouse" from
  `stocks-report/wb-warehouses.quantity` alone. While the supply has
  `statusID=4` (acceptance), show the FBW supply figures separately:
  `quantity`, `acceptedQuantity`, `readyForSaleQuantity`, and
  `unloadingQuantity`. Do not add the stock report to supply quantities because
  the sources can overlap or refresh at different times. Recheck the warehouse
  stock after `statusID=5` if the owner needs final sellable stock.
- A WB stock-and-supply report must include every registered FBW supply in an
  active pre-completion status, including `statusID=3` (`Отгрузка разрешена`),
  not only supplies already in transit or acceptance. Group supplies by status
  and show supply ID, warehouse, creation date, planned supply date, marketplace
  units, accepted units and ready-for-sale units. Do not present allowed-to-ship
  or acceptance quantities as current sellable stock.
- Confirmed on 2026-07-18: during `statusID=4`, WB
  `stocks-report.inWayFromClient` can temporarily contain accepted inbound FBW
  supply units, despite the LK label "В пути от покупателя". Detect this by
  matching `nmId` against `/supplies/{id}/goods`; do not classify the whole
  value as buyer returns. For the Electrostal case, 313 units matched all 23
  supply nmIds and another 3 of those units were already `inWayToClient`,
  exactly reconciling to `acceptedQuantity=316`.
- If `inWayFromClient` spikes implausibly, compare it with the previous saved
  warehouse snapshot before calling the difference returns. In the
  Electrostal incident, `quantity/inWayFromClient/inWayToClient` changed from
  `383/22/1` to `9/401/3` while the combined tracked mass changed only from
  `406` to `413`. This is a WB state reclassification/warehouse incident, not
  evidence of hundreds of buyer returns. Report the cause as unconfirmed until
  WB provides transaction-level movement or the warehouse status normalizes.
- For Ozon FBO supply orders, use the `supply-order` chain documented in
  `data/reference/api_docs/ozon/endpoints/supply_order.md`:
  `/v3/supply-order/list`, `/v3/supply-order/get`,
  `/v1/supply-order/details`, `/v1/supply-order/bundle`.
- For an Ozon stock monitor, keep three layers separate: general FBO
  `/v4/product/info/stocks`, warehouse `/v2/analytics/stock_on_warehouses`,
  and active supply-order bundles. Reconcile `general present` against
  `warehouse free_to_sell + warehouse reserved`, not against free stock alone.
  `promised_amount` can overlap the active supply-order quantity and must not
  be added again.
- Show unresolved Ozon order states, but count confirmed inbound only for
  `READY_TO_SUPPLY`, `ACCEPTED_AT_SUPPLY_WAREHOUSE`, `IN_TRANSIT`, and
  `ACCEPTANCE_AT_STORAGE_WAREHOUSE`. Report-stage orders can already overlap
  sellable stock. Exclude `order_tags.is_virtual=true` duplicates from totals.
- For the Ozon `В работу` workflow, calculate demand at `offer_id x destination
  cluster` granularity. Use `/v2/cluster/list` as the authoritative bridge:
  `financial_data.cluster_to` for demand, `data.fulfillments[].name` for
  warehouse stock, and `macrolocal_cluster_id` for confirmed inbound. Never
  subtract federal stock or inbound from every cluster.
- Rank Ozon destination clusters by summed positive physical deficit after
  subtracting only `free_to_sell` and confirmed inbound in the same cluster.
  Keep `reserved` and `promised` visible but do not treat them as free stock;
  `promised` can overlap supply-order. Let the owner choose `1..20` clusters.
- Ozon `В работу` uses the same production multiples as WB: sleeve `8`,
  call-sign kits `6` marketplace kits, chest `12`, back `2`, cap `9`, unknown
  type `8` with a control warning. Capacity is measured in physical pieces;
  local approval is checksum-bound and never creates an Ozon supply.
- Live Ozon Seller API verified 2026-07-14 returns `order_ids`, `orders` and
  bundle `items` at the response top level. Use `last_id`, not `offset`, for
  supply-order list pagination; bundle requests require `bundle_ids` and
  `limit <= 100`. Keep legacy nested `result` parsing only as compatibility.
- For historical physical inbound counts, group by Ozon `created_date` and WB
  `createDate` in the owner timezone. Exclude Ozon orders with
  `order_tags.is_virtual=true`: they redistribute goods already present in the
  original supply identified by `original_supply_id` and otherwise duplicate
  physical quantities.
- Use the штатный CLI entrypoint before one-off scripts:
  `PYTHONPATH=src /home/Codex/agent-tools/python/bin/python -m seller_agent.cli plan-supply-workbooks`.
  As of 2026-06-29 it has live read-only adapters for Ozon stocks/FBO
  postings/supply-order and WB stocks/sales/content/FBW supplies, writes raw
  snapshots, processed CSV, Markdown report, RunManifest and Excel workbooks.
- For the WB-only owner flow use
  `seller_agent.cli wb-production-work-plan --mode capacity --value N
  --cluster-count C` or `--mode coverage_days --value N --cluster-count C`,
  where `C` is `1..6`. It uses the current Analytics stocks
  report, buyer geography from Statistics sales, Content API cards/barcodes,
  active FBW inbound and unified `pack_qty`. It creates `Артикулы`,
  `ВБ регионы`, `Контроль`; it never creates a WB supply.
- In capacity mode calculate demand to a 30-day target and allocate no more
  than the entered physical capacity. In coverage mode use the owner-entered
  days. Demand forecast uses 90 full days with increased weight for the last
  30 days. Calculate each `product x WB destination cluster` independently:
  subtract only that product's regional sellable `quantity` and confirmed
  inbound statuses `2/3/4/6` to the same cluster. Never subtract store-wide
  stock from cluster demand and never count `inWayFromClient` as sellable
  stock. Rank clusters by summed positive physical deficit, then recent sales,
  and select the owner-requested top `C` before capacity allocation.
- Verified live on 2026-07-18: three WB `statusID=3` supplies were mapped by
  destination warehouse to Shushary, Volgograd and Novosemeykino and removed
  demand only for matching product-cluster pairs. A zero inbound value among
  final deficit rows can therefore mean inbound-covered products were excluded;
  verify against the saved `wb_active_inbound.json` before reporting that
  registered supplies were missed.
- WB historical Statistics rows can retain an old `supplierArticle` after the
  seller SKU is changed. Normalize each sale through stable `nmId` to the
  current Content API `vendorCode` before catalog mapping and aggregation;
  otherwise confirmed products are falsely reported as unmapped.
- If a warehouse has a strong `quantity`/`inWayFromClient` reclassification
  anomaly, exclude affected nmID+region rows from automatic production and
  put them on `Контроль`; do not manufacture against an unverified stock
  deficit.
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
- For WB warehouse fire/incident loss audits, calculate the store-wide
  accounting delta as `baseline + accepted inbound - net sales - current`,
  using identical stock states at both ends. Internal WB movements cancel at
  store level. Do not call that delta physical destruction when WB keeps
  damaged/frozen goods in the current stock ledger.
- Per-warehouse deltas require internal movement data: movements cancel for the
  whole store but create apparent shortages and surpluses by warehouse. Without
  movement evidence, label the row `preliminary balance deviation`, not loss.
  Treat the nearest incident snapshot, including `inWayToClient` and
  `inWayFromClient`, only as an exposed inventory upper bound.
- Confirm destroyed WB quantity only from a line-item loss/compensation
  document, preferably Documents API category `compensation-report`, or another
  official row with `nmId` and quantity. A `Добровольная выплата` finance row
  with `nmId=0` and `quantity=0` confirms only the paid amount; negative
  `deduction` increases seller payout.
- Convert confirmed marketplace quantities to physical items with the unified
  catalog `pack_qty`. Keep attacked warehouses without confirmed storage-zone
  damage in a separate control section and schedule a follow-up for the stated
  WB financial-report date.

- Confirmed on 2026-07-31: a recurring incident audit may automate the
  store-wide balance and compensation search, but its per-warehouse table must
  keep `loss_confirmed=false` until WB internal movements and a line-item
  compensation/loss document are available. A live stock drop inside the day
  is a control signal, not proof of destruction.

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
