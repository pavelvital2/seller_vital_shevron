from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
from typing import Any

from openpyxl import Workbook

from seller_agent.config import AppCredentials
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.marketplaces.ozon.adapter import OzonSellerAdapter
from seller_agent.reports.writer import ensure_dir, write_json


READ_PAGE_SIZE = 100
SUPPORTED_ACTION_ROW_GROUPS = {"active", "candidate"}
DEFAULT_LK_BOOST_GLOB = "runs/*/ozon_actions_boost_probe_*/processed/boost_source_summary.json"


def _first(row: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    for alias in aliases:
        value = row.get(alias)
        if value not in (None, ""):
            return value
    return None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(" ", "").replace("\u00a0", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


def _json_decimal(value: Decimal | None) -> str:
    if value is None:
        return ""
    return format(value.normalize(), "f")


def _int_text(value: Any) -> str:
    decimal = _decimal(value)
    if decimal is None:
        return ""
    return str(int(decimal))


def _safe_action(action: dict[str, Any]) -> dict[str, Any]:
    return {
        "action_id": _text(action.get("id") or action.get("action_id")),
        "title": _text(action.get("title") or action.get("name")),
        "action_type": _text(action.get("action_type")),
        "status": action.get("status"),
        "date_start": action.get("date_start") or action.get("start_date"),
        "date_end": action.get("date_end") or action.get("end_date"),
        "auto_add_dates": action.get("auto_add_dates") if isinstance(action.get("auto_add_dates"), list) else [],
        "is_participating": action.get("is_participating"),
        "participating_products_count": action.get("participating_products_count"),
        "potential_products_count": action.get("potential_products_count"),
        "candidates_count": action.get("candidates_count"),
        "active_products_count": action.get("active_products_count"),
        "discount_type": action.get("discount_type"),
        "discount_value": action.get("discount_value"),
    }


def _response_rows(response: dict[str, Any]) -> tuple[list[dict[str, Any]], int | None, str]:
    result = response.get("result") or {}
    rows = result.get("products") or result.get("items") or result.get("list") or []
    if not isinstance(rows, list):
        rows = []
    total = result.get("total")
    last_id = _text(result.get("last_id"))
    return [row for row in rows if isinstance(row, dict)], int(total) if total is not None else None, last_id


def _fetch_actions(ozon: OzonSellerAdapter, raw_dir: Path) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    offset = 0
    page_no = 1
    while True:
        data = ozon.get("/v1/actions", {"limit": READ_PAGE_SIZE, "offset": offset})
        write_json(raw_dir / f"ozon_actions_page_{page_no:03d}.json", data)
        result = data.get("result")
        if isinstance(result, list):
            page_rows = result
        elif isinstance(result, dict):
            page_rows = result.get("actions") or result.get("items") or result.get("list") or []
        else:
            page_rows = []
        page_rows = [row for row in page_rows if isinstance(row, dict)]
        actions.extend(page_rows)
        if len(page_rows) < READ_PAGE_SIZE:
            break
        offset += READ_PAGE_SIZE
        page_no += 1
    return actions


def _fetch_action_rows(
    ozon: OzonSellerAdapter,
    *,
    action: dict[str, Any],
    group: str,
    raw_dir: Path,
    lk_boost_source: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if group not in SUPPORTED_ACTION_ROW_GROUPS:
        raise ValueError(f"unsupported action group: {group}")
    action_id = _text(action["action_id"])
    path = "/v1/actions/products" if group == "active" else "/v1/actions/candidates"
    rows: list[dict[str, Any]] = []
    last_id = ""
    page_no = 1
    while True:
        payload_action_id: int | str = int(action_id) if action_id.isdigit() else action_id
        payload: dict[str, Any] = {"action_id": payload_action_id, "limit": READ_PAGE_SIZE}
        if last_id:
            payload["last_id"] = last_id
        data = ozon.post(path, payload)
        write_json(raw_dir / f"ozon_action_{action_id}_{group}_page_{page_no:03d}.json", data)
        page_rows, total, next_last_id = _response_rows(data)
        for row in page_rows:
            product_id = _text(_first(row, ("id", "product_id")))
            if not product_id:
                continue
            rows.append(_action_offer_row(action=action, row=row, group=group, lk_boost_source=lk_boost_source))
        if total is not None and len(rows) >= total:
            break
        if total is None and len(page_rows) < READ_PAGE_SIZE:
            break
        if not next_last_id or next_last_id == last_id:
            break
        last_id = next_last_id
        page_no += 1
    return rows


def _title_percent_boost(action_title: str) -> Decimal | None:
    if not re.search(r"буст|boost", action_title, flags=re.IGNORECASE):
        return None
    matches = [Decimal(item.replace(",", ".")) for item in re.findall(r"(\d+(?:[,.]\d+)?)\s*%", action_title)]
    return max(matches) if matches else None


def _latest_lk_boost_summary(data_dir: Path) -> Path | None:
    candidates = sorted(data_dir.glob(DEFAULT_LK_BOOST_GLOB), key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def _load_lk_boost_sources(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in data:
        if not isinstance(row, dict):
            continue
        action_id = _text(row.get("action_id"))
        boost = _decimal(row.get("boost_percent_from_description"))
        if not action_id or boost is None or boost <= 0:
            continue
        result[action_id] = {
            "action_id": action_id,
            "title": _text(row.get("title")),
            "action_type": _text(row.get("action_type")),
            "boost_percent": _json_decimal(boost),
            "boost_source": _text(row.get("boost_source")) or "lk_seller_actions_description",
            "source_path": str(path),
        }
    return result


def _price_boost_pair(
    *,
    row: dict[str, Any],
    action: dict[str, Any],
    group: str,
    lk_boost_source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    action_type = _text(action.get("action_type"))
    action_title = _text(action.get("title"))
    action_price = _decimal(row.get("action_price"))
    current_boost = _decimal(row.get("current_boost"))
    price_max_elastic = _decimal(row.get("price_max_elastic"))
    max_boost = _decimal(row.get("max_boost"))

    if action_type == "MARKETPLACE_MULTI_LEVEL_DISCOUNT_ON_AMOUNT":
        if group == "active" and action_price is not None and action_price > 0:
            return {
                "target_action_price": action_price,
                "target_price_source": "action_price",
                "boost_score": current_boost,
                "boost_source": "current_boost",
                "boost_known": bool(current_boost is not None and current_boost > 0),
                "boost_note": "active_elastic_current_action_price_boost",
            }
        if price_max_elastic is not None and price_max_elastic > 0:
            return {
                "target_action_price": price_max_elastic,
                "target_price_source": "price_max_elastic",
                "boost_score": max_boost,
                "boost_source": "max_boost",
                "boost_known": bool(max_boost is not None and max_boost > 0),
                "boost_note": "elastic_max_boost_price",
            }

    aliases = (
        ("max_action_price", "max_action_price"),
        ("price_max_elastic", "price_max_elastic"),
        ("action_price", "action_price"),
        ("price_min_elastic", "price_min_elastic"),
        ("alert_max_action_price", "alert_max_action_price"),
    )
    target_price: Decimal | None = None
    price_source = ""
    for field_name, label in aliases:
        value = _decimal(row.get(field_name))
        if value is not None and value > 0:
            target_price = value
            price_source = label
            break

    structured_boosts = [
        _decimal(row.get("max_boost")),
        _decimal(row.get("current_boost")),
        _decimal(row.get("min_boost")),
    ]
    positive_structured = [value for value in structured_boosts if value is not None and value > 0]
    if positive_structured:
        return {
            "target_action_price": target_price,
            "target_price_source": price_source,
            "boost_score": max(positive_structured),
            "boost_source": "structured_boost_fields",
            "boost_known": True,
            "boost_note": "structured_boost_without_explicit_price_pair",
        }

    lk_boost = _decimal((lk_boost_source or {}).get("boost_percent"))
    if action_type == "STOCK_DISCOUNT" and lk_boost is not None and lk_boost > 0:
        return {
            "target_action_price": target_price,
            "target_price_source": price_source,
            "boost_score": lk_boost,
            "boost_source": (lk_boost_source or {}).get("boost_source") or "lk_seller_actions_description",
            "boost_known": True,
            "boost_note": "stock_discount_fixed_action_boost_from_lk_description",
        }

    title_boost = _title_percent_boost(action_title)
    return {
        "target_action_price": target_price,
        "target_price_source": price_source,
        "boost_score": title_boost,
        "boost_source": "title_percent" if title_boost is not None else "",
        "boost_known": bool(title_boost is not None),
        "boost_note": "title_percent_fallback" if title_boost is not None else "missing_confirmed_boost_at_action_price",
    }


def _action_offer_row(
    *,
    action: dict[str, Any],
    row: dict[str, Any],
    group: str,
    lk_boost_source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    product_id = _text(_first(row, ("id", "product_id")))
    action_title = _text(action.get("title"))
    price_boost = _price_boost_pair(row=row, action=action, group=group, lk_boost_source=lk_boost_source)
    target_price = price_boost["target_action_price"]
    boost = price_boost["boost_score"]
    return {
        "action_id": _text(action.get("action_id")),
        "action_name": action_title,
        "action_type": _text(action.get("action_type")),
        "action_status": _text(action.get("status")),
        "source_group": group,
        "product_id": product_id,
        "offer_id": _text(_first(row, ("offer_id", "offer"))),
        "name": _text(_first(row, ("name", "title"))),
        "current_action_price": _json_decimal(_decimal(row.get("action_price"))),
        "target_action_price": _json_decimal(target_price),
        "target_price_source": price_boost["target_price_source"],
        "boost_score": _json_decimal(boost),
        "boost_known": "true" if price_boost["boost_known"] else "false",
        "boost_source": price_boost["boost_source"],
        "boost_note": price_boost["boost_note"],
        "current_boost": _json_decimal(_decimal(row.get("current_boost"))),
        "min_boost": _json_decimal(_decimal(row.get("min_boost"))),
        "max_boost": _json_decimal(_decimal(row.get("max_boost"))),
        "raw_price": _json_decimal(_decimal(row.get("price"))),
        "stock_in_action": _json_decimal(_decimal(row.get("stock"))),
        "add_mode": _text(row.get("add_mode")),
    }


def _price_object(row: dict[str, Any]) -> dict[str, Any]:
    price = row.get("price")
    return price if isinstance(price, dict) else {}


def _product_price_rows(price_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in price_rows:
        product_id = _text(row.get("product_id"))
        if product_id:
            result[product_id] = row
    return result


def _product_info_rows(info_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in info_rows:
        product_id = _text(row.get("id") or row.get("product_id"))
        if product_id:
            result[product_id] = row
    return result


def _fbo_stock_present(stock_item: dict[str, Any] | None) -> Decimal | None:
    if not stock_item:
        return None
    stocks = stock_item.get("stocks") or stock_item.get("stock") or []
    if isinstance(stocks, dict):
        stocks = [stocks]
    if not isinstance(stocks, list):
        return None
    total = Decimal("0")
    found = False
    for stock in stocks:
        if not isinstance(stock, dict):
            continue
        if _text(stock.get("type")).lower() != "fbo":
            continue
        present = _decimal(stock.get("present"))
        if present is None:
            continue
        total += present
        found = True
    return total if found else None


def _stock_rows(stock_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in stock_rows:
        product_id = _text(row.get("product_id") or row.get("id"))
        if product_id:
            result[product_id] = row
    return result


@dataclass(frozen=True)
class OfferDecision:
    status: str
    reason_code: str
    target_action_price: Decimal | None
    boost_score: Decimal | None
    price_loss: Decimal | None

    @property
    def valid(self) -> bool:
        return self.status == "valid"


def evaluate_offer(
    offer: dict[str, Any],
    *,
    price_row: dict[str, Any] | None,
    fbo_stock: Decimal | None,
) -> OfferDecision:
    price_obj = _price_object(price_row or {})
    min_price = _decimal(price_obj.get("min_price"))
    seller_price = _decimal(price_obj.get("price"))
    target = _decimal(offer.get("target_action_price"))
    boost = _decimal(offer.get("boost_score"))
    boost_known = str(offer.get("boost_known") or "").lower() == "true"
    if fbo_stock is None or fbo_stock <= 0:
        return OfferDecision("blocked", "no_fbo_stock", target, boost, None)
    if min_price is None:
        return OfferDecision("blocked", "missing_min_price", target, boost, None)
    if target is None:
        return OfferDecision("blocked", "missing_action_price", target, boost, None)
    if target < min_price:
        return OfferDecision("blocked", "action_price_below_min_price", target, boost, None)
    if not boost_known:
        return OfferDecision("blocked", "missing_confirmed_boost_at_action_price", target, boost, None)
    price_loss = seller_price - target if seller_price is not None else None
    return OfferDecision("valid", "valid_action_offer", target, boost, price_loss)


def _offer_sort_key(row: dict[str, Any]) -> tuple[Decimal, Decimal, Decimal]:
    boost = _decimal(row.get("boost_score")) or Decimal("-1")
    target = _decimal(row.get("target_action_price")) or Decimal("0")
    loss = _decimal(row.get("price_loss")) or Decimal("0")
    return boost, target, -loss


def _effective_action_price(row: dict[str, Any] | None) -> Decimal | None:
    if not row:
        return None
    return _decimal(row.get("target_action_price")) or _decimal(row.get("current_action_price"))


def _business_better_than_current(
    *,
    candidate: dict[str, Any],
    current: dict[str, Any],
) -> tuple[bool, str]:
    candidate_boost = _decimal(candidate.get("boost_score"))
    current_boost = _decimal(current.get("boost_score"))
    candidate_price = _effective_action_price(candidate)
    current_price = _effective_action_price(current)

    if candidate_boost is None:
        return False, "candidate_boost_unknown"
    if current_boost is None:
        return True, "current_boost_unknown_candidate_confirmed"
    if candidate_boost > current_boost:
        return True, "candidate_higher_boost"
    if candidate_boost == current_boost and candidate_price is not None and current_price is not None:
        if candidate_price >= current_price:
            return True, "same_boost_not_lower_price"
        return False, "same_boost_lower_price"
    return False, "candidate_not_better_by_business_policy"


def _pick_recommendations(offers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_product: dict[str, list[dict[str, Any]]] = {}
    for offer in offers:
        by_product.setdefault(str(offer.get("product_id") or ""), []).append(offer)

    recommendations: list[dict[str, Any]] = []
    for product_id, product_offers in sorted(by_product.items(), key=lambda item: item[0]):
        valid = [row for row in product_offers if row.get("offer_status") == "valid"]
        active = [row for row in product_offers if row.get("source_group") == "active"]
        active_valid = [row for row in active if row.get("offer_status") == "valid"]
        current_best_active = sorted(active_valid, key=_offer_sort_key, reverse=True)[0] if active_valid else None
        if not valid:
            sample = product_offers[0]
            reason_counts: dict[str, int] = {}
            for row in product_offers:
                reason = str(row.get("reason_code") or "")
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
            recommendations.append(
                {
                    **_recommendation_base(sample),
                    "recommended_action": "skip",
                    "reason_code": "no_valid_action_offer",
                    "reason_details": json.dumps(reason_counts, ensure_ascii=False, sort_keys=True),
                }
            )
            continue

        best = sorted(valid, key=_offer_sort_key, reverse=True)[0]
        business_policy_reason = ""
        if current_best_active and best["action_id"] == current_best_active["action_id"]:
            recommended_action = "keep_current_action"
            if best.get("current_action_price") and best.get("target_action_price") != best.get("current_action_price"):
                recommended_action = "update_current_action_price"
        elif current_best_active:
            is_better, business_policy_reason = _business_better_than_current(
                candidate=best,
                current=current_best_active,
            )
            if is_better:
                recommended_action = "switch_to_better_action_review"
            else:
                best = current_best_active
                recommended_action = "keep_current_action"
        else:
            recommended_action = "add_to_best_action"
        recommendations.append(
            {
                **_recommendation_base(best),
                "recommended_action": recommended_action,
                "reason_code": (
                    business_policy_reason
                    if business_policy_reason and recommended_action == "keep_current_action"
                    else "best_boost_then_price_above_min_price"
                ),
                "reason_details": (
                    f"best boost={best.get('boost_score') or 'n/a'}, "
                    f"target_price={best.get('target_action_price')}, "
                    f"min_price={best.get('min_price') or ''}, "
                    f"business_policy={business_policy_reason or 'best_or_current_action'}"
                ),
                "current_active_action_id": current_best_active.get("action_id") if current_best_active else "",
                "current_active_action_name": current_best_active.get("action_name") if current_best_active else "",
                "current_active_boost": current_best_active.get("boost_score") if current_best_active else "",
                "current_active_action_price": current_best_active.get("current_action_price") if current_best_active else "",
                "current_active_target_action_price": (
                    current_best_active.get("target_action_price") if current_best_active else ""
                ),
            }
        )
    return recommendations


def _recommendation_base(row: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "product_id",
        "offer_id",
        "name",
        "action_id",
        "action_name",
        "action_type",
        "source_group",
        "current_action_price",
        "target_action_price",
        "target_price_source",
        "boost_score",
        "boost_known",
        "boost_source",
        "boost_note",
        "seller_price",
        "min_price",
        "fbo_stock",
        "price_loss",
    ]
    return {key: row.get(key, "") for key in keys}


def _write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter=";", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_xlsx(*, offers: list[dict[str, Any]], recommendations: list[dict[str, Any]], path: Path) -> None:
    workbook = Workbook()
    default = workbook.active
    workbook.remove(default)
    for title, rows in (("recommendations", recommendations), ("offers", offers)):
        sheet = workbook.create_sheet(title)
        headers: list[str] = []
        for row in rows:
            for key in row:
                if key not in headers:
                    headers.append(key)
        if not headers:
            headers = ["empty"]
        sheet.append(headers)
        for row in rows:
            sheet.append([row.get(header, "") for header in headers])
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for column in sheet.columns:
            max_len = max(len(str(cell.value or "")) for cell in column)
            sheet.column_dimensions[column[0].column_letter].width = min(max(max_len + 2, 12), 80)
    workbook.save(path)


def _write_report(path: Path, *, result: dict[str, Any]) -> None:
    summary = result["summary"]
    lines = [
        "# Ozon Actions Optimizer Dry Run",
        "",
        "Mode: read-only/dry-run. No Ozon actions were changed.",
        "",
        "## Summary",
        "",
    ]
    for key in (
        "actions_total",
        "actions_with_rows",
        "offers_total",
        "valid_offers",
        "blocked_offers",
        "recommended_add",
        "recommended_keep",
        "recommended_update",
        "recommended_switch_review",
        "recommended_skip",
        "lk_boost_actions_with_numeric_boost",
        "lk_boost_source_path",
    ):
        lines.append(f"- `{key}`: {summary.get(key)}")
    lines.extend(["", "## Recommendation Reasons", ""])
    for reason, count in sorted((summary.get("recommendation_reason_counts") or {}).items()):
        lines.append(f"- `{reason}`: {count}")
    lines.extend(["", "## Offer Block Reasons", ""])
    for reason, count in sorted((summary.get("offer_reason_counts") or {}).items()):
        lines.append(f"- `{reason}`: {count}")
    lines.extend(["", "## Artifacts", ""])
    for key, value in sorted(result["artifacts"].items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _count_by(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "")
        counts[value] = counts.get(value, 0) + 1
    return counts


def run_ozon_actions_optimizer_plan(
    *,
    credentials: AppCredentials,
    data_dir: Path = Path("data"),
    run_id: str | None = None,
    lk_boost_summary_json: Path | None = None,
) -> dict[str, Any]:
    if not credentials.ozon_seller:
        raise RuntimeError("missing Ozon Seller API credentials")

    started_at = datetime.now()
    run_id = run_id or f"ozon_actions_optimizer_plan_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)
    raw_dir = ensure_dir(run_dir / "raw")
    processed_dir = ensure_dir(run_dir / "processed")

    resolved_lk_boost_summary_json = lk_boost_summary_json or _latest_lk_boost_summary(data_dir)
    lk_boost_sources = _load_lk_boost_sources(resolved_lk_boost_summary_json)
    write_json(processed_dir / "lk_boost_sources.json", lk_boost_sources)

    ozon = OzonSellerAdapter(credentials.ozon_seller)
    actions_raw = _fetch_actions(ozon, raw_dir)
    actions = [_safe_action(action) for action in actions_raw]
    write_json(processed_dir / "actions_safe_snapshot.json", actions)

    source_offers: list[dict[str, Any]] = []
    action_errors: list[dict[str, Any]] = []
    for action in actions:
        action_id = str(action.get("action_id") or "")
        if not action_id:
            continue
        for group in ("active", "candidate"):
            try:
                source_offers.extend(
                    _fetch_action_rows(
                        ozon,
                        action=action,
                        group=group,
                        raw_dir=raw_dir,
                        lk_boost_source=lk_boost_sources.get(action_id),
                    )
                )
            except Exception as exc:  # noqa: BLE001 - one broken action should not hide all other actions.
                action_errors.append({"action_id": action_id, "group": group, "error": str(exc)[:1000]})
    write_json(processed_dir / "action_fetch_errors.json", action_errors)
    write_json(processed_dir / "source_offers.json", source_offers)

    product_ids = sorted({str(row.get("product_id") or "") for row in source_offers if row.get("product_id")})
    price_rows = _product_price_rows(ozon.fetch_product_info_prices(visibility="ALL"))
    info_rows = _product_info_rows(ozon.fetch_product_info(product_ids)) if product_ids else {}
    stock_rows = _stock_rows(ozon.fetch_product_stocks(product_ids)) if product_ids else {}
    write_json(processed_dir / "prices_safe_snapshot.json", price_rows)
    write_json(processed_dir / "product_info_safe_snapshot.json", info_rows)
    write_json(processed_dir / "stocks_safe_snapshot.json", stock_rows)

    offers: list[dict[str, Any]] = []
    for source in source_offers:
        product_id = str(source.get("product_id") or "")
        price_row = price_rows.get(product_id) or {}
        info_row = info_rows.get(product_id) or {}
        price_obj = _price_object(price_row)
        fbo_stock = _fbo_stock_present(stock_rows.get(product_id))
        decision = evaluate_offer(source, price_row=price_row, fbo_stock=fbo_stock)
        offer = {
            **source,
            "offer_id": source.get("offer_id") or price_row.get("offer_id") or info_row.get("offer_id") or "",
            "name": source.get("name") or info_row.get("name") or "",
            "seller_price": _json_decimal(_decimal(price_obj.get("price"))),
            "old_price": _json_decimal(_decimal(price_obj.get("old_price"))),
            "min_price": _json_decimal(_decimal(price_obj.get("min_price"))),
            "currency_code": price_obj.get("currency_code") or "",
            "fbo_stock": _json_decimal(fbo_stock),
            "offer_status": decision.status,
            "reason_code": decision.reason_code,
            "price_loss": _json_decimal(decision.price_loss),
        }
        offers.append(offer)

    recommendations = _pick_recommendations(offers)

    offers_csv = run_dir / "ozon_actions_optimizer_offers.csv"
    recommendations_csv = run_dir / "ozon_actions_optimizer_recommendations.csv"
    xlsx_path = run_dir / "ozon_actions_optimizer.xlsx"
    report_path = run_dir / "ozon_actions_optimizer_report.md"
    payload_preview_path = run_dir / "ozon_actions_optimizer_payload_preview.json"
    write_json(processed_dir / "offers.json", offers)
    write_json(processed_dir / "recommendations.json", recommendations)
    _write_csv(offers, offers_csv)
    _write_csv(recommendations, recommendations_csv)
    _write_xlsx(offers=offers, recommendations=recommendations, path=xlsx_path)

    payload_preview = {
        "dry_run": True,
        "apply_performed": False,
        "note": "Preview only. Ozon action changes require owner approval, fresh dry-run and drift-check.",
        "add_or_update": [
            {
                "action_id": row.get("action_id"),
                "product_id": row.get("product_id"),
                "offer_id": row.get("offer_id"),
                "action_price": row.get("target_action_price"),
                "recommended_action": row.get("recommended_action"),
            }
            for row in recommendations
            if row.get("recommended_action") in {"add_to_best_action", "update_current_action_price"}
        ],
        "switch_review": [
            {
                "from_action_id": row.get("current_active_action_id"),
                "to_action_id": row.get("action_id"),
                "product_id": row.get("product_id"),
                "offer_id": row.get("offer_id"),
                "action_price": row.get("target_action_price"),
            }
            for row in recommendations
            if row.get("recommended_action") == "switch_to_better_action_review"
        ],
    }
    write_json(payload_preview_path, payload_preview)

    recommendation_counts = _count_by(recommendations, "recommended_action")
    summary = {
        "actions_total": len(actions),
        "actions_with_rows": len({row.get("action_id") for row in source_offers}),
        "action_fetch_errors": len(action_errors),
        "products_with_action_offers": len({row.get("product_id") for row in offers}),
        "offers_total": len(offers),
        "valid_offers": sum(1 for row in offers if row.get("offer_status") == "valid"),
        "blocked_offers": sum(1 for row in offers if row.get("offer_status") != "valid"),
        "recommended_add": recommendation_counts.get("add_to_best_action", 0),
        "recommended_keep": recommendation_counts.get("keep_current_action", 0),
        "recommended_update": recommendation_counts.get("update_current_action_price", 0),
        "recommended_switch_review": recommendation_counts.get("switch_to_better_action_review", 0),
        "recommended_skip": recommendation_counts.get("skip", 0),
        "lk_boost_source_path": str(resolved_lk_boost_summary_json) if resolved_lk_boost_summary_json else "",
        "lk_boost_actions_with_numeric_boost": len(lk_boost_sources),
        "recommendation_reason_counts": _count_by(recommendations, "reason_code"),
        "offer_reason_counts": _count_by(offers, "reason_code"),
    }
    artifacts = {
        "run_dir": str(run_dir),
        "report": str(report_path),
        "offers_csv": str(offers_csv),
        "recommendations_csv": str(recommendations_csv),
        "xlsx": str(xlsx_path),
        "payload_preview": str(payload_preview_path),
        "summary": str(run_dir / "summary.json"),
        "run_manifest": str(run_dir / "manifest.json"),
    }
    result = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "dry-run",
        "overall_status": "warning" if action_errors else "ok",
        "summary": summary,
        "artifacts": artifacts,
        "apply_performed": False,
    }
    write_json(run_dir / "summary.json", result)
    _write_report(report_path, result=result)
    write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=result,
        task="ozon-actions-optimizer-plan",
        mode="dry_run",
        risk="normal",
        marketplaces=["ozon"],
        inputs={
            "lk_boost_summary_json": str(resolved_lk_boost_summary_json) if resolved_lk_boost_summary_json else "",
        },
    )
    return result
