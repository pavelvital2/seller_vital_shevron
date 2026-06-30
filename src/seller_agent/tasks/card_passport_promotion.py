from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any

from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.reports.writer import ensure_dir, write_json


OWNER_APPROVED_PREFIX = "owner_approved"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_text(value: Any) -> str:
    text = str(value or "").strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text


def _split_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_normalize_text(item) for item in value if _normalize_text(item)]
    return [_normalize_text(item) for item in re.split(r"[,;]", _normalize_text(value)) if _normalize_text(item)]


def _numbers(value: Any) -> list[float]:
    return [float(item.replace(",", ".")) for item in re.findall(r"\d+(?:[,.]\d+)?", _normalize_text(value))]


def _clean_size(value: Any, unit: str) -> str:
    text = _normalize_text(value)
    if not text:
        return ""
    return text if text.endswith(unit) else f"{text} {unit}"


def _as_int(value: Any, default: int = 0) -> int:
    nums = _numbers(value)
    return int(nums[0]) if nums else default


def _get_nested(data: dict[str, Any], *path: str) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _first_existing(*values: Any) -> str:
    for value in values:
        text = _normalize_text(value)
        if text:
            return text
    return ""


def _audit_internal_sku(audit: dict[str, Any]) -> str:
    return _first_existing(
        _get_nested(audit, "identity", "internal_sku"),
        _get_nested(audit, "proposed_final_card", "internal_sku"),
        audit.get("internal_sku"),
    )


def _find_audit_path(data_dir: Path, sku: str, audit_root: Path | None = None) -> Path | None:
    roots = [audit_root] if audit_root else [data_dir / "catalog" / "card_audits"]
    for root in roots:
        if not root or not root.exists():
            continue
        for path in sorted(root.rglob("audit.json")):
            try:
                audit = _read_json(path)
            except (OSError, json.JSONDecodeError):
                continue
            if _audit_internal_sku(audit) == sku:
                return path
    return None


def _owner_review_status(audit: dict[str, Any]) -> str:
    return _normalize_text(_get_nested(audit, "owner_review", "status") or _get_nested(audit, "approval", "status"))


def _is_owner_approved(audit: dict[str, Any]) -> bool:
    return _owner_review_status(audit).startswith(OWNER_APPROVED_PREFIX)


def _photo_url_map(audit: dict[str, Any]) -> dict[tuple[str, int], str]:
    media = audit.get("media") if isinstance(audit.get("media"), dict) else {}
    photos = media.get("photos") or media.get("photo_audit") or []
    result: dict[tuple[str, int], str] = {}
    for photo in photos if isinstance(photos, list) else []:
        if not isinstance(photo, dict):
            continue
        marketplace = _normalize_text(photo.get("marketplace")).lower()
        position = _as_int(photo.get("position"))
        url = _normalize_text(photo.get("source_url"))
        if marketplace and position and url:
            result[(marketplace, position)] = url
    return result


