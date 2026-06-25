from __future__ import annotations

import csv
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from seller_agent.config import AppCredentials
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.marketplaces.ozon.adapter import OzonSellerAdapter
from seller_agent.marketplaces.wb.adapter import WbContentAdapter
from seller_agent.reports.writer import ensure_dir, write_json


DEFAULT_CONTENT_DIR = Path("catalog/content")
DEFAULT_OUTPUT_DIR = Path("catalog/content/parameter_inventory")

OZON_CURRENT_FIELDS = [
    "description_category_id",
    "type_id",
    "attribute_id",
    "attribute_name",
    "type",
    "is_required",
    "is_aspect",
    "is_collection",
    "dictionary_id",
    "used_in_cards",
    "cards_total",
    "fill_rate",
    "unique_values_count",
    "sample_values",
]

OZON_SCHEMA_FIELDS = [
    "description_category_id",
    "type_id",
    "attribute_id",
    "attribute_name",
    "description",
    "type",
    "is_required",
    "is_aspect",
    "is_collection",
    "max_value_count",
    "dictionary_id",
    "group_name",
    "category_dependent",
]

WB_CURRENT_FIELDS = [
    "subject_id",
    "subject_name",
    "characteristic_id",
    "characteristic_name",
    "used_in_cards",
    "cards_total",
    "fill_rate",
    "unique_values_count",
    "sample_values",
]

WB_SCHEMA_FIELDS = [
    "subject_id",
    "subject_name",
    "characteristic_id",
    "characteristic_name",
    "charc_type",
    "required",
    "has_filter",
    "unit_name",
    "max_count",
]


def _read_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(str(path))
    import json

    return json.loads(path.read_text(encoding="utf-8"))


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def _text(value: Any) -> str:
    return str(value or "").strip()


