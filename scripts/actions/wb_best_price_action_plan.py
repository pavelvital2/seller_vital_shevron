#!/usr/bin/env python3
"""Build a read-only WB action plan constrained by per-product minimum prices."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.pricing.wb_price_grid import (  # noqa: E402
    minimum_is_active,
    minimum_number,
    parse_minimum_xlsx,
)


WB_MAX_DISCOUNT_STEP_PERCENTAGE_POINTS = 35
WB_QUARANTINE_SAFE_PRICE_DROP_PERCENT = Decimal("33")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--prices", type=Path, required=True)
    parser.add_argument("--price-plan", type=Path, required=True)
    parser.add_argument("--minimum-workbook", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--outside-discount", type=int, default=50)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def price_plan_with_fresh_minimums(
    reference_plan: dict[str, Any],
    minimum_workbook_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    parsed_rows = parse_minimum_xlsx(minimum_workbook_path)
    rows_by_nm: dict[int, dict[str, str]] = {}
    duplicates: set[int] = set()
    for row in parsed_rows:
        raw_nm_id = str(row.get("Артикул WB") or "").strip()
        if not raw_nm_id:
            continue
        nm_id = int(raw_nm_id)
        if nm_id in rows_by_nm:
            duplicates.add(nm_id)
        rows_by_nm[nm_id] = row

    effective_rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    distribution: Counter[int] = Counter()
    changed_from_reference = 0
    for source in reference_plan.get("rows", []):
        nm_id = int(source["nm_id"])
        current = rows_by_nm.get(nm_id)
        if current is None:
            issues.append({"nm_id": nm_id, "reason": "missing"})
            continue
        if nm_id in duplicates:
            issues.append({"nm_id": nm_id, "reason": "duplicate"})
            continue
        current_text = current.get(
            "Текущая минимальная цена для применения скидки по автоакции",
            "",
        )
        try:
            current_minimum = minimum_number(current_text)
            active = minimum_is_active(current_text)
        except ValueError:
            issues.append(
                {
                    "nm_id": nm_id,
                    "reason": "invalid_minimum",
                    "actual": current_text,
                }
            )
            continue
        if not active:
            issues.append(
                {
                    "nm_id": nm_id,
                    "reason": "minimum_not_active",
                    "actual": current_text,
                }
            )
            continue

        reference_minimum = int(source["target_minimum"])
        effective = dict(source)
        effective["reference_minimum"] = reference_minimum
        effective["target_minimum"] = current_minimum
        effective_rows.append(effective)
        distribution[current_minimum] += 1
        changed_from_reference += int(current_minimum != reference_minimum)

    snapshot = {
        "status": "ok" if not issues else "blocked",
        "workbook": str(minimum_workbook_path),
        "workbook_sha256": file_sha256(minimum_workbook_path),
        "reference_rows": len(reference_plan.get("rows", [])),
        "verified_active_rows": len(effective_rows),
        "changed_from_reference": changed_from_reference,
        "minimum_distribution": {
            str(value): count for value, count in sorted(distribution.items())
        },
        "issues": issues,
    }
    if issues:
        raise RuntimeError(
            "fresh WB minimum-price workbook is incomplete for action scope: "
            f"{len(issues)} rows"
        )

    effective_plan = dict(reference_plan)
    effective_plan["rows"] = effective_rows
    effective_plan["minimum_source"] = {
        "kind": "fresh_wb_minimum_workbook",
        "workbook": str(minimum_workbook_path),
        "workbook_sha256": snapshot["workbook_sha256"],
    }
    return effective_plan, snapshot


def clean_header(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u00a0", " ")).strip()


def decimal_value(value: Any) -> Decimal:
    return Decimal(str(value).strip().replace(" ", "").replace(",", "."))


def money(value: Decimal | int | float) -> str:
    dec = Decimal(str(value)).quantize(Decimal("0.01"))
    if dec == dec.to_integral_value():
        return f"{int(dec):,}".replace(",", " ")
    return f"{dec:,.2f}".replace(",", " ").replace(".", ",")


def required_discount(base_price: Decimal, plan_price: Decimal) -> int:
    raw = (Decimal("1") - plan_price / base_price) * Decimal("100")
    return int(raw.to_integral_value(rounding=ROUND_CEILING))


def actual_price(base_price: Decimal, discount: int) -> Decimal:
    return (base_price * (Decimal("100") - Decimal(discount)) / Decimal("100")).quantize(
        Decimal("0.01")
    )


def optional_discount(value: Any) -> int | None:
    try:
        discount = decimal_value(value)
    except (ArithmeticError, TypeError, ValueError):
        return None
    if discount != discount.to_integral_value():
        return None
    result = int(discount)
    return result if 0 <= result <= 99 else None


def promo_id_from_filename(path: Path) -> int:
    match = re.search(r"promo-(\d+)-", path.name)
    if not match:
        raise ValueError(f"Cannot parse action ID from {path.name}")
    return int(match.group(1))


def read_promo_rows(
    snapshot_path: Path,
    *,
    scope_nm_ids: set[int] | None = None,
) -> tuple[dict[int, dict[str, Any]], dict[int, list[dict[str, Any]]]]:
    snapshot = read_json(snapshot_path)
    promos = {int(item["actionID"]): item for item in snapshot.get("promos", [])}
    rows_by_nm: dict[int, list[dict[str, Any]]] = defaultdict(list)
    excel_dir = snapshot_path.parent / "excel"

    for path in sorted(excel_dir.glob("*.xlsx")):
        action_id = promo_id_from_filename(path)
        promo = promos.get(action_id)
        if not promo:
            continue
        workbook = load_workbook(path, read_only=False, data_only=True)
        sheet = workbook.active
        rows = sheet.iter_rows(values_only=True)
        header = next(rows)
        idx = {clean_header(name): pos for pos, name in enumerate(header)}
        required = (
            "Товар уже участвует в акции",
            "Артикул поставщика",
            "Артикул WB",
            "Плановая цена для акции",
            "Текущая розничная цена",
            "Текущая скидка на сайте, %",
            "Загружаемая скидка для участия в акции",
            "Статус",
        )
        missing = [name for name in required if name not in idx]
        if missing:
            raise ValueError(f"{path.name}: missing columns {missing}")

        for row in rows:
            raw_nm = row[idx["Артикул WB"]]
            if raw_nm in (None, ""):
                continue
            nm_id = int(raw_nm)
            if scope_nm_ids is not None and nm_id not in scope_nm_ids:
                continue
            base = decimal_value(row[idx["Текущая розничная цена"]])
            plan = decimal_value(row[idx["Плановая цена для акции"]])
            calculated = required_discount(base, plan)
            upload_raw = row[idx["Загружаемая скидка для участия в акции"]]
            upload = optional_discount(upload_raw)
            current_discount = optional_discount(row[idx["Текущая скидка на сайте, %"]])
            participates = clean_header(row[idx["Товар уже участвует в акции"]]).lower() == "да"
            status = clean_header(row[idx["Статус"]])

            available = True
            unavailable_reason = ""
            discount_source = "wb_upload_discount"
            target_discount: int | None = upload
            if participates:
                observed_discount = upload if upload is not None else current_discount
                if observed_discount is None or observed_discount < calculated:
                    available = False
                    unavailable_reason = "WB сообщает об участии, но текущая скидка не подтверждает плановую цену"
                    target_discount = None
                else:
                    # For participating goods WB echoes the current discount in the
                    # upload column. The smallest qualifying discount is calculated
                    # from the action plan price so the plan can choose the highest
                    # buyer price that still keeps the product in the action.
                    target_discount = calculated
                    discount_source = "calculated_from_plan_price_active"
            elif upload is None:
                available = False
                unavailable_reason = status or "WB не предоставил числовую скидку для вступления в акцию"
                target_discount = None
            elif upload < calculated:
                available = False
                unavailable_reason = "Скидка WB не обеспечивает указанную плановую цену акции"
                target_discount = None

            rows_by_nm[nm_id].append(
                {
                    "action_id": action_id,
                    "action_name": promo["name"],
                    "action_start": promo.get("startDate", ""),
                    "action_end": promo.get("endDate", ""),
                    "currently_participates": participates,
                    "status": status,
                    "vendor_code": clean_header(row[idx["Артикул поставщика"]]),
                    "plan_price": plan,
                    "required_discount": target_discount,
                    "reported_upload_discount": upload,
                    "discount_source": discount_source,
                    "actual_price": (
                        actual_price(base, target_discount)
                        if target_discount is not None
                        else None
                    ),
                    "available": available,
                    "unavailable_reason": unavailable_reason,
                }
            )
        workbook.close()
    return promos, rows_by_nm


def build_plan(
    *,
    snapshot: dict[str, Any],
    prices: dict[str, Any],
    price_plan: dict[str, Any],
    promo_rows_by_nm: dict[int, list[dict[str, Any]]],
    outside_discount: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    current_prices = {int(item["nmID"]): item for item in prices["goods"]}
    product_rows: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    summary: dict[str, Any] = {
        "scope_total": 0,
        "currently_participating": 0,
        "offered_any_action": 0,
        "not_offered": 0,
        "eligible_any_action": 0,
        "offered_but_below_minimum": 0,
        "offered_but_unavailable": 0,
        "selected_multiple_choice": 0,
        "outside_action": 0,
        "to_change_discount": 0,
        "no_change_discount": 0,
        "selected_by_action": Counter(),
        "target_discount_distribution": Counter(),
        "selected_discount_distribution": Counter(),
        "selected_pack_distribution": Counter(),
        "rejected_candidate_reasons": Counter(),
        "unavailable_action_offers": 0,
        "plan_price_false_positives": 0,
        "target_below_minimum": 0,
        "unsafe_single_upload": 0,
    }

    for source in price_plan["rows"]:
        nm_id = int(source["nm_id"])
        current = current_prices.get(nm_id)
        if not current:
            raise ValueError(f"nmID {nm_id} from price plan is absent in fresh prices")
        base = decimal_value((current.get("prices") or [None])[0])
        current_discount = int(current.get("discount") or 0)
        current_discounted = decimal_value((current.get("discountedPrices") or [None])[0])
        minimum = decimal_value(source["target_minimum"])
        pack_qty = int(source["pack_qty"])
        offers = promo_rows_by_nm.get(nm_id, [])
        eligible: list[dict[str, Any]] = []
        current_participation = any(item["currently_participates"] for item in offers)
        available_offers = [item for item in offers if item.get("available", True)]

        for offer in offers:
            if not offer.get("available", True):
                row = {
                    "nm_id": nm_id,
                    "vendor_code": current.get("vendorCode") or source.get("vendor_code", ""),
                    "title": current.get("title") or "",
                    "pack_qty": pack_qty,
                    "minimum": minimum,
                    **offer,
                    "eligible": False,
                    "selected": False,
                    "reason": f"исключено: {offer.get('unavailable_reason') or 'предложение акции недоступно'}",
                }
                candidate_rows.append(row)
                summary["unavailable_action_offers"] += 1
                summary["rejected_candidate_reasons"]["акция не предоставила числовую скидку"] += 1
                continue
            is_eligible = offer["actual_price"] >= minimum
            plan_only_passes = offer["plan_price"] >= minimum and not is_eligible
            reason = (
                "проходит: итоговая цена не ниже минимума"
                if is_eligible
                else "исключено: итоговая цена ниже минимума"
            )
            if plan_only_passes:
                reason += "; плановая цена проходит, но округление скидки опускает цену ниже"
                summary["plan_price_false_positives"] += 1
            row = {
                "nm_id": nm_id,
                "vendor_code": current.get("vendorCode") or source.get("vendor_code", ""),
                "title": current.get("title") or "",
                "pack_qty": pack_qty,
                "minimum": minimum,
                **offer,
                "eligible": is_eligible,
                "selected": False,
                "reason": reason,
            }
            candidate_rows.append(row)
            if is_eligible:
                eligible.append(row)
            else:
                summary["rejected_candidate_reasons"]["цена ниже минимума"] += 1

        chosen: dict[str, Any] | None = None
        if eligible:
            chosen = max(
                eligible,
                key=lambda item: (
                    item["actual_price"],
                    item["plan_price"],
                    -item["required_discount"],
                    -item["action_id"],
                ),
            )
            chosen["selected"] = True
            target_discount = int(chosen["required_discount"])
            target_price = chosen["actual_price"]
            decision = "Включить в лучшую допустимую акцию"
            reason = (
                f"выбрана максимальная допустимая цена {money(target_price)} ₽; "
                f"минимум {money(minimum)} ₽"
            )
        else:
            target_discount = outside_discount
            target_price = actual_price(base, outside_discount)
            decision = f"Оставить вне акций со скидкой {outside_discount}%"
            if offers:
                reason = "все доступные активные акции дают цену ниже минимума"
            else:
                reason = "товар отсутствует в доступных активных акциях"

        delta = target_discount - current_discount
        price_drop_percent = (
            max(
                Decimal("0"),
                (current_discounted - target_price) / current_discounted * Decimal("100"),
            )
            if current_discounted > 0
            else Decimal("0")
        )
        target_below_minimum = target_price < minimum
        safe_single_upload = (
            not target_below_minimum
            and delta <= WB_MAX_DISCOUNT_STEP_PERCENTAGE_POINTS
            and price_drop_percent <= WB_QUARANTINE_SAFE_PRICE_DROP_PERCENT
        )
        product_rows.append(
            {
                "nm_id": nm_id,
                "internal_sku": source.get("internal_sku", ""),
                "vendor_code": current.get("vendorCode") or source.get("vendor_code", ""),
                "title": current.get("title") or "",
                "pack_qty": pack_qty,
                "base_price": base,
                "minimum": minimum,
                "current_discount": current_discount,
                "current_price": current_discounted,
                "currently_participates": current_participation,
                "offered_actions": len(offers),
                "eligible_actions": len(eligible),
                "chosen_action_id": chosen["action_id"] if chosen else "",
                "chosen_action_name": chosen["action_name"] if chosen else "",
                "chosen_plan_price": chosen["plan_price"] if chosen else "",
                "target_discount": target_discount,
                "target_price": target_price,
                "gap_above_minimum": target_price - minimum,
                "discount_delta": delta,
                "price_drop_percent": price_drop_percent,
                "target_below_minimum": target_below_minimum,
                "safe_single_upload": safe_single_upload,
                "decision": decision,
                "reason": reason,
            }
        )

        summary["scope_total"] += 1
        summary["currently_participating"] += int(current_participation)
        summary["target_discount_distribution"][str(target_discount)] += 1
        if offers:
            summary["offered_any_action"] += 1
        else:
            summary["not_offered"] += 1
        if eligible:
            summary["eligible_any_action"] += 1
            summary["selected_by_action"][str(chosen["action_id"])] += 1
            summary["selected_discount_distribution"][str(target_discount)] += 1
            summary["selected_pack_distribution"][str(pack_qty)] += 1
            if len(eligible) > 1:
                summary["selected_multiple_choice"] += 1
        elif available_offers:
            summary["offered_but_below_minimum"] += 1
        elif offers:
            summary["offered_but_unavailable"] += 1
        else:
            summary["outside_action"] += 1
        if delta:
            summary["to_change_discount"] += 1
        else:
            summary["no_change_discount"] += 1
        summary["target_below_minimum"] += int(target_below_minimum)
        summary["unsafe_single_upload"] += int(not safe_single_upload)

    summary["outside_action"] = summary["scope_total"] - summary["eligible_any_action"]
    summary["selected_by_action"] = dict(sorted(summary["selected_by_action"].items()))
    summary["target_discount_distribution"] = dict(
        sorted(summary["target_discount_distribution"].items(), key=lambda item: int(item[0]))
    )
    summary["selected_discount_distribution"] = dict(
        sorted(summary["selected_discount_distribution"].items(), key=lambda item: int(item[0]))
    )
    summary["selected_pack_distribution"] = dict(
        sorted(summary["selected_pack_distribution"].items(), key=lambda item: int(item[0]))
    )
    summary["rejected_candidate_reasons"] = dict(summary["rejected_candidate_reasons"])
    summary["snapshot_checked_at"] = snapshot["checkedAt"]
    summary["active_actions"] = len(snapshot.get("promos", []))
    summary["future_actions"] = len(snapshot.get("futurePromos", []))
    return product_rows, candidate_rows, summary


def json_ready(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_ready(item) for item in value]
    return value


def style_sheet(sheet: Any) -> None:
    header_fill = PatternFill("solid", fgColor="1F4E78")
    for cell in sheet[1]:
        cell.font = Font(color="FFFFFF", bold=True)
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for column in sheet.columns:
        letter = get_column_letter(column[0].column)
        width = min(max(len(str(cell.value or "")) for cell in column) + 2, 52)
        sheet.column_dimensions[letter].width = max(width, 10)


def add_table_sheet(workbook: Workbook, title: str, headers: list[str], rows: list[list[Any]]) -> None:
    sheet = workbook.create_sheet(title)
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    style_sheet(sheet)


def write_xlsx(
    path: Path,
    *,
    summary: dict[str, Any],
    promos: dict[int, dict[str, Any]],
    product_rows: list[dict[str, Any]],
    candidate_rows: list[dict[str, Any]],
    outside_discount: int,
) -> None:
    workbook = Workbook()
    workbook.remove(workbook.active)
    summary_rows = [
        ["Всего товаров в расчете", summary["scope_total"]],
        ["Участвуют сейчас", summary["currently_participating"]],
        ["Предложены хотя бы одной активной акции", summary["offered_any_action"]],
        ["Не предложены активными акциями", summary["not_offered"]],
        ["Проходят минимум хотя бы в одной акции", summary["eligible_any_action"]],
        ["Доступны акциям, но цена ниже минимума", summary["offered_but_below_minimum"]],
        ["Предложений акций без числовой скидки", summary["unavailable_action_offers"]],
        [f"Будут вне акций со скидкой {outside_discount}%", summary["outside_action"]],
        ["Нужно изменить скидку", summary["to_change_discount"]],
        ["Скидка без изменения", summary["no_change_discount"]],
        ["Целевая цена ниже минимума", summary["target_below_minimum"]],
        ["Небезопасно для одного upload", summary["unsafe_single_upload"]],
    ]
    add_table_sheet(workbook, "Сводка", ["Показатель", "Значение"], summary_rows)
    add_table_sheet(
        workbook,
        "Товары",
        [
            "Артикул WB",
            "Артикул продавца",
            "Название",
            "Изделий в товаре",
            "Базовая цена",
            "Минимальная цена",
            "Текущая скидка, %",
            "Текущая цена",
            "Доступных акций",
            "Проходящих акций",
            "Выбранная акция",
            "Целевая скидка, %",
            "Целевая цена",
            "Запас над минимумом",
            "Снижение цены, %",
            "Безопасно для одного upload",
            "Решение",
            "Причина",
        ],
        [
            [
                row["nm_id"],
                row["vendor_code"],
                row["title"],
                row["pack_qty"],
                float(row["base_price"]),
                float(row["minimum"]),
                row["current_discount"],
                float(row["current_price"]),
                row["offered_actions"],
                row["eligible_actions"],
                row["chosen_action_name"],
                row["target_discount"],
                float(row["target_price"]),
                float(row["gap_above_minimum"]),
                float(row["price_drop_percent"]),
                "Да" if row["safe_single_upload"] else "Нет",
                row["decision"],
                row["reason"],
            ]
            for row in product_rows
        ],
    )
    add_table_sheet(
        workbook,
        "Кандидаты акций",
        [
            "Артикул WB",
            "Артикул продавца",
            "Название",
            "Изделий в товаре",
            "Минимальная цена",
            "ID акции",
            "Акция",
            "Плановая цена WB",
            "Требуемая скидка, %",
            "Фактическая цена",
            "Проходит минимум",
            "Выбрана",
            "Причина",
        ],
        [
            [
                row["nm_id"],
                row["vendor_code"],
                row["title"],
                row["pack_qty"],
                float(row["minimum"]),
                row["action_id"],
                row["action_name"],
                float(row["plan_price"]),
                row["required_discount"] if row["required_discount"] is not None else "",
                float(row["actual_price"]) if row["actual_price"] is not None else "",
                "Да" if row["eligible"] else "Нет",
                "Да" if row["selected"] else "Нет",
                row["reason"],
            ]
            for row in candidate_rows
        ],
    )
    add_table_sheet(
        workbook,
        "Акции",
        ["ID", "Название", "Начало UTC", "Окончание UTC", "Статус"],
        [
            [
                action_id,
                promo["name"],
                promo.get("startDate", ""),
                promo.get("endDate", ""),
                promo.get("status", ""),
            ]
            for action_id, promo in sorted(promos.items())
        ],
    )
    workbook.save(path)


def selected_rows_html(product_rows: list[dict[str, Any]]) -> str:
    selected = [row for row in product_rows if row["chosen_action_id"]]
    return "".join(
        "<tr>"
        f"<td>{row['nm_id']}</td>"
        f"<td>{html.escape(str(row['vendor_code']))}</td>"
        f"<td>{html.escape(str(row['title']))}</td>"
        f"<td>{row['pack_qty']}</td>"
        f"<td>{money(row['minimum'])} ₽</td>"
        f"<td>{html.escape(str(row['chosen_action_name']))}</td>"
        f"<td>{row['target_discount']}%</td>"
        f"<td>{money(row['target_price'])} ₽</td>"
        f"<td>+{money(row['gap_above_minimum'])} ₽</td>"
        "</tr>"
        for row in selected
    )


def write_html(
    path: Path,
    *,
    run_id: str,
    snapshot: dict[str, Any],
    summary: dict[str, Any],
    promos: dict[int, dict[str, Any]],
    product_rows: list[dict[str, Any]],
    outside_discount: int,
) -> None:
    actions = "".join(
        f"<li><strong>{action_id}</strong> · {html.escape(promo['name'])} "
        f"<span>{html.escape(promo.get('startDate', ''))} — "
        f"{html.escape(promo.get('endDate', ''))}</span></li>"
        for action_id, promo in sorted(promos.items())
    )
    discounts = "".join(
        f"<li><strong>{percent}%</strong><span>{count} товаров</span></li>"
        for percent, count in summary["target_discount_distribution"].items()
    )
    safety_class = "ok" if summary["unsafe_single_upload"] == 0 else "warn"
    safety_text = (
        "Пакет можно применить за один безопасный upload."
        if summary["unsafe_single_upload"] == 0
        else (
            f"{summary['unsafe_single_upload']} товаров нельзя безопасно изменить за один upload; "
            "кнопка применения должна быть заблокирована."
        )
    )
    document = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>WB: предварительный расчет акций</title>
  <style>
    :root {{ --ink:#17212b; --muted:#5f6b76; --line:#d9e0e5; --blue:#146c94;
      --green:#26734d; --amber:#9a6700; --bg:#f5f7f8; --white:#fff; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; color:var(--ink); background:var(--bg);
      font:14px/1.45 Arial,Helvetica,sans-serif; letter-spacing:0; }}
    main {{ max-width:1440px; margin:0 auto; padding:24px; }}
    h1 {{ font-size:26px; margin:0 0 8px; }}
    h2 {{ font-size:18px; margin:0 0 14px; }}
    p {{ margin:6px 0; }}
    .meta {{ color:var(--muted); margin-bottom:18px; }}
    .notice {{ background:#eaf4f8; border-left:4px solid var(--blue); padding:14px 16px; }}
    .grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px;
      margin:16px 0; }}
    .metric {{ background:var(--white); border:1px solid var(--line); padding:14px; }}
    .metric strong {{ display:block; font-size:25px; margin-bottom:3px; }}
    .metric span {{ color:var(--muted); }}
    section {{ background:var(--white); border-top:1px solid var(--line);
      border-bottom:1px solid var(--line); padding:18px; margin:16px 0; }}
    .split {{ display:grid; grid-template-columns:1fr 1fr; gap:24px; }}
    ul {{ margin:0; padding-left:18px; }}
    .distribution {{ list-style:none; padding:0; }}
    .distribution li {{ display:flex; justify-content:space-between; border-bottom:1px solid var(--line);
      padding:7px 0; }}
    .table-wrap {{ overflow-x:auto; }}
    table {{ width:100%; border-collapse:collapse; min-width:1080px; }}
    th,td {{ text-align:left; vertical-align:top; border-bottom:1px solid var(--line);
      padding:9px 8px; }}
    th {{ background:#eef2f4; position:sticky; top:0; }}
    .ok {{ color:var(--green); font-weight:bold; }}
    .warn {{ color:var(--amber); font-weight:bold; }}
    input {{ width:100%; max-width:420px; border:1px solid #aeb8bf; padding:9px 10px;
      margin:0 0 12px; }}
    @media (max-width:800px) {{
      main {{ padding:14px; }} .grid {{ grid-template-columns:1fr 1fr; }}
      .split {{ grid-template-columns:1fr; }} h1 {{ font-size:22px; }}
    }}
  </style>
</head>
<body>
<main>
  <h1>WB: предварительный расчет акций по минимальной цене</h1>
  <div class="meta">Run ID: {html.escape(run_id)} · свежий срез:
    {html.escape(snapshot['checkedAt'])} · режим: read-only / dry-run</div>
  <div class="notice"><strong>Итог:</strong> из {summary['scope_total']} товаров
    <span class="ok">{summary['eligible_any_action']} можно включить</span> в активные акции
    без нарушения минимальной цены. Остальные <strong>{summary['outside_action']}</strong>
    остаются вне акций с ручной скидкой {outside_discount}%.
    Минимумы взяты из свежей выгрузки WB; у
    <strong>{summary['current_minimum_changed_from_reference']}</strong> товаров они
    отличаются от исторической основной сетки.
    <span class="{safety_class}">{safety_text}</span> Изменений в WB не выполнялось.</div>
  <section>
    <h2>Сейчас</h2>
    <div class="grid">
      <div class="metric"><strong>{summary['scope_total']}</strong><span>товаров в расчете</span></div>
      <div class="metric"><strong>{summary['offered_any_action']}</strong><span>доступны акциям</span></div>
      <div class="metric"><strong>{summary['currently_participating']}</strong><span>фактически участвуют</span></div>
      <div class="metric"><strong>{summary['scope_total'] - summary['currently_participating']}</strong><span>не участвуют</span></div>
    </div>
  </section>
  <section>
    <h2>После применения расчета</h2>
  <div class="grid">
    <div class="metric"><strong>{summary['eligible_any_action']}</strong><span>будут участвовать</span></div>
    <div class="metric"><strong>{summary['outside_action']}</strong><span>будут вне акций на {outside_discount}%</span></div>
    <div class="metric"><strong>{summary['to_change_discount']}</strong><span>скидку нужно изменить</span></div>
    <div class="metric"><strong>{summary['no_change_discount']}</strong><span>скидка без изменения</span></div>
  </div>
  </section>
  <section>
    <h2>Почему товары остаются вне акций</h2>
    <div class="grid">
      <div class="metric"><strong>{summary['offered_but_below_minimum']}</strong>
        <span>акции доступны, но итоговая цена ниже минимума</span></div>
      <div class="metric"><strong>{summary['not_offered']}</strong>
        <span>нет ни в одной активной акции</span></div>
      <div class="metric"><strong>{summary['unavailable_action_offers']}</strong>
        <span>предложений, где WB требует сначала изменить базовую цену</span></div>
      <div class="metric"><strong>{summary['selected_multiple_choice']}</strong>
        <span>подошли обе акции; выбрана более высокая цена</span></div>
      <div class="metric"><strong>{summary['to_change_discount']}</strong>
        <span>скидку потребуется изменить после согласования</span></div>
    </div>
  </section>
  <section class="split">
    <div><h2>Активные акции</h2><ul>{actions}</ul></div>
    <div><h2>Целевые скидки после расчета</h2>
      <ul class="distribution">{discounts}</ul></div>
  </section>
  <section>
    <h2>{summary['eligible_any_action']} товаров, которые проходят минимум</h2>
    <input id="q" type="search" placeholder="Поиск по артикулу, названию или акции">
    <div class="table-wrap"><table id="selected"><thead><tr>
      <th>nmID</th><th>Артикул</th><th>Товар</th><th>Штук</th><th>Минимум</th>
      <th>Выбранная акция</th><th>Скидка</th><th>Цена</th><th>Запас</th>
    </tr></thead><tbody>{selected_rows_html(product_rows)}</tbody></table></div>
  </section>
  <section>
    <h2>Метод и ограничения</h2>
    <p>По каждой доступной акции рассчитана фактическая цена:
      <code>базовая цена × (100 − требуемая скидка) / 100</code>.
      Акция допускается только при фактической цене не ниже установленного минимума.
      Если проходят несколько акций, выбирается максимальная фактическая цена.</p>
    <p class="warn">Это предварительный расчет по двум акциям, активным на момент среза.
      Будущие акции ({summary['future_actions']}) в выбор не включены. Фактический состав
      и цены нужно пересчитать fresh dry-run непосредственно перед apply.</p>
    <p>Полная построчная проверка находится в <code>report.xlsx</code> и
      <code>report.json</code>.</p>
  </section>
</main>
<script>
  const q=document.getElementById('q');
  q.addEventListener('input',()=>{{
    const needle=q.value.toLowerCase();
    document.querySelectorAll('#selected tbody tr').forEach(row=>{{
      row.hidden=!row.textContent.toLowerCase().includes(needle);
    }});
  }});
</script>
</body></html>"""
    path.write_text(document, encoding="utf-8")


