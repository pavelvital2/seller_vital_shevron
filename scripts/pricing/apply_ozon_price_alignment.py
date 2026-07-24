#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import time
from typing import Any
from zoneinfo import ZoneInfo

from seller_agent.config import load_credentials
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.marketplaces.ozon.adapter import OzonSellerAdapter
from seller_agent.reports.writer import ensure_dir, write_json
from seller_agent.safety.approvals import (
    apply_marker_for,
    assert_apply_not_repeated,
    canonical_checksum,
    mark_approved_applied,
)
from seller_agent.tasks.ozon_elastic_plan import (
    ACTIVE,
    _fetch_action_group,
    _fetch_actions,
    _pick_elastic_action,
)
from seller_agent.tasks.pricing_status import normalize_ozon_price_item
from seller_agent.tasks.status_preflight import run_status_preflight


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
MOSCOW = ZoneInfo("Europe/Moscow")
INT_FIELDS = {"pack_qty", "stock", "orders_30d"}
BOOL_FIELDS = {
    "elastic_active",
    "elastic_price_api_confirmed",
    "min_price_matches_grid",
}
FORBIDDEN_PAYLOAD_KEYS = {
    "min_price",
    "min_price_for_auto_actions_enabled",
    "manage_elastic_boosting_through_price",
    "auto_action_enabled",
    "auto_add_to_ozon_actions_list_enabled",
}


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply an approved Ozon price alignment package.")
    parser.add_argument("--plan-run-id", required=True)
    parser.add_argument("--data-dir", default=str(DATA_DIR))
    parser.add_argument("--confirmed-by-user", action="store_true")
    return parser.parse_args()


def _dec(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value).replace(" ", "").replace("\u00a0", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _money(value: Any) -> str:
    amount = _dec(value)
    if amount == amount.to_integral_value():
        return str(int(amount))
    return str(amount.quantize(Decimal("0.01")))


def _bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, Any]], *, empty_header: str) -> None:
    fields = list(rows[0]) if rows else [empty_header]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _plan_dir(data_dir: Path, run_id: str) -> Path:
    matches = sorted((data_dir / "runs").glob(f"*/{run_id}"))
    if not matches:
        raise FileNotFoundError(f"Approved plan not found: {run_id}")
    return matches[-1]


