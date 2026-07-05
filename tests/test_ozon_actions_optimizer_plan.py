from __future__ import annotations

from pathlib import Path

from seller_agent.config import AppCredentials, OzonSellerCredentials
from seller_agent.tasks.ozon_actions_optimizer_plan import (
    _action_offer_row,
    _pick_recommendations,
    evaluate_offer,
    run_ozon_actions_optimizer_plan,
)


def test_evaluate_offer_blocks_action_price_below_min_price() -> None:
    decision = evaluate_offer(
        {"target_action_price": "499", "boost_score": "65"},
        price_row={"price": {"price": "700", "min_price": "500"}},
        fbo_stock=10,
    )

    assert decision.status == "blocked"
    assert decision.reason_code == "action_price_below_min_price"


def test_evaluate_offer_blocks_missing_confirmed_boost() -> None:
    decision = evaluate_offer(
        {"target_action_price": "600", "boost_score": "", "boost_known": "false"},
        price_row={"price": {"price": "700", "min_price": "500"}},
        fbo_stock=10,
    )

    assert decision.status == "blocked"
    assert decision.reason_code == "missing_confirmed_boost_at_action_price"


def test_elastic_active_offer_uses_current_action_price_and_current_boost() -> None:
    row = _action_offer_row(
        action={
            "action_id": "1",
            "title": "Эластичный бустинг",
            "action_type": "MARKETPLACE_MULTI_LEVEL_DISCOUNT_ON_AMOUNT",
        },
        row={
            "id": 101,
            "action_price": "400",
            "max_action_price": "503",
            "price_min_elastic": "503",
            "price_max_elastic": "382",
            "current_boost": "66.1",
            "min_boost": "15",
            "max_boost": "75",
        },
        group="active",
    )

    assert row["target_action_price"] == "400"
    assert row["target_price_source"] == "action_price"
    assert row["boost_score"] == "66.1"
    assert row["boost_source"] == "current_boost"
    assert row["boost_known"] == "true"


def test_stock_discount_offer_uses_lk_fixed_boost_when_api_boost_is_zero() -> None:
    row = _action_offer_row(
        action={
            "action_id": "3779044",
            "title": "Максимальный бустинг",
            "action_type": "STOCK_DISCOUNT",
        },
        row={
            "id": 101,
            "max_action_price": "580",
            "current_boost": "0",
            "min_boost": "0",
            "max_boost": "0",
        },
        group="candidate",
        lk_boost_source={
            "boost_percent": "55",
            "boost_source": "lk_seller_actions_description",
        },
    )

    assert row["target_action_price"] == "580"
    assert row["target_price_source"] == "max_action_price"
    assert row["boost_score"] == "55"
    assert row["boost_source"] == "lk_seller_actions_description"
    assert row["boost_known"] == "true"
    assert row["boost_note"] == "stock_discount_fixed_action_boost_from_lk_description"


def test_pick_recommendations_prefers_higher_boost_then_price() -> None:
    recommendations = _pick_recommendations(
        [
            {
                "product_id": "101",
                "offer_id": "a",
                "name": "Товар",
                "action_id": "1",
                "action_name": "Эластик",
                "action_type": "MARKETPLACE_MULTI_LEVEL_DISCOUNT_ON_AMOUNT",
                "source_group": "candidate",
                "target_action_price": "620",
                "target_price_source": "max_action_price",
                "boost_score": "55",
                "seller_price": "700",
                "min_price": "500",
                "fbo_stock": "5",
                "price_loss": "80",
                "offer_status": "valid",
                "reason_code": "valid_action_offer",
            },
            {
                "product_id": "101",
                "offer_id": "a",
                "name": "Товар",
                "action_id": "2",
                "action_name": "Супербустинг",
                "action_type": "STOCK_DISCOUNT",
                "source_group": "candidate",
                "target_action_price": "580",
                "target_price_source": "max_action_price",
                "boost_score": "65",
                "seller_price": "700",
                "min_price": "500",
                "fbo_stock": "5",
                "price_loss": "120",
                "offer_status": "valid",
                "reason_code": "valid_action_offer",
            },
        ]
    )

    assert recommendations[0]["recommended_action"] == "add_to_best_action"
    assert recommendations[0]["action_id"] == "2"
    assert recommendations[0]["target_action_price"] == "580"


def test_pick_recommendations_keeps_current_when_new_action_has_same_boost_lower_price() -> None:
    recommendations = _pick_recommendations(
        [
            {
                "product_id": "101",
                "offer_id": "a",
                "name": "Товар",
                "action_id": "1",
                "action_name": "Эластик",
                "action_type": "MARKETPLACE_MULTI_LEVEL_DISCOUNT_ON_AMOUNT",
                "source_group": "active",
                "current_action_price": "620",
                "target_action_price": "620",
                "target_price_source": "action_price",
                "boost_score": "55",
                "seller_price": "700",
                "min_price": "500",
                "fbo_stock": "5",
                "price_loss": "80",
                "offer_status": "valid",
                "reason_code": "valid_action_offer",
            },
            {
                "product_id": "101",
                "offer_id": "a",
                "name": "Товар",
                "action_id": "2",
                "action_name": "Максимальный бустинг",
                "action_type": "STOCK_DISCOUNT",
                "source_group": "candidate",
                "target_action_price": "580",
                "target_price_source": "max_action_price",
                "boost_score": "55",
                "seller_price": "700",
                "min_price": "500",
                "fbo_stock": "5",
                "price_loss": "120",
                "offer_status": "valid",
                "reason_code": "valid_action_offer",
            },
        ]
    )

    assert recommendations[0]["recommended_action"] == "keep_current_action"
    assert recommendations[0]["action_id"] == "1"


