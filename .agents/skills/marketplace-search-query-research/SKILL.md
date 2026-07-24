---
name: marketplace-search-query-research
description: "Use for Ozon/Wildberries search query research: LK/API popular queries, period selection, Excel exports, top query lists, parser top-query comparison and SEO demand reports."
---

# Marketplace Search Query Research

## Core Rules

- Read `AGENTS.md`, `data/planning/search_queries_runbook.md`,
  `data/planning/ozon_parser_positions_runbook.md`,
  `data/planning/wb_parser_positions_runbook.md`, and
  `data/planning/chat_report_templates.md`.
- Work read-only unless the owner explicitly moves to a card/promotion dry-run.
- Verify source freshness: period, export timestamp, parser run id, query
  count, marketplace and keyword.
- Do not estimate sales from query counts alone. Query counts are demand and
  opportunity signals unless conversion assumptions are explicitly labeled.

## Source Routes

- WB popular search queries page may require scroll and `Показать ещё` for the
  next 50 queries; verify the chosen period before export.
- Ozon search-query pages/API may have pagination, period limits and Premium
  restrictions; record limitations.
- Confirmed 2026-07-21 for long Ozon multi-seed LK collection: arm a technical
  warm-up request after page load before recording the first business seed.
  Otherwise the response listener can capture the initial unfiltered table.
  The keeper may also replace/close the page during a long run; checkpoint
  every seed and retry by reconnecting only after the CDP guard, expected
  profile and `Vital Shevron` store marker pass again. Do not replace the UI
  trigger with raw in-page `fetch`: the endpoint returned `403 Failed to get
  company ID` without the service context injected by the Ozon interface.
  Never extract or persist auth headers to work around that response.
- For exact-frequency tables, save a value only for an exact normalized query
  match. An empty response is `no_data_returned`; a nonempty top-50 response
  without the exact phrase is `not_returned_top50`, not zero demand.
- Parser top-query passes must be checked for latest run, collection time and
  unique query count before analysis.
- For Vital Shevron card-audit orchestration, do not make fresh card auditors
  collect LK query data. Prepare a reusable `seo_query_pack` first: Ozon/WB
  top-query tables with period, frequency/popularity, source and export
  timestamp, plus target query clusters (`primary_target`,
  `secondary_target`, `broad_identity`, `placement`, `exclude`). Parser
  per-query positions are optional baseline/monitoring data for later
  movement checks after card changes.
- For Vital Shevron card-audit packages, `seo_query_pack` must pass row-level
  demand evidence in `confirmed_query_rows`: query, marketplace, role,
  seed_query, rank, frequency/popularity, period, source and collected_at.
  Aggregate sums such as `ozon_frequency_sum` and `wb_frequency_sum` are not
  enough for fresh auditors to prepare demand-backed SEO recommendations.
- Use `scripts/search_queries/collect_seo_query_pack_sources.js` to collect
  fresh Ozon/WB top-query sources when current source tables are missing or
  stale, then build the card-auditor input with
  `PYTHONPATH=src /home/Codex/agent-tools/python/bin/python -m seller_agent.cli seo-query-pack`.
- Treat `seo_query_pack` statuses as routing, not decoration: `ready` can go
  to auditors, `ready_broad_only` must carry a "broad demand only" limitation,
  `needs_manual_review` requires owner/orchestrator clarification first, and
  `excluded_non_patch_assortment` must not be audited as a shevron/patch card.
- In Vital Shevron mass audit packages, exclude `needs_manual_review` and
  `excluded_non_patch_assortment` from generated auditor packages; keep them in
  `excluded_package_index.csv/json` for later owner review.

## Output

For top-query reports show:

1. marketplace and period;
2. keyword/query set;
3. top queries and counts;
4. overlap/differences Ozon vs WB when requested;
5. own-card visibility and parser positions when available;
6. SEO/promotion/card recommendations;
7. saved file paths.

For card-audit inputs also save the normalized `seo_query_pack` path and
include source freshness metadata. If only aggregate parser visibility exists
without concrete queries, mark demand SEO as `blocked_no_query_list`.

Update `search_queries_runbook.md` after confirmed LK/API behavior changes.
