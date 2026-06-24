from __future__ import annotations

import csv
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Any

from seller_agent.catalog.loader import normalize_sku
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.reports.writer import ensure_dir, write_json


DEFAULT_PRODUCTS_PATH = Path("catalog/unified/products.csv")
DEFAULT_OUTPUT_DIR = Path("pricing")
UNIT_TARGETS = {
    "profit_85_ads_20": Decimal("190"),
    "profit_60_ads_25": Decimal("170"),
}

REPORT_FIELDS = [
    "internal_product_id",
    "internal_sku",
    "product_name",
    "mapping_status",
    "pack_qty",
    "cost_total",
    "ozon_offer_id",
    "ozon_product_id",
    "ozon_sku",
    "ozon_price",
    "ozon_old_price",
    "ozon_min_price",
    "ozon_marketing_seller_price",
    "wb_vendor_code",
    "wb_nm_id",
    "wb_base_price",
    "wb_discount_percent",
    "wb_discounted_price",
    "target_net_profit_85_ads_20",
    "target_net_profit_60_ads_25",
    "warning_codes",
]


@dataclass
class PricingStatusRow:
    internal_product_id: str = ""
    internal_sku: str = ""
    product_name: str = ""
    mapping_status: str = ""
    pack_qty: str = ""
    cost_total: str = ""
    ozon_offer_id: str = ""
    ozon_product_id: str = ""
    ozon_sku: str = ""
    ozon_price: str = ""
    ozon_old_price: str = ""
    ozon_min_price: str = ""
    ozon_marketing_seller_price: str = ""
    wb_vendor_code: str = ""
    wb_nm_id: str = ""
    wb_base_price: str = ""
    wb_discount_percent: str = ""
    wb_discounted_price: str = ""
    target_net_profit_85_ads_20: str = ""
    target_net_profit_60_ads_25: str = ""
    warning_codes: str = ""


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


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _json_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return []
    for key in ("items", "goods", "listGoods", "rows"):
        items = value.get(key)
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    for key in ("result", "data"):
        nested = value.get(key)
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
        if isinstance(nested, dict):
            items = _json_items(nested)
            if items:
                return items
    return []


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


def _money(value: Decimal | None) -> str:
    if value is None:
        return ""
    return str(value.quantize(Decimal("1")))


def _first_text(row: dict[str, Any], fields: tuple[str, ...]) -> str:
    for field in fields:
        value = normalize_sku(row.get(field))
        if value:
            return value
    return ""


def _first_decimal(row: dict[str, Any], fields: tuple[str, ...]) -> Decimal | None:
    for field in fields:
        value = _dec(row.get(field))
        if value is not None:
            return value
    return None


def _latest_matching_file(data_dir: Path, patterns: tuple[str, ...]) -> Path | None:
    candidates: list[Path] = []
    runs_dir = data_dir / "runs"
    if not runs_dir.exists():
        return None
    for pattern in patterns:
        candidates.extend(path for path in runs_dir.glob(pattern) if path.is_file())
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def default_ozon_prices_path(data_dir: Path) -> Path | None:
    return _latest_matching_file(
        data_dir,
        (
            "*/ozon_min_price_action_timer_update_*/after_prices.json",
            "*/ozon_min_price_action_timer_update_*/before_prices.json",
            "*/ozon_min_price_api_snapshot_*/product_info_prices_summary.json",
            "*/ozon_min_price_api_snapshot_*/prices_summary.json",
        ),
    )


def default_wb_prices_path(data_dir: Path) -> Path | None:
    return _latest_matching_file(
        data_dir,
        (
            "*/wb_full_seo_audit_*/raw/wb_prices_goods_filter.json",
            "*/wb_actions_discount_plan_*/raw/current_prices.json",
            "*/wb_actions_discount_plan_*/current_prices.json",
        ),
    )


