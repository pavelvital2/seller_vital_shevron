from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "analytics" / "apply_wb_portfolio_existing_bids.py"
SPEC = spec_from_file_location("apply_wb_portfolio_existing_bids", SCRIPT)
assert SPEC and SPEC.loader
MODULE = module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_parse_approved_rows_extracts_exact_campaign_bid_change() -> None:
    rows = MODULE.parse_approved_rows(
        [
            {
                "Первая волна": "да",
                "Сегмент": "scale_strong",
                "nmID": "123",
                "Товар": "Тест",
                "План ставок": "456:1.10->1.32",
            },
            {
                "Первая волна": "да",
                "Сегмент": "launch_priority",
                "nmID": "789",
                "Товар": "Новый",
                "План ставок": "новая:1.10",
            },
        ]
    )

    assert rows == [
        {
            "advert_id": "456",
            "nm_id": "123",
            "name": "Тест",
            "segment": "scale_strong",
            "current_bid": "1.10",
            "target_bid": "1.32",
            "current_bid_place": "search",
            "current_bid_source": "current_bid_api",
            "target_bid_kopecks": 132,
            "current_bid_kopecks": 110,
        }
    ]


def test_parse_approved_rows_rejects_non_increasing_bid() -> None:
    with pytest.raises(RuntimeError, match="greater than current"):
        MODULE.parse_approved_rows(
            [
                {
                    "Первая волна": "да",
                    "Сегмент": "visibility_test",
                    "nmID": "123",
                    "Товар": "Тест",
                    "План ставок": "456:1.10->1.00",
                }
            ]
        )


def test_parse_approved_rows_accepts_exact_existing_launch_correction() -> None:
    rows = MODULE.parse_approved_rows(
        [
            {
                "Первая волна": "да",
                "Сегмент": "launch_priority",
                "nmID": "789",
                "Товар": "Уже подключен",
                "План ставок": "456:1.00->1.10",
            }
        ]
    )

    assert rows[0]["advert_id"] == "456"
    assert rows[0]["target_bid_kopecks"] == 110
