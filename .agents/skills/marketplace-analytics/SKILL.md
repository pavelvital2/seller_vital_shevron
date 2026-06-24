---
name: marketplace-analytics
description: "Router skill for Vital Shevron marketplace work. Use when a task mentions Ozon/Wildberries seller analytics, reports, parser data, prices, promotions, reviews/questions, customer chats, search queries, stock/supply, product cards, or marketplace operations and a more specific repo skill/user skill may need to be selected."
---

# Marketplace Analytics Router

This repo skill is only a routing layer. Do not grow it into a detailed
runbook. Detailed rules live in `data/planning/*.md` and in narrower skills.

## Always

- Read `AGENTS.md` first.
- Keep Ozon and WB identities separate until mapping is confirmed.
- Use Ozon-native IDs (`offer_id`, `product_id`, `sku`) and WB-native IDs
  (`vendorCode`, `nmID`, `barcode`).
- Use API-first sources; use LK/CDP only when API is missing or insufficient,
  and record why.
- Do not print or save secrets, cookies, storage state, auth headers, tokens,
  login codes, or private buyer data into committed files.
- For write-related marketplace actions use:
  `read-only -> dry-run -> review -> approved -> apply -> verify -> result`.
- After successful marketplace operations update the matching instruction in
  `data/planning/`; if a reusable cross-task rule was discovered, update the
  relevant skill too.

## Route By Task

Prefer the narrowest matching skill:

- `marketplace-reviews-questions` - reviews, questions, reply drafts, media,
  approval packages, apply/verify.
- `marketplace-ozon-messenger` - Ozon Messenger, buyer chats, notifications,
  important platform messages, mark-read and tail cleanup.
- `marketplace-supply-planning` - stocks, 90-day sales, localization,
  destination clusters, production constraints and supply workbooks.
- `marketplace-search-query-research` - Ozon/WB search query demand, LK/API
  exports, Excel exports, top queries and parser query comparison.
- `marketplace-action-monitoring` - promotion/action apply monitoring,
  Superboosting/Elastic/WB action baselines, post-apply sales checks.
- User-level `marketplace-sales-analytics` - sales, orders, buyouts, returns,
  margin, stock coverage and period comparisons.
- User-level `marketplace-promotion-analytics` - ads, bids, budgets, CPC,
  Elastic, Superboosting, WB promotion and action economics.
- User-level `marketplace-seo-card-optimization` - SEO visibility, parser
  positions, query coverage, cannibalization and card optimization.
- User-level `marketplace-product-card-content` - card titles, descriptions,
  attributes, photos, hashtags, color/name, grouping and designer tasks.
- User-level `marketplace-reporting` - owner-facing Telegram/Markdown/Excel
  report formatting and approval summaries.

## Required Source Check

For any marketplace task, read the matching runbook before acting. Common
routes:

- `data/planning/chat_report_templates.md`
- `data/planning/run_manifest_runbook.md`
- `data/planning/recommendations_index.md`
- `data/planning/project_map.md`

If no narrow skill exists, use this router plus the closest runbook and report
whether a new narrow skill should be created.

## Output

Report in the owner's preferred format:

1. Краткий вывод.
2. Источники и период.
3. Что сделано.
4. Риски и ограничения.
5. Файлы результата.
6. Следующий шаг.
