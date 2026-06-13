import pytest

from takterra_agent.tasks.wb_actions_discount_apply import (
    _assert_no_drift,
    _latest_history_data,
    _payload_from_rows,
)


def test_payload_from_rows_includes_only_changed_discounts() -> None:
    payload, changed_rows = _payload_from_rows(
        [
            {
                "Артикул WB": "101",
                "Базовая цена": "1100",
                "Финальная скидка": "55",
                "Дельта, п.п.": "-10",
            },
            {
                "Артикул WB": "102",
                "Базовая цена": "2000",
                "Финальная скидка": "68",
                "Дельта, п.п.": "0",
            },
        ]
    )

    assert changed_rows == [
        {
            "Артикул WB": "101",
            "Базовая цена": "1100",
            "Финальная скидка": "55",
            "Дельта, п.п.": "-10",
        }
    ]
    assert payload == {"data": [{"nmID": 101, "price": 1100, "discount": 55}]}


def test_assert_no_drift_compares_nm_price_discount_signature() -> None:
    approved = {"data": [{"nmID": 101, "price": 1100, "discount": 55}]}
    fresh = {"data": [{"nmID": 101, "price": 1100, "discount": 55}]}

    assert _assert_no_drift(approved_payload=approved, fresh_payload=fresh) == {
        "approved_payload_rows": 1,
        "fresh_payload_rows": 1,
        "added": [],
        "removed": [],
    }


def test_assert_no_drift_raises_on_changed_discount() -> None:
    approved = {"data": [{"nmID": 101, "price": 1100, "discount": 55}]}
    fresh = {"data": [{"nmID": 101, "price": 1100, "discount": 56}]}

    with pytest.raises(RuntimeError, match="drift-check failed"):
        _assert_no_drift(approved_payload=approved, fresh_payload=fresh)


def test_latest_history_data_returns_last_non_empty_poll() -> None:
    polls = [
        {"status": {"history": {"data": {"data": {}}}}},
        {
            "status": {
                "history": {
                    "data": {
                        "data": {
                            "uploadID": 1,
                            "overAllGoodsNumber": 302,
                            "successGoodsNumber": 302,
                        }
                    }
                }
            }
        },
    ]

    assert _latest_history_data(polls) == {
        "uploadID": 1,
        "overAllGoodsNumber": 302,
        "successGoodsNumber": 302,
    }
