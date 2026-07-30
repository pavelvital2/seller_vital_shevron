#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import time
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from seller_agent.config import load_credentials  # noqa: E402
from seller_agent.marketplaces.wb.prices_adapter import WbPricesAdapter  # noqa: E402


PRICE_GRID = {
    1: {"minimum": 500, "discounted": 650, "base": 1300},
    2: {"minimum": 810, "discounted": 1050, "base": 2100},
    3: {"minimum": 1120, "discounted": 1400, "base": 2800},
    4: {"minimum": 1440, "discounted": 1800, "base": 3600},
    5: {"minimum": 1750, "discounted": 2200, "base": 4400},
}
TARGET_DISCOUNT = 50
TARGET_CATEGORY = "Декор для одежды"
NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cell_value(cell: ElementTree.Element | None) -> str:
    if cell is None:
        return ""
    inline = cell.find("x:is/x:t", NS)
    if inline is not None:
        return inline.text or ""
    value = cell.find("x:v", NS)
    return value.text if value is not None and value.text is not None else ""


def parse_minimum_xlsx(path: Path) -> list[dict[str, str]]:
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("xl/worksheets/sheet1.xml")
    root = ElementTree.fromstring(xml)
    parsed: list[list[str]] = []
    for row in root.findall("x:sheetData/x:row", NS):
        cells = {
            re.sub(r"\d+$", "", cell.attrib.get("r", "")): cell_value(cell)
            for cell in row.findall("x:c", NS)
        }
        parsed.append([cells.get(chr(65 + index), "") for index in range(13)])
    if not parsed:
        raise RuntimeError("WB minimum-price workbook has no rows")
    headers = parsed[0]
    return [dict(zip(headers, values)) for values in parsed[1:]]


def minimum_number(value: Any) -> int:
    match = re.match(r"\s*(\d+)", str(value or ""))
    if not match:
        raise ValueError(f"Cannot parse WB minimum price: {value!r}")
    return int(match.group(1))


def minimum_is_active(value: Any) -> bool:
    text = str(value or "")
    if minimum_number(text) <= 0:
        return False
    match = re.search(r"осталось\s+(\d+)\s+д", text, flags=re.IGNORECASE)
    return not match or int(match.group(1)) > 0


def load_catalog() -> dict[int, dict[str, str]]:
    path = PROJECT_ROOT / "data/catalog/unified/products.csv"
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    result: dict[int, dict[str, str]] = {}
    for row in rows:
        raw_nm = str(row.get("wb_nm_id") or "").strip()
        if not raw_nm:
            continue
        nm_id = int(raw_nm)
        if nm_id in result:
            raise RuntimeError(f"Duplicate wb_nm_id in unified catalog: {nm_id}")
        result[nm_id] = row
    return result


def fetch_prices() -> dict[int, dict[str, Any]]:
    credentials = load_credentials()
    if credentials.wb is None:
        raise RuntimeError("WB credentials are not configured")
    goods = WbPricesAdapter(credentials.wb).fetch_goods_prices(limit=1000)
    result = {int(good["nmID"]): good for good in goods}
    if len(result) != len(goods):
        raise RuntimeError("Duplicate nmID in WB Prices API response")
    return result


def normalized_price(good: dict[str, Any]) -> dict[str, int]:
    sizes = good.get("sizes") or []
    if not sizes:
        raise RuntimeError(f"WB good {good.get('nmID')} has no price sizes")
    base_prices = {int(size["price"]) for size in sizes}
    discounted_prices = {int(size["discountedPrice"]) for size in sizes}
    if len(base_prices) != 1 or len(discounted_prices) != 1:
        raise RuntimeError(f"WB good {good.get('nmID')} has divergent size prices")
    return {
        "base": base_prices.pop(),
        "discount": int(good.get("discount") or 0),
        "discounted": discounted_prices.pop(),
    }


