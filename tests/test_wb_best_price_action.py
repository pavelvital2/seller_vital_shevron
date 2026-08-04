import json
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook

from scripts.actions.wb_best_price_action_apply import (
    fresh_minimum_verify,
    plan_drift,
    target_payload,
)
from scripts.actions.wb_best_price_action_plan import (
    build_plan,
    price_plan_with_fresh_minimums,
    read_promo_rows,
)
from seller_agent.tasks.wb_best_price_action import _target_plan_check


def _offer(action_id: int, price: int, discount: int) -> dict:
    return {
        "action_id": action_id,
        "action_name": f"action-{action_id}",
        "action_start": "2026-07-01T00:00:00Z",
        "action_end": "2026-08-01T00:00:00Z",
        "currently_participates": False,
        "status": "Не участвует",
        "vendor_code": "sku",
        "plan_price": Decimal(price),
        "required_discount": discount,
        "actual_price": Decimal(price),
        "available": True,
    }


def _write_promo_workbook(path: Path, rows: list[list[object]]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(
        [
            "Товар уже участвует в акции",
            "Артикул поставщика",
            "Артикул WB",
            "Плановая цена для акции",
            "Текущая розничная цена",
            "Текущая скидка на сайте, %",
            "Загружаемая скидка для участия в акции",
            "Статус",
        ]
    )
    for row in rows:
        sheet.append(row)
    workbook.save(path)


def _write_snapshot(tmp_path: Path, rows: list[list[object]]) -> Path:
    snapshot_path = tmp_path / "cabinet-actions-snapshot.json"
    snapshot_path.write_text(
        json.dumps({"promos": [{"actionID": 2670, "name": "Акция"}]}),
        encoding="utf-8",
    )
    excel_dir = tmp_path / "excel"
    excel_dir.mkdir()
    _write_promo_workbook(excel_dir / "promo-2670-test.xlsx", rows)
    return snapshot_path


def _write_minimum_workbook(path: Path, rows: list[list[object]]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(
        [
            "Бренд",
            "Категория",
            "Артикул WB",
            "Артикул продавца",
            "Последний баркод",
            "Остатки WB",
            "Остатки продавца",
            "Оборачиваемость",
            "Цена со скидкой",
            "Текущая минимальная цена для применения скидки по автоакции",
            "Новая минимальная цена для применения скидки по автоакции, RUB",
            "Текущая блокировка применения скидки по автоакции",
            "Новая блокировка применения скидки по автоакции",
        ]
    )
    for row in rows:
        sheet.append(row)
    workbook.save(path)


def _minimum_row(nm_id: int, minimum: str) -> list[object]:
    return ["", "Декор для одежды", nm_id, "sku", "", 1, 0, 0, 650, minimum, "", "Нет", ""]


def test_fresh_minimums_override_reference_price_grid(tmp_path: Path) -> None:
    workbook_path = tmp_path / "minimum.xlsx"
    _write_minimum_workbook(
        workbook_path,
        [
            _minimum_row(1, "430 (осталось 26 дней)"),
            _minimum_row(2, "810 (осталось 26 дней)"),
            _minimum_row(3, "820 (осталось 26 дней)"),
        ],
    )
    reference = {
        "schema": "wb_price_grid_plan.v1",
        "rows": [
            {"nm_id": 1, "target_minimum": 500, "pack_qty": 1},
            {"nm_id": 2, "target_minimum": 810, "pack_qty": 2},
            {"nm_id": 3, "target_minimum": 1120, "pack_qty": 3},
        ],
    }

    effective, snapshot = price_plan_with_fresh_minimums(reference, workbook_path)

    assert [row["target_minimum"] for row in effective["rows"]] == [430, 810, 820]
    assert [row["reference_minimum"] for row in effective["rows"]] == [500, 810, 1120]
    assert snapshot["changed_from_reference"] == 2
    assert snapshot["minimum_distribution"] == {"430": 1, "810": 1, "820": 1}


def test_fresh_minimums_reject_inactive_or_missing_rows(tmp_path: Path) -> None:
    workbook_path = tmp_path / "minimum.xlsx"
    _write_minimum_workbook(
        workbook_path,
        [_minimum_row(1, "430 (осталось 0 дней)")],
    )
    reference = {
        "rows": [
            {"nm_id": 1, "target_minimum": 500},
            {"nm_id": 2, "target_minimum": 810},
        ]
    }

    try:
        price_plan_with_fresh_minimums(reference, workbook_path)
    except RuntimeError as exc:
        assert "incomplete" in str(exc)
    else:
        raise AssertionError("inactive and missing minimum rows must block the plan")


def test_apply_minimum_verify_uses_owner_approved_values(
    tmp_path: Path,
    monkeypatch,
) -> None:
    run_dir = tmp_path / "apply"
    (run_dir / "processed").mkdir(parents=True)

    def fake_run_command(command: list[str], *, timeout: int = 900) -> dict:
        del timeout
        out_dir = Path(command[command.index("--out-dir") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        _write_minimum_workbook(
            out_dir / "fresh_source.xlsx",
            [_minimum_row(1, "430 (осталось 26 дней)")],
        )
        return {"returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(
        "scripts.actions.wb_best_price_action_apply.run_command",
        fake_run_command,
    )

    result = fresh_minimum_verify(
        run_dir=run_dir,
        expected_products=[{"nm_id": 1, "minimum": 430}],
    )

    assert result["status"] == "ok"
    assert result["verified"] == 1
    assert result["expected_source"] == "owner_approved_report"


def test_read_promo_rows_uses_calculated_discount_for_active_goods(tmp_path: Path) -> None:
    snapshot_path = _write_snapshot(
        tmp_path,
        [["Да", "sku", 436578563, 490, 1500, 70, 70, "Участвует"]],
    )

    _, rows = read_promo_rows(snapshot_path, scope_nm_ids={436578563})

    offer = rows[436578563][0]
    assert offer["required_discount"] == 68
    assert offer["reported_upload_discount"] == 70
    assert offer["actual_price"] == Decimal("480.00")
    assert offer["discount_source"] == "calculated_from_plan_price_active"
    assert offer["available"] is True


def test_read_promo_rows_skips_out_of_scope_invalid_rows(tmp_path: Path) -> None:
    snapshot_path = _write_snapshot(
        tmp_path,
        [
            ["Да", "other", 436578563, 490, 1500, 70, "invalid", "Участвует"],
            ["Нет", "target", 719729537, 520, 1300, 50, 60, "Не участвует"],
        ],
    )

    _, rows = read_promo_rows(snapshot_path, scope_nm_ids={719729537})

    assert set(rows) == {719729537}
    assert rows[719729537][0]["required_discount"] == 60


def test_non_numeric_wb_action_instruction_is_a_blocked_candidate(tmp_path: Path) -> None:
    snapshot_path = _write_snapshot(
        tmp_path,
        [[
            "Нет",
            "target",
            719729537,
            53,
            1300,
            50,
            'Измените цену через раздел "Цены и скидки"',
            "Не участвует: цена выше плановой",
        ]],
    )
    _, rows = read_promo_rows(snapshot_path, scope_nm_ids={719729537})
    price_plan = {
        "rows": [{
            "nm_id": 719729537,
            "internal_sku": "single",
            "vendor_code": "target",
            "pack_qty": 1,
            "target_minimum": 500,
        }]
    }
    prices = {
        "goods": [{
            "nmID": 719729537,
            "vendorCode": "target",
            "title": "Товар",
            "prices": [1300],
            "discount": 50,
            "discountedPrices": [650],
        }]
    }

    products, candidates, summary = build_plan(
        snapshot={"checkedAt": "now", "promos": [{}], "futurePromos": []},
        prices=prices,
        price_plan=price_plan,
        promo_rows_by_nm=rows,
        outside_discount=50,
    )

    assert candidates[0]["eligible"] is False
    assert candidates[0]["required_discount"] is None
    assert products[0]["chosen_action_id"] == ""
    assert products[0]["target_discount"] == 50
    assert summary["unavailable_action_offers"] == 1


def test_best_price_plan_selects_highest_eligible_price_and_fallback() -> None:
    price_plan = {
        "rows": [
            {
                "nm_id": 1,
                "internal_sku": "kit2",
                "vendor_code": "kit2",
                "pack_qty": 2,
                "target_minimum": 810,
            },
            {
                "nm_id": 2,
                "internal_sku": "single",
                "vendor_code": "single",
                "pack_qty": 1,
                "target_minimum": 500,
            },
        ]
    }
    prices = {
        "goods": [
            {
                "nmID": 1,
                "vendorCode": "kit2",
                "title": "Комплект",
                "prices": [2100],
                "discount": 50,
                "discountedPrices": [1050],
            },
            {
                "nmID": 2,
                "vendorCode": "single",
                "title": "Одиночный",
                "prices": [1300],
                "discount": 50,
                "discountedPrices": [650],
            },
        ]
    }
    promo_rows = {
        1: [_offer(10, 861, 59), _offer(20, 840, 60)],
        2: [_offer(10, 494, 62)],
    }

    products, candidates, summary = build_plan(
        snapshot={"checkedAt": "now", "promos": [{}, {}], "futurePromos": []},
        prices=prices,
        price_plan=price_plan,
        promo_rows_by_nm=promo_rows,
        outside_discount=50,
    )

    assert len(candidates) == 3
    assert products[0]["chosen_action_id"] == 10
    assert products[0]["target_price"] == Decimal("861")
    assert products[0]["target_discount"] == 59
    assert products[1]["chosen_action_id"] == ""
    assert products[1]["target_discount"] == 50
    assert summary["eligible_any_action"] == 1
    assert summary["outside_action"] == 1
    assert summary["selected_multiple_choice"] == 1
    assert summary["target_below_minimum"] == 0
    assert summary["unsafe_single_upload"] == 0


def test_best_price_plan_blocks_outside_discount_below_minimum() -> None:
    price_plan = {
        "rows": [
            {
                "nm_id": 1,
                "internal_sku": "single",
                "vendor_code": "single",
                "pack_qty": 1,
                "target_minimum": 530,
            }
        ]
    }
    prices = {
        "goods": [
            {
                "nmID": 1,
                "vendorCode": "single",
                "title": "Одиночный",
                "prices": [1300],
                "discount": 50,
                "discountedPrices": [650],
            }
        ]
    }

    products, _, summary = build_plan(
        snapshot={"checkedAt": "now", "promos": [], "futurePromos": []},
        prices=prices,
        price_plan=price_plan,
        promo_rows_by_nm={},
        outside_discount=60,
    )

    assert products[0]["target_price"] == Decimal("520.00")
    assert products[0]["target_below_minimum"] is True
    assert products[0]["safe_single_upload"] is False
    assert summary["target_below_minimum"] == 1
    assert summary["unsafe_single_upload"] == 1


def test_apply_payload_and_drift_are_exact() -> None:
    products = [
        {
            "nm_id": 1,
            "base_price": 2100,
            "minimum": 810,
            "current_discount": 50,
            "current_price": 1050,
            "chosen_action_id": 10,
            "target_discount": 59,
            "target_price": 861,
        },
        {
            "nm_id": 2,
            "base_price": 1300,
            "minimum": 500,
            "current_discount": 50,
            "current_price": 650,
            "chosen_action_id": "",
            "target_discount": 50,
            "target_price": 650,
        },
    ]

    assert plan_drift(products, products)["status"] == "ok"
    payload = target_payload(products, changes_only=True)
    assert payload == {"data": [{"nmID": 1, "price": 2100, "discount": 59}]}

    changed = [dict(row) for row in products]
    changed[0]["target_discount"] = 60
    drift = plan_drift(products, changed)
    assert drift["status"] == "blocked"
    assert drift["drift_rows_count"] == 1


def test_verify_target_signature_normalizes_json_numbers() -> None:
    approved = [
        {
            "nm_id": 1,
            "minimum": 530.0,
            "chosen_action_id": "",
            "target_discount": 50,
            "target_price": 650.0,
        }
    ]
    fresh = [
        {
            "nm_id": 1,
            "minimum": Decimal("530"),
            "chosen_action_id": "",
            "target_discount": 50,
            "target_price": Decimal("650.00"),
        }
    ]

    assert _target_plan_check(approved, fresh)["status"] == "ok"
