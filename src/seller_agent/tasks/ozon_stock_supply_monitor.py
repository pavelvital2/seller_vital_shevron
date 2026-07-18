from __future__ import annotations

from collections import defaultdict
import csv
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from seller_agent.config import AppCredentials
from seller_agent.core.run_manifest import write_summary_run_manifest
from seller_agent.marketplaces.ozon.adapter import OzonSellerAdapter
from seller_agent.reports.writer import ensure_dir, write_json


MOSCOW = ZoneInfo("Europe/Moscow")
MONITORED_ORDER_STATES = [
    "DATA_FILLING",
    "READY_TO_SUPPLY",
    "ACCEPTED_AT_SUPPLY_WAREHOUSE",
    "IN_TRANSIT",
    "ACCEPTANCE_AT_STORAGE_WAREHOUSE",
    "REPORTS_CONFIRMATION_AWAITING",
    "REPORT_REJECTED",
    "OVERDUE",
]
CONFIRMED_INBOUND_STATES = {
    "READY_TO_SUPPLY",
    "ACCEPTED_AT_SUPPLY_WAREHOUSE",
    "IN_TRANSIT",
    "ACCEPTANCE_AT_STORAGE_WAREHOUSE",
}
STATE_LABELS = {
    "DATA_FILLING": "Заполнение данных",
    "READY_TO_SUPPLY": "Готова к отгрузке",
    "ACCEPTED_AT_SUPPLY_WAREHOUSE": "Принята на точке отгрузки",
    "IN_TRANSIT": "В пути",
    "ACCEPTANCE_AT_STORAGE_WAREHOUSE": "Приемка на складе",
    "ACCEPTED_AT_STORAGE_WAREHOUSE": "Принята на складе",
    "REPORTS_CONFIRMATION_AWAITING": "Ожидает согласования актов",
    "REPORT_REJECTED": "Акт отклонен",
    "OVERDUE": "Просрочена",
}