def create_minimum_upload(
    *,
    source: Path,
    target: Path,
    row_numbers: dict[int, int],
    rows: list[dict[str, Any]],
) -> None:
    replacements = {
        row_numbers[int(row["nm_id"])]: int(row["target_minimum"])
        for row in rows
    }
    with zipfile.ZipFile(source) as source_zip:
        sheet = source_zip.read("xl/worksheets/sheet1.xml").decode("utf-8")
        for row_number, minimum in replacements.items():
            pattern = re.compile(
                rf'<c r="K{row_number}"[^>]*>.*?</c>',
                flags=re.DOTALL,
            )
            replacement = (
                f'<c r="K{row_number}" s="1" t="inlineStr">'
                f"<is><t>{minimum}</t></is></c>"
            )
            sheet, count = pattern.subn(replacement, sheet, count=1)
            if count != 1:
                raise RuntimeError(f"Cannot replace K{row_number} in WB workbook")
        target.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as out:
            for info in source_zip.infolist():
                payload = (
                    sheet.encode("utf-8")
                    if info.filename == "xl/worksheets/sheet1.xml"
                    else source_zip.read(info.filename)
                )
                out.writestr(info, payload)


def command_plan(args: argparse.Namespace) -> None:
    source_xlsx = Path(args.source_min_xlsx).resolve()
    run_dir = Path(args.run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    source_rows = parse_minimum_xlsx(source_xlsx)
    catalog = load_catalog()
    prices = fetch_prices()

    row_numbers: dict[int, int] = {}
    source_by_nm: dict[int, dict[str, str]] = {}
    for row_number, row in enumerate(source_rows, start=2):
        nm_id = int(row["Артикул WB"])
        source_by_nm[nm_id] = row
        row_numbers[nm_id] = row_number

    target_nms = sorted(
        nm_id
        for nm_id, row in source_by_nm.items()
        if row.get("Категория") == TARGET_CATEGORY
    )
    if len(target_nms) != 478:
        raise RuntimeError(f"Expected 478 target WB goods, got {len(target_nms)}")

    plan_rows: list[dict[str, Any]] = []
    for nm_id in target_nms:
        catalog_row = catalog.get(nm_id)
        good = prices.get(nm_id)
        if catalog_row is None or good is None:
            raise RuntimeError(f"Missing mapped/API row for WB nmID {nm_id}")
        pack_qty = int(catalog_row["pack_qty"])
        if pack_qty not in PRICE_GRID:
            raise RuntimeError(f"Unsupported pack_qty={pack_qty} for WB nmID {nm_id}")
        current = normalized_price(good)
        source_row = source_by_nm[nm_id]
        target = PRICE_GRID[pack_qty]
        current_minimum_text = source_row[
            "Текущая минимальная цена для применения скидки по автоакции"
        ]
        plan_rows.append(
            {
                "nm_id": nm_id,
                "internal_sku": catalog_row["internal_sku"],
                "vendor_code": str(good.get("vendorCode") or ""),
                "pack_qty": pack_qty,
                "category": source_row["Категория"],
                "current_base": current["base"],
                "current_discount": current["discount"],
                "current_discounted": current["discounted"],
                "current_minimum_text": current_minimum_text,
                "current_minimum": minimum_number(current_minimum_text),
                "current_minimum_active": minimum_is_active(current_minimum_text),
                "target_base": target["base"],
                "target_discount": TARGET_DISCOUNT,
                "target_discounted": target["discounted"],
                "target_minimum": target["minimum"],
                "price_change": (
                    current["base"] != target["base"]
                    or current["discount"] != TARGET_DISCOUNT
                    or current["discounted"] != target["discounted"]
                ),
                "minimum_refresh": (
                    minimum_number(current_minimum_text) != target["minimum"]
                    or not minimum_is_active(current_minimum_text)
                ),
            }
        )

    modified_xlsx = run_dir / "wb_minimum_price_upload.xlsx"
    create_minimum_upload(
        source=source_xlsx,
        target=modified_xlsx,
        row_numbers=row_numbers,
        rows=plan_rows,
    )
    parsed_modified = {
        int(row["Артикул WB"]): row for row in parse_minimum_xlsx(modified_xlsx)
    }
    for row in plan_rows:
        actual = minimum_number(
            parsed_modified[row["nm_id"]][
                "Новая минимальная цена для применения скидки по автоакции, RUB"
            ]
        )
        if actual != row["target_minimum"]:
            raise RuntimeError(f"Modified XLSX mismatch for {row['nm_id']}")

    summary = {
        "target_rows": len(plan_rows),
        "pack_qty": dict(sorted(Counter(row["pack_qty"] for row in plan_rows).items())),
        "price_change_rows": sum(bool(row["price_change"]) for row in plan_rows),
        "minimum_refresh_rows": sum(bool(row["minimum_refresh"]) for row in plan_rows),
        "current_discount": dict(
            sorted(Counter(row["current_discount"] for row in plan_rows).items())
        ),
    }
    plan = {
        "schema": "wb_price_grid_plan.v1",
        "created_at": datetime.now().astimezone().isoformat(),
        "marketplace": "wb",
        "account": "Vital Shevron",
        "category": TARGET_CATEGORY,
        "price_grid": PRICE_GRID,
        "target_discount": TARGET_DISCOUNT,
        "source_minimum_xlsx_sha256": file_sha256(source_xlsx),
        "minimum_upload_xlsx_sha256": file_sha256(modified_xlsx),
        "summary": summary,
        "rows": plan_rows,
    }
    plan_path = run_dir / "plan.json"
    write_json(plan_path, plan)
    plan_sha = file_sha256(plan_path)
    write_json(
        run_dir / "approval.json",
        {
            "plan": str(plan_path.relative_to(PROJECT_ROOT)),
            "plan_sha256": plan_sha,
            "minimum_upload_xlsx_sha256": file_sha256(modified_xlsx),
            "owner_instruction": (
                "Применяй сетку по вб, в том числе с установкой минимальных цен"
            ),
        },
    )

    headers = list(plan_rows[0])
    with (run_dir / "review.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, delimiter=";")
        writer.writeheader()
        writer.writerows(plan_rows)
    write_json(
        run_dir / "price_payload_preview.json",
        {
            "data": [
                {
                    "nmID": row["nm_id"],
                    "price": row["target_base"],
                    "discount": row["target_discount"],
                }
                for row in plan_rows
                if row["price_change"]
            ]
        },
    )
    print(
        json.dumps(
            {
                "status": "ok",
                "plan": str(plan_path),
                "plan_sha256": plan_sha,
                "minimum_upload": str(modified_xlsx),
                "summary": summary,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def validate_plan_hash(plan_path: Path, expected: str) -> dict[str, Any]:
    actual = file_sha256(plan_path)
    if actual != expected:
        raise RuntimeError(f"Plan checksum mismatch: expected {expected}, got {actual}")
    plan = read_json(plan_path)
    if plan.get("schema") != "wb_price_grid_plan.v1":
        raise RuntimeError("Unsupported WB price plan schema")
    return plan


def validate_minimum_baseline(plan: dict[str, Any], xlsx: Path) -> dict[str, Any]:
    by_nm = {
        int(row["Артикул WB"]): row
        for row in parse_minimum_xlsx(xlsx)
    }
    drift: list[dict[str, Any]] = []
    for row in plan["rows"]:
        current = by_nm.get(int(row["nm_id"]))
        if current is None:
            drift.append({"nm_id": row["nm_id"], "reason": "missing"})
            continue
        current_text = current[
            "Текущая минимальная цена для применения скидки по автоакции"
        ]
        if (
            current.get("Категория") != row["category"]
            or str(current_text) != str(row["current_minimum_text"])
        ):
            drift.append(
                {
                    "nm_id": row["nm_id"],
                    "reason": "minimum_baseline_drift",
                    "expected": row["current_minimum_text"],
                    "actual": current_text,
                }
            )
    return {"status": "ok" if not drift else "blocked", "drift": drift}


def verify_minimum_targets(plan: dict[str, Any], xlsx: Path) -> dict[str, Any]:
    by_nm = {
        int(row["Артикул WB"]): row
        for row in parse_minimum_xlsx(xlsx)
    }
    mismatches: list[dict[str, Any]] = []
    for row in plan["rows"]:
        current = by_nm.get(int(row["nm_id"]))
        if current is None:
            mismatches.append({"nm_id": row["nm_id"], "reason": "missing"})
            continue
        text = current["Текущая минимальная цена для применения скидки по автоакции"]
        if minimum_number(text) != int(row["target_minimum"]) or not minimum_is_active(text):
            mismatches.append(
                {
                    "nm_id": row["nm_id"],
                    "expected": row["target_minimum"],
                    "actual": text,
                }
            )
    return {
        "status": "ok" if not mismatches else "partial",
        "verified": len(plan["rows"]) - len(mismatches),
        "target": len(plan["rows"]),
        "mismatches": mismatches,
    }


def command_validate_min(args: argparse.Namespace) -> None:
    plan = validate_plan_hash(Path(args.plan), args.approval_sha)
    result = validate_minimum_baseline(plan, Path(args.xlsx))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "ok":
        raise SystemExit(2)


def command_verify_min(args: argparse.Namespace) -> None:
    plan = validate_plan_hash(Path(args.plan), args.approval_sha)
    result = verify_minimum_targets(plan, Path(args.xlsx))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "ok":
        raise SystemExit(3)


def command_apply_prices(args: argparse.Namespace) -> None:
    plan_path = Path(args.plan).resolve()
    plan = validate_plan_hash(plan_path, args.approval_sha)
    current = fetch_prices()
    drift: list[dict[str, Any]] = []
    payload_rows: list[dict[str, int]] = []
    for row in plan["rows"]:
        good = current.get(int(row["nm_id"]))
        if good is None:
            drift.append({"nm_id": row["nm_id"], "reason": "missing"})
            continue
        actual = normalized_price(good)
        expected = {
            "base": int(row["current_base"]),
            "discount": int(row["current_discount"]),
            "discounted": int(row["current_discounted"]),
        }
        if actual != expected:
            drift.append(
                {
                    "nm_id": row["nm_id"],
                    "reason": "price_drift",
                    "expected": expected,
                    "actual": actual,
                }
            )
            continue
        if row["price_change"]:
            payload_rows.append(
                {
                    "nmID": int(row["nm_id"]),
                    "price": int(row["target_base"]),
                    "discount": int(row["target_discount"]),
                }
            )
    if drift:
        write_json(Path(args.out), {"status": "blocked", "drift": drift})
        raise SystemExit(2)

    credentials = load_credentials()
    adapter = WbPricesAdapter(credentials.wb)
    response = adapter.post("/api/v2/upload/task", {"data": payload_rows})
    result: dict[str, Any] = {
        "status": "pending",
        "submitted": len(payload_rows),
        "response": response,
        "verified": 0,
        "mismatches": [],
    }
    out = Path(args.out)
    write_json(out, result)

    target_by_nm = {int(row["nm_id"]): row for row in plan["rows"]}
    deadline = time.monotonic() + int(args.verify_timeout)
    while True:
        time.sleep(5)
        fresh = fetch_prices()
        mismatches = []
        for nm_id, row in target_by_nm.items():
            good = fresh.get(nm_id)
            if good is None:
                mismatches.append({"nm_id": nm_id, "reason": "missing"})
                continue
            actual = normalized_price(good)
            expected = {
                "base": int(row["target_base"]),
                "discount": int(row["target_discount"]),
                "discounted": int(row["target_discounted"]),
            }
            if actual != expected:
                mismatches.append(
                    {"nm_id": nm_id, "expected": expected, "actual": actual}
                )
        result["verified"] = len(target_by_nm) - len(mismatches)
        result["mismatches"] = mismatches
        result["status"] = "ok" if not mismatches else "pending"
        write_json(out, result)
        if not mismatches or time.monotonic() >= deadline:
            break
    if result["mismatches"]:
        result["status"] = "partial"
        write_json(out, result)
        raise SystemExit(3)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan")
    plan.add_argument("--source-min-xlsx", required=True)
    plan.add_argument("--run-dir", required=True)
    plan.set_defaults(func=command_plan)

    validate_min = subparsers.add_parser("validate-min-baseline")
    validate_min.add_argument("--plan", required=True)
    validate_min.add_argument("--approval-sha", required=True)
    validate_min.add_argument("--xlsx", required=True)
    validate_min.set_defaults(func=command_validate_min)

    verify_min = subparsers.add_parser("verify-min")
    verify_min.add_argument("--plan", required=True)
    verify_min.add_argument("--approval-sha", required=True)
    verify_min.add_argument("--xlsx", required=True)
    verify_min.set_defaults(func=command_verify_min)

    apply_prices = subparsers.add_parser("apply-prices")
    apply_prices.add_argument("--plan", required=True)
    apply_prices.add_argument("--approval-sha", required=True)
    apply_prices.add_argument("--out", required=True)
    apply_prices.add_argument("--verify-timeout", type=int, default=120)
    apply_prices.set_defaults(func=command_apply_prices)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
