#!/usr/bin/env python3
"""Apply the first owner-approved WB dormant-stock liquidation stage."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from scripts.analysis.wb_dormant_inventory import _promo_rows  # noqa: E402
from scripts.pricing.wb_price_grid import (  # noqa: E402
    create_minimum_upload,
    file_sha256,
    minimum_is_active,
    minimum_number,
    normalized_price,
    parse_minimum_xlsx,
)
from seller_agent.config import load_credentials  # noqa: E402
from seller_agent.core.run_manifest import write_summary_run_manifest  # noqa: E402
from seller_agent.marketplaces.wb.prices_adapter import WbPricesAdapter  # noqa: E402
from seller_agent.marketplaces.wb.promotion_adapter import WbPromotionAdapter  # noqa: E402
from seller_agent.reports.writer import ensure_dir, write_json  # noqa: E402
from seller_agent.safety.approvals import (  # noqa: E402
    apply_marker_for,
    assert_apply_not_repeated,
    canonical_checksum,
    mark_approved_applied,
)
from seller_agent.tasks.status_preflight import run_status_preflight  # noqa: E402
from seller_agent.tasks.wb_actions_discount_apply import (  # noqa: E402
    _verify_current_discount_payload,
    _wb_upload_and_verify,
)
from seller_agent.tasks.wb_promotion_bids_apply import (  # noqa: E402
    _current_bids_from_campaigns,
    _group_bid_payload,
    _verify_applied_rows,
)


CRITICAL_COLUMNS = (
    "nm_id",
    "minimum_price",
    "target_minimum",
    "base_price",
    "current_discount",
    "current_price",
    "target_action_id",
    "target_discount",
    "target_price",
    "upload_discount_stage1",
    "upload_price_stage1",
    "requires_second_price_stage",
    "advert_id",
    "current_bid_recalc",
    "target_bid_recalc",
    "bid_action",
)
EXPECTED_ROWS = 163
EXPECTED_SECOND_STAGE = 18
EXPECTED_CPC_CHANGES = 113
EXPECTED_CPC_ADD = 1
PRICE_FIELDS = ("base", "discount", "discounted")


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--approved-run-dir", type=Path, required=True)
    parser.add_argument("--fresh-run-dir", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--confirmed-by-user", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--resume-cpc-run-dir", type=Path)
    parser.add_argument("--bid-verify-wait", type=int, default=45)
    parser.add_argument("--action-verify-attempts", type=int, default=4)
    parser.add_argument("--action-verify-delay", type=int, default=20)
    return parser.parse_args()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as source:
        return list(csv.DictReader(source))


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(rows[0]) if rows else ["empty"]
    with path.open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value or "0").strip().replace(",", "."))


def _int(value: Any) -> int:
    return int(_decimal(value))


def _bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "да"}


def _same_value(left: Any, right: Any) -> bool:
    if str(left or "").strip() == str(right or "").strip():
        return True
    try:
        return _decimal(left) == _decimal(right)
    except Exception:
        return False


def _approved_drift(
    approved: list[dict[str, str]],
    fresh: list[dict[str, str]],
) -> dict[str, Any]:
    approved_by_nm = {_int(row["nm_id"]): row for row in approved}
    fresh_by_nm = {_int(row["nm_id"]): row for row in fresh}
    drift: list[dict[str, Any]] = []
    for nm_id in sorted(set(approved_by_nm) | set(fresh_by_nm)):
        old = approved_by_nm.get(nm_id)
        new = fresh_by_nm.get(nm_id)
        if old is None or new is None:
            drift.append({"nm_id": nm_id, "field": "scope"})
            continue
        for field in CRITICAL_COLUMNS:
            if not _same_value(old.get(field), new.get(field)):
                drift.append(
                    {
                        "nm_id": nm_id,
                        "field": field,
                        "approved": old.get(field),
                        "fresh": new.get(field),
                    }
                )
    return {
        "status": "ok" if not drift else "blocked",
        "approved_rows": len(approved),
        "fresh_rows": len(fresh),
        "drift_rows": len({row["nm_id"] for row in drift}),
        "details": drift,
    }


def _run(command: list[str], *, timeout: int = 1200) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    result = {
        "returncode": completed.returncode,
        "stdout": completed.stdout[-12000:],
        "stderr": completed.stderr[-4000:],
    }
    if completed.returncode:
        raise RuntimeError(
            f"command failed ({completed.returncode}): "
            f"{(completed.stderr or completed.stdout).strip()[-2000:]}"
        )
    return result


def _download_minimum(run_dir: Path, label: str) -> Path:
    out_dir = ensure_dir(run_dir / "raw" / label)
    _run(
        [
            "node",
            "scripts/pricing/wb_autoaction_min_price_apply.js",
            "--mode",
            "download",
            "--out-dir",
            str(out_dir),
        ]
    )
    path = out_dir / "fresh_source.xlsx"
    if not path.exists():
        raise RuntimeError("fresh WB minimum-price workbook was not created")
    return path


def _prices_by_nm(adapter: WbPricesAdapter) -> dict[int, dict[str, Any]]:
    rows = adapter.fetch_goods_prices(limit=1000)
    result = {int(row["nmID"]): row for row in rows}
    if len(result) != len(rows):
        raise RuntimeError("duplicate nmID in WB prices response")
    return result


def _build_minimum_package(
    *,
    rows: list[dict[str, str]],
    current_prices: dict[int, dict[str, Any]],
    source_xlsx: Path,
    run_dir: Path,
    approved_hashes: dict[str, str],
) -> tuple[Path, str, Path]:
    source_rows = parse_minimum_xlsx(source_xlsx)
    source_by_nm: dict[int, dict[str, str]] = {}
    row_numbers: dict[int, int] = {}
    for row_number, row in enumerate(source_rows, start=2):
        nm_id = int(row["Артикул WB"])
        source_by_nm[nm_id] = row
        row_numbers[nm_id] = row_number

    plan_rows: list[dict[str, Any]] = []
    minimum_drift: list[dict[str, Any]] = []
    price_drift: list[dict[str, Any]] = []
    for row in rows:
        nm_id = _int(row["nm_id"])
        source = source_by_nm.get(nm_id)
        good = current_prices.get(nm_id)
        if source is None or good is None:
            raise RuntimeError(f"missing WB current row for nmID {nm_id}")
        current_minimum_text = source[
            "Текущая минимальная цена для применения скидки по автоакции"
        ]
        current_minimum = minimum_number(current_minimum_text)
        if current_minimum != _int(row["minimum_price"]):
            minimum_drift.append(
                {
                    "nm_id": nm_id,
                    "approved": _int(row["minimum_price"]),
                    "fresh": current_minimum_text,
                }
            )
        current = normalized_price(good)
        expected = {
            "base": _int(row["base_price"]),
            "discount": _int(row["current_discount"]),
            "discounted": _int(row["current_price"]),
        }
        if current != expected:
            price_drift.append(
                {"nm_id": nm_id, "approved": expected, "fresh": current}
            )
        plan_rows.append(
            {
                "nm_id": nm_id,
                "internal_sku": row["internal_sku"],
                "vendor_code": row["vendor_code"],
                "pack_qty": _int(row["pack_qty"]),
                "category": source["Категория"],
                "current_base": current["base"],
                "current_discount": current["discount"],
                "current_discounted": current["discounted"],
                "current_minimum_text": current_minimum_text,
                "current_minimum": current_minimum,
                "current_minimum_active": minimum_is_active(current_minimum_text),
                "target_base": current["base"],
                "target_discount": _int(row["upload_discount_stage1"]),
                "target_discounted": _int(row["upload_price_stage1"]),
                "target_minimum": _int(row["target_minimum"]),
                "price_change": current["discount"]
                != _int(row["upload_discount_stage1"]),
                "minimum_refresh": current_minimum
                != _int(row["target_minimum"]),
            }
        )
    if minimum_drift or price_drift:
        write_json(
            run_dir / "processed" / "live_baseline_drift.json",
            {"minimum": minimum_drift, "prices": price_drift},
        )
        raise RuntimeError(
            f"live WB baseline drift: minimum={len(minimum_drift)}, "
            f"prices={len(price_drift)}"
        )

    upload_path = run_dir / "processed" / "minimum_upload.xlsx"
    create_minimum_upload(
        source=source_xlsx,
        target=upload_path,
        row_numbers=row_numbers,
        rows=plan_rows,
    )
    plan = {
        "schema": "wb_price_grid_plan.v1",
        "created_at": datetime.now().astimezone().isoformat(),
        "marketplace": "wb",
        "account": "Vital Shevron",
        "category": "Декор для одежды",
        "price_grid": "owner-approved zero-margin liquidation",
        "target_discount": "per-product",
        "source_minimum_xlsx_sha256": file_sha256(source_xlsx),
        "minimum_upload_xlsx_sha256": file_sha256(upload_path),
        "approved_sources": approved_hashes,
        "summary": {
            "target_rows": len(plan_rows),
            "price_change_rows": sum(bool(row["price_change"]) for row in plan_rows),
            "minimum_refresh_rows": sum(
                bool(row["minimum_refresh"]) for row in plan_rows
            ),
        },
        "rows": plan_rows,
    }
    plan_path = run_dir / "processed" / "minimum_price_plan.json"
    write_json(plan_path, plan)
    return plan_path, file_sha256(plan_path), upload_path


def _apply_minimum(
    *,
    plan_path: Path,
    plan_hash: str,
    upload_path: Path,
    run_dir: Path,
) -> dict[str, Any]:
    out_dir = ensure_dir(run_dir / "raw" / "minimum_apply")
    command = _run(
        [
            "node",
            "scripts/pricing/wb_autoaction_min_price_apply.js",
            "--mode",
            "apply",
            "--plan",
            str(plan_path),
            "--approval-sha",
            plan_hash,
            "--upload",
            str(upload_path),
            "--out-dir",
            str(out_dir),
        ]
    )
    result = _json(out_dir / "apply_result.json")
    write_json(
        run_dir / "processed" / "minimum_apply_command.json",
        {"returncode": command["returncode"]},
    )
    if result.get("status") != "ok":
        raise RuntimeError("WB minimum-price apply did not verify")
    return result


def _price_payload(rows: list[dict[str, str]], *, changes_only: bool) -> dict[str, Any]:
    data = []
    for row in rows:
        current = _int(row["current_discount"])
        target = _int(row["upload_discount_stage1"])
        if changes_only and current == target:
            continue
        data.append(
            {
                "nmID": _int(row["nm_id"]),
                "price": _int(row["base_price"]),
                "discount": target,
            }
        )
    return {"data": data}


def _verify_prices(
    *,
    rows: list[dict[str, str]],
    current: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    return _verify_current_discount_payload(
        target_payload=_price_payload(rows, changes_only=False),
        current_rows=current,
    )


def _verify_minimum_current(
    *,
    rows: list[dict[str, str]],
    source_xlsx: Path,
) -> dict[str, Any]:
    current = {
        int(row["Артикул WB"]): row for row in parse_minimum_xlsx(source_xlsx)
    }
    mismatches = []
    for row in rows:
        nm_id = _int(row["nm_id"])
        source = current.get(nm_id)
        if source is None:
            mismatches.append({"nm_id": nm_id, "reason": "missing"})
            continue
        text = source[
            "Текущая минимальная цена для применения скидки по автоакции"
        ]
        if (
            minimum_number(text) != _int(row["target_minimum"])
            or not minimum_is_active(text)
        ):
            mismatches.append(
                {
                    "nm_id": nm_id,
                    "expected": _int(row["target_minimum"]),
                    "actual": text,
                }
            )
    return {
        "status": "ok" if not mismatches else "blocked",
        "verified": len(rows) - len(mismatches),
        "target": len(rows),
        "mismatches": mismatches,
    }


def _apply_prices(
    *,
    rows: list[dict[str, str]],
    credentials: Any,
    run_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    current = _prices_by_nm(WbPricesAdapter(credentials.wb))
    drift = []
    for row in rows:
        nm_id = _int(row["nm_id"])
        actual = normalized_price(current[nm_id])
        expected = {
            "base": _int(row["base_price"]),
            "discount": _int(row["current_discount"]),
            "discounted": _int(row["current_price"]),
        }
        if any(actual[field] != expected[field] for field in PRICE_FIELDS):
            drift.append({"nm_id": nm_id, "expected": expected, "actual": actual})
    write_json(run_dir / "processed" / "price_drift_check.json", {"rows": drift})
    if drift:
        raise RuntimeError(f"WB price drift before stage 1: {len(drift)} rows")

    payload = _price_payload(rows, changes_only=True)
    write_json(run_dir / "processed" / "price_stage1_payload.json", payload)
    if len(payload["data"]) != 159:
        raise RuntimeError(f"expected 159 price changes, got {len(payload['data'])}")
    upload = _wb_upload_and_verify(
        payload=payload,
        token=credentials.wb.token,
        raw_dir=run_dir / "raw",
        label="price_stage1",
    )
    write_json(run_dir / "processed" / "price_stage1_upload.json", upload)
    if upload["verify_status"] != "ok":
        raise RuntimeError(
            f"WB price stage 1 upload status: {upload['verify_status']}"
        )
    current_after = _prices_by_nm(WbPricesAdapter(credentials.wb))
    verify = _verify_prices(rows=rows, current=current_after)
    write_json(run_dir / "processed" / "price_stage1_verify.json", verify)
    if verify["status"] != "ok":
        raise RuntimeError(
            f"WB price stage 1 verify failed: "
            f"{verify['matched_rows']}/{verify['expected_rows']}"
        )
    return upload, verify


def _campaigns(
    adapter: WbPromotionAdapter,
    rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    advert_ids = sorted({_int(row["advert_id"]) for row in rows})
    return adapter.fetch_campaigns(
        ids=advert_ids,
        statuses=[4, 9, 11],
        payment_type="cpc",
    )


def _apply_cpc(
    *,
    rows: list[dict[str, str]],
    credentials: Any,
    run_dir: Path,
    verify_wait: int,
    allow_existing_approved_add: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    adapter = WbPromotionAdapter(credentials.wb)
    campaigns_before = _campaigns(adapter, rows)
    write_json(
        run_dir / "raw" / "cpc_campaigns_before.json",
        {"adverts": campaigns_before},
    )
    current_bids = _current_bids_from_campaigns(campaigns_before)
    apply_rows: list[dict[str, Any]] = []
    add_rows: list[dict[str, str]] = []
    recovered_add_rows: list[dict[str, Any]] = []
    drift: list[dict[str, Any]] = []
    for row in rows:
        action = str(row["bid_action"]).strip()
        key = (str(_int(row["advert_id"])), str(_int(row["nm_id"])), "search")
        actual = current_bids.get(key)
        expected = _decimal(row["current_bid_recalc"]).quantize(Decimal("0.01"))
        target = _decimal(row["target_bid_recalc"]).quantize(Decimal("0.01"))
        if action == "add":
            if actual is not None:
                if allow_existing_approved_add:
                    recovered_add_rows.append(
                        {
                            "advert_id": key[0],
                            "nm_id": key[1],
                            "current_bid_place": "search",
                            "current_bid": str(actual),
                            "target_bid": str(target),
                            "target_bid_kopecks": int(target * 100),
                        }
                    )
                else:
                    drift.append(
                        {
                            "advert_id": key[0],
                            "nm_id": key[1],
                            "reason": "approved_add_is_already_member",
                            "actual_bid": str(actual),
                        }
                    )
            else:
                add_rows.append(row)
            continue
        if actual != expected:
            drift.append(
                {
                    "advert_id": key[0],
                    "nm_id": key[1],
                    "reason": "bid_drift",
                    "expected": str(expected),
                    "actual": str(actual or ""),
                }
            )
            continue
        if action in {"increase", "decrease"}:
            apply_rows.append(
                {
                    "advert_id": key[0],
                    "nm_id": key[1],
                    "current_bid_place": "search",
                    "current_bid": str(actual),
                    "target_bid": str(target),
                    "target_bid_kopecks": int(target * 100),
                }
            )
    write_json(
        run_dir / "processed" / "cpc_drift_check.json",
        {
            "status": "ok" if not drift else "blocked",
            "drift": drift,
            "existing_changes": len(apply_rows),
            "add_rows": len(add_rows),
            "recovered_add_rows": len(recovered_add_rows),
        },
    )
    if drift:
        raise RuntimeError(f"WB CPC drift before apply: {len(drift)} rows")
    if len(add_rows) + len(recovered_add_rows) != EXPECTED_CPC_ADD:
        raise RuntimeError(
            "expected one CPC add or recovered-add row, got "
            f"{len(add_rows) + len(recovered_add_rows)}"
        )

    add_response: dict[str, Any] = {"status": "already_added_verified"}
    if add_rows:
        add_row = add_rows[0]
        add_response = adapter.update_campaign_products(
            advert_id=_int(add_row["advert_id"]),
            add=[_int(add_row["nm_id"])],
        )
        write_json(run_dir / "raw" / "cpc_add_response.json", add_response)
        add_key = (
            str(_int(add_row["advert_id"])),
            str(_int(add_row["nm_id"])),
            "search",
        )
        bids_after_add: dict[tuple[str, str, str], Decimal] = {}
        for attempt in range(1, 7):
            time.sleep(10)
            campaigns_after_add = _campaigns(adapter, rows)
            bids_after_add = _current_bids_from_campaigns(campaigns_after_add)
            if add_key in bids_after_add:
                break
        if add_key not in bids_after_add:
            raise RuntimeError("WB CPC product add was not visible after 60 seconds")
        target = _decimal(add_row["target_bid_recalc"]).quantize(Decimal("0.01"))
        recovered_add_rows.append(
            {
                "advert_id": add_key[0],
                "nm_id": add_key[1],
                "current_bid_place": "search",
                "current_bid": str(bids_after_add[add_key]),
                "target_bid": str(target),
                "target_bid_kopecks": int(target * 100),
            }
        )
    apply_rows.extend(recovered_add_rows)
    if len(apply_rows) != EXPECTED_CPC_CHANGES:
        raise RuntimeError(
            f"expected {EXPECTED_CPC_CHANGES} CPC changes, got {len(apply_rows)}"
        )
    payload = _group_bid_payload(apply_rows)
    write_json(run_dir / "processed" / "cpc_bid_payload.json", {"bids": payload})
    response = adapter.update_bids(payload)
    write_json(run_dir / "raw" / "cpc_bid_response.json", response)
    time.sleep(max(1, verify_wait))
    campaigns_after = _campaigns(adapter, rows)
    write_json(
        run_dir / "raw" / "cpc_campaigns_after.json",
        {"adverts": campaigns_after},
    )
    verify = _verify_applied_rows(
        campaigns=campaigns_after,
        apply_rows=apply_rows,
    )
    write_json(run_dir / "processed" / "cpc_verify.json", verify)
    if verify["status"] != "ok":
        raise RuntimeError(
            f"WB CPC verify failed on {len(verify['mismatches'])} rows"
        )
    return {
        "added": EXPECTED_CPC_ADD,
        "added_now": len(add_rows),
        "recovered_add": len(recovered_add_rows) if not add_rows else 0,
        "changed": len(apply_rows),
        "add_response": add_response,
        "response": response,
    }, verify


def _verify_actions(
    *,
    rows: list[dict[str, str]],
    run_dir: Path,
    attempts: int,
    delay: int,
) -> dict[str, Any]:
    target_rows = [
        row
        for row in rows
        if _int(row["target_action_id"]) > 0
        and not _bool(row["requires_second_price_stage"])
    ]
    history: list[dict[str, Any]] = []
    for attempt in range(1, attempts + 1):
        action_root = run_dir / "raw" / f"action_verify_{attempt:02d}"
        action_dir = action_root / "raw"
        prices_dir = action_root / "prices"
        ensure_dir(action_dir)
        ensure_dir(prices_dir)
        _run(
            [
                "node",
                "scripts/actions/wb_download_active_actions.js",
                "--date",
                datetime.now().strftime("%Y-%m-%d"),
                "--out-dir",
                str(action_dir),
                "--prices-dir",
                str(prices_dir),
            ]
        )
        by_nm = _promo_rows(action_root)
        missing = []
        for row in target_rows:
            nm_id = _int(row["nm_id"])
            action_id = _int(row["target_action_id"])
            matches = [
                item
                for item in by_nm.get(nm_id, [])
                if _int(item["action_id"]) == action_id
            ]
            if not any(item.get("currently_participates") for item in matches):
                missing.append({"nm_id": nm_id, "action_id": action_id})
        history.append(
            {
                "attempt": attempt,
                "target": len(target_rows),
                "confirmed": len(target_rows) - len(missing),
                "missing": missing,
            }
        )
        if not missing:
            break
        if attempt < attempts:
            time.sleep(delay)
    result = {
        "status": "ok" if history and not history[-1]["missing"] else "pending",
        "target": len(target_rows),
        "confirmed": history[-1]["confirmed"] if history else 0,
        "history": history,
    }
    write_json(run_dir / "processed" / "action_verify.json", result)
    return result


def _write_report(path: Path, result: dict[str, Any]) -> None:
    summary = result["summary"]
    path.write_text(
        "\n".join(
            [
                "# WB: первый этап распродажи залежалого остатка",
                "",
                f"- Статус: `{result['overall_status']}`.",
                f"- Согласовано карточек: `{summary['approved_rows']}`.",
                f"- Minimum применено и проверено: `{summary['minimum_verified']}`.",
                f"- Price stage 1: `{summary['price_uploaded']}` строк; "
                f"проверено `{summary['price_verified']}` карточки.",
                f"- CPC: добавлено `{summary['cpc_added']}`, "
                f"ставки изменены и проверены у `{summary['cpc_verified']}`.",
                f"- Автоакции после первого этапа: "
                f"`{summary['actions_confirmed']}/{summary['actions_target']}`.",
                f"- Второй price stage: `{summary['second_stage_pending']}` карточек; "
                "нужен новый fresh dry-run и отдельное согласование.",
                "- Другие товары, бюджеты, карточки и остатки не изменялись.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def main() -> int:
    args = _args()
    if not args.prepare_only and not args.confirmed_by_user:
        raise RuntimeError("explicit owner confirmation is required")
    approved_dir = args.approved_run_dir.resolve()
    fresh_dir = args.fresh_run_dir.resolve()
    approved_csv = approved_dir / "liquidation_plan.csv"
    fresh_csv = fresh_dir / "liquidation_plan.csv"
    approved_report = approved_dir / "report.html"
    approved_summary = approved_dir / "summary.json"
    for path in (approved_csv, fresh_csv, approved_report, approved_summary):
        if not path.exists():
            raise RuntimeError(f"required source is missing: {path}")

    approved_rows = _read_csv(approved_csv)
    fresh_rows = _read_csv(fresh_csv)
    drift = _approved_drift(approved_rows, fresh_rows)
    if drift["status"] != "ok":
        raise RuntimeError(
            f"fresh WB liquidation plan drifted on {drift['drift_rows']} products"
        )
    if len(approved_rows) != EXPECTED_ROWS:
        raise RuntimeError(f"expected {EXPECTED_ROWS} approved rows")
    second_stage = sum(
        _bool(row["requires_second_price_stage"]) for row in approved_rows
    )
    if second_stage != EXPECTED_SECOND_STAGE:
        raise RuntimeError(f"expected {EXPECTED_SECOND_STAGE} second-stage rows")

    started = datetime.now().astimezone()
    run_id = f"wb_dormant_liquidation_apply_stage1_{started.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(
        args.data_dir / "runs" / started.strftime("%Y-%m-%d") / run_id
    )
    raw_dir = ensure_dir(run_dir / "raw")
    processed_dir = ensure_dir(run_dir / "processed")
    write_json(processed_dir / "approved_fresh_drift.json", drift)
    approved_hashes = {
        "liquidation_plan_csv_sha256": file_sha256(approved_csv),
        "report_html_sha256": file_sha256(approved_report),
        "summary_json_sha256": file_sha256(approved_summary),
        "fresh_liquidation_plan_csv_sha256": file_sha256(fresh_csv),
    }
    approved_id = (
        f"{approved_dir.name}:stage1:"
        f"{approved_hashes['liquidation_plan_csv_sha256']}"
    )
    if not args.prepare_only:
        assert_apply_not_repeated(
            data_dir=args.data_dir,
            approved_id=approved_id,
            current_run_id=run_id,
        )

    credentials = load_credentials()
    if not credentials.wb:
        raise RuntimeError("WB credentials are not configured")
    preflight = run_status_preflight(
        credentials=credentials,
        data_dir=args.data_dir,
        include_lk=False,
        marketplaces=("wb",),
        include_ozon_performance=False,
    )
    write_json(processed_dir / "preflight.json", preflight)
    if preflight["overall_status"] != "ok":
        raise RuntimeError(f"WB preflight failed: {preflight['overall_status']}")

    if args.resume_cpc_run_dir:
        source_run = args.resume_cpc_run_dir.resolve()
        source_package = _json(source_run / "processed" / "apply_package.json")
        source_minimum = _json(source_run / "raw" / "minimum_apply" / "apply_result.json")
        source_price = _json(source_run / "processed" / "price_stage1_verify.json")
        if source_package.get("approved_id") != approved_id:
            raise RuntimeError("CPC recovery source approved id mismatch")
        package = source_package
        write_json(processed_dir / "apply_package.json", package)
        _write_csv(processed_dir / "approved_rows.csv", approved_rows)
        if source_minimum.get("status") != "ok" or source_price.get("status") != "ok":
            raise RuntimeError("CPC recovery source price/minimum stage was not verified")
        current_price_verify = _verify_prices(
            rows=approved_rows,
            current=_prices_by_nm(WbPricesAdapter(credentials.wb)),
        )
        write_json(
            processed_dir / "resume_price_stage1_verify.json",
            current_price_verify,
        )
        if current_price_verify["status"] != "ok":
            raise RuntimeError("WB stage 1 prices drifted before CPC recovery")
        minimum_current_xlsx = _download_minimum(run_dir, "resume_minimum_verify")
        current_minimum_verify = _verify_minimum_current(
            rows=approved_rows,
            source_xlsx=minimum_current_xlsx,
        )
        write_json(
            processed_dir / "resume_minimum_verify.json",
            current_minimum_verify,
        )
        if current_minimum_verify["status"] != "ok":
            raise RuntimeError("WB minimum prices drifted before CPC recovery")
        cpc_apply, cpc_verify = _apply_cpc(
            rows=approved_rows,
            credentials=credentials,
            run_dir=run_dir,
            verify_wait=args.bid_verify_wait,
            allow_existing_approved_add=True,
        )
        action_verify = _verify_actions(
            rows=approved_rows,
            run_dir=run_dir,
            attempts=args.action_verify_attempts,
            delay=args.action_verify_delay,
        )
        overall_status = (
            "warning" if action_verify["status"] == "ok" else "needs_attention"
        )
        summary = {
            "approved_rows": EXPECTED_ROWS,
            "minimum_verified": current_minimum_verify["verified"],
            "price_uploaded": _json(
                source_run / "processed" / "price_stage1_upload.json"
            )["payload_rows_count"],
            "price_verified": current_price_verify["matched_rows"],
            "cpc_added": cpc_apply["added"],
            "cpc_verified": cpc_verify["checked_rows"],
            "actions_confirmed": action_verify["confirmed"],
            "actions_target": action_verify["target"],
            "second_stage_pending": EXPECTED_SECOND_STAGE,
        }
        result = {
            "run_id": run_id,
            "started_at": started.isoformat(timespec="seconds"),
            "mode": "apply_recovery",
            "overall_status": overall_status,
            "approved_id": approved_id,
            "approved_checksum": package["checksum"],
            "source_stage1_run_id": source_run.name,
            "summary": summary,
            "cpc": {"apply": cpc_apply, "verify": cpc_verify},
            "actions": action_verify,
            "marketplace_write_performed": True,
            "artifacts": {
                "run_dir": str(run_dir),
                "summary": str(run_dir / "summary.json"),
                "report": str(run_dir / "result.md"),
                "apply_package": str(processed_dir / "apply_package.json"),
                "approved_rows": str(processed_dir / "approved_rows.csv"),
                "second_stage_source": str(approved_csv),
                "run_manifest": str(run_dir / "manifest.json"),
                "apply_marker": str(
                    apply_marker_for(data_dir=args.data_dir, approved_id=approved_id)
                ),
            },
        }
        write_json(run_dir / "summary.json", result)
        _write_report(run_dir / "result.md", result)
        manifest = write_summary_run_manifest(
            data_dir=args.data_dir,
            run_dir=run_dir,
            summary=result,
            task="wb-dormant-liquidation-apply-stage1-recovery",
            mode="apply",
            risk="high",
            marketplaces=["wb"],
            inputs={
                "approved_run_dir": str(approved_dir),
                "fresh_run_dir": str(fresh_dir),
                "resume_cpc_run_dir": str(source_run),
                "confirmed_by_user": True,
            },
            lifecycle_status=(
                "needs_attention"
                if overall_status == "needs_attention"
                else "verified"
            ),
            closed=False,
        )
        result["artifacts"].update(manifest)
        write_json(run_dir / "summary.json", result)
        mark_approved_applied(
            data_dir=args.data_dir,
            approved_id=approved_id,
            apply_run_id=run_id,
            task="wb-dormant-liquidation-apply-stage1-recovery",
            status=overall_status,
            run_manifest_path=manifest["manifest"],
            checksum=package["checksum"],
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if overall_status == "warning" else 2

    minimum_source = _download_minimum(run_dir, "minimum_before")
    current_prices = _prices_by_nm(WbPricesAdapter(credentials.wb))
    plan_path, plan_hash, minimum_upload = _build_minimum_package(
        rows=approved_rows,
        current_prices=current_prices,
        source_xlsx=minimum_source,
        run_dir=run_dir,
        approved_hashes=approved_hashes,
    )
    package = {
        "schema": "wb_dormant_liquidation_apply.v1",
        "approved_id": approved_id,
        "approved_hashes": approved_hashes,
        "minimum_plan_sha256": plan_hash,
        "minimum_upload_sha256": file_sha256(minimum_upload),
        "price_rows": EXPECTED_ROWS,
        "price_stage1_changes": 159,
        "second_stage_pending": EXPECTED_SECOND_STAGE,
        "cpc_changes": EXPECTED_CPC_CHANGES,
        "cpc_add": EXPECTED_CPC_ADD,
    }
    package["checksum"] = canonical_checksum(package)
    write_json(processed_dir / "apply_package.json", package)
    _write_csv(processed_dir / "approved_rows.csv", approved_rows)
    if args.prepare_only:
        result = {
            "run_id": run_id,
            "mode": "dry_run",
            "overall_status": "pending_review",
            "approved_id": approved_id,
            "package": package,
            "marketplace_write_performed": False,
        }
        write_json(run_dir / "summary.json", result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    minimum_result = _apply_minimum(
        plan_path=plan_path,
        plan_hash=plan_hash,
        upload_path=minimum_upload,
        run_dir=run_dir,
    )
    price_upload, price_verify = _apply_prices(
        rows=approved_rows,
        credentials=credentials,
        run_dir=run_dir,
    )
    cpc_apply, cpc_verify = _apply_cpc(
        rows=approved_rows,
        credentials=credentials,
        run_dir=run_dir,
        verify_wait=args.bid_verify_wait,
    )
    action_verify = _verify_actions(
        rows=approved_rows,
        run_dir=run_dir,
        attempts=args.action_verify_attempts,
        delay=args.action_verify_delay,
    )
    overall_status = "warning"
    if action_verify["status"] != "ok":
        overall_status = "needs_attention"
    summary = {
        "approved_rows": EXPECTED_ROWS,
        "minimum_verified": minimum_result["verify"]["verified"],
        "price_uploaded": price_upload["payload_rows_count"],
        "price_verified": price_verify["matched_rows"],
        "cpc_added": cpc_apply["added"],
        "cpc_verified": cpc_verify["checked_rows"],
        "actions_confirmed": action_verify["confirmed"],
        "actions_target": action_verify["target"],
        "second_stage_pending": EXPECTED_SECOND_STAGE,
    }
    result = {
        "run_id": run_id,
        "started_at": started.isoformat(timespec="seconds"),
        "mode": "apply",
        "overall_status": overall_status,
        "approved_id": approved_id,
        "approved_checksum": package["checksum"],
        "summary": summary,
        "minimum": minimum_result,
        "price_stage1": {
            "upload_id": price_upload["upload_id"],
            "verify_status": price_upload["verify_status"],
            "price_verify": price_verify["status"],
        },
        "cpc": {
            "apply": cpc_apply,
            "verify": cpc_verify,
        },
        "actions": action_verify,
        "marketplace_write_performed": True,
        "artifacts": {
            "run_dir": str(run_dir),
            "summary": str(run_dir / "summary.json"),
            "report": str(run_dir / "result.md"),
            "apply_package": str(processed_dir / "apply_package.json"),
            "approved_rows": str(processed_dir / "approved_rows.csv"),
            "second_stage_source": str(approved_csv),
            "run_manifest": str(run_dir / "manifest.json"),
            "apply_marker": str(
                apply_marker_for(data_dir=args.data_dir, approved_id=approved_id)
            ),
        },
    }
    write_json(run_dir / "summary.json", result)
    _write_report(run_dir / "result.md", result)
    manifest = write_summary_run_manifest(
        data_dir=args.data_dir,
        run_dir=run_dir,
        summary=result,
        task="wb-dormant-liquidation-apply-stage1",
        mode="apply",
        risk="high",
        marketplaces=["wb"],
        inputs={
            "approved_run_dir": str(approved_dir),
            "fresh_run_dir": str(fresh_dir),
            "confirmed_by_user": True,
        },
        lifecycle_status=(
            "needs_attention" if overall_status == "needs_attention" else "verified"
        ),
        closed=False,
    )
    result["artifacts"].update(manifest)
    write_json(run_dir / "summary.json", result)
    mark_approved_applied(
        data_dir=args.data_dir,
        approved_id=approved_id,
        apply_run_id=run_id,
        task="wb-dormant-liquidation-apply-stage1",
        status=overall_status,
        run_manifest_path=manifest["manifest"],
        checksum=package["checksum"],
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if overall_status == "warning" else 2


if __name__ == "__main__":
    raise SystemExit(main())