def run_ozon_stock_supply_monitor(
    *,
    data_dir: Path,
    credentials: AppCredentials | None = None,
    run_id: str | None = None,
    adapter: OzonSellerAdapter | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    started_at = now or datetime.now(MOSCOW)
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=MOSCOW)
    run_id = run_id or f"ozon_stock_supply_monitor_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.date().isoformat() / run_id)
    raw_dir = ensure_dir(run_dir / "raw")

    if adapter is None:
        if credentials is None or credentials.ozon_seller is None:
            raise ValueError("Ozon Seller API credentials are not configured")
        adapter = OzonSellerAdapter(credentials.ozon_seller)

    catalog = _load_catalog(data_dir / "catalog" / "unified" / "products.csv")
    warnings: list[str] = []

    product_list = adapter.fetch_product_list(visibility="ALL")
    product_ids = [_text(row.get("product_id")) for row in product_list if _text(row.get("product_id"))]
    product_info = adapter.fetch_product_info(product_ids)
    product_stocks = adapter.fetch_product_stocks(product_ids)
    warehouse_stocks = adapter.fetch_stock_on_warehouses()
    write_json(raw_dir / "ozon_product_list.json", product_list)
    write_json(raw_dir / "ozon_product_info.json", product_info)
    write_json(raw_dir / "ozon_product_stocks.json", product_stocks)
    write_json(raw_dir / "ozon_stock_on_warehouses.json", warehouse_stocks)

    identity = _product_identity(product_list, product_info)
    general_rows, general_metrics, general_unmapped = _normalize_general_stocks(product_stocks, identity, catalog)
    warehouses, warehouse_rows, warehouse_metrics, warehouse_unmapped = _normalize_warehouse_stocks(
        warehouse_stocks,
        identity,
        catalog,
    )
    if general_unmapped:
        warnings.append(
            f"Для {general_unmapped} строк общего FBO-остатка не найден pack_qty; использовано значение 1."
        )
    if warehouse_unmapped:
        warnings.append(
            f"Для {warehouse_unmapped} строк складского остатка не найден pack_qty; использовано значение 1."
        )

    supply_data = _fetch_active_supplies(adapter, catalog)
    warnings.extend(supply_data["warnings"])
    write_json(raw_dir / "ozon_active_supply_orders.json", supply_data["raw"])

    reconciliation = {
        "general_fbo_present": general_metrics["present"],
        "warehouse_free_to_sell": warehouse_metrics["free_to_sell"],
        "warehouse_reserved": warehouse_metrics["reserved"],
        "warehouse_accounted": warehouse_metrics["free_to_sell"] + warehouse_metrics["reserved"],
        "difference": general_metrics["present"]
        - warehouse_metrics["free_to_sell"]
        - warehouse_metrics["reserved"],
    }
    if reconciliation["difference"]:
        warnings.append(
            "Общий FBO present и сумма warehouse free_to_sell + reserved расходятся на "
            f"{reconciliation['difference']} ед.; источники показаны раздельно."
        )

    report_path = run_dir / "ozon_stock_supply_monitor.md"
    warehouse_csv = run_dir / "ozon_warehouses.csv"
    stock_rows_csv = run_dir / "ozon_stock_rows.csv"
    supplies_csv = run_dir / "ozon_active_supplies.csv"
    general_csv = run_dir / "ozon_general_stocks.csv"
    summary_path = run_dir / "summary.json"
    _write_csv(warehouse_csv, warehouses)
    _write_csv(stock_rows_csv, warehouse_rows)
    _write_csv(supplies_csv, supply_data["rows"])
    _write_csv(general_csv, general_rows)

    metrics = {
        "general_fbo": general_metrics,
        "warehouses": warehouse_metrics,
        "supplies": supply_data["metrics"],
        "warehouse_count": len(warehouses),
        "active_order_count": supply_data["metrics"]["orders"],
        "active_supply_count": supply_data["metrics"]["supplies"],
        "virtual_order_count": supply_data["metrics"]["virtual_orders"],
        "reconciliation": reconciliation,
    }
    summary: dict[str, Any] = {
        "task": "ozon-stock-supply-monitor",
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "overall_status": "warning" if warnings else "ok",
        "metrics": metrics,
        "warehouses": warehouses,
        "active_supplies": supply_data["rows"],
        "warnings": warnings,
        "sources": [
            "Ozon POST /v3/product/list",
            "Ozon POST /v3/product/info/list",
            "Ozon POST /v4/product/info/stocks",
            "Ozon POST /v2/analytics/stock_on_warehouses",
            "Ozon POST /v3/supply-order/list",
            "Ozon POST /v3/supply-order/get",
            "Ozon POST /v1/supply-order/details",
            "Ozon POST /v1/supply-order/bundle",
        ],
        "artifacts": {
            "report": str(report_path),
            "warehouse_csv": str(warehouse_csv),
            "stock_rows_csv": str(stock_rows_csv),
            "supplies_csv": str(supplies_csv),
            "general_csv": str(general_csv),
            "summary": str(summary_path),
        },
        "next_step": "Использовать отчет для контроля; остатки и поставки Ozon не изменялись.",
    }
    _write_report(report_path, summary)
    write_json(summary_path, summary)
    manifest = write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=summary,
        task="ozon-stock-supply-monitor",
        mode="read_only",
        risk="low",
        marketplaces=["ozon"],
        lifecycle_status="closed",
        closed=True,
    )
    summary["artifacts"].update(manifest)
    write_json(summary_path, summary)
    return summary


