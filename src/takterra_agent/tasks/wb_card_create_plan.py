from __future__ import annotations

from collections import Counter
import csv
from datetime import datetime
import html
import json
from pathlib import Path
import re
from typing import Any

from takterra_agent.config import AppCredentials
from takterra_agent.core.run_manifest import write_summary_run_manifest
from takterra_agent.marketplaces.ozon.adapter import OzonSellerAdapter
from takterra_agent.marketplaces.wb.adapter import WbContentAdapter
from takterra_agent.reports.writer import ensure_dir, write_json


WB_TITLE_MAX_LEN = 60
WB_BARCODE_PLACEHOLDER = "GENERATE_AT_APPLY"
DIMENSION_CHARACTERISTIC_IDS = {88952, 90745, 90846, 90849}
WB_FORBIDDEN_SYMBOL_PATTERN = re.compile(
    "["
    "\U00002600-\U000027BF"
    "\U0001F000-\U0001FAFF"
    "\U0000FE0E-\U0000FE0F"
    "\U0000200D"
    "]"
)


def _latest_raw_dir(data_dir: Path) -> Path:
    candidates = sorted((data_dir / "catalog" / "raw").glob("*/*"))
    if not candidates:
        raise FileNotFoundError("No catalog raw directories found")
    return candidates[-1]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _attr_values(item: dict[str, Any], attr_id: int) -> list[str]:
    for attr in item.get("attributes") or []:
        if attr.get("id") != attr_id:
            continue
        values: list[str] = []
        for value in attr.get("values") or []:
            raw_value = value.get("value") if isinstance(value, dict) else value
            if raw_value is not None and str(raw_value).strip():
                values.append(str(raw_value).strip())
        return values
    return []


def _attr_value(item: dict[str, Any], attr_id: int) -> str:
    values = _attr_values(item, attr_id)
    return values[0] if values else ""