def _target_photo_set(audit: dict[str, Any], proposed: dict[str, Any]) -> list[dict[str, Any]]:
    value = proposed.get("target_marketplace_photo_set") or _get_nested(audit, "media", "target_marketplace_photo_set") or []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _media_assets(audit: dict[str, Any], proposed: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    urls = _photo_url_map(audit)
    warnings: list[str] = []
    assets: list[dict[str, Any]] = []
    for row in _target_photo_set(audit, proposed):
        position = _as_int(row.get("position"))
        source = _normalize_text(row.get("source"))
        source_lower = source.lower()
        marketplace = "wb" if "wb" in source_lower else "ozon"
        source_numbers = _numbers(source)
        source_position = int(source_numbers[-1]) if source_numbers else position
        url = urls.get((marketplace, source_position)) or urls.get(("ozon", position)) or urls.get(("wb", position))
        if not url:
            warnings.append(f"missing_photo_url:{source or position}")
            continue
        assets.append(
            {
                "position": position,
                "role": _first_existing(row.get("role"), row.get("name"), "additional"),
                "source": source or f"{marketplace.upper()} {source_position}",
                "marketplace": "wb_update_from_ozon" if marketplace == "ozon" else "wb_current",
                "note": _first_existing(row.get("note"), row.get("target")),
                "url": url,
            }
        )
    return assets, warnings


def _physical(proposed: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    source = proposed.get("target_physical_params") or proposed.get("target_physical_parameters") or {}
    errors: list[str] = []
    product_size = _first_existing(source.get("product_size_mm"), source.get("product_size"), proposed.get("product_size_mm"))
    ozon_package = _first_existing(source.get("ozon_package_mm"), source.get("ozon_package"))
    wb_package = _first_existing(source.get("wb_package_cm"), source.get("wb_package"))
    weight = _as_int(source.get("weight_g") or source.get("weight"), 0)
    pack_qty = _as_int(source.get("pack_qty") or source.get("units_in_one_product"), 1)
    if not product_size:
        errors.append("product_size_missing")
    if not ozon_package:
        errors.append("ozon_package_missing")
    if not wb_package:
        errors.append("wb_package_missing")
    if not weight:
        errors.append("weight_missing")
    return (
        {
            "product_size_mm": _clean_size(product_size, "мм"),
            "package_dimensions_ozon_mm": _clean_size(ozon_package, "мм"),
            "package_dimensions_wb_cm": _clean_size(wb_package, "см"),
            "item_weight_g": weight,
            "package_weight_g": weight,
            "pack_qty": pack_qty,
        },
        errors,
    )


def _content(proposed: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    title = _first_existing(proposed.get("canonical_title"), proposed.get("ozon_title"), proposed.get("wb_title"))
    description = _first_existing(
        proposed.get("canonical_description"),
        proposed.get("ozon_description"),
        proposed.get("wb_description"),
    )
    errors: list[str] = []
    if not title:
        errors.append("title_missing")
    if not description:
        errors.append("description_missing")
    blocks = proposed.get("description_blocks")
    if not isinstance(blocks, list) or not blocks:
        blocks = [part.strip() for part in description.split("\n\n") if part.strip()]
    return (
        {
            "canonical_title": title,
            "ozon_title": _first_existing(proposed.get("ozon_title"), title),
            "wb_title": _first_existing(proposed.get("wb_title"), title),
            "canonical_description": description,
            "ozon_description": _first_existing(proposed.get("ozon_description"), description),
            "wb_description": _first_existing(proposed.get("wb_description"), description),
            "description_blocks": blocks,
        },
        errors,
    )


def _colors(proposed: dict[str, Any]) -> list[str]:
    return _split_values(
        proposed.get("color")
        or proposed.get("colors")
        or _get_nested(proposed, "target_physical_parameters", "colors")
        or _get_nested(proposed, "target_physical_params", "colors")
    )


def _composition(proposed: dict[str, Any]) -> list[str]:
    return _split_values(
        proposed.get("composition")
        or _get_nested(proposed, "target_physical_parameters", "composition")
        or _get_nested(proposed, "target_physical_params", "composition")
        or ["полиэстер", "нейлон"]
    )


def _material(proposed: dict[str, Any]) -> str:
    return _first_existing(
        proposed.get("material"),
        _get_nested(proposed, "target_physical_parameters", "material"),
        _get_nested(proposed, "target_physical_params", "material"),
        "Габардин",
    )


def _field_value(data: dict[str, Any], *names: str) -> str:
    for name in names:
        value = data.get(name)
        if value:
            return _normalize_text(value)
    return ""


def _marketplace_attributes(
    *,
    proposed: dict[str, Any],
    content: dict[str, Any],
    physical: dict[str, Any],
    colors: list[str],
    color_name: str,
    material: str,
    composition: list[str],
    ozon_model_name: str,
    hashtags: str,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    pack_qty = str(physical["pack_qty"])
    composition_text = "; ".join(composition)
    ozon_attrs = [
        {"field": "Название", "value": content["ozon_title"]},
        {"field": "Аннотация", "value": content["ozon_description"]},
        {"field": "#Хештеги", "value": hashtags},
        {"field": "Бренд", "value": "VitalEmb"},
        {"field": "Название модели", "value": ozon_model_name},
        {"field": "Материал", "value": material},
        {"field": "Состав", "value": composition_text},
        {"field": "Размеры, мм", "value": physical["product_size_mm"]},
        {"field": "Габариты упаковки", "value": physical["package_dimensions_ozon_mm"]},
        {"field": "Вес с упаковкой, г", "value": str(physical["package_weight_g"])},
        {"field": "Количество в упаковке, шт", "value": pack_qty},
        {"field": "Единиц в одном товаре", "value": pack_qty},
        {"field": "Количество заводских упаковок", "value": "1"},
        {"field": "Цвет товара", "value": ", ".join(colors)},
        {"field": "Название цвета", "value": color_name},
        {"field": "Целевая аудитория", "value": "Взрослая"},
        {"field": "Вид выпуска товара", "value": "Фабричное производство"},
        {"field": "Страна-изготовитель", "value": "Россия"},
        {"field": "ТН ВЭД коды ЕАЭС", "value": "5810999000 - Прочие вышивки из прочих текстильных материалов"},
    ]
    wb_attrs = [
        {"field": "Наименование", "value": content["wb_title"]},
        {"field": "Описание", "value": content["wb_description"]},
        {"field": "Бренд", "value": "VitalEmb"},
        {"field": "Категория продавца", "value": "Декор для одежды"},
        {"field": "Цвет", "value": ", ".join(colors)},
        {"field": "Вид декора для одежды", "value": _field_value(proposed.get("wb_attributes") or {}, "Вид декора для одежды", "decor_type") or "шеврон"},
        {"field": "Состав", "value": composition_text},
        {"field": "Страна производства", "value": "Россия"},
        {"field": "Количество предметов в упаковке", "value": f"{pack_qty} шт."},
        {"field": "Комплектация", "value": f"шеврон на липучке {pack_qty} шт."},
        {"field": "ТНВЭД", "value": "5810999000"},
    ]
    return ozon_attrs, wb_attrs


def build_passport_from_audit(audit: dict[str, Any], audit_path: Path) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not _is_owner_approved(audit):
        return None, ["audit_not_owner_approved"], []
    proposed = audit.get("proposed_final_card")
    if not isinstance(proposed, dict) or not proposed:
        return None, ["proposed_final_card_missing"], []
    identity = audit.get("identity") if isinstance(audit.get("identity"), dict) else {}
    sku = _audit_internal_sku(audit)
    if not sku:
        errors.append("internal_sku_missing")
    content, content_errors = _content(proposed)
    physical, physical_errors = _physical(proposed)
    errors.extend(content_errors)
    errors.extend(physical_errors)
    colors = _colors(proposed)
    if not colors:
        errors.append("colors_missing")
    color_name = _first_existing(
        proposed.get("color_name"),
        _get_nested(proposed, "target_physical_parameters", "color_name"),
        _get_nested(proposed, "target_physical_params", "color_name"),
    )
    if not color_name:
        errors.append("color_name_missing")
    material = _material(proposed)
    composition = _composition(proposed)
    ozon_model_name = _first_existing(proposed.get("ozon_model_name"), _get_nested(proposed, "ozon_attributes", "Название модели"), _get_nested(proposed, "ozon_attributes", "model_name"))
    hashtags = _first_existing(proposed.get("ozon_hashtags"), _get_nested(proposed, "ozon_attributes", "#Хештеги"), _get_nested(proposed, "ozon_attributes", "hashtags"))
    media_assets, media_warnings = _media_assets(audit, proposed)
    warnings.extend(media_warnings)
    if errors:
        return None, errors, warnings
    ozon_attrs, wb_attrs = _marketplace_attributes(
        proposed=proposed,
        content=content,
        physical=physical,
        colors=colors,
        color_name=color_name,
        material=material,
        composition=composition,
        ozon_model_name=ozon_model_name,
        hashtags=hashtags,
    )
    owner_review = audit.get("owner_review") if isinstance(audit.get("owner_review"), dict) else {}
    approved_at = _first_existing(owner_review.get("approved_at"), owner_review.get("final_review_sent_at"), datetime.now().isoformat(timespec="seconds"))
    passport = {
        "$schema": "./master_product_passport_approved.schema.json",
        "approval": {
            "status": "owner_approved_pending_batch_apply",
            "approved_at": approved_at,
            "approved_by": _first_existing(owner_review.get("approved_by"), "owner_telegram_confirmation"),
            "source_audit_id": str(audit_path.parent.relative_to(audit_path.parents[2])) if len(audit_path.parents) > 2 else audit_path.parent.name,
            "source_review_html": _first_existing(owner_review.get("final_review_html_path"), owner_review.get("submitted_html_path")),
            "change_notes": "Promoted from owner-approved Layer 2 audit by штатный passport promotion command.",
            "owner_corrections": owner_review.get("corrections") or [],
            "marketplace_apply": {"status": "not_applied"},
        },
        "identity": {
            "internal_product_id": _first_existing(identity.get("internal_product_id"), sku),
            "internal_sku": sku,
            "marketplace_presence": _first_existing(identity.get("marketplace_presence"), "ozon_wb"),
            "ozon_offer_id": _first_existing(_get_nested(identity, "ozon", "offer_id"), identity.get("ozon_offer_id"), _get_nested(audit, "current_state", "ozon", "offer_id")),
            "ozon_offer_id_after_seller_sku_update": sku,
            "ozon_product_id": _first_existing(_get_nested(identity, "ozon", "product_id"), identity.get("ozon_product_id"), _get_nested(audit, "current_state", "ozon", "product_id")),
            "ozon_sku": _first_existing(_get_nested(identity, "ozon", "sku"), identity.get("ozon_sku"), _get_nested(audit, "current_state", "ozon", "sku")),
            "wb_vendor_code": _first_existing(_get_nested(identity, "wb", "vendor_code"), identity.get("wb_vendor_code"), _get_nested(audit, "current_state", "wb", "vendor_code")),
            "wb_vendor_code_after_seller_sku_update": sku,
            "wb_nm_id": _first_existing(_get_nested(identity, "wb", "nm_id"), identity.get("wb_nm_id"), _get_nested(audit, "current_state", "wb", "nm_id")),
            "wb_barcode": _first_existing(_get_nested(identity, "wb", "barcode"), identity.get("wb_barcode")),
        },
        "content": content,
        "physical": physical,
        "materials": {
            "material": material,
            "composition": composition,
            "attachment_type": "липучка Velcro: крючок пришит с обратной стороны, петля в комплекте",
        },
        "classification": {"tnved": "5810999000", "country_of_origin": "Россия"},
        "grouping": {
            "target_group_key": ozon_model_name,
            "decision": "owner approved grouping/model name; apply only through approved dry-run",
        },
        "media": {
            "designer_tasks": audit.get("designer_tasks") or [],
            "target_assets": media_assets,
            "target_marketplace_photo_set": _target_photo_set(audit, proposed),
        },
        "seo": {
            "search_queries": _get_nested(audit, "seo", "query_pack", "terms") or [],
            "ozon_hashtags": hashtags.split() if hashtags else [],
            "wb_tags": _first_existing(_get_nested(proposed, "wb_attributes", "wb_tags"), proposed.get("wb_tags")),
        },
        "ozon": {"attributes": ozon_attrs},
        "wb": {"attributes": wb_attrs},
        "safety": {
            "dangerous_actions": ["card_content_update", "seller_sku_update", "wb_media_update"],
            "approval_source": "owner-reviewed HTML and owner-approved Layer 2 audit",
        },
    }
    return passport, [], warnings


def run_promote_approved_card_passport(
    *,
    data_dir: Path = Path("data"),
    internal_skus: list[str] | None = None,
    audit_paths: list[Path] | None = None,
    audit_root: Path | None = None,
    run_id: str | None = None,
    write: bool = False,
    overwrite: bool = False,
) -> dict[str, Any]:
    started_at = datetime.now()
    run_id = run_id or f"promote_approved_card_passport_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    approved_dir = data_dir / "catalog" / "master_passport" / "approved"
    inputs = [sku.strip() for sku in (internal_skus or []) if sku and sku.strip()]
    explicit_paths = list(audit_paths or [])
    rows: list[dict[str, Any]] = []
    paths: list[Path] = []
    for path in explicit_paths:
        if path.exists():
            paths.append(path)
        else:
            rows.append({"audit_path": str(path), "status": "blocked", "errors": ["audit_path_not_found"]})
    for sku in inputs:
        path = _find_audit_path(data_dir, sku, audit_root=audit_root)
        if path:
            paths.append(path)
        else:
            rows.append({"internal_sku": sku, "status": "blocked", "errors": ["owner_approved_audit_not_found"]})
    seen: set[Path] = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        try:
            audit = _read_json(path)
        except json.JSONDecodeError as exc:
            rows.append({"audit_path": str(path), "status": "blocked", "errors": [f"invalid_json:{exc}"]})
            continue
        sku = _audit_internal_sku(audit)
        out_path = approved_dir / f"{sku}.json" if sku else approved_dir / "missing_sku.json"
        if out_path.exists() and not overwrite:
            rows.append({"internal_sku": sku, "audit_path": str(path), "passport_path": str(out_path), "status": "skipped_existing", "errors": [], "warnings": []})
            continue
        passport, errors, warnings = build_passport_from_audit(audit, path)
        if errors or not passport:
            rows.append({"internal_sku": sku, "audit_path": str(path), "passport_path": str(out_path), "status": "blocked", "errors": errors, "warnings": warnings})
            continue
        status = "ready_to_write"
        if write:
            ensure_dir(out_path.parent)
            write_json(out_path, passport)
            status = "written"
        rows.append({"internal_sku": sku, "audit_path": str(path), "passport_path": str(out_path), "status": status, "errors": [], "warnings": warnings})
    plan_path = run_dir / "passport_promotion_plan.json"
    write_json(plan_path, rows)
    written = sum(1 for row in rows if row["status"] == "written")
    ready = sum(1 for row in rows if row["status"] in {"written", "ready_to_write", "skipped_existing"})
    blocked = sum(1 for row in rows if row["status"] == "blocked")
    summary = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "apply" if write else "dry_run",
        "overall_status": "ok" if blocked == 0 else "warning",
        "input_rows": len(rows),
        "ready_rows": ready,
        "blocked_rows": blocked,
        "written_rows": written,
        "artifacts": {"run_dir": str(run_dir), "plan": str(plan_path), "summary": str(run_dir / "summary.json")},
    }
    write_json(run_dir / "summary.json", summary)
    manifest = write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=summary,
        task="promote-approved-card-passport",
        mode="apply" if write else "dry_run",
        risk="medium",
        marketplaces=["ozon", "wb"],
        inputs={"internal_skus": inputs, "audit_paths": [str(path) for path in explicit_paths], "write": write, "overwrite": overwrite},
        lifecycle_status="verified" if write and blocked == 0 else "pending_review",
        closed=write and blocked == 0,
    )
    summary["artifacts"]["run_manifest"] = manifest["manifest"]
    write_json(run_dir / "summary.json", summary)
    return summary


def ensure_approved_passports_for_batch(*, data_dir: Path, internal_skus: list[str], base_run_id: str) -> dict[str, Any]:
    approved_dir = data_dir / "catalog" / "master_passport" / "approved"
    missing = [sku for sku in internal_skus if not (approved_dir / f"{sku}.json").exists()]
    if not missing:
        return {"status": "ok", "missing_skus": [], "promotion": None}
    promotion = run_promote_approved_card_passport(
        data_dir=data_dir,
        internal_skus=missing,
        run_id=f"{base_run_id}_passport_promotion",
        write=True,
        overwrite=False,
    )
    plan_path = Path(promotion["artifacts"]["plan"])
    rows = _read_json(plan_path)
    still_missing = [
        sku
        for sku in missing
        if not (approved_dir / f"{sku}.json").exists()
    ]
    blocked = [row for row in rows if row.get("status") == "blocked"]
    return {
        "status": "ok" if not still_missing and not blocked else "blocked",
        "missing_skus": missing,
        "still_missing_skus": still_missing,
        "blocked": blocked,
        "promotion": promotion,
    }