def _normalize_general_stocks(
    rows: list[dict[str, Any]],
    identity: dict[str, dict[str, str]],
    catalog: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int], int]:
    normalized: list[dict[str, Any]] = []
    unmapped = 0
    for row in rows:
        offer_id = _text(row.get("offer_id"))
        product_id = _text(row.get("product_id"))
        item_identity = identity.get(f"offer:{offer_id}") or identity.get(f"product:{product_id}") or {}
        for stock in row.get("stocks") or []:
            if not isinstance(stock, dict):
                continue
            stock_type = _text(stock.get("type") or stock.get("source")).lower()
            if stock_type != "fbo":
                continue
            sku = _text(stock.get("sku") or item_identity.get("sku"))
            pack_qty, mapped = _pack_qty(catalog, offer_id=offer_id, sku=sku, product_id=product_id)
            if not mapped:
                unmapped += 1
            present = _int(stock.get("present"))
            reserved = _int(stock.get("reserved"))
            normalized.append(
                {
                    "offer_id": offer_id,
                    "product_id": product_id,
                    "sku": sku,
                    "title": _text(item_identity.get("title")),
                    "present": present,
                    "reserved": reserved,
                    "pack_qty": pack_qty,
                    "physical_present": present * pack_qty,
                    "physical_reserved": reserved * pack_qty,
                }
            )
    return (
        normalized,
        {
            "present": sum(_int(row["present"]) for row in normalized),
            "reserved": sum(_int(row["reserved"]) for row in normalized),
            "physical_present": sum(_int(row["physical_present"]) for row in normalized),
            "physical_reserved": sum(_int(row["physical_reserved"]) for row in normalized),
            "products": len(normalized),
        },
        unmapped,
    )


def _normalize_warehouse_stocks(
    rows: list[dict[str, Any]],
    identity: dict[str, dict[str, str]],
    catalog: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int], int]:
    detail_rows: list[dict[str, Any]] = []
    grouped: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "warehouse_name": "Не указан",
            "free_to_sell": 0,
            "physical_free_to_sell": 0,
            "reserved": 0,
            "physical_reserved": 0,
            "promised": 0,
            "physical_promised": 0,
            "product_rows": 0,
        }
    )
    unmapped = 0
    for row in rows:
        offer_id = _text(row.get("item_code") or row.get("offer_id"))
        sku = _text(row.get("sku"))
        item_identity = identity.get(f"offer:{offer_id}") or identity.get(f"sku:{sku}") or {}
        product_id = _text(item_identity.get("product_id"))
        pack_qty, mapped = _pack_qty(catalog, offer_id=offer_id, sku=sku, product_id=product_id)
        if not mapped:
            unmapped += 1
        warehouse = _text(row.get("warehouse_name")) or "Не указан"
        free = _int(row.get("free_to_sell_amount"))
        reserved = _int(row.get("reserved_amount"))
        promised = _int(row.get("promised_amount"))
        detail = {
            "warehouse_name": warehouse,
            "offer_id": offer_id,
            "sku": sku,
            "title": _text(row.get("item_name") or item_identity.get("title")),
            "free_to_sell": free,
            "reserved": reserved,
            "promised": promised,
            "pack_qty": pack_qty,
            "physical_free_to_sell": free * pack_qty,
            "physical_reserved": reserved * pack_qty,
            "physical_promised": promised * pack_qty,
        }
        detail_rows.append(detail)
        target = grouped[warehouse]
        target["warehouse_name"] = warehouse
        target["free_to_sell"] += free
        target["physical_free_to_sell"] += free * pack_qty
        target["reserved"] += reserved
        target["physical_reserved"] += reserved * pack_qty
        target["promised"] += promised
        target["physical_promised"] += promised * pack_qty
        target["product_rows"] += 1
    warehouses = sorted(grouped.values(), key=lambda row: (-_int(row["free_to_sell"]), str(row["warehouse_name"])))
    metrics = {
        "free_to_sell": sum(_int(row["free_to_sell"]) for row in warehouses),
        "physical_free_to_sell": sum(_int(row["physical_free_to_sell"]) for row in warehouses),
        "reserved": sum(_int(row["reserved"]) for row in warehouses),
        "physical_reserved": sum(_int(row["physical_reserved"]) for row in warehouses),
        "promised": sum(_int(row["promised"]) for row in warehouses),
        "physical_promised": sum(_int(row["physical_promised"]) for row in warehouses),
        "product_rows": len(detail_rows),
    }
    return warehouses, detail_rows, metrics, unmapped


