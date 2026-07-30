#!/usr/bin/env python3
"""Isolate one Ozon hashtag on one approved test card."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from seller_agent.config import load_credentials  # noqa: E402
from seller_agent.core.run_manifest import write_summary_run_manifest  # noqa: E402
from seller_agent.marketplaces.ozon.adapter import OzonSellerAdapter  # noqa: E402
from seller_agent.reports.writer import ensure_dir, write_json  # noqa: E402


HASHTAG_ATTR_ID = 23171
COLOR_ATTR_ID = 10096
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
INVARIANT_KEYS = (
    "offer_id",
    "product_id",
    "name",
    "description",
    "colors",
    "dimensions",
    "primary_image",
    "images",
    "price",
)


def _canonical_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _checksum(payload: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _adapter() -> OzonSellerAdapter:
    credentials = load_credentials().ozon_seller
    if credentials is None:
        raise RuntimeError("Ozon Seller credentials are unavailable")
    return OzonSellerAdapter(credentials)


def _run_dir(data_dir: Path, run_id: str) -> Path:
    day = datetime.now().date().isoformat()
    return data_dir / "runs" / day / run_id


def _resolve_run_dir(data_dir: Path, run_id: str) -> Path:
    matches = sorted(path for path in (data_dir / "runs").glob(f"*/{run_id}") if path.is_dir())
    if not matches:
        raise FileNotFoundError(f"Run not found: {run_id}")
    return matches[-1]


def _hashtags(item: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for attribute in item.get("attributes") or []:
        if int(attribute.get("id") or 0) != HASHTAG_ATTR_ID:
            continue
        for row in attribute.get("values") or []:
            values.extend(str(row.get("value") or "").split())
    return values


def _fresh_state(
    adapter: OzonSellerAdapter,
    *,
    internal_sku: str,
    product_id: str,
) -> dict[str, Any]:
    attributes = adapter.fetch_product_attributes([internal_sku])
    product_info = adapter.fetch_product_info([product_id])
    descriptions = adapter.fetch_product_descriptions([internal_sku])
    prices = adapter.fetch_product_info_prices_by_offer_ids([internal_sku])
    counts = {
        "attributes": len(attributes),
        "product_info": len(product_info),
        "descriptions": len(descriptions),
        "prices": len(prices),
    }
    if any(value != 1 for value in counts.values()):
        raise RuntimeError(f"Fresh source cardinality mismatch: {counts}")

    attribute_item = attributes[0]
    info_item = product_info[0]
    description_item = descriptions[0]
    price_item = prices[0].get("price") or {}
    normalized = {
        "offer_id": internal_sku,
        "product_id": str(product_id),
        "name": info_item.get("name"),
        "description": description_item.get("description") or description_item.get("result"),
        "hashtags": _hashtags(attribute_item),
        "colors": [
            row
            for row in attribute_item.get("attributes") or []
            if int(row.get("id") or 0) == COLOR_ATTR_ID
        ],
        "dimensions": {
            key: attribute_item.get(key)
            for key in ("depth", "width", "height", "dimension_unit", "weight", "weight_unit")
        },
        "primary_image": attribute_item.get("primary_image"),
        "images": attribute_item.get("images"),
        "price": {
            key: price_item.get(key)
            for key in ("price", "old_price", "min_price", "marketing_seller_price")
        },
        "product_errors": info_item.get("errors") or [],
        "statuses": info_item.get("statuses") or {},
    }
    return {
        "normalized": normalized,
        "attributes_raw": attributes,
        "product_info_raw": product_info,
        "descriptions_raw": descriptions,
        "prices_raw": prices,
    }


def _request(internal_sku: str, candidate: str) -> dict[str, Any]:
    target_value = " ".join(_candidate_hashtags(candidate))
    return {
        "items": [
            {
                "offer_id": internal_sku,
                "attributes": [
                    {
                        "complex_id": 0,
                        "id": HASHTAG_ATTR_ID,
                        "values": [{"dictionary_value_id": 0, "value": target_value}],
                    }
                ],
            }
        ]
    }


def _candidate_hashtags(candidate: str) -> list[str]:
    return candidate.split()


def _validate_candidate(candidate: str) -> None:
    hashtags = _candidate_hashtags(candidate)
    if not hashtags or len(hashtags) > 30:
        raise ValueError("Candidate package must contain between 1 and 30 hashtags")
    if len(set(hashtags)) != len(hashtags):
        raise ValueError("Candidate package contains duplicate hashtags")
    for hashtag in hashtags:
        if not hashtag.startswith("#"):
            raise ValueError("Every candidate must start with #")
        if len(hashtag) > 30:
            raise ValueError("Every candidate must be no longer than 30 characters")


def _write_manifest(
    *,
    data_dir: Path,
    run_dir: Path,
    summary: dict[str, Any],
    mode: str,
    source_run_ids: list[str],
    pending_id: str | None = None,
    approved_id: str | None = None,
    applied_by_run_id: str | None = None,
    closed: bool = False,
) -> None:
    result = write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=summary,
        task="ozon-hashtag-isolation-probe",
        mode=mode,  # type: ignore[arg-type]
        risk="high",
        marketplaces=["ozon"],
        inputs={
            "internal_sku": summary["internal_sku"],
            "candidate": summary["candidate"],
            "target_hashtags": summary.get("target_hashtags"),
            "attribute_id": HASHTAG_ATTR_ID,
        },
        source_run_ids=source_run_ids,
        pending_id=pending_id,
        approved_id=approved_id,
        applied_by_run_id=applied_by_run_id,
        lifecycle_status=(
            "pending_review"
            if mode == "dry_run"
            else ("verified" if closed else "applied")
        ),
        closed=closed,
    )
    summary.setdefault("artifacts", {})["run_manifest"] = str(
        Path(result["manifest"]).relative_to(PROJECT_ROOT)
    )
    write_json(run_dir / "summary.json", summary)


def plan_probe(args: argparse.Namespace) -> dict[str, Any]:
    _validate_candidate(args.candidate)
    data_dir = Path(args.data_dir).resolve()
    run_dir = _run_dir(data_dir, args.run_id)
    ensure_dir(run_dir)
    state = _fresh_state(
        _adapter(),
        internal_sku=args.internal_sku,
        product_id=args.product_id,
    )
    current_hashtags = state["normalized"]["hashtags"]
    expected = args.expected_current or []
    if current_hashtags != expected:
        raise RuntimeError(f"DRIFT hashtags: {current_hashtags} != {expected}")
    write_json(run_dir / "fresh_precheck.json", state)
    request = _request(args.internal_sku, args.candidate)
    target_hashtags = _candidate_hashtags(args.candidate)
    plan = {
        "run_id": args.run_id,
        "mode": "dry_run",
        "status": "pending_owner_review",
        "experiment": "single_hashtag_isolation_probe",
        "internal_sku": args.internal_sku,
        "product_id": str(args.product_id),
        "candidate": args.candidate,
        "target_hashtags": target_hashtags,
        "baseline": state["normalized"],
        "baseline_checksum": _checksum(state["normalized"]),
        "request": request,
        "request_checksum": _checksum(request),
        "write_scope": ["ozon.attribute.23171"],
        "must_remain_unchanged": list(INVARIANT_KEYS) + ["wb_all_fields"],
        "approval_required": True,
        "source_run_ids": args.source_run_id or [],
    }
    write_json(run_dir / "probe_plan.json", plan)
    summary = {
        "run_id": args.run_id,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "dry_run",
        "overall_status": "ok",
        "status": "pending_owner_review",
        "marketplace": "ozon",
        "internal_sku": args.internal_sku,
        "candidate": args.candidate,
        "current_hashtags": current_hashtags,
        "target_hashtags": target_hashtags,
        "request_checksum": plan["request_checksum"],
        "baseline_checksum": plan["baseline_checksum"],
        "artifacts": {
            "run_dir": str(run_dir.relative_to(PROJECT_ROOT)),
            "plan": str((run_dir / "probe_plan.json").relative_to(PROJECT_ROOT)),
        },
    }
    write_json(run_dir / "summary.json", summary)
    _write_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=summary,
        mode="dry_run",
        source_run_ids=plan["source_run_ids"],
        pending_id=args.run_id,
    )
    return summary


def _task_id(response: dict[str, Any]) -> Any:
    result = response.get("result") if isinstance(response.get("result"), dict) else {}
    return response.get("task_id") or result.get("task_id")


def apply_probe(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirmed_by_user:
        raise RuntimeError("Apply requires --confirmed-by-user")
    data_dir = Path(args.data_dir).resolve()
    plan_dir = _resolve_run_dir(data_dir, args.plan_run_id)
    plan = _read_json(plan_dir / "probe_plan.json")
    if _checksum(plan["request"]) != plan["request_checksum"]:
        raise RuntimeError("Plan request checksum mismatch")
    if _checksum(plan["baseline"]) != plan["baseline_checksum"]:
        raise RuntimeError("Plan baseline checksum mismatch")
    run_dir = _run_dir(data_dir, args.run_id)
    ensure_dir(run_dir)
    adapter = _adapter()
    precheck = _fresh_state(
        adapter,
        internal_sku=plan["internal_sku"],
        product_id=plan["product_id"],
    )
    write_json(run_dir / "fresh_precheck.json", precheck)
    for key in (*INVARIANT_KEYS, "hashtags", "product_errors"):
        if precheck["normalized"].get(key) != plan["baseline"].get(key):
            raise RuntimeError(
                f"DRIFT {key}: {precheck['normalized'].get(key)!r} "
                f"!= {plan['baseline'].get(key)!r}"
            )

    response = adapter.update_product_attributes(plan["request"]["items"])
    write_json(run_dir / "apply_response.json", response)
    task_id = _task_id(response)
    if not task_id:
        raise RuntimeError("No task_id in Ozon response")

    import_info: dict[str, Any] = {}
    deadline = time.monotonic() + max(args.wait_seconds, 0)
    attempt = 0
    while True:
        attempt += 1
        import_info = adapter.fetch_product_import_info(task_id)
        write_json(run_dir / f"import_info_{attempt:02d}.json", import_info)
        text = json.dumps(import_info, ensure_ascii=False).lower()
        if (
            "fb_obscene_model_hashtag" in text
            or "failed" in text
            or "declined" in text
            or "imported" in text
            or time.monotonic() >= deadline
        ):
            break
        time.sleep(max(args.poll_interval, 1))

    classification = "unknown"
    reason = "async_timeout"
    postcheck: dict[str, Any] | None = None
    post_attempt = 0
    while time.monotonic() < deadline:
        post_attempt += 1
        postcheck = _fresh_state(
            adapter,
            internal_sku=plan["internal_sku"],
            product_id=plan["product_id"],
        )
        write_json(run_dir / f"fresh_post_{post_attempt:02d}.json", postcheck)
        import_text = json.dumps(import_info, ensure_ascii=False).lower()
        status_text = json.dumps(postcheck["normalized"], ensure_ascii=False).lower()
        if "fb_obscene_model_hashtag" in import_text or (
            "fb_obscene_model_hashtag" in status_text
        ):
            classification = "rejected"
            reason = "FB_OBSCENE_MODEL_hashtag"
            break
        statuses = postcheck["normalized"]["statuses"]
        if (
            statuses.get("status_description") == "Не обновлен"
            and statuses.get("status_failed") == "imported"
        ):
            baseline_statuses = plan["baseline"].get("statuses") or {}
            status_is_new = statuses.get("status_updated_at") != baseline_statuses.get(
                "status_updated_at"
            )
            if status_is_new:
                classification = "needs_lk_confirmation"
                reason = "seller_api_hides_import_error_open_lk_import_history"
                break
        stable = (
            statuses.get("validation_status") == "success"
            and statuses.get("moderate_status") == "approved"
            and not statuses.get("status_description")
        )
        target_hashtags = plan.get("target_hashtags") or _candidate_hashtags(
            plan["candidate"]
        )
        if (
            postcheck["normalized"]["hashtags"] == target_hashtags
            and not postcheck["normalized"]["product_errors"]
            and stable
        ):
            classification = "accepted"
            reason = "exact_hashtag_set_visible_and_moderation_approved"
            break
        time.sleep(max(args.poll_interval, 1))

    if postcheck is None:
        postcheck = _fresh_state(
            adapter,
            internal_sku=plan["internal_sku"],
            product_id=plan["product_id"],
        )
        write_json(run_dir / "fresh_post_final.json", postcheck)

    invariant_checks = {
        key: postcheck["normalized"].get(key) == plan["baseline"].get(key)
        for key in INVARIANT_KEYS
    }
    invariant_checks["product_errors"] = not postcheck["normalized"]["product_errors"]
    invariants_ok = all(invariant_checks.values())
    if not invariants_ok:
        classification = "unknown"
        reason = "invariant_drift"
    overall_status = (
        "ok"
        if classification in {"accepted", "rejected"} and invariants_ok
        else "warning"
    )
    summary = {
        "run_id": args.run_id,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "apply",
        "overall_status": overall_status,
        "experiment": "single_hashtag_isolation_probe",
        "approved_plan_run_id": args.plan_run_id,
        "approval_source": args.approval_source,
        "marketplace": "ozon",
        "internal_sku": plan["internal_sku"],
        "candidate": plan["candidate"],
        "target_hashtags": plan.get("target_hashtags")
        or _candidate_hashtags(plan["candidate"]),
        "task_id": str(task_id),
        "classification": classification,
        "reason": reason,
        "before_hashtags": plan["baseline"]["hashtags"],
        "after_hashtags": postcheck["normalized"]["hashtags"],
        "invariant_checks": invariant_checks,
        "product_errors": postcheck["normalized"]["product_errors"],
        "statuses": postcheck["normalized"]["statuses"],
        "artifacts": {
            "run_dir": str(run_dir.relative_to(PROJECT_ROOT)),
            "plan": str((plan_dir / "probe_plan.json").relative_to(PROJECT_ROOT)),
        },
    }
    write_json(run_dir / "summary.json", summary)
    _write_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=summary,
        mode="apply",
        source_run_ids=[args.plan_run_id],
        approved_id=args.plan_run_id,
        applied_by_run_id=args.run_id,
        closed=overall_status == "ok",
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan")
    plan.add_argument("--internal-sku", required=True)
    plan.add_argument("--product-id", required=True)
    plan.add_argument("--candidate", required=True)
    plan.add_argument("--expected-current", action="append")
    plan.add_argument("--source-run-id", action="append")
    plan.add_argument("--run-id", required=True)

    apply = subparsers.add_parser("apply")
    apply.add_argument("--plan-run-id", required=True)
    apply.add_argument("--run-id", required=True)
    apply.add_argument("--confirmed-by-user", action="store_true")
    apply.add_argument("--approval-source", required=True)
    apply.add_argument("--wait-seconds", type=int, default=150)
    apply.add_argument("--poll-interval", type=int, default=5)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = plan_probe(args) if args.command == "plan" else apply_probe(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("overall_status") == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
