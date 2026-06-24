from decimal import Decimal

from seller_agent.tasks.wb_actions_discount_plan import _header_index, _find_column
from seller_agent.tasks.wb_actions_discount_plan import ceil_percent_from_price, parse_scheme


def test_parse_scheme_65_50_50() -> None:
    scheme = parse_scheme("65-50-50")

    assert scheme.threshold == 65
    assert scheme.fallback_no_promo == 50
    assert scheme.fallback_over_threshold == 50
    assert scheme.label == "65-50-50"


def test_ceil_percent_from_price_uses_decimal_ceiling() -> None:
    assert ceil_percent_from_price(Decimal("1000"), Decimal("349")) == 66


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
