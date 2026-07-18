from __future__ import annotations

from collections import defaultdict
import csv
from datetime import datetime
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from seller_agent.config import AppCredentials
from seller_agent.core.run_manifest import latest_run, write_summary_run_manifest
from seller_agent.marketplaces.wb.analytics_adapter import WbAnalyticsAdapter
from seller_agent.marketplaces.wb.fbw_supplies_adapter import WbFbwSuppliesAdapter
from seller_agent.reports.writer import ensure_dir, write_json


MOSCOW = ZoneInfo("Europe/Moscow")
ACTIVE_STATUS_IDS = {1, 2, 3, 4, 6}
STATUS_LABELS = {
    1: "Не запланировано",
    2: "Запланировано",
    3: "Отгрузка разрешена",
    4: "Идет приемка",
    5: "Принято",
    6: "Выгружено на воротах",
}


def run_wb_stock_supply_monitor(
    *,
    data_dir: Path,
    credentials: AppCredentials | None = None,
    run_id: str | None = None,
    analytics_adapter: WbAnalyticsAdapter | None = None,
    supplies_adapter: WbFbwSuppliesAdapter | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    started_at = now or datetime.now(MOSCOW)
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=MOSCOW)
    run_id = run_id or f"wb_stock_supply_monitor_{started_at.strftime('%Y%m%dT%H%M%S')}"
    run_dir = ensure_dir(data_dir / "runs" / started_at.date().isoformat() / run_id)
    raw_dir = ensure_dir(run_dir / "raw")

    if analytics_adapter is None or supplies_adapter is None:
        if credentials is None or credentials.wb is None:
            raise ValueError("WB API credentials are not configured")
        analytics_adapter = analytics_adapter or WbAnalyticsAdapter(credentials.wb)
        supplies_adapter = supplies_adapter or WbFbwSuppliesAdapter(credentials.wb)

    previous = _load_previous_summary(data_dir)
    catalog = _load_catalog_index(data_dir / "catalog" / "unified" / "products.csv")
    warnings: list[str] = []

    stock_rows = analytics_adapter.fetch_wb_warehouse_stocks()
    write_json(raw_dir / "wb_warehouse_stocks.json", stock_rows)
    warehouses, stock_metrics, stock_unmapped = _aggregate_warehouses(stock_rows, catalog)
    if stock_unmapped:
        warnings.append(
            f"Для {stock_unmapped} строк остатков не найден pack_qty; для физических изделий использовано значение 1."
        )

    supply_list = supplies_adapter.fetch_supplies()
    write_json(raw_dir / "wb_supplies.json", supply_list)
    active_supplies: list[dict[str, Any]] = []
    supply_details_raw: list[dict[str, Any]] = []
    supply_goods_raw: list[dict[str, Any]] = []
    supply_unmapped_units = 0
    for supply in supply_list:
        status_id = _int(supply.get("statusID") or supply.get("statusId"))
        if status_id not in ACTIVE_STATUS_IDS:
            continue
        supply_id = supply.get("supplyID") or supply.get("supplyId")
        if not supply_id:
            warnings.append("Активная поставка WB без supplyID пропущена.")
            continue
        try:
            detail = supplies_adapter.fetch_supply(supply_id)
        except Exception as exc:  # noqa: BLE001 - one supply must not hide the others.
            detail = {}
            warnings.append(f"Не получены детали поставки {supply_id}: {_safe_error(exc)}")
        try:
            goods = supplies_adapter.fetch_supply_goods(supply_id)
        except Exception as exc:  # noqa: BLE001 - aggregate detail remains useful.
            goods = []
            warnings.append(f"Не получен состав поставки {supply_id}: {_safe_error(exc)}")
        supply_details_raw.append({"supply_id": supply_id, "detail": detail})
        supply_goods_raw.append({"supply_id": supply_id, "goods": goods})
        row, unmapped_units = _normalize_supply(supply_id, supply, detail, goods, catalog)
        supply_unmapped_units += unmapped_units
        active_supplies.append(row)
    write_json(raw_dir / "wb_supply_details.json", supply_details_raw)
    write_json(raw_dir / "wb_supply_goods.json", supply_goods_raw)
    if supply_unmapped_units:
        warnings.append(
            f"Для {supply_unmapped_units} товарных единиц активных поставок не найден pack_qty; использовано значение 1."
        )

    active_supplies.sort(key=lambda row: (_int(row.get("status_id")), str(row.get("supply_date") or ""), str(row["supply_id"])))
    supply_metrics = _summarize_supplies(active_supplies)
    anomalies = _detect_anomalies(warehouses, active_supplies, previous)
    if anomalies:
        warnings.append(f"Обнаружено предупреждений по состояниям WB: {len(anomalies)}.")

    report_path = run_dir / "wb_stock_supply_monitor.md"
    warehouses_csv = run_dir / "wb_warehouses.csv"
    supplies_csv = run_dir / "wb_active_supplies.csv"
    summary_path = run_dir / "summary.json"
    _write_csv(warehouses_csv, warehouses)
    _write_csv(supplies_csv, active_supplies)

    metrics = {
        "stocks": stock_metrics,
        "supplies": supply_metrics,
        "warehouse_count": len(warehouses),
        "active_supply_count": len(active_supplies),
        "anomaly_count": len(anomalies),
    }
    summary = {
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "overall_status": "warning" if warnings else "ok",
        "metrics": metrics,
        "warehouses": warehouses,
        "active_supplies": active_supplies,
        "anomalies": anomalies,
        "warnings": warnings,
        "previous_run_id": str(previous.get("run_id") or ""),
        "sources": [
            "WB POST /api/analytics/v1/stocks-report/wb-warehouses",
            "WB POST /api/v1/supplies",
            "WB GET /api/v1/supplies/{supplyID}",
            "WB GET /api/v1/supplies/{supplyID}/goods",
        ],
        "artifacts": {
            "report": str(report_path),
            "warehouses_csv": str(warehouses_csv),
            "supplies_csv": str(supplies_csv),
            "summary": str(summary_path),
        },
    }
    _write_report(report_path, summary)
    write_json(summary_path, summary)
    manifest_artifacts = write_summary_run_manifest(
        data_dir=data_dir,
        run_dir=run_dir,
        summary=summary,
        task="wb-stock-supply-monitor",
        mode="read_only",
        risk="low",
        marketplaces=["wb"],
        lifecycle_status="closed",
        closed=True,
    )
    summary["artifacts"].update(manifest_artifacts)
    write_json(summary_path, summary)
    return summary


