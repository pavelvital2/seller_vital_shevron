from takterra_agent.tasks.actions_apply import _ozon_apply_rows, build_wb_guarded_payload


def test_wb_guarded_payload_excludes_rows_not_in_master_catalog() -> None:
    rows = [
        {
            "Артикул WB": "1",
            "Артикул поставщика": "known",
            "Базовая цена": "1000",
            "Финальная скидка": "50",
            "Дельта, п.п.": "5",
        },
        {
            "Артикул WB": "2",
            "Артикул поставщика": "unknown",
            "Базовая цена": "1000",
            "Финальная скидка": "50",
            "Дельта, п.п.": "-1",
        },
    ]

    payload, guarded_rows, excluded_rows = build_wb_guarded_payload(rows, master_skus={"known"})

    assert payload["data"] == [{"nmID": 1, "price": 1000, "discount": 50}]
    assert [row["Артикул поставщика"] for row in guarded_rows] == ["known"]
    assert [row["Артикул поставщика"] for row in excluded_rows] == ["unknown"]


def test_ozon_apply_rows_selects_changed_updates_and_deactivations_only() -> None:
    rows = [
        {
            "planned_action": "update_action_price",
            "product_id": "1",
            "current_action_price": "500",
            "calculated_action_price": "500",
        },
        {
            "planned_action": "update_action_price",
            "product_id": "2",
            "current_action_price": "500",
            "calculated_action_price": "510",
        },
        {
            "planned_action": "deactivate_from_action",
            "product_id": "3",
            "current_action_price": "",
            "calculated_action_price": "",
        },
    ]

    activate_rows, deactivate_rows = _ozon_apply_rows(rows)

    assert [row["product_id"] for row in activate_rows] == ["2"]
    assert [row["product_id"] for row in deactivate_rows] == ["3"]

