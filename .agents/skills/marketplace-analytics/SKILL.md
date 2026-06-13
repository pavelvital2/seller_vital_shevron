---
name: marketplace-analytics
description: "Run read-only marketplace analytics workflows for Ozon/Wildberries seller projects: SEO audits, parser position analysis, search-query demand, pricing and margin checks, ads efficiency, stocks/supply planning, reviews/questions summaries, cannibalization checks, and repeatable report/backlog generation. Use when Codex is asked to analyze marketplace data, parser runs, Ozon/WB search queries, product cards, prices, stocks, supplies, promotions, or to prepare an analytics report without write changes."
---

# Marketplace Analytics

## Core Rules

- Read `AGENTS.md` first and follow the project's safety rules.
- Treat analytics as read-only unless the owner explicitly approves an apply
  operation through the project safety chain.
- Never save or print API keys, tokens, cookies, storage state, auth headers,
  login codes, or closed-source secret file contents.
- Use API-first sources when an official API can provide the required data.
  Use LK pages only when API data is unavailable or insufficient, and record
  why the API path was not enough.
- Do not commit raw parser datasets, catalog snapshots, `data/runs/`,
  `data/pending/`, `data/approved/`, sessions, or temporary auth files.
- Use temporary folders for working datasets when needed, then clean them after
  the derived report is saved.
- If source freshness matters, verify source timestamps before drawing
  conclusions. If freshness cannot be confirmed, say so in the report.
- After a marketplace parser finishes a new top-query pass, verify the latest
  parser slice before analysis: file `mtime`, parser `run_id`, unique query
  count and `collected_at_utc` range. Do not reuse an older parser report just
  because it is already present under `data/runs/`.
- Do not estimate sales from search-query counts alone. Query counts can support
  demand/opportunity ranking only when conversion assumptions are explicitly
  labeled as assumptions.
- For supply planning, never treat stock warehouse or shipment warehouse as the
  destination cluster. Separate `stock_warehouse`, `ship_from_warehouse`,
  `buyer_region`/`buyer_city`, and `destination_cluster`. If only shipment
  warehouses are available, label the limitation and do not present them as
  where goods should be sent.
- For Vital Shevron supply workbooks, include only currently manufactured
  goods: chevrons, patches, petlitcy, and kits made from them. Exclude hats,
  pouches, panamas, aprons, false epaulettes, and other non-manufactured goods
  from the production/supply workbook.
- Build the owner-facing supply workbook as `.xlsx` with three sheets:
  `Артикулы`, `Озон кластеры`, `ВБ кластеры`. Keep CSV files as supporting
  artifacts only.

## Required Source Discovery

Before analysis, read the runbooks that match the task:

- `data/planning/search_queries_runbook.md` for Ozon/WB search demand;
- `data/planning/seo_audit_runbook.md` for card SEO audits;
- `data/planning/card_grouping_runbook.md` for Ozon/WB card grouping,
  Ozon `model_info`, WB `imtID`, and future grouping dry-runs;
- `data/planning/ozon_parser_positions_runbook.md` for Ozon parser positions;
- `data/planning/wb_parser_positions_runbook.md` for WB parser positions;
- `data/planning/pricing_runbook.md` for price, min price and margin checks;
- `data/planning/ozon_cpc_efficiency_runbook.md` for Ozon CPC;
- `data/planning/wb_promotion_runbook.md` for WB promotion;
- `data/planning/supply_planning_runbook.md` for stocks, 90-day sales,
  localization and supply planning;
- `data/planning/reviews_questions_runbook.md` for reviews and questions;
- `data/planning/catalog_mapping_runbook.md` when cross-marketplace matching
  or unified product reporting is involved.

Also check:

- latest relevant `data/runs/YYYY-MM-DD/` derived reports;
- local Ozon/WB catalog snapshots, without committing raw snapshots;
- confirmed native marketplace IDs before matching rows;
- `data/planning/recommendations_index.md` for existing backlog items.

## Identity Rules

- Ozon scenarios use Ozon-native identifiers: `offer_id`, `product_id`, `sku`.
- WB scenarios use WB-native identifiers: `vendorCode`, `nmID`, barcode.
- Cross-marketplace conclusions require confirmed mapping. Missing mapping does
  not block marketplace-local read-only analysis.
- For WB parser analysis, do not identify own goods by brand alone. Use local
  `nmID` and confirmed supplier/shop identity.
- For Ozon parser exports, verify the meaning of parser `productId` and `sku`
  on every run. In the 2026-06-13 Vital Shevron run, parser `productId`/`sku`
  matched local Ozon `sku`, not local API `product_id`; this is a known
  project observation, not a permanent external rule.

## Workflow

1. Define scope: marketplace, operation, period, query set, product set and
   whether the result is read-only, dry-run, or write-related.
2. Gather sources and record source paths, timestamps, export periods and API/LK
   routes used.
3. Validate product identity and mapping before joining datasets.
4. Build derived tables only:
   - query opportunities;
   - product/card audit;
   - cluster coverage;
   - cannibalization;
   - competitor visibility;
   - pricing/margin constraints;
   - ads efficiency;
   - stock coverage and supply recommendations;
   - destination cluster allocation, separate from shipment/stock warehouses;
   - limitations and missing data.
5. Save the report under `data/runs/<date>/<operation>_<timestamp>/` unless the
   task explicitly requires a permanent planning document.
6. Present a concise chat report with sources, metrics, findings,
   recommendations, risks, and saved file paths.
7. If a write action is requested, stop at dry-run/review unless the owner has
   explicitly approved the apply stage.
8. If a new source, page, API method, error recovery, rule or recommendation is
   discovered, update the matching runbook, `project_map.md` and
   `recommendations_index.md` in the same work cycle.
9. At the end of the task, assess whether this skill itself should be improved.
   If there is a concrete improvement, report it to the owner and propose the
   edit. Add only repeatable rules, verified limitations, source routes,
   recovery steps, report templates, or quality checks that reduce future error
   risk or speed up recurring work.

## SEO-Specific Rules

- Use search clusters from the card title for target query matching.
- Description text is diagnostic only; do not treat description-only matches as
  full SEO coverage.
- For WB SEO audits, keep rating sources separate: parser SERP rating,
  Feedbacks API review valuations, and official card rating are different
  metrics. If official card rating cannot be confirmed from an available
  source, label it as a limitation instead of merging it with parser or
  feedback ratings.
- Compare demand, parser position, own-card coverage, card quality and
  competitor visibility together. Do not recommend title edits from query count
  alone.
- Wait for parser runs to finish when the user asks for a complete comparison.
  If only partial parser data is available, label the report as partial.
- Keep Ozon and WB results separate until mapping quality is confirmed.

## Output Shape

For Telegram-facing analytics reports, follow
`data/planning/chat_report_templates.md` first. The chat message is the owner's
primary report screen; the full report file must still be saved and attached or
listed at the bottom of the message.

For analytics reports without a more specific template, use this order:

1. Краткий вывод.
2. Источники и период.
3. Что проверено.
4. Находки.
5. Рекомендации.
6. Риски и ограничения.
7. Файлы результата.
8. Что делать дальше.

## Development Boundary

This repo skill is the immediate lightweight layer. Do not build a Codex plugin,
multi-agent workflow, or Agents SDK tracing until the underlying project
workflows have stable CLI commands, run manifests, source contracts and report
formats. Keep those larger steps in the development plan.
