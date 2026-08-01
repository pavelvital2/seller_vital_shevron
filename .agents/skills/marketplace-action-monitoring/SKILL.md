---
name: marketplace-action-monitoring
description: "Use for Ozon/Wildberries marketplace action monitoring after promotions or discounts are applied: Superboosting, Elastic, WB actions, baseline sales, daily by-product control, keep/watch/remove decisions and follow-up reminders."
---

# Marketplace Action Monitoring

## Core Rules

- Read `AGENTS.md`, the matching action/promotion runbook, and
  `data/planning/followups.md`.
- Monitoring is read-only unless it prepares a dry-run to remove/change items.
- Do not judge action effectiveness only by total sales. Compare each product
  individually against its own baseline.
- For quick post-action checks, use ordered units/orders rather than buyouts
  when buyouts lag the action timing.
- For liquidation controls, keep marketplace order evidence separate from
  advertising attribution: Seller/Statistics API answers whether the exact
  cohort received an order after apply, while Performance/Promotion API
  answers whether advertising received credit for it. Do not call an
  unattributed order an advertising order, and do not call zero advertising
  orders zero marketplace orders. Confirmed by the first Vital Shevron
  Ozon/WB liquidation control on 2026-07-31.
- Save both `by_product` and `daily_by_product` tables. Include zero-sales days.
- For Ozon `STOCK_DISCOUNT` actions such as `Супербустинг`, keep them separate
  from Elastic and compare action price against min price and the previous
  Elastic/action price.
- For API-only action apply flows, preflight must be scoped to the marketplace
  and API actually used by the write operation. For Ozon Elastic, use Ozon
  Seller API scoped preflight and do not block apply on WB LK/keepalive/refresh
  failures. If a relevant preflight check fails, report the failed check names
  and the preflight report path.
- For Ozon Superboosting sales control, if `/v1/analytics/data` rejects a
  `sku IN [list]` filter, fetch `dimensions=["sku","day"]` by pages without
  the SKU filter and filter the needed SKU list locally. This was confirmed on
  2026-06-25 by a 400 response for array `filters.value`.
- For WB discount apply verification, classify both legacy gradual-price
  errors and the current `moved to Price Quarantine` wording as price
  quarantine. If even a `35 p.p.` step is quarantined, verify current prices
  read-only and stop before any retry or `Apply New Price` action.
- Confirmed WB recovery sequence on 2026-07-14: exact-target quarantine
  `--dry-run` -> owner-approved `Apply New Price` to `35%` -> Prices API
  verify -> exact official upload to `55%` -> upload-history and Prices API
  verify. Stop if any target is missing or its base price/current discount
  drifted.
- Official WB seller documentation checked on 2026-07-14 sets the default
  quarantine threshold at a `33.3%` selling-price decrease. Keep the LK
  protection unchanged and cap automated increases in discount so the actual
  selling-price decrease is at most `33%` per upload; for `0% -> 55%`, use
  `0% -> 33% -> 55%`.
- For WB action exports, do not call every product present in an active promo
  file a current participant. `Акций > 0` means the product is offered in at
  least one active action; current participation requires `Да` in
  `Статусы в файлах акций` (including mixed `Да, Нет`). When forecasting a
  threshold change, count exclusions only among current participants, and
  derive target discount distribution from `Финальная скидка` separately from
  the next safe upload distribution in `Скидка к загрузке`.
- Confirmed on 2026-07-24: WB promo export status `Да` alone does not prove
  that the product currently sells in the action. Actual current participation
  requires both `Да` and a current seller discount not lower than
  `Скидка до порога`. A product at `50%` with a required `64-72%` discount is
  outside the action even if the export says `Да`. Forecast transitions from
  current qualification to target qualification; preserve the current result
  for rows whose discount does not change.
- WB action dry-run messages from Job Worker must show current participant
  count and discount distribution, exclusion count grouped by reason, and
  post-apply participant/non-participant counts with separate target discount
  distributions. A payload row count alone is not an owner-decision report.
- Confirmed on 2026-07-29 for WB automatic actions: compare the actual seller
  price after the required integer discount with the active per-product
  minimum. When several autoactions pass, select the highest actual price;
  keep the owner-selected manual discount for products outside actions.
  Before apply, freeze the reviewed report by checksum, verify the full
  minimum-price scope and require zero fresh-plan drift. After the discount
  upload, verify both the full price scope and exact `nmID + actionID`
  participation in fresh action exports. Confirmed Vital Shevron run
  `wb_best_price_action_apply_20260729T133631`: `11/11` joined their selected
  autoactions, price scope `478/478`, no quarantine.
- The Telegram entry point for this flow is `Wildberries -> Акции от
  минимальной цены`. It asks for the outside-action seller discount, defaults
  to `50%` through an explicit button, queues the fresh plan in Job Worker and
  exposes apply only when no target price is below minimum and every row is
  safe for one upload. Keep `WB акции 70-55-55` and `Ручная акция` as separate
  threshold-based alternatives.
- For WB parser checks immediately after combined price/action/bid changes,
  record the exact SERP collection time relative to every apply. A first
  parser run collected about two hours after bid apply is an early joint
  signal, not proof of bid impact. Compare the same query set and exact
  `query + nmID` pairs, then wait for 3-day and 7-day Promotion API/order/DRR
  controls before a second bid change. Confirmed on Vital Shevron 2026-07-18.
