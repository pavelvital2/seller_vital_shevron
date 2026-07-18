from decimal import Decimal

import pytest

from seller_agent.tasks.wb_actions_discount_plan import (
    WbActionsSnapshotBusyError,
    WbActionsSnapshotLock,
    _find_column,
    _header_index,
    _safe_snapshot_error,
    build_payload,
    limit_discount_step,
)
from seller_agent.tasks.wb_actions_discount_plan import ceil_percent_from_price, parse_scheme


def test_parse_scheme_65_50_50() -> None:
    scheme = parse_scheme("65-50-50")

    assert scheme.threshold == 65
    assert scheme.fallback_no_promo == 50
    assert scheme.fallback_over_threshold == 50
    assert scheme.label == "65-50-50"


def test_ceil_percent_from_price_uses_decimal_ceiling() -> None:
    assert ceil_percent_from_price(Decimal("1000"), Decimal("349")) == 66


def test_limit_discount_step_caps_price_drop_below_default_quarantine_threshold() -> None:
    assert limit_discount_step(current_discount=0, target_discount=55) == 33
    assert limit_discount_step(current_discount=33, target_discount=55) == 55
    assert limit_discount_step(current_discount=10, target_discount=55) == 39
    assert limit_discount_step(current_discount=70, target_discount=20) == 35


def test_wb_actions_payload_uses_limited_upload_discount() -> None:
    payload, changed_rows = build_payload(
        [
            {
                "Артикул WB": 101,
                "Базовая цена": "1100",
                "Финальная скидка": 55,
                "Дельта, п.п.": 55,
                "Скидка к загрузке": 33,
                "Дельта загрузки, п.п.": 33,
            }
        ]
    )

    assert len(changed_rows) == 1
    assert payload["discount_step_limit_pp"] == 35
    assert payload["quarantine_safe_price_drop_percent"] == 33
    assert payload["data"] == [{"nmID": 101, "price": 1100, "discount": 33}]
    assert payload["target_discounts"] == {"101": 55}


def test_wb_excel_header_aliases_support_english_export() -> None:
    idx = _header_index(
        (
            "Item in promo (Yes\xa0/\xa0No)",
            "Subcategory",
            "Item name",
            "Seller item No.",
            "WB item No.",
            "Target promo price",
            "Recommended promo discount",
        )
    )

    assert _find_column(idx, ("Товар уже участвует в акции", "Item in promo (Yes / No)")) == 0
    assert _find_column(idx, ("Артикул WB", "WB item No.")) == 4
    assert _find_column(idx, ("Плановая цена для акции", "Target promo price")) == 5


def test_wb_actions_snapshot_lock_blocks_parallel_profile_use(tmp_path) -> None:
    lock_path = tmp_path / "wb_actions.lock"

    with WbActionsSnapshotLock(lock_path):
        with pytest.raises(WbActionsSnapshotBusyError):
            with WbActionsSnapshotLock(lock_path):
                pass

    with WbActionsSnapshotLock(lock_path):
        pass


def test_wb_snapshot_error_hides_chrome_command_line() -> None:
    message = _safe_snapshot_error(
        "",
        "chrome --user-data-dir=/home/pavel/projects/seller_vital_shevron/.sessions/wb/browser-profile "
        "--disable-features=slate,AutoDeElevate,RenderDocument",
    )

    assert "WB LK browser profile is busy" in message
    assert "--disable-features" not in message