def _plain_text(value: str) -> str:
    text = value or ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</?(p|div|section|article|h[1-6])[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<li[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</li>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"</?(ul|ol)[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = WB_FORBIDDEN_SYMBOL_PATTERN.sub("", text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _shorten_wb_title(title: str, max_len: int = WB_TITLE_MAX_LEN) -> tuple[str, bool]:
    title = " ".join((title or "").split())
    if len(title) <= max_len:
        return title, False

    shortened = ""
    for word in title.split():
        candidate = f"{shortened} {word}".strip()
        if len(candidate) > max_len:
            break
        shortened = candidate

    if not shortened:
        shortened = title[:max_len].rstrip()
    return shortened, True


def _cm_from_mm(value: Any) -> int:
    try:
        return max(1, round(float(value) / 10))
    except (TypeError, ValueError):
        return 1


def _kg_from_g(value: Any) -> float:
    try:
        return round(float(value) / 1000, 3)
    except (TypeError, ValueError):
        return 0.001


def _prefix_without_number(value: str) -> str:
    return re.sub(r"\d+$", "", value)


def _most_common(values: list[Any]) -> Any:
    if not values:
        return None
    return Counter(values).most_common(1)[0][0]


def _template_candidates(sku: str, wb_cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prefix = _prefix_without_number(sku).lower()
    return [
        card
        for card in wb_cards
        if str(card.get("vendorCode", "")).lower().startswith(prefix)
    ]


def _infer_wb_target(sku: str, wb_cards: list[dict[str, Any]]) -> dict[str, Any]:
    templates = _template_candidates(sku, wb_cards)
    if templates:
        imt_id = _most_common([card.get("imtID") for card in templates if card.get("imtID")])
        subject_id = _most_common([card.get("subjectID") for card in templates if card.get("subjectID")])
        subject_name = _most_common(
            [card.get("subjectName") for card in templates if card.get("subjectName")]
        )
        return {
            "action": "upload_add",
            "confidence": "high",
            "subject_id": subject_id,
            "subject_name": subject_name,
            "imt_id": imt_id,
            "template_vendor_codes": [card.get("vendorCode") for card in templates[:5]],
        }

    if sku.lower().startswith("chev_"):
        return {
            "action": "upload",
            "confidence": "medium",
            "subject_id": 2367,
            "subject_name": "Декор для одежды",
            "imt_id": None,
            "template_vendor_codes": [],
        }

    return {
        "action": "upload",
        "confidence": "low",
        "subject_id": 5517,
        "subject_name": "Аксессуары для оружия",
        "imt_id": None,
        "template_vendor_codes": [],
    }


def _colors(ozon_attrs: dict[str, Any]) -> list[str]:
    return [value.lower() for value in _attr_values(ozon_attrs, 10096)]


def _composition(ozon_attrs: dict[str, Any]) -> list[str]:
    return [value.lower() for value in _attr_values(ozon_attrs, 7405) or _attr_values(ozon_attrs, 5309)]


def _tnved(ozon_attrs: dict[str, Any]) -> str:
    value = _attr_value(ozon_attrs, 22232)
    return value.split(" - ", 1)[0].strip() if value else ""


def _dimensions(ozon_attrs: dict[str, Any]) -> dict[str, Any]:
    return {
        "length": _cm_from_mm(ozon_attrs.get("depth")),
        "width": _cm_from_mm(ozon_attrs.get("width")),
        "height": _cm_from_mm(ozon_attrs.get("height")),
        "weightBrutto": _kg_from_g(ozon_attrs.get("weight")),
    }


def _item_size_text(ozon_attrs: dict[str, Any]) -> str:
    value = _attr_value(ozon_attrs, 4382)
    if value:
        return value
    dimensions = _dimensions(ozon_attrs)
    return f"{dimensions['length']}x{dimensions['width']}x{dimensions['height']} см"


def _characteristics(
    ozon_attrs: dict[str, Any],
    subject_id: int,
    *,
    title: str,
    description: str,
    vendor_code: str,
) -> list[dict[str, Any]]:
    common = [
        {"id": 15000000, "value": title},
        {"id": 14177452, "value": description},
        {"id": 15003293, "value": [vendor_code]},
        {"id": 14177453, "value": [WB_BARCODE_PLACEHOLDER]},
        {"id": 14177449, "value": _colors(ozon_attrs)},
        {"id": 14177450, "value": _composition(ozon_attrs)},
        {"id": 14177451, "value": ["Россия"]},
        {"id": 179792, "value": ["1 шт."]},
        {"id": 15001405, "value": ["0"]},
    ]
    tnved = _tnved(ozon_attrs)
    if tnved:
        common.append({"id": 15000001, "value": [tnved]})

    if subject_id == 2367:
        common.extend(
            [
                {"id": 378533, "value": ["шеврон и липучка"]},
                {"id": 384944, "value": ["шеврон", "нашивка", "патч"]},
            ]
        )
    elif subject_id == 5517:
        common.extend(
            [
                {"id": 640, "value": ["охота", "туризм"]},
                {"id": 17596, "value": _composition(ozon_attrs)},
                {"id": 378533, "value": ["патронташ"]},
                {"id": 254882, "value": ["поясной"]},
                {"id": 15003951, "value": ["патронташ"]},
            ]
        )

    return [item for item in common if item["value"]]


def _filled_characteristic_ids(variant: dict[str, Any]) -> set[int]:
    return {
        int(item["id"])
        for item in variant.get("characteristics") or []
        if item.get("value") not in (None, "", [], {})
    }


def _missing_characteristics(
    variant: dict[str, Any],
    subject_characteristics: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    filled_ids = _filled_characteristic_ids(variant)
    missing: list[dict[str, Any]] = []
    for item in subject_characteristics:
        char_id = item.get("charcID")
        if char_id in filled_ids:
            continue
        if char_id in DIMENSION_CHARACTERISTIC_IDS:
            continue
        missing.append(
            {
                "id": char_id,
                "name": item.get("name"),
                "required": item.get("required"),
            }
        )
    return missing


def _price(ozon_info: dict[str, Any]) -> int:
    for key in ("old_price", "price", "min_price"):
        value = ozon_info.get(key)
        try:
            price = int(round(float(value)))
        except (TypeError, ValueError):
            continue
        if price > 0:
            return price
    return 0


def _draft_variant(
    sku: str,
    ozon_attrs: dict[str, Any],
    ozon_info: dict[str, Any],
    target: dict[str, Any],
) -> dict[str, Any]:
    original_title = _attr_value(ozon_attrs, 4180) or ozon_info.get("name") or sku
    title, _ = _shorten_wb_title(original_title)
    description = _plain_text(_attr_value(ozon_attrs, 4191))
    variant = {
        "vendorCode": sku,
        "title": title,
        "description": description,
        "brand": "",
        "dimensions": _dimensions(ozon_attrs),
        "characteristics": _characteristics(
            ozon_attrs,
            int(target["subject_id"]),
            title=title,
            description=description,
            vendor_code=sku,
        ),
        "sizes": [
            {
                "techSize": "0",
                "wbSize": "",
                "price": _price(ozon_info),
                "skus": [WB_BARCODE_PLACEHOLDER],
            }
        ],
    }
    return variant


def _images(ozon_attrs: dict[str, Any]) -> list[str]:
    result: list[str] = []
    primary = ozon_attrs.get("primary_image")
    if primary:
        result.append(str(primary))
    for value in ozon_attrs.get("images") or []:
        if value and str(value) not in result:
            result.append(str(value))
    return result


def run_wb_card_create_plan(
    *,
    credentials: AppCredentials,
    data_dir: Path = Path("data"),
    run_id: str | None = None,
) -> dict[str, Any]:
    if not credentials.ozon_seller:
        raise RuntimeError("Ozon Seller credentials are required")
    if not credentials.wb:
        raise RuntimeError("Wildberries credentials are required")

    started_at = datetime.now()
    run_id = run_id or f"wb_card_create_plan_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    raw_dir = _latest_raw_dir(data_dir)

    master_rows: list[dict[str, str]] = []
    with (data_dir / "catalog" / "processed" / "master_catalog.csv").open(
        encoding="utf-8"
    ) as handle:
        master_rows = list(csv.DictReader(handle))

    ozon_only = [row for row in master_rows if row.get("match_status") == "ozon_only"]
    offer_ids = [row["ozon_offer_id"] for row in ozon_only if row.get("ozon_offer_id")]

    ozon_info_items = _read_json(raw_dir / "ozon_product_info.json")
    ozon_info_by_offer = {str(item.get("offer_id")): item for item in ozon_info_items}
    wb_cards = _read_json(raw_dir / "wb_cards.json")

    ozon = OzonSellerAdapter(credentials.ozon_seller)
    wb = WbContentAdapter(credentials.wb)
    ozon_attrs_items = ozon.fetch_product_attributes(offer_ids)
    ozon_attrs_by_offer = {str(item.get("offer_id")): item for item in ozon_attrs_items}

    subject_ids = {2367, 5517}
    subject_charcs = {
        str(subject_id): wb.get_subject_characteristics(subject_id)
        for subject_id in sorted(subject_ids)
    }

    create_payload: list[dict[str, Any]] = []
    add_payloads: list[dict[str, Any]] = []
    plan_items: list[dict[str, Any]] = []

    for row in ozon_only:
        sku = row["master_sku"]
        target = _infer_wb_target(sku, wb_cards)
        attrs = ozon_attrs_by_offer.get(sku, {})
        info = ozon_info_by_offer.get(sku, {})
        variant = _draft_variant(sku, attrs, info, target)
        images = _images(attrs)
        original_title = _attr_value(attrs, 4180) or info.get("name") or sku
        wb_title, title_shortened = _shorten_wb_title(original_title)
        missing_characteristics = _missing_characteristics(
            variant,
            subject_charcs.get(str(target["subject_id"]), []),
        )

        item = {
            "master_sku": sku,
            "title": variant["title"],
            "ozon_title": original_title,
            "wb_title": wb_title,
            "title_shortened": title_shortened,
            "wb_title_max_len": WB_TITLE_MAX_LEN,
            "target": target,
            "draft_variant": variant,
            "images_from_ozon": images,
            "filled_characteristics_count": len(_filled_characteristic_ids(variant)),
            "subject_characteristics_count": len(
                subject_charcs.get(str(target["subject_id"]), [])
            ),
            "missing_characteristics": missing_characteristics,
            "needs_manual_review": target["confidence"] != "high"
            or bool(missing_characteristics),
            "manual_review_reason": ""
            if target["confidence"] == "high" and not missing_characteristics
            else "Confirm subject/category and fill or explicitly reject missing characteristics before apply",
        }
        plan_items.append(item)

        if target["action"] == "upload_add":
            add_payloads.append({"imtID": target["imt_id"], "cardsToAdd": [variant]})
        else:
            create_payload.append({"subjectID": target["subject_id"], "variants": [variant]})

    summary = {
        "plan_items": len(plan_items),
        "upload_add_items": sum(1 for item in plan_items if item["target"]["action"] == "upload_add"),
        "upload_items": sum(1 for item in plan_items if item["target"]["action"] == "upload"),
        "high_confidence_items": sum(1 for item in plan_items if item["target"]["confidence"] == "high"),
        "manual_review_items": sum(1 for item in plan_items if item["needs_manual_review"]),
        "title_shortened_items": sum(1 for item in plan_items if item["title_shortened"]),
        "media_upload_items": sum(1 for item in plan_items if item["images_from_ozon"]),
        "strict_card_rule": "copy all applicable Ozon fields, fill all WB characteristics, upload Ozon photos",
        "will_apply": False,
    }

    media_upload_plan = [
        {
            "vendorCode": item["master_sku"],
            "images": item["images_from_ozon"],
            "apply_note": "Call /content/v3/media/save after WB nmID is known",
        }
        for item in plan_items
    ]

    artifacts = {
        "run_dir": str(run_dir),
        "plan_json": str(run_dir / "wb_card_create_plan.json"),
        "upload_payload_draft": str(run_dir / "wb_cards_upload_payload_draft.json"),
        "upload_add_payload_draft": str(run_dir / "wb_cards_upload_add_payload_draft.json"),
        "media_upload_plan": str(run_dir / "wb_media_upload_plan.json"),
        "ozon_attributes": str(run_dir / "ozon_only_attributes.json"),
        "subject_characteristics": str(run_dir / "wb_subject_characteristics.json"),
        "report": str(run_dir / "wb_card_create_dry_run.md"),
        "summary": str(run_dir / "summary.json"),
        "run_manifest": str(run_dir / "manifest.json"),
    }

    write_json(run_dir / "wb_card_create_plan.json", plan_items)
    write_json(run_dir / "wb_cards_upload_payload_draft.json", create_payload)
    write_json(run_dir / "wb_cards_upload_add_payload_draft.json", add_payloads)
    write_json(run_dir / "wb_media_upload_plan.json", media_upload_plan)
    write_json(run_dir / "ozon_only_attributes.json", ozon_attrs_items)
    write_json(run_dir / "wb_subject_characteristics.json", subject_charcs)
    result = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "dry-run",
        "overall_status": "warning" if summary["manual_review_items"] else "ok",
        "pending_id": f"{run_id}_pending",
        "summary": summary,
        "artifacts": artifacts,
        "apply_performed": False,
    }
    write_json(run_dir / "summary.json", result)
    write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=result,
        task="wb-card-create-plan",
        mode="dry_run",
        risk="high",
        marketplaces=["ozon", "wb"],
        inputs={},
    )

    _write_report(run_dir / "wb_card_create_dry_run.md", run_id, summary, artifacts, plan_items)
    return result


def _write_report(
    path: Path,
    run_id: str,
    summary: dict[str, Any],
    artifacts: dict[str, str],
    plan_items: list[dict[str, Any]],
) -> None:
    lines = [
        "# WB Card Create Dry-Run",
        "",
        f"Run ID: `{run_id}`",
        "",
        "## Summary",
        "",
    ]
    for key in sorted(summary):
        lines.append(f"- `{key}`: {summary[key]}")

    lines.extend(["", "## Items", ""])
    for item in plan_items:
        target = item["target"]
        lines.extend(
            [
                f"### {item['master_sku']}",
                "",
                f"- title: {item['title']}",
                f"- Ozon title: {item['ozon_title']}",
                f"- WB title: {item['wb_title']}",
                f"- title shortened: `{item['title_shortened']}`",
                f"- action: `{target['action']}`",
                f"- confidence: `{target['confidence']}`",
                f"- subject: `{target['subject_id']}` {target['subject_name']}",
                f"- imtID: `{target['imt_id']}`",
                f"- template examples: `{', '.join(target['template_vendor_codes'])}`",
                f"- images from Ozon: {len(item['images_from_ozon'])}",
                f"- filled characteristics: {item['filled_characteristics_count']} / {item['subject_characteristics_count']}",
                f"- missing characteristics: {len(item['missing_characteristics'])}",
                f"- manual review: `{item['needs_manual_review']}`",
            ]
        )
        if item["manual_review_reason"]:
            lines.append(f"- review reason: {item['manual_review_reason']}")
        if item["missing_characteristics"]:
            missing = ", ".join(
                f"{value['id']} {value['name']}"
                for value in item["missing_characteristics"][:12]
            )
            lines.append(f"- missing examples: {missing}")
        lines.append("")

    lines.extend(
        [
            "## Safety",
            "",
            "- This is a dry-run only.",
            "- No request was sent to `/content/v2/cards/upload` or `/content/v2/cards/upload/add`.",
            "- `sizes.skus` is set to `GENERATE_AT_APPLY`; real WB barcodes must be generated only during approved apply.",
            "- Ozon photos are saved in the media upload plan; media upload is a separate apply step after WB `nmID` exists.",
            "- Apply is blocked until owner confirms that missing optional characteristics are either filled or not applicable.",
            "",
            "## Artifacts",
            "",
        ]
    )
    for key in sorted(artifacts):
        lines.append(f"- `{key}`: `{artifacts[key]}`")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
