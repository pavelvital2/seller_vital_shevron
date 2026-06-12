from __future__ import annotations

import csv
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from takterra_agent.catalog.schema import MASTER_CATALOG_FIELDS, MasterCatalogRow


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, data: Any) -> None:
    ensure_dir(path.parent)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def write_master_catalog_csv(path: Path, rows: list[MasterCatalogRow]) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MASTER_CATALOG_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: getattr(row, field) for field in MASTER_CATALOG_FIELDS})


def write_markdown_report(
    path: Path,
    *,
    run_id: str,
    started_at: datetime,
    summary: dict[str, Any],
    errors: dict[str, str],
    artifacts: dict[str, str],
) -> None:
    ensure_dir(path.parent)
    lines = [
        "# Catalog Check Report",
        "",
        f"Run ID: `{run_id}`",
        f"Started at: `{started_at.isoformat(timespec='seconds')}`",
        "",
        "## Summary",
        "",
    ]
    for key in sorted(summary):
        lines.append(f"- `{key}`: {summary[key]}")

    lines.extend(["", "## Errors", ""])
    if errors:
        for key, value in sorted(errors.items()):
            lines.append(f"- `{key}`: {value}")
    else:
        lines.append("- none")

    lines.extend(["", "## Artifacts", ""])
    for key, value in sorted(artifacts.items()):
        lines.append(f"- `{key}`: `{value}`")

    lines.extend(
        [
            "",
            "## Recommendations",
            "",
            "1. Vital Shevron starts in `SELLER_SKU_MODE=separate`: Ozon and WB seller SKU may differ.",
            "2. Use Ozon/WB native IDs for marketplace-local scenarios before seller SKU unification.",
            "3. Use mapping review before cross-marketplace product operations.",
            "4. Treat `ozon_platform_barcode` as informational: Ozon may return platform barcodes with `OZN` prefix.",
            "5. Seller SKU unification is a separate dangerous operation and requires owner approval.",
            "6. Do not block marketplace-local read-only/dry-run tasks only because mapping is incomplete.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
