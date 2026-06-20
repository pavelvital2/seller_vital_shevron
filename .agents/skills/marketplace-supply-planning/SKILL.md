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
- For Vital Shevron production planning include only currently manufactured
  goods: chevrons, patches, petlitcy, and kits made from them.
- Exclude hats, pouches, panamas, aprons, false epaulettes, and other
  non-manufactured goods from production/supply workbooks.
- Production quantity per article must be a multiple of 8. Current production
  capacity is 200 physical pieces per day until the owner changes it.

## Workbook Shape

Owner-facing supply workbooks should be `.xlsx`:

1. `Артикулы` - combined Ozon/WB article totals.
2. `Озон кластеры` - Ozon destination clusters, articles and quantities.
3. `ВБ кластеры` - WB destination clusters, articles and quantities.

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