def _aggregate_warehouses(
    rows: list[dict[str, Any]],
    catalog: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int], int]:
    grouped: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "warehouse_id": "",
            "warehouse_name": "Не указан",
            "region_name": "",
            "quantity": 0,
            "physical_quantity": 0,
            "in_way_to_client": 0,
            "in_way_from_client": 0,
            "total_state_units": 0,
        }
    )
    unmapped_rows = 0
    for source in rows:
        warehouse_id = _text(source.get("warehouseId") or source.get("warehouse_id"))
        warehouse_name = _text(source.get("warehouseName") or source.get("warehouse_name")) or "Не указан"
        key = (warehouse_id, warehouse_name)
        target = grouped[key]
        target["warehouse_id"] = warehouse_id
        target["warehouse_name"] = warehouse_name
        target["region_name"] = _text(source.get("regionName") or source.get("region_name"))
        quantity = _int(source.get("quantity") or source.get("qty") or source.get("stock"))
        to_client = _int(source.get("inWayToClient") or source.get("in_way_to_client"))
        from_client = _int(source.get("inWayFromClient") or source.get("in_way_from_client"))
        nm_id = _text(source.get("nmId") or source.get("nm_id"))
        pack_qty = _pack_qty(catalog, nm_id=nm_id)
        if nm_id and nm_id not in catalog:
            unmapped_rows += 1
        target["quantity"] += quantity
        target["physical_quantity"] += quantity * pack_qty
        target["in_way_to_client"] += to_client
        target["in_way_from_client"] += from_client
        target["total_state_units"] += quantity + to_client + from_client
    warehouses = sorted(grouped.values(), key=lambda row: (-_int(row["quantity"]), str(row["warehouse_name"])))
    metrics = {
        "quantity": sum(_int(row["quantity"]) for row in warehouses),
        "physical_quantity": sum(_int(row["physical_quantity"]) for row in warehouses),
        "in_way_to_client": sum(_int(row["in_way_to_client"]) for row in warehouses),
        "in_way_from_client": sum(_int(row["in_way_from_client"]) for row in warehouses),
        "total_state_units": sum(_int(row["total_state_units"]) for row in warehouses),
    }
    return warehouses, metrics, unmapped_rows


