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
- Parser top-query passes must be checked for latest run, collection time and
  unique query count before analysis.

## Output

For top-query reports show:

1. marketplace and period;
2. keyword/query set;
3. top queries and counts;
4. overlap/differences Ozon vs WB when requested;
5. own-card visibility and parser positions when available;
6. SEO/promotion/card recommendations;
7. saved file paths.

Update `search_queries_runbook.md` after confirmed LK/API behavior changes.
