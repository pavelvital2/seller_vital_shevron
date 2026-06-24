from __future__ import annotations

import csv
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
import re
from pathlib import Path
from typing import Any

from seller_agent.catalog.loader import normalize_sku
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.reports.writer import ensure_dir, write_json


DEFAULT_UNIFIED_PRODUCTS_PATH = Path("catalog/unified/products.csv")
DEFAULT_OUTPUT_DIR = Path("catalog/unified")

PLAN_FIELDS = [
    "source_marketplace",
    "source_id",
    "source_secondary_id",
    "product_name",
    "mapping_status",
    "current_internal_product_id",
    "proposed_internal_sku",
    "proposal_status",
    "confidence",
    "product_prefix",
    "pack_qty",
    "purpose_prefix",
    "theme_prefix",
    "structure_prefix",
    "content_prefix",
    "number_scope",
    "reasons",
    "notes",
]

SUPPORTED_PRODUCT_PREFIXES = {"chev", "nash", "loop"}
STRUCTURE_PATTERNS = (
    ("fsin", ("фсин", "уис")),
    ("fssp", ("фссп", "судебн", "пристав")),
    ("fsb", ("фсб", "погранич")),
    ("fso", ("фсо",)),
    ("rg", ("росгвард", "фсвнг", "внг", "нацгвард")),
    ("mvd", ("мвд", "полиция", "дпс", "гибдд")),
    ("chvk", ("чвк",)),
)
THEME_PATTERNS = (
    ("bpla", ("бпла", "беспилот", "дрон")),
    ("sht", ("шторм",)),
    ("gv", ("группировка войск",)),
    ("berserk", ("берсерк",)),
    ("fan", ("фанат",)),
    ("prikol", ("прикол", "рыболовн", "войска тыла")),
    ("form", ("security", "секьюрити", "staff", "стафф", "охрана", "кадет", "контролер")),
    ("voisk", ("войск", "морская пехота", "вдв", "сухопутн")),
    ("svo", ("сво",)),
)
UNSUPPORTED_PRODUCT_MARKERS = (
    "головной убор",
    "подсумок",
    "патронташ",
    "фартук",
    "платок",
    "фальшпогон",
    "коврик",
    "юбка",
)


@dataclass
class InternalSkuProposal:
    source_marketplace: str
    source_id: str
    source_secondary_id: str
    product_name: str
    mapping_status: str
    current_internal_product_id: str
    proposed_internal_sku: str = ""
    proposal_status: str = ""
    confidence: str = ""
    product_prefix: str = ""
    pack_qty: str = ""
    purpose_prefix: str = ""
    theme_prefix: str = ""
    structure_prefix: str = ""
    content_prefix: str = ""
    number_scope: str = ""
    reasons: str = ""
    notes: str = ""


@dataclass
class SkuParts:
    product_prefix: str = ""
    pack_qty: int = 1
    purpose_prefix: str = ""
    theme_prefix: str = ""
    structure_prefix: str = ""
    content_prefix: str = ""
    status: str = "auto_candidate"
    reasons: list[str] | None = None
    notes: list[str] | None = None


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


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _normalize_title(value: str) -> str:
    return value.lower().replace("ё", "е")


def _first_match(text: str, patterns: tuple[tuple[str, tuple[str, ...]], ...]) -> str:
    for value, markers in patterns:
        if _contains_any(text, markers):
            return value
    return ""


def _extract_pack_qty(text: str) -> tuple[int, list[str]]:
    reasons: list[str] = []
    match = re.search(r"(?:комплект[^\d]{0,20}|набор[^\d]{0,20})?(\d{1,2})\s*(?:шт|штук|штуки)", text)
    if match:
        qty = max(1, int(match.group(1)))
        if qty > 1:
            reasons.append("pack_qty_from_title")
        return qty, reasons
    if "комплект" in text or "набор" in text:
        return 1, ["kit_without_qty_needs_review"]
    return 1, reasons


def _infer_product_prefix(text: str) -> tuple[str, str]:
    if _contains_any(text, UNSUPPORTED_PRODUCT_MARKERS):
        return "", "unsupported_product_type"
    if "петлиц" in text:
        return "loop", "product_type_loop_from_title"
    if "нашив" in text and "шеврон" not in text:
        return "nash", "product_type_nash_from_title"
    if any(marker in text for marker in ("шеврон", "патч", "patch", "позывн")):
        return "chev", "product_type_chev_from_title"
    return "", "product_type_unknown"


