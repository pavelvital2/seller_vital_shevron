import pytest

from seller_agent.config import AppCredentials, WbCredentials
from seller_agent.tasks.wb_actions_discount_apply import (
    _assert_no_drift,
    _build_partial_drift_payload,
    _latest_history_data,
    _payload_from_rows,
    run_wb_actions_discount_apply,
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


def test_build_partial_drift_payload_uploads_only_unchanged_rows() -> None:
    approved = {
        "data": [
            {"nmID": 101, "price": 1100, "discount": 55},
            {"nmID": 102, "price": 1200, "discount": 55},
        ]
    }
    fresh = {
        "data": [
            {"nmID": 101, "price": 1100, "discount": 55},
            {"nmID": 102, "price": 1200, "discount": 56},
        ]
    }

    drift, upload_payload = _build_partial_drift_payload(
        approved_payload=approved,
        fresh_payload=fresh,
    )

    assert upload_payload == {"data": [{"nmID": 101, "price": 1100, "discount": 55}]}
    assert drift["mode"] == "partial_apply_unchanged_rows"
    assert drift["skipped_due_to_drift_nm_ids"] == [102]
    assert drift["skipped_due_to_drift_product_count"] == 1
    assert drift["skipped_due_to_drift_count"] == 2


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


def test_wb_actions_apply_uses_wb_scoped_api_preflight(tmp_path, monkeypatch) -> None:
    plan_dir = tmp_path / "runs" / "2026-07-04" / "wb_actions_discount_plan_70-55-55_test"
    plan_dir.mkdir(parents=True)
    csv_path = plan_dir / "wb-discount-calculation-active-actions-70-55-55.csv"
    csv_path.write_text(
        "Артикул WB;Базовая цена;Финальная скидка;Дельта, п.п.\n"
        "101;1100;55;0\n",
        encoding="utf-8-sig",
    )
    (plan_dir / "summary.json").write_text(
        (
            '{"run_id":"wb_actions_discount_plan_70-55-55_test",'
            '"summary":{"scheme":"70-55-55"},'
            f'"artifacts":{{"csv":"{csv_path}"}}}}'
        ),
        encoding="utf-8",
    )
    calls = {}

    def fake_preflight(**kwargs):
        calls["preflight"] = kwargs
        return {
            "run_id": "status_preflight_wb_api_only",
            "overall_status": "ok",
            "artifacts": {},
        }

    def fake_fresh_plan(**kwargs):
        calls["fresh_plan"] = kwargs
        return {
            "run_id": "wb_actions_discount_plan_70-55-55_fresh",
            "summary": {"scheme": "70-55-55"},
            "artifacts": {"csv": str(csv_path)},
        }

    monkeypatch.setattr("seller_agent.tasks.wb_actions_discount_apply.run_status_preflight", fake_preflight)
    monkeypatch.setattr("seller_agent.tasks.wb_actions_discount_apply.run_wb_actions_discount_plan", fake_fresh_plan)

    result = run_wb_actions_discount_apply(
        credentials=AppCredentials(ozon_seller=None, ozon_performance=None, wb=WbCredentials(token="token")),
        data_dir=tmp_path,
        plan_run_id="wb_actions_discount_plan_70-55-55_test",
        run_id="wb_actions_discount_apply_70-55-55_test",
        confirmed_by_user=True,
    )

    assert result["preflight"]["run_id"] == "status_preflight_wb_api_only"
    assert calls["preflight"]["include_lk"] is False
    assert calls["preflight"]["marketplaces"] == ("wb",)
    assert calls["preflight"]["include_ozon_performance"] is False
    assert calls["fresh_plan"]["scheme_text"] == "70-55-55"
