from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Any

from seller_agent.catalog.loader import normalize_sku
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.reports.writer import ensure_dir, write_json


DEFAULT_CONTENT_MASTER_PATH = Path("catalog/content/content_master.csv")
DEFAULT_OUTPUT_DIR = Path("catalog/content")


@dataclass
class CardContentBacklogRow:
    backlog_rank: str
    audit_priority: str
    audit_focus: str
    score: str
    internal_product_id: str
    internal_sku: str = ""
    product_name: str = ""
    mapping_status: str = ""
    marketplace_presence: str = ""
    title_alignment_status: str = ""
    full_snapshot_status: str = ""
    transfer_direction: str = ""
    ozon_offer_id: str = ""
    wb_vendor_code: str = ""
    ozon_photo_count: str = ""
    wb_photo_count: str = ""
    ozon_description_present: str = ""
    wb_description_present: str = ""
    ozon_attribute_count: str = ""
    wb_attribute_count: str = ""
    ozon_hashtags_count: str = ""
    wb_tags_count: str = ""
    cost_total: str = ""
    reasons: str = ""
    next_step: str = ""
    visual_audit_required: str = "true"
    notes: str = ""


BACKLOG_FIELDS = [field.name for field in fields(CardContentBacklogRow)]


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [
            {key: (value or "").strip() for key, value in row.items()}
            for row in csv.DictReader(handle)
        ]


def _write_dict_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def _int_value(value: Any) -> int:
    try:
        return int(float(str(value or "0").replace(",", ".")))
    except ValueError:
        return 0


def _is_true(value: Any) -> bool:
    return str(value or "").strip().lower() in {"true", "1", "yes", "да"}


def _count_tokens(value: str) -> int:
    text = normalize_sku(value)
    if not text:
        return 0
    return len([part for part in text.replace(";", " ").split() if part.strip()])


def _has_ozon(row: dict[str, str]) -> bool:
    return normalize_sku(row.get("ozon_offer_id")) != ""


def _has_wb(row: dict[str, str]) -> bool:
    return normalize_sku(row.get("wb_vendor_code")) != ""


def _score_and_reasons(row: dict[str, str]) -> tuple[int, list[str], str]:
    score = 0
    reasons: list[str] = []
    focus_parts: list[str] = []

    if row.get("title_alignment_status") == "mismatch":
        score += 35
        reasons.append("title_mismatch")
        focus_parts.append("content_unification")

    if row.get("marketplace_presence") in {"ozon_only", "wb_only"}:
        score += 30
        reasons.append("marketplace_only")
        focus_parts.append("assortment_transfer_review")

    if not normalize_sku(row.get("cost_total")):
        score += 15
        reasons.append("missing_cost")
        focus_parts.append("margin_data")

    if row.get("full_snapshot_status") not in {"both_found", "ozon_found", "wb_found"}:
        score += 25
        reasons.append("content_snapshot_missing")
        focus_parts.append("data_gap")

    if _has_ozon(row):
        ozon_photo_count = _int_value(row.get("ozon_photo_count"))
        if ozon_photo_count < 5:
            score += 25
            reasons.append("ozon_photo_lt5")
            focus_parts.append("photo_backlog")
        if not _is_true(row.get("ozon_description_present")):
            score += 20
            reasons.append("ozon_description_missing")
            focus_parts.append("text_content")
        if _int_value(row.get("ozon_attribute_count")) == 0:
            score += 15
            reasons.append("ozon_attributes_missing")
            focus_parts.append("attributes")
        if _count_tokens(row.get("ozon_hashtags", "")) == 0:
            score += 10
            reasons.append("ozon_hashtags_missing")
            focus_parts.append("seo_hashtags")

    if _has_wb(row):
        wb_photo_count = _int_value(row.get("wb_photo_count"))
        if wb_photo_count < 5:
            score += 20
            reasons.append("wb_photo_lt5")
            focus_parts.append("photo_backlog")
        if not _is_true(row.get("wb_description_present")):
            score += 20
            reasons.append("wb_description_missing")
            focus_parts.append("text_content")
        if _int_value(row.get("wb_attribute_count")) == 0:
            score += 15
            reasons.append("wb_attributes_missing")
            focus_parts.append("attributes")

    if not reasons:
        return 0, [], "ready_for_visual_seo_audit"
    return score, reasons, ";".join(dict.fromkeys(focus_parts))