def _normalize_supply(
    supply_id: Any,
    source: dict[str, Any],
    detail: dict[str, Any],
    goods: list[dict[str, Any]],
    catalog: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], int]:
    merged = {**source, **detail}
    status_id = _int(merged.get("statusID") or merged.get("statusId"))
    physical_quantity = 0
    physical_accepted = 0
    unmapped_units = 0
    for good in goods:
        units = _int(good.get("quantity"))
        accepted = _int(good.get("acceptedQuantity"))
        nm_id = _text(good.get("nmID") or good.get("nmId"))
        vendor_code = _text(good.get("vendorCode") or good.get("supplierArticle"))
        pack_qty = _pack_qty(catalog, nm_id=nm_id, vendor_code=vendor_code)
        if not _catalog_has(catalog, nm_id=nm_id, vendor_code=vendor_code):
            unmapped_units += units
        physical_quantity += units * pack_qty
        physical_accepted += accepted * pack_qty
    quantity = _int(merged.get("quantity"))
    accepted = _int(merged.get("acceptedQuantity"))
    if not goods:
        physical_quantity = quantity
        physical_accepted = accepted
    return (
        {
            "supply_id": str(supply_id),
            "status_id": status_id,
            "status": STATUS_LABELS.get(status_id, f"Статус {status_id}"),
            "warehouse_name": _text(
                merged.get("actualWarehouseName") or merged.get("warehouseName") or source.get("warehouseName")
            )
            or "Не указан",
            "create_date": _date_text(merged.get("createDate")),
            "supply_date": _date_text(merged.get("supplyDate")),
            "fact_date": _date_text(merged.get("factDate")),
            "quantity": quantity,
            "physical_quantity": physical_quantity,
            "accepted_quantity": accepted,
            "physical_accepted_quantity": physical_accepted,
            "ready_for_sale_quantity": _int(merged.get("readyForSaleQuantity")),
            "unloading_quantity": _int(merged.get("unloadingQuantity")),
            "pending_acceptance_quantity": max(0, quantity - accepted),
            "goods_count": len(goods),
        },
        unmapped_units,
    )


def _summarize_supplies(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, dict[str, Any]] = {}
    for status_id in sorted(ACTIVE_STATUS_IDS):
        status_rows = [row for row in rows if _int(row.get("status_id")) == status_id]
        by_status[str(status_id)] = {
            "status": STATUS_LABELS[status_id],
            "count": len(status_rows),
            "quantity": sum(_int(row.get("quantity")) for row in status_rows),
            "physical_quantity": sum(_int(row.get("physical_quantity")) for row in status_rows),
            "accepted_quantity": sum(_int(row.get("accepted_quantity")) for row in status_rows),
            "ready_for_sale_quantity": sum(_int(row.get("ready_for_sale_quantity")) for row in status_rows),
        }
    return {
        "count": len(rows),
        "quantity": sum(_int(row.get("quantity")) for row in rows),
        "physical_quantity": sum(_int(row.get("physical_quantity")) for row in rows),
        "accepted_quantity": sum(_int(row.get("accepted_quantity")) for row in rows),
        "ready_for_sale_quantity": sum(_int(row.get("ready_for_sale_quantity")) for row in rows),
        "by_status": by_status,
    }