def _infer_purpose(text: str, product_prefix: str, pack_qty: int) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if product_prefix == "loop":
        return "", reasons
    if pack_qty > 2:
        reasons.append("kit3_plus_purpose_skipped")
        return "", reasons
    if "на спину" in text or "наспин" in text:
        return "back", ["purpose_back_from_title"]
    if "на кепк" in text or re.search(r"\b80\s*[xх*]\s*50\b", text):
        return "kp", ["purpose_kp_from_title_or_size"]
    if "нагруд" in text or "на груд" in text or re.search(r"\b1[23]\d\s*[xх*]\s*[234]\d\b", text):
        return "ng", ["purpose_ng_from_title_or_size"]
    if "нарукав" in text:
        return "nr", ["purpose_nr_from_title"]
    if re.search(r"\b(?:80|85|90)\s*[xх*]\s*(?:80|85|90|95|96|100)\b", text):
        return "nr", ["purpose_nr_from_size_family"]
    return "", reasons


def _infer_content(text: str, purpose: str, structure: str, theme: str) -> tuple[str, list[str]]:
    if any(marker in text for marker in ("орел", "герб", "эмблем", "флаг", "череп", "картин")):
        return "pict", ["content_pict_from_title"]
    if structure in {"mvd", "fsin", "fsb", "fso", "fssp", "rg"} and purpose == "nr":
        return "pict", ["content_pict_for_force_sleeve"]
    if theme in {"bpla", "berserk"}:
        return "pict", ["content_pict_from_theme"]
    return "text", ["content_text_default"]


def infer_sku_parts(row: dict[str, str]) -> SkuParts:
    title = _normalize_title(row.get("product_name", ""))
    reasons: list[str] = []
    notes: list[str] = []

    product_prefix, product_reason = _infer_product_prefix(title)
    reasons.append(product_reason)
    if not product_prefix:
        return SkuParts(
            status="unsupported_product_type"
            if product_reason == "unsupported_product_type"
            else "needs_owner_review",
            reasons=reasons,
            notes=["no_supported_product_prefix"],
        )

    pack_qty, pack_reasons = _extract_pack_qty(title)
    reasons.extend(pack_reasons)
    if "kit_without_qty_needs_review" in pack_reasons:
        notes.append("kit_qty_unknown")

    structure = _first_match(title, STRUCTURE_PATTERNS)
    if structure:
        reasons.append(f"structure_{structure}_from_title")
    elif "обществен" in title:
        structure = "oborg"
        reasons.append("structure_oborg_from_title")

    is_callsign = "позывн" in title

    theme = _first_match(title, THEME_PATTERNS)
    if theme:
        reasons.append(f"theme_{theme}_from_title")
    elif product_prefix in SUPPORTED_PRODUCT_PREFIXES and not structure and not is_callsign:
        theme = "raz"
        reasons.append("theme_raz_fallback")

    purpose, purpose_reasons = _infer_purpose(title, product_prefix, pack_qty)
    reasons.extend(purpose_reasons)
    if not purpose and product_prefix != "loop" and pack_qty <= 2:
        notes.append("purpose_unknown")

    if is_callsign:
        if pack_qty > 1:
            purpose = "pz"
        elif purpose:
            purpose = f"pz_{purpose}"
        else:
            purpose = "pz"
        if theme:
            theme = ""
            reasons.append("theme_skipped_for_callsign")
        reasons.append("purpose_pz_from_title")

    if product_prefix == "loop":
        if not structure:
            notes.append("loop_structure_unknown")
        content = ""
    else:
        content, content_reasons = _infer_content(title, purpose, structure, theme)
        reasons.extend(content_reasons)

    status = "auto_candidate"
    if notes:
        status = "needs_owner_review"
    return SkuParts(
        product_prefix=product_prefix,
        pack_qty=pack_qty,
        purpose_prefix=purpose,
        theme_prefix=theme,
        structure_prefix=structure,
        content_prefix=content,
        status=status,
        reasons=reasons,
        notes=notes,
    )


