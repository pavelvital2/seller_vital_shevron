from __future__ import annotations

import csv
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from seller_agent.config import AppCredentials
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.reports.writer import ensure_dir, write_json
from seller_agent.tasks.card_content_update import (
    run_card_content_update_apply,
    run_card_content_update_plan,
)
from seller_agent.tasks.seller_sku_update import (
    run_seller_sku_update_apply,
    run_seller_sku_update_plan,
)
from seller_agent.tasks.wb_card_create_apply import run_wb_card_create_apply
from seller_agent.tasks.wb_card_create_plan import run_wb_card_create_plan


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    if not path.exists():
        return [], []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader), list(reader.fieldnames or [])


def _write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _normalize_text(value: Any) -> str:
    text = str(value or "").strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text


def _ready_skus_from_plan(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    plan = _read_json(path)
    ready: list[str] = []
    blocked: list[dict[str, Any]] = []
    for row in plan if isinstance(plan, list) else []:
        sku = _normalize_text(row.get("internal_sku") or row.get("master_sku"))
        if row.get("ready"):
            ready.append(sku)
        else:
            blocked.append({"internal_sku": sku, "errors": row.get("errors") or row})
    return [sku for sku in ready if sku], blocked


def _stage(status: str, **kwargs: Any) -> dict[str, Any]:
    return {"status": status, **kwargs}


def _run_content_stage(
    *,
    credentials: AppCredentials,
    data_dir: Path,
    internal_skus: list[str],
    base_run_id: str,
    wait_seconds: int,
    poll_interval: int,
) -> dict[str, Any]:
    plan = run_card_content_update_plan(
        credentials=credentials,
        data_dir=data_dir,
        internal_skus=internal_skus,
        run_id=f"{base_run_id}_content_plan_all",
        skip_api=False,
    )
    plan_path = Path(plan["artifacts"]["plan"])
    ready_skus, blocked = _ready_skus_from_plan(plan_path)
    if not ready_skus:
        return _stage("blocked", plan=plan, apply=None, ready_skus=[], blocked=blocked)
    apply_plan = plan
    apply_plan_run_id = plan["run_id"]
    if set(ready_skus) != set(internal_skus):
        apply_plan = run_card_content_update_plan(
            credentials=credentials,
            data_dir=data_dir,
            internal_skus=ready_skus,
            run_id=f"{base_run_id}_content_plan_ready",
            skip_api=False,
        )
        apply_plan_run_id = apply_plan["run_id"]
    apply = run_card_content_update_apply(
        credentials=credentials,
        data_dir=data_dir,
        plan_run_id=apply_plan_run_id,
        run_id=f"{base_run_id}_content_apply",
        confirmed_by_user=True,
        wait_seconds=wait_seconds,
        poll_interval=poll_interval,
    )
    passport_updates = _mark_content_passports_applied(
        data_dir=data_dir,
        plan_path=Path(apply_plan["artifacts"]["plan"]),
        run_id=str(apply["run_id"]),
    )
    status = "ok" if apply.get("overall_status") == "ok" else "warning"
    return _stage(
        status,
        plan=plan,
        apply=apply,
        ready_skus=ready_skus,
        blocked=blocked,
        passport_updates=passport_updates,
    )


def _run_seller_sku_stage(
    *,
    credentials: AppCredentials,
    data_dir: Path,
    internal_skus: list[str],
    base_run_id: str,
    wait_seconds: int,
    poll_interval: int,
) -> dict[str, Any]:
    plan = run_seller_sku_update_plan(
        credentials=credentials,
        data_dir=data_dir,
        internal_skus=internal_skus,
        run_id=f"{base_run_id}_seller_sku_plan_all",
        skip_api=False,
    )
    plan_path = Path(plan["artifacts"]["seller_sku_update_plan"])
    ready_skus, blocked = _ready_skus_from_plan(plan_path)
    if not ready_skus:
        return _stage("blocked", plan=plan, apply=None, ready_skus=[], blocked=blocked)
    apply_plan = plan
    apply_plan_run_id = plan["run_id"]
    if set(ready_skus) != set(internal_skus):
        apply_plan = run_seller_sku_update_plan(
            credentials=credentials,
            data_dir=data_dir,
            internal_skus=ready_skus,
            run_id=f"{base_run_id}_seller_sku_plan_ready",
            skip_api=False,
        )
        apply_plan_run_id = apply_plan["run_id"]
    apply = run_seller_sku_update_apply(
        credentials=credentials,
        data_dir=data_dir,
        plan_run_id=apply_plan_run_id,
        run_id=f"{base_run_id}_seller_sku_apply",
        confirmed_by_user=True,
        wait_seconds=wait_seconds,
        poll_interval=poll_interval,
        update_local_layers=True,
    )
    passport_updates = _update_seller_sku_passports(
        data_dir=data_dir,
        plan_path=Path(apply_plan["artifacts"]["seller_sku_update_plan"]),
        run_id=str(apply["run_id"]),
    )
    status = "ok" if apply.get("overall_status") == "ok" else "warning"
    return _stage(
        status,
        plan=plan,
        apply=apply,
        ready_skus=ready_skus,
        blocked=blocked,
        passport_updates=passport_updates,
    )


def _passport_wants_wb_create(data_dir: Path, sku: str) -> bool:
    path = data_dir / "catalog" / "master_passport" / "approved" / f"{sku}.json"
    if not path.exists():
        return False
    passport = _read_json(path)
    identity = passport.get("identity") or {}
    if _normalize_text(identity.get("wb_nm_id")):
        return False
    return True


def _run_wb_create_stage(
    *,
    credentials: AppCredentials,
    data_dir: Path,
    internal_skus: list[str],
    base_run_id: str,
    wait_seconds: int,
    poll_interval: int,
) -> dict[str, Any]:
    create_skus = [sku for sku in internal_skus if _passport_wants_wb_create(data_dir, sku)]
    if not create_skus:
        return _stage("skipped", plan=None, apply=None, ready_skus=[], blocked=[])
    plan = run_wb_card_create_plan(
        credentials=credentials,
        data_dir=data_dir,
        internal_skus=create_skus,
        run_id=f"{base_run_id}_wb_create_plan",
    )
    plan_items = int((plan.get("summary") or {}).get("plan_items") or 0)
    manual_review_items = int((plan.get("summary") or {}).get("manual_review_items") or 0)
    if plan_items <= 0:
        return _stage("skipped", plan=plan, apply=None, ready_skus=[], blocked=[])
    if manual_review_items:
        return _stage("blocked", plan=plan, apply=None, ready_skus=[], blocked=[{"reason": "wb_create_manual_review_items"}])
    apply = run_wb_card_create_apply(
        credentials=credentials,
        data_dir=data_dir,
        plan_run_id=plan["run_id"],
        run_id=f"{base_run_id}_wb_create_apply",
        confirmed_by_user=True,
        allow_manual_review=True,
        wait_seconds=wait_seconds,
        poll_interval=poll_interval,
    )
    local_updates = _update_wb_create_local_layers(
        data_dir=data_dir,
        apply_run_dir=Path(apply["artifacts"]["run_dir"]),
        run_id=str(apply["run_id"]),
    )
    status = "ok" if apply.get("overall_status") == "ok" else "warning"
    return _stage(status, plan=plan, apply=apply, ready_skus=create_skus, blocked=[], local_updates=local_updates)


def _update_json_rows(path: Path, key_names: tuple[str, ...], values: dict[str, dict[str, str]]) -> int:
    if not path.exists():
        return 0
    data = _read_json(path)
    if not isinstance(data, list):
        return 0
    updated = 0
    for row in data:
        if not isinstance(row, dict):
            continue
        key = next((_normalize_text(row.get(name)) for name in key_names if _normalize_text(row.get(name)) in values), "")
        if not key:
            continue
        update = dict(values[key])
        if "notes" in update:
            update["notes"] = _append_note(str(row.get("notes") or ""), update["notes"])
        row.update(update)
        updated += 1
    write_json(path, data)
    return updated


def _update_csv_rows(path: Path, key_names: tuple[str, ...], values: dict[str, dict[str, str]]) -> int:
    rows, fieldnames = _read_csv(path)
    if not rows:
        return 0
    for value_map in values.values():
        for field in value_map:
            if field not in fieldnames:
                fieldnames.append(field)
    updated = 0
    for row in rows:
        key = next((_normalize_text(row.get(name)) for name in key_names if _normalize_text(row.get(name)) in values), "")
        if not key:
            continue
        update = dict(values[key])
        if "notes" in update:
            update["notes"] = _append_note(str(row.get("notes") or ""), update["notes"])
        row.update(update)
        updated += 1
    _write_csv(path, rows, fieldnames)
    return updated


def _append_note(existing: str, note: str) -> str:
    parts = [part for part in str(existing or "").split(";") if part]
    if note not in parts:
        parts.append(note)
    return ";".join(parts)


def _merge_notes(existing: str, note: str, *, remove: tuple[str, ...] = ()) -> str:
    remove_set = set(remove)
    parts = [part for part in str(existing or "").split(";") if part and part not in remove_set]
    if note and note not in parts:
        parts.append(note)
    return ";".join(parts)


def _nested_text(payload: dict[str, Any], *path: str) -> str:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
    return _normalize_text(current)


def _catalog_aliases_for_passport(passport: dict[str, Any], sku: str) -> tuple[set[str], set[str]]:
    aliases = {
        sku,
        _nested_text(passport, "identity", "internal_sku"),
        _nested_text(passport, "identity", "master_sku"),
        _nested_text(passport, "identity", "ozon_offer_id"),
        _nested_text(passport, "identity", "ozon_product_id"),
        _nested_text(passport, "identity", "ozon_sku"),
        _nested_text(passport, "identity", "wb_vendor_code"),
        _nested_text(passport, "identity", "wb_nm_id"),
        _nested_text(passport, "ozon", "offer_id_before_seller_sku_update"),
        _nested_text(passport, "ozon", "offer_id_after_seller_sku_update"),
        _nested_text(passport, "wb", "vendor_code_before_seller_sku_update"),
        _nested_text(passport, "wb", "vendor_code_after_seller_sku_update"),
        _nested_text(passport, "wb", "vendor_code"),
        _nested_text(passport, "wb", "nm_id"),
    }
    old_wb_aliases = {
        _nested_text(passport, "wb", "vendor_code_before_seller_sku_update"),
    }
    return {alias for alias in aliases if alias}, {alias for alias in old_wb_aliases if alias and alias != sku}


def _catalog_sync_values_from_passport(passport: dict[str, Any], sku: str, run_id: str) -> dict[str, str] | None:
    identity = passport.get("identity") if isinstance(passport.get("identity"), dict) else {}
    wb = passport.get("wb") if isinstance(passport.get("wb"), dict) else {}
    ozon = passport.get("ozon") if isinstance(passport.get("ozon"), dict) else {}
    ozon_offer_id = _normalize_text(identity.get("ozon_offer_id") or ozon.get("offer_id_after_seller_sku_update") or sku)
    ozon_product_id = _normalize_text(identity.get("ozon_product_id") or ozon.get("product_id"))
    ozon_sku = _normalize_text(identity.get("ozon_sku") or ozon.get("sku"))
    wb_vendor_code = _normalize_text(identity.get("wb_vendor_code") or wb.get("vendor_code") or sku)
    wb_nm_id = _normalize_text(identity.get("wb_nm_id") or wb.get("nm_id"))
    wb_barcode = _normalize_text(identity.get("wb_barcode") or wb.get("barcode"))
    if not ozon_offer_id or not wb_vendor_code or not wb_nm_id:
        return None
    wb_status = _normalize_text(wb.get("transfer_status") or wb.get("export_status") or "present_verified")
    if wb_status == "created_on_wb_verified":
        transfer_direction = "done_created_on_wb"
        note = f"wb_created:{run_id}"
    else:
        transfer_direction = "done_seller_sku_unified"
        note = f"catalog_sync:{run_id}"
    return {
        "internal_sku": sku,
        "master_sku": sku,
        "ozon_offer_id": ozon_offer_id,
        "ozon_product_id": ozon_product_id,
        "ozon_sku": ozon_sku,
        "wb_vendor_code": wb_vendor_code,
        "wb_nm_id": wb_nm_id,
        "wb_barcode": wb_barcode,
        "active_ozon": "true",
        "active_wb": "true",
        "mapping_status": "ozon_wb",
        "marketplace_presence": "ozon_wb",
        "match_status": "ozon_wb",
        "status_ozon": "present_verified",
        "status_wb": wb_status,
        "wb_status": wb_status,
        "transfer_direction": transfer_direction,
        "full_snapshot_status": "both_found",
        "content_review_priority": "owner_approved_applied",
        "next_content_step": "Контролировать модерацию/видимость WB и позиции после индексации.",
        "notes": note,
    }


def _row_alias_match(row: dict[str, Any], key_names: tuple[str, ...], alias_to_sku: dict[str, str]) -> tuple[str, str]:
    for name in key_names:
        value = _normalize_text(row.get(name))
        if value and value in alias_to_sku:
            return alias_to_sku[value], value
    return "", ""


def _row_has_alias(row: dict[str, Any], key_names: tuple[str, ...], aliases: set[str]) -> bool:
    return any(_normalize_text(row.get(name)) in aliases for name in key_names)


def _update_json_rows_by_alias(
    path: Path,
    key_names: tuple[str, ...],
    values: dict[str, dict[str, str]],
    aliases: dict[str, set[str]],
    *,
    remove_duplicate_aliases: dict[str, set[str]] | None = None,
) -> dict[str, int]:
    if not path.exists():
        return {"updated": 0, "removed": 0}
    data = _read_json(path)
    if not isinstance(data, list):
        return {"updated": 0, "removed": 0}
    alias_to_sku = {alias: sku for sku, sku_aliases in aliases.items() for alias in sku_aliases}
    remove_duplicate_aliases = remove_duplicate_aliases or {}
    has_main_row: set[str] = set()
    matches: list[tuple[int, str, str]] = []
    for idx, row in enumerate(data):
        if not isinstance(row, dict):
            continue
        sku, alias = _row_alias_match(row, key_names, alias_to_sku)
        if not sku:
            continue
        matches.append((idx, sku, alias))
        if not _row_has_alias(row, key_names, remove_duplicate_aliases.get(sku, set())):
            has_main_row.add(sku)

    updated = 0
    remove_indices: set[int] = set()
    stale_notes = ("not_found_in_wb", "not_found_in_ozon")
    for idx, sku, _alias in matches:
        row = data[idx]
        if (
            sku in has_main_row
            and _row_has_alias(row, key_names, remove_duplicate_aliases.get(sku, set()))
            and not _normalize_text(row.get("ozon_offer_id") or row.get("ozon_product_id") or row.get("ozon_sku"))
        ):
            remove_indices.add(idx)
            continue
        update = dict(values[sku])
        if "notes" in update:
            update["notes"] = _merge_notes(str(row.get("notes") or ""), update["notes"], remove=stale_notes)
        row.update(update)
        updated += 1
    if remove_indices:
        data = [row for idx, row in enumerate(data) if idx not in remove_indices]
    write_json(path, data)
    return {"updated": updated, "removed": len(remove_indices)}


def _update_csv_rows_by_alias(
    path: Path,
    key_names: tuple[str, ...],
    values: dict[str, dict[str, str]],
    aliases: dict[str, set[str]],
    *,
    remove_duplicate_aliases: dict[str, set[str]] | None = None,
) -> dict[str, int]:
    rows, fieldnames = _read_csv(path)
    if not rows:
        return {"updated": 0, "removed": 0}
    for value_map in values.values():
        for field in value_map:
            if field not in fieldnames:
                fieldnames.append(field)
    alias_to_sku = {alias: sku for sku, sku_aliases in aliases.items() for alias in sku_aliases}
    remove_duplicate_aliases = remove_duplicate_aliases or {}
    has_main_row: set[str] = set()
    matches: list[tuple[int, str, str]] = []
    for idx, row in enumerate(rows):
        sku, alias = _row_alias_match(row, key_names, alias_to_sku)
        if not sku:
            continue
        matches.append((idx, sku, alias))
        if not _row_has_alias(row, key_names, remove_duplicate_aliases.get(sku, set())):
            has_main_row.add(sku)

    updated = 0
    remove_indices: set[int] = set()
    stale_notes = ("not_found_in_wb", "not_found_in_ozon")
    for idx, sku, _alias in matches:
        row = rows[idx]
        if (
            sku in has_main_row
            and _row_has_alias(row, key_names, remove_duplicate_aliases.get(sku, set()))
            and not _normalize_text(row.get("ozon_offer_id") or row.get("ozon_product_id") or row.get("ozon_sku"))
        ):
            remove_indices.add(idx)
            continue
        update = dict(values[sku])
        if "notes" in update:
            update["notes"] = _merge_notes(str(row.get("notes") or ""), update["notes"], remove=stale_notes)
        row.update(update)
        updated += 1
    if remove_indices:
        rows = [row for idx, row in enumerate(rows) if idx not in remove_indices]
    _write_csv(path, rows, fieldnames)
    return {"updated": updated, "removed": len(remove_indices)}


def _preserve_wb_barcodes_from_catalog_rows(
    *,
    data_dir: Path,
    values: dict[str, dict[str, str]],
    remove_duplicate_aliases: dict[str, set[str]],
) -> None:
    if not values or not remove_duplicate_aliases:
        return
    key_names = ("master_sku", "internal_sku", "ozon_offer_id", "ozon_product_id", "wb_vendor_code", "wb_nm_id")
    for rel_path in [
        "catalog/processed/master_catalog.csv",
        "catalog/processed/master_catalog.json",
        "catalog/unified/products.csv",
        "catalog/unified/products.json",
        "catalog/content/content_master.csv",
        "catalog/content/content_master.json",
    ]:
        path = data_dir / rel_path
        if not path.exists():
            continue
        rows: list[dict[str, Any]]
        if path.suffix == ".json":
            payload = _read_json(path)
            rows = payload if isinstance(payload, list) else []
        else:
            rows, _fieldnames = _read_csv(path)
        for row in rows:
            if not isinstance(row, dict):
                continue
            barcode = _normalize_text(row.get("wb_barcode"))
            if not barcode:
                continue
            for sku, duplicate_aliases in remove_duplicate_aliases.items():
                if values.get(sku, {}).get("wb_barcode"):
                    continue
                if _row_has_alias(row, key_names, duplicate_aliases):
                    values[sku]["wb_barcode"] = barcode


def _sync_approved_card_catalog_layers(*, data_dir: Path, internal_skus: list[str], run_id: str) -> dict[str, Any]:
    values: dict[str, dict[str, str]] = {}
    aliases: dict[str, set[str]] = {}
    remove_duplicate_aliases: dict[str, set[str]] = {}
    for sku in internal_skus:
        passport_path = data_dir / "catalog" / "master_passport" / "approved" / f"{sku}.json"
        if not passport_path.exists():
            continue
        passport = _read_json(passport_path)
        value_map = _catalog_sync_values_from_passport(passport, sku, run_id)
        if not value_map:
            continue
        sku_aliases, old_wb_aliases = _catalog_aliases_for_passport(passport, sku)
        aliases[sku] = sku_aliases
        values[sku] = value_map
        if old_wb_aliases:
            remove_duplicate_aliases[sku] = old_wb_aliases
    if not values:
        return {"status": "skipped", "reason": "no_completed_ozon_wb_passports"}

    _preserve_wb_barcodes_from_catalog_rows(
        data_dir=data_dir,
        values=values,
        remove_duplicate_aliases=remove_duplicate_aliases,
    )

    key_names = ("master_sku", "internal_sku", "ozon_offer_id", "ozon_product_id", "ozon_sku", "wb_vendor_code", "wb_nm_id")
    updates: dict[str, Any] = {
        "products_csv": _update_csv_rows_by_alias(
            data_dir / "catalog" / "unified" / "products.csv",
            key_names,
            values,
            aliases,
            remove_duplicate_aliases=remove_duplicate_aliases,
        ),
        "content_master_csv": _update_csv_rows_by_alias(
            data_dir / "catalog" / "content" / "content_master.csv",
            key_names,
            values,
            aliases,
            remove_duplicate_aliases=remove_duplicate_aliases,
        ),
        "master_catalog_csv": _update_csv_rows_by_alias(
            data_dir / "catalog" / "processed" / "master_catalog.csv",
            key_names,
            values,
            aliases,
            remove_duplicate_aliases=remove_duplicate_aliases,
        ),
        "products_json": _update_json_rows_by_alias(
            data_dir / "catalog" / "unified" / "products.json",
            key_names,
            values,
            aliases,
            remove_duplicate_aliases=remove_duplicate_aliases,
        ),
        "content_master_json": _update_json_rows_by_alias(
            data_dir / "catalog" / "content" / "content_master.json",
            key_names,
            values,
            aliases,
            remove_duplicate_aliases=remove_duplicate_aliases,
        ),
        "master_catalog_json": _update_json_rows_by_alias(
            data_dir / "catalog" / "processed" / "master_catalog.json",
            key_names,
            values,
            aliases,
            remove_duplicate_aliases=remove_duplicate_aliases,
        ),
    }
    passport_updates = 0
    for sku, value_map in values.items():
        passport_path = data_dir / "catalog" / "master_passport" / "approved" / f"{sku}.json"
        if not passport_path.exists():
            continue
        passport = _read_json(passport_path)
        identity = passport.setdefault("identity", {})
        wb = passport.setdefault("wb", {})
        identity["marketplace_presence"] = "ozon_wb"
        identity["wb_barcode"] = value_map.get("wb_barcode", "")
        wb["barcode"] = value_map.get("wb_barcode", "")
        approval = passport.setdefault("approval", {}).setdefault("marketplace_apply", {})
        approval["catalog_sync_run_id"] = run_id
        approval["status"] = "applied_verified"
        write_json(passport_path, passport)
        passport_updates += 1
    updates["passports"] = passport_updates
    updates["status"] = "ok"
    return updates


def _update_seller_sku_passports(*, data_dir: Path, plan_path: Path, run_id: str) -> dict[str, int]:
    if not plan_path.exists():
        return {}
    plan = _read_json(plan_path)
    updated = 0
    for row in plan if isinstance(plan, list) else []:
        if not row.get("ready"):
            continue
        sku = _normalize_text(row.get("internal_sku"))
        if not sku:
            continue
        passport_path = data_dir / "catalog" / "master_passport" / "approved" / f"{sku}.json"
        if not passport_path.exists():
            continue
        passport = _read_json(passport_path)
        identity = passport.setdefault("identity", {})
        ozon_op = row.get("ozon") if isinstance(row.get("ozon"), dict) else {}
        wb_op = row.get("wb") if isinstance(row.get("wb"), dict) else {}
        if ozon_op:
            identity["ozon_offer_id"] = _normalize_text(ozon_op.get("new_offer_id") or sku)
            ozon = passport.setdefault("ozon", {})
            ozon["offer_id_before_seller_sku_update"] = _normalize_text(ozon_op.get("old_offer_id"))
            ozon["offer_id_after_seller_sku_update"] = _normalize_text(ozon_op.get("new_offer_id") or sku)
            ozon["seller_sku_update_status"] = "applied_verified_by_product_id"
        if wb_op:
            identity["wb_vendor_code"] = _normalize_text(wb_op.get("new_vendor_code") or sku)
            wb = passport.setdefault("wb", {})
            wb["vendor_code"] = _normalize_text(wb_op.get("new_vendor_code") or sku)
            wb["seller_sku_update_status"] = "applied_verified"
        approval = passport.setdefault("approval", {}).setdefault("marketplace_apply", {})
        approval["seller_sku_update_run_id"] = run_id
        approval["status"] = "applied_verified"
        write_json(passport_path, passport)
        updated += 1
    return {"passports": updated}


def _mark_content_passports_applied(*, data_dir: Path, plan_path: Path, run_id: str) -> dict[str, int]:
    if not plan_path.exists():
        return {}
    plan = _read_json(plan_path)
    updated = 0
    for row in plan if isinstance(plan, list) else []:
        if not row.get("ready"):
            continue
        sku = _normalize_text(row.get("internal_sku"))
        if not sku:
            continue
        passport_path = data_dir / "catalog" / "master_passport" / "approved" / f"{sku}.json"
        if not passport_path.exists():
            continue
        passport = _read_json(passport_path)
        approval = passport.setdefault("approval", {}).setdefault("marketplace_apply", {})
        approval["content_update_run_id"] = run_id
        approval["status"] = "content_applied"
        write_json(passport_path, passport)
        updated += 1
    return {"passports": updated}


def _update_wb_create_local_layers(*, data_dir: Path, apply_run_dir: Path, run_id: str) -> dict[str, int]:
    found_path = apply_run_dir / "found_cards_after_apply.json"
    source_path = apply_run_dir / "source_plan_items.json"
    barcode_path = apply_run_dir / "barcode_by_vendor.json"
    if not found_path.exists() or not source_path.exists():
        return {}
    found_cards = _read_json(found_path)
    source_items = _read_json(source_path)
    barcodes = _read_json(barcode_path) if barcode_path.exists() else {}
    values: dict[str, dict[str, str]] = {}
    for item in source_items if isinstance(source_items, list) else []:
        sku = _normalize_text(item.get("master_sku"))
        if not sku:
            continue
        card = (found_cards or {}).get(sku) or {}
        nm_id = _normalize_text(card.get("nmID") or card.get("nmId"))
        barcode = _normalize_text((barcodes or {}).get(sku))
        if not barcode:
            for size in card.get("sizes") or []:
                skus = size.get("skus") or []
                if skus:
                    barcode = _normalize_text(skus[0])
                    break
        note = f"wb_created:{run_id}"
        values[sku] = {
            "wb_vendor_code": sku,
            "wb_nm_id": nm_id,
            "wb_barcode": barcode,
            "active_wb": "true",
            "mapping_status": "ozon_wb",
            "marketplace_presence": "ozon_wb",
            "match_status": "ozon_wb",
            "status_wb": "created_verified",
            "wb_status": "created_verified",
            "transfer_direction": "done_created_on_wb",
            "full_snapshot_status": "both_found",
            "content_review_priority": "owner_approved_applied",
            "next_content_step": "Контролировать модерацию/видимость WB и позиции после индексации.",
            "notes": note,
        }

    updates = {
        "products_csv": _update_csv_rows(
            data_dir / "catalog" / "unified" / "products.csv",
            ("internal_sku", "master_sku", "ozon_offer_id"),
            values,
        ),
        "content_master_csv": _update_csv_rows(
            data_dir / "catalog" / "content" / "content_master.csv",
            ("internal_sku", "master_sku", "ozon_offer_id"),
            values,
        ),
        "master_catalog_csv": _update_csv_rows(
            data_dir / "catalog" / "processed" / "master_catalog.csv",
            ("master_sku", "internal_sku", "ozon_offer_id"),
            values,
        ),
        "products_json": _update_json_rows(
            data_dir / "catalog" / "unified" / "products.json",
            ("internal_sku", "master_sku", "ozon_offer_id"),
            values,
        ),
        "content_master_json": _update_json_rows(
            data_dir / "catalog" / "content" / "content_master.json",
            ("internal_sku", "master_sku", "ozon_offer_id"),
            values,
        ),
        "master_catalog_json": _update_json_rows(
            data_dir / "catalog" / "processed" / "master_catalog.json",
            ("master_sku", "internal_sku", "ozon_offer_id"),
            values,
        ),
    }
    for sku, value_map in values.items():
        passport_path = data_dir / "catalog" / "master_passport" / "approved" / f"{sku}.json"
        if not passport_path.exists():
            continue
        passport = _read_json(passport_path)
        identity = passport.setdefault("identity", {})
        identity.update(
            {
                "marketplace_presence": "ozon_wb",
                "wb_vendor_code": sku,
                "wb_nm_id": value_map.get("wb_nm_id", ""),
                "wb_barcode": value_map.get("wb_barcode", ""),
            }
        )
        wb = passport.setdefault("wb", {})
        wb.update(
            {
                "transfer_status": "created_on_wb_verified",
                "export_status": "created_on_wb_verified",
                "created_apply_run_id": run_id,
                "vendor_code": sku,
                "nm_id": value_map.get("wb_nm_id", ""),
                "barcode": value_map.get("wb_barcode", ""),
            }
        )
        approval = passport.setdefault("approval", {}).setdefault("marketplace_apply", {})
        approval.update({"status": "applied_verified", "wb_card_create_run_id": run_id})
        write_json(passport_path, passport)
        updates["passports"] = updates.get("passports", 0) + 1
    write_json(apply_run_dir / "local_layer_update_summary.json", {"updated": updates})
    return updates


def run_apply_approved_cards(
    *,
    credentials: AppCredentials,
    data_dir: Path = Path("data"),
    internal_skus: list[str] | None = None,
    run_id: str | None = None,
    confirmed_by_user: bool = False,
    content_wait_seconds: int = 180,
    content_poll_interval: int = 10,
    seller_sku_wait_seconds: int = 60,
    seller_sku_poll_interval: int = 5,
    wb_create_wait_seconds: int = 600,
    wb_create_poll_interval: int = 30,
) -> dict[str, Any]:
    if not confirmed_by_user:
        raise RuntimeError("Apply requires explicit user confirmation")
    skus = [sku.strip() for sku in (internal_skus or []) if sku and sku.strip()]
    if not skus:
        raise ValueError("no internal_skus")
    started_at = datetime.now()
    base_run_id = run_id or f"apply_approved_cards_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / base_run_id)

    content = _run_content_stage(
        credentials=credentials,
        data_dir=data_dir,
        internal_skus=skus,
        base_run_id=base_run_id,
        wait_seconds=content_wait_seconds,
        poll_interval=content_poll_interval,
    )
    content_ready = content.get("ready_skus") or []

    seller_sku = _run_seller_sku_stage(
        credentials=credentials,
        data_dir=data_dir,
        internal_skus=content_ready or skus,
        base_run_id=base_run_id,
        wait_seconds=seller_sku_wait_seconds,
        poll_interval=seller_sku_poll_interval,
    )
    seller_ready = seller_sku.get("ready_skus") or []

    wb_create = _run_wb_create_stage(
        credentials=credentials,
        data_dir=data_dir,
        internal_skus=seller_ready or content_ready or skus,
        base_run_id=base_run_id,
        wait_seconds=wb_create_wait_seconds,
        poll_interval=wb_create_poll_interval,
    )

    catalog_sync = _sync_approved_card_catalog_layers(
        data_dir=data_dir,
        internal_skus=seller_ready or content_ready or skus,
        run_id=base_run_id,
    )

    stage_statuses = [content["status"], seller_sku["status"], wb_create["status"]]
    overall_status = "ok"
    if any(status in {"blocked", "error"} for status in stage_statuses):
        overall_status = "warning"
    elif any(status == "warning" for status in stage_statuses):
        overall_status = "warning"

    result = {
        "run_id": base_run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "apply",
        "task": "apply-approved-cards",
        "overall_status": overall_status,
        "input_skus": skus,
        "summary": {
            "input_skus": len(skus),
            "content_ready": len(content_ready),
            "seller_sku_ready": len(seller_ready),
            "wb_create_ready": len(wb_create.get("ready_skus") or []),
            "content_status": content["status"],
            "seller_sku_status": seller_sku["status"],
            "wb_create_status": wb_create["status"],
            "catalog_sync_status": catalog_sync.get("status", "unknown"),
        },
        "stages": {
            "content_update": content,
            "seller_sku_update": seller_sku,
            "wb_card_create": wb_create,
        },
        "local_catalog_sync": catalog_sync,
        "artifacts": {
            "run_dir": str(run_dir),
            "summary": str(run_dir / "summary.json"),
            "report": str(run_dir / "apply_approved_cards_report.md"),
        },
    }
    write_json(run_dir / "summary.json", result)
    _write_report(run_dir / "apply_approved_cards_report.md", result)
    manifest = write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=result,
        task="apply-approved-cards",
        mode="apply",
        risk="high",
        marketplaces=["ozon", "wb"],
        inputs={
            "internal_skus": skus,
            "content_wait_seconds": content_wait_seconds,
            "seller_sku_wait_seconds": seller_sku_wait_seconds,
            "wb_create_wait_seconds": wb_create_wait_seconds,
        },
        lifecycle_status="verified" if overall_status == "ok" else "applied",
        closed=overall_status == "ok",
    )
    result["artifacts"]["run_manifest"] = manifest["manifest"]
    write_json(run_dir / "summary.json", result)
    return result