def _detect_anomalies(
    warehouses: list[dict[str, Any]],
    active_supplies: list[dict[str, Any]],
    previous: dict[str, Any],
) -> list[dict[str, Any]]:
    anomalies: list[dict[str, Any]] = []
    acceptance_warehouses = {
        _normalize_name(row.get("warehouse_name"))
        for row in active_supplies
        if _int(row.get("status_id")) == 4
    }
    for row in warehouses:
        quantity = _int(row.get("quantity"))
        from_client = _int(row.get("in_way_from_client"))
        if from_client >= 100 and from_client >= max(1, quantity * 2):
            warehouse = str(row.get("warehouse_name") or "Не указан")
            has_acceptance = _normalize_name(warehouse) in acceptance_warehouses
            anomalies.append(
                {
                    "code": "in_way_from_client_spike",
                    "warehouse": warehouse,
                    "severity": "warning",
                    "message": (
                        f"{warehouse}: inWayFromClient={from_client} при quantity={quantity}. "
                        + (
                            "На этом складе идет приемка FBW; показатель нельзя считать возвратами без сверки движения."
                            if has_acceptance
                            else "Показатель требует сверки с поставками и предыдущим снимком; это не подтвержденные возвраты."
                        )
                    ),
                }
            )

    previous_by_warehouse = {
        _normalize_name(row.get("warehouse_name")): row
        for row in previous.get("warehouses", [])
        if isinstance(row, dict)
    }
    for row in warehouses:
        old = previous_by_warehouse.get(_normalize_name(row.get("warehouse_name")))
        if not old:
            continue
        old_quantity = _int(old.get("quantity"))
        old_total = _int(old.get("total_state_units"))
        new_quantity = _int(row.get("quantity"))
        new_total = _int(row.get("total_state_units"))
        from_growth = _int(row.get("in_way_from_client")) - _int(old.get("in_way_from_client"))
        quantity_drop = old_quantity - new_quantity
        total_tolerance = max(20, int(max(old_total, 1) * 0.1))
        if quantity_drop >= 50 and from_growth >= 50 and abs(new_total - old_total) <= total_tolerance:
            warehouse = str(row.get("warehouse_name") or "Не указан")
            anomalies.append(
                {
                    "code": "state_reclassification",
                    "warehouse": warehouse,
                    "severity": "warning",
                    "message": (
                        f"{warehouse}: quantity изменился {old_quantity}->{new_quantity}, "
                        f"inWayFromClient вырос на {from_growth}, общая масса {old_total}->{new_total}. "
                        "Похоже на переклассификацию состояний WB, а не на массовые возвраты."
                    ),
                }
            )
    return anomalies


def _load_catalog_index(path: Path) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return index
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            pack_qty = max(1, _int(row.get("pack_qty"), 1))
            value = {"pack_qty": pack_qty, "internal_sku": _text(row.get("internal_sku"))}
            nm_id = _text(row.get("wb_nm_id"))
            vendor_code = _text(row.get("wb_vendor_code"))
            if nm_id:
                index[nm_id] = value
            if vendor_code:
                index[f"vendor:{vendor_code}"] = value
    return index


def _catalog_has(catalog: dict[str, dict[str, Any]], *, nm_id: str = "", vendor_code: str = "") -> bool:
    return bool((nm_id and nm_id in catalog) or (vendor_code and f"vendor:{vendor_code}" in catalog))


def _pack_qty(catalog: dict[str, dict[str, Any]], *, nm_id: str = "", vendor_code: str = "") -> int:
    row = catalog.get(nm_id) if nm_id else None
    row = row or (catalog.get(f"vendor:{vendor_code}") if vendor_code else None)
    return max(1, _int((row or {}).get("pack_qty"), 1))