def number_scope_from_parts(parts: SkuParts) -> str:
    if parts.product_prefix == "loop":
        blocks = ["loop", parts.structure_prefix]
        return "_".join(block for block in blocks if block) + "_"

    blocks = [parts.product_prefix]
    if parts.pack_qty > 1:
        blocks.append(f"kit{parts.pack_qty}")
    if parts.purpose_prefix:
        blocks.extend(parts.purpose_prefix.split("_"))
    if parts.theme_prefix:
        blocks.append(parts.theme_prefix)
    if parts.structure_prefix:
        blocks.append(parts.structure_prefix)
    if parts.content_prefix:
        blocks.append(parts.content_prefix)
    return "_".join(block for block in blocks if block)


def _scope_and_number(internal_sku: str) -> tuple[str, int] | None:
    match = re.match(r"^(.*?)(\d{4})$", internal_sku)
    if not match:
        return None
    return match.group(1), int(match.group(2))


def _existing_scope_numbers(rows: list[dict[str, str]]) -> dict[str, int]:
    numbers: dict[str, int] = {}
    for row in rows:
        internal_sku = normalize_sku(row.get("internal_sku"))
        if not internal_sku:
            continue
        parsed = _scope_and_number(internal_sku)
        if not parsed:
            continue
        scope, number = parsed
        numbers[scope] = max(numbers.get(scope, 0), number)
    return numbers


def _source_fields(row: dict[str, str]) -> tuple[str, str, str]:
    status = row.get("mapping_status", "")
    if status == "ozon_only":
        return "ozon", row.get("ozon_offer_id", ""), row.get("ozon_sku", "")
    if status == "wb_only":
        return "wb", row.get("wb_vendor_code", ""), row.get("wb_nm_id", "")
    return "", "", ""


def build_internal_sku_plan(
    *,
    products: list[dict[str, str]],
) -> tuple[list[InternalSkuProposal], dict[str, Any]]:
    scope_numbers = _existing_scope_numbers(products)
    used_skus = {
        normalize_sku(row.get("internal_sku"))
        for row in products
        if normalize_sku(row.get("internal_sku"))
    }
    confirmed_titles = {
        _normalize_title(row.get("product_name", ""))
        for row in products
        if row.get("mapping_status") == "confirmed" and row.get("product_name")
    }
    proposals: list[InternalSkuProposal] = []
    marketplace_only_rows = [
        row for row in products if row.get("mapping_status") in {"ozon_only", "wb_only"}
    ]
    marketplace_only_title_counts = Counter(
        _normalize_title(row.get("product_name", ""))
        for row in marketplace_only_rows
        if row.get("product_name")
    )

    for row in marketplace_only_rows:
        source_marketplace, source_id, source_secondary_id = _source_fields(row)
        parts = infer_sku_parts(row)
        scope = number_scope_from_parts(parts) if parts.product_prefix else ""
        proposed_sku = ""
        proposal_status = parts.status
        notes = list(parts.notes or [])
        normalized_title = _normalize_title(row.get("product_name", ""))

        if normalized_title in confirmed_titles:
            proposal_status = "needs_owner_review"
            notes.append("title_matches_confirmed_product")
        if marketplace_only_title_counts.get(normalized_title, 0) > 1:
            proposal_status = (
                "needs_owner_review"
                if proposal_status == "auto_candidate"
                else proposal_status
            )
            notes.append("title_repeated_in_marketplace_only")

        if scope and proposal_status in {"auto_candidate", "needs_owner_review"}:
            next_number = scope_numbers.get(scope, 0) + 1
            proposed_sku = f"{scope}{next_number:04d}"
            scope_numbers[scope] = next_number
            if proposed_sku in used_skus:
                proposal_status = "conflict"
                notes.append("proposed_internal_sku_conflict")
            used_skus.add(proposed_sku)

        confidence = {
            "auto_candidate": "medium",
            "needs_owner_review": "low",
            "unsupported_product_type": "none",
            "conflict": "none",
        }.get(proposal_status, "low")

        proposals.append(
            InternalSkuProposal(
                source_marketplace=source_marketplace,
                source_id=source_id,
                source_secondary_id=source_secondary_id,
                product_name=row.get("product_name", ""),
                mapping_status=row.get("mapping_status", ""),
                current_internal_product_id=row.get("internal_product_id", ""),
                proposed_internal_sku=proposed_sku,
                proposal_status=proposal_status,
                confidence=confidence,
                product_prefix=parts.product_prefix,
                pack_qty=str(parts.pack_qty or ""),
                purpose_prefix=parts.purpose_prefix,
                theme_prefix=parts.theme_prefix,
                structure_prefix=parts.structure_prefix,
                content_prefix=parts.content_prefix,
                number_scope=scope,
                reasons=";".join(parts.reasons or []),
                notes=";".join(notes),
            )
        )

    counts = Counter(proposal.proposal_status for proposal in proposals)
    summary = {
        "input_products": len(products),
        "marketplace_only_products": len(marketplace_only_rows),
        "ozon_only_products": sum(
            1 for row in marketplace_only_rows if row.get("mapping_status") == "ozon_only"
        ),
        "wb_only_products": sum(1 for row in marketplace_only_rows if row.get("mapping_status") == "wb_only"),
        "auto_candidate_rows": counts.get("auto_candidate", 0),
        "needs_owner_review_rows": counts.get("needs_owner_review", 0),
        "unsupported_product_type_rows": counts.get("unsupported_product_type", 0),
        "conflict_rows": counts.get("conflict", 0),
        "proposed_internal_skus": sum(1 for proposal in proposals if proposal.proposed_internal_sku),
    }
    return proposals, summary