- For Ozon CPC checks after a combined price + bid apply, compare the exact
  changed SKU cohort against the same SKU before apply and against products
  whose bids were not increased. The apply day is mixed and the current day
  may be partial, so a 24-hour parser improvement is only an early joint
  price/advertising signal. Do not make a second mass bid change before three
  full post days; repeat the stable control after seven full days. Confirmed by
  Vital Shevron run `ozon_cpc_post_apply_review_20260720T2130`.
- Confirmed on 2026-07-29 for Ozon `Звёздные товары`: finance operation
  `StarsMembership` matches delivered rows one-to-one by
  `posting_number + sku`, and the current LK tariff is `1.5%` of the full
  realized turnover while all moderated goods available for purchase
  participate. Do not treat LK `Оборот пользователей в программе` as
  incremental revenue. Calculate the 30-day full-cost break-even uplift, show
  a generous sensitivity excluding advertising/storage allocation, and use a
  14-full-day controlled disable test for a causal decision. Disabling is a
  marketplace write and still requires owner approval.
- Confirmed on 2026-07-30 after disabling Ozon `Звёздные товары`: do not
  classify all later `StarsMembership` operations by finance operation date.
  Use the UTC `posting.order_date` relative to the exact deactivation time.
  One completed order created 2h41m after the verified disable still received
  the exact 1.5% fee; preserve it as a disputed propagation-tail row and
  recheck after finance maturity. Separately verify live `isActive=false`;
  an active CDP/keeper does not prove that the Ozon web session is authorized.
  In daily financial reports keep the fee in total expenses because it affected
  payout, but split it into orders before disable, the first 24 hours, and
  after the first 24 hours. Label zero post-24h rows as late/transition
  accruals, not as evidence that the program is active again.
- Confirmed on 2026-07-30 for WB dormant-stock analysis: do not classify a
  card from 30-day sales alone. Join current sellable stock, card creation
  date, completed Statistics API sales after removing returned `srid` pairs,
  open/cancelled orders, current autoaction offers, minimum price, active CPC
  membership/metrics and latest parser visibility. Keep open orders separate
  from completed buyouts. Before recommending a lower minimum, estimate the
  action price with the approved 30-day cost model; if the action price is
  below cost plus logistics, label it as loss-making instead of calling it a
  normal promotion. Vital Shevron reference:
  `scripts/analysis/wb_dormant_inventory.py` and
  `data/runs/2026-07-30/wb_dormant_inventory_20260730T113307/`.
- For a Vital Shevron owner-approved WB zero-margin liquidation objective,
  calculate a temporary per-pack floor as `production cost + average
  marketplace/logistics cost + explicit CPC reserve`, then round up. Select
  the highest active action price that remains at or above that floor;
  otherwise use a manual seller discount whose actual integer-discount price
  stays above the floor. For CPC campaigns, the official WB bid
  recommendation endpoint supports CPM only, so do not invent CPC auction
  recommendations. Use the median current bid of products with attributed
  orders in the same active CPC campaign, preserve a bid that already
  produced an order or 10+ clicks until the new price is tested, and enforce
  a post-apply per-card hard stop. Confirmed by dry-run
  `wb_dormant_liquidation_plan_20260730T1320`.
- Confirmed on 2026-07-30 for WB CPC campaign membership: a successful
  `PATCH /adv/v0/auction/nms` add can become visible in campaign reads before
  the bid-write backend accepts the nomenclature. Poll until the card has a
  positive system bid, recheck every existing approved bid, then send the
  exact bid package and verify it. If bid update returns
  `nomenclature not found in advert`, first prove whether any existing bids
  changed and whether the card is now registered; only then run a checksummed
  recovery for the same approved package.

- Confirmed on 2026-07-31 for ongoing liquidation control: replace reminder-
  only timers with a deduplicated Job Worker task over the exact approved
  cohort. Persist one product/day row including zeros, keep Seller/Statistics
  orders separate from ad attribution, exclude the incomplete current day and
  emit only a checksummed stop-review. Reaching a hard stop is not permission
  to remove a product from CPC automatically.
- Confirmed on 2026-08-01 for liquidation membership checks: current Ozon
  action membership is stored in `marketing_actions.actions`; accept
  `marketing_actions.current` only as a backward-compatible fallback. For WB
  CPC, derive campaign membership from every campaign `nm_settings` row with a
  positive search bid, not from product statistics rows: products with zero
  impressions may be absent from statistics while remaining active in CPC.
  Regression-test both source shapes and expose exact cohort membership counts
  in the owner report.
- Confirmed on 2026-08-01 for the WB liquidation second price stage: register
  a dedicated Job Worker `plan -> approval -> apply -> verify` chain. Rebuild
  the exact cohort immediately before upload and require the fresh actions
  checksum and full payload to match the owner-approved plan. Upload only the
  discount while preserving the reviewed base price, then require both upload
  history success and fresh Prices API equality for every target. Confirmed
  run `wb_liquidation_stage2_apply_20260801T150526`: drift `0`, upload
  `17/17`, Prices API `17/17`, base price unchanged. Start effectiveness
  evaluation from the new final-price window, not the earlier intermediate
  price stage.

## Baseline Pattern

For short promotion tests:

- primary baseline: 7 full days before apply;
- secondary context: 14 full days before apply for low-volume products;
- source: official marketplace analytics/finance/order API;
- daily table: one row per product per day, including zeros;
- output decisions: `keep`, `watch`, `remove_candidate`, `blocked`.

## Output

Control reports must include:

- action id/name and apply run id;
- baseline period and post-apply period;
- per-product baseline 7d/14d;
- post-apply product sales;
- price difference/risk;
- recommended decision per product;
- full file path and next action.

If removal or price/action changes are recommended, stop at dry-run/review
until owner approval.