def _write_report(path: Path, result: dict[str, Any]) -> None:
    lines = [
        "# Apply Approved Cards Batch",
        "",
        f"Run ID: `{result['run_id']}`",
        f"Status: `{result['overall_status']}`",
        "",
        "## Summary",
        "",
    ]
    for key, value in result["summary"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## SKUs", ""])
    for sku in result["input_skus"]:
        lines.append(f"- `{sku}`")
    lines.extend(["", "## Stage Run IDs", ""])
    for name, stage in result["stages"].items():
        lines.append(f"### {name}")
        lines.append(f"- status: `{stage.get('status')}`")
        plan = stage.get("plan") or {}
        apply = stage.get("apply") or {}
        if plan:
            lines.append(f"- plan: `{plan.get('run_id')}`")
        if apply:
            lines.append(f"- apply: `{apply.get('run_id')}`")
        if stage.get("blocked"):
            lines.append(f"- blocked: `{len(stage['blocked'])}`")
        lines.append("")
    catalog_sync = result.get("local_catalog_sync") or {}
    if catalog_sync:
        lines.extend(["## Local Catalog Sync", ""])
        lines.append(f"- status: `{catalog_sync.get('status')}`")
        for key, value in catalog_sync.items():
            if key == "status":
                continue
            lines.append(f"- `{key}`: `{value}`")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