def write_markdown(
    path: Path,
    *,
    run_id: str,
    snapshot: dict[str, Any],
    summary: dict[str, Any],
    promos: dict[int, dict[str, Any]],
    product_rows: list[dict[str, Any]],
    outside_discount: int,
) -> None:
    action_names = {str(action_id): promo["name"] for action_id, promo in promos.items()}
    lines = [
        "# WB: предварительный расчет акций по минимальной цене",
        "",
        "## Итог",
        "",
        f"- Run ID: `{run_id}`.",
        f"- Свежий срез WB: `{snapshot['checkedAt']}`.",
        f"- Товаров в расчете: `{summary['scope_total']}`.",
        f"- Фактически участвуют сейчас: `{summary['currently_participating']}`.",
        f"- Минимумы из свежей выгрузки WB отличаются от основной исторической "
        f"сетки у `{summary['current_minimum_changed_from_reference']}` товаров.",
        f"- Можно включить в активные акции без нарушения минимума: `{summary['eligible_any_action']}`.",
        f"- Останутся вне акций с ручной скидкой `{outside_discount}%`: "
        f"`{summary['outside_action']}`.",
        f"- Скидку потребуется изменить: `{summary['to_change_discount']}`; "
        f"без изменения: `{summary['no_change_discount']}`.",
        f"- Целевая цена ниже минимума: `{summary['target_below_minimum']}`.",
        f"- Небезопасно для одного upload: `{summary['unsafe_single_upload']}`.",
        f"- Изменений в WB не выполнялось.",
        "",
        "## Причины",
        "",
        f"- Доступны хотя бы одной активной акции: `{summary['offered_any_action']}`.",
        f"- Доступны акциям, но все цены ниже минимума: `{summary['offered_but_below_minimum']}`.",
        f"- Предложений акций без числовой скидки: `{summary['unavailable_action_offers']}`.",
        f"- Не предложены ни одной активной акцией: `{summary['not_offered']}`.",
        f"- Подошли обе акции, выбрана более высокая цена: `{summary['selected_multiple_choice']}`.",
        "",
        "## Распределение",
        "",
    ]
    for action_id, count in summary["selected_by_action"].items():
        lines.append(f"- `{action_names[action_id]}`: `{count}` товаров.")
    for discount, count in summary["target_discount_distribution"].items():
        lines.append(f"- Целевая скидка `{discount}%`: `{count}` товаров.")
    lines += [
        "",
        "## Товары для акции",
        "",
        "| nmID | Артикул | Изделий | Минимум | Акция | Скидка | Цена | Запас |",
        "|---:|---|---:|---:|---|---:|---:|---:|",
    ]
    for row in product_rows:
        if not row["chosen_action_id"]:
            continue
        lines.append(
            f"| {row['nm_id']} | {row['vendor_code']} | {row['pack_qty']} | "
            f"{money(row['minimum'])} ₽ | {row['chosen_action_name']} | "
            f"{row['target_discount']}% | {money(row['target_price'])} ₽ | "
            f"+{money(row['gap_above_minimum'])} ₽ |"
        )
    lines += [
        "",
        "## Метод",
        "",
        "- Проверялась фактическая цена после требуемой целочисленной скидки WB, "
        "а не только плановая цена из файла акции.",
        "- Допустима только акция, где фактическая цена не ниже минимальной цены товара.",
        "- Если подходят несколько акций, выбирается акция с максимальной фактической ценой.",
        f"- Если подходящей акции нет, целевая ручная скидка вне акций равна "
        f"`{outside_discount}%`.",
        f"- В расчет включены `{summary['active_actions']}` активные акции. "
        f"`{summary['future_actions']}` будущие акции не включены.",
        "- Перед apply нужен новый fresh dry-run, drift-check, подтверждение владельца и verify.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def generate_plan_artifacts(
    *,
    snapshot_path: Path,
    prices_path: Path,
    price_plan_path: Path,
    minimum_workbook_path: Path,
    run_dir: Path,
    outside_discount: int,
) -> dict[str, Any]:
    if not 0 <= outside_discount <= 99:
        raise ValueError("--outside-discount must be between 0 and 99")
    run_dir.mkdir(parents=True, exist_ok=True)

    snapshot = read_json(snapshot_path)
    prices = read_json(prices_path)
    reference_price_plan = read_json(price_plan_path)
    price_plan, minimum_snapshot = price_plan_with_fresh_minimums(
        reference_price_plan,
        minimum_workbook_path,
    )
    scope_nm_ids = {int(row["nm_id"]) for row in price_plan.get("rows", [])}
    promos, promo_rows_by_nm = read_promo_rows(
        snapshot_path,
        scope_nm_ids=scope_nm_ids,
    )
    product_rows, candidate_rows, summary = build_plan(
        snapshot=snapshot,
        prices=prices,
        price_plan=price_plan,
        promo_rows_by_nm=promo_rows_by_nm,
        outside_discount=outside_discount,
    )
    summary["current_minimum_changed_from_reference"] = minimum_snapshot[
        "changed_from_reference"
    ]
    summary["current_minimum_distribution"] = minimum_snapshot[
        "minimum_distribution"
    ]
    run_id = run_dir.name
    generated_at = datetime.now().astimezone().isoformat()
    report_data = {
        "schema": "wb_best_price_action_plan.v2",
        "run_id": run_id,
        "generated_at": generated_at,
        "mode": "dry_run",
        "marketplace": "wb",
        "account": "Vital Shevron",
        "outside_action_discount": outside_discount,
        "source": {
            "snapshot": str(snapshot_path),
            "prices": str(prices_path),
            "price_plan": str(price_plan_path),
            "minimum_workbook": str(minimum_workbook_path),
            "minimum_workbook_sha256": minimum_snapshot["workbook_sha256"],
        },
        "minimum_snapshot": minimum_snapshot,
        "summary": summary,
        "active_promos": list(promos.values()),
        "future_promos": snapshot.get("futurePromos", []),
        "products": product_rows,
        "candidates": candidate_rows,
    }
    (run_dir / "report.json").write_text(
        json.dumps(json_ready(report_data), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    processed_dir = run_dir / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    (processed_dir / "effective_price_plan.json").write_text(
        json.dumps(json_ready(price_plan), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (processed_dir / "minimum_snapshot.json").write_text(
        json.dumps(json_ready(minimum_snapshot), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_xlsx(
        run_dir / "report.xlsx",
        summary=summary,
        promos=promos,
        product_rows=product_rows,
        candidate_rows=candidate_rows,
        outside_discount=outside_discount,
    )
    write_html(
        run_dir / "report.html",
        run_id=run_id,
        snapshot=snapshot,
        summary=summary,
        promos=promos,
        product_rows=product_rows,
        outside_discount=outside_discount,
    )
    write_markdown(
        run_dir / "report.md",
        run_id=run_id,
        snapshot=snapshot,
        summary=summary,
        promos=promos,
        product_rows=product_rows,
        outside_discount=outside_discount,
    )
    manifest = {
        "run_id": run_id,
        "task": "wb-best-price-action-plan",
        "mode": "dry_run",
        "risk": "normal",
        "marketplaces": ["wb"],
        "status": "ok",
        "lifecycle_status": "pending_review",
        "started_at": generated_at,
        "finished_at": datetime.now().astimezone().isoformat(),
        "inputs": {
            "outside_action_discount": outside_discount,
            "scope": price_plan.get("category", ""),
            "target_rows": len(price_plan.get("rows", [])),
            "minimum_workbook_sha256": minimum_snapshot["workbook_sha256"],
        },
        "artifacts": {
            "run_dir": str(run_dir),
            "report_html": str(run_dir / "report.html"),
            "report_markdown": str(run_dir / "report.md"),
            "report_xlsx": str(run_dir / "report.xlsx"),
            "report_json": str(run_dir / "report.json"),
            "minimum_snapshot": str(processed_dir / "minimum_snapshot.json"),
            "effective_price_plan": str(processed_dir / "effective_price_plan.json"),
        },
        "source_run_ids": [price_plan_path.parent.name],
        "pending_id": f"{run_id}_pending",
        "approved_id": "",
        "applied_by_run_id": "",
        "closed": False,
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return json_ready(report_data)


def main() -> None:
    args = parse_args()
    report = generate_plan_artifacts(
        snapshot_path=args.snapshot,
        prices_path=args.prices,
        price_plan_path=args.price_plan,
        minimum_workbook_path=args.minimum_workbook,
        run_dir=args.run_dir,
        outside_discount=args.outside_discount,
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