def _priority(score: int) -> str:
    if score >= 60:
        return "high"
    if score >= 30:
        return "normal"
    return "low"


def _next_step(row: dict[str, str], reasons: list[str]) -> str:
    if "content_snapshot_missing" in reasons:
        return "Повторить fetch-card-content и проверить native IDs."
    if "marketplace_only" in reasons:
        return "Проверить продажи/маржу/остатки и решить, переносить ли товар на вторую площадку."
    if "title_mismatch" in reasons:
        return "Провести визуальный аудит фото и подготовить единый SEO/content draft Ozon/WB."
    if any(reason.endswith("photo_lt5") for reason in reasons):
        return "Открыть все фото, сделать коллаж и поставить задачи дизайнеру по недостающим изображениям."
    if any("description_missing" in reason or "attributes_missing" in reason for reason in reasons):
        return "Проверить карточку и подготовить draft описания/характеристик."
    if "ozon_hashtags_missing" in reasons:
        return "Подобрать релевантные Ozon-хештеги после проверки запросов и фото."
    return normalize_sku(row.get("next_content_step")) or "Провести карточный аудит по product_card_work_runbook."


def build_card_content_audit_backlog(
    content_rows: list[dict[str, str]],
    *,
    include_low: bool = False,
) -> tuple[list[CardContentBacklogRow], dict[str, Any]]:
    backlog: list[CardContentBacklogRow] = []

    for row in content_rows:
        score, reasons, focus = _score_and_reasons(row)
        if score == 0 and not include_low:
            continue
        priority = _priority(score)
        backlog.append(
            CardContentBacklogRow(
                backlog_rank="",
                audit_priority=priority,
                audit_focus=focus,
                score=str(score),
                internal_product_id=normalize_sku(row.get("internal_product_id")),
                internal_sku=normalize_sku(row.get("internal_sku")),
                product_name=normalize_sku(row.get("product_name")),
                mapping_status=normalize_sku(row.get("mapping_status")),
                marketplace_presence=normalize_sku(row.get("marketplace_presence")),
                title_alignment_status=normalize_sku(row.get("title_alignment_status")),
                full_snapshot_status=normalize_sku(row.get("full_snapshot_status")),
                transfer_direction=normalize_sku(row.get("transfer_direction")),
                ozon_offer_id=normalize_sku(row.get("ozon_offer_id")),
                wb_vendor_code=normalize_sku(row.get("wb_vendor_code")),
                ozon_photo_count=normalize_sku(row.get("ozon_photo_count")),
                wb_photo_count=normalize_sku(row.get("wb_photo_count")),
                ozon_description_present=normalize_sku(row.get("ozon_description_present")),
                wb_description_present=normalize_sku(row.get("wb_description_present")),
                ozon_attribute_count=normalize_sku(row.get("ozon_attribute_count")),
                wb_attribute_count=normalize_sku(row.get("wb_attribute_count")),
                ozon_hashtags_count=str(_count_tokens(row.get("ozon_hashtags", ""))),
                wb_tags_count=str(_count_tokens(row.get("wb_tags", ""))),
                cost_total=normalize_sku(row.get("cost_total")),
                reasons=";".join(reasons) if reasons else "ready_for_visual_seo_audit",
                next_step=_next_step(row, reasons),
                notes=normalize_sku(row.get("notes")),
            )
        )

    backlog.sort(
        key=lambda item: (
            -_int_value(item.score),
            item.mapping_status != "confirmed",
            item.internal_sku or item.internal_product_id,
        )
    )
    for index, row in enumerate(backlog, start=1):
        row.backlog_rank = str(index)

    summary = {
        "input_rows": len(content_rows),
        "backlog_rows": len(backlog),
        "high_priority_rows": sum(1 for row in backlog if row.audit_priority == "high"),
        "normal_priority_rows": sum(1 for row in backlog if row.audit_priority == "normal"),
        "low_priority_rows": sum(1 for row in backlog if row.audit_priority == "low"),
        "title_mismatch_rows": sum(1 for row in backlog if "title_mismatch" in row.reasons),
        "marketplace_only_rows": sum(1 for row in backlog if "marketplace_only" in row.reasons),
        "photo_lt5_rows": sum(1 for row in backlog if "photo_lt5" in row.reasons),
        "missing_cost_rows": sum(1 for row in backlog if "missing_cost" in row.reasons),
        "ozon_hashtags_missing_rows": sum(1 for row in backlog if "ozon_hashtags_missing" in row.reasons),
        "include_low": include_low,
    }
    return backlog, summary