def _load_previous_summary(data_dir: Path) -> dict[str, Any]:
    run = latest_run(data_dir=data_dir, task="wb-stock-supply-monitor")
    if not run:
        return {}
    path = ((run.get("artifacts") or {}).get("summary")) if isinstance(run.get("artifacts"), dict) else ""
    if not path:
        return {}
    try:
        value = json.loads(Path(str(path)).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(rows[0]) if rows else ["status"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_report(path: Path, summary: dict[str, Any]) -> None:
    metrics = summary["metrics"]
    stocks = metrics["stocks"]
    supplies = metrics["supplies"]
    lines = [
        "# WB: остатки и поставки",
        "",
        f"Сформировано: `{summary['started_at']}`",
        f"Run ID: `{summary['run_id']}`",
        "Режим: `read-only`.",
        "",
        "## Итог",
        "",
        f"- Доступный остаток `quantity`: `{stocks['quantity']}` товарных единиц / `{stocks['physical_quantity']}` физических изделий.",
        f"- В пути к покупателю: `{stocks['in_way_to_client']}`.",
        f"- Поле `inWayFromClient`: `{stocks['in_way_from_client']}`; оно не считается возвратами без сверки.",
        f"- Активных поставок: `{supplies['count']}`, объем: `{supplies['quantity']}` товарных единиц / `{supplies['physical_quantity']}` физических изделий.",
        "- Остатки и активные поставки не складываются в доступный остаток.",
        "",
        "## Активные поставки по статусам",
        "",
        "| Статус | Поставок | Товарных единиц | Физических изделий | Принято | Готово к продаже |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for status_id in sorted(ACTIVE_STATUS_IDS):
        row = supplies["by_status"][str(status_id)]
        lines.append(
            f"| {row['status']} | {row['count']} | {row['quantity']} | {row['physical_quantity']} | "
            f"{row['accepted_quantity']} | {row['ready_for_sale_quantity']} |"
        )
    lines.extend(
        [
            "",
            "## Активные поставки",
            "",
            "| ID | Статус | Склад | Создана | Плановая дата | Единиц WB | Физических изделий | Принято | Готово | Разгружается |",
            "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    if summary["active_supplies"]:
        for row in summary["active_supplies"]:
            lines.append(
                f"| {row['supply_id']} | {row['status']} | {row['warehouse_name']} | {row['create_date'] or '-'} | "
                f"{row['supply_date'] or '-'} | {row['quantity']} | {row['physical_quantity']} | "
                f"{row['accepted_quantity']} | {row['ready_for_sale_quantity']} | {row['unloading_quantity']} |"
            )
    else:
        lines.append("| - | Активных поставок нет | - | - | - | 0 | 0 | 0 | 0 | 0 |")
    lines.extend(
        [
            "",
            "## Остатки по складам",
            "",
            "| Склад | Регион | Доступно | Физических изделий | К покупателю | inWayFromClient | Общая масса состояний |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in summary["warehouses"]:
        lines.append(
            f"| {row['warehouse_name']} | {row['region_name'] or '-'} | {row['quantity']} | "
            f"{row['physical_quantity']} | {row['in_way_to_client']} | {row['in_way_from_client']} | "
            f"{row['total_state_units']} |"
        )
    lines.extend(["", "## Предупреждения", ""])
    if summary["anomalies"]:
        lines.extend(f"- {row['message']}" for row in summary["anomalies"])
    else:
        lines.append("- Резких аномалий по текущему и предыдущему снимкам не обнаружено.")
    lines.extend(f"- {warning}" for warning in summary["warnings"] if "предупреждений по состояниям" not in warning)
    lines.extend(["", "## Источники", ""])
    lines.extend(f"- {source}" for source in summary["sources"])
    lines.extend(["", "Изменений в WB не выполнялось.", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def _date_text(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    return text[:10]


def _normalize_name(value: Any) -> str:
    return " ".join(_text(value).lower().replace("ё", "е").split())


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any, default: int = 0) -> int:
    if value in (None, ""):
        return default
    try:
        return int(float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        return default


def _safe_error(exc: BaseException) -> str:
    text = str(exc).replace("\n", " ").replace("\r", " ")
    for marker in ("token", "secret", "cookie", "storage", "auth", "api_key", "password"):
        text = text.replace(marker, "<redacted>")
    return text[:300]