def _bool_text(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    return _text(value).lower() if value not in {None, ""} else ""


def _values_text(values: Any) -> list[str]:
    result: list[str] = []
    for raw in values or []:
        value = raw.get("value") if isinstance(raw, dict) else raw
        text = _text(value)
        if text:
            result.append(text)
    return result


def _sample(values: list[str], *, limit: int = 8) -> str:
    seen: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
        if len(seen) >= limit:
            break
    return " | ".join(seen)


def _pct(part: int, total: int) -> str:
    if total <= 0:
        return ""
    return f"{(part / total * 100):.1f}%"


def _ozon_schema_row(category_id: str, type_id: str, item: dict[str, Any]) -> dict[str, Any]:
    return {
        "description_category_id": category_id,
        "type_id": type_id,
        "attribute_id": _text(item.get("id")),
        "attribute_name": _text(item.get("name")),
        "description": _text(item.get("description")),
        "type": _text(item.get("type")),
        "is_required": _bool_text(item.get("is_required")),
        "is_aspect": _bool_text(item.get("is_aspect")),
        "is_collection": _bool_text(item.get("is_collection")),
        "max_value_count": _text(item.get("max_value_count")),
        "dictionary_id": _text(item.get("dictionary_id")),
        "group_name": _text(item.get("group_name")),
        "category_dependent": _bool_text(item.get("category_dependent")),
    }


def _wb_schema_row(subject_id: str, subject_name: str, item: dict[str, Any]) -> dict[str, Any]:
    return {
        "subject_id": subject_id,
        "subject_name": subject_name,
        "characteristic_id": _text(item.get("charcID") or item.get("id")),
        "characteristic_name": _text(item.get("name")),
        "charc_type": _text(item.get("charcType")),
        "required": _bool_text(item.get("required")),
        "has_filter": _bool_text(item.get("hasFilter")),
        "unit_name": _text(item.get("unitName")),
        "max_count": _text(item.get("maxCount")),
    }


def build_parameter_inventory(
    *,
    ozon_content: dict[str, Any],
    wb_cards: list[dict[str, Any]],
    ozon_schema_items: dict[tuple[str, str], list[dict[str, Any]]] | None = None,
    wb_subject_characteristics: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    ozon_attrs = ozon_content.get("attributes") or []
    if not isinstance(ozon_attrs, list):
        ozon_attrs = []
    ozon_schema_items = ozon_schema_items or {}
    wb_subject_characteristics = wb_subject_characteristics or {}

    ozon_pairs = Counter(
        (_text(item.get("description_category_id")), _text(item.get("type_id")))
        for item in ozon_attrs
        if _text(item.get("description_category_id")) or _text(item.get("type_id"))
    )
    ozon_total_by_pair = dict(ozon_pairs)
    ozon_schema_rows: list[dict[str, Any]] = []
    ozon_schema_by_id: dict[tuple[str, str, str], dict[str, Any]] = {}
    for pair, items in ozon_schema_items.items():
        category_id, type_id = pair
        for item in items:
            row = _ozon_schema_row(category_id, type_id, item)
            ozon_schema_rows.append(row)
            ozon_schema_by_id[(category_id, type_id, row["attribute_id"])] = row

    ozon_usage: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    ozon_used_cards: Counter[tuple[str, str, str]] = Counter()
    for item in ozon_attrs:
        category_id = _text(item.get("description_category_id"))
        type_id = _text(item.get("type_id"))
        for attr in item.get("attributes") or []:
            attr_id = _text(attr.get("id"))
            values = _values_text(attr.get("values"))
            if values or attr_id:
                ozon_used_cards[(category_id, type_id, attr_id)] += 1
                ozon_usage[(category_id, type_id, attr_id)].extend(values or ["<empty>"])

    ozon_current_rows: list[dict[str, Any]] = []
    for (category_id, type_id, attr_id), values in sorted(ozon_usage.items(), key=lambda item: (item[0][0], item[0][1], int(item[0][2] or 0))):
        schema = ozon_schema_by_id.get((category_id, type_id, attr_id), {})
        used = ozon_used_cards[(category_id, type_id, attr_id)]
        total = ozon_total_by_pair.get((category_id, type_id), len(ozon_attrs))
        unique_values = sorted({value for value in values if value and value != "<empty>"})
        ozon_current_rows.append(
            {
                "description_category_id": category_id,
                "type_id": type_id,
                "attribute_id": attr_id,
                "attribute_name": schema.get("attribute_name", ""),
                "type": schema.get("type", ""),
                "is_required": schema.get("is_required", ""),
                "is_aspect": schema.get("is_aspect", ""),
                "is_collection": schema.get("is_collection", ""),
                "dictionary_id": schema.get("dictionary_id", ""),
                "used_in_cards": str(used),
                "cards_total": str(total),
                "fill_rate": _pct(used, total),
                "unique_values_count": str(len(unique_values)),
                "sample_values": _sample(unique_values),
            }
        )

    wb_cards = wb_cards if isinstance(wb_cards, list) else []
    wb_subjects = Counter(
        (_text(card.get("subjectID")), _text(card.get("subjectName")))
        for card in wb_cards
        if _text(card.get("subjectID")) or _text(card.get("subjectName"))
    )
    wb_subject_name_by_id: dict[str, str] = {}
    for (subject_id, subject_name), _count in wb_subjects.items():
        wb_subject_name_by_id.setdefault(subject_id, subject_name)

    wb_schema_rows: list[dict[str, Any]] = []
    wb_schema_by_id: dict[tuple[str, str], dict[str, Any]] = {}
    for subject_id, items in wb_subject_characteristics.items():
        subject_name = wb_subject_name_by_id.get(str(subject_id), "")
        for item in items:
            row = _wb_schema_row(str(subject_id), subject_name, item)
            wb_schema_rows.append(row)
            wb_schema_by_id[(str(subject_id), row["characteristic_id"])] = row

    wb_usage: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    wb_used_cards: Counter[tuple[str, str, str]] = Counter()
    wb_total_by_subject = {subject_id: count for (subject_id, _name), count in wb_subjects.items()}
    for card in wb_cards:
        subject_id = _text(card.get("subjectID"))
        subject_name = _text(card.get("subjectName"))
        for item in card.get("characteristics") or []:
            char_id = _text(item.get("id"))
            name = _text(item.get("name"))
            values = item.get("value")
            if not isinstance(values, list):
                values = [values]
            texts = [_text(value) for value in values if _text(value)]
            wb_usage[(subject_id, char_id, name)].extend(texts or ["<empty>"])
            wb_used_cards[(subject_id, char_id, name)] += 1

    wb_current_rows: list[dict[str, Any]] = []
    for (subject_id, char_id, name), values in sorted(wb_usage.items(), key=lambda item: (int(item[0][0] or 0), int(item[0][1] or 0))):
        schema = wb_schema_by_id.get((subject_id, char_id), {})
        used = wb_used_cards[(subject_id, char_id, name)]
        total = wb_total_by_subject.get(subject_id, len(wb_cards))
        unique_values = sorted({value for value in values if value and value != "<empty>"})
        wb_current_rows.append(
            {
                "subject_id": subject_id,
                "subject_name": wb_subject_name_by_id.get(subject_id, ""),
                "characteristic_id": char_id,
                "characteristic_name": name or schema.get("characteristic_name", ""),
                "used_in_cards": str(used),
                "cards_total": str(total),
                "fill_rate": _pct(used, total),
                "unique_values_count": str(len(unique_values)),
                "sample_values": _sample(unique_values),
            }
        )

    return {
        "ozon_current_rows": ozon_current_rows,
        "ozon_schema_rows": sorted(ozon_schema_rows, key=lambda row: (row["description_category_id"], row["type_id"], int(row["attribute_id"] or 0))),
        "wb_current_rows": wb_current_rows,
        "wb_schema_rows": sorted(wb_schema_rows, key=lambda row: (int(row["subject_id"] or 0), int(row["characteristic_id"] or 0))),
        "summary": {
            "ozon_cards": len(ozon_attrs),
            "ozon_category_type_pairs": len(ozon_pairs),
            "ozon_current_attributes": len(ozon_current_rows),
            "ozon_schema_attributes": sum(len(items) for items in ozon_schema_items.values()),
            "wb_cards": len(wb_cards),
            "wb_subjects": len(wb_subjects),
            "wb_current_characteristics": len(wb_current_rows),
            "wb_schema_characteristics": sum(len(items) for items in wb_subject_characteristics.values()),
            "wb_decor_for_clothes_cards": wb_total_by_subject.get("2367", 0),
        },
        "ozon_category_type_pairs": [
            {"description_category_id": key[0], "type_id": key[1], "cards": count}
            for key, count in sorted(ozon_pairs.items(), key=lambda item: (-item[1], item[0]))
        ],
        "wb_subjects": [
            {"subject_id": key[0], "subject_name": key[1], "cards": count}
            for key, count in sorted(wb_subjects.items(), key=lambda item: (-item[1], item[0]))
        ],
    }


def _write_report(path: Path, *, run_id: str, started_at: datetime, result: dict[str, Any]) -> None:
    summary = result["summary"]
    lines = [
        "# Card Content Parameter Inventory",
        "",
        f"Run ID: `{run_id}`",
        f"Started at: `{started_at.isoformat(timespec='seconds')}`",
        f"Status: `{result['overall_status']}`",
        "",
        "## Итог",
        "",
        "- Это read-only инвентаризация параметров карточек Ozon/WB перед созданием полноценного мастер-паспорта товара.",
        "- Отчет разделяет фактически заполненные параметры и схему площадки по категории/предмету.",
        "- Изменения карточек, фото, характеристик или группировок не выполнялись.",
        "",
        "## Summary",
        "",
    ]
    for key in sorted(summary):
        lines.append(f"- `{key}`: {summary[key]}")

    lines.extend(["", "## Ozon category/type pairs", ""])
    for row in result["ozon_category_type_pairs"][:30]:
        lines.append(
            f"- `{row['description_category_id']}` / `{row['type_id']}`: `{row['cards']}` cards"
        )

    lines.extend(["", "## WB subjects", ""])
    for row in result["wb_subjects"][:30]:
        lines.append(f"- `{row['subject_id']}` {row['subject_name']}: `{row['cards']}` cards")

    lines.extend(["", "## Errors", ""])
    if result["errors"]:
        for key, value in sorted(result["errors"].items()):
            lines.append(f"- `{key}`: {value}")
    else:
        lines.append("- none")

    lines.extend(["", "## Artifacts", ""])
    for key, value in sorted(result["artifacts"].items()):
        lines.append(f"- `{key}`: `{value}`")

    lines.extend(
        [
            "",
            "## Next step",
            "",
            "1. Review Ozon/WB schema CSV files.",
            "2. Decide which attributes become internal product-passport fields.",
            "3. Map internal fields to `ozon_attributes` and `wb_attributes` separately.",
            "4. Only after that generate saved card audit reports.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run_card_content_parameter_inventory(
    *,
    credentials: AppCredentials | None,
    data_dir: Path = Path("data"),
    content_dir: Path | None = None,
    output_dir: Path | None = None,
    run_id: str | None = None,
    fetch_schema: bool = True,
) -> dict[str, Any]:
    started_at = datetime.now()
    run_id = run_id or f"card_content_parameter_inventory_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    content_dir = content_dir or data_dir / DEFAULT_CONTENT_DIR
    output_dir = ensure_dir(output_dir or data_dir / DEFAULT_OUTPUT_DIR)
    errors: dict[str, str] = {}

    ozon_content: dict[str, Any] = {"attributes": [], "descriptions": []}
    wb_cards: list[dict[str, Any]] = []
    try:
        loaded = _read_json(content_dir / "ozon_card_content.json")
        if isinstance(loaded, dict):
            ozon_content = loaded
    except Exception as exc:  # noqa: BLE001 - report source data errors
        errors["ozon_card_content"] = str(exc)
    try:
        loaded_wb = _read_json(content_dir / "wb_card_content.json")
        if isinstance(loaded_wb, list):
            wb_cards = loaded_wb
    except Exception as exc:  # noqa: BLE001
        errors["wb_card_content"] = str(exc)

    inventory_probe = build_parameter_inventory(ozon_content=ozon_content, wb_cards=wb_cards)
    ozon_schema_items: dict[tuple[str, str], list[dict[str, Any]]] = {}
    wb_subject_characteristics: dict[str, list[dict[str, Any]]] = {}

    if fetch_schema:
        if credentials and credentials.ozon_seller:
            ozon = OzonSellerAdapter(credentials.ozon_seller)
            for row in inventory_probe["ozon_category_type_pairs"]:
                category_id = str(row["description_category_id"])
                type_id = str(row["type_id"])
                try:
                    ozon_schema_items[(category_id, type_id)] = ozon.fetch_description_category_attributes(
                        description_category_id=int(category_id),
                        type_id=int(type_id),
                    )
                except Exception as exc:  # noqa: BLE001
                    errors[f"ozon_schema:{category_id}:{type_id}"] = str(exc)
        else:
            errors["ozon_schema"] = "missing Ozon Seller API credentials"

        if credentials and credentials.wb:
            wb = WbContentAdapter(credentials.wb)
            for row in inventory_probe["wb_subjects"]:
                subject_id = str(row["subject_id"])
                if not subject_id.isdigit():
                    continue
                try:
                    wb_subject_characteristics[subject_id] = wb.get_subject_characteristics(int(subject_id))
                except Exception as exc:  # noqa: BLE001
                    errors[f"wb_schema:{subject_id}"] = str(exc)
        else:
            errors["wb_schema"] = "missing Wildberries API token"

    inventory = build_parameter_inventory(
        ozon_content=ozon_content,
        wb_cards=wb_cards,
        ozon_schema_items=ozon_schema_items,
        wb_subject_characteristics=wb_subject_characteristics,
    )

    ozon_current_path = output_dir / "ozon_current_attributes.csv"
    ozon_schema_path = output_dir / "ozon_schema_attributes.csv"
    wb_current_path = output_dir / "wb_current_characteristics.csv"
    wb_schema_path = output_dir / "wb_schema_characteristics.csv"
    report_path = run_dir / "card_content_parameter_inventory_report.md"
    summary_path = run_dir / "summary.json"

    _write_csv(ozon_current_path, inventory["ozon_current_rows"], OZON_CURRENT_FIELDS)
    _write_csv(ozon_schema_path, inventory["ozon_schema_rows"], OZON_SCHEMA_FIELDS)
    _write_csv(wb_current_path, inventory["wb_current_rows"], WB_CURRENT_FIELDS)
    _write_csv(wb_schema_path, inventory["wb_schema_rows"], WB_SCHEMA_FIELDS)

    overall_status = "warning" if errors else "ok"
    artifacts = {
        "run_dir": str(run_dir),
        "report": str(report_path),
        "summary": str(summary_path),
        "ozon_current_attributes_csv": str(ozon_current_path),
        "ozon_schema_attributes_csv": str(ozon_schema_path),
        "wb_current_characteristics_csv": str(wb_current_path),
        "wb_schema_characteristics_csv": str(wb_schema_path),
        "run_manifest": str(run_dir / "manifest.json"),
    }
    result = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "read_only",
        "overall_status": overall_status,
        "summary": inventory["summary"],
        "errors": errors,
        "inputs": {
            "content_dir": str(content_dir),
            "fetch_schema": fetch_schema,
        },
        "ozon_category_type_pairs": inventory["ozon_category_type_pairs"],
        "wb_subjects": inventory["wb_subjects"],
        "artifacts": artifacts,
    }
    write_json(summary_path, result)
    _write_report(report_path, run_id=run_id, started_at=started_at, result=result)
    write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=result,
        task="card-content-parameter-inventory",
        mode="read_only",
        risk="low",
        marketplaces=["ozon", "wb"],
        inputs=result["inputs"],
    )
    return result