def _normalize_approved_row(row: dict[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in row.items():
        if key in INT_FIELDS:
            result[key] = int(_dec(value))
        elif key in BOOL_FIELDS:
            result[key] = _bool(value)
        else:
            result[key] = value
    return result


def _load_approved_package(plan_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    summary = json.loads((plan_dir / "summary.json").read_text(encoding="utf-8"))
    plan_rows = [
        _normalize_approved_row(row)
        for row in _read_csv(plan_dir / "processed/ozon_price_alignment_plan.csv")
    ]
    payload = json.loads((plan_dir / "processed/price_payload_preview.json").read_text(encoding="utf-8"))
    payload_rows = payload.get("prices") if isinstance(payload, dict) else None
    if not isinstance(payload_rows, list):
        raise RuntimeError("Approved payload is invalid")
    payload_keys = {key for row in payload_rows for key in row}
    forbidden = payload_keys & FORBIDDEN_PAYLOAD_KEYS
    if forbidden:
        raise RuntimeError(f"Approved payload has forbidden keys: {sorted(forbidden)}")
    if payload_keys != {"offer_id", "price", "old_price", "currency_code"}:
        raise RuntimeError(f"Unexpected approved payload keys: {sorted(payload_keys)}")
    if len(plan_rows) != len(payload_rows) or len(plan_rows) != int(summary.get("selected_rows") or -1):
        raise RuntimeError("Approved package row count mismatch")
    actual_checksum = canonical_checksum(
        {
            "elastic_source_run_id": summary.get("elastic_source_run_id"),
            "selected_rows": plan_rows,
            "write_payload": payload,
        }
    )
    expected_checksum = str(summary.get("actions_checksum") or "")
    if not expected_checksum or actual_checksum != expected_checksum:
        raise RuntimeError(
            f"Approved package checksum mismatch: expected {expected_checksum}, got {actual_checksum}"
        )
    payload_by_offer = {str(row["offer_id"]): row for row in payload_rows}
    for row in plan_rows:
        expected = payload_by_offer.get(str(row["offer_id"]))
        if not expected:
            raise RuntimeError(f"Approved payload misses offer_id {row['offer_id']}")
        if _dec(expected["price"]) != _dec(row["target_price"]):
            raise RuntimeError(f"Approved target price mismatch for {row['offer_id']}")
        if _dec(expected["old_price"]) != _dec(row["target_old_price"]):
            raise RuntimeError(f"Approved target old_price mismatch for {row['offer_id']}")
    return summary, plan_rows, payload


def _fetch_prices(
    adapter: OzonSellerAdapter,
    offer_ids: list[str],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    raw = adapter.fetch_product_info_prices_by_offer_ids(offer_ids)
    normalized = [normalize_ozon_price_item(row) for row in raw]
    return raw, {str(row.get("offer_id") or ""): row for row in normalized}


def _price_elastic_active(raw_row: dict[str, Any] | None) -> bool:
    if not raw_row:
        return False
    actions = ((raw_row.get("marketing_actions") or {}).get("actions") or [])
    return any("эластич" in str(action.get("title") or "").lower() for action in actions)


def _raw_price_by_offer(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row.get("offer_id") or ""): row for row in rows if row.get("offer_id")}


def _fetch_elastic_active_ids(
    adapter: OzonSellerAdapter,
    raw_dir: Path,
) -> tuple[str, set[str]]:
    raw_dir = ensure_dir(raw_dir)
    actions = _fetch_actions(adapter, raw_dir)
    action = _pick_elastic_action(actions)
    action_id = str(action["action_id"])
    rows = _fetch_action_group(
        adapter,
        action_id=action_id,
        source_group=ACTIVE,
        raw_dir=raw_dir,
    )
    return action_id, {str(row.get("product_id") or "") for row in rows if row.get("product_id")}


def _classify_rows(
    approved_rows: list[dict[str, Any]],
    fresh_by_offer: dict[str, dict[str, Any]],
    raw_by_offer: dict[str, dict[str, Any]],
    elastic_active_ids: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    apply_rows: list[dict[str, Any]] = []
    already_target: list[dict[str, Any]] = []
    drifted: list[dict[str, Any]] = []
    for row in approved_rows:
        offer_id = str(row["offer_id"])
        fresh = fresh_by_offer.get(offer_id)
        if not fresh:
            drifted.append({"offer_id": offer_id, "reason": "missing_in_fresh_prices"})
            continue
        product_id = str(row["product_id"])
        identity_ok = str(fresh.get("product_id") or "") == product_id
        direct_elastic = product_id in elastic_active_ids
        price_elastic = _price_elastic_active(raw_by_offer.get(offer_id))
        expected_elastic = bool(row["elastic_active"])
        elastic_ok = direct_elastic == expected_elastic and price_elastic == expected_elastic
        min_ok = _dec(fresh.get("min_price")) == _dec(row["current_min_price_control_only"])
        current_ok = (
            identity_ok
            and min_ok
            and elastic_ok
            and _dec(fresh.get("price")) == _dec(row["current_price"])
            and _dec(fresh.get("old_price")) == _dec(row["current_old_price"])
        )
        target_ok = (
            identity_ok
            and min_ok
            and elastic_ok
            and _dec(fresh.get("price")) == _dec(row["target_price"])
            and _dec(fresh.get("old_price")) == _dec(row["target_old_price"])
        )
        buyer_price_ok = not expected_elastic or (
            _dec(fresh.get("marketing_seller_price")) == _dec(row["current_action_price"])
        )
        if target_ok and buyer_price_ok:
            already_target.append({**row, "fresh_state": "already_target"})
        elif current_ok and buyer_price_ok:
            apply_rows.append({**row, "fresh_state": "approved_current"})
        else:
            drifted.append(
                {
                    "offer_id": offer_id,
                    "reason": "fresh_state_drift",
                    "expected": {
                        "product_id": product_id,
                        "min_price": row["current_min_price_control_only"],
                        "price": row["current_price"],
                        "old_price": row["current_old_price"],
                        "elastic_active": expected_elastic,
                        "elastic_buyer_price": row["current_action_price"] if expected_elastic else "not_checked",
                    },
                    "fresh": {
                        "product_id": fresh.get("product_id"),
                        "min_price": _money(fresh.get("min_price")),
                        "price": _money(fresh.get("price")),
                        "old_price": _money(fresh.get("old_price")),
                        "elastic_action_api": direct_elastic,
                        "elastic_price_api": price_elastic,
                        "marketing_seller_price": _money(fresh.get("marketing_seller_price")),
                    },
                }
            )
    return apply_rows, already_target, drifted


def _payload(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "offer_id": str(row["offer_id"]),
            "price": _money(row["target_price"]),
            "old_price": _money(row["target_old_price"]),
            "currency_code": "RUB",
        }
        for row in rows
    ]


def _response_errors(response: Any) -> list[dict[str, Any]]:
    result = response.get("result") if isinstance(response, dict) else []
    if not isinstance(result, list):
        return [{"reason": "invalid_response_shape", "response_type": type(response).__name__}]
    errors: list[dict[str, Any]] = []
    for row in result:
        if not isinstance(row, dict):
            errors.append({"reason": "invalid_response_row"})
            continue
        if row.get("errors") or not row.get("updated"):
            errors.append(
                {
                    "offer_id": row.get("offer_id"),
                    "updated": row.get("updated"),
                    "errors": row.get("errors") or [],
                    "warnings": row.get("warnings") or [],
                }
            )
    return errors


def _verify_rows(
    rows: list[dict[str, Any]],
    fresh_by_offer: dict[str, dict[str, Any]],
    raw_by_offer: dict[str, dict[str, Any]],
    elastic_active_ids: set[str],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        offer_id = str(row["offer_id"])
        fresh = fresh_by_offer.get(offer_id)
        product_id = str(row["product_id"])
        expected_elastic = bool(row["elastic_active"])
        direct_elastic = product_id in elastic_active_ids
        price_elastic = _price_elastic_active(raw_by_offer.get(offer_id))
        identity_ok = bool(fresh) and str((fresh or {}).get("product_id") or "") == product_id
        price_ok = bool(fresh) and _dec((fresh or {}).get("price")) == _dec(row["target_price"])
        old_price_ok = bool(fresh) and _dec((fresh or {}).get("old_price")) == _dec(row["target_old_price"])
        min_unchanged = bool(fresh) and (
            _dec((fresh or {}).get("min_price")) == _dec(row["current_min_price_control_only"])
        )
        elastic_ok = direct_elastic == expected_elastic and price_elastic == expected_elastic
        buyer_price_unchanged = not expected_elastic or (
            bool(fresh)
            and _dec((fresh or {}).get("marketing_seller_price")) == _dec(row["current_action_price"])
        )
        ok = all((identity_ok, price_ok, old_price_ok, min_unchanged, elastic_ok, buyer_price_unchanged))
        result.append(
            {
                "offer_id": offer_id,
                "product_id": product_id,
                "status": "ok" if ok else "warning",
                "actual_price": _money((fresh or {}).get("price")),
                "target_price": row["target_price"],
                "actual_old_price": _money((fresh or {}).get("old_price")),
                "target_old_price": row["target_old_price"],
                "actual_min_price": _money((fresh or {}).get("min_price")),
                "approved_min_price": row["current_min_price_control_only"],
                "elastic_expected": expected_elastic,
                "elastic_action_api": direct_elastic,
                "elastic_price_api": price_elastic,
                "actual_marketing_seller_price": _money((fresh or {}).get("marketing_seller_price")),
                "approved_elastic_buyer_price": row["current_action_price"] if expected_elastic else "not_checked",
                "identity_ok": identity_ok,
                "price_ok": price_ok,
                "old_price_ok": old_price_ok,
                "min_unchanged": min_unchanged,
                "elastic_ok": elastic_ok,
                "buyer_price_unchanged": buyer_price_unchanged,
            }
        )
    return result


def _run_stage(
    *,
    stage: str,
    rows: list[dict[str, Any]],
    adapter: OzonSellerAdapter,
    raw_dir: Path,
    processed_dir: Path,
) -> dict[str, Any]:
    stage_key = stage.lower()
    stage_raw = ensure_dir(raw_dir / f"stage_{stage_key}")
    offer_ids = [str(row["offer_id"]) for row in rows]
    raw_before, prices_before = _fetch_prices(adapter, offer_ids)
    write_json(stage_raw / "prices_before.json", raw_before)
    action_id_before, active_before = _fetch_elastic_active_ids(adapter, stage_raw / "elastic_before")
    raw_before_by_offer = _raw_price_by_offer(raw_before)
    apply_rows, already_target, drifted = _classify_rows(
        rows,
        prices_before,
        raw_before_by_offer,
        active_before,
    )
    drift = {
        "stage": stage,
        "approved_rows": len(rows),
        "apply_rows": len(apply_rows),
        "already_target_rows": len(already_target),
        "drifted_rows": len(drifted),
        "elastic_action_id": action_id_before,
        "drifted": drifted,
    }
    write_json(processed_dir / f"stage_{stage_key}_drift_check.json", drift)
    if drifted:
        return {
            "stage": stage,
            "status": "blocked_drift",
            "approved_rows": len(rows),
            "submitted_rows": 0,
            "already_target_rows": len(already_target),
            "drifted_rows": len(drifted),
            "verified_rows": len(already_target),
            "verify_mismatches": [],
            "write_performed": False,
        }

    request_rows = _payload(apply_rows)
    write_json(stage_raw / "price_request.json", {"prices": request_rows})
    response: Any = {"result": []}
    if request_rows:
        response = adapter.import_product_prices(request_rows)
    write_json(stage_raw / "price_response.json", response)
    response_errors = _response_errors(response) if request_rows else []
    write_json(processed_dir / f"stage_{stage_key}_response_errors.json", response_errors)
    if response_errors:
        return {
            "stage": stage,
            "status": "warning_api_errors",
            "approved_rows": len(rows),
            "submitted_rows": len(request_rows),
            "already_target_rows": len(already_target),
            "drifted_rows": 0,
            "verified_rows": len(already_target),
            "verify_mismatches": [],
            "response_errors": response_errors,
            "write_performed": bool(request_rows),
        }

    verify_rows: list[dict[str, Any]] = []
    raw_after: list[dict[str, Any]] = []
    prices_after: dict[str, dict[str, Any]] = {}
    for attempt in range(1, 9):
        if request_rows:
            time.sleep(3 if attempt == 1 else 7)
        raw_after, prices_after = _fetch_prices(adapter, offer_ids)
        provisional = _verify_rows(rows, prices_after, _raw_price_by_offer(raw_after), active_before)
        write_json(processed_dir / f"stage_{stage_key}_price_verify_attempt_{attempt:02d}.json", provisional)
        if all(row["price_ok"] and row["old_price_ok"] and row["min_unchanged"] for row in provisional):
            break
    write_json(stage_raw / "prices_after.json", raw_after)

    action_id_after, active_after = _fetch_elastic_active_ids(adapter, stage_raw / "elastic_after")
    if action_id_after != action_id_before:
        raise RuntimeError(f"Elastic action id changed during stage {stage}")
    verify_rows = _verify_rows(rows, prices_after, _raw_price_by_offer(raw_after), active_after)
    _write_csv(processed_dir / f"stage_{stage_key}_verify.csv", verify_rows, empty_header="offer_id")
    mismatches = [row for row in verify_rows if row["status"] != "ok"]
    return {
        "stage": stage,
        "status": "ok" if not mismatches else "warning_verify",
        "approved_rows": len(rows),
        "submitted_rows": len(request_rows),
        "already_target_rows": len(already_target),
        "drifted_rows": 0,
        "verified_rows": len(verify_rows) - len(mismatches),
        "verify_mismatches": mismatches,
        "response_errors": response_errors,
        "write_performed": bool(request_rows),
    }


def _report(result: dict[str, Any]) -> str:
    stages = result["stages"]
    lines = [
        "# Ozon: результат применения базовых цен и цен со скидкой",
        "",
        f"Run ID: `{result['run_id']}`",
        f"Approved plan: `{result['approved_plan_run_id']}`",
        f"Status: `{result['overall_status']}`",
        "",
        "Изменялись только цена со скидкой (`price`) и базовая зачеркнутая цена "
        "(`old_price`). Минимальные цены не передавались в payload и проверялись "
        "на неизменность.",
        "",
    ]
    for stage in stages:
        lines.extend(
            [
                f"## Этап {stage['stage']}",
                "",
                f"- статус: `{stage['status']}`;",
                f"- согласовано строк: `{stage['approved_rows']}`;",
                f"- отправлено: `{stage['submitted_rows']}`;",
                f"- уже в целевом состоянии: `{stage['already_target_rows']}`;",
                f"- проверено успешно: `{stage['verified_rows']}`;",
                f"- drift: `{stage['drifted_rows']}`;",
                f"- verify mismatch: `{len(stage['verify_mismatches'])}`.",
                "",
            ]
        )
    lines.extend(
        [
            "## Итоговая проверка",
            "",
            f"- всего согласовано: `{result['summary']['approved_rows']}`;",
            f"- успешно проверено: `{result['summary']['verified_rows']}`;",
            f"- min_price изменился: `{result['summary']['min_price_changed_rows']}`;",
            f"- Elastic/цена покупателя mismatch: `{result['summary']['elastic_or_buyer_price_mismatches']}`.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    args = _args()
    if not args.confirmed_by_user:
        raise RuntimeError("Apply requires --confirmed-by-user")
    data_dir = Path(args.data_dir)
    plan_dir = _plan_dir(data_dir, args.plan_run_id)
    plan_summary, approved_rows, _ = _load_approved_package(plan_dir)
    approved_id = args.plan_run_id
    assert_apply_not_repeated(data_dir=data_dir, approved_id=approved_id)

    credentials = load_credentials()
    if not credentials.ozon_seller:
        raise RuntimeError("Ozon Seller credentials are unavailable")

    started = datetime.now(MOSCOW)
    run_id = f"ozon_price_alignment_apply_{started:%Y%m%dT%H%M%S}"
    run_dir = ensure_dir(data_dir / "runs" / started.date().isoformat() / run_id)
    raw_dir = ensure_dir(run_dir / "raw")
    processed_dir = ensure_dir(run_dir / "processed")

    preflight = run_status_preflight(credentials=credentials, data_dir=data_dir, include_lk=False)
    if preflight["overall_status"] not in {"ok", "warning"}:
        raise RuntimeError(f"Preflight is not safe: {preflight['overall_status']}")

    adapter = OzonSellerAdapter(credentials.ozon_seller)
    stage_a_rows = [row for row in approved_rows if row.get("apply_stage") == "A"]
    stage_b_rows = [row for row in approved_rows if row.get("apply_stage") == "B"]
    if len(stage_a_rows) + len(stage_b_rows) != len(approved_rows):
        raise RuntimeError("Approved rows contain an unknown apply stage")

    stages: list[dict[str, Any]] = []
    stage_a = _run_stage(
        stage="A",
        rows=stage_a_rows,
        adapter=adapter,
        raw_dir=raw_dir,
        processed_dir=processed_dir,
    )
    stages.append(stage_a)
    if stage_a["status"] == "ok":
        stage_b = _run_stage(
            stage="B",
            rows=stage_b_rows,
            adapter=adapter,
            raw_dir=raw_dir,
            processed_dir=processed_dir,
        )
        stages.append(stage_b)

    write_performed = any(stage.get("write_performed") for stage in stages)
    all_ok = len(stages) == 2 and all(stage["status"] == "ok" for stage in stages)
    verified_rows = sum(int(stage.get("verified_rows") or 0) for stage in stages)
    all_verify_rows: list[dict[str, str]] = []
    for stage in ("a", "b"):
        path = processed_dir / f"stage_{stage}_verify.csv"
        if path.exists():
            all_verify_rows.extend(_read_csv(path))
    min_changed = sum(not _bool(row.get("min_unchanged")) for row in all_verify_rows)
    elastic_mismatches = sum(
        not _bool(row.get("elastic_ok")) or not _bool(row.get("buyer_price_unchanged"))
        for row in all_verify_rows
    )
    overall_status = "ok" if all_ok and verified_rows == len(approved_rows) else "warning"
    summary = {
        "approved_rows": len(approved_rows),
        "stage_a_rows": len(stage_a_rows),
        "stage_b_rows": len(stage_b_rows),
        "submitted_rows": sum(int(stage.get("submitted_rows") or 0) for stage in stages),
        "already_target_rows": sum(int(stage.get("already_target_rows") or 0) for stage in stages),
        "verified_rows": verified_rows,
        "drifted_rows": sum(int(stage.get("drifted_rows") or 0) for stage in stages),
        "min_price_changed_rows": min_changed,
        "elastic_or_buyer_price_mismatches": elastic_mismatches,
    }
    result = {
        "run_id": run_id,
        "started_at": started.isoformat(timespec="seconds"),
        "mode": "apply",
        "overall_status": overall_status,
        "approved_plan_run_id": approved_id,
        "approved_id": approved_id,
        "actions_checksum": plan_summary["actions_checksum"],
        "apply_performed": write_performed,
        "preflight": {"run_id": preflight["run_id"], "status": preflight["overall_status"]},
        "stages": stages,
        "summary": summary,
        "artifacts": {
            "run_dir": str(run_dir),
            "report": str(run_dir / "report.md"),
            "summary": str(run_dir / "summary.json"),
            "apply_marker": str(apply_marker_for(data_dir=data_dir, approved_id=approved_id)),
        },
    }
    (run_dir / "report.md").write_text(_report(result), encoding="utf-8")
    write_json(run_dir / "summary.json", result)
    manifest = write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=result,
        task="ozon-price-alignment-apply",
        mode="apply",
        risk="high",
        marketplaces=["ozon"],
        inputs={
            "plan_run_id": approved_id,
            "confirmed_by_user": True,
            "min_price_in_payload": False,
            "stage_order": ["A", "B"],
        },
        source_run_ids=[approved_id, preflight["run_id"]],
        approved_id=approved_id,
        lifecycle_status="verified" if overall_status == "ok" else "needs_attention",
        closed=overall_status == "ok",
    )
    result["artifacts"].update(manifest)
    write_json(run_dir / "summary.json", result)
    if write_performed:
        mark_approved_applied(
            data_dir=data_dir,
            approved_id=approved_id,
            apply_run_id=run_id,
            task="ozon-price-alignment-apply",
            status=overall_status,
            run_manifest_path=manifest["manifest"],
            checksum=str(plan_summary["actions_checksum"]),
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if overall_status == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
