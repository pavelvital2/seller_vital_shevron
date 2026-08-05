from __future__ import annotations

from seller_agent.tasks.liquidation_report import (
    build_visibility_index,
    classify_product_action,
    query_is_relevant,
)


def test_query_relevance_keeps_exact_semantics_and_excludes_false_matches() -> None:
    assert query_is_relevant("Шеврон на липучке ФСБ на спину", "шеврон фсб")
    assert query_is_relevant("Шеврон СВО БПЛА Рой", "шеврон бпла")
    assert query_is_relevant("Шеврон на липучке позывной Волк", "именной шеврон")
    assert not query_is_relevant("Шеврон на липучке ФСБ на спину", "шеврон бпла")
    assert not query_is_relevant("Петлица ФСИН, 2 шт.", "шевроны фсин")
    assert not query_is_relevant("Шеврон на липучке ФСБ", "липучка для шевронов")


def test_visibility_index_distinguishes_absence_from_query_pack_gap() -> None:
    products = [
        {"ozon_sku": "100", "title": "Шеврон на липучке ФСБ"},
        {"ozon_sku": "200", "title": "Петлица РВСН"},
    ]
    coverage = [
        {"region_id": "moscow", "region_name": "Москва", "query": "шеврон"},
        {"region_id": "moscow", "region_name": "Москва", "query": "шеврон фсб"},
    ]
    positions = [
        {
            "normalized_sku": "OZN100",
            "region_id": "moscow",
            "query": "шеврон фсб",
            "absolute_position": 28,
        }
    ]

    result = build_visibility_index(
        marketplace="ozon",
        products=products,
        positions=positions,
        coverage=coverage,
        warehouse_date="2026-07-30",
        built_at="2026-07-31T00:00:00Z",
    )

    assert result["by_product"]["OZN100"]["status"] == "top30"
    assert result["by_product"]["OZN100"]["best_position"] == 28
    assert result["by_product"]["OZN200"]["status"] == "query_pack_gap"


def test_action_classification_does_not_raise_bid_for_clicks_without_sales() -> None:
    action, recommendation = classify_product_action(
        {
            "stock_fbo": 10,
            "seller_order_units": 0,
            "clicks": 4,
            "views": 80,
            "ad_spend": 12,
            "visibility_status": "top100",
        },
        "ozon",
    )

    assert action == "clicks_no_sales"
    assert "ставку не повышать" in recommendation


def test_new_ozon_candidate_is_not_treated_as_launched_liquidation_row() -> None:
    action, recommendation = classify_product_action(
        {
            "stock_fbo": 10,
            "seller_order_units": 0,
            "cohort_status": "new_candidate_not_in_previous_cohort",
            "visibility_status": "not_visible",
        },
        "ozon",
    )

    assert action == "new_candidate_review"
    assert "отдельно" in recommendation


def test_wb_second_stage_is_not_evaluated_before_two_full_days() -> None:
    action, _ = classify_product_action(
        {
            "stock_goods": 10,
            "seller_order_units": 0,
            "second_price_stage_applied": True,
            "monitoring_full_days": 0,
            "visibility_status": "top100",
        },
        "wb",
    )

    assert action == "stage2_too_early"