def _write_report(path: Path, *, run_id: str, started_at: datetime, result: dict[str, Any]) -> None:
    lines = [
        "# Card Content Audit Backlog Report",
        "",
        f"Run ID: `{run_id}`",
        f"Started at: `{started_at.isoformat(timespec='seconds')}`",
        f"Status: `{result['overall_status']}`",
        "",
        "## Summary",
        "",
    ]
    for key in sorted(result["summary"]):
        lines.append(f"- `{key}`: {result['summary'][key]}")

    lines.extend(["", "## Top Backlog Rows", ""])
    for row in result.get("backlog_sample", [])[:30]:
        lines.append(
            f"- `{row['backlog_rank']}` `{row['audit_priority']}` score `{row['score']}` "
            f"`{row['internal_product_id']}`: {row['product_name']} "
            f"({row['reasons']})"
        )
    if result.get("backlog_sample_truncated"):
        lines.append(f"- truncated: {result['backlog_sample_truncated']} more rows")

    lines.extend(["", "## Artifacts", ""])
    for key, value in sorted(result["artifacts"].items()):
        lines.append(f"- `{key}`: `{value}`")

    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Read-only backlog only. It does not recommend final card text or apply changes.",
            "- `photo_lt5` uses API photo count and still requires visual inspection and collage.",
            "- Sales, stocks and parser positions are not part of this first backlog layer yet.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run_card_content_audit_backlog(
    *,
    data_dir: Path = Path("data"),
    content_master_path: Path | None = None,
    output_dir: Path | None = None,
    run_id: str | None = None,
    include_low: bool = False,
) -> dict[str, Any]:
    started_at = datetime.now()
    run_id = run_id or f"card_content_audit_backlog_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    content_master_path = content_master_path or data_dir / DEFAULT_CONTENT_MASTER_PATH
    output_dir = ensure_dir(output_dir or data_dir / DEFAULT_OUTPUT_DIR)

    errors: dict[str, str] = {}
    content_rows: list[dict[str, str]] = []
    try:
        content_rows = _read_csv(content_master_path)
    except Exception as exc:  # noqa: BLE001 - task report must capture local input errors
        errors["content_master"] = str(exc)

    backlog: list[CardContentBacklogRow] = []
    summary: dict[str, Any] = {
        "input_rows": len(content_rows),
        "backlog_rows": 0,
        "high_priority_rows": 0,
        "normal_priority_rows": 0,
        "low_priority_rows": 0,
        "title_mismatch_rows": 0,
        "marketplace_only_rows": 0,
        "photo_lt5_rows": 0,
        "missing_cost_rows": 0,
        "ozon_hashtags_missing_rows": 0,
        "include_low": include_low,
    }
    if not errors:
        backlog, summary = build_card_content_audit_backlog(content_rows, include_low=include_low)

    backlog_dicts = [asdict(row) for row in backlog]
    backlog_csv_path = output_dir / "card_content_audit_backlog.csv"
    backlog_json_path = output_dir / "card_content_audit_backlog.json"
    report_path = run_dir / "card_content_audit_backlog_report.md"
    summary_path = run_dir / "summary.json"
    if not errors:
        _write_dict_csv(backlog_csv_path, backlog_dicts, BACKLOG_FIELDS)
        write_json(backlog_json_path, backlog_dicts)

    overall_status = "error" if errors else "warning" if backlog else "ok"
    artifacts = {
        "run_dir": str(run_dir),
        "backlog_csv": str(backlog_csv_path),
        "backlog_json": str(backlog_json_path),
        "report": str(report_path),
        "summary": str(summary_path),
        "run_manifest": str(run_dir / "manifest.json"),
    }
    backlog_sample = backlog_dicts[:50]
    result = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "read_only",
        "overall_status": overall_status,
        "summary": summary,
        "errors": errors,
        "backlog_sample": backlog_sample,
        "backlog_sample_truncated": max(0, len(backlog_dicts) - len(backlog_sample)),
        "inputs": {
            "content_master_path": str(content_master_path),
            "include_low": include_low,
        },
        "artifacts": artifacts,
    }

    write_json(summary_path, result)
    _write_report(report_path, run_id=run_id, started_at=started_at, result=result)
    write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=result,
        task="card-content-audit-backlog",
        mode="read_only",
        risk="low",
        marketplaces=["ozon", "wb"],
        inputs=result["inputs"],
    )
    return result