def _fetch_active_supplies(adapter: OzonSellerAdapter, catalog: dict[str, dict[str, Any]]) -> dict[str, Any]:
    warnings: list[str] = []
    order_ids = adapter.fetch_supply_order_ids(states=MONITORED_ORDER_STATES)
    orders = adapter.fetch_supply_orders(order_ids) if order_ids else []
    raw: dict[str, Any] = {"order_ids": order_ids, "orders": orders, "details": [], "bundles": []}
    normalized: list[dict[str, Any]] = []
    virtual_orders = 0
    unmapped_units = 0
    for order in orders:
        tags = order.get("order_tags") if isinstance(order.get("order_tags"), dict) else {}
        if bool(tags.get("is_virtual")):
            virtual_orders += 1
            continue
        order_id = _text(order.get("order_id") or order.get("orderId") or order.get("id"))
        order_number = _text(order.get("order_number") or order.get("orderNumber"))
        order_state = _text(order.get("state"))
        supplies = [row for row in (order.get("supplies") or []) if isinstance(row, dict)]
        if not supplies:
            normalized.append(
                _supply_row(
                    order=order,
                    order_id=order_id,
                    order_number=order_number,
                    order_state=order_state,
                    supply={},
                    goods=[],
                    mapped_physical=0,
                )
            )
            continue
        for supply in supplies:
            supply_id = _text(supply.get("supply_id") or supply.get("supplyId"))
            bundle_id = _text(supply.get("bundle_id") or supply.get("bundleId"))
            if not bundle_id and supply_id:
                try:
                    detail = adapter.fetch_supply_order_details(supply_id)
                    raw["details"].append({"supply_id": supply_id, "detail": detail})
                    detail_supplies = detail.get("supplies") if isinstance(detail.get("supplies"), list) else []
                    detail_supply = next(
                        (
                            row
                            for row in detail_supplies
                            if isinstance(row, dict)
                            and _text(row.get("supply_id") or row.get("supplyId")) == supply_id
                        ),
                        detail_supplies[0] if detail_supplies else {},
                    )
                    content = detail_supply.get("content") if isinstance(detail_supply, dict) else {}
                    bundle_id = _text((content or {}).get("bundle_id") or (content or {}).get("bundleId"))
                except Exception as exc:  # noqa: BLE001 - one supply must not hide the others.
                    warnings.append(f"Не получены детали поставки {supply_id}: {_safe_error(exc)}")
            goods: list[dict[str, Any]] = []
            if bundle_id:
                try:
                    goods = adapter.fetch_supply_order_bundle(bundle_id)
                    raw["bundles"].append({"supply_id": supply_id, "bundle_id": bundle_id, "items": goods})
                except Exception as exc:  # noqa: BLE001 - aggregate order remains useful.
                    warnings.append(f"Не получен состав поставки {supply_id or order_id}: {_safe_error(exc)}")
            else:
                warnings.append(f"У поставки {supply_id or order_id} не найден bundle_id; состав не подтвержден.")
            physical = 0
            for good in goods:
                quantity = _int(good.get("quantity"))
                pack_qty, mapped = _pack_qty(
                    catalog,
                    offer_id=_text(good.get("offer_id")),
                    sku=_text(good.get("sku")),
                    product_id=_text(good.get("product_id")),
                )
                if not mapped:
                    unmapped_units += quantity
                physical += quantity * pack_qty
            normalized.append(
                _supply_row(
                    order=order,
                    order_id=order_id,
                    order_number=order_number,
                    order_state=order_state,
                    supply=supply,
                    goods=goods,
                    mapped_physical=physical,
                )
            )
    if virtual_orders:
        warnings.append(f"Исключено виртуальных заявок-дублей: {virtual_orders}.")
    if unmapped_units:
        warnings.append(
            f"Для {unmapped_units} товарных единиц активных поставок не найден pack_qty; использовано значение 1."
        )
    by_state: dict[str, dict[str, Any]] = {}
    for state in MONITORED_ORDER_STATES:
        state_rows = [row for row in normalized if row["state"] == state]
        by_state[state] = {
            "label": STATE_LABELS.get(state, state),
            "supplies": len(state_rows),
            "quantity": sum(_int(row["quantity"]) for row in state_rows),
            "physical_quantity": sum(_int(row["physical_quantity"]) for row in state_rows),
        }
    confirmed_rows = [row for row in normalized if row["state"] in CONFIRMED_INBOUND_STATES]
    metrics = {
        "orders": len({row["order_id"] for row in normalized if row["order_id"]}),
        "supplies": len(normalized),
        "quantity": sum(_int(row["quantity"]) for row in normalized),
        "physical_quantity": sum(_int(row["physical_quantity"]) for row in normalized),
        "confirmed_inbound_quantity": sum(_int(row["quantity"]) for row in confirmed_rows),
        "confirmed_inbound_physical": sum(_int(row["physical_quantity"]) for row in confirmed_rows),
        "virtual_orders": virtual_orders,
        "by_state": by_state,
    }
    normalized.sort(key=lambda row: (row["state"], row["created_date"], row["order_id"], row["supply_id"]))
    return {"rows": normalized, "metrics": metrics, "warnings": warnings, "raw": raw}