def _write_report(path: Path, *, run_id: str, started_at: datetime, result: dict[str, Any]) -> None:
    lines = [
        "# Internal SKU Assignment Plan",
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

    lines.extend(
        [
            "",
            "## Rules",
            "",
            "- This is a read-only proposal plan.",
            "- It does not change Ozon `offer_id` or WB `vendorCode`.",
            "- `auto_candidate` still requires owner review before assignment.",
            "- `unsupported_product_type` means current SKU rules do not cover the product type.",
            "",
            "## Artifacts",
            "",
        ]
    )
    for key, value in sorted(result["artifacts"].items()):
        lines.append(f"- `{key}`: `{value}`")
    path.write_text("\n".join(lines), encoding="utf-8")


def run_internal_sku_plan(
    *,
    data_dir: Path = Path("data"),
    products_path: Path | None = None,
    output_dir: Path | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    started_at = datetime.now()
    run_id = run_id or f"internal_sku_plan_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    products_path = products_path or data_dir / DEFAULT_UNIFIED_PRODUCTS_PATH
    output_dir = ensure_dir(output_dir or data_dir / DEFAULT_OUTPUT_DIR)

    errors: dict[str, str] = {}
    products: list[dict[str, str]] = []
    try:
        products = _read_csv(products_path)
    except Exception as exc:  # noqa: BLE001 - task report must capture local input failures
        errors["products"] = str(exc)

    proposals: list[InternalSkuProposal] = []
    summary: dict[str, Any] = {
        "input_products": len(products),
        "marketplace_only_products": 0,
        "ozon_only_products": 0,
        "wb_only_products": 0,
        "auto_candidate_rows": 0,
        "needs_owner_review_rows": 0,
        "unsupported_product_type_rows": 0,
        "conflict_rows": 0,
        "proposed_internal_skus": 0,
    }
    if not errors:
        proposals, summary = build_internal_sku_plan(products=products)

    plan_rows = [asdict(proposal) for proposal in proposals]
    plan_csv_path = output_dir / "internal_sku_assignment_plan.csv"
    plan_json_path = output_dir / "internal_sku_assignment_plan.json"
    report_path = run_dir / "internal_sku_assignment_report.md"
    summary_path = run_dir / "summary.json"
    if not errors:
        _write_dict_csv(plan_csv_path, plan_rows, PLAN_FIELDS)
        write_json(plan_json_path, plan_rows)

    overall_status = "error" if errors else "warning" if summary.get("conflict_rows") else "ok"
    artifacts = {
        "run_dir": str(run_dir),
        "plan_csv": str(plan_csv_path),
        "plan_json": str(plan_json_path),
        "report": str(report_path),
        "summary": str(summary_path),
        "run_manifest": str(run_dir / "manifest.json"),
    }
    result = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "read_only",
        "overall_status": overall_status,
        "summary": summary,
        "errors": errors,
        "inputs": {"products_path": str(products_path)},
        "artifacts": artifacts,
    }

    write_json(summary_path, result)
    _write_report(report_path, run_id=run_id, started_at=started_at, result=result)
    write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=result,
        task="catalog-internal-sku-plan",
        mode="read_only",
        risk="low",
        marketplaces=["ozon", "wb"],
        inputs=result["inputs"],
    )
    return result
