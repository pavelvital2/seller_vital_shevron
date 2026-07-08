from seller_agent.tasks.ozon_elastic_plan import _is_elastic


def test_is_elastic_accepts_current_ozon_action_type() -> None:
    assert _is_elastic(
        {
            "title": "Эластичный бустинг. Без ограничения срока действия",
            "action_type": "ELASTIC_BOOSTING",
        }
    )


def test_is_elastic_keeps_legacy_ozon_action_type() -> None:
    assert _is_elastic(
        {
            "title": "Эластичный бустинг",
            "action_type": "MARKETPLACE_MULTI_LEVEL_DISCOUNT_ON_AMOUNT",
        }
    )


def test_is_elastic_rejects_non_elastic_stock_discount() -> None:
    assert not _is_elastic(
        {
            "title": "Максимальный бустинг",
            "action_type": "STOCK_DISCOUNT",
        }
    )