def _supply_row(
    *,
    order: dict[str, Any],
    order_id: str,
    order_number: str,
    order_state: str,
    supply: dict[str, Any],
    goods: list[dict[str, Any]],
    mapped_physical: int,
) -> dict[str, Any]:
    supply_state = _text(supply.get("state") or supply.get("supply_state")) or order_state
    storage = supply.get("storage_warehouse") if isinstance(supply.get("storage_warehouse"), dict) else {}
    dropoff = order.get("drop_off_warehouse") if isinstance(order.get("drop_off_warehouse"), dict) else {}
    timeslot = order.get("timeslot") if isinstance(order.get("timeslot"), dict) else {}
    slot_value = timeslot.get("timeslot") if isinstance(timeslot.get("timeslot"), dict) else timeslot
    quantity = sum(_int(good.get("quantity")) for good in goods)
    cluster_id = _text(supply.get("macrolocal_cluster_id") or supply.get("macrolocalClusterId"))
    return {
        "order_id": order_id,
        "order_number": order_number,
        "supply_id": _text(supply.get("supply_id") or supply.get("supplyId")),
        "state": supply_state,
        "state_label": STATE_LABELS.get(supply_state, supply_state or "Не указан"),
        "confirmed_inbound": supply_state in CONFIRMED_INBOUND_STATES,
        "created_date": _date_text(order.get("created_date")),
        "timeslot_from": _date_text((slot_value or {}).get("from") if isinstance(slot_value, dict) else ""),
        "dropoff_warehouse": _text(dropoff.get("name")),
        "storage_warehouse": _text(storage.get("name")) or (f"Кластер {cluster_id}" if cluster_id else "Не указан"),
        "macrolocal_cluster_id": cluster_id,
        "quantity": quantity,
        "physical_quantity": mapped_physical,
        "goods_count": len(goods),
    }


def _product_identity(
    product_list: list[dict[str, Any]], product_info: list[dict[str, Any]]
) -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    for source in [*product_list, *product_info]:
        offer_id = _text(source.get("offer_id"))
        product_id = _text(source.get("product_id") or source.get("id"))
        sku = _text(source.get("sku") or source.get("fbo_sku") or source.get("fboSku"))
        value = {
            "offer_id": offer_id,
            "product_id": product_id,
            "sku": sku,
            "title": _text(source.get("name") or source.get("title")),
        }
        if offer_id:
            existing = index.setdefault(f"offer:{offer_id}", {})
            existing.update({key: item for key, item in value.items() if item})
            value = existing
        if product_id:
            index[f"product:{product_id}"] = value
        if sku:
            index[f"sku:{sku}"] = value
    return index


def _load_catalog(path: Path) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return index
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            value = {
                "pack_qty": max(1, _int(row.get("pack_qty"), 1)),
                "internal_sku": _text(row.get("internal_sku")),
            }
            for prefix, field in (
                ("offer", "ozon_offer_id"),
                ("sku", "ozon_sku"),
                ("product", "ozon_product_id"),
            ):
                identifier = _text(row.get(field))
                if identifier:
                    index[f"{prefix}:{identifier}"] = value
    return index