def test_pick_recommendations_switches_when_new_action_has_higher_boost() -> None:
    recommendations = _pick_recommendations(
        [
            {
                "product_id": "101",
                "offer_id": "a",
                "name": "Товар",
                "action_id": "1",
                "action_name": "Эластик",
                "action_type": "MARKETPLACE_MULTI_LEVEL_DISCOUNT_ON_AMOUNT",
                "source_group": "active",
                "current_action_price": "620",
                "target_action_price": "620",
                "target_price_source": "action_price",
                "boost_score": "55",
                "seller_price": "700",
                "min_price": "500",
                "fbo_stock": "5",
                "price_loss": "80",
                "offer_status": "valid",
                "reason_code": "valid_action_offer",
            },
            {
                "product_id": "101",
                "offer_id": "a",
                "name": "Товар",
                "action_id": "2",
                "action_name": "Супербустинг",
                "action_type": "STOCK_DISCOUNT",
                "source_group": "candidate",
                "target_action_price": "580",
                "target_price_source": "max_action_price",
                "boost_score": "65",
                "seller_price": "700",
                "min_price": "500",
                "fbo_stock": "5",
                "price_loss": "120",
                "offer_status": "valid",
                "reason_code": "valid_action_offer",
            },
        ]
    )

    assert recommendations[0]["recommended_action"] == "switch_to_better_action_review"
    assert recommendations[0]["action_id"] == "2"


def test_run_ozon_actions_optimizer_plan_builds_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class FakeOzonSellerAdapter:
        def __init__(self, credentials: OzonSellerCredentials) -> None:
            self.credentials = credentials

        def get(self, path: str, params: dict | None = None) -> dict:
            assert path == "/v1/actions"
            assert params
            return {
                "result": [
                    {
                        "id": 1,
                        "title": "Эластичный бустинг",
                        "action_type": "MARKETPLACE_MULTI_LEVEL_DISCOUNT_ON_AMOUNT",
                        "status": "ACTIVE",
                    },
                    {
                        "id": 2,
                        "title": "Супербустинг 65%",
                        "action_type": "STOCK_DISCOUNT",
                        "status": "ACTIVE",
                    },
                ]
            }

        def post(self, path: str, payload: dict) -> dict:
            assert path in {"/v1/actions/products", "/v1/actions/candidates"}
            action_id = payload["action_id"]
            if path == "/v1/actions/products" and action_id == 1:
                return {
                    "result": {
                        "products": [
                            {
                                "id": 101,
                                "offer_id": "offer101",
                                "name": "Шеврон",
                                "action_price": "600",
                                "max_action_price": "620",
                                "current_boost": "55",
                            }
                        ],
                        "total": 1,
                    }
                }
            if path == "/v1/actions/candidates" and action_id == 2:
                return {
                    "result": {
                        "products": [
                            {
                                "id": 101,
                                "offer_id": "offer101",
                                "name": "Шеврон",
                                "max_action_price": "580",
                                "max_boost": "0",
                            }
                        ],
                        "total": 1,
                    }
                }
            return {"result": {"products": [], "total": 0}}

        def fetch_product_info_prices(self, *, visibility: str = "ALL") -> list[dict]:
            assert visibility == "ALL"
            return [
                {
                    "product_id": 101,
                    "offer_id": "offer101",
                    "price": {
                        "price": "700",
                        "old_price": "900",
                        "min_price": "550",
                        "currency_code": "RUB",
                    },
                }
            ]

        def fetch_product_info(self, product_ids: list[str]) -> list[dict]:
            assert product_ids == ["101"]
            return [{"id": 101, "offer_id": "offer101", "name": "Шеврон"}]

        def fetch_product_stocks(self, product_ids: list[str]) -> list[dict]:
            assert product_ids == ["101"]
            return [{"product_id": 101, "stocks": [{"type": "fbo", "present": 4}]}]

    monkeypatch.setattr(
        "seller_agent.tasks.ozon_actions_optimizer_plan.OzonSellerAdapter",
        FakeOzonSellerAdapter,
    )
    lk_boost_summary_json = tmp_path / "lk_boost_source.json"
    lk_boost_summary_json.write_text(
        """
[
  {
    "action_id": "2",
    "title": "Супербустинг 65%",
    "action_type": "STOCK_DISCOUNT",
    "boost_percent_from_description": "65",
    "boost_source": "lk_seller_actions_description"
  }
]
""".strip(),
        encoding="utf-8",
    )

    result = run_ozon_actions_optimizer_plan(
        credentials=AppCredentials(
            ozon_seller=OzonSellerCredentials(client_id="cid", api_key="key"),
            ozon_performance=None,
            wb=None,
        ),
        data_dir=tmp_path,
        run_id="test_ozon_actions_optimizer",
        lk_boost_summary_json=lk_boost_summary_json,
    )

    assert result["overall_status"] == "ok"
    assert result["summary"]["recommended_switch_review"] == 1
    assert result["summary"]["lk_boost_actions_with_numeric_boost"] == 1
    assert result["apply_performed"] is False
    assert Path(result["artifacts"]["report"]).exists()
    assert Path(result["artifacts"]["xlsx"]).exists()
