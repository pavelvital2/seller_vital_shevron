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
- For WB parser checks immediately after combined price/action/bid changes,
  record the exact SERP collection time relative to every apply. A first
  parser run collected about two hours after bid apply is an early joint
  signal, not proof of bid impact. Compare the same query set and exact
  `query + nmID` pairs, then wait for 3-day and 7-day Promotion API/order/DRR
  controls before a second bid change. Confirmed on Vital Shevron 2026-07-18.

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