def _pack_qty(
    catalog: dict[str, dict[str, Any]], *, offer_id: str = "", sku: str = "", product_id: str = ""
) -> tuple[int, bool]:
    row = (
        (catalog.get(f"offer:{offer_id}") if offer_id else None)
        or (catalog.get(f"sku:{sku}") if sku else None)
        or (catalog.get(f"product:{product_id}") if product_id else None)
    )
    return max(1, _int((row or {}).get("pack_qty"), 1)), bool(row)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields: list[str] = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    if not fields:
        fields = ["status"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_report(path: Path, summary: dict[str, Any]) -> None:
    metrics = summary["metrics"]
    general = metrics["general_fbo"]
    warehouses = metrics["warehouses"]
    supplies = metrics["supplies"]
    reconciliation = metrics["reconciliation"]
    lines = [
        "# Ozon: остатки и поставки",
        "",
        "## Доступный FBO-остаток",
        "",
        f"- Общий FBO present: `{general['present']}` товарных единиц / `{general['physical_present']}` физических изделий.",
        f"- Зарезервировано: `{general['reserved']}` товарных единиц.",
        f"- Свободно по складскому отчету: `{warehouses['free_to_sell']}` товарных единиц / `{warehouses['physical_free_to_sell']}` физических изделий.",
        f"- Складов с товарными строками: `{metrics['warehouse_count']}`.",
        f"- Сверено по складам: free_to_sell `{reconciliation['warehouse_free_to_sell']}` + reserved `{reconciliation['warehouse_reserved']}` = `{reconciliation['warehouse_accounted']}`.",
        f"- Расхождение general present и warehouse accounted: `{reconciliation['difference']}`.",
        "",
        "Общий и складской источники не складываются: они описывают один остаток в разных разрезах.",
        "",
        "## По складам",
        "",
    ]
    for row in summary["warehouses"]:
        lines.append(
            f"- {row['warehouse_name']}: свободно `{row['free_to_sell']}` / физических `{row['physical_free_to_sell']}`; "
            f"резерв `{row['reserved']}`; обещано `{row['promised']}`."
        )
    lines.extend(
        [
            "",
            "## Активные заявки и поставки",
            "",
            f"- Заявок: `{supplies['orders']}`; поставок: `{supplies['supplies']}`.",
            f"- Состав активного контура: `{supplies['quantity']}` товарных единиц / `{supplies['physical_quantity']}` физических изделий.",
            f"- Подтвержденный inbound до завершения приемки: `{supplies['confirmed_inbound_quantity']}` / физических `{supplies['confirmed_inbound_physical']}`.",
            f"- Исключено виртуальных заявок-дублей: `{supplies['virtual_orders']}`.",
            "",
            "Активные поставки не прибавляются к доступному остатку: во время приемки источники могут пересекаться.",
            "",
        ]
    )
    for state in MONITORED_ORDER_STATES:
        row = supplies["by_state"][state]
        if row["supplies"]:
            lines.append(
                f"- {row['label']}: поставок `{row['supplies']}`, товаров `{row['quantity']}`, физических изделий `{row['physical_quantity']}`."
            )
    if summary["active_supplies"]:
        lines.extend(["", "### Детали", ""])
        for row in summary["active_supplies"]:
            lines.append(
                f"- `{row['order_number'] or row['order_id']}` / `{row['supply_id'] or 'без supply_id'}`: "
                f"{row['state_label']}, {row['storage_warehouse']}, "
                f"`{row['quantity']}` товаров / `{row['physical_quantity']}` физических изделий."
            )
    if summary["warnings"]:
        lines.extend(["", "## Ограничения", ""])
        lines.extend(f"- {warning}" for warning in summary["warnings"])
    lines.extend(["", "Остатки и поставки Ozon не изменялись."])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _date_text(value: Any) -> str:
    return _text(value).replace("T", " ")[:19]


def _safe_error(exc: BaseException) -> str:
    return str(exc).replace("\n", " ").replace("\r", " ")[:500]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        return default