def load_ozon_prices(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    rows = _json_items(_load_json(path))
    by_key: dict[str, dict[str, Any]] = {}
    for row in rows:
        for key in (
            normalize_sku(row.get("offer_id")),
            normalize_sku(row.get("product_id")),
            normalize_sku(row.get("sku")),
        ):
            if key and key not in by_key:
                by_key[key] = row
    return by_key


def _wb_size(row: dict[str, Any]) -> dict[str, Any]:
    sizes = row.get("sizes")
    if isinstance(sizes, list) and sizes and isinstance(sizes[0], dict):
        return sizes[0]
    return {}


def load_wb_prices(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    rows = _json_items(_load_json(path))
    by_key: dict[str, dict[str, Any]] = {}
    for row in rows:
        for key in (
            normalize_sku(row.get("vendorCode") or row.get("vendor_code")),
            normalize_sku(row.get("nmID") or row.get("nm_id")),
        ):
            if key and key not in by_key:
                by_key[key] = row
    return by_key


def _ozon_price_for(product: dict[str, str], prices: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    for key in (
        normalize_sku(product.get("ozon_offer_id")),
        normalize_sku(product.get("ozon_product_id")),
        normalize_sku(product.get("ozon_sku")),
    ):
        if key and key in prices:
            return prices[key]
    return None


def _wb_price_for(product: dict[str, str], prices: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    for key in (
        normalize_sku(product.get("wb_vendor_code")),
        normalize_sku(product.get("wb_nm_id")),
    ):
        if key and key in prices:
            return prices[key]
    return None


def _is_true(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "да"}


def _targets_for_pack(pack_qty: Decimal | None) -> dict[str, Decimal]:
    qty = pack_qty if pack_qty and pack_qty > 0 else Decimal("1")
    return {name: unit_target * qty for name, unit_target in UNIT_TARGETS.items()}


def _warning_summary(rows: list[PricingStatusRow]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for row in rows:
        for code in row.warning_codes.split(";"):
            if code:
                counter[code] += 1
    return dict(sorted(counter.items()))


def build_pricing_status(
    *,
    products: list[dict[str, str]],
    ozon_prices: dict[str, dict[str, Any]] | None = None,
    wb_prices: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[PricingStatusRow], dict[str, Any]]:
    ozon_prices = ozon_prices or {}
    wb_prices = wb_prices or {}
    rows: list[PricingStatusRow] = []

    for product in products:
        warnings: list[str] = []
        pack_qty = _dec(product.get("pack_qty"))
        cost_total = _dec(product.get("cost_total"))
        targets = _targets_for_pack(pack_qty)

        if cost_total is None:
            warnings.append("missing_cost")

        ozon_price_row = _ozon_price_for(product, ozon_prices)
        ozon_price = _first_decimal(ozon_price_row or {}, ("price", "marketing_seller_price"))
        ozon_min_price = _first_decimal(ozon_price_row or {}, ("min_price",))
        if _is_true(product.get("active_ozon")):
            if not ozon_price_row:
                warnings.append("ozon_missing_price_snapshot")
            if ozon_price_row and ozon_min_price is None:
                warnings.append("ozon_missing_min_price")
        if ozon_price is not None and cost_total is not None and ozon_price < cost_total:
            warnings.append("ozon_price_below_cost")
        if ozon_price is not None and ozon_price < targets["profit_85_ads_20"]:
            warnings.append("ozon_price_below_target_profit_85_ads_20")
        if ozon_price is not None and ozon_price < targets["profit_60_ads_25"]:
            warnings.append("ozon_price_below_target_profit_60_ads_25")

        wb_price_row = _wb_price_for(product, wb_prices)
        wb_size = _wb_size(wb_price_row or {})
        wb_base_price = _first_decimal(wb_size, ("price",)) or _first_decimal(wb_price_row or {}, ("price",))
        wb_discounted_price = _first_decimal(wb_size, ("discountedPrice", "clubDiscountedPrice")) or _first_decimal(
            wb_price_row or {},
            ("discountedPrice", "discounted_price"),
        )
        if _is_true(product.get("active_wb")):
            if not wb_price_row:
                warnings.append("wb_missing_price_snapshot")
            if wb_price_row and _first_decimal(wb_price_row, ("min_price", "minPrice")) is None:
                warnings.append("wb_min_price_not_confirmed_by_source")
        compare_wb_price = wb_discounted_price or wb_base_price
        if compare_wb_price is not None and cost_total is not None and compare_wb_price < cost_total:
            warnings.append("wb_price_below_cost")
        if compare_wb_price is not None and compare_wb_price < targets["profit_85_ads_20"]:
            warnings.append("wb_price_below_target_profit_85_ads_20")
        if compare_wb_price is not None and compare_wb_price < targets["profit_60_ads_25"]:
            warnings.append("wb_price_below_target_profit_60_ads_25")

        rows.append(
            PricingStatusRow(
                internal_product_id=product.get("internal_product_id", ""),
                internal_sku=product.get("internal_sku", ""),
                product_name=product.get("product_name", ""),
                mapping_status=product.get("mapping_status", ""),
                pack_qty=product.get("pack_qty", ""),
                cost_total=product.get("cost_total", ""),
                ozon_offer_id=product.get("ozon_offer_id", ""),
                ozon_product_id=product.get("ozon_product_id", ""),
                ozon_sku=product.get("ozon_sku", ""),
                ozon_price=_money(ozon_price),
                ozon_old_price=_money(_first_decimal(ozon_price_row or {}, ("old_price",))),
                ozon_min_price=_money(ozon_min_price),
                ozon_marketing_seller_price=_money(_first_decimal(ozon_price_row or {}, ("marketing_seller_price",))),
                wb_vendor_code=product.get("wb_vendor_code", ""),
                wb_nm_id=product.get("wb_nm_id", ""),
                wb_base_price=_money(wb_base_price),
                wb_discount_percent=_first_text(wb_price_row or {}, ("discount", "clubDiscount")),
                wb_discounted_price=_money(wb_discounted_price),
                target_net_profit_85_ads_20=_money(targets["profit_85_ads_20"]),
                target_net_profit_60_ads_25=_money(targets["profit_60_ads_25"]),
                warning_codes=";".join(dict.fromkeys(warnings)),
            )
        )

    warning_summary = _warning_summary(rows)
    summary = {
        "input_products": len(products),
        "rows": len(rows),
        "with_cost": sum(1 for row in rows if row.cost_total),
        "missing_cost": warning_summary.get("missing_cost", 0),
        "ozon_active_products": sum(1 for product in products if _is_true(product.get("active_ozon"))),
        "ozon_price_rows": sum(1 for row in rows if row.ozon_price),
        "ozon_missing_price_rows": warning_summary.get("ozon_missing_price_snapshot", 0),
        "ozon_missing_min_price_rows": warning_summary.get("ozon_missing_min_price", 0),
        "wb_active_products": sum(1 for product in products if _is_true(product.get("active_wb"))),
        "wb_price_rows": sum(1 for row in rows if row.wb_base_price or row.wb_discounted_price),
        "wb_missing_price_rows": warning_summary.get("wb_missing_price_snapshot", 0),
        "wb_min_price_not_confirmed_rows": warning_summary.get("wb_min_price_not_confirmed_by_source", 0),
        "below_cost_rows": sum(
            warning_summary.get(code, 0)
            for code in ("ozon_price_below_cost", "wb_price_below_cost")
        ),
        "below_target_profit_85_ads_20_rows": sum(
            warning_summary.get(code, 0)
            for code in ("ozon_price_below_target_profit_85_ads_20", "wb_price_below_target_profit_85_ads_20")
        ),
        "below_target_profit_60_ads_25_rows": sum(
            warning_summary.get(code, 0)
            for code in ("ozon_price_below_target_profit_60_ads_25", "wb_price_below_target_profit_60_ads_25")
        ),
        "warning_rows": sum(1 for row in rows if row.warning_codes),
        "warning_summary": warning_summary,
    }
    return rows, summary


def _write_report(path: Path, *, run_id: str, started_at: datetime, result: dict[str, Any]) -> None:
    lines = [
        "# Pricing Status Report",
        "",
        f"Run ID: `{run_id}`",
        f"Started at: `{started_at.isoformat(timespec='seconds')}`",
        f"Status: `{result['overall_status']}`",
        "",
        "## Summary",
        "",
    ]
    for key, value in result["summary"].items():
        if key == "warning_summary":
            continue
        lines.append(f"- `{key}`: {value}")

    lines.extend(["", "## Warning Summary", ""])
    warnings = result["summary"].get("warning_summary", {})
    if warnings:
        for key, value in warnings.items():
            lines.append(f"- `{key}`: {value}")
    else:
        lines.append("- none")

    lines.extend(
        [
            "",
            "## Limits",
            "",
            "- This is a read-only local status report.",
            "- Target net thresholds are internal owner targets before marketplace tariff modeling.",
            "- WB minimum price is reported only if the source snapshot contains a confirmed field.",
            "",
            "## Artifacts",
            "",
        ]
    )
    for key, value in sorted(result["artifacts"].items()):
        lines.append(f"- `{key}`: `{value}`")
    path.write_text("\n".join(lines), encoding="utf-8")


def run_pricing_status(
    *,
    data_dir: Path = Path("data"),
    products_path: Path | None = None,
    ozon_prices_path: Path | None = None,
    wb_prices_path: Path | None = None,
    output_dir: Path | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    started_at = datetime.now()
    run_id = run_id or f"pricing_status_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.strftime("%Y-%m-%d") / run_id)

    products_path = products_path or data_dir / DEFAULT_PRODUCTS_PATH
    ozon_prices_path = ozon_prices_path or default_ozon_prices_path(data_dir)
    wb_prices_path = wb_prices_path or default_wb_prices_path(data_dir)
    output_dir = ensure_dir(output_dir or data_dir / DEFAULT_OUTPUT_DIR)

    errors: dict[str, str] = {}
    products: list[dict[str, str]] = []
    ozon_prices: dict[str, dict[str, Any]] = {}
    wb_prices: dict[str, dict[str, Any]] = {}

    try:
        products = _read_csv(products_path)
    except Exception as exc:  # noqa: BLE001 - task report must capture local input failures
        errors["products"] = str(exc)
    try:
        ozon_prices = load_ozon_prices(ozon_prices_path)
    except Exception as exc:  # noqa: BLE001 - task report must capture bad local snapshot
        errors["ozon_prices"] = str(exc)
    try:
        wb_prices = load_wb_prices(wb_prices_path)
    except Exception as exc:  # noqa: BLE001 - task report must capture bad local snapshot
        errors["wb_prices"] = str(exc)

    rows: list[PricingStatusRow] = []
    summary: dict[str, Any] = {
        "input_products": len(products),
        "rows": 0,
        "with_cost": 0,
        "missing_cost": 0,
        "ozon_active_products": 0,
        "ozon_price_rows": 0,
        "ozon_missing_price_rows": 0,
        "ozon_missing_min_price_rows": 0,
        "wb_active_products": 0,
        "wb_price_rows": 0,
        "wb_missing_price_rows": 0,
        "wb_min_price_not_confirmed_rows": 0,
        "below_cost_rows": 0,
        "below_target_profit_85_ads_20_rows": 0,
        "below_target_profit_60_ads_25_rows": 0,
        "warning_rows": 0,
        "warning_summary": {},
    }
    if not errors:
        rows, summary = build_pricing_status(
            products=products,
            ozon_prices=ozon_prices,
            wb_prices=wb_prices,
        )

    row_dicts = [asdict(row) for row in rows]
    status_csv_path = output_dir / "pricing_status.csv"
    status_json_path = output_dir / "pricing_status.json"
    report_path = run_dir / "pricing_status_report.md"
    summary_path = run_dir / "summary.json"
    if not errors:
        _write_dict_csv(status_csv_path, row_dicts, REPORT_FIELDS)
        write_json(status_json_path, row_dicts)

    overall_status = "error" if errors else "warning" if summary.get("warning_rows") else "ok"
    artifacts = {
        "run_dir": str(run_dir),
        "status_csv": str(status_csv_path),
        "status_json": str(status_json_path),
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
        "inputs": {
            "products_path": str(products_path),
            "ozon_prices_path": str(ozon_prices_path) if ozon_prices_path else "",
            "wb_prices_path": str(wb_prices_path) if wb_prices_path else "",
        },
        "artifacts": artifacts,
    }

    write_json(summary_path, result)
    _write_report(report_path, run_id=run_id, started_at=started_at, result=result)
    write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=result,
        task="pricing-status",
        mode="read_only",
        risk="low",
        marketplaces=["ozon", "wb"],
        inputs=result["inputs"],
    )
    return result
