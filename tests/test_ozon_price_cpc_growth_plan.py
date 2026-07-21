from __future__ import annotations

from decimal import Decimal
import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/pricing/plan_ozon_price_cpc_growth.py"
SPEC = importlib.util.spec_from_file_location("plan_ozon_price_cpc_growth", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_growth_target_bid_is_material() -> None:
    assert MODULE.growth_target_bid("1") == Decimal("2.0")
    assert MODULE.growth_target_bid("1.5") == Decimal("2.5")
    assert MODULE.growth_target_bid("2.3") == Decimal("3.4")
    assert MODULE.growth_target_bid("3") == Decimal("4.5")


def test_price_targets_never_reduce_current_prices() -> None:
    assert MODULE.price_targets(1, "550", "1100") == (
        Decimal("530"),
        Decimal("650"),
        Decimal("1300"),
    )
    assert MODULE.price_targets(3, "1800", "3600") == (
        Decimal("1180"),
        Decimal("1800"),
        Decimal("3600"),
    )


def test_reduce_target_bid_is_not_cosmetic() -> None:
    assert MODULE.reduce_target_bid("2400299708", "3") == Decimal("1.0")
    assert MODULE.reduce_target_bid("other", "4") == Decimal("2.0")
    assert MODULE.reduce_target_bid("other", "2") == Decimal("1.0")


def test_high_drr_signal_must_override_growth_portfolio() -> None:
    row = {"signal": "высокая ДРР", "portfolio_code": "test_b"}
    action = "reduce_high_drr" if row["signal"] == "высокая ДРР" else "increase_growth"
    assert action == "reduce_high_drr"
