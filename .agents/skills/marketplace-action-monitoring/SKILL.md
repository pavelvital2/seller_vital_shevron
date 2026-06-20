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
